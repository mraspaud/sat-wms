"""Tests for GetMap."""
from datetime import timedelta

import pytest

VALID_PARAMS = {
    "LAYERS": "true_color_day",
    "BBOX": "-1320000,-2781000,569250,245250",
    "CRS": "EPSG:3575",
    "WIDTH": "256",
    "HEIGHT": "256",
    "TIME": "2026-03-24T05:04:00Z",
}


@pytest.mark.asyncio
async def test_generate_map_live(local_mda):
    """Live test: render a real PNG from actual GeoTIFFs in data/real/."""
    from sat_wms.getmap import generate_map

    params = {
        "LAYERS": "true_color_day",
        "BBOX": "-1320000,-2781000,569250,245250",
        "CRS": "EPSG:3575",
        "WIDTH": "512",
        "HEIGHT": "512",
        "TIME": "2026-03-24T05:10:00Z",
    }
    res = await generate_map(local_mda, params, timedelta(minutes=30))
    assert res.status_code == 200
    assert res.body[:4] == b"\x89PNG"


def test_parse_params_layer_name():
    """_parse_params extracts the layer name from LAYERS."""
    from sat_wms.getmap import _parse_params

    result = _parse_params(VALID_PARAMS)
    assert result.layer_name == "true_color_day"


def test_parse_params_srid():
    """_parse_params parses the SRID from the CRS string."""
    from sat_wms.getmap import _parse_params

    assert _parse_params(VALID_PARAMS).srid == 3575


def test_parse_params_missing_layers_raises():
    """_parse_params raises KeyError when LAYERS is absent."""
    from sat_wms.getmap import _parse_params

    params = {k: v for k, v in VALID_PARAMS.items() if k != "LAYERS"}
    with pytest.raises(KeyError):
        _parse_params(params)


@pytest.mark.asyncio
async def test_generate_map_returns_png(synth_mda):
    """generate_map returns 200 with image/png content type."""
    from sat_wms.getmap import generate_map

    res = await generate_map(synth_mda, VALID_PARAMS, timedelta(hours=1))
    assert res.status_code == 200
    assert res.media_type == "image/png"


@pytest.mark.asyncio
async def test_generate_map_body_is_png(synth_mda):
    """generate_map response body starts with the PNG magic bytes."""
    from sat_wms.getmap import generate_map

    res = await generate_map(synth_mda, VALID_PARAMS, timedelta(hours=1))
    assert res.body[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_generate_map_empty_result_is_transparent_png(local_mda):
    """generate_map returns a transparent PNG when no granules match."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "TIME": "2099-01-01T00:00:00Z"}
    res = await generate_map(local_mda, params, timedelta(hours=1))
    assert res.status_code == 200
    assert res.body[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_generate_map_time_window(local_mda):
    """TIME is floored and the duration window is applied correctly."""
    from sat_wms.getmap import generate_map

    # TIME=05:10 → floor=05:10, duration=5m → window=[05:05, 05:10]
    params = {**VALID_PARAMS, "TIME": "2026-03-24T05:10:00Z"}
    captured = []

    class CaptureMDA:
        async def get_latest_time(self, layer_name):
            return None

        async def get_map_assets(self, layer_name, bbox_list, start_dt, end_dt, src_srid=3575):
            captured.append((start_dt, end_dt))
            return []

    await generate_map(CaptureMDA(), params, timedelta(minutes=5))
    start, end = captured[0]
    assert end.minute == 10
    assert start.minute == 5


def test_parse_params_epsg4326_swaps_axes():
    """EPSG:4326 BBOX (minlat,minlon,maxlat,maxlon) is swapped to (minlon,minlat,...)."""
    from sat_wms.getmap import _parse_params

    params = {**VALID_PARAMS, "CRS": "EPSG:4326", "BBOX": "60.0,-30.0,80.0,40.0"}
    result = _parse_params(params)
    assert result.bbox == (-30.0, 60.0, 40.0, 80.0)


def test_parse_params_epsg3575_no_axis_swap():
    """EPSG:3575 BBOX is not swapped (easting-first CRS)."""
    from sat_wms.getmap import _parse_params

    result = _parse_params(VALID_PARAMS)
    assert result.bbox == (-1320000.0, -2781000.0, 569250.0, 245250.0)


def test_read_one_caches_result(test_tif):
    """_read_one returns the same ImageData object for identical arguments (LRU cache hit)."""
    from sat_wms.getmap import _read_one

    bbox = (-1320000.0, -2781000.0, 569250.0, 245250.0)
    result1 = _read_one(test_tif, bbox, "EPSG:3575", 10, 10)
    result2 = _read_one(test_tif, bbox, "EPSG:3575", 10, 10)
    assert result1 is result2


@pytest.mark.asyncio
async def test_generate_map_epsg3857(synth_mda):
    """generate_map accepts an EPSG:3857 BBOX and returns a PNG."""
    from sat_wms.getmap import generate_map

    params = {
        "LAYERS": "true_color_day",
        "BBOX": "1000000,7000000,4000000,9000000",
        "CRS": "EPSG:3857",
        "WIDTH": "256",
        "HEIGHT": "256",
        "TIME": "2026-03-24T05:04:00Z",
    }
    res = await generate_map(synth_mda, params, timedelta(hours=1))
    assert res.status_code == 200
    assert res.body[:4] == b"\x89PNG"
