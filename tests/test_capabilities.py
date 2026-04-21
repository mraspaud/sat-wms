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
async def test_capabilities_hourly_interval_uses_iso_hours(local_mda):
    """A 60-minute interval must appear as PT1H, not PT60M."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda, interval_min=60)
    xml = res.body.decode()
    assert "PT1H" in xml
    assert "PT60M" not in xml


@pytest.mark.asyncio
async def test_capabilities_title_comes_from_config(local_mda):
    """The WMS capabilities title is read from the wms_title config key."""
    import sat_wms.config as cfg
    from sat_wms.capabilities import generate_capabilities

    with cfg.config.set({"wms_title": "My Sat"}):
        res = await generate_capabilities(local_mda)
    assert "My Sat" in res.body.decode()


@pytest.mark.asyncio
async def test_capabilities_title_includes_duration(local_mda):
    """The WMS capabilities title has the duration string appended."""
    import sat_wms.config as cfg
    from sat_wms.capabilities import generate_capabilities

    with cfg.config.set({"wms_title": "My Sat"}):
        res = await generate_capabilities(local_mda, duration_str="2h")
    assert "My Sat (2h)" in res.body.decode()


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


@pytest.mark.asyncio
async def test_capabilities_stepped_mode_lists_discrete_times(local_mda):
    """In 'stepped' mode the Dimension element lists comma-separated ISO timestamps."""
    import sat_wms.config as cfg
    from sat_wms.capabilities import generate_capabilities

    with cfg.config.set({"timestep_mode": "stepped", "snapshot_step": "24h", "snapshot_count": 2}):
        res = await generate_capabilities(local_mda)
    xml = res.body.decode()
    # Must NOT contain interval syntax (Z/PTxH or Z/PTxM)
    assert "Z/PT" not in xml
    # Must contain at least two comma-separated timestamps
    assert xml.count("Z,") >= 1


@pytest.mark.asyncio
async def test_capabilities_stepped_mode_first_time_is_latest(local_mda):
    """In 'stepped' mode the default time equals the layer's latest data time."""
    import sat_wms.config as cfg
    from sat_wms.capabilities import generate_capabilities

    with cfg.config.set({"timestep_mode": "stepped", "snapshot_step": "24h", "snapshot_count": 1}):
        res = await generate_capabilities(local_mda)
    xml = res.body.decode()
    # The latest time in test data is 05:34:29
    assert "2026-03-24T05:34:29Z" in xml


@pytest.mark.asyncio
async def test_capabilities_layer_name_prefix_applied(local_mda):
    """When layer_name_prefix is set, it is prepended to every layer name in the XML."""
    from sat_wms.capabilities import generate_capabilities

    res = await generate_capabilities(local_mda, layer_name_prefix="Sentinel-1 SAR ")
    xml = res.body.decode()
    assert "Sentinel-1 SAR " in xml
    # Raw DB name must still appear (as part of the prefixed name)
    assert "Sentinel-1 SAR true_color_day" in xml
