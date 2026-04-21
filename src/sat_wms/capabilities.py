"""Get capabilities."""
from importlib.resources import files

from fastapi.templating import Jinja2Templates

from sat_wms.config import config
from sat_wms.time_utils import _to_iso_duration, ceil_dt, compute_snapshot_times, floor_dt, parse_duration

templates = Jinja2Templates(directory=str(files("sat_wms").joinpath("templates")))


def _parse_postgis_box(bbox_str: str) -> tuple[str, str, str, str]:
    """Parse a PostGIS ST_Extent string 'BOX(x0 y0, x1 y1)' into (x0, y0, x1, y1)."""
    coords = bbox_str.replace("BOX(", "").replace(")", "").replace(",", " ").split()
    return coords[0], coords[1], coords[2], coords[3]


async def generate_capabilities(
    mda, request=None, online_resource=None, supported_crs=None, interval_min: int = 10,
    force_webp: bool = False, duration_str: str | None = None,
):
    """Generate the GetCapabilities document."""
    base_title = config.get("wms_title")
    title = f"{base_title} ({duration_str})" if duration_str else base_title
    raw_layers = await mda.get_layers()
    stepped = config.get("timestep_mode") == "stepped"
    snapshot_step = parse_duration(config.get("snapshot_step") or "24h")
    snapshot_count = int(config.get("snapshot_count") or 7)
    processed_layers = []
    for layer in raw_layers:
        minx, miny, maxx, maxy = _parse_postgis_box(layer["bbox"])
        entry = {
            "layer_name": layer["layer_name"],
            "start_str": floor_dt(layer["start_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "end_str": layer["end_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_range_str": ceil_dt(layer["end_time"], interval_min).strftime("%Y-%m-%dT%H:%M:00Z"),
            "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
            "time_values": None,
        }
        if stepped:
            times = compute_snapshot_times(layer["end_time"], snapshot_step, snapshot_count)
            entry["time_values"] = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
        processed_layers.append(entry)

    return templates.TemplateResponse(
        request,
        "capabilities.xml.j2",
        context={
            "title": title,
            "layers": processed_layers,
            "online_resource": online_resource,
            "supported_crs": supported_crs or ["EPSG:3575", "EPSG:3857"],
            "interval_iso": _to_iso_duration(interval_min),
            "map_formats": ["image/webp"] if force_webp else ["image/png", "image/webp"],
        },
        media_type="text/xml",
    )
