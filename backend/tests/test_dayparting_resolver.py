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


# ── Integration: schedule drives /content endpoint ────────────────────
import io
import pytest


def _create_two_playlists_and_screen(client, signed_up_org):
    """Helper: create 2 playlists with media, a screen, return ids/token."""
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}

    def upload(name):
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
        r = client.post(
            "/media/upload",
            headers=bearer,
            files={"file": (name, io.BytesIO(png), "image/png")},
        )
        assert r.status_code == 200, r.text
        return r.json()

    media_a = upload("dp_a.png")
    media_b = upload("dp_b.png")

    def make_playlist(name, mid):
        r = client.post("/playlists", headers=bearer, json={"name": name})
        assert r.status_code == 200, r.text
        pl = r.json()
        r = client.post(
            f"/playlists/{pl['id']}/items",
            headers=bearer,
            json={"media_id": mid},
        )
        assert r.status_code == 200, r.text
        return pl["id"]

    pl_a = make_playlist("Default", media_a["id"])
    pl_b = make_playlist("Scheduled", media_b["id"])

    r = client.post("/screens", headers=bearer, json={"name": "S"})
    assert r.status_code == 200, r.text
    screen = r.json()

    r = client.put(
        f"/screens/{screen['id']}",
        headers=bearer,
        json={"playlist_id": pl_a},
    )
    assert r.status_code == 200, r.text

    return {
        "token": screen["token"],
        "screen_id": screen["id"],
        "playlist_a": pl_a,
        "playlist_b": pl_b,
    }


def test_content_endpoint_uses_resolver_with_no_schedule(client, signed_up_org):
    """No schedule attached → /content returns the default playlist's items."""
    info = _create_two_playlists_and_screen(client, signed_up_org)
    r = client.get(f"/screens/{info['token']}/content")
    assert r.status_code == 200
    body = r.json()
    assert body["playlist"]["id"] == info["playlist_a"]


@pytest.mark.skip(reason="depends on Task 4+5 — schedule CRUD + PUT /screens schedule_id")
def test_content_endpoint_picks_scheduled_playlist(client, signed_up_org):
    """A schedule with a rule covering all hours of all days → /content
    returns the scheduled playlist, not the default. Un-skipped in Task 5."""
    info = _create_two_playlists_and_screen(client, signed_up_org)
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}

    r = client.post("/schedules", headers=bearer, json={"name": "AlwaysOn"})
    assert r.status_code in (200, 201), r.text
    sched_id = r.json()["id"]
    r = client.put(
        f"/schedules/{sched_id}/rules",
        headers=bearer,
        json={
            "rules": [
                {
                    "playlist_id": info["playlist_b"],
                    "start_time": "00:00",
                    "end_time": "23:59",
                    "days_of_week": 127,  # all days
                    "position": 0,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = client.put(
        f"/screens/{info['screen_id']}",
        headers=bearer,
        json={"schedule_id": sched_id},
    )
    assert r.status_code == 200, r.text

    r = client.get(f"/screens/{info['token']}/content")
    assert r.status_code == 200
    body = r.json()
    assert body["playlist"]["id"] == info["playlist_b"], \
        f"expected scheduled playlist, got {body['playlist']}"
