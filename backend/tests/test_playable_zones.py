"""Player-facing layout: an all-empty zone layout must not black out a screen.

A screen carrying both a playlist and a zone layout whose zones are all empty
used to render black -- the player entered zones mode, drew empty regions, and
never looked at the playlist. Nothing in the product said why.
"""
import uuid

from db import execute, query_one


def _org(client):
    sfx = uuid.uuid4().hex[:8]
    email = f"z-{sfx}@example.com"
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Z {sfx}", "email": email})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": "Khanshoof2026Test"})
    assert r.status_code == 200, r.text
    b = r.json()
    return b["token"], b["organization"]["id"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _screen_with_token(org_id, name="Z"):
    tok = uuid.uuid4().hex
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) "
        "VALUES (?, ?, ?, ?, now())",
        (org_id, name, uuid.uuid4().hex[:6].upper(), tok),
    )
    return sid, tok


def _add_zone(screen_id, name="Full", with_item=False, org_id=None):
    zid = execute(
        "INSERT INTO screen_zones (screen_id, name, x, y, width, height, sort_order, transition_ms, created_at) "
        "VALUES (?, ?, 0, 0, 1, 1, 0, 600, now())",
        (screen_id, name),
    )
    if with_item:
        mid = execute(
            "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
            "VALUES (?, ?, ?, ?, ?, now())",
            (org_id, "a.jpg", "https://example.com/a.jpg", "image/jpeg", 0),
        )
        execute(
            "INSERT INTO screen_zone_items (zone_id, media_id, duration_seconds, position, created_at) "
            "VALUES (?, ?, 10, 1, now())",
            (zid, mid),
        )
    return zid


def test_all_empty_zones_are_hidden_from_the_player(client):
    """The bug: a screen with a playlist and only empty zones showed black."""
    _tok, org = _org(client)
    sid, stoken = _screen_with_token(org)
    _add_zone(sid, "Full", with_item=False)

    r = client.get(f"/screens/{stoken}/layout")
    assert r.status_code == 200, r.text
    assert r.json()["zones"] == [], (
        "an all-empty zone layout must not put the player into zones mode"
    )


def test_a_zone_with_content_still_wins(client):
    """Partially built layouts must not be yanked back to a playlist."""
    _tok, org = _org(client)
    sid, stoken = _screen_with_token(org)
    _add_zone(sid, "Empty", with_item=False)
    _add_zone(sid, "Filled", with_item=True, org_id=org)

    zones = client.get(f"/screens/{stoken}/layout").json()["zones"]
    assert len(zones) == 2, "both zones should be returned once any has content"
    assert {z["name"] for z in zones} == {"Empty", "Filled"}


def test_dashboard_editor_still_sees_empty_zones(client):
    """The operator must be able to see and edit a zone they have not filled."""
    tok, org = _org(client)
    sid, _stoken = _screen_with_token(org)
    _add_zone(sid, "Full", with_item=False)

    r = client.get(f"/screens/{sid}/zones", headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert len(r.json()["zones"]) == 1, "editor must still show the empty zone"


def test_preview_layout_follows_the_same_rule(client):
    """Preview should show what the screen will actually do, not something else."""
    _tok, org = _org(client)
    sid, _stoken = _screen_with_token(org)
    _add_zone(sid, "Full", with_item=False)
    ptoken = uuid.uuid4().hex
    execute(
        "INSERT INTO preview_tokens (screen_id, token, created_at, expires_at) "
        "VALUES (?, ?, now()::text, (now() + interval '10 minutes')::text)",
        (sid, ptoken),
    )
    r = client.get(f"/preview/{ptoken}/layout")
    assert r.status_code == 200, r.text
    assert r.json()["zones"] == []


def test_screen_with_no_zones_at_all_is_unchanged(client):
    _tok, org = _org(client)
    sid, stoken = _screen_with_token(org)
    assert client.get(f"/screens/{stoken}/layout").json()["zones"] == []
