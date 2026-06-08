"""Shared tile-rendering infrastructure (thread pool, semaphore, image helpers)."""
import asyncio
import functools
import os
import struct
import warnings
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from fastapi import Response
from rio_tiler.models import ImageData

from sat_wms.config import config
from sat_wms.time_utils import floor_dt, parse_interval_min

# GDAL performance hints for Cloud-Optimized GeoTIFFs.
os.environ.setdefault("GDAL_CACHEMAX", "1024")                      # tile cache (MB) — doubled for large SAR COGs
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")  # skip dir listing on open
os.environ.setdefault("VSI_CACHE", "TRUE")                          # cache range-request headers
os.environ.setdefault("VSI_CACHE_SIZE", "50000000")                 # 50 MB VSI header cache
os.environ.setdefault("GDAL_INGESTED_BYTES_AT_OPEN", "32768")       # pre-fetch 32 kB COG header in one request
os.environ.setdefault("GDAL_NUM_THREADS", "ALL_CPUS")               # multi-threaded decompression
os.environ.setdefault("GDAL_BAND_BLOCK_CACHE", "HASHSET")           # faster for sparse COG reads

# JPEG-compressed COGs store NoData=0, but JPEG's lossy compression can produce
# pixels with value exactly 0 in valid data areas.  GDAL bumps these 0→1 during
# warping, which is visually imperceptible for uint8 RGB but very noisy in logs.
warnings.filterwarnings("ignore", ".*has been changed.*to avoid being treated as NoData")

# I/O-bound COG reads benefit from more threads than CPU cores.
READ_POOL = ThreadPoolExecutor(max_workers=os.cpu_count() * 4)

# Limit concurrent renders to prevent READ_POOL starvation under high load.
RENDER_SEM = asyncio.Semaphore(os.cpu_count())

TILE_FORMATS: dict[str, str] = {"image/png": "PNG", "image/webp": "WEBP"}
MEDIA_TYPES: dict[str, str] = {"PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True)
class RenderOptions:
    """Server-side rendering knobs sourced from config, threaded into the render entry points.

    Bundled so handlers read config once (``from_config``) and pass a single object instead
    of repeating the same four arguments. ``wmts_max_zoom`` is only meaningful for tiles.
    """

    interval_min: int = 10
    force_webp: bool = False
    empty_no_content: bool = False
    wmts_max_zoom: int = 9

    @classmethod
    def from_config(cls) -> "RenderOptions":
        """Build options from the live application config (reads each key once)."""
        return cls(
            interval_min=parse_interval_min(config.get("granule_interval")),
            force_webp=bool(config.get("force_webp")),
            empty_no_content=bool(config.get("empty_no_content")),
            wmts_max_zoom=int(config.get("wmts_max_zoom")),
        )


# Shared immutable default so render entry points avoid a call in their argument defaults.
DEFAULT_RENDER_OPTIONS = RenderOptions()


def format_from_params(params: dict) -> str:
    """Resolve the requested image FORMAT to an internal format name, defaulting to PNG."""
    return TILE_FORMATS.get((params.get("FORMAT") or "").lower(), "PNG")


def is_range_probe(range_header: str | None) -> bool:
    """Return True for the ``bytes=0-0`` presence probe some clients send before fetching."""
    if range_header is None:
        return False
    return range_header.replace(" ", "").lower() == "bytes=0-0"


def range_probe_response(data_available: bool, media_type: str, headers: dict | None = None) -> Response:
    """Answer a ``bytes=0-0`` probe without rendering: 206 if data is available, else 204.

    ``data_available`` is the probe's answer (does an image exist for this bbox/time?),
    not a behaviour switch. The 206 carries a single dummy byte so the range is
    satisfiable; the client only needs the status to decide whether to fetch the image.
    """
    if not data_available:
        return Response(status_code=204, headers=headers)
    probe_headers = {"Accept-Ranges": "bytes", "Content-Range": "bytes 0-0/*"}
    if headers:
        probe_headers = {**headers, **probe_headers}
    return Response(content=b"\x00", status_code=206, media_type=media_type, headers=probe_headers)


def read_cog(fp: str, dst_crs, read, *, tms=None):
    """Open a COG and apply `read(cog)`, returning None if the file is missing on disk.

    Rasters carrying GCPs (e.g. raw SAR granules) are pre-wrapped in a WarpedVRT
    targeting `dst_crs` so GDAL overview selection operates in the destination CRS's
    units. `read` is the caller's read strategy (e.g. ``cog.part(...)`` or
    ``cog.tile(...)``) and may itself return None for out-of-bounds reads. When `tms`
    is given it is forwarded to COGReader; otherwise COGReader keeps its own default.

    Imports are local so this stays cheap to import and so the thread-pool callers
    (and test monkeypatches of rasterio/rio_tiler) resolve the real objects at call time.
    """
    import contextlib  # noqa: PLC0415
    import logging  # noqa: PLC0415

    import rasterio  # noqa: PLC0415
    from rasterio.enums import Resampling  # noqa: PLC0415
    from rasterio.transform import from_gcps  # noqa: PLC0415
    from rasterio.vrt import WarpedVRT  # noqa: PLC0415
    from rio_tiler.io import COGReader  # noqa: PLC0415

    try:
        with rasterio.Env():
            with contextlib.ExitStack() as stack:
                src = stack.enter_context(rasterio.open(fp))
                gcps, gcp_crs = src.gcps
                if gcps:
                    dataset = stack.enter_context(
                        WarpedVRT(src, src_crs=gcp_crs, src_transform=from_gcps(gcps),
                                  crs=dst_crs, resampling=Resampling.bilinear, add_alpha=True)
                    )
                else:
                    dataset = src
                reader_kwargs = {"tms": tms} if tms is not None else {}
                cog = stack.enter_context(COGReader(fp, dataset=dataset, **reader_kwargs))
                return read(cog)
    except (OSError, rasterio.errors.RasterioIOError):
        logging.getLogger(__name__).warning("File not found, skipping: %s", fp)
        return None


async def encode_image(image_data: ImageData, fmt: str) -> bytes:
    """Encode an ImageData to bytes in `fmt`, off the event loop.

    PNG uses zlevel=1 (fast) since these tiles are re-encoded on every cache miss.
    """
    render_kwargs = {"img_format": fmt} if fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(image_data.render, **render_kwargs))


def empty_image(width: int, height: int, img_format: str) -> bytes:
    """Return a fully transparent image in the requested format."""
    if img_format == "PNG":
        return _empty_png(width, height)
    mask = np.ones((1, height, width), dtype=bool)
    data = np.ma.MaskedArray(np.zeros((1, height, width), dtype=np.uint8), mask)
    return ImageData(data).render(img_format=img_format)


def _empty_png(width: int, height: int) -> bytes:
    """Return a fully transparent RGBA PNG using stdlib only."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"\x00" * (height * (width * 4 + 1))
    idat = chunk(b"IDAT", zlib.compress(raw, 1))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def cache_control(latest: datetime | None, end_dt: datetime, interval_min: int, stepped: bool = False) -> str:
    """Return an appropriate Cache-Control header value.

    Works correctly whether `latest` is tz-aware or tz-naive (the DB schema stores
    ``timestamp without time zone``, so psycopg3 returns tz-naive datetimes).
    """
    if latest is None:
        return "public, max-age=86400, immutable"
    # Normalise: strip tz info from both sides so the comparison is always apples-to-apples.
    latest_naive = latest.replace(tzinfo=None)
    end_dt_naive = end_dt.replace(tzinfo=None)
    is_latest = floor_dt(latest_naive, interval_min) == floor_dt(end_dt_naive, interval_min)
    if is_latest:
        return "public, max-age=60, stale-while-revalidate=60"
    if stepped:
        return "public, max-age=300"
    return "public, max-age=86400, immutable"


def composite_images(images: list[ImageData | None]) -> ImageData | None:
    """Composite images in priority order onto a pre-allocated canvas.

    None entries (out-of-bounds reads) are skipped.  Uses in-place updates
    instead of allocating a new array per merge step.
    """
    canvas = None
    gaps = None
    bounds = crs = None

    for img in images:
        if img is None:
            continue
        if canvas is None:
            canvas = img.array.data.copy()
            gaps = np.all(img.array.mask, axis=0)
            bounds, crs = img.bounds, img.crs
            continue
        if not np.any(gaps):
            break
        has_data = ~np.all(img.array.mask, axis=0)
        fill = gaps & has_data
        if np.any(fill):
            canvas[:, fill] = img.array.data[:, fill]
            gaps[fill] = False

    if canvas is None:
        return None
    full_mask = np.broadcast_to(gaps[np.newaxis, :, :], canvas.shape).copy()
    return ImageData(np.ma.MaskedArray(canvas, full_mask), bounds=bounds, crs=crs)
