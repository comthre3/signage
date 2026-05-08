import pytest
import secrets
import uuid

from fastapi.testclient import TestClient

from db import execute, utc_now_iso
from main import app, hash_password


@pytest.fixture
def client():
    return TestClient(app)


def _make_paired_wall(client):
    slug = "ws" + secrets.token_hex(3)
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
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (oid, "p", utc_now_iso()),
    )
    w = client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": 1, "cols": 1,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers={"Authorization": f"Bearer {tok}"}).json()
    code = client.post(
        f"/walls/{w['id']}/cells/0/0/pair",
        headers={"Authorization": f"Bearer {tok}"},
    ).json()["code"]
    redeem = client.post("/walls/cells/redeem", json={"code": code}).json()
    return w, redeem["screen_token"]


def test_ws_hello_frame_on_connect(client):
    wall, token = _make_paired_wall(client)
    with client.websocket_connect(f"/walls/{wall['id']}/ws?screen_token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello"
        assert msg["wall_id"] == wall["id"]
        assert msg["mode"] == "mirrored"
        assert msg["cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 1}
        assert "server_now_ms" in msg


def test_ws_rejects_unknown_token(client):
    wall, _ = _make_paired_wall(client)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/walls/{wall['id']}/ws?screen_token=zzzzzzzz"
        ) as ws:
            ws.receive_json()


def test_ws_rejects_wrong_wall(client):
    wall1, token1 = _make_paired_wall(client)
    wall2, _ = _make_paired_wall(client)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/walls/{wall2['id']}/ws?screen_token={token1}"
        ) as ws:
            ws.receive_json()
