"""CSV-backed metadata repository and MDA factory."""
import csv
from datetime import datetime, timezone

from shapely import wkt
from shapely.geometry import box


class LocalMetadataRepository:
    """CSV-backed stub that simulates PostGIS queries using Shapely."""

    def __init__(self, csv_path=None):
        """Initialise, optionally loading granules from a CSV file."""
        self.granules = []
        if csv_path:
            self._load(csv_path)

    def _load(self, csv_path):
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                self.granules.append({
                    "product_name": row["product_name"],
                    "time": datetime.fromisoformat(row["time"]).replace(tzinfo=timezone.utc),
                    "srid": int(row["srid"]),
                    "geom": wkt.loads(row["geom_wkt"]),
                    "filename": row["filename"],
                })

    async def get_layers(self):
        """Simulate: SELECT product_name, MIN(time), MAX(time), ST_Extent(geom)..."""
        products = {}
        for g in self.granules:
            name = g["product_name"]
            if name not in products:
                products[name] = {"start": g["time"], "end": g["time"], "geoms": []}
            products[name]["start"] = min(products[name]["start"], g["time"])
            products[name]["end"] = max(products[name]["end"], g["time"])
            products[name]["geoms"].append(g["geom"])

        results = []
        for name, data in products.items():
            total = box(*data["geoms"][0].bounds)
            for geo in data["geoms"]:
                total = total.union(geo)
            minx, miny, maxx, maxy = total.bounds
            results.append({
                "layer_name": name,
                "start_time": data["start"],
                "end_time": data["end"],
                "bbox": f"BOX({minx} {miny}, {maxx} {maxy})",
            })
        return results

    async def get_latest_time(self, layer_name):
        """Return the timestamp of the most recent granule for a layer, or None."""
        times = [g["time"] for g in self.granules if g["product_name"] == layer_name]
        return max(times) if times else None

    async def get_map_assets(self, layer_name, bbox_list, start_dt, end_dt, src_srid=3575):
        """Simulate ST_Intersects + time filter. src_srid is ignored (geometries share the same CRS)."""
        query_box = box(*bbox_list)
        matches = [
            g for g in self.granules
            if g["product_name"] == layer_name
            and start_dt <= g["time"] <= end_dt
            and g["geom"].intersects(query_box)
        ]
        matches.sort(key=lambda g: g["time"], reverse=True)
        return [g["filename"] for g in matches]


def make_mda(conn_str: str):
    """Return a LocalMetadataRepository for .csv paths, else a MetadataRepository."""
    if conn_str.endswith(".csv"):
        return LocalMetadataRepository(conn_str)
    from sat_wms.pg_mda import MetadataRepository  # noqa: PLC0415 (avoid circular at module load)
    return MetadataRepository(conn_str)
