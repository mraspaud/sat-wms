"""Tests for the PostGIS-backed metadata repository."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sat_wms.pg_mda import PooledMetadataRepository


def _make_pool(fetchall_return):
    """Build a mock psycopg pool whose cursor returns fetchall_return."""
    cur = AsyncMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(return_value=fetchall_return)
    cur.fetchone = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _cursor():
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor

    @asynccontextmanager
    async def _connection():
        yield conn

    pool = MagicMock()
    pool.connection = _connection
    return pool, cur


@pytest.mark.asyncio
async def test_get_map_assets_passes_src_srid_to_query():
    """The SQL params dict must include src_srid so the named placeholder resolves."""
    pool, cur = _make_pool([])
    mda = PooledMetadataRepository(pool)
    await mda.get_map_assets(
        "true_color_day",
        [-1e6, -1e6, 1e6, 1e6],
        datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc),
        src_srid=32661,
    )
    _sql, params = cur.execute.call_args.args
    assert "src_srid" in params
    assert params["src_srid"] == 32661


@pytest.mark.asyncio
async def test_get_map_assets_returns_dicts_with_expected_keys():
    """Each returned asset has filename, bbox (4-tuple of float), and bbox_srid (int)."""
    fake_rows = [
        {"filename": "/data/foo.tif", "granule_bbox": "BOX(0 1, 2 3)", "bbox_srid": 32661},
    ]
    pool, _cur = _make_pool(fake_rows)
    mda = PooledMetadataRepository(pool)
    assets = await mda.get_map_assets(
        "true_color_day",
        [-1e6, -1e6, 1e6, 1e6],
        datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc),
        src_srid=32661,
    )
    assert len(assets) == 1
    a = assets[0]
    assert a["filename"] == "/data/foo.tif"
    assert len(a["bbox"]) == 4
    assert all(isinstance(v, float) for v in a["bbox"])
    assert isinstance(a["bbox_srid"], int)
    assert a["bbox_srid"] == 32661
