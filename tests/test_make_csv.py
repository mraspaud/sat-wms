"""Tests for scripts/make_csv.py."""
from datetime import datetime
from pathlib import Path

PATTERN = "{start_time:%Y%m%d_%H%M}_{satellite}_{scene}_{srid}_{resolution}_{product_name}.tif"
FILENAME = "20260324_0504_Suomi-NPP_dynamic_3575_750_true_color_day.tif"


def test_parse_filename_extracts_product_name():
    """Trollsift pattern extracts product_name from a filename."""
    from make_csv import _parse_filename

    product_name, _ = _parse_filename(FILENAME, PATTERN, "start_time", "product_name")
    assert product_name == "true_color_day"


def test_parse_filename_extracts_datetime():
    """Trollsift pattern extracts start_time as a datetime."""
    from make_csv import _parse_filename

    _, time = _parse_filename(FILENAME, PATTERN, "start_time", "product_name")
    assert time == datetime(2026, 3, 24, 5, 4)


def test_geotiff_srid_reads_epsg_from_file(test_tif):
    """_geotiff_srid returns the EPSG code from the GeoTIFF CRS."""
    from make_csv import _geotiff_srid

    assert _geotiff_srid(test_tif) == 3575


def test_geotiff_bounds_wkt_is_closed_polygon(test_tif):
    """_geotiff_bounds_wkt returns a WKT polygon whose first and last point match."""
    from make_csv import _geotiff_bounds_wkt

    wkt = _geotiff_bounds_wkt(test_tif)
    assert wkt.startswith("POLYGON((")
    coords = wkt.removeprefix("POLYGON((").removesuffix("))").split(",")
    assert coords[0] == coords[-1]  # closed ring


def test_build_row_contains_all_csv_fields(test_tif):
    """build_row returns a dict with all five CSV columns populated."""
    from make_csv import build_row

    fname = f"20260324_0504_Suomi-NPP_dynamic_3575_750_true_color_day{Path(test_tif).suffix}"
    filepath = str(Path(test_tif).parent / fname)
    Path(filepath).symlink_to(test_tif)

    row = build_row(filepath, PATTERN)
    assert row["product_name"] == "true_color_day"
    assert row["time"] == "2026-03-24 05:04:00"
    assert row["srid"] == 3575
    assert row["geom_wkt"].startswith("POLYGON")
    assert row["filename"] == filepath
