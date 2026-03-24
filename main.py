from fastapi.templating import Jinja2Templates
from fastapi import Request
from datetime import datetime

import os
from fastapi import FastAPI, Response

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

# Use 'postgresql+asyncpg' for the modern async driver
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/viirs_db")

# 1. Create the Engine
# We use 'NullPool' if we're in a serverless environment, 
# but for a persistent FastAPI app, the default QueuePool is excellent.
engine = create_async_engine(DATABASE_URL, echo=False)

# 2. Create a Session Factory
async_session = async_sessionmaker(engine, expire_on_commit=False)
BASE_URL = "https://wms-proxy-staging.int-nordmet-nordsat.s.ewcloud.host/viirs"

app = FastAPI()

# Initialize templates
templates = Jinja2Templates(directory="templates")

@app.get("/{duration_str}/")
async def wms_capabilities(duration_str: str, request: Request):
    params = {k.upper(): v for k, v in request.query_params.items()}

    if params.get("REQUEST", params.get("request")) == "GetCapabilities":
        # 1. Fetch data from PostGIS
        raw_layers = await get_layer_metadata()

        # 2. Pre-process data for the template (flooring times, etc.)
        processed_layers = []
        for l in raw_layers:
            # Handle the BBOX string (parsing the BOX() format)
            b = l['bbox'].replace('BOX(', '').replace(')', '').replace(',', ' ').split()
            processed_layers.append({
                "layer_name": l['layer_name'],
                "start_str": floor_dt(l['start_time']).strftime("%Y-%m-%dT%H:%M:00Z"),
                "end_str": floor_dt(l['end_time']).strftime("%Y-%m-%dT%H:%M:00Z"),
                "minx": b[0], "miny": b[1], "maxx": b[2], "maxy": b[3]
            })

        # 3. Render the template
        return templates.TemplateResponse(
            "capabilities.xml", 
            {
                "request": request, 
                "layers": processed_layers,
                "online_resource": f"{BASE_URL}/{duration_str}/"
            },
            media_type="text/xml"
        )

    return Response("GetMap implementation pending.", status_code=200)

def floor_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Floors datetime to the grid (e.g., 12:08 -> 12:00)."""
    return dt.replace(minute=(dt.minute // interval_min) * interval_min, 
                      second=0, microsecond=0)


async def get_layer_metadata():
    """Modern SQLAlchemy 2.0 Async Query."""
    async with async_session() as session:
        # We use 'text()' for raw SQL, or we could use the ORM/Core builder.
        # ST_AsText or ST_Extent works perfectly here.
        stmt = text("""
            SELECT 
                layer_name, 
                MIN(timestamp) as start_time, 
                MAX(timestamp) as end_time,
                ST_Extent(geom) as bbox
            FROM viirs_metadata
            GROUP BY layer_name;
        """)
        
        result = await session.execute(stmt)
        # SQLAlchemy returns 'Row' objects which act like dictionaries
        return result.mappings().all()

class MetadataRepository:
    def __init__(self, conn_info: str):
        self.conn_info = conn_info

    async def get_layers(self):
        """Query using the actual schema: products_viirs."""
        async with await psycopg.AsyncConnection.connect(self.conn_info, row_factory=dict_row) as aconn:
            async with aconn.cursor() as cur:
                await cur.execute("""
                    SELECT 
                        product_name as layer_name, 
                        MIN(time) as start_time, 
                        MAX(time) as end_time, 
                        ST_Extent(geom) as bbox
                    FROM public.products_viirs 
                    GROUP BY product_name;
                """)
                return await cur.fetchall()
