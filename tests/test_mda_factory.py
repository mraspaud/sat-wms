"""Tests for the metadata repository factory."""
from datetime import datetime, timezone

import pytest

from sat_wms.local_mda import LocalMetadataRepository, make_mda


def test_csv_path_returns_local_mda():
    """A .csv path produces a LocalMetadataRepository."""
    assert isinstance(make_mda("data/test_data.csv"), LocalMetadataRepository)


def test_local_mda_loads_granules():
    """make_mda from CSV populates the granule list."""
    mda = make_mda("data/test_data.csv")
    assert len(mda.granules) > 0


@pytest.mark.asyncio
async def test_get_map_assets_returns_dicts_with_filename_and_bbox():
    """get_map_assets returns list of dicts with 'filename' and 'bbox' keys."""
    mda = make_mda("data/test_data.csv")
    assets = await mda.get_map_assets(
        "true_color_day",
        [-5400000, -5400000, 5400000, 5400000],
        datetime(2026, 3, 24, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
    )
    assert len(assets) > 0
    for asset in assets:
        assert "filename" in asset
        assert "bbox" in asset
        assert isinstance(asset["filename"], str)
        # bbox should be a 4-tuple of floats (minx, miny, maxx, maxy)
        assert len(asset["bbox"]) == 4
        assert all(isinstance(v, float) for v in asset["bbox"])
        assert isinstance(asset["bbox_srid"], int)
