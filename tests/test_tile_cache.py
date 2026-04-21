"""Tests for the disk tile cache."""


def test_tile_cache_get_returns_none_when_disabled(tmp_path):
    """get() returns None when tile_cache_dir is empty (cache disabled)."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir="")
    assert cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png") is None


def test_tile_cache_put_then_get_returns_data(tmp_path):
    """put() writes to disk; get() returns the same bytes."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", b"\x89PNG")
    assert cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png") == b"\x89PNG"


def test_tile_cache_get_returns_none_after_ttl(tmp_path):
    """get() deletes and returns None when the cached file is older than TTL."""
    import time as _time
    from unittest.mock import patch

    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path), ttl_days=1)
    cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", b"\x89PNG")

    future = _time.time() + 2 * 86400  # 2 days ahead — past TTL
    with patch("sat_wms.tile_cache.time.time", return_value=future):
        result = cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png")

    assert result is None


def test_tile_cache_latest_bucket_expired_by_short_ttl(tmp_path):
    """get() with short_ttl_seconds returns None when tile is older than that interval."""
    import time as _time
    from unittest.mock import patch

    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path), ttl_days=7)
    cache.put("layer", "tms", 3, 4, 5, "2026T1200", "png", b"\x89PNG")

    # 2 hours later — within 7-day TTL but past a 1-hour interval
    later = _time.time() + 2 * 3600
    with patch("sat_wms.tile_cache.time.time", return_value=later):
        result = cache.get("layer", "tms", 3, 4, 5, "2026T1200", "png", short_ttl_seconds=3600)

    assert result is None


def test_tile_cache_set_and_get_previous_latest(tmp_path):
    """set_latest persists the bucket string; get_previous_latest reads it back."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    assert cache.get_previous_latest("layer", "tms") is None
    cache.set_latest("layer", "tms", "2026T1032")
    assert cache.get_previous_latest("layer", "tms") == "2026T1032"


def test_tile_cache_set_latest_overwrites(tmp_path):
    """set_latest replaces the previous value atomically."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.set_latest("layer", "tms", "2026T1000")
    cache.set_latest("layer", "tms", "2026T1037")
    assert cache.get_previous_latest("layer", "tms") == "2026T1037"


def test_tile_cache_link_tile_both_paths_exist(tmp_path):
    """link_tile creates a hard link; both old and new bucket paths remain accessible."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", b"\x89PNG")
    linked = cache.link_tile("layer", "tms", 3, 4, 5, "2026T1000", "2026T1037", "png")
    assert linked is True
    assert cache.get("layer", "tms", 3, 4, 5, "2026T1037", "png") == b"\x89PNG"
    # Old path still exists (hard link, not rename)
    assert cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png") == b"\x89PNG"


def test_tile_cache_link_tile_shares_inode(tmp_path):
    """Hard-linked tiles share the same inode (no data duplication)."""
    import os

    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", b"\x89PNG")
    cache.link_tile("layer", "tms", 3, 4, 5, "2026T1000", "2026T1037", "png")

    src = tmp_path / "layer" / "tms" / "3" / "4" / "5_2026T1000.png"
    dst = tmp_path / "layer" / "tms" / "3" / "4" / "5_2026T1037.png"
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_tile_cache_link_tile_returns_false_when_missing(tmp_path):
    """link_tile returns False if the source file does not exist."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    linked = cache.link_tile("layer", "tms", 3, 4, 5, "2026T1000", "2026T1037", "png")
    assert linked is False
