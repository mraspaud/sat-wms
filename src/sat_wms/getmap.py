"""GetMap request handler."""
import asyncio
import dataclasses
import functools
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pyproj
from async_lru import alru_cache
from fastapi import Response
from rasterio.crs import CRS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData

from sat_wms.config import config
from sat_wms.rendering import MEDIA_TYPES, READ_POOL, RENDER_SEM, TILE_FORMATS, cache_control, empty_image
from sat_wms.time_utils import parse_end_time


class WmsRequestError(ValueError):
    """Raised for invalid or out-of-range WMS GetMap parameters."""


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
def _to_request_crs(src_srid: int, dst_srid: int):
    """Return a cached pyproj Transformer between two SRIDs."""
    return pyproj.Transformer.from_crs(f"EPSG:{src_srid}", f"EPSG:{dst_srid}", always_xy=True)


def _granule_pixel_region(granule_bbox, granule_srid, canvas_bbox, canvas_srid, width, height):
    """Return (sub_bbox, col_min, row_min, col_max, row_max) or None if no overlap.

    All bboxes are (minx, miny, maxx, maxy).
    """
    gminx, gminy, gmaxx, gmaxy = granule_bbox
    if granule_srid != canvas_srid:
        tx = _to_request_crs(granule_srid, canvas_srid)
        corners = [tx.transform(x, y) for x, y in ((gminx, gminy), (gminx, gmaxy), (gmaxx, gminy), (gmaxx, gmaxy))]
        gminx = min(c[0] for c in corners)
        gmaxx = max(c[0] for c in corners)
        gminy = min(c[1] for c in corners)
        gmaxy = max(c[1] for c in corners)
    cminx, cminy, cmaxx, cmaxy = canvas_bbox
    sub_minx, sub_miny = max(gminx, cminx), max(gminy, cminy)
    sub_maxx, sub_maxy = min(gmaxx, cmaxx), min(gmaxy, cmaxy)
    if sub_minx >= sub_maxx or sub_miny >= sub_maxy:
        return None
    dx, dy = cmaxx - cminx, cmaxy - cminy
    col_min = max(0, round((sub_minx - cminx) / dx * width))
    col_max = min(width, round((sub_maxx - cminx) / dx * width))
    row_min = max(0, round((cmaxy - sub_maxy) / dy * height))
    row_max = min(height, round((cmaxy - sub_miny) / dy * height))
    if col_min >= col_max or row_min >= row_max:
        return None
    return (sub_minx, sub_miny, sub_maxx, sub_maxy), col_min, row_min, col_max, row_max


@functools.lru_cache(maxsize=16)
def _is_geographic(crs: str) -> bool:
    """Return True if the CRS uses geographic (lat/lon) axis order (cached)."""
    authority, code = crs.split(":")
    return pyproj.CRS.from_authority(authority, code).is_geographic


def _parse_params(params: dict) -> WmsParams:
    """Parse and validate raw WMS query parameters.

    Raises:
        WmsRequestError: on missing, malformed, or out-of-range parameters.
    """
    try:
        layer_name = params["LAYERS"]
        crs = params["CRS"]
        srid = int(crs.split(":")[1])
        bbox = tuple(float(v) for v in params["BBOX"].split(","))
    except (KeyError, ValueError, IndexError) as exc:
        raise WmsRequestError(f"Missing or malformed required parameter: {exc}") from exc

    # WMS 1.3.0 uses CRS-defined axis order. Geographic CRS like EPSG:4326 are
    # lat/lon (latitude first), so BBOX arrives as (minlat, minlon, maxlat, maxlon).
    # Swap to (minlon, minlat, maxlon, maxlat) for consistent easting-first handling.
    if _is_geographic(crs):
        bbox = (bbox[1], bbox[0], bbox[3], bbox[2])

    try:
        width = int(params["WIDTH"])
        height = int(params["HEIGHT"])
    except (KeyError, ValueError) as exc:
        raise WmsRequestError(f"WIDTH and HEIGHT must be positive integers: {exc}") from exc

    max_pixels = int(config.get("max_map_pixels"))
    if width < 1 or height < 1:
        raise WmsRequestError("WIDTH and HEIGHT must be positive integers.")
    if width > max_pixels or height > max_pixels:
        raise WmsRequestError(f"WIDTH and HEIGHT must not exceed {max_pixels} pixels.")

    time_str = params.get("TIME")
    time = parse_end_time(time_str)

    fmt = TILE_FORMATS.get(params.get("FORMAT", "").lower(), "PNG")
    return WmsParams(layer_name=layer_name, bbox=bbox, crs=crs, srid=srid,
                     width=width, height=height, time=time, fmt=fmt)


def _read_one_sync(fp: str, bbox: tuple, dst_crs: str, width: int, height: int):
    """Read a single GeoTIFF into an ImageData (runs in a thread). Returns None if file is missing."""
    import contextlib  # noqa: PLC0415
    import logging  # noqa: PLC0415

    import rasterio  # noqa: PLC0415
    from rasterio.enums import Resampling  # noqa: PLC0415
    from rasterio.transform import from_gcps  # noqa: PLC0415
    from rasterio.vrt import WarpedVRT  # noqa: PLC0415

    dst = CRS.from_authority(*dst_crs.split(":"))
    try:
        with rasterio.Env():
            with contextlib.ExitStack() as stack:
                src = stack.enter_context(rasterio.open(fp))
                gcps, gcp_crs = src.gcps
                if gcps:
                    dataset = stack.enter_context(
                        WarpedVRT(src, src_crs=gcp_crs, src_transform=from_gcps(gcps),
                                  crs=dst, resampling=Resampling.bilinear, add_alpha=True)
                    )
                else:
                    dataset = src
                cog = stack.enter_context(COGReader(fp, dataset=dataset))
                return cog.part(bbox, bounds_crs=dst, dst_crs=dst, width=width, height=height,
                                resampling_method="bilinear")
    except (OSError, rasterio.errors.RasterioIOError):
        logging.getLogger(__name__).warning("File not found, skipping: %s", fp)
        return None


@alru_cache(maxsize=config.get("tile_cache_entries"), ttl=300)
async def _read_one(fp: str, bbox: tuple, dst_crs: str, width: int, height: int):
    """Async LRU-cached granule read with 5-min TTL; coalesces concurrent requests."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(READ_POOL, _read_one_sync, fp, bbox, dst_crs, width, height)


def _merge_sub(canvas, gaps, img, col_min, row_min, col_max, row_max):
    """Paste img into the sub-region of canvas defined by pixel coords, respecting gaps."""
    sub_canvas = canvas[:, row_min:row_max, col_min:col_max]
    sub_gaps = gaps[row_min:row_max, col_min:col_max]
    has_data = ~np.all(img.array.mask, axis=0)
    fill = sub_gaps & has_data
    if np.any(fill):
        sub_canvas[:, fill] = img.array.data[:, fill]
        sub_gaps[fill] = False


async def _read_and_composite(assets, p):
    """Read each asset at its canvas sub-region size in parallel, composite in priority order."""
    canvas = None
    gaps = None
    dst_crs_obj = CRS.from_authority(*p.crs.split(":"))

    regions = []
    reads = []
    for a in assets:
        region = _granule_pixel_region(a["bbox"], a["bbox_srid"], p.bbox, p.srid, p.width, p.height)
        if region is None:
            continue
        sub_bbox, col_min, row_min, col_max, row_max = region
        regions.append((col_min, row_min, col_max, row_max))
        sub_w, sub_h = col_max - col_min, row_max - row_min
        reads.append(_read_one(a["filename"], sub_bbox, p.crs, sub_w, sub_h))

    if not reads:
        return None

    images = await asyncio.gather(*reads)
    for img, (col_min, row_min, col_max, row_max) in zip(images, regions, strict=False):
        if img is None:
            continue
        if canvas is None:
            canvas = np.zeros((img.array.shape[0], p.height, p.width), dtype=img.array.dtype)
            gaps = np.ones((p.height, p.width), dtype=bool)
        _merge_sub(canvas, gaps, img, col_min, row_min, col_max, row_max)
        if not np.any(gaps):
            break

    if canvas is None:
        return None
    full_mask = np.broadcast_to(gaps[np.newaxis, :, :], canvas.shape).copy()
    return ImageData(np.ma.MaskedArray(canvas, full_mask), bounds=p.bbox, crs=dst_crs_obj)


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

    latest, assets = await asyncio.gather(
        mda.get_latest_time(p.layer_name),
        mda.get_map_assets(p.layer_name, list(p.bbox), start_dt, end_dt, src_srid=p.srid),
    )

    headers = {"Cache-Control": cache_control(latest, end_dt, interval_min,
                                              stepped=config.get("timestep_mode") == "stepped")}
    media_type = MEDIA_TYPES[p.fmt]

    if not assets:
        if empty_no_content:
            return Response(status_code=204, headers=headers)
        return Response(content=empty_image(p.width, p.height, p.fmt), media_type=media_type,
                        headers=headers)

    async with RENDER_SEM:
        result = await _read_and_composite(assets, p)

        if result is None:
            return Response(content=empty_image(p.width, p.height, p.fmt), media_type=media_type,
                            headers=headers)

        render_kwargs = {"img_format": p.fmt} if p.fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
        image = await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(result.render, **render_kwargs),
        )

    return Response(content=image, media_type=media_type, headers=headers)
