"""PostGIS-backed metadata repository."""
from psycopg.rows import dict_row


def _parse_box(box_str: str) -> tuple[float, float, float, float]:
    """Parse a PostGIS BOX string into (minx, miny, maxx, maxy)."""
    coords = box_str.replace("BOX(", "").replace(")", "").replace(",", " ").split()
    return tuple(float(c) for c in coords)


class PooledMetadataRepository:
    """PostGIS-backed repository that borrows connections from a shared pool."""

    def __init__(self, pool):
        """Initialise with an AsyncConnectionPool."""
        self._pool = pool

    async def get_layers(self):
        """Return layer summaries: name, time extent, spatial extent."""
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT
                        product_name AS layer_name,
                        MIN(time) AS start_time,
                        MAX(time) AS end_time,
                        ST_Extent(geom)::text AS bbox
                    FROM public.products_viirs
                    GROUP BY product_name;
                """)
                return await cur.fetchall()

    async def get_latest_time(self, layer_name):
        """Return the timestamp of the most recent granule for a layer, or None."""
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT MAX(time) AS t FROM public.products_viirs WHERE product_name = %s",
                    (layer_name,),
                )
                row = await cur.fetchone()
                return row["t"] if row else None

    async def get_map_assets(self, layer_name, bbox_list, start_dt, end_dt, src_srid=3575):
        """Return assets (filename + bbox) of granules intersecting the bbox within the time window."""
        minx, miny, maxx, maxy = bbox_list
        # Segmentize the bbox before reprojecting to EPSG:3575 so that curved edges
        # (e.g. latitude lines in polar projections) are faithfully represented.
        # Units match the source CRS: degrees for geographic (4326), metres for projected.
        seg_length = "1.0" if src_srid == 4326 else "1e5"
        envelope_sql = (
            f"ST_Segmentize(ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, %(srid)s), {seg_length})"
        )
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    SELECT filename,
                           ST_Extent(ST_Transform(geom, %(src_srid)s))::text AS granule_bbox,
                           %(src_srid)s AS bbox_srid
                    FROM public.products_viirs
                    WHERE product_name = %(layer_name)s
                      AND time >= %(start_dt)s
                      AND time <= %(end_dt)s
                      AND ST_Intersects(
                            geom,
                            ST_Transform({envelope_sql}, 3575)
                          )
                    GROUP BY filename, time
                    ORDER BY time DESC;
                """, {
                    "layer_name": layer_name,
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
                    "srid": src_srid, "src_srid": src_srid,
                })
                rows = await cur.fetchall()
                return [
                    {"filename": row["filename"], "bbox": _parse_box(row["granule_bbox"]),
                     "bbox_srid": row["bbox_srid"]}
                    for row in rows
                ]
