from db import connect, init_db


def test_walls_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'walls'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "organization_id", "name", "mode", "rows", "cols",
        "canvas_width_px", "canvas_height_px", "bezel_enabled",
        "spanned_playlist_id", "mirrored_mode", "mirrored_playlist_id",
        "created_at", "updated_at",
    }
    missing = expected - cols
    assert not missing, f"walls table missing columns: {missing}"


def test_wall_cells_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_cells'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "wall_id", "row_index", "col_index", "screen_id",
        "screen_size_inches",
        "bezel_top_mm", "bezel_right_mm", "bezel_bottom_mm", "bezel_left_mm",
        "playlist_id", "created_at",
    }
    missing = expected - cols
    assert not missing, f"wall_cells table missing columns: {missing}"


def test_wall_pairing_codes_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_pairing_codes'
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    assert {"id", "code", "wall_id", "row_index", "col_index", "status",
            "expires_at", "created_at", "claimed_at"}.issubset(cols)


def test_screens_has_wall_cell_id_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'screens' AND column_name = 'wall_cell_id'
        """)
        assert cur.fetchone() is not None


def test_organizations_has_walls_enabled_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'organizations' AND column_name = 'walls_enabled'
        """)
        assert cur.fetchone() is not None


def test_playlists_has_kind_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'playlists' AND column_name = 'kind'
        """)
        assert cur.fetchone() is not None


# ---- CRUD endpoint tests ----

import pytest
import secrets
import uuid

from fastapi.testclient import TestClient

from db import execute, query_one, utc_now_iso


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def admin_token():
    from main import hash_password
    org_slug = "wt" + secrets.token_hex(3)
    org_id = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        (f"WallTest {org_slug}", org_slug, utc_now_iso()),
    )
    user_id = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, f"u_{org_slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
        (user_id, token, utc_now_iso()),
    )
    return {"token": token, "org_id": org_id, "user_id": user_id}


def auth(t):
    return {"Authorization": f"Bearer {t['token']}"}


def test_create_mirrored_wall_same_playlist(client, admin_token):
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p1", utc_now_iso()),
    )
    res = client.post("/walls", json={
        "name": "Lobby Wall", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin_token))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Lobby Wall"
    assert body["mode"] == "mirrored"
    assert body["rows"] == 1 and body["cols"] == 2
    assert body["mirrored_mode"] == "same_playlist"
    assert len(body["cells"]) == 2
    assert {(c["row_index"], c["col_index"]) for c in body["cells"]} == {(0, 0), (0, 1)}


def test_create_wall_grid_bounds(client, admin_token):
    res = client.post("/walls", json={
        "name": "X", "mode": "mirrored", "rows": 9, "cols": 1,
        "mirrored_mode": "same_playlist",
    }, headers=auth(admin_token))
    assert res.status_code == 422


def test_list_walls_org_isolation(client, admin_token):
    res = client.get("/walls", headers=auth(admin_token))
    assert res.status_code == 200
    before = len(res.json())
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p_iso", utc_now_iso()),
    )
    client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": 1, "cols": 1,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin_token))
    res = client.get("/walls", headers=auth(admin_token))
    assert len(res.json()) == before + 1


def test_get_wall_includes_cells(client, admin_token):
    create = client.post("/walls", json={
        "name": "W2", "mode": "mirrored", "rows": 2, "cols": 2,
        "mirrored_mode": "synced_rotation",
    }, headers=auth(admin_token)).json()
    res = client.get(f"/walls/{create['id']}", headers=auth(admin_token))
    assert res.status_code == 200
    assert len(res.json()["cells"]) == 4


def test_patch_wall_name(client, admin_token):
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p_pat", utc_now_iso()),
    )
    w = client.post("/walls", json={
        "name": "Old", "mode": "mirrored", "rows": 1, "cols": 1,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin_token)).json()
    res = client.patch(f"/walls/{w['id']}", json={"name": "New"}, headers=auth(admin_token))
    assert res.status_code == 200
    assert res.json()["name"] == "New"


def test_delete_wall_cascades_cells(client, admin_token):
    from db import query_all
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p_del", utc_now_iso()),
    )
    w = client.post("/walls", json={
        "name": "X", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin_token)).json()
    res = client.delete(f"/walls/{w['id']}", headers=auth(admin_token))
    assert res.status_code == 204
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (w["id"],))
    assert cells == []
