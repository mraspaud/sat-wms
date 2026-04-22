"""Tests for postgis_utils helpers."""


def test_parse_postgis_box_returns_float_tuple():
    """parse_postgis_box returns (minx, miny, maxx, maxy) as floats."""
    from sat_wms.postgis_utils import parse_postgis_box

    result = parse_postgis_box("BOX(-1320000.0 -2781000.5, 569250.0 245250.75)")
    assert result == (-1320000.0, -2781000.5, 569250.0, 245250.75)
