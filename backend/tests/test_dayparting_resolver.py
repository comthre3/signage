"""Unit tests for the dayparting resolver helpers."""
from datetime import time, datetime, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from main import _time_in_window, _site_timezone, resolve_active_playlist


# ── _time_in_window ───────────────────────────────────────────────────

def test_normal_window_inclusive_start():
    assert _time_in_window(time(11, 0), time(11, 0), time(14, 0)) is True


def test_normal_window_exclusive_end():
    assert _time_in_window(time(14, 0), time(11, 0), time(14, 0)) is False


def test_normal_window_middle_match():
    assert _time_in_window(time(12, 30), time(11, 0), time(14, 0)) is True


def test_normal_window_before_start():
    assert _time_in_window(time(10, 59), time(11, 0), time(14, 0)) is False


def test_wrap_midnight_after_start():
    # 22:00–02:00 — 23:30 is inside the late-night window
    assert _time_in_window(time(23, 30), time(22, 0), time(2, 0)) is True


def test_wrap_midnight_before_end():
    # 22:00–02:00 — 01:30 is inside (early-morning leg)
    assert _time_in_window(time(1, 30), time(22, 0), time(2, 0)) is True


def test_wrap_midnight_outside():
    # 22:00–02:00 — 10:00 is OUTSIDE
    assert _time_in_window(time(10, 0), time(22, 0), time(2, 0)) is False


# ── _site_timezone ────────────────────────────────────────────────────

def test_site_timezone_returns_kuwait_for_no_site():
    tz = _site_timezone(None)
    assert tz == ZoneInfo("Asia/Kuwait")


def test_site_timezone_returns_kuwait_for_unknown_site():
    tz = _site_timezone(9999999)  # nonexistent
    assert tz == ZoneInfo("Asia/Kuwait")


# ── resolve_active_playlist ───────────────────────────────────────────

def test_resolve_no_schedule_returns_default_playlist():
    screen = {"schedule_id": None, "playlist_id": 42, "site_id": None}
    assert resolve_active_playlist(screen) == 42


def test_resolve_no_schedule_no_default_returns_none():
    screen = {"schedule_id": None, "playlist_id": None, "site_id": None}
    assert resolve_active_playlist(screen) is None
