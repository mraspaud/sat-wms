"""Tests for the metadata repository factory."""
from sat_wms.local_mda import LocalMetadataRepository, make_mda
from sat_wms.pg_mda import MetadataRepository


def test_csv_connection_string_returns_local_mda():
    """A .csv path produces a LocalMetadataRepository."""
    assert isinstance(make_mda("data/test_data.csv"), LocalMetadataRepository)


def test_db_connection_string_returns_metadata_repository():
    """A postgresql:// URL produces a MetadataRepository."""
    assert isinstance(make_mda("postgresql://user:pass@localhost/db"), MetadataRepository)
