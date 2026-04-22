"""Shared tile-rendering infrastructure (thread pool, semaphore, image helpers)."""
import asyncio
import os
import struct
import warnings
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
from rio_tiler.models import ImageData

from sat_wms.time_utils import floor_dt

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


def _empty_png(width: int, height: int) -> bytes:
    """Return a fully transparent RGBA PNG using stdlib only."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"\x00" * (height * (width * 4 + 1))
    idat = chunk(b"IDAT", zlib.compress(raw, 1))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def empty_image(width: int, height: int, img_format: str) -> bytes:
    """Return a fully transparent image in the requested format."""
    if img_format == "PNG":
        return _empty_png(width, height)
    mask = np.ones((1, height, width), dtype=bool)
    data = np.ma.MaskedArray(np.zeros((1, height, width), dtype=np.uint8), mask)
    return ImageData(data).render(img_format=img_format)


def cache_control(latest: datetime | None, end_dt: datetime, interval_min: int) -> str:
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
    return "public, max-age=60, stale-while-revalidate=60" if is_latest else "public, max-age=86400, immutable"


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
