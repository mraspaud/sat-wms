"""GetMap request handler."""
import asyncio
import functools
import io
import os as _os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pyproj
import rasterio
from fastapi import Response
from rasterio.crs import CRS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData

from sat_wms.time_utils import ceil_dt, floor_dt

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


def _parse_params(params: dict) -> WmsParams:
    """Parse and validate raw WMS query parameters."""
    layer_name = params["LAYERS"]
    crs = params["CRS"]
    srid = int(crs.split(":")[1])
    bbox = tuple(float(v) for v in params["BBOX"].split(","))
    # WMS 1.3.0 uses CRS-defined axis order. Geographic CRS like EPSG:4326 are
    # lat/lon (latitude first), so BBOX arrives as (minlat, minlon, maxlat, maxlon).
    # Swap to (minlon, minlat, maxlon, maxlat) for consistent easting-first handling.
    if pyproj.CRS.from_authority(*crs.split(":")).is_geographic:
        bbox = (bbox[1], bbox[0], bbox[3], bbox[2])
    width = int(params["WIDTH"])
    height = int(params["HEIGHT"])
    time_str = params.get("TIME")
    time = (
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        if time_str
        else datetime.now(timezone.utc)
    )
    return WmsParams(layer_name=layer_name, bbox=bbox, crs=crs, srid=srid,
                     width=width, height=height, time=time)


@functools.lru_cache(maxsize=128)
def _read_one(fp: str, bbox: tuple, dst_crs: str, width: int, height: int) -> ImageData:
    """Read a single GeoTIFF into an ImageData (runs in a thread)."""
    dst = CRS.from_authority(*dst_crs.split(":"))
    with COGReader(fp) as cog:
        return cog.part(bbox, bounds_crs=dst, dst_crs=dst, width=width, height=height)


def _render(filepaths, bbox, width, height, dst_crs):
    """Read all GeoTIFFs in parallel, composite newest-first, return PNG bytes.

    Composites as each future arrives (streaming): once the canvas is full,
    remaining slow futures are skipped rather than waited on.

    Runs synchronously — call via asyncio.to_thread so the event loop
    stays free to accept other requests while GDAL does I/O.
    """
    futures = [_READ_POOL.submit(_read_one, fp, bbox, dst_crs, width, height) for fp in filepaths]
    result = None
    for f in futures:
        img = f.result()
        if result is None:
            result = img
            continue
        if not np.any(result.array.mask):
            break  # canvas full; skip remaining futures
        no_data = np.all(result.array.mask, axis=0, keepdims=True)
        data = np.where(no_data, img.array.data, result.array.data)
        mask = np.broadcast_to(
            no_data & np.all(img.array.mask, axis=0, keepdims=True),
            data.shape,
        ).copy()
        result = ImageData(np.ma.MaskedArray(data, mask), bounds=result.bounds, crs=result.crs)
    return result.render(img_format="PNG", zlevel=1)


def _empty_png(width, height):
    """Return a transparent PNG of the given dimensions."""
    data = np.zeros((4, height, width), dtype=np.uint8)
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="PNG", width=width, height=height, count=4,
                      dtype=np.uint8,
                      transform=rasterio.transform.from_bounds(0, 0, 1, 1, width, height)) as dst:
            dst.write(data)
        buf.write(mem.read())
    return buf.getvalue()


async def generate_map(mda, params: dict, duration: timedelta) -> Response:
    """Handle a WMS GetMap request."""
    p = _parse_params(params)
    end_dt = floor_dt(p.time)
    start_dt = end_dt - duration

    # Short TTL if this is the latest available timestep (data may still be arriving).
    # Compare end_dt against ceil of the actual latest granule time — not wall clock,
    # since data can lag real time by 20+ minutes.
    latest = await mda.get_latest_time(p.layer_name)
    is_latest = latest is not None and ceil_dt(latest) == end_dt
    cache_control = "public, max-age=60" if is_latest else "public, max-age=3600"
    headers = {"Cache-Control": cache_control}

    filepaths = await mda.get_map_assets(
        p.layer_name, list(p.bbox), start_dt, end_dt, src_srid=p.srid,
    )

    if not filepaths:
        return Response(content=_empty_png(p.width, p.height), media_type="image/png",
                        headers=headers)

    async with _RENDER_SEM:
        png = await asyncio.to_thread(_render, filepaths, p.bbox, p.width, p.height, p.crs)
    return Response(content=png, media_type="image/png", headers=headers)
