"""Tests for the metadata repository factory."""
from sat_wms.local_mda import LocalMetadataRepository, make_mda


def test_csv_path_returns_local_mda():
    """A .csv path produces a LocalMetadataRepository."""
    assert isinstance(make_mda("data/test_data.csv"), LocalMetadataRepository)


def test_local_mda_loads_granules():
    """make_mda from CSV populates the granule list."""
    mda = make_mda("data/test_data.csv")
    assert len(mda.granules) > 0
