"""Helpers for parsing PostGIS geometry strings."""


def parse_postgis_box(box_str: str) -> tuple[float, float, float, float]:
    """Parse a PostGIS ST_Extent string 'BOX(x0 y0, x1 y1)' into (minx, miny, maxx, maxy)."""
    coords = box_str.replace("BOX(", "").replace(")", "").replace(",", " ").split()
    return float(coords[0]), float(coords[1]), float(coords[2]), float(coords[3])
