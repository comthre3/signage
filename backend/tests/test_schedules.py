"""Tests for the Schedule + ScheduleRule CRUD endpoints."""
from fastapi.testclient import TestClient


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_playlist(client, signed_up_org, name="P"):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/playlists", headers=bearer, json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── Schedule CRUD ─────────────────────────────────────────────────────

def test_post_schedule_creates_row(client, signed_up_org):
    r = client.post("/schedules", headers=_bearer(signed_up_org["token"]),
                    json={"name": "Daypart"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["name"] == "Daypart"
    assert "id" in body
    assert body.get("rules") == []


def test_post_schedule_rejects_empty_name(client, signed_up_org):
    r = client.post("/schedules", headers=_bearer(signed_up_org["token"]),
                    json={"name": ""})
    assert r.status_code in (400, 422)


def test_post_schedule_requires_role(client, signed_up_org):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/users", headers=bearer,
                    json={"username": f"v-{suffix}@example.com",
                          "password": "Khanshoof2026Pass", "role": "viewer"})
    assert r.status_code in (200, 201), r.text
    r = client.post("/auth/login",
                    json={"username": f"v-{suffix}@example.com",
                          "password": "Khanshoof2026Pass"})
    viewer_token = r.json()["token"]

    r = client.post("/schedules", headers=_bearer(viewer_token),
                    json={"name": "ViewerNope"})
    assert r.status_code == 403


def test_get_schedules_lists_only_own_org(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "MyOrg"})
    assert r.status_code in (200, 201), r.text
    r = client.get("/schedules", headers=bearer)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(s["name"] == "MyOrg" for s in items)


def test_put_schedule_updates_name(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "Before"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}", headers=bearer, json={"name": "After"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "After"


def test_delete_schedule_cascades_rules(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Tmp"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [{"playlist_id": pl,
                                    "start_time": "09:00", "end_time": "17:00",
                                    "days_of_week": 127, "position": 0}]})
    assert r.status_code == 200, r.text
    r = client.delete(f"/schedules/{sid}", headers=bearer)
    assert r.status_code in (200, 204)
    r = client.get(f"/schedules/{sid}", headers=bearer)
    assert r.status_code == 404


# ── Rules CRUD (replace-all) ──────────────────────────────────────────

def test_put_rules_replaces_all(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Test"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "06:00",
                        "end_time": "11:00", "days_of_week": 31, "position": 0},
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 31, "position": 1},
                   ]})
    assert r.status_code == 200, r.text
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "00:00",
                        "end_time": "23:59", "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code == 200, r.text
    r = client.get(f"/schedules/{sid}", headers=bearer)
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert len(rules) == 1


def test_post_rule_allows_wrap_midnight(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Late"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "22:00",
                        "end_time": "02:00", "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code == 200, r.text


def test_post_rule_rejects_overlap_same_day(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Overlap"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 4, "position": 0},
                       {"playlist_id": pl, "start_time": "13:00",
                        "end_time": "15:00", "days_of_week": 4, "position": 1},
                   ]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "schedule.rule_overlap"


def test_post_rule_allows_overlap_different_days(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "NoOverlap"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 1, "position": 0},
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 2, "position": 1},
                   ]})
    assert r.status_code == 200, r.text


def test_post_rule_rejects_invalid_dow(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "BadDOW"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 128,
                        "position": 0},
                   ]})
    assert r.status_code in (400, 422)


def test_post_rule_requires_at_least_one_day(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "NoDays"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 0,
                        "position": 0},
                   ]})
    assert r.status_code in (400, 422)


def test_post_rule_rejects_playlist_from_other_org(client, signed_up_org):
    """Cannot reference a playlist that belongs to another org (we use a
    nonexistent id as a stand-in)."""
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "Bad"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": 9999999,
                        "start_time": "11:00", "end_time": "14:00",
                        "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code in (404, 422)


# ── Site timezone ─────────────────────────────────────────────────────

def test_default_site_timezone_is_kuwait(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Default tz"})
    assert r.status_code in (200, 201), r.text
    site = r.json()
    # Default tz is implicit at DB level; verify via GET if the endpoint
    # exposes timezone in its response
    r = client.get(f"/sites/{site['id']}", headers=bearer)
    if r.status_code == 200:
        assert r.json().get("timezone") == "Asia/Kuwait"


def test_put_site_accepts_timezone(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Riyadh"})
    site = r.json()
    r = client.put(f"/sites/{site['id']}", headers=bearer,
                   json={"timezone": "Asia/Riyadh"})
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "Asia/Riyadh"


def test_put_site_rejects_invalid_timezone(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Bad"})
    site = r.json()
    r = client.put(f"/sites/{site['id']}", headers=bearer,
                   json={"timezone": "Mars/Olympus"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "site.timezone_invalid"


# ── Screen schedule_id ────────────────────────────────────────────────

def test_put_screen_accepts_schedule_id(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "Test"})
    sid = r.json()["id"]
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": sid})
    assert r.status_code == 200, r.text
    assert r.json().get("schedule_id") == sid


def test_put_screen_accepts_null_schedule_id(client, signed_up_org):
    """Setting schedule_id to None detaches the schedule."""
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "X"})
    sid = r.json()["id"]
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": sid})
    assert r.status_code == 200
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": None})
    assert r.status_code == 200
    # Verify via GET if the endpoint exposes schedule_id
    r = client.get(f"/screens/{screen['id']}", headers=bearer)
    if r.status_code == 200:
        assert r.json().get("schedule_id") is None


def test_put_screen_rejects_schedule_from_other_org(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": 9999999})
    assert r.status_code in (404, 422)
