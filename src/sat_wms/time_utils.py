"""Time utility functions."""
from datetime import datetime, timedelta


def floor_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Floor datetime to the nearest grid interval (e.g., 12:08 -> 12:00)."""
    return dt.replace(minute=(dt.minute // interval_min) * interval_min,
                      second=0, microsecond=0)


def ceil_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Ceil datetime to the next grid interval (e.g., 12:05 -> 12:10, 12:00 -> 12:00)."""
    floored = floor_dt(dt, interval_min)
    if dt.minute % interval_min == 0 and dt.second == 0 and dt.microsecond == 0:
        return floored
    return floored + timedelta(minutes=interval_min)
