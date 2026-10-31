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


def test_site_timezone_falls_back_for_malformed_stored_key(client, signed_up_org):
    """A bad value already in the row (edited by hand, say) must not take
    every screen at the site down: ZoneInfo raises ValueError for "Asia/"."""
    from db import execute
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}
    r = client.post("/sites", headers=bearer, json={"name": "Hand-edited"})
    assert r.status_code == 200, r.text
    site_id = r.json()["id"]
    execute("UPDATE sites SET timezone = ? WHERE id = ?", ("Asia/", site_id))
    assert _site_timezone(site_id) == ZoneInfo("Asia/Kuwait")


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


# ── Wrap-midnight rules belong to the day they START on ───────────────
# 2026-10-26 is a Monday, so 2026-10-30 is a Friday and 2026-10-31 a Saturday.
_KUWAIT = ZoneInfo("Asia/Kuwait")
_FRI_BIT, _SUN_BIT = 1 << 4, 1 << 6


def _resolve_at(screen, when):
    """resolve_active_playlist as if the site's wall clock read `when`."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when
    with patch("main.datetime", _Frozen):
        return resolve_active_playlist(screen)


def _late_night_schedule(client, signed_up_org, days_of_week):
    """A schedule whose only rule is 22:00-02:00 on `days_of_week`.

    Returns (screen dict for the resolver, the rule's playlist id). The
    screen's own default playlist is a different one, so a miss is visible.
    """
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}
    ids = []
    for name in ("Default", "Late"):
        r = client.post("/playlists", headers=bearer, json={"name": name})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    default_pl, late_pl = ids
    r = client.post("/schedules", headers=bearer, json={"name": "Late show"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer, json={"rules": [
        {"playlist_id": late_pl, "start_time": "22:00", "end_time": "02:00",
         "days_of_week": days_of_week, "position": 0},
    ]})
    assert r.status_code == 200, r.text
    return {"schedule_id": sid, "playlist_id": default_pl, "site_id": None}, late_pl


def test_wrap_rule_plays_its_own_evening(client, signed_up_org):
    screen, late = _late_night_schedule(client, signed_up_org, _FRI_BIT)
    assert _resolve_at(screen, datetime(2026, 10, 30, 23, 0, tzinfo=_KUWAIT)) == late


def test_wrap_rule_runs_past_midnight_into_the_next_day(client, signed_up_org):
    """Friday 22:00-02:00 is Friday night: it is still on at Saturday 01:00."""
    screen, late = _late_night_schedule(client, signed_up_org, _FRI_BIT)
    assert _resolve_at(screen, datetime(2026, 10, 31, 1, 0, tzinfo=_KUWAIT)) == late


def test_wrap_rule_does_not_play_early_on_its_own_day(client, signed_up_org):
    """Friday 01:00 is the tail of THURSDAY night, which has no rule."""
    screen, late = _late_night_schedule(client, signed_up_org, _FRI_BIT)
    got = _resolve_at(screen, datetime(2026, 10, 30, 1, 0, tzinfo=_KUWAIT))
    assert got == screen["playlist_id"]


def test_wrap_rule_stops_at_end_time_next_day(client, signed_up_org):
    screen, late = _late_night_schedule(client, signed_up_org, _FRI_BIT)
    got = _resolve_at(screen, datetime(2026, 10, 31, 2, 0, tzinfo=_KUWAIT))
    assert got == screen["playlist_id"]


def test_sunday_wrap_rule_runs_into_monday(client, signed_up_org):
    """The day after Sunday (bit 6) is Monday (bit 0), not bit 7."""
    screen, late = _late_night_schedule(client, signed_up_org, _SUN_BIT)
    assert _resolve_at(screen, datetime(2026, 10, 26, 1, 0, tzinfo=_KUWAIT)) == late
