"""Time utility functions."""
import re
from datetime import datetime, timedelta

_DURATION_RE = re.compile(r"(\d+)(m|h|d)")


def parse_duration(s: str) -> timedelta:
    """Parse a duration string like '30m', '2h', '1d' into a timedelta.

    Falls back to 1 hour for unrecognised formats.
    """
    m = _DURATION_RE.fullmatch(s)
    if not m:
        return timedelta(hours=1)
    value, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]


def parse_interval_min(s: str) -> int:
    """Parse a human-readable interval string into minutes (e.g. '5m' → 5, '1h' → 60)."""
    m = re.fullmatch(r"(\d+)(m|h)", s)
    if not m:
        msg = f"Invalid interval {s!r}; expected format like '10m' or '1h'."
        raise ValueError(msg)
    value, unit = int(m.group(1)), m.group(2)
    return value if unit == "m" else value * 60


def _to_iso_duration(interval_min: int) -> str:
    """Return an ISO 8601 duration string for an interval given in minutes.

    Outputs canonical form: PT1H for 60 minutes, PT2H for 120, PT5M for 5, etc.
    """
    if interval_min % 60 == 0:
        return f"PT{interval_min // 60}H"
    return f"PT{interval_min}M"


def floor_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Floor datetime to the nearest grid interval (e.g., 12:08 -> 12:00)."""
    return dt.replace(minute=(dt.minute // interval_min) * interval_min,
                      second=0, microsecond=0)


def compute_snapshot_times(latest: datetime, step: timedelta, count: int) -> list[datetime]:
    """Return sorted list of snapshot times: midnight-based historical steps + latest.

    Historical entries snap to midnight UTC: today's midnight, today's-step, …, today's-(count-1)*step.
    The result is sorted ascending so the most recent time is last.  Duplicate entries (e.g. when
    ``latest`` falls exactly on a midnight boundary) are removed.
    """
    midnight = latest.replace(hour=0, minute=0, second=0, microsecond=0)
    snapshots = {midnight - i * step for i in range(count)}
    return sorted(snapshots | {latest})


def ceil_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Ceil datetime to the next grid interval (e.g., 12:05 -> 12:10, 12:00 -> 12:00)."""
    floored = floor_dt(dt, interval_min)
    if dt.minute % interval_min == 0 and dt.second == 0 and dt.microsecond == 0:
        return floored
    return floored + timedelta(minutes=interval_min)
