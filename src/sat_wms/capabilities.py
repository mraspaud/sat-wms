"""Get capabilities."""
from fastapi.templating import Jinja2Templates

from sat_wms.time_utils import ceil_dt, floor_dt

templates = Jinja2Templates(directory="templates")


async def generate_capabilities(mda, request=None, online_resource=None, supported_crs=None):
    """Generate the GetCapabilities document."""
    raw_layers = await mda.get_layers()
    processed_layers = []
    for layer in raw_layers:
        b = layer["bbox"].replace("BOX(", "").replace(")", "").replace(",", " ").split()
        processed_layers.append({
            "layer_name": layer["layer_name"],
            "start_str": floor_dt(layer["start_time"]).strftime("%Y-%m-%dT%H:%M:00Z"),
            "end_str": layer["end_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_range_str": ceil_dt(layer["end_time"]).strftime("%Y-%m-%dT%H:%M:00Z"),
            "minx": b[0], "miny": b[1], "maxx": b[2], "maxy": b[3],
        })

    return templates.TemplateResponse(
        request,
        "capabilities.xml.j2",
        context={
            "layers": processed_layers,
            "online_resource": online_resource,
            "supported_crs": supported_crs or ["EPSG:3575", "EPSG:3857"],
        },
        media_type="text/xml",
    )
