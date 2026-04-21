"""Tests for time utility functions."""
from datetime import datetime, timedelta, timezone

from sat_wms.time_utils import compute_snapshot_times


def test_compute_snapshot_times_sorted_ascending():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    assert times == sorted(times)


def test_compute_snapshot_times_last_entry_is_latest():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    assert times[-1] == latest


def test_compute_snapshot_times_historical_entries_at_midnight():
    # Non-latest entries must be snapped to midnight UTC, not offset from latest.
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    for t in times[:-1]:
        assert t.hour == 0
        assert t.minute == 0
        assert t.second == 0


def test_compute_snapshot_times_count_determines_midnight_snapshots():
    latest = datetime(2026, 4, 21, 10, 32, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    # 3 midnight snapshots (today, yesterday, day-before) + 1 latest = 4
    assert len(times) == 4
    assert times[0] == datetime(2026, 4, 19, 0, 0, tzinfo=timezone.utc)
    assert times[1] == datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    assert times[2] == datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)


def test_compute_snapshot_times_latest_at_midnight_no_duplicate():
    # When latest is exactly at midnight, today's snapshot and latest coincide → no duplicate.
    latest = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=24), count=3)
    assert len(times) == 3
    assert times[-1] == latest


def test_compute_snapshot_times_12h_step():
    latest = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
    times = compute_snapshot_times(latest, timedelta(hours=12), count=3)
    # midnight today, midnight-12h, midnight-24h, then latest
    assert times[0] == datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    assert times[1] == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    assert times[2] == datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    assert times[3] == latest
