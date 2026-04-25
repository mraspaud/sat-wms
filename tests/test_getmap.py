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
    assert res.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=60"


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


@pytest.mark.asyncio
async def test_many_assets_returns_valid_png():
    """generate_map composites correctly when many assets are provided."""
    import numpy as np
    from rio_tiler.models import ImageData

    from sat_wms.getmap import generate_map

    class ManyAssetsMDA:
        async def get_latest_time(self, _layer):
            return None

        async def get_map_assets(self, *_a, **_kw):
            return [{"filename": f"fake_{i}.tif", "bbox": (0.0, 0.0, 1.0, 1.0), "bbox_srid": 3575} for i in range(12)]

    def _fake_read(fp, bbox, crs, w, h):
        data = np.full((3, int(h), int(w)), 128, dtype=np.uint8)
        mask = np.zeros_like(data, dtype=bool)
        return ImageData(np.ma.MaskedArray(data, mask))

    import sat_wms.getmap as gm
    original = gm._read_one
    gm._read_one = _fake_read
    try:
        res = await generate_map(ManyAssetsMDA(), VALID_PARAMS, timedelta(hours=1))
    finally:
        gm._read_one = original

    assert res.status_code == 200
    assert res.body[:4] == b"\x89PNG"


def test_read_one_uses_bilinear_resampling():
    """_read_one must pass resampling_method='bilinear' to COGReader.part to prevent seam artefacts."""
    from unittest.mock import MagicMock, patch

    from sat_wms.getmap import _read_one, _read_one_sync

    _read_one.cache_clear()
    mock_cog = MagicMock()
    mock_cog.__enter__ = MagicMock(return_value=mock_cog)
    mock_cog.__exit__ = MagicMock(return_value=False)

    mock_src = MagicMock()
    mock_src.__enter__ = MagicMock(return_value=mock_src)
    mock_src.__exit__ = MagicMock(return_value=False)
    mock_src.gcps = ([], None)

    import rasterio
    with patch("rasterio.open", return_value=mock_src), \
         patch("sat_wms.getmap.COGReader", return_value=mock_cog):
        with rasterio.Env():
            _read_one_sync("test.tif", (0.0, 0.0, 1.0, 1.0), "EPSG:3575", 256, 256)

    _, call_kwargs = mock_cog.part.call_args
    assert call_kwargs.get("resampling_method") == "bilinear"


def test_read_one_missing_file_returns_none():
    """_read_one returns None when the file does not exist on disk."""
    from sat_wms.getmap import _read_one, _read_one_sync

    _read_one.cache_clear()
    result = _read_one_sync("/nonexistent/missing.tiff", (-1000000, -2000000, 0, 0), "EPSG:3575", 256, 256)
    assert result is None


def test_read_one_gcp_file_pre_wraps_to_dst_crs(tmp_path):
    """_read_one must wrap GCP-based COGs in a WarpedVRT targeting dst_crs."""
    import numpy as np
    import rasterio
    from rasterio.control import GroundControlPoint
    from rasterio.crs import CRS

    from sat_wms.getmap import _read_one, _read_one_sync

    _read_one.cache_clear()

    fp = str(tmp_path / "gcp.tif")
    with rasterio.open(fp, "w", driver="GTiff", height=16, width=16, count=1, dtype="uint8") as ds:
        ds.write(np.zeros((1, 16, 16), dtype="uint8"))
        ds._set_gcps([  # noqa: SLF001
            GroundControlPoint(row=0, col=0, x=10.0, y=74.0),
            GroundControlPoint(row=0, col=15, x=11.0, y=74.0),
            GroundControlPoint(row=15, col=0, x=10.0, y=73.0),
            GroundControlPoint(row=15, col=15, x=11.0, y=73.0),
        ], CRS.from_epsg(4326))

    captured_crs = []
    real_warp = rasterio.vrt.WarpedVRT

    def spy_vrt(src, **kwargs):
        captured_crs.append(kwargs.get("crs"))
        return real_warp(src, **kwargs)

    from unittest.mock import patch
    with patch("rasterio.vrt.WarpedVRT", side_effect=spy_vrt):
        _read_one_sync(fp, (-627608, -3909127, -340705, -3738249), "EPSG:3575", 256, 256)

    assert captured_crs, "WarpedVRT was not called — GCP file must be pre-wrapped"
    assert captured_crs[0] == CRS.from_epsg(3575)


@pytest.mark.asyncio
async def test_generate_map_comma_separated_time_uses_latest(synth_mda):
    """A comma-separated TIME list (as sent by QGIS) uses the latest timestamp."""
    from sat_wms.getmap import generate_map

    params = {**VALID_PARAMS, "TIME": "2026-03-20T12:00:00Z,2026-03-21T00:00:00Z,2026-03-24T05:04:00Z"}
    res = await generate_map(synth_mda, params, timedelta(hours=72))
    assert res.status_code == 200
