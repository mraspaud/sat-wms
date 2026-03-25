"""WMS server entry point."""
import contextlib
import logging
import time

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from sat_wms.capabilities import generate_capabilities
from sat_wms.config import config
from sat_wms.local_mda import make_mda
from sat_wms.time_utils import parse_duration, parse_interval_min
from sat_wms.wmts import generate_tile, generate_wmts_capabilities

logger = logging.getLogger("sat_wms.access")
_templates = Jinja2Templates(directory="templates")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared resources (MDA, connection pool) on startup."""
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


@app.get("/{duration_str}/wmts/")
@app.get("/{duration_str}/wmts")
async def wmts_endpoint(
    duration_str: str,
    request: Request,
    # Map the OGC KVP names to local variables
    layer: str = Query(None, alias="layer"),
    tms_id: str = Query(None, alias="tilematrixset"),
    z: int = Query(None, alias="tilematrix"),
    y: int = Query(None, alias="tilerow"),
    x: int = Query(None, alias="tilecol"),
    request_type: str = Query(None, alias="request")
):
    """Dispatch WMTS requests."""
    mda = request.app.state.mda
    req = (request_type or "GetCapabilities").upper()
    if req == "GETCAPABILITIES":
        online_resource = f"{config.get('base_url')}/{duration_str}/"
        return await generate_wmts_capabilities(
            mda,
            request=request,
            online_resource=online_resource,
            supported_crs=config.get("supported_crs"),
            interval_min=parse_interval_min(config.get("granule_interval")),
            force_webp=bool(config.get("force_webp")),
            wmts_max_zoom=int(config.get("wmts_max_zoom")),
        )

    # 1. Check if it's actually a GetTile request
    if request_type and request_type.upper() != "GETTILE":
        # If it's GetCapabilities, you'd handle that here or elsewhere
        raise HTTPException(status_code=400, detail="Only GetTile is supported at this KVP endpoint.")

    # 2. Extract query params once for the rest of the logic
    # OGC specifies case-insensitivity, so we normalize keys to uppercase
    params = {k.upper(): v for k, v in request.query_params.items()}

    # 3. Validation: KVP clients are notorious for missing params
    if None in (layer, tms_id, z, y, x):
        raise HTTPException(status_code=400, detail="Missing required WMTS KVP parameters.")

    mda = request.app.state.mda

    # 4. Delegate to your existing worker function
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
    params = {k.upper(): v for k, v in request.query_params.items()}
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


@app.get("/{duration_str}/")
@app.get("/{duration_str}")
async def wms_endpoint(duration_str: str, request: Request):
    """Dispatch WMS requests."""
    params = {k.upper(): v for k, v in request.query_params.items()}

    version = params.get("VERSION")
    if version and version != "1.3.0":
        return _wms_exception(f"VERSION {version!r} is not supported; only 1.3.0 is implemented.")

    mda = request.app.state.mda
    online_resource = f"{config.get('base_url')}/{duration_str}/"
    interval_min = parse_interval_min(config.get("granule_interval"))
    force_webp = bool(config.get("force_webp"))
    empty_no_content = bool(config.get("empty_no_content"))

    match (params.get("REQUEST") or "").upper():
        case "GETCAPABILITIES":
            return await generate_capabilities(
                mda,
                request=request,
                online_resource=online_resource,
                supported_crs=config.get("supported_crs"),
                interval_min=interval_min,
                force_webp=force_webp,
            )
        case "GETMAP":
            from pyproj.exceptions import CRSError  # noqa: PLC0415

            from sat_wms.getmap import generate_map  # noqa: PLC0415
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
