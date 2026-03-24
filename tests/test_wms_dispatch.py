"""Tests for WMS endpoint dispatch and version validation."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, local_mda):
    """TestClient with LocalMetadataRepository injected."""
    from sat_wms import local_mda as local_mda_mod
    monkeypatch.setattr(local_mda_mod, "make_mda", lambda _: local_mda)
    from main import app
    return TestClient(app)


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
