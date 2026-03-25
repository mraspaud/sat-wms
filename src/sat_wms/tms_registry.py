"""TileMatrixSet registry keyed by EPSG code and TMS identifier.

Pure data — no I/O, no HTTP. Built once at call time from the supported_crs
config list and cached for the lifetime of the process.
"""
import morecantile
import pyproj

_BY_NAME: dict[str, morecantile.TileMatrixSet] = {}
_BY_EPSG: dict[int, morecantile.TileMatrixSet] = {}

# Pre-built morecantile TMS identifiers for each supported EPSG code.
_PREBUILT: dict[int, str] = {
    3857: "WebMercatorQuad",
    5041: "UPSArcticWGS84Quad",
    4326: "WGS1984Quad",
}

# Custom TileMatrixSet for EPSG:3575 (North Pole LAEA Europe).
# Extent covers the full valid domain of the projection (≈ ±5 400 000 m from pole).
# z7 ≈ 330 m/px matches VIIRS 375 m resolution; z9 is the practical ceiling.
_CUSTOM: dict[int, morecantile.TileMatrixSet] = {
    3575: morecantile.TileMatrixSet.custom(
        extent=[-5_400_000, -5_400_000, 5_400_000, 5_400_000],
        crs=pyproj.CRS.from_epsg(3575),
        minzoom=0,
        maxzoom=9,
        id="NorthPolarLAEAEurope",
        title="North Pole LAEA Europe (EPSG:3575)",
    ),
}


def build_registry(supported_crs: list[str]) -> None:
    """Populate the registry from a list of 'EPSG:XXXX' strings.

    Safe to call multiple times; each call replaces the previous registry.
    """
    _BY_NAME.clear()
    _BY_EPSG.clear()
    for crs_str in supported_crs:
        epsg = int(crs_str.split(":")[1])
        if epsg in _PREBUILT:
            tms = morecantile.tms.get(_PREBUILT[epsg])
        elif epsg in _CUSTOM:
            tms = _CUSTOM[epsg]
        else:
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
