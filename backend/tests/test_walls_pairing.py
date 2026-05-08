import pytest
import secrets
import uuid

from fastapi.testclient import TestClient

from db import execute, query_one, utc_now_iso
from main import app, hash_password


@pytest.fixture
def client():
    return TestClient(app)


def make_admin(label="wt"):
    slug = label + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        (f"O {slug}", slug, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
        (uid, tok, utc_now_iso()),
    )
    return {"token": tok, "org_id": oid, "user_id": uid}


def auth(t):
    return {"Authorization": f"Bearer {t['token']}"}


def make_wall(client, admin, rows=1, cols=2):
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin["org_id"], "p", utc_now_iso()),
    )
    return client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": rows, "cols": cols,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin)).json()


def test_pair_into_cell_returns_code(client):
    a = make_admin()
    w = make_wall(client, a)
    res = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["code"]) == 6
    assert body["expires_in_seconds"] >= 60


def test_redeem_creates_screen_and_binds_cell(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    res = client.post("/walls/cells/redeem", json={"code": code})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "paired"
    assert body["wall_id"] == w["id"]
    assert body["cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 2}
    assert body["mode"] == "mirrored"
    assert len(body["screen_token"]) > 16
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
        (w["id"],),
    )
    assert cell["screen_id"] is not None
    screen = query_one("SELECT * FROM screens WHERE id = ?", (cell["screen_id"],))
    assert screen["organization_id"] == a["org_id"]
    assert screen["wall_cell_id"] == cell["id"]


def test_redeem_unknown_code_404(client):
    res = client.post("/walls/cells/redeem", json={"code": "ZZZZZZ"})
    assert res.status_code == 404


def test_redeem_double_returns_409(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    client.post("/walls/cells/redeem", json={"code": code})
    res = client.post("/walls/cells/redeem", json={"code": code})
    assert res.status_code == 409


def test_redeem_expired_returns_410(client):
    a = make_admin()
    w = make_wall(client, a)
    body = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()
    execute(
        "UPDATE wall_pairing_codes SET expires_at = ? WHERE code = ?",
        ("2020-01-01T00:00:00+00:00", body["code"]),
    )
    res = client.post("/walls/cells/redeem", json={"code": body["code"]})
    assert res.status_code == 410


def test_unpair_clears_cell_and_screen(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    client.post("/walls/cells/redeem", json={"code": code})
    res = client.delete(f"/walls/{w['id']}/cells/0/0/pairing", headers=auth(a))
    assert res.status_code == 204
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
        (w["id"],),
    )
    assert cell["screen_id"] is None


def test_pair_other_org_404(client):
    a1 = make_admin("a1")
    a2 = make_admin("a2")
    w = make_wall(client, a1)
    res = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a2))
    assert res.status_code == 404


def test_screen_content_includes_wall_id_for_paired_cells(client):
    a = make_admin("c")
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    redeem = client.post("/walls/cells/redeem", json={"code": code}).json()
    res = client.get(f"/screens/{redeem['screen_token']}/content")
    assert res.status_code == 200
    body = res.json()
    assert body["wall_id"] == w["id"]
    assert body["wall_cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 2}


def test_screen_content_no_wall_id_for_standalone(client):
    a = make_admin("std")
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) VALUES (?, ?, ?, ?, ?)",
        (a["org_id"], "S", secrets.token_hex(3), "tok_" + secrets.token_hex(8), utc_now_iso()),
    )
    tok = query_one("SELECT token FROM screens WHERE id = ?", (sid,))["token"]
    res = client.get(f"/screens/{tok}/content")
    assert "wall_id" not in res.json() or res.json().get("wall_id") is None
