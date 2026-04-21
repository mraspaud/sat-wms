"""Disk-based tile cache for WMTS rendered tiles."""
import time
from pathlib import Path


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
            time_bucket: str, ext: str, short_ttl_seconds: int | None = None) -> bytes | None:
        """Return cached tile bytes or None (cache miss, expired, or disabled).

        short_ttl_seconds: if provided, also expire when file age exceeds this value.
        Use this for the latest time-bucket so it re-renders after one interval.
        """
        if not self._dir:
            return None
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        effective_ttl = self._ttl_seconds
        if short_ttl_seconds is not None:
            effective_ttl = min(effective_ttl, short_ttl_seconds)
        if age > effective_ttl:
            p.unlink(missing_ok=True)
            return None
        return p.read_bytes()

    def put(self, layer: str, tms_id: str, z: int, y: int, x: int,
            time_bucket: str, ext: str, data: bytes) -> None:
        """Write tile bytes to disk."""
        if not self._dir:
            return
        p = self._path(layer, tms_id, z, y, x, time_bucket, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def _latest_path(self, layer: str, tms_id: str) -> Path:
        return Path(self._dir) / layer / tms_id / "_latest.txt"

    def get_previous_latest(self, layer: str, tms_id: str) -> str | None:
        """Return the time_bucket string stored by the last set_latest call, or None."""
        if not self._dir:
            return None
        p = self._latest_path(layer, tms_id)
        return p.read_text().strip() if p.exists() else None

    def set_latest(self, layer: str, tms_id: str, time_bucket: str) -> None:
        """Record the current 'latest' time_bucket atomically."""
        if not self._dir:
            return
        p = self._latest_path(layer, tms_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(time_bucket)
        tmp.replace(p)

    def link_tile(self, layer: str, tms_id: str, z: int, y: int, x: int,
                  old_bucket: str, new_bucket: str, ext: str) -> bool:
        """Create a hard link from old_bucket tile to new_bucket. Returns True on success.

        Both paths remain accessible and share the same on-disk data (no duplication).
        """
        if not self._dir:
            return False
        src = self._path(layer, tms_id, z, y, x, old_bucket, ext)
        if not src.exists():
            return False
        dst = self._path(layer, tms_id, z, y, x, new_bucket, ext)
        dst.parent.mkdir(parents=True, exist_ok=True)
        import os  # noqa: PLC0415
        os.link(src, dst)
        return True
