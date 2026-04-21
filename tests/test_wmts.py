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
async def test_wmts_capabilities_title_comes_from_config(local_mda):
    """WMTS capabilities title is read from the wms_title config key."""
    import sat_wms.config as cfg
    from sat_wms.wmts import generate_wmts_capabilities

    with cfg.config.set({"wms_title": "My Sat"}):
        resp = await generate_wmts_capabilities(local_mda)
    assert b"My Sat" in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_title_includes_duration(local_mda):
    """WMTS capabilities title has the duration string appended."""
    import sat_wms.config as cfg
    from sat_wms.wmts import generate_wmts_capabilities

    with cfg.config.set({"wms_title": "My Sat"}):
        resp = await generate_wmts_capabilities(local_mda, duration_str="3h")
    assert b"My Sat (3h)" in resp.body


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


@pytest.mark.asyncio
async def test_wmts_capabilities_hourly_interval_uses_iso_hours(local_mda):
    """A 60-minute interval must appear as PT1H, not PT60M."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda, interval_min=60)
    assert b"PT1H" in resp.body
    assert b"PT60M" not in resp.body


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
async def test_generate_tile_all_out_of_bounds_returns_transparent_png():
    """When all file reads are out-of-bounds, a transparent PNG is returned (not a crash)."""
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import generate_tile

    build_registry(["EPSG:3575"])

    class OutOfBoundsMDA:
        async def get_latest_time(self, layer_name):
            return None

        async def get_map_assets(self, *args, **kwargs):
            return [
                {"filename": "nonexistent1.tif", "bbox": (0.0, 0.0, 1.0, 1.0)},
                {"filename": "nonexistent2.tif", "bbox": (0.0, 0.0, 1.0, 1.0)},
            ]

    from unittest.mock import patch

    with patch("sat_wms.wmts._read_tile", return_value=None):
        resp = await generate_tile(
            OutOfBoundsMDA(), **_TILE_KW, duration=timedelta(hours=1),
            time_str="2026-03-24T06:00:00Z",
        )
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
    assert resp.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=60"


# ---------------------------------------------------------------------------
# HTTP dispatch (iterations 17-18)
# ---------------------------------------------------------------------------


def test_composite_tiles_batch_size_is_large_enough_for_sar():
    """_BATCH_SIZE must be >= 32 to minimise sequential round-trips for large SAR windows."""
    from sat_wms.wmts import _BATCH_SIZE

    assert _BATCH_SIZE >= 32


@pytest.mark.asyncio
async def test_generate_tile_serves_from_disk_cache(tmp_path):
    """generate_tile returns cached bytes without calling the repository when cache hits."""
    from datetime import datetime, timedelta, timezone

    from sat_wms.config import config
    from sat_wms.tile_cache import TileCache
    from sat_wms.time_utils import floor_dt
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import generate_tile

    build_registry(["EPSG:3575"])

    interval_min = 60
    end_dt = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    time_bucket = floor_dt(end_dt, interval_min).strftime("%Y%m%dT%H%M")

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("test_layer", "NorthPolarLAEAEurope", 3, 4, 5, time_bucket, "png", b"\x89PNG-cached")

    class ShouldNotBeCalledMDA:
        async def get_latest_time(self, _layer):
            raise AssertionError("repository should not be called on cache hit")

        async def get_map_assets(self, *_args, **_kwargs):
            raise AssertionError("repository should not be called on cache hit")

    with config.set({"tile_cache_dir": str(tmp_path)}):
        resp = await generate_tile(
            ShouldNotBeCalledMDA(),
            layer="test_layer",
            tms_id="NorthPolarLAEAEurope",
            z=3, y=4, x=5,
            duration=timedelta(hours=1),
            time_str="2026-01-01T12:00:00Z",
            fmt="PNG",
            interval_min=interval_min,
        )

    assert resp.body == b"\x89PNG-cached"


@pytest.mark.asyncio
async def test_generate_tile_writes_rendered_tile_to_cache(tmp_path, synth_mda):
    """After rendering, generate_tile stores the result in the disk cache."""
    from datetime import datetime, timedelta, timezone

    from sat_wms.config import config
    from sat_wms.tile_cache import TileCache
    from sat_wms.time_utils import floor_dt
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import generate_tile

    build_registry(["EPSG:3575"])
    interval_min = 60

    with config.set({"tile_cache_dir": str(tmp_path)}):
        resp = await generate_tile(
            synth_mda,
            layer="true_color_day",
            tms_id="NorthPolarLAEAEurope",
            z=3, y=4, x=3,
            duration=timedelta(hours=6),
            time_str="2026-03-24T06:00:00Z",
            fmt="PNG",
            interval_min=interval_min,
        )

    assert resp.status_code == 200
    end_dt = datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc)
    time_bucket = floor_dt(end_dt, interval_min).strftime("%Y%m%dT%H%M")
    cache = TileCache(cache_dir=str(tmp_path))
    cached = cache.get("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, time_bucket, "png")
    assert cached == resp.body


def test_read_tile_uses_bilinear_resampling():
    """_read_tile must pass resampling_method='bilinear' to COGReader.tile to prevent seam artefacts."""
    from unittest.mock import MagicMock, patch

    from sat_wms.wmts import _read_tile

    _read_tile.cache_clear()
    mock_cog = MagicMock()
    mock_cog.__enter__ = MagicMock(return_value=mock_cog)
    mock_cog.__exit__ = MagicMock(return_value=False)

    mock_src = MagicMock()
    mock_src.__enter__ = MagicMock(return_value=mock_src)
    mock_src.__exit__ = MagicMock(return_value=False)
    mock_src.gcps = ([], None)

    with patch("rasterio.open", return_value=mock_src), \
         patch("rio_tiler.io.COGReader", return_value=mock_cog):
        _read_tile("test.tif", "NorthPolarLAEAEurope", 3, 4, 3)

    mock_cog.tile.assert_called_once_with(3, 4, 3, resampling_method="bilinear")


def test_read_tile_gcp_file_pre_wraps_to_tms_crs(tmp_path):
    """_read_tile must wrap GCP-based COGs in a WarpedVRT targeting the TMS CRS.

    This ensures GDAL overview selection operates in metres (the TMS CRS units)
    rather than degrees, which prevents pixelation at high latitudes.
    """
    import numpy as np
    import rasterio
    from rasterio.control import GroundControlPoint
    from rasterio.crs import CRS

    from sat_wms.tms_registry import build_registry, get_by_name
    from sat_wms.wmts import _read_tile

    build_registry(["EPSG:3575"])
    _read_tile.cache_clear()

    # A small GeoTIFF with GCPs in EPSG:4326 covering Arctic Norway (~74°N, 10-11°E).
    fp = str(tmp_path / "gcp.tif")
    with rasterio.open(fp, "w", driver="GTiff", height=16, width=16, count=1, dtype="uint8") as ds:
        ds.write(np.zeros((1, 16, 16), dtype="uint8"))
        ds._set_gcps([  # noqa: SLF001
            GroundControlPoint(row=0, col=0, x=10.0, y=74.0),
            GroundControlPoint(row=0, col=15, x=11.0, y=74.0),
            GroundControlPoint(row=15, col=0, x=10.0, y=73.0),
            GroundControlPoint(row=15, col=15, x=11.0, y=73.0),
        ], CRS.from_epsg(4326))

    tms = get_by_name("NorthPolarLAEAEurope")
    captured_crs = []
    real_warp = rasterio.vrt.WarpedVRT

    def spy_vrt(src, **kwargs):
        captured_crs.append(kwargs.get("crs"))
        return real_warp(src, **kwargs)

    from unittest.mock import patch
    with patch("rasterio.vrt.WarpedVRT", side_effect=spy_vrt):
        _read_tile(fp, "NorthPolarLAEAEurope", 3, 4, 3)

    assert captured_crs, "WarpedVRT was not called — GCP file must be pre-wrapped"
    assert captured_crs[0] == tms.crs, f"WarpedVRT must target TMS CRS {tms.crs}, got {captured_crs[0]}"

@pytest.fixture
def wmts_client(local_mda):
    """TestClient with LocalMetadataRepository injected via app.state."""
    from sat_wms.app import app
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


# ---------------------------------------------------------------------------
# WMTS capabilities - stepped timestep mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wmts_capabilities_stepped_mode_lists_discrete_times(local_mda):
    """In 'stepped' mode the WMTS Dimension Value lists comma-separated ISO timestamps."""
    import sat_wms.config as cfg
    from sat_wms.wmts import generate_wmts_capabilities

    with cfg.config.set({"timestep_mode": "stepped", "snapshot_step": "24h", "snapshot_count": 2}):
        resp = await generate_wmts_capabilities(local_mda)
    xml = resp.body.decode()
    # No interval syntax
    assert "Z/PT" not in xml
    # Comma-separated timestamps present
    assert xml.count("Z,") >= 1


@pytest.mark.asyncio
async def test_wmts_capabilities_stepped_mode_first_time_is_latest(local_mda):
    """In 'stepped' mode the first listed time equals the layer's latest data time."""
    import sat_wms.config as cfg
    from sat_wms.wmts import generate_wmts_capabilities

    with cfg.config.set({"timestep_mode": "stepped", "snapshot_step": "24h", "snapshot_count": 1}):
        resp = await generate_wmts_capabilities(local_mda)
    assert b"2026-03-24T05:34:29Z" in resp.body


@pytest.mark.asyncio
async def test_wmts_capabilities_layer_name_prefix_applied(local_mda):
    """When layer_name_prefix is set, it is prepended to every layer name in WMTS capabilities."""
    from sat_wms.wmts import generate_wmts_capabilities

    resp = await generate_wmts_capabilities(local_mda, layer_name_prefix="Sentinel-1 SAR ")
    xml = resp.body.decode()
    assert "Sentinel-1 SAR true_color_day" in xml



@pytest.mark.asyncio
async def test_generate_tile_stepped_mode_uses_exact_time_as_cache_key(tmp_path, synth_mda):
    """In stepped mode the cache key is the exact requested time, not floor'd."""
    from datetime import timedelta

    import sat_wms.config as cfg
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import generate_tile

    build_registry(["EPSG:3575"])
    time_str = "2026-03-24T10:37:00Z"  # not on any 60-min boundary

    with cfg.config.set({"tile_cache_dir": str(tmp_path), "timestep_mode": "stepped"}):
        await generate_tile(synth_mda, **_TILE_KW, duration=timedelta(hours=6),
                            time_str=time_str, fmt="PNG", interval_min=60)

    # Cache file must use exact time (103700), not floored to 100000
    from sat_wms.tile_cache import TileCache
    cache = TileCache(cache_dir=str(tmp_path))
    assert cache.get("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, "20260324T1037", "png") is not None
    assert cache.get("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, "20260324T1000", "png") is None


@pytest.mark.asyncio
async def test_generate_tile_stepped_mode_moves_tile_when_no_new_or_removed_data(tmp_path):
    """In stepped mode, if no data was added or removed, the old tile is moved to new bucket."""
    from datetime import datetime, timedelta, timezone

    import sat_wms.config as cfg
    from sat_wms.tile_cache import TileCache
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import generate_tile

    build_registry(["EPSG:3575"])

    class StableMDA:
        """MDA where no new or removed data affects the tile."""
        async def get_latest_time(self, _layer):
            return datetime(2026, 3, 24, 10, 37, tzinfo=timezone.utc)

        async def get_map_assets(self, layer, bbox, start_dt, end_dt, src_srid):
            # Return empty for both the "added" and "removed" window queries
            return []

    cache = TileCache(cache_dir=str(tmp_path))
    old_bucket = "20260324T1000"
    cache.put("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, old_bucket, "png", b"\x89PNG-old")
    cache.set_latest("true_color_day", "NorthPolarLAEAEurope", old_bucket)

    with cfg.config.set({"tile_cache_dir": str(tmp_path), "timestep_mode": "stepped"}):
        await generate_tile(
            StableMDA(), **_TILE_KW,
            duration=timedelta(hours=6),
            time_str="2026-03-24T10:37:00Z", fmt="PNG",
        )

    # Old tile should have been moved to new bucket
    assert cache.get("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, "20260324T1037", "png") == b"\x89PNG-old"
    # Old bucket still accessible (hard link, not rename)
    assert cache.get("true_color_day", "NorthPolarLAEAEurope", 3, 4, 3, old_bucket, "png") == b"\x89PNG-old"


# ---------------------------------------------------------------------------
# Missing files on disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_tile_missing_file_returns_none():
    """_read_tile returns None when the file is not found on disk."""
    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import _read_tile

    build_registry(["EPSG:3575"])
    _read_tile.cache_clear()
    result = _read_tile("/nonexistent/path/file.tiff", "NorthPolarLAEAEurope", 3, 4, 3)
    assert result is None


@pytest.mark.asyncio
async def test_generate_tile_skips_missing_file_and_renders_rest(tmp_path, synth_mda):
    """generate_tile renders a tile even when one of the matched files is missing on disk."""
    from datetime import timedelta

    from sat_wms.tms_registry import build_registry
    from sat_wms.wmts import _read_tile, generate_tile

    build_registry(["EPSG:3575"])
    _read_tile.cache_clear()

    class MissingFileMDA:
        """MDA returning one valid asset and one missing file."""
        async def get_latest_time(self, _layer):
            return None

        async def get_map_assets(self, *_args, **_kwargs):
            assets = await synth_mda.get_map_assets(*_args, **_kwargs)
            return assets + [{"filename": "/nonexistent/missing.tiff",
                               "bbox": assets[0]["bbox"] if assets else (0, 0, 1, 1)}]

    resp = await generate_tile(
        MissingFileMDA(), **_TILE_KW,
        duration=timedelta(hours=6),
        time_str="2026-03-24T06:00:00Z",
    )
    _read_tile.cache_clear()
    assert resp.status_code == 200
    assert resp.body[:4] == b"\x89PNG"
