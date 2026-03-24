"""WMS server entry point."""
import re
from datetime import timedelta

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from sat_wms.capabilities import generate_capabilities
from sat_wms.config import config
from sat_wms.local_mda import make_mda

app = FastAPI(title=config.get("wms_title"))
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

_SERVICE_EXCEPTION_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ServiceExceptionReport version="1.3.0"'
    ' xmlns="http://www.opengis.net/ogc">'
    "<ServiceException>{msg}</ServiceException>"
    "</ServiceExceptionReport>"
)


def _wms_exception(msg: str) -> Response:
    """Return a WMS 1.3.0 ServiceException response."""
    return Response(
        content=_SERVICE_EXCEPTION_TEMPLATE.format(msg=msg),
        media_type="text/xml",
        status_code=400,
    )


def _parse_duration(s: str) -> timedelta:
    """Parse a duration string like '30m', '2h', '1d' into a timedelta."""
    match = re.fullmatch(r"(\d+)(m|h|d)", s)
    if not match:
        return timedelta(hours=1)
    value, unit = int(match.group(1)), match.group(2)
    return {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]


@app.get("/{duration_str}/")
@app.get("/{duration_str}")
async def wms_endpoint(duration_str: str, request: Request):
    """Dispatch WMS requests."""
    params = {k.upper(): v for k, v in request.query_params.items()}

    version = params.get("VERSION")
    if version and version != "1.3.0":
        return _wms_exception(f"VERSION {version!r} is not supported; only 1.3.0 is implemented.")

    mda = make_mda(config.get("database_url"))
    online_resource = f"{config.get('base_url')}/{duration_str}/"

    match params.get("REQUEST"):
        case "GetCapabilities":
            return await generate_capabilities(
                mda,
                request=request,
                online_resource=online_resource,
                supported_crs=config.get("supported_crs"),
            )
        case "GetMap":
            from sat_wms.getmap import generate_map  # noqa: PLC0415
            return await generate_map(mda, params, _parse_duration(duration_str))
        case _:
            return Response("Unknown REQUEST type", status_code=400)
