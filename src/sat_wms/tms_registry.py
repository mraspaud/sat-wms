"""TileMatrixSet registry keyed by EPSG code and TMS identifier.

Pure data — no I/O, no HTTP. Built once at call time from the supported_crs
config list and cached for the lifetime of the process.
"""
import functools
import logging

import morecantile
import pyproj
from pyproj import Transformer

log = logging.getLogger(__name__)

_BY_NAME: dict[str, morecantile.TileMatrixSet] = {}
_BY_EPSG: dict[int, morecantile.TileMatrixSet] = {}

# When an EPSG code has multiple morecantile built-in TMS, pick this one.
_PREFERRED_BUILTIN: dict[int, str] = {
    4326: "WGS1984Quad",
}

# Override the auto-computed extent for CRS where area_of_use corners project
# incorrectly (e.g., polar LAEA whose valid area spans the whole hemisphere).
_EXTENT_OVERRIDES: dict[int, tuple[float, float, float, float]] = {
    3575: (-5_400_000.0, -5_400_000.0, 5_400_000.0, 5_400_000.0),
}

# Override the auto-generated TMS identifier for backward compatibility.
_ID_OVERRIDES: dict[int, str] = {
    3575: "NorthPolarLAEAEurope",
}


@functools.lru_cache(maxsize=None)
def _build_tms_for_epsg(epsg: int) -> morecantile.TileMatrixSet | None:
    """Build (and cache) a TileMatrixSet for the given EPSG code.

    Tries morecantile built-ins first; auto-generates from pyproj area_of_use otherwise.
    Returns None if the EPSG is unknown or has no usable area_of_use.
    """
    # --- Try morecantile built-ins ---
    candidates: list[str] = [
        name for name in morecantile.tms.list()
        if morecantile.tms.get(name).crs.to_epsg() == epsg
    ]
    if candidates:
        preferred = _PREFERRED_BUILTIN.get(epsg)
        name = preferred if preferred in candidates else candidates[0]
        return morecantile.tms.get(name)

    # --- Auto-generate from pyproj ---
    try:
        crs = pyproj.CRS.from_epsg(epsg)
    except pyproj.exceptions.CRSError:
        log.warning("Unknown EPSG:%s — skipped", epsg)
        return None

    tms_id = _ID_OVERRIDES.get(epsg, f"EPSG{epsg}")

    if epsg in _EXTENT_OVERRIDES:
        extent = _EXTENT_OVERRIDES[epsg]
    else:
        aou = crs.area_of_use
        if aou is None:
            log.warning("EPSG:%s has no area_of_use — skipped", epsg)
            return None
        extent = _compute_extent(crs, aou)
        if extent is None:
            log.warning("EPSG:%s extent could not be computed — skipped", epsg)
            return None

    return morecantile.TileMatrixSet.custom(
        extent=list(extent),
        crs=crs,
        minzoom=0,
        maxzoom=12,
        id=tms_id,
        title=f"{crs.name} (EPSG:{epsg})",
    )


def _compute_extent(
    crs: pyproj.CRS,
    aou: pyproj.aoi.AreaOfInterest,
) -> tuple[float, float, float, float] | None:
    """Project the area_of_use geographic corners into *crs* and return the bounding box."""
    t = Transformer.from_crs(4326, crs, always_xy=True)
    xs: list[float] = []
    ys: list[float] = []
    for lon in (aou.west, aou.east):
        for lat in (aou.south, aou.north):
            try:
                x, y = t.transform(lon, lat)
                if abs(x) < 1e10 and abs(y) < 1e10:  # filter inf/overflow
                    xs.append(x)
                    ys.append(y)
            except Exception:  # noqa: BLE001, S110
                pass
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def build_registry(supported_crs: list[str]) -> None:
    """Populate the registry from a list of 'EPSG:XXXX' strings.

    Safe to call multiple times; each call replaces the previous registry.
    """
    _BY_NAME.clear()
    _BY_EPSG.clear()
    for crs_str in supported_crs:
        epsg = int(crs_str.split(":")[1])
        tms = _build_tms_for_epsg(epsg)
        if tms is None:
            continue
        _BY_NAME[tms.id] = tms
        _BY_EPSG[epsg] = tms


def get_by_name(tms_id: str) -> morecantile.TileMatrixSet | None:
    """Return the TMS for the given identifier string, or None."""
    return _BY_NAME.get(tms_id)


def get_by_epsg(epsg: int) -> morecantile.TileMatrixSet | None:
    """Return the TMS for the given EPSG code, or None."""
    return _BY_EPSG.get(epsg)


def all_tms() -> list[morecantile.TileMatrixSet]:
    """Return all registered TileMatrixSets."""
    return list(_BY_NAME.values())
