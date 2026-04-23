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


def test_tile_cache_rejects_path_traversal_in_layer(tmp_path):
    """get() returns None and put() is a no-op when layer contains path traversal sequences."""
    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("../../etc/passwd", "tms", 0, 0, 0, "bucket", "png", b"data")
    assert cache.get("../../etc/passwd", "tms", 0, 0, 0, "bucket", "png") is None
    assert not any(tmp_path.iterdir())


def test_tile_cache_get_returns_none_if_file_disappears(tmp_path):
    """get() returns None if the file is deleted between exists-check and read (concurrent eviction)."""
    from unittest.mock import patch

    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", b"\x89PNG")

    with patch("sat_wms.tile_cache.Path.read_bytes", side_effect=FileNotFoundError):
        result = cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png")

    assert result is None


def test_tile_cache_evicts_least_recently_used_when_over_limit(tmp_path):
    """put() evicts the least-recently-used tile when the cache exceeds max_size_mb."""
    import time as _time

    from sat_wms.tile_cache import TileCache

    data = b"x" * 500_000  # 0.5 MB each

    cache = TileCache(cache_dir=str(tmp_path), max_size_mb=1)
    cache.put("layer", "tms", 0, 0, 0, "bucket0", "webp", data)
    _time.sleep(0.02)
    cache.put("layer", "tms", 0, 0, 1, "bucket1", "webp", data)
    _time.sleep(0.02)
    # Access tile 0 to make it more recently used than tile 1
    cache.get("layer", "tms", 0, 0, 0, "bucket0", "webp")
    _time.sleep(0.02)
    # This third tile pushes total over 1 MB; tile 1 is LRU and should be evicted
    cache.put("layer", "tms", 0, 0, 2, "bucket2", "webp", data)

    assert cache.get("layer", "tms", 0, 0, 0, "bucket0", "webp") is not None, "recently used tile should survive"
    assert cache.get("layer", "tms", 0, 0, 1, "bucket1", "webp") is None, "LRU tile should be evicted"
    assert cache.get("layer", "tms", 0, 0, 2, "bucket2", "webp") is not None, "newest tile should survive"


def test_tile_cache_no_eviction_when_under_limit(tmp_path):
    """put() does not evict any tiles when total cache size is under max_size_mb."""
    from sat_wms.tile_cache import TileCache

    data = b"x" * 100  # tiny — well under any limit
    cache = TileCache(cache_dir=str(tmp_path), max_size_mb=10)
    cache.put("layer", "tms", 0, 0, 0, "bucket0", "webp", data)
    cache.put("layer", "tms", 0, 0, 1, "bucket1", "webp", data)

    assert cache.get("layer", "tms", 0, 0, 0, "bucket0", "webp") == data
    assert cache.get("layer", "tms", 0, 0, 1, "bucket1", "webp") == data


def test_tile_cache_zero_max_size_never_evicts(tmp_path):
    """max_size_mb=0 (default) disables eviction regardless of cache size."""
    from sat_wms.tile_cache import TileCache

    data = b"x" * 500_000
    cache = TileCache(cache_dir=str(tmp_path), max_size_mb=0)
    cache.put("layer", "tms", 0, 0, 0, "bucket0", "webp", data)
    cache.put("layer", "tms", 0, 0, 1, "bucket1", "webp", data)
    cache.put("layer", "tms", 0, 0, 2, "bucket2", "webp", data)

    assert cache.get("layer", "tms", 0, 0, 0, "bucket0", "webp") == data
    assert cache.get("layer", "tms", 0, 0, 1, "bucket1", "webp") == data
    assert cache.get("layer", "tms", 0, 0, 2, "bucket2", "webp") == data


def test_tile_cache_put_is_atomic(tmp_path):
    """Concurrent put() calls never leave a partial file visible to get()."""
    import threading

    from sat_wms.tile_cache import TileCache

    cache = TileCache(cache_dir=str(tmp_path))
    data = b"\x89PNG" * 1000
    errors = []

    def writer():
        cache.put("layer", "tms", 3, 4, 5, "2026T1000", "png", data)

    def reader():
        result = cache.get("layer", "tms", 3, 4, 5, "2026T1000", "png")
        if result is not None and result != data:
            errors.append(f"Partial read: got {len(result)} bytes, expected {len(data)}")

    threads = [threading.Thread(target=writer) for _ in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors[0]
