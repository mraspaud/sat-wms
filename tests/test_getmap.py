"""Tests for GetMap."""
from datetime import datetime, timedelta, timezone

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


@pytest.mark.asyncio
async def test_generate_map_webp_returns_webp_content_type(synth_mda):
    """FORMAT=image/webp produces a response with media_type image/webp."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "FORMAT": "image/webp"}
    res = await generate_map(synth_mda, params, timedelta(hours=1))
    assert res.media_type == "image/webp"


@pytest.mark.asyncio
async def test_generate_map_webp_body_starts_with_riff(synth_mda):
    """FORMAT=image/webp response body is a valid WebP file."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "FORMAT": "image/webp"}
    res = await generate_map(synth_mda, params, timedelta(hours=1))
    assert res.body[:4] == b"RIFF"
    assert res.body[8:12] == b"WEBP"


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
async def test_generate_map_empty_no_content_returns_204(local_mda):
    """empty_no_content=True returns 204 with no body when no granules match."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "TIME": "2099-01-01T00:00:00Z"}
    res = await generate_map(local_mda, params, timedelta(hours=1), empty_no_content=True)
    assert res.status_code == 204
    assert not res.body


@pytest.mark.asyncio
async def test_generate_map_empty_no_content_false_still_returns_image(local_mda):
    """empty_no_content=False (default) keeps returning a transparent image."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "TIME": "2099-01-01T00:00:00Z"}
    res = await generate_map(local_mda, params, timedelta(hours=1), empty_no_content=False)
    assert res.status_code == 200
    assert res.body[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_force_webp_overrides_png_request(synth_mda):
    """force_webp=True returns WebP even when the client requests image/png."""
    from sat_wms.getmap import generate_map

    res = await generate_map(synth_mda, VALID_PARAMS, timedelta(hours=1), force_webp=True)
    assert res.media_type == "image/webp"
    assert res.body[8:12] == b"WEBP"


@pytest.mark.asyncio
async def test_latest_granule_gets_short_cache_ttl():
    """When TIME equals the latest granule time, Cache-Control is max-age=60."""
    from sat_wms.getmap import generate_map

    latest_time = datetime(2026, 3, 24, 5, 4, tzinfo=timezone.utc)

    class LatestMDA:
        async def get_latest_time(self, layer_name):
            return latest_time

        async def get_map_assets(self, *args, **kwargs):
            return []

    params = {**VALID_PARAMS, "TIME": "2026-03-24T05:04:00Z"}
    res = await generate_map(LatestMDA(), params, timedelta(minutes=30))
    assert res.headers["cache-control"] == "public, max-age=60"


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


@pytest.mark.asyncio
async def test_generate_map_epsg4326_interprets_bbox_as_lat_lon(synth_mda):
    """WMS 1.3.0: EPSG:4326 BBOX is (minlat,minlon,maxlat,maxlon); generate_map must accept it."""
    from sat_wms.getmap import generate_map

    params = {
        "LAYERS": "true_color_day",
        "CRS": "EPSG:4326",
        "BBOX": "-90.0,-180.0,90.0,180.0",
        "WIDTH": "256",
        "HEIGHT": "256",
    }
    res = await generate_map(synth_mda, params, timedelta(hours=1))
    assert res.status_code == 200
