"""Disk-based tile cache for WMTS rendered tiles."""
import os
import re
import tempfile
import time
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


def _is_safe(*names: str) -> bool:
    """Return True if every name matches the safe-name allowlist (alphanumerics, _, -)."""
    return all(_SAFE_NAME.match(n) for n in names)


class TileCache:
    """Cache rendered WMTS tiles to disk.

    Disabled when cache_dir is empty.  When max_size_mb > 0, the cache evicts the
    least-recently-used tiles (by mtime) to stay within the size limit.
    """

    def __init__(self, cache_dir: str, ttl_days: int = 7, max_size_mb: int = 0):
        """Initialise with a cache directory, TTL in days, and optional size cap in MB."""
        self._dir = cache_dir
        self._ttl_seconds = ttl_days * 86400
        self._max_size_bytes = max_size_mb * 1024 * 1024

    def _path(self, layer: str, tms_id: str, z: int, y: int, x: int,
               time_bucket: str, ext: str) -> Path:
        return Path(self._dir) / layer / tms_id / str(z) / str(y) / f"{x}_{time_bucket}.{ext}"

    def get(self, layer: str, tms_id: str, z: int, y: int, x: int,
            time_bucket: str, ext: str) -> bytes | None:
        """Return cached tile bytes or None (cache miss, expired, disabled, or unsafe name).

        Updates mtime on a hit so that eviction uses true last-access order (LRU).
        """
        if not self._dir or not _is_safe(layer, tms_id):
            return None
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        if age > self._ttl_seconds:
            p.unlink(missing_ok=True)
            return None
        try:
            data = p.read_bytes()
            p.touch()
            return data
        except FileNotFoundError:
            return None

    def put(self, layer: str, tms_id: str, z: int, y: int, x: int,
            time_bucket: str, ext: str, data: bytes) -> None:
        """Write tile bytes to disk atomically, then evict LRU tiles if over the size cap."""
        if not self._dir or not _is_safe(layer, tms_id):
            return
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            os.write(fd, data)
            os.close(fd)
            os.replace(tmp, p)
        except Exception:
            os.close(fd)
            Path(tmp).unlink(missing_ok=True)
            raise
        if self._max_size_bytes:
            self._evict()

    def _evict(self) -> None:
        """Delete least-recently-used tiles until total cache size is under the cap."""
        root = Path(self._dir)
        entries = []
        total = 0
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix == ".tmp":
                continue
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            entries.append((st.st_mtime, st.st_size, f))
            total += st.st_size

        if total <= self._max_size_bytes:
            return

        entries.sort()  # oldest mtime first
        for _mtime, size, f in entries:
            if total <= self._max_size_bytes:
                break
            f.unlink(missing_ok=True)
            total -= size


