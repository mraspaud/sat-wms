"""Application configuration."""
from donfig import Config

config = Config("sat_wms", defaults=[{
    "database_url": "postgresql://user:pass@localhost/viirs_db",
    "base_url": "http://localhost:8000",
    "wms_title": "Sat-WMS",
    "supported_crs": ["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"],
    "granule_interval": "5m",
    "force_webp": False,
    "empty_no_content": False,
    "wmts_max_zoom": 9,
    # Number of (filepath, bbox, crs, width, height) combinations to keep in the
    # in-process tile read cache. Each entry holds a NumPy masked array; at
    # 512×512 RGBA that's roughly 1 MB per entry, so 1024 ≈ 1 GB.
    "tile_cache_entries": 128,
    "products_table_name": "public.products_sat",
    # Disk tile cache: set tile_cache_dir to a writable path to enable.
    "tile_cache_dir": "",
    "tile_cache_ttl_days": 7,
    "timestep_mode": "interval",
    "snapshot_step": "24h",
    "snapshot_count": 7,
    "layer_name_prefix": "",
}])

_DEFAULT_CRS = ["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"]


def get_supported_crs() -> list[str]:
    """Return the configured CRS list, accepting both YAML lists and comma-separated env-var strings."""
    value = config.get("supported_crs")
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value) if value else _DEFAULT_CRS
