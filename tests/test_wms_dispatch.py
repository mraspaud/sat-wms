"""Tests for WMS endpoint dispatch and version validation."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(local_mda):
    """TestClient with LocalMetadataRepository injected via app.state."""
    from sat_wms.app import app
    app.state.mda = local_mda
    return TestClient(app)


def test_lifespan_sets_app_state_mda(monkeypatch, local_mda):
    """The lifespan initialises app.state.mda on startup."""
    from sat_wms import local_mda as local_mda_mod
    monkeypatch.setattr(local_mda_mod, "make_mda", lambda _: local_mda)
    from sat_wms.app import app
    with TestClient(app) as c:  # noqa: F841 — triggers lifespan
        assert hasattr(app.state, "mda")


def test_unsupported_version_returns_exception_xml(client):
    """VERSION != 1.3.0 returns a WMS ServiceException."""
    res = client.get("/30m/", params={"REQUEST": "GetCapabilities", "VERSION": "1.1.1"})
    assert res.status_code == 400
    assert "ServiceException" in res.text


def test_no_version_is_accepted_for_getcapabilities(client):
    """Omitting VERSION is valid for GetCapabilities."""
    res = client.get("/30m/", params={"REQUEST": "GetCapabilities"})
    assert res.status_code == 200


def test_correct_version_is_accepted(client):
    """VERSION=1.3.0 is accepted."""
    res = client.get("/30m/", params={"REQUEST": "GetCapabilities", "VERSION": "1.3.0"})
    assert res.status_code == 200


def test_lowercase_request_param_is_accepted(client):
    """Lowercase 'request=GetCapabilities' is accepted (case-insensitive params)."""
    res = client.get("/30m/", params={"request": "GetCapabilities"})
    assert res.status_code == 200


def test_lowercase_request_value_is_accepted(client):
    """'request=getcapabilities' (lowercase value) is dispatched correctly."""
    res = client.get("/30m/", params={"REQUEST": "getcapabilities"})
    assert res.status_code == 200


def test_mixed_case_request_value_is_accepted(client):
    """'REQUEST=GETMAP' (all-caps value) does not return 400 unknown request."""
    res = client.get("/30m/", params={
        "REQUEST": "GETMAP", "LAYERS": "true_color_day",
        "CRS": "EPSG:3575", "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256", "HEIGHT": "256", "TIME": "2099-01-01T00:00:00Z",
    })
    assert res.status_code == 200


def test_invalid_crs_returns_exception_xml(client):
    """An unresolvable CRS returns a ServiceException with code InvalidCRS."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap", "VERSION": "1.3.0",
        "LAYERS": "true_color_day", "CRS": "EPSG:99999",
        "BBOX": "0,0,1,1", "WIDTH": "256", "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert 'code="InvalidCRS"' in res.text


def test_getmap_missing_layers_returns_error(client):
    """GetMap without LAYERS returns a 400 ServiceException."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "CRS": "EPSG:3575",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256",
        "HEIGHT": "256",
    })
    assert res.status_code == 400


def test_getmap_oversized_width_returns_exception_xml(client):
    """GetMap with WIDTH exceeding the configured limit returns a WMS ServiceException."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "LAYERS": "true_color_day",
        "CRS": "EPSG:3575",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "99999",
        "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert "ServiceException" in res.text


def test_getmap_internal_error_returns_generic_message(client, monkeypatch):
    """An unexpected server error does not leak internal exception details to the client."""
    import sat_wms.getmap as gm

    async def _boom(*_a, **_kw):
        raise RuntimeError("internal secret path: /etc/shadow")

    monkeypatch.setattr(gm, "generate_map", _boom)
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "LAYERS": "true_color_day",
        "CRS": "EPSG:3575",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256",
        "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert "shadow" not in res.text
    assert "ServiceException" in res.text


def test_getmap_missing_crs_returns_error(client):
    """GetMap without CRS returns a 400 ServiceException (not 500)."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "LAYERS": "true_color_day",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256",
        "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert "ServiceException" in res.text


def test_wmts_kvp_non_numeric_z_returns_error(client):
    """WMTS KVP GetTile with non-numeric TILEMATRIX returns 400 (not 500)."""
    res = client.get("/30m/wmts/", params={
        "REQUEST": "GetTile",
        "LAYER": "true_color_day",
        "TILEMATRIXSET": "NorthPolarLAEAEurope",
        "TILEMATRIX": "abc",
        "TILEROW": "0",
        "TILECOL": "0",
        "FORMAT": "image/png",
    })
    assert res.status_code == 400


def test_cors_wildcard_is_present_in_response(client):
    """CORS allow_origins is reflected in response Access-Control-Allow-Origin header."""
    res = client.get("/30m/", params={"REQUEST": "GetCapabilities"},
                     headers={"Origin": "https://example.com"})
    assert res.headers.get("access-control-allow-origin") == "*"


def test_getmap_malformed_width_returns_wms_exception(client):
    """GetMap with non-numeric WIDTH returns a WMS ServiceException (not a generic server error)."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "LAYERS": "true_color_day",
        "CRS": "EPSG:3575",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "abc",
        "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert "ServiceException" in res.text
    assert "internal" not in res.text.lower()


def test_getmap_zero_width_returns_wms_exception(client):
    """GetMap with WIDTH=0 returns a WMS ServiceException."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap",
        "LAYERS": "true_color_day",
        "CRS": "EPSG:3575",
        "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "0",
        "HEIGHT": "256",
    })
    assert res.status_code == 400
    assert "ServiceException" in res.text


def test_getmap_range_probe_with_data_returns_206(client):
    """A GetMap with 'Range: bytes=0-0' over an area with data returns 206 without rendering."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap", "LAYERS": "true_color_day",
        "CRS": "EPSG:3575", "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256", "HEIGHT": "256", "TIME": "2026-03-24T05:10:00Z",
    }, headers={"Range": "bytes=0-0"})
    assert res.status_code == 206
    assert res.headers.get("Content-Range") == "bytes 0-0/*"


def test_getmap_range_probe_without_data_returns_204(client):
    """A GetMap probe over an area/time with no assets returns 204."""
    res = client.get("/30m/", params={
        "REQUEST": "GetMap", "LAYERS": "true_color_day",
        "CRS": "EPSG:3575", "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256", "HEIGHT": "256", "TIME": "2099-01-01T00:00:00Z",
    }, headers={"Range": "bytes=0-0"})
    assert res.status_code == 204


def test_getmap_range_probe_skips_render(client, monkeypatch):
    """The probe must not render: it returns 206 even when the compositor would fail."""
    import sat_wms.getmap as gm

    async def _boom(*_a, **_kw):
        raise RuntimeError("render should not run for a probe")

    monkeypatch.setattr(gm, "_read_and_composite", _boom)
    res = client.get("/30m/", params={
        "REQUEST": "GetMap", "LAYERS": "true_color_day",
        "CRS": "EPSG:3575", "BBOX": "-1320000,-2781000,569250,245250",
        "WIDTH": "256", "HEIGHT": "256", "TIME": "2026-03-24T05:10:00Z",
    }, headers={"Range": "bytes=0-0"})
    assert res.status_code == 206


def test_strip_layer_prefix_removes_known_prefix():
    from sat_wms.app import _strip_layer_prefix
    assert _strip_layer_prefix("Sentinel-1 SAR sar-ice-log", "Sentinel-1 SAR ") == "sar-ice-log"


def test_strip_layer_prefix_noop_when_no_match():
    from sat_wms.app import _strip_layer_prefix
    assert _strip_layer_prefix("sar-ice-log", "Sentinel-1 SAR ") == "sar-ice-log"


def test_strip_layer_prefix_noop_when_empty_prefix():
    from sat_wms.app import _strip_layer_prefix
    assert _strip_layer_prefix("sar-ice-log", "") == "sar-ice-log"
