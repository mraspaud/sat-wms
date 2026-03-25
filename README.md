# sat-wms

OGC WMS 1.3.0 server for Nordsat VIIRS satellite data, backed by PostGIS.

Serves `GetCapabilities` and `GetMap` requests over a `public.products_viirs` PostGIS table. `GetMap` composites Cloud-Optimized GeoTIFFs (COGs) newest-first into a single PNG tile.

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
http://localhost:8000/30m/?REQUEST=GetCapabilities
```

### Production

```bash
uv run fastapi run main.py --host 0.0.0.0 --port 8000
```

For multiple workers (recommended behind a reverse proxy):

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Configuration

All settings are read from environment variables at startup with the prefix `SAT_WMS_`.

| Variable | Default | Description |
|---|---|---|
| `SAT_WMS_DATABASE_URL` | `postgresql://user:pass@localhost/viirs_db` | psycopg connection string |
| `SAT_WMS_BASE_URL` | `http://localhost:8000` | Public base URL included in `OnlineResource` |
| `SAT_WMS_WMS_TITLE` | `Nordsat VIIRS WMS` | Service title in GetCapabilities |
| `SAT_WMS_GRANULE_INTERVAL` | `10m` | Granule repeat cycle (`5m`, `10m`, `15m`, `1h`, …) |
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
export SAT_WMS_WMS_TITLE="My VIIRS WMS"
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
