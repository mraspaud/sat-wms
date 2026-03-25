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

### Development

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
uv run ruff check src/ tests/ main.py
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
