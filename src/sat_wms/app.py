"""WMS server entry point."""
import contextlib
import logging
import time
from importlib.resources import files

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from sat_wms.capabilities import generate_capabilities
from sat_wms.config import config
from sat_wms.local_mda import make_mda
from sat_wms.time_utils import parse_duration, parse_interval_min
from sat_wms.tms_registry import build_registry
from sat_wms.wmts import generate_tile, generate_wmts_capabilities

logger = logging.getLogger("sat_wms.access")
_templates = Jinja2Templates(directory=str(files("sat_wms").joinpath("templates")))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared resources (MDA, connection pool) on startup."""
    build_registry(config.get("supported_crs") or ["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"])
    conn_str = config.get("database_url")
    if conn_str.endswith(".csv"):
        app.state.mda = make_mda(conn_str)
        yield
    else:
        from psycopg_pool import AsyncConnectionPool  # noqa: PLC0415

        from sat_wms.pg_mda import PooledMetadataRepository  # noqa: PLC0415
        async with AsyncConnectionPool(conn_str, min_size=2, max_size=10) as pool:
            app.state.mda = PooledMetadataRepository(pool)
            yield


app = FastAPI(title=config.get("wms_title"), lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each request with timestamp and duration."""
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    logger.info("%s %s %d %.0fms", request.method, request.url, response.status_code, ms)
    return response

def _wms_exception(msg: str, code: str | None = None) -> Response:
    """Return a WMS 1.3.0 ServiceException response."""
    content = _templates.get_template("service_exception.xml.j2").render(msg=msg, code=code)
    return Response(content=content, media_type="text/xml", status_code=400)


def _strip_layer_prefix(layer: str, prefix: str) -> str:
    """Strip a known prefix from a client-supplied layer name."""
    if prefix and layer.startswith(prefix):
        return layer[len(prefix):]
    return layer


def _uppercase_params(request: Request) -> dict[str, str]:
    """Return query parameters with keys uppercased for case-insensitive WMS/WMTS dispatch."""
    return {k.upper(): v for k, v in request.query_params.items()}


@app.get("/{duration_str}/wmts/")
@app.get("/{duration_str}/wmts")
async def wmts_endpoint(duration_str: str, request: Request):
    """Dispatch WMTS requests."""
    mda = request.app.state.mda
    params = _uppercase_params(request)
    prefix = config.get("layer_name_prefix") or ""
    request_type = params.get("REQUEST", "GetCapabilities").upper()
    if request_type == "GETCAPABILITIES":
        online_resource = f"{config.get('base_url')}/{duration_str}/"
        return await generate_wmts_capabilities(
            mda,
            request=request,
            online_resource=online_resource,
            supported_crs=config.get("supported_crs"),
            interval_min=parse_interval_min(config.get("granule_interval")),
            force_webp=bool(config.get("force_webp")),
            wmts_max_zoom=int(config.get("wmts_max_zoom")),
            duration_str=duration_str,
            layer_name_prefix=prefix,
        )

    if request_type and request_type.upper() != "GETTILE":
        raise HTTPException(status_code=400, detail="Only GetTile is supported at this KVP endpoint.")

    layer = _strip_layer_prefix(params.get("LAYER") or "", prefix)
    tms_id = params.get("TILEMATRIXSET")
    z_str = params.get("TILEMATRIX")
    y_str = params.get("TILEROW")
    x_str = params.get("TILECOL")
    if not layer or None in (tms_id, z_str, y_str, x_str):
        raise HTTPException(status_code=400, detail="Missing required WMTS KVP parameters.")
    z, y, x = int(z_str), int(y_str), int(x_str)

    return await generate_tile(
        mda,
        layer=layer,
        tms_id=tms_id,
        z=z, y=y, x=x,
        duration=parse_duration(duration_str),
        time_str=params.get("TIME"),
        fmt={"image/png": "PNG", "image/webp": "WEBP"}.get(
            (params.get("FORMAT") or "").lower(), "PNG"
        ),
        interval_min=parse_interval_min(config.get("granule_interval")),
        force_webp=bool(config.get("force_webp")),
        empty_no_content=bool(config.get("empty_no_content")),
        wmts_max_zoom=int(config.get("wmts_max_zoom")),
    )


@app.get("/{duration_str}/wmts/{layer}/{tms_id}/{z}/{y}/{x}")
async def wmts_tile_endpoint(
    duration_str: str, layer: str, tms_id: str, z: int, y: int, x: int, request: Request,
):
    """Dispatch WMTS GetTile requests."""
    mda = request.app.state.mda
    params = _uppercase_params(request)
    prefix = config.get("layer_name_prefix") or ""
    return await generate_tile(
        mda,
        layer=_strip_layer_prefix(layer, prefix),
        tms_id=tms_id,
        z=z, y=y, x=x,
        duration=parse_duration(duration_str),
        time_str=params.get("TIME"),
        fmt={"image/png": "PNG", "image/webp": "WEBP"}.get(
            (params.get("FORMAT") or "").lower(), "PNG"
        ),
        interval_min=parse_interval_min(config.get("granule_interval")),
        force_webp=bool(config.get("force_webp")),
        empty_no_content=bool(config.get("empty_no_content")),
        wmts_max_zoom=int(config.get("wmts_max_zoom")),
    )


@app.get("/{duration_str}/")
@app.get("/{duration_str}")
async def wms_endpoint(duration_str: str, request: Request):
    """Dispatch WMS requests."""
    params = _uppercase_params(request)

    version = params.get("VERSION")
    if version and version != "1.3.0":
        return _wms_exception(f"VERSION {version!r} is not supported; only 1.3.0 is implemented.")

    mda = request.app.state.mda
    online_resource = f"{config.get('base_url')}/{duration_str}/"
    interval_min = parse_interval_min(config.get("granule_interval"))
    force_webp = bool(config.get("force_webp"))
    empty_no_content = bool(config.get("empty_no_content"))
    prefix = config.get("layer_name_prefix") or ""

    match (params.get("REQUEST") or "").upper():
        case "GETCAPABILITIES":
            return await generate_capabilities(
                mda,
                request=request,
                online_resource=online_resource,
                supported_crs=config.get("supported_crs"),
                interval_min=interval_min,
                force_webp=force_webp,
                duration_str=duration_str,
                layer_name_prefix=prefix,
            )
        case "GETMAP":
            from pyproj.exceptions import CRSError  # noqa: PLC0415

            from sat_wms.getmap import generate_map  # noqa: PLC0415
            if "LAYERS" in params and prefix:
                params = dict(params)
                params["LAYERS"] = _strip_layer_prefix(params["LAYERS"], prefix)
            try:
                return await generate_map(mda, params, parse_duration(duration_str),
                                          interval_min=interval_min,
                                          force_webp=force_webp,
                                          empty_no_content=empty_no_content)
            except CRSError as exc:
                return _wms_exception(str(exc), code="InvalidCRS")
            except Exception as exc:
                logger.exception("GetMap failed: %s", exc)
                return _wms_exception(str(exc))
        case _:
            return Response("Unknown REQUEST type", status_code=400)
