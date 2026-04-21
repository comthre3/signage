import uuid


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup(client, suffix: str) -> dict:
    response = client.post(
        "/auth/signup",
        json={
            "business_name": f"Biz {suffix}",
            "email": f"owner-{suffix}@example.com",
            "password": "testpass1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_org_a_cannot_see_or_mutate_org_b_screens(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.post("/screens", json={"name": "A-only"}, headers=_bearer(a["token"]))
    assert r.status_code == 200, r.text
    a_screen_id = r.json()["id"]

    # B lists screens — must not include A's
    r = client.get("/screens", headers=_bearer(b["token"]))
    assert r.status_code == 200
    assert a_screen_id not in [s["id"] for s in r.json()]

    # B tries to update A's screen by id — must 404
    r = client.put(
        f"/screens/{a_screen_id}",
        json={"name": "pwned"},
        headers=_bearer(b["token"]),
    )
    assert r.status_code == 404, r.text

    # B tries to delete A's screen — must 404
    r = client.delete(f"/screens/{a_screen_id}", headers=_bearer(b["token"]))
    assert r.status_code == 404, r.text


def test_org_a_cannot_see_org_b_playlists(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.post(
        "/playlists",
        json={"name": "A-only playlist"},
        headers=_bearer(a["token"]),
    )
    assert r.status_code == 200, r.text
    a_playlist_id = r.json()["id"]

    r = client.get("/playlists", headers=_bearer(b["token"]))
    assert r.status_code == 200
    assert a_playlist_id not in [p["id"] for p in r.json()]


def test_org_a_cannot_see_org_b_users(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.get("/users", headers=_bearer(a["token"]))
    assert r.status_code == 200
    a_usernames = [u["username"] for u in r.json()]

    r = client.get("/users", headers=_bearer(b["token"]))
    assert r.status_code == 200
    b_usernames = [u["username"] for u in r.json()]

    assert a["user"]["username"] in a_usernames
    assert a["user"]["username"] not in b_usernames
    assert b["user"]["username"] in b_usernames
    assert b["user"]["username"] not in a_usernames
