"""WMTS 1.0.0 request handlers."""
import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import timedelta
from importlib.resources import files

import morecantile
import numpy as np
import pyproj
from async_lru import alru_cache
from fastapi import Response
from fastapi.templating import Jinja2Templates
from rio_tiler.errors import TileOutsideBounds

from sat_wms.config import config, get_supported_crs
from sat_wms.postgis_utils import parse_postgis_box
from sat_wms.rendering import (
    DEFAULT_RENDER_OPTIONS,
    MEDIA_TYPES,
    READ_POOL,
    RENDER_SEM,
    RenderOptions,
    cache_control,
    composite_images,
    empty_image,
    encode_image,
    is_range_probe,
    range_probe_response,
    read_cog,
)
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


@dataclass(frozen=True)
class TileCoord:
    """A WMTS tile address: tile-matrix-set id plus zoom/row/column."""

    tms_id: str
    z: int
    y: int
    x: int


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


def _bbox_to_wgs84(bbox_str: str) -> tuple[float, float, float, float]:
    """Reproject a PostGIS BOX string from EPSG:3575 to WGS84 lon/lat."""
    minx, miny, maxx, maxy = parse_postgis_box(bbox_str)
    lon_min, lat_min = _TO_WGS84.transform(minx, miny)
    lon_max, lat_max = _TO_WGS84.transform(maxx, maxy)
    return round(lon_min, 4), round(lat_min, 4), round(lon_max, 4), round(lat_max, 4)


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


async def generate_tile(
    mda,
    layer: str,
    tile: TileCoord,
    duration: timedelta,
    *,
    time_str: str | None = None,
    fmt: str = "PNG",
    options: RenderOptions = DEFAULT_RENDER_OPTIONS,
    range_header: str | None = None,
) -> Response:
    """Handle a WMTS GetTile request."""
    tms, error = _validate_tile(tile, options.wmts_max_zoom)
    if error is not None:
        return error

    if options.force_webp:
        fmt = "WEBP"
    media_type = MEDIA_TYPES[fmt]
    ext = fmt.lower()

    end_dt = parse_end_time(time_str)
    start_dt = end_dt - duration

    tile_cache = TileCache(
        cache_dir=config.get("tile_cache_dir"),
        ttl_days=int(config.get("tile_cache_ttl_days")),
        max_size_mb=int(config.get("tile_cache_max_size_mb")),
    )
    bounds = tms.xy_bounds(morecantile.Tile(tile.x, tile.y, tile.z))
    bbox_list = [bounds.left, bounds.bottom, bounds.right, bounds.top]

    headers, assets, files_key = await _resolve_assets(
        mda, layer, bbox_list, tms.crs.to_epsg(), start_dt, end_dt, options.interval_min,
    )
    cached = tile_cache.get(layer, tile.tms_id, tile.z, tile.y, tile.x, files_key, ext)

    if is_range_probe(range_header):
        return range_probe_response(cached is not None or bool(assets), media_type, headers)

    if cached is not None:
        return Response(content=cached, media_type=media_type, headers=headers)

    if not assets:
        return _empty_response(fmt, media_type, headers, options.empty_no_content)

    async with RENDER_SEM:
        image = await _render_tile_image([a["filename"] for a in assets], tile, fmt)

    if image is None:
        return _empty_response(fmt, media_type, headers, options.empty_no_content)

    tile_cache.put(layer, tile.tms_id, tile.z, tile.y, tile.x, files_key, ext, image)
    return Response(content=image, media_type=media_type, headers=headers)


def _validate_tile(tile: TileCoord, max_zoom_cap: int):
    """Return (tms, None) if the tile is servable, else (None, an OWS error Response)."""
    tms = get_by_name(tile.tms_id)
    if tms is None:
        return None, _ows_exception(f"TileMatrixSet {tile.tms_id!r} is not supported.", "InvalidParameterValue")
    max_zoom = min(tms.maxzoom, max_zoom_cap)
    if not (tms.minzoom <= tile.z <= max_zoom):
        return None, _ows_exception(
            f"TileMatrix {tile.z} is out of range [{tms.minzoom}, {max_zoom}].", "TileOutOfRange",
        )
    return tms, None


def _ows_exception(msg: str, code: str) -> Response:
    """Return an OWS Common 1.1.0 ExceptionReport response (400)."""
    content = _templates.get_template("ows_exception.xml.j2").render(msg=msg, code=code)
    return Response(content=content, media_type="text/xml", status_code=400)


async def _resolve_assets(
    mda, layer: str, bbox_list: list, src_srid: int, start_dt, end_dt, interval_min: int,
) -> tuple[dict, list, str]:
    """Query the DB for live assets; return (headers, assets, files_key).

    Always queries the DB so the cache key (``files_key``) reflects the current file
    set on disk — ensuring stale tiles are never served when data is added or removed.
    The caller uses ``files_key`` to look the tile up in the disk cache.
    """
    stepped = config.get("timestep_mode") == "stepped"
    latest, assets = await asyncio.gather(
        mda.get_latest_time(layer),
        mda.get_map_assets(layer, bbox_list, start_dt, end_dt, src_srid=src_srid),
    )
    headers = {"Cache-Control": cache_control(latest, end_dt, interval_min, stepped=stepped)}
    assets, files_key = _validate_assets_and_compute_key(assets)
    return headers, assets, files_key


def _validate_assets_and_compute_key(assets: list) -> tuple[list, str]:
    """Drop assets whose file is missing, and key the cache on (filename, mtime_ns, size).

    Including disk state in the key prevents stale tiles when DB metadata lags behind disk
    cleanup (or in-place file rewrites): a file disappearing or being modified produces a
    new key, forcing a fresh render rather than serving a previously-cached composite.
    """
    kept = []
    parts = []
    for a in assets:
        try:
            st = os.stat(a["filename"])
        except FileNotFoundError:
            continue
        kept.append(a)
        parts.append(f"{a['filename']}|{st.st_mtime_ns}|{st.st_size}")
    files_key = hashlib.sha1("||".join(sorted(parts)).encode()).hexdigest()[:16]  # noqa: S324
    return kept, files_key


def _empty_response(fmt: str, media_type: str, headers: dict, empty_no_content: bool) -> Response:
    """Return a 204 or transparent-image response for tiles with no renderable data."""
    if empty_no_content:
        return Response(status_code=204, headers=headers)
    return Response(content=empty_image(256, 256, fmt), media_type=media_type, headers=headers)


async def _render_tile_image(filepaths: list[str], tile: TileCoord, fmt: str) -> bytes | None:
    """Composite granule tiles and encode to the requested format. Returns None if all tiles are empty."""
    result = await _composite_tiles(filepaths, tile)
    if result is None:
        return None
    return await encode_image(result, fmt)


async def _composite_tiles(filepaths: list[str], tile: TileCoord) -> "ImageData | None":
    """Read and composite tiles from filepaths, returning None if all are out-of-bounds."""
    images = []
    gaps = None

    for start in range(0, len(filepaths), _BATCH_SIZE):
        batch = filepaths[start:start + _BATCH_SIZE]
        aws = [_read_tile(fp, tile.tms_id, tile.x, tile.y, tile.z) for fp in batch]
        batch_images = await asyncio.gather(*aws)
        for img in batch_images:
            images.append(img)
            if img is not None:
                no_data = np.all(img.array.mask, axis=0)
                gaps = no_data if gaps is None else (gaps & no_data)

        if gaps is not None and not np.any(gaps):
            break

    return composite_images(images)


@alru_cache(maxsize=config.get("tile_cache_entries"), ttl=300)
async def _read_tile(fp: str, tms_id: str, x: int, y: int, z: int):
    """Async LRU-cached tile read with 5-min TTL; coalesces concurrent requests for the same tile."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(READ_POOL, _read_tile_sync, fp, tms_id, x, y, z)


def _read_tile_sync(fp: str, tms_id: str, x: int, y: int, z: int):
    """Read a 256×256 tile from a COG via COGReader.tile() (runs in a thread).

    The dataset is pre-wrapped (when it carries GCPs) in a WarpedVRT targeting the TMS
    CRS so GDAL overview selection operates in metres rather than degrees, preventing
    pixelation at high latitudes. Returns None if the file is missing or the tile is
    outside the granule bounds.
    """
    tms = get_by_name(tms_id)

    def read(cog):
        try:
            return cog.tile(x, y, z, resampling_method="bilinear")
        except TileOutsideBounds:
            return None

    return read_cog(fp, tms.crs, read, tms=tms)
