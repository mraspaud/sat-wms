"""Tests for time utility functions."""
from datetime import datetime, timedelta, timezone

from sat_wms.time_utils import compute_snapshot_times


def test_compute_snapshot_times_first_entry_is_latest():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    assert times[0] == latest


def test_compute_snapshot_times_count_plus_one_entries():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    assert len(times) == 4  # latest + 3 snapshots


def test_compute_snapshot_times_steps_back_by_step():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    step = timedelta(hours=24)
    times = compute_snapshot_times(latest, step, count=3)
    assert times[1] == latest - step
    assert times[2] == latest - 2 * step
    assert times[3] == latest - 3 * step


def test_compute_snapshot_times_custom_step():
    latest = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=12), count=2)
    assert times[1] == datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    assert times[2] == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
