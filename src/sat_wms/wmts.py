"""WMTS 1.0.0 request handlers."""
import asyncio
import functools
import hashlib
from datetime import timedelta
from importlib.resources import files

import morecantile
import numpy as np
import pyproj
from fastapi import Response
from fastapi.templating import Jinja2Templates
from rio_tiler.errors import TileOutsideBounds

from sat_wms.config import config, get_supported_crs
from sat_wms.postgis_utils import parse_postgis_box
from sat_wms.rendering import MEDIA_TYPES, READ_POOL, RENDER_SEM, cache_control, composite_images, empty_image
from sat_wms.tile_cache import TileCache
from sat_wms.time_utils import (
    _to_iso_duration,
    ceil_dt,
    compute_snapshot_times,
    floor_dt,
    parse_duration,
    parse_end_time,
)
from sat_wms.tms_registry import all_tms, build_registry, get_by_name

TYPE_CHECKING = False
if TYPE_CHECKING:
    from rio_tiler.models import ImageData

_BATCH_SIZE = 32

_templates = Jinja2Templates(directory=str(files("sat_wms").joinpath("templates")))

# Reproject from EPSG:3575 (storage CRS) to WGS84 for ows:WGS84BoundingBox.
_TO_WGS84 = pyproj.Transformer.from_crs("EPSG:3575", "EPSG:4326", always_xy=True)


def _ows_exception(msg: str, code: str) -> Response:
    """Return an OWS Common 1.1.0 ExceptionReport response (400)."""
    content = _templates.get_template("ows_exception.xml.j2").render(msg=msg, code=code)
    return Response(content=content, media_type="text/xml", status_code=400)


@functools.lru_cache(maxsize=config.get("tile_cache_entries"))
def _read_tile(fp: str, tms_id: str, x: int, y: int, z: int):
    """Read a 256×256 tile from a COG via COGReader.tile() (LRU-cached, runs in a thread).

    If the file carries GCPs (e.g. raw SAR granules) the dataset is pre-wrapped in a
    WarpedVRT targeting the TMS CRS so that GDAL overview selection operates in metres
    rather than degrees, which prevents pixelation at high latitudes.
    """
    import contextlib  # noqa: PLC0415
    import logging  # noqa: PLC0415

    import rasterio  # noqa: PLC0415
    from rasterio.enums import Resampling  # noqa: PLC0415
    from rasterio.transform import from_gcps  # noqa: PLC0415
    from rasterio.vrt import WarpedVRT  # noqa: PLC0415
    from rio_tiler.io import COGReader  # noqa: PLC0415

    tms = get_by_name(tms_id)
    try:
        with rasterio.Env():
            with contextlib.ExitStack() as stack:
                src = stack.enter_context(rasterio.open(fp))
                gcps, gcp_crs = src.gcps
                if gcps:
                    dataset = stack.enter_context(
                        WarpedVRT(src, src_crs=gcp_crs, src_transform=from_gcps(gcps),
                                  crs=tms.crs, resampling=Resampling.bilinear, add_alpha=True)
                    )
                else:
                    dataset = src
                cog = stack.enter_context(COGReader(fp, dataset=dataset, tms=tms))
                try:
                    return cog.tile(x, y, z, resampling_method="bilinear")
                except TileOutsideBounds:
                    return None
    except (OSError, rasterio.errors.RasterioIOError):
        logging.getLogger(__name__).warning("File not found, skipping: %s", fp)
        return None


def _tms_entry(tms: morecantile.TileMatrixSet, max_zoom: int) -> dict:
    """Serialise a TileMatrixSet into a template-friendly dict."""
    # OGC URN format: OpenLayers resolves this via ol/proj; the HTTP URI form is not recognised.
    crs_uri = f"urn:ogc:def:crs:EPSG::{tms.crs.to_epsg()}"
    # Detect CRS with non-standard axis order (first axis = northing, e.g. EPSG:3301).
    # OGC WMTS spec requires TopLeftCorner in CRS-native axis order, but morecantile
    # always outputs pointOfOrigin in (east, north) order for projected CRS.
    pyproj_crs = pyproj.CRS.from_epsg(tms.crs.to_epsg())
    crs_axes_north_first = pyproj_crs.axis_info[0].direction == "north"
    matrices = []
    for z in range(tms.minzoom, min(tms.maxzoom, max_zoom) + 1):
        m = tms.matrix(z)
        ox, oy = m.pointOfOrigin
        # Swap to CRS-native (north, east) order when the CRS has northing as its first axis.
        top_left_corner = f"{oy} {ox}" if crs_axes_north_first else f"{ox} {oy}"
        matrices.append({
            "id": m.id,
            "scale_denominator": f"{m.scaleDenominator:.6f}",
            "top_left_corner": top_left_corner,
            "tile_width": m.tileWidth,
            "tile_height": m.tileHeight,
            "matrix_width": m.matrixWidth,
            "matrix_height": m.matrixHeight,
        })
    return {"id": tms.id, "title": tms.title, "crs_uri": crs_uri, "matrices": matrices}


def _bbox_to_wgs84(bbox_str: str) -> tuple[float, float, float, float]:
    """Reproject a PostGIS BOX string from EPSG:3575 to WGS84 lon/lat."""
    minx, miny, maxx, maxy = parse_postgis_box(bbox_str)
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
    layer_name_prefix: str = "",
) -> Response:
    """Render the WMTS 1.0.0 GetCapabilities document."""
    build_registry(supported_crs if supported_crs is not None else get_supported_crs())

    base_title = config.get("wms_title")
    title = f"{base_title} ({duration_str})" if duration_str else base_title

    raw_layers = await mda.get_layers()
    stepped = config.get("timestep_mode") == "stepped"
    layers = []
    for layer in raw_layers:
        lon_min, lat_min, lon_max, lat_max = _bbox_to_wgs84(layer["bbox"])
        entry = {
            "layer_name": layer_name_prefix + layer["layer_name"],
            "start_str": floor_dt(layer["start_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "end_str": layer["end_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_range_str": ceil_dt(layer["end_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "lon_min": lon_min, "lat_min": lat_min,
            "lon_max": lon_max, "lat_max": lat_max,
            "time_values": None,
        }
        if stepped:
            snapshot_step = parse_duration(config["snapshot_step"])
            snapshot_count = int(config["snapshot_count"])
            times = compute_snapshot_times(layer["end_time"], snapshot_step, snapshot_count)
            entry["time_values"] = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
        layers.append(entry)

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
            "interval_iso": _to_iso_duration(interval_min),
        },
        media_type="text/xml",
    )


async def _composite_tiles(filepaths: list[str], tms_id: str, x: int, y: int, z: int) -> "ImageData | None":
    """Read and composite tiles from filepaths, returning None if all are out-of-bounds."""
    loop = asyncio.get_running_loop()
    images = []
    gaps = None

    for start in range(0, len(filepaths), _BATCH_SIZE):
        batch = filepaths[start:start + _BATCH_SIZE]
        aws = [loop.run_in_executor(READ_POOL, _read_tile, fp, tms_id, x, y, z) for fp in batch]
        batch_images = await asyncio.gather(*aws)
        for img in batch_images:
            images.append(img)
            if img is not None:
                no_data = np.all(img.array.mask, axis=0)
                gaps = no_data if gaps is None else (gaps & no_data)

        if gaps is not None and not np.any(gaps):
            break

    return composite_images(images)


def _empty_response(fmt: str, media_type: str, headers: dict, empty_no_content: bool) -> Response:
    """Return a 204 or transparent-image response for tiles with no renderable data."""
    if empty_no_content:
        return Response(status_code=204, headers=headers)
    return Response(content=empty_image(256, 256, fmt), media_type=media_type, headers=headers)


async def _resolve_cache_and_assets(
    mda, tile_cache: TileCache, layer: str, tms_id: str,
    z: int, y: int, x: int, bbox_list: list, src_srid: int,
    start_dt, end_dt, time_bucket: str, ext: str, interval_min: int,
) -> tuple[dict, list, str, bytes | None]:
    """Determine cache key and headers; return (headers, assets, files_key, cached_bytes).

    Stepped mode fetches MDA first (the file list determines the cache key, so out-of-order
    granules within a time window are never silently ignored).  Interval mode checks the disk
    cache first and only fetches MDA on a miss.
    """
    stepped = config.get("timestep_mode") == "stepped"
    if stepped:
        latest, assets = await asyncio.gather(
            mda.get_latest_time(layer),
            mda.get_map_assets(layer, bbox_list, start_dt, end_dt, src_srid=src_srid),
        )
        headers = {"Cache-Control": cache_control(latest, end_dt, interval_min)}
        files_key = hashlib.sha1(  # noqa: S324
            "|".join(sorted(a["filename"] for a in assets)).encode()
        ).hexdigest()[:16]
        cached = tile_cache.get(layer, tms_id, z, y, x, files_key, ext)
        return headers, assets, files_key, cached

    cached = tile_cache.get(layer, tms_id, z, y, x, time_bucket, ext)
    if cached is not None:
        return {}, [], time_bucket, cached
    latest, assets = await asyncio.gather(
        mda.get_latest_time(layer),
        mda.get_map_assets(layer, bbox_list, start_dt, end_dt, src_srid=src_srid),
    )
    headers = {"Cache-Control": cache_control(latest, end_dt, interval_min)}
    return headers, assets, time_bucket, None


async def _render_tile_image(filepaths: list[str], tms_id: str, x: int, y: int, z: int, fmt: str) -> bytes | None:
    """Composite granule tiles and encode to the requested format. Returns None if all tiles are empty."""
    result = await _composite_tiles(filepaths, tms_id, x, y, z)
    if result is None:
        return None
    render_kwargs = {"img_format": fmt} if fmt != "PNG" else {"img_format": "PNG", "zlevel": 1}
    return await asyncio.get_running_loop().run_in_executor(
        None, functools.partial(result.render, **render_kwargs)
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
    ext = fmt.lower()

    end_dt = parse_end_time(time_str)
    start_dt = end_dt - duration
    time_bucket = floor_dt(end_dt, interval_min).strftime("%Y%m%dT%H%M")

    tile_cache = TileCache(cache_dir=config.get("tile_cache_dir"), ttl_days=config.get("tile_cache_ttl_days"))
    bounds = tms.xy_bounds(morecantile.Tile(x, y, z))
    bbox_list = [bounds.left, bounds.bottom, bounds.right, bounds.top]
    src_srid = tms.crs.to_epsg()

    headers, assets, files_key, cached = await _resolve_cache_and_assets(
        mda, tile_cache, layer, tms_id, z, y, x, bbox_list, src_srid,
        start_dt, end_dt, time_bucket, ext, interval_min,
    )

    if cached is not None:
        return Response(content=cached, media_type=media_type, headers=headers)

    if not assets:
        return _empty_response(fmt, media_type, headers, empty_no_content)

    async with RENDER_SEM:
        image = await _render_tile_image([a["filename"] for a in assets], tms_id, x, y, z, fmt)

    if image is None:
        return _empty_response(fmt, media_type, headers, empty_no_content)

    tile_cache.put(layer, tms_id, z, y, x, files_key, ext, image)
    return Response(content=image, media_type=media_type, headers=headers)
