"""Application configuration."""
from donfig import Config

config = Config("sat_wms", defaults=[{
    "database_url": "postgresql://user:pass@localhost/viirs_db",
    "base_url": "http://localhost:8000",
    "wms_title": "Nordsat VIIRS WMS",
    "supported_crs": ["EPSG:3575", "EPSG:3857", "EPSG:5041", "EPSG:4326"],
    "max_granules": 10,
}])
