"""PostGIS-backed metadata repository."""
import psycopg
from psycopg.rows import dict_row

class MetadataRepository:
    """PostGIS-backed metadata repository."""

    def __init__(self, conn_info: str):
        """Initialise with a psycopg connection string."""
        self.conn_info = conn_info

    async def get_layers(self):
        """Return layer summaries: name, time extent, spatial extent."""
        async with await psycopg.AsyncConnection.connect(self.conn_info, row_factory=dict_row) as conn:
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
        async with await psycopg.AsyncConnection.connect(self.conn_info, row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT MAX(time) AS t FROM public.products_viirs WHERE product_name = %s",
                    (layer_name,),
                )
                row = await cur.fetchone()
                return row["t"] if row else None

    async def get_map_assets(self, layer_name, bbox_list, start_dt, end_dt, src_srid=3575):
        """Return filenames of granules intersecting the bbox within the time window."""
        minx, miny, maxx, maxy = bbox_list
        # For geographic CRS (degrees), segmentize the bbox before reprojecting so that
        # the curved edges in polar projections are faithfully represented. For projected
        # CRS (metres), segmentize is not needed and would generate millions of vertices.
        envelope_sql = (
            "ST_Segmentize(ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, %(srid)s), 1.0)"
            if src_srid == 4326
            else "ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, %(srid)s)"
        )
        async with await psycopg.AsyncConnection.connect(self.conn_info, row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    SELECT filename FROM public.products_viirs
                    WHERE product_name = %(layer_name)s
                      AND time >= %(start_dt)s
                      AND time <= %(end_dt)s
                      AND ST_Intersects(
                            geom,
                            ST_Transform({envelope_sql}, 3575)
                          )
                    ORDER BY time DESC;
                """, {
                    "layer_name": layer_name,
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
                    "srid": src_srid,
                })
                rows = await cur.fetchall()
                return [row["filename"] for row in rows]
