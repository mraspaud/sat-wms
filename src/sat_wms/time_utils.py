"""Time utility functions."""
from datetime import datetime


def floor_dt(dt: datetime, interval_min: int = 10) -> datetime:
    """Floor datetime to the nearest grid interval (e.g., 12:08 -> 12:00)."""
    return dt.replace(minute=(dt.minute // interval_min) * interval_min,
                      second=0, microsecond=0)
