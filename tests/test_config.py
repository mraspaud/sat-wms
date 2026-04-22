"""Tests for sat_wms.config helpers."""
import sat_wms.config as cfg
from sat_wms.config import get_supported_crs


def test_get_supported_crs_list_passthrough():
    """A list value from YAML config is returned as-is."""
    with cfg.config.set({"supported_crs": ["EPSG:3575", "EPSG:3857"]}):
        assert get_supported_crs() == ["EPSG:3575", "EPSG:3857"]


def test_get_supported_crs_string_splits_on_comma():
    """A comma-separated env-var string is split into a list."""
    with cfg.config.set({"supported_crs": "EPSG:3575,EPSG:3857"}):
        assert get_supported_crs() == ["EPSG:3575", "EPSG:3857"]


def test_get_supported_crs_string_strips_whitespace():
    """Whitespace around items is stripped when parsing the env-var string."""
    with cfg.config.set({"supported_crs": " EPSG:3575 , EPSG:3857 "}):
        assert get_supported_crs() == ["EPSG:3575", "EPSG:3857"]
