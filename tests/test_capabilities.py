"""Tests for GetCapabilities."""
import pytest


@pytest.mark.asyncio
async def test_generate_capabilities(local_mda):
    """GetCapabilities response is valid WMS 1.3.0 XML."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda)
    assert res.body.decode().startswith("<?xml")
