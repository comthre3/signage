"""Ids that point into another organization must be refused, not stored.

Fetching a row by id already checks the caller's organization everywhere. The
gap these tests pin is one level down: a write that stores a *reference* --
a screen's playlist, a zone item's media, a wall's playlist -- used to accept
any id at all. The reference was then followed without an organization filter
when content was served, so org A could play, read or delete org B's content
just by naming its ids, which are sequential and therefore guessable.
"""
import uuid

from db import query_all, query_one
from tests.test_multi_tenancy import _bearer, _signup


def _two_orgs(client):
    return _signup(client, uuid.uuid4().hex[:8]), _signup(client, uuid.uuid4().hex[:8])


def _make(client, org, path, body):
    r = client.post(path, json=body, headers=_bearer(org["token"]))
    assert r.status_code in (200, 201), r.text
    return r.json()


def _url_media(client, org, name="m"):
    return _make(client, org, "/media/url", {"name": name, "url": "https://example.com/x.png"})


# ── screens ──────────────────────────────────────────────────────────────

def test_screen_cannot_be_assigned_another_orgs_playlist(client):
    a, b = _two_orgs(client)
    screen = _make(client, a, "/screens", {"name": "A screen"})
    b_playlist = _make(client, b, "/playlists", {"name": "B private"})

    r = client.put(f"/screens/{screen['id']}", json={"playlist_id": b_playlist["id"]},
                   headers=_bearer(a["token"]))
    assert r.status_code == 404, r.text
    row = query_one("SELECT playlist_id FROM screens WHERE id = ?", (screen["id"],))
    assert row["playlist_id"] is None


def test_screen_cannot_reference_another_orgs_site_or_user(client):
    a, b = _two_orgs(client)
    screen = _make(client, a, "/screens", {"name": "A screen"})
    b_site = _make(client, b, "/sites", {"name": "B site"})
    b_user_id = b["user"]["id"]

    for body in ({"site_id": b_site["id"]}, {"owner_user_id": b_user_id}):
        r = client.put(f"/screens/{screen['id']}", json=body, headers=_bearer(a["token"]))
        assert r.status_code == 404, (body, r.text)
        r = client.post("/screens", json={"name": "x", **body}, headers=_bearer(a["token"]))
        assert r.status_code == 404, (body, r.text)

    row = query_one("SELECT site_id, owner_user_id FROM screens WHERE id = ?", (screen["id"],))
    assert row["site_id"] is None and row["owner_user_id"] is None


def test_explicit_null_clears_playlist_site_and_owner(client):
    """The dashboard sends null for "No playlist" / "Unassigned"; it used to be ignored."""
    a, _ = _two_orgs(client)
    playlist = _make(client, a, "/playlists", {"name": "P"})
    site = _make(client, a, "/sites", {"name": "S"})
    screen = _make(client, a, "/screens", {"name": "A screen"})
    r = client.put(f"/screens/{screen['id']}", headers=_bearer(a["token"]), json={
        "playlist_id": playlist["id"], "site_id": site["id"], "owner_user_id": a["user"]["id"]})
    assert r.status_code == 200, r.text

    r = client.put(f"/screens/{screen['id']}", headers=_bearer(a["token"]),
                   json={"playlist_id": None, "site_id": None, "owner_user_id": None})
    assert r.status_code == 200, r.text
    row = query_one("SELECT playlist_id, site_id, owner_user_id, name FROM screens WHERE id = ?",
                    (screen["id"],))
    assert (row["playlist_id"], row["site_id"], row["owner_user_id"]) == (None, None, None)
    assert row["name"] == "A screen"


def test_fields_left_out_of_an_update_are_kept(client):
    a, _ = _two_orgs(client)
    playlist = _make(client, a, "/playlists", {"name": "P"})
    screen = _make(client, a, "/screens", {"name": "A screen"})
    client.put(f"/screens/{screen['id']}", json={"playlist_id": playlist["id"]},
               headers=_bearer(a["token"]))
    r = client.put(f"/screens/{screen['id']}", json={"name": "Renamed"}, headers=_bearer(a["token"]))
    assert r.status_code == 200, r.text
    row = query_one("SELECT playlist_id, name FROM screens WHERE id = ?", (screen["id"],))
    assert row["playlist_id"] == playlist["id"] and row["name"] == "Renamed"


# ── zones ────────────────────────────────────────────────────────────────

def _zones(media_id):
    return {"zones": [{"name": "Z", "x": 0, "y": 0, "width": 1, "height": 1,
                       "items": [{"media_id": media_id, "duration_seconds": 10}]}]}


def test_zone_items_cannot_reference_another_orgs_media(client):
    a, b = _two_orgs(client)
    screen = _make(client, a, "/screens", {"name": "A screen"})
    b_media = _url_media(client, b, "B private")

    r = client.put(f"/screens/{screen['id']}/zones", json=_zones(b_media["id"]),
                   headers=_bearer(a["token"]))
    assert r.status_code == 404, r.text
    leaked = client.get(f"/screens/{screen['id']}/zones", headers=_bearer(a["token"])).json()
    assert all(item["media_id"] != b_media["id"] for z in leaked["zones"] for item in z["items"])

    r = client.post("/zone-templates", headers=_bearer(a["token"]),
                    json={"name": "T", **_zones(b_media["id"])})
    assert r.status_code == 404, r.text


def test_a_rejected_zone_save_leaves_the_existing_layout_intact(client):
    """Zones were deleted before the new ones were validated; a bad id wiped the layout."""
    a, _ = _two_orgs(client)
    screen = _make(client, a, "/screens", {"name": "A screen"})
    media = _url_media(client, a)
    r = client.put(f"/screens/{screen['id']}/zones", json=_zones(media["id"]),
                   headers=_bearer(a["token"]))
    assert r.status_code == 200, r.text

    r = client.put(f"/screens/{screen['id']}/zones", json=_zones(10**9),
                   headers=_bearer(a["token"]))
    assert r.status_code == 404, r.text
    zones = query_all("SELECT id FROM screen_zones WHERE screen_id = ?", (screen["id"],))
    assert len(zones) == 1


# ── walls ────────────────────────────────────────────────────────────────

def _mirrored_wall(client, org, playlist_id):
    return _make(client, org, "/walls", {
        "name": "W", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": playlist_id})


def test_wall_cannot_be_pointed_at_another_orgs_playlist(client):
    a, b = _two_orgs(client)
    a_playlist = _make(client, a, "/playlists", {"name": "A"})
    b_playlist = _make(client, b, "/playlists", {"name": "B private"})
    wall = _mirrored_wall(client, a, a_playlist["id"])

    r = client.patch(f"/walls/{wall['id']}", json={"mirrored_playlist_id": b_playlist["id"]},
                     headers=_bearer(a["token"]))
    assert r.status_code == 404, r.text

    # The deletion half of the attack: switching mode must not remove B's playlist.
    client.patch(f"/walls/{wall['id']}", headers=_bearer(a["token"]), json={
        "mode": "spanned", "canvas_width_px": 3840, "canvas_height_px": 2160})
    assert query_one("SELECT id FROM playlists WHERE id = ?", (b_playlist["id"],))


def test_switching_a_mirrored_wall_keeps_the_users_own_playlist(client):
    """A mirrored wall plays a playlist the user picked; it is not the wall's to delete."""
    a, _ = _two_orgs(client)
    playlist = _make(client, a, "/playlists", {"name": "Shared with other screens"})
    wall = _mirrored_wall(client, a, playlist["id"])
    r = client.patch(f"/walls/{wall['id']}", headers=_bearer(a["token"]), json={
        "mode": "spanned", "canvas_width_px": 3840, "canvas_height_px": 2160})
    assert r.status_code == 200, r.text
    assert query_one("SELECT id FROM playlists WHERE id = ?", (playlist["id"],))
