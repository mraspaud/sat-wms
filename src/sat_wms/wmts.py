"""WMTS 1.0.0 request handlers."""
import asyncio
import functools
from datetime import datetime, timedelta, timezone

import morecantile
import numpy as np
import pyproj
from fastapi import Response
from fastapi.templating import Jinja2Templates
from rio_tiler.errors import TileOutsideBounds

from sat_wms.config import config
from sat_wms.rendering import MEDIA_TYPES, READ_POOL, RENDER_SEM, cache_control, empty_image, merge_images
from sat_wms.time_utils import ceil_dt, floor_dt
from sat_wms.tms_registry import all_tms, build_registry, get_by_name

_templates = Jinja2Templates(directory="templates")

# Reproject from EPSG:3575 (storage CRS) to WGS84 for ows:WGS84BoundingBox.
_TO_WGS84 = pyproj.Transformer.from_crs("EPSG:3575", "EPSG:4326", always_xy=True)


def _ows_exception(msg: str, code: str) -> Response:
    """Return an OWS Common 1.1.0 ExceptionReport response (400)."""
    content = _templates.get_template("ows_exception.xml.j2").render(msg=msg, code=code)
    return Response(content=content, media_type="text/xml", status_code=400)


@functools.lru_cache(maxsize=config.get("tile_cache_entries"))
def _read_tile(fp: str, tms_id: str, x: int, y: int, z: int):
    """Read a 256×256 tile from a COG via COGReader.tile() (LRU-cached, runs in a thread)."""
    from rio_tiler.io import COGReader  # noqa: PLC0415

    with COGReader(fp, tms=get_by_name(tms_id)) as cog:
        try:
            return cog.tile(x, y, z)
        except TileOutsideBounds:
            return None


def _tms_entry(tms: morecantile.TileMatrixSet, max_zoom: int) -> dict:
    """Serialise a TileMatrixSet into a template-friendly dict."""
    # OGC URN format: OpenLayers resolves this via ol/proj; the HTTP URI form is not recognised.
    crs_uri = f"urn:ogc:def:crs:EPSG::{tms.crs.to_epsg()}"
    matrices = []
    for z in range(tms.minzoom, min(tms.maxzoom, max_zoom) + 1):
        m = tms.matrix(z)
        ox, oy = m.pointOfOrigin
        matrices.append({
            "id": m.id,
            "scale_denominator": f"{m.scaleDenominator:.6f}",
            "top_left_corner": f"{ox} {oy}",
            "tile_width": m.tileWidth,
            "tile_height": m.tileHeight,
            "matrix_width": m.matrixWidth,
            "matrix_height": m.matrixHeight,
        })
    return {"id": tms.id, "title": tms.title, "crs_uri": crs_uri, "matrices": matrices}


def _bbox_to_wgs84(bbox_str: str) -> tuple[float, float, float, float]:
    """Reproject a PostGIS BOX string from EPSG:3575 to WGS84 lon/lat."""
    coords = bbox_str.replace("BOX(", "").replace(")", "").replace(",", " ").split()
    minx, miny, maxx, maxy = (float(c) for c in coords)
    lon_min, lat_min = _TO_WGS84.transform(minx, miny)
    lon_max, lat_max = _TO_WGS84.transform(maxx, maxy)
    return round(lon_min, 4), round(lat_min, 4), round(lon_max, 4), round(lat_max, 4)


async def generate_wmts_capabilities(
    mda,
    request=None,
    online_resource: str | None = None,
    supported_crs: list[str] | None = None,
    interval_min: int = 10,
    force_webp: bool = False,
    wmts_max_zoom: int = 9,
    duration_str: str | None = None,
) -> Response:
    """Render the WMTS 1.0.0 GetCapabilities document."""
    if supported_crs:
        build_registry(supported_crs)

    base_title = config.get("wms_title")
    title = f"{base_title} ({duration_str})" if duration_str else base_title

    raw_layers = await mda.get_layers()
    layers = []
    for layer in raw_layers:
        lon_min, lat_min, lon_max, lat_max = _bbox_to_wgs84(layer["bbox"])
        layers.append({
            "layer_name": layer["layer_name"],
            "start_str": floor_dt(layer["start_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "end_str": layer["end_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_range_str": ceil_dt(layer["end_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "lon_min": lon_min, "lat_min": lat_min,
            "lon_max": lon_max, "lat_max": lat_max,
        })

    tms_entries = [_tms_entry(t, wmts_max_zoom) for t in all_tms()]
    tile_formats = ["image/webp"] if force_webp else ["image/png", "image/webp"]

    return _templates.TemplateResponse(
        request,
        "wmts_capabilities.xml.j2",
        context={
            "title": title,
            "online_resource": online_resource or "",
            "layers": layers,
            "tms_entries": tms_entries,
            "tile_formats": tile_formats,
            "interval_iso": f"PT{interval_min}M",
        },
        media_type="text/xml",
    )


async def generate_tile(
    mda,
    layer: str,
    tms_id: str,
    z: int,
    y: int,
    x: int,
    duration: timedelta,
    time_str: str | None = None,
    fmt: str = "PNG",
    interval_min: int = 10,
    force_webp: bool = False,
    empty_no_content: bool = False,
    wmts_max_zoom: int = 9,
) -> Response:
    """Handle a WMTS GetTile request."""
    tms = get_by_name(tms_id)
    if tms is None:
        return _ows_exception(f"TileMatrixSet {tms_id!r} is not supported.", "InvalidParameterValue")

    if not (tms.minzoom <= z <= min(tms.maxzoom, wmts_max_zoom)):
        return _ows_exception(
            f"TileMatrix {z} is out of range [{tms.minzoom}, {min(tms.maxzoom, wmts_max_zoom)}].",
            "TileOutOfRange",
        )

    if force_webp:
        fmt = "WEBP"
    media_type = MEDIA_TYPES[fmt]

    end_dt = (
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        if time_str
        else datetime.now(timezone.utc)
    )
    start_dt = end_dt - duration

    bounds = tms.xy_bounds(morecantile.Tile(x, y, z))
    bbox_list = [bounds.left, bounds.bottom, bounds.right, bounds.top]
    src_srid = tms.crs.to_epsg()

    latest, filepaths = await asyncio.gather(
        mda.get_latest_time(layer),
        mda.get_map_assets(layer, bbox_list, start_dt, end_dt, src_srid=src_srid),
    )

    headers = {"Cache-Control": cache_control(latest, end_dt, interval_min)}

    if not filepaths:
        if empty_no_content:
            return Response(status_code=204, headers=headers)
        return Response(content=empty_image(256, 256, fmt), media_type=media_type, headers=headers)

    async with RENDER_SEM:
        loop = asyncio.get_running_loop()
        aws = [
            loop.run_in_executor(READ_POOL, _read_tile, fp, tms_id, x, y, z)
            for fp in filepaths
        ]
        result = None
        for aw in aws:
            img = await aw
            if result is None:
                result = img
                continue
            if not np.any(result.array.mask):
                break
            result = merge_images(result, img)

        render_kwargs = {"img_format": fmt} if fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
        image = await loop.run_in_executor(
            None, functools.partial(result.render, **render_kwargs)
        )

    return Response(content=image, media_type=media_type, headers=headers)
