"""GetMap request handler."""
import asyncio
import dataclasses
import functools
import os as _os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pyproj
from fastapi import Response
from rasterio.crs import CRS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData

from sat_wms.config import config
from sat_wms.time_utils import floor_dt

# GDAL performance hints for Cloud Optimized GeoTIFFs.
_os.environ.setdefault("GDAL_CACHEMAX", "512")                  # tile cache (MB)
_os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")  # skip dir listing on open
_os.environ.setdefault("VSI_CACHE", "TRUE")                     # cache range-request headers
_os.environ.setdefault("VSI_CACHE_SIZE", "25000000")            # 25 MB VSI header cache

# I/O-bound COG reads benefit from more threads than CPU cores.
_READ_POOL = ThreadPoolExecutor(max_workers=_os.cpu_count() * 4)

# Limit concurrent renders per worker to prevent _READ_POOL starvation when
# many requests arrive simultaneously. Sized to cpu_count so each render
# gets roughly one CPU worth of file-read threads.
_RENDER_SEM = asyncio.Semaphore(_os.cpu_count())


_FORMATS = {"image/png": "PNG", "image/webp": "WEBP"}
_MEDIA_TYPES = {"PNG": "image/png", "WEBP": "image/webp"}


@dataclass
class WmsParams:
    """Parsed WMS GetMap parameters."""

    layer_name: str
    bbox: tuple
    crs: str
    srid: int
    width: int
    height: int
    time: datetime
    fmt: str = "PNG"


@functools.lru_cache(maxsize=16)
def _is_geographic(crs: str) -> bool:
    """Return True if the CRS uses geographic (lat/lon) axis order (cached)."""
    authority, code = crs.split(":")
    return pyproj.CRS.from_authority(authority, code).is_geographic


def _parse_params(params: dict) -> WmsParams:
    """Parse and validate raw WMS query parameters."""
    layer_name = params["LAYERS"]
    crs = params["CRS"]
    srid = int(crs.split(":")[1])
    bbox = tuple(float(v) for v in params["BBOX"].split(","))
    # WMS 1.3.0 uses CRS-defined axis order. Geographic CRS like EPSG:4326 are
    # lat/lon (latitude first), so BBOX arrives as (minlat, minlon, maxlat, maxlon).
    # Swap to (minlon, minlat, maxlon, maxlat) for consistent easting-first handling.
    if _is_geographic(crs):
        bbox = (bbox[1], bbox[0], bbox[3], bbox[2])
    width = int(params["WIDTH"])
    height = int(params["HEIGHT"])
    time_str = params.get("TIME")
    time = (
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        if time_str
        else datetime.now(timezone.utc)
    )
    fmt = _FORMATS.get(params.get("FORMAT", "").lower(), "PNG")
    return WmsParams(layer_name=layer_name, bbox=bbox, crs=crs, srid=srid,
                     width=width, height=height, time=time, fmt=fmt)


@functools.lru_cache(maxsize=config.get("tile_cache_entries"))
def _read_one(fp: str, bbox: tuple, dst_crs: str, width: int, height: int) -> ImageData:
    """Read a single GeoTIFF into an ImageData (runs in a thread)."""
    dst = CRS.from_authority(*dst_crs.split(":"))
    with COGReader(fp) as cog:
        return cog.part(bbox, bounds_crs=dst, dst_crs=dst, width=width, height=height)


@functools.lru_cache(maxsize=32)
def _empty_png(width: int, height: int) -> bytes:
    """Return a fully transparent RGBA PNG using stdlib only (cached per size)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"\x00" * (height * (width * 4 + 1))  # filter-none byte + RGBA zeros per row
    idat = chunk(b"IDAT", zlib.compress(raw, 1))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


@functools.lru_cache(maxsize=32)
def _empty_image(width: int, height: int, img_format: str) -> bytes:
    """Return a fully transparent image in the requested format (cached per size+format)."""
    if img_format == "PNG":
        return _empty_png(width, height)
    mask = np.ones((1, height, width), dtype=bool)
    data = np.ma.MaskedArray(np.zeros((1, height, width), dtype=np.uint8), mask)
    return ImageData(data).render(img_format=img_format)


async def generate_map(
    mda, params: dict, duration: timedelta, interval_min: int = 10,
    force_webp: bool = False, empty_no_content: bool = False,
) -> Response:
    """Handle a WMS GetMap request."""
    p = _parse_params(params)
    if force_webp:
        p = dataclasses.replace(p, fmt="WEBP")
    end_dt = p.time
    start_dt = end_dt - duration

    # Run both queries concurrently — they are independent.
    latest, filepaths = await asyncio.gather(
        mda.get_latest_time(p.layer_name),
        mda.get_map_assets(p.layer_name, list(p.bbox), start_dt, end_dt, src_srid=p.srid),
    )

    # Short TTL if the requested time falls in the same interval bucket as the
    # latest granule — new data may still be arriving for this window.
    is_latest = latest is not None and floor_dt(latest, interval_min) == floor_dt(end_dt, interval_min)
    cache_control = "public, max-age=60" if is_latest else "public, max-age=3600"
    headers = {"Cache-Control": cache_control}

    media_type = _MEDIA_TYPES[p.fmt]

    if not filepaths:
        if empty_no_content:
            return Response(status_code=204, headers=headers)
        return Response(content=_empty_image(p.width, p.height, p.fmt), media_type=media_type,
                        headers=headers)

    async with _RENDER_SEM:
        loop = asyncio.get_running_loop()
        # Submit all reads to the pool simultaneously so they run in parallel,
        # then await them in list order (newest file first = highest composite priority).
        aws = [loop.run_in_executor(_READ_POOL, _read_one, fp, p.bbox, p.crs, p.width, p.height)
               for fp in filepaths]
        result = None
        for aw in aws:
            img = await aw
            if result is None:
                result = img
                continue
            if not np.any(result.array.mask):
                break  # canvas full; remaining reads continue in pool but we don't wait
            no_data = np.all(result.array.mask, axis=0, keepdims=True)
            data = np.where(no_data, img.array.data, result.array.data)
            mask = np.broadcast_to(
                no_data & np.all(img.array.mask, axis=0, keepdims=True),
                data.shape,
            ).copy()
            result = ImageData(np.ma.MaskedArray(data, mask), bounds=result.bounds, crs=result.crs)
        render_kwargs = {"img_format": p.fmt} if p.fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
        image = await loop.run_in_executor(None, functools.partial(result.render, **render_kwargs))

    return Response(content=image, media_type=media_type, headers=headers)
