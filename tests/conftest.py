"""Shared test fixtures."""
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds

from sat_wms.local_mda import LocalMetadataRepository


@pytest.fixture
def local_mda():
    """LocalMetadataRepository loaded from the test CSV."""
    return LocalMetadataRepository("data/test_data.csv")


@pytest.fixture
def test_tif(tmp_path):
    """Minimal 10×10 RGBA GeoTIFF in EPSG:3575 covering the first CSV granule bbox."""
    data = np.zeros((4, 10, 10), dtype=np.uint8)
    data[:3, :, :] = 128
    data[3, :, :] = 255
    transform = from_bounds(-1320000, -2781000, 569250, 245250, 10, 10)
    path = tmp_path / "test.tif"
    with rasterio.open(
        path, "w", driver="GTiff",
        height=10, width=10, count=4, dtype=np.uint8,
        crs=CRS.from_epsg(3575), transform=transform,
    ) as dst:
        dst.write(data)
    return str(path)


@pytest.fixture
def synth_mda(test_tif):
    """Stub MDA that always returns the synthetic test GeoTIFF."""
    class SyntheticMDA:
        def __init__(self, path):
            self.path = path

        async def get_latest_time(self, layer_name):
            return None

        async def get_map_assets(self, *args, **kwargs):
            return [{"filename": self.path, "bbox": (-1320000.0, -2781000.0, 569250.0, 245250.0), "bbox_srid": 3575}]

    return SyntheticMDA(test_tif)
