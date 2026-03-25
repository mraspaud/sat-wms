"""Tests for WMS endpoint dispatch and version validation."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(local_mda):
    """TestClient with LocalMetadataRepository injected via app.state."""
    from main import app
    app.state.mda = local_mda
    return TestClient(app)


def test_lifespan_sets_app_state_mda(monkeypatch, local_mda):
    """The lifespan initialises app.state.mda on startup."""
    from sat_wms import local_mda as local_mda_mod
    monkeypatch.setattr(local_mda_mod, "make_mda", lambda _: local_mda)
    from main import app
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
