"""GetMap request handler."""
import asyncio
import dataclasses
import functools
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pyproj
from fastapi import Response
from rasterio.crs import CRS
from rio_tiler.io import COGReader

from sat_wms.config import config
from sat_wms.rendering import MEDIA_TYPES, READ_POOL, RENDER_SEM, TILE_FORMATS, cache_control, empty_image, merge_images


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
    fmt = TILE_FORMATS.get(params.get("FORMAT", "").lower(), "PNG")
    return WmsParams(layer_name=layer_name, bbox=bbox, crs=crs, srid=srid,
                     width=width, height=height, time=time, fmt=fmt)


@functools.lru_cache(maxsize=config.get("tile_cache_entries"))
def _read_one(fp: str, bbox: tuple, dst_crs: str, width: int, height: int):
    """Read a single GeoTIFF into an ImageData (runs in a thread)."""
    dst = CRS.from_authority(*dst_crs.split(":"))
    with COGReader(fp) as cog:
        return cog.part(bbox, bounds_crs=dst, dst_crs=dst, width=width, height=height)


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

    latest, filepaths = await asyncio.gather(
        mda.get_latest_time(p.layer_name),
        mda.get_map_assets(p.layer_name, list(p.bbox), start_dt, end_dt, src_srid=p.srid),
    )

    headers = {"Cache-Control": cache_control(latest, end_dt, interval_min)}

    media_type = MEDIA_TYPES[p.fmt]

    if not filepaths:
        if empty_no_content:
            return Response(status_code=204, headers=headers)
        return Response(content=empty_image(p.width, p.height, p.fmt), media_type=media_type,
                        headers=headers)

    async with RENDER_SEM:
        loop = asyncio.get_running_loop()
        # Submit all reads to the pool simultaneously so they run in parallel,
        # then await them in list order (newest file first = highest composite priority).
        aws = [loop.run_in_executor(READ_POOL, _read_one, fp, p.bbox, p.crs, p.width, p.height)
               for fp in filepaths]
        result = None
        for aw in aws:
            img = await aw
            if result is None:
                result = img
                continue
            if not np.any(result.array.mask):
                break  # canvas full; remaining reads continue in pool but we don't wait
            result = merge_images(result, img)
        render_kwargs = {"img_format": p.fmt} if p.fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
        image = await loop.run_in_executor(None, functools.partial(result.render, **render_kwargs))

    return Response(content=image, media_type=media_type, headers=headers)
