"""Tests for GetCapabilities."""
import pytest


@pytest.mark.asyncio
async def test_generate_capabilities(local_mda):
    """GetCapabilities response is valid WMS 1.3.0 XML."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda)
    assert res.body.decode().startswith("<?xml")


@pytest.mark.asyncio
async def test_capabilities_dimension_uses_configured_interval(local_mda):
    """The Dimension step and floor/ceil reflect the supplied interval."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda, interval_min=5)
    xml = res.body.decode()
    assert "PT5M" in xml


@pytest.mark.asyncio
async def test_capabilities_default_is_raw_latest_time(local_mda):
    """The Dimension default is the raw latest granule time (not ceiled) for cache busting."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda)
    xml = res.body.decode()
    # raw end_time in test data is 05:34:29 — must appear as default
    assert 'default="2026-03-24T05:34:29Z"' in xml
    # range endpoint must be ceiled (05:40:00) so PT10M grid is valid
    assert "05:40:00Z/PT10M" in xml
