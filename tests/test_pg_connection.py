import csv
from datetime import datetime, timezone
from shapely import wkt
from shapely.geometry import box

class TestMetadataRepository:
    def __init__(self, csv_path: str = None):
        self.granules = []
        if csv_path:
            self.load_from_csv(csv_path)

    def load_from_csv(self, csv_path):
        with open(csv_path, mode='r') as f:
            lines = f.readlines()
            reader = csv.DictReader(lines)
            for row in reader:
                self.granules.append({
                    "product_name": row['product_name'],
                    "time": datetime.fromisoformat(row['time']).replace(tzinfo=timezone.utc),
                    "srid": int(row['srid']),
                    "geom": wkt.loads(row['geom_wkt']),
                    "filename": row['filename']
                })

    async def get_layers(self):
        """Simulates: SELECT product_name, MIN(time), MAX(time), ST_Extent(geom)..."""
        products = {}
        for g in self.granules:
            name = g['product_name']
            if name not in products:
                products[name] = {"start": g['time'], "end": g['time'], "geoms": []}
            products[name]["start"] = min(products[name]["start"], g['time'])
            products[name]["end"] = max(products[name]["end"], g['time'])
            products[name]["geoms"].append(g['geom'])

        # Format for the Jinja2 template
        results = []
        for name, data in products.items():
            # Combine all polygons to get a total extent
            # (Simplistic version of ST_Extent)
            total_bounds = box(*wkt.loads(str(data["geoms"][0])).bounds)
            for geo in data["geoms"]:
                total_bounds = total_bounds.union(geo)
            minx, miny, maxx, maxy = total_bounds.bounds
            results.append({
                "layer_name": name,
                "start_time": data["start"],
                "end_time": data["end"],
                "bbox": f"BOX({minx} {miny}, {maxx} {maxy})"
            })
        return results

    async def get_map_assets(self, layer_name, bbox_list, start_dt, end_dt):
        """Simulates the ST_Intersects and time filtering."""
        query_box = box(*bbox_list)
        matches = []
        for g in self.granules:
            if (g['product_name'] == layer_name and 
                start_dt <= g['time'] <= end_dt and 
                g['geom'].intersects(query_box)):
                matches.append(g)
        # Order by time DESC and limit 10, just like the real SQL
        matches.sort(key=lambda x: x['time'], reverse=True)
        return [m['filename'] for m in matches[:10]]


def test_generate_capabilities():
    from sat_wms.capabilities import generate
    generate()
    pass
