"""Application configuration."""
from donfig import Config

config = Config("sat_wms", defaults=[{
    "database_url": "postgresql://user:pass@localhost/viirs_db",
    "base_url": "http://localhost:8000",
    "wms_title": "Nordsat VIIRS WMS",
    "supported_crs": ["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"],
    "granule_interval": "5m",
    "force_webp": False,
    "empty_no_content": False,
    # Number of (filepath, bbox, crs, width, height) combinations to keep in the
    # in-process tile read cache. Each entry holds a NumPy masked array; at
    # 512×512 RGBA that's roughly 1 MB per entry, so 1024 ≈ 1 GB.
    "tile_cache_entries": 128,
}])
