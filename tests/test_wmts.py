"""Tests for WMTS 1.0.0 support."""
from datetime import timedelta

import pytest

# ---------------------------------------------------------------------------
# Config (iteration 4)
# ---------------------------------------------------------------------------

def test_wmts_max_zoom_default_is_9():
    """wmts_max_zoom defaults to 9."""
    from sat_wms.config import config

    assert config.get("wmts_max_zoom") == 9


# ---------------------------------------------------------------------------
# WMTS capabilities (iterations 5-8)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wmts_capabilities_returns_xml(local_mda):
    """generate_wmts_capabilities returns a text/xml response."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda)
    assert resp.media_type == "text/xml"
    assert b"<?xml" in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_contains_tilematrixset_identifier(local_mda):
    """Capabilities document includes the NorthPolarLAEAEurope TileMatrixSet."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda, supported_crs=["EPSG:3575"])
    assert b"NorthPolarLAEAEurope" in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_crs_uri_is_ogc_urn(local_mda):
    """SupportedCRS uses the OGC URN format that OpenLayers can resolve."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda, supported_crs=["EPSG:3857", "EPSG:3575"])
    assert b"urn:ogc:def:crs:EPSG::3857" in resp.body
    assert b"urn:ogc:def:crs:EPSG::3575" in resp.body
    # Must NOT contain the HTTP URI form that OL can't resolve
    assert b"http://www.opengis.net/def/crs" not in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_contains_layer_name(local_mda):
    """Capabilities document lists at least one layer from the MDA."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda)
    assert b"true_color_day" in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_dimension_step_reflects_interval(local_mda):
    """Time dimension period uses the configured interval."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda, interval_min=5)
    assert b"PT5M" in resp.body


# ---------------------------------------------------------------------------
# Tile generation (iterations 9-16)
# ---------------------------------------------------------------------------

# Tile (x=3, y=4, z=3) in NorthPolarLAEAEurope intersects the test_tif bbox
# (-1320000, -2781000, 569250, 245250) ∩ tile (-1350000, -1350000, 0, 0).
_TILE_KW = {"layer": "true_color_day", "tms_id": "NorthPolarLAEAEurope", "z": 3, "y": 4, "x": 3}


@pytest.mark.asyncio
async def test_generate_tile_unknown_tms_returns_400(local_mda):
    """An unknown TileMatrixSet identifier returns a 400 OWS exception."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(local_mda, layer="true_color_day", tms_id="DoesNotExist",
                                z=3, y=4, x=3, duration=timedelta(hours=1))
    assert resp.status_code == 400
    assert b"InvalidParameterValue" in resp.body


@pytest.mark.asyncio
async def test_generate_tile_z_out_of_range_returns_400(local_mda):
    """A zoom level beyond wmts_max_zoom returns a 400 OWS exception."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(local_mda, **{**_TILE_KW, "z": 99}, duration=timedelta(hours=1))
    assert resp.status_code == 400
    assert b"TileOutOfRange" in resp.body


@pytest.mark.asyncio
async def test_generate_tile_no_assets_returns_transparent_png(local_mda):
    """A tile request with no matching granules returns a transparent PNG."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(local_mda, **{**_TILE_KW, "z": 3, "y": 4, "x": 3},
                                duration=timedelta(hours=1),
                                time_str="2099-01-01T00:00:00Z")
    assert resp.status_code == 200
    assert resp.body[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_generate_tile_returns_png_for_valid_tile(synth_mda):
    """A tile request with matching assets returns a PNG image."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(synth_mda, **_TILE_KW, duration=timedelta(hours=6),
                                time_str="2026-03-24T06:00:00Z")
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_generate_tile_webp_returns_webp(synth_mda):
    """fmt=WEBP returns a WebP tile."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(synth_mda, **_TILE_KW, duration=timedelta(hours=6),
                                time_str="2026-03-24T06:00:00Z", fmt="WEBP")
    assert resp.media_type == "image/webp"
    assert resp.body[:4] == b"RIFF"
    assert resp.body[8:12] == b"WEBP"


@pytest.mark.asyncio
async def test_generate_tile_empty_no_content_returns_204(local_mda):
    """empty_no_content=True returns 204 when no assets match."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(local_mda, **_TILE_KW, duration=timedelta(hours=1),
                                time_str="2099-01-01T00:00:00Z", empty_no_content=True)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_generate_tile_force_webp_overrides_png(synth_mda):
    """force_webp=True returns WebP even when fmt=PNG."""
    from sat_wms.wmts import generate_tile

    resp = await generate_tile(synth_mda, **_TILE_KW, duration=timedelta(hours=6),
                                time_str="2026-03-24T06:00:00Z", fmt="PNG", force_webp=True)
    assert resp.media_type == "image/webp"


@pytest.mark.asyncio
async def test_generate_tile_cache_control_short_ttl_for_latest():
    """Cache-Control is max-age=60 when the tile time matches the latest granule."""
    from datetime import datetime, timezone

    from sat_wms.wmts import generate_tile

    latest_time = datetime(2026, 3, 24, 5, 4, tzinfo=timezone.utc)

    class LatestMDA:
        async def get_latest_time(self, _layer):
            return latest_time

        async def get_map_assets(self, *_args, **_kwargs):
            return []

    resp = await generate_tile(LatestMDA(), **_TILE_KW, duration=timedelta(minutes=30),
                                time_str="2026-03-24T05:04:00Z")
    assert resp.headers["cache-control"] == "public, max-age=60"


# ---------------------------------------------------------------------------
# HTTP dispatch (iterations 17-18)
# ---------------------------------------------------------------------------

@pytest.fixture
def wmts_client(local_mda):
    """TestClient with LocalMetadataRepository injected via app.state."""
    from main import app
    app.state.mda = local_mda
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_wmts_capabilities_endpoint_returns_200(wmts_client):
    """GET /{duration_str}/wmts/ returns 200 text/xml."""
    res = wmts_client.get("/30m/wmts/")
    assert res.status_code == 200
    assert "text/xml" in res.headers["content-type"]
    assert "NorthPolarLAEAEurope" in res.text


def test_wmts_tile_endpoint_accepts_lowercase_time(wmts_client):
    """Lowercase 'time' query param is respected (future time → transparent PNG, not today's data)."""
    res = wmts_client.get(
        "/6h/wmts/true_color_day/NorthPolarLAEAEurope/3/4/3",
        params={"time": "2099-01-01T00:00:00Z"},
    )
    # With lowercase time honoured, no granules match → empty image (200 transparent PNG)
    assert res.status_code == 200
    assert res.content[:4] == b"\x89PNG"


def test_wmts_tile_endpoint_accepts_lowercase_format(wmts_client):
    """Lowercase 'format=image/webp' is respected and returns a WebP tile."""
    res = wmts_client.get(
        "/6h/wmts/true_color_day/NorthPolarLAEAEurope/3/4/3",
        params={"time": "2099-01-01T00:00:00Z", "format": "image/webp"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"


def test_wmts_tile_endpoint_returns_png(wmts_client):
    """GET /{duration_str}/wmts/{layer}/NorthPolarLAEAEurope/3/4/3 returns a PNG."""
    res = wmts_client.get(
        "/6h/wmts/true_color_day/NorthPolarLAEAEurope/3/4/3",
        params={"TIME": "2026-03-24T06:00:00Z"},
    )
    # May be transparent (no real data at tile coords from CSV) but must be valid PNG or 200
    assert res.status_code in (200, 204)
    if res.status_code == 200:
        assert res.content[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# TMS Registry (iterations 1-3)
# ---------------------------------------------------------------------------

def test_registry_returns_tms_for_each_supported_crs():
    """build_registry populates a TMS for each EPSG in supported_crs."""
    from sat_wms.tms_registry import all_tms, build_registry

    build_registry(["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"])
    ids = {t.crs.to_epsg() for t in all_tms()}
    assert {3575, 3857, 5041, 4326}.issubset(ids)


def test_registry_epsg3575_id_is_north_polar_laea_europe():
    """The custom EPSG:3575 TMS carries the expected identifier string."""
    from sat_wms.tms_registry import build_registry, get_by_epsg

    build_registry(["EPSG:3575"])
    tms = get_by_epsg(3575)
    assert tms is not None
    assert tms.id == "NorthPolarLAEAEurope"


def test_registry_unknown_epsg_returns_none():
    """get_by_name returns None for an unregistered TMS identifier."""
    from sat_wms.tms_registry import build_registry, get_by_name

    build_registry(["EPSG:3857"])
    assert get_by_name("DoesNotExist") is None
