"""Disk-based tile cache for WMTS rendered tiles."""
import re
import time
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


def _is_safe(*names: str) -> bool:
    """Return True if every name matches the safe-name allowlist (alphanumerics, _, -)."""
    return all(_SAFE_NAME.match(n) for n in names)


class TileCache:
    """Cache rendered WMTS tiles to disk.

    Disabled when cache_dir is empty.
    """

    def __init__(self, cache_dir: str, ttl_days: int = 7):
        """Initialise with a cache directory and TTL in days."""
        self._dir = cache_dir
        self._ttl_seconds = ttl_days * 86400

    def _path(self, layer: str, tms_id: str, z: int, y: int, x: int,
               time_bucket: str, ext: str) -> Path:
        return Path(self._dir) / layer / tms_id / str(z) / str(y) / f"{x}_{time_bucket}.{ext}"

    def get(self, layer: str, tms_id: str, z: int, y: int, x: int,
            time_bucket: str, ext: str) -> bytes | None:
        """Return cached tile bytes or None (cache miss, expired, disabled, or unsafe name)."""
        if not self._dir or not _is_safe(layer, tms_id):
            return None
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        if age > self._ttl_seconds:
            p.unlink(missing_ok=True)
            return None
        return p.read_bytes()

    def put(self, layer: str, tms_id: str, z: int, y: int, x: int,
            time_bucket: str, ext: str, data: bytes) -> None:
        """Write tile bytes to disk, silently ignoring unsafe layer or TMS names."""
        if not self._dir or not _is_safe(layer, tms_id):
            return
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

