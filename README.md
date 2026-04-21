# sat-wms

OGC WMS 1.3.0 and WMTS 1.0.0 server for time-based EO satellite data, backed by PostGIS or CSV.

Serves `GetCapabilities`, `GetMap` (WMS), and `GetTile` (WMTS) requests over a `public.products_viirs` PostGIS table. `GetMap`/`GetTile` composite Cloud-Optimized GeoTIFFs (COGs) newest-first into a single PNG or WebP tile.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 14+ with the PostGIS extension (production)

## Installation

```bash
git clone <repo-url>
cd sat-wms
uv sync --all-groups
```

## Database setup

Apply the schema to an existing PostGIS-enabled database:

```bash
psql "$DATABASE_URL" -f data/schema.sql
```

The schema creates one table:

```sql
CREATE TABLE public.products_viirs (
    id            integer PRIMARY KEY,
    filename      varchar,   -- path or URI to the COG file
    product_name  varchar,   -- layer name exposed in GetCapabilities
    time          timestamp, -- granule timestamp (UTC)
    geom          geometry   -- footprint in any SRID
);
```

## Running

### Without a database — CSV mode

The app can run entirely without PostGIS by pointing it at a CSV catalogue file. This is useful for local development or when data lives on a shared filesystem.

**Step 1 — build the catalogue.** Use `scripts/make_csv.py` with a [trollsift](https://trollsift.readthedocs.io/) filename pattern that extracts `start_time` and `product_name` from your GeoTIFF filenames:

```bash
uv run --group scripts python scripts/make_csv.py \
  '{start_time:%Y%m%d_%H%M}_{satellite}_{scene}_{srid}_{resolution}_{product_name}.tif' \
  /data/viirs/*.tif \
  --output catalogue.csv
```

The script reads the CRS and bounding box directly from each file, so no manual metadata is needed.

```
# Custom key names if your pattern uses different names
uv run --group scripts python scripts/make_csv.py \
  '{time:%Y%m%d_%H%M}_{name}.tif' /data/*.tif \
  --time-key time --product-key name \
  --output catalogue.csv
```

**Step 2 — start the server:**

```bash
SAT_WMS_DATABASE_URL=catalogue.csv uv run fastapi dev main.py
```

### Development (PostGIS)

Hot-reloads on source changes; logs requests to the console.

```bash
uv run fastapi dev main.py
```

The server starts at `http://localhost:8000`. Test it:

```
# WMS
http://localhost:8000/30m/?REQUEST=GetCapabilities

# WMTS
http://localhost:8000/30m/wmts/?REQUEST=GetCapabilities
```

### Production

```bash
uv run fastapi run main.py --host 0.0.0.0 --port 8000
```

For multiple workers (recommended behind a reverse proxy):

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Endpoints

All endpoints share a `{duration}` path prefix (e.g. `30m`, `2h`, `1d`) that sets the time window for data queries.

### WMS

| Request | URL |
|---|---|
| `GetCapabilities` | `GET /{duration}/?REQUEST=GetCapabilities` |
| `GetMap` | `GET /{duration}/?REQUEST=GetMap&...` |

### WMTS

| Request | URL |
|---|---|
| `GetCapabilities` (KVP) | `GET /{duration}/wmts/?REQUEST=GetCapabilities` |
| `GetTile` (KVP) | `GET /{duration}/wmts/?REQUEST=GetTile&LAYER=...&TILEMATRIXSET=...&TILEMATRIX=...&TILEROW=...&TILECOL=...` |
| `GetTile` (REST) | `GET /{duration}/wmts/{layer}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}` |

The WMTS capabilities document uses OGC URN CRS identifiers (`urn:ogc:def:crs:EPSG::XXXX`), which OpenLayers resolves correctly via `ol/proj`.

The service title in both WMS and WMTS capabilities includes the duration suffix, e.g. `"Sat-WMS (30m)"`, so clients can distinguish windows.

## Configuration

All settings are read from environment variables at startup with the prefix `SAT_WMS_`.

| Variable | Default | Description |
|---|---|---|
| `SAT_WMS_DATABASE_URL` | `postgresql://user:pass@localhost/viirs_db` | psycopg connection string |
| `SAT_WMS_BASE_URL` | `http://localhost:8000` | Public base URL included in `OnlineResource` |
| `SAT_WMS_WMS_TITLE` | `Sat-WMS` | Base service title in GetCapabilities (duration suffix appended automatically) |
| `SAT_WMS_GRANULE_INTERVAL` | `5m` | Granule repeat cycle (`5m`, `10m`, `15m`, `1h`, …) |
| `SAT_WMS_FORCE_WEBP` | `false` | Serve only WebP tiles (omits PNG from format list) |
| `SAT_WMS_EMPTY_NO_CONTENT` | `false` | Return HTTP 204 instead of a transparent tile when no data is available |
| `SAT_WMS_WMTS_MAX_ZOOM` | `9` | Maximum zoom level advertised and served by WMTS |
| `SAT_WMS_TILE_CACHE_ENTRIES` | `128` | Number of COG tile reads to keep in memory (~1 MB each, so `1024` ≈ 1 GB) |
| `SAT_WMS_TILE_CACHE_DIR` | *(disabled)* | Directory for the **disk tile cache** (see [Disk tile cache](#disk-tile-cache)). Leave empty to disable. |
| `SAT_WMS_TILE_CACHE_TTL_DAYS` | `7` | Disk cache TTL in days — tiles older than this are evicted on next access |

`supported_crs` (list of EPSG codes) can only be set via a YAML config file — environment variables do not support lists. Place the file at one of the standard donfig search paths:

```yaml
# ~/.config/sat_wms/sat_wms.yaml
supported_crs:
  - EPSG:3575
  - EPSG:3857
  - EPSG:5041
  - EPSG:4326
```

### Example production environment

```bash
export SAT_WMS_DATABASE_URL="postgresql://wms:secret@db.example.com/viirs"
export SAT_WMS_BASE_URL="https://wms.example.com"
export SAT_WMS_WMS_TITLE="My Satellite WMS"
export SAT_WMS_GRANULE_INTERVAL="5m"
export SAT_WMS_TILE_CACHE_ENTRIES="512"
```

## Testing

The test suite uses a CSV-backed stub repository so no database is required.

```bash
# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_getmap.py::test_generate_map_returns_png

# With verbose output
uv run pytest -v
```

## Linting

```bash
uv run ruff check src/ tests/ scripts/
```

## Disk tile cache

For long aggregation windows (e.g. 48 h of SAR data, ~250 granules) generating every tile on the fly is expensive even with parallelised COG reads.  The disk tile cache renders each WMTS tile once and stores it to disk; subsequent requests for the same tile are served directly from the file system without touching the database or COGs.

Enable the cache by setting `SAT_WMS_TILE_CACHE_DIR` to a writable directory:

```bash
export SAT_WMS_TILE_CACHE_DIR=/var/cache/sat-wms
export SAT_WMS_TILE_CACHE_TTL_DAYS=7
```

Cache layout: `{cache_dir}/{layer}/{tms_id}/{z}/{y}/{x}_{time_bucket}.{ext}` where `time_bucket = floor(request_time, granule_interval)`.

The latest time-bucket is automatically re-rendered after one `granule_interval` so that fresh data is picked up as new granules arrive; all other tiles are held for `tile_cache_ttl_days` days.

Disk usage is roughly `(tiles per session) × (tile size)`.  For SAR at z=5 over a 48 h window, expect ~1–3 MB per tile.

---

## Preparing SAR COGs for sat-wms

Sentinel-1 IW granules need to be in Cloud-Optimized GeoTIFF format with a properly embedded CRS before sat-wms can serve them.

### Requirements

| Parameter | Required value | Notes |
|---|---|---|
| CRS | EPSG:3575 (Arctic LAEA) or matching PostGIS SRID | **Must be embedded** — files without a CRS are invisible to rio-tiler |
| Affine transform | Derived from the granule's georeferenced bounding box | |
| Internal tile size | 512 × 512 px | Already set in current granules ✓ |
| Overview levels | 2 / 4 / 8 / 16 / 32 / 64 / **128** | Level 128 covers ~5 km/px; current granules stop at 64 |
| Overview resampling | `AVERAGE` | Preserves radiometry better than `NEAREST` |
| Compression | JPEG + `PHOTOMETRIC=YCBCR` | Baseline; reduces chromatic noise; valid in COGs via GDAL |

### Georeferencing and overview creation

Use `scripts/georeference_sar.py` (see script header for full instructions):

```bash
# 1. Export bbox from PostGIS
psql "$DATABASE_URL" -c \
  "COPY (
     SELECT filename,
            ST_SRID(geom)          AS epsg,
            ST_XMin(geom::box2d)   AS minx,
            ST_YMin(geom::box2d)   AS miny,
            ST_XMax(geom::box2d)   AS maxx,
            ST_YMax(geom::box2d)   AS maxy
     FROM   public.products_sat
     WHERE  product_name = 'sar-ice-log-v'
   ) TO '/tmp/sar_bbox.csv' CSV HEADER"

# 2. Georeference and extend overviews
uv run --group scripts python scripts/georeference_sar.py /tmp/sar_bbox.csv

# 3. Build the CSV catalogue
uv run --group scripts python scripts/make_csv.py \
    --pattern '{start_time:%Y%m%d_%H%M%S}_Sentinel-1{_sensor}_iw_{product_name}_40m.tiff' \
    --srid 3575 \
    data/sar/cog/*.tiff > data/sar/sar.csv

# 4. Start sat-wms in CSV mode
SAT_WMS_DATABASE_URL=data/sar/sar.csv uv run fastapi dev src/sat_wms/app.py
```

### Compression trade-offs

| Compression | Size vs JPEG+YCBCR | Decode speed | Notes |
|---|---|---|---|
| **JPEG + YCBCR** | 1× (baseline) | fast | Current approach; reduces chromatic noise |
| JPEG + RGB | ~+10–20% | fast | More compatible; safe fallback |
| ZSTD + `PREDICTOR=2` | ~5–8× larger | very fast | Lossless; good if storage is not a constraint |
| LZW | ~10× larger | moderate | **Avoid** — confirmed unacceptable for this use case |

For read-speed benchmarking use `scripts/bench_sar.py` (see its header for setup instructions).

---



Apache License 2.0 — see [LICENSE](LICENSE).
