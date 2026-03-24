"""GetMap request handler."""
import asyncio
import io
import os as _os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from fastapi import Response
from rasterio.crs import CRS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData

from sat_wms.time_utils import floor_dt

# Increase GDAL tile cache if not already configured — dramatically speeds up
# repeated reads of the same GeoTIFF tiles across requests.
_os.environ.setdefault("GDAL_CACHEMAX", "512")

# Shared executor for parallel file reads within a single request.
# Sized to cpu_count so we don't over-subscribe across concurrent renders.
_READ_POOL = ThreadPoolExecutor(max_workers=_os.cpu_count())


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
    bbox = tuple(float(v) for v in params["BBOX"].split(","))
    crs = params["CRS"]
    srid = int(crs.split(":")[1])
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


def _read_one(fp, bbox, dst, width, height):
    """Read a single GeoTIFF into an ImageData (runs in a thread)."""
    with COGReader(fp) as cog:
        return cog.part(bbox, bounds_crs=dst, dst_crs=dst,
                        width=width, height=height)


def _composite(imgs):
    """First-valid-pixel composite, preserving list order (index 0 = highest priority)."""
    result = imgs[0]
    for img in imgs[1:]:
        if not np.any(result.array.mask):
            break  # canvas full, no holes left
        no_data = np.all(result.array.mask, axis=0, keepdims=True)
        data = np.where(no_data, img.array.data, result.array.data)
        mask = np.broadcast_to(
            no_data & np.all(img.array.mask, axis=0, keepdims=True),
            data.shape,
        ).copy()
        result = ImageData(np.ma.MaskedArray(data, mask),
                           bounds=result.bounds, crs=result.crs)
    return result


def _render(filepaths, bbox, width, height, dst_crs):
    """Read all GeoTIFFs in parallel, composite newest-first, return PNG bytes.

    Runs synchronously — call via asyncio.to_thread so the event loop
    stays free to accept other requests while GDAL does I/O.
    """
    dst = CRS.from_authority(*dst_crs.split(":"))

    # Read all files concurrently; preserve submission order for compositing.
    futures = [_READ_POOL.submit(_read_one, fp, bbox, dst, width, height) for fp in filepaths]
    imgs = [f.result() for f in futures]  # in order: newest first

    return _composite(imgs).render(img_format="PNG", zlevel=1)


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

    filepaths = await mda.get_map_assets(
        p.layer_name, list(p.bbox), start_dt, end_dt, src_srid=p.srid,
    )

    if not filepaths:
        return Response(content=_empty_png(p.width, p.height), media_type="image/png")

    png = await asyncio.to_thread(_render, filepaths, p.bbox, p.width, p.height, p.crs)
    return Response(content=png, media_type="image/png")
