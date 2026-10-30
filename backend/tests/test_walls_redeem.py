"""Wall-cell redemption must respect the plan's screen limit and be atomic.

Every redeemed cell is a screens row, so it counts toward the plan exactly like
a screen created on the Screens page; re-pairing a cell must not leave the old
screen behind; and two concurrent redeems must not both succeed.
"""
import secrets
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from db import execute, query_all, query_one, utc_now_iso
from main import app, hash_password


@pytest.fixture
def client():
    return TestClient(app)


def _org(screen_limit=10):
    slug = "rd" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"RD {slug}", slug, "active", screen_limit, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return oid, {"Authorization": f"Bearer {tok}"}


def _wall(client, headers, oid, rows=1, cols=2):
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (oid, "p", utc_now_iso()),
    )
    res = client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": rows, "cols": cols,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=headers)
    assert res.status_code in (200, 201), res.text
    return res.json()


def _code(client, headers, wall_id, row=0, col=0):
    res = client.post(f"/walls/{wall_id}/cells/{row}/{col}/pair", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["code"]


def _screen_count(oid):
    return query_one("SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?", (oid,))["n"]


def test_redeem_refuses_past_the_plan_screen_limit(client):
    oid, headers = _org(screen_limit=1)
    wall = _wall(client, headers, oid)
    assert client.post("/walls/cells/redeem",
                       json={"code": _code(client, headers, wall["id"], 0, 0)}).status_code == 200
    second = _code(client, headers, wall["id"], 0, 1)
    res = client.post("/walls/cells/redeem", json={"code": second})
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "plan_limit"
    assert _screen_count(oid) == 1
    # Refused, not consumed: the same code works once the plan allows it.
    execute("UPDATE organizations SET screen_limit = 2 WHERE id = ?", (oid,))
    assert client.post("/walls/cells/redeem", json={"code": second}).status_code == 200
    assert _screen_count(oid) == 2


def test_repairing_a_cell_reuses_its_screen(client):
    oid, headers = _org(screen_limit=1)
    wall = _wall(client, headers, oid)
    first = client.post("/walls/cells/redeem",
                        json={"code": _code(client, headers, wall["id"])}).json()
    res = client.post("/walls/cells/redeem", json={"code": _code(client, headers, wall["id"])})
    # Re-pairing the same cell is not a new screen, so the limit does not bite.
    assert res.status_code == 200, res.text
    second = res.json()
    assert _screen_count(oid) == 1
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
        (wall["id"],),
    )
    claimants = query_all("SELECT id FROM screens WHERE wall_cell_id = ?", (cell["id"],))
    assert [r["id"] for r in claimants] == [cell["screen_id"]]
    # The replaced TV's token is dead; the new one plays the wall.
    assert client.get(f"/screens/{first['screen_token']}/content").status_code == 404
    content = client.get(f"/screens/{second['screen_token']}/content").json()
    assert content["wall_id"] == wall["id"]


def test_repair_cleans_up_stale_claimants(client):
    """Rows left by the old bug (a second screen claiming the cell) are removed."""
    oid, headers = _org()
    wall = _wall(client, headers, oid)
    client.post("/walls/cells/redeem", json={"code": _code(client, headers, wall["id"])})
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
        (wall["id"],),
    )
    stale = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, wall_cell_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, "stale", secrets.token_hex(3), "tok_" + secrets.token_hex(8), cell["id"],
         utc_now_iso()),
    )
    client.post("/walls/cells/redeem", json={"code": _code(client, headers, wall["id"])})
    assert query_one("SELECT id FROM screens WHERE id = ?", (stale,)) is None
    assert _screen_count(oid) == 1


def test_unpair_removes_the_cells_screen(client):
    oid, headers = _org()
    wall = _wall(client, headers, oid)
    token = client.post("/walls/cells/redeem",
                        json={"code": _code(client, headers, wall["id"])}).json()["screen_token"]
    assert _screen_count(oid) == 1
    assert client.delete(f"/walls/{wall['id']}/cells/0/0/pairing",
                         headers=headers).status_code == 204
    assert _screen_count(oid) == 0
    assert client.get(f"/screens/{token}/content").status_code == 404


def _slow_token(monkeypatch):
    real = main.generate_unique_token

    def slow():
        time.sleep(0.4)        # widen the race window between check and write
        return real()

    monkeypatch.setattr(main, "generate_unique_token", slow)


def _redeem_concurrently(codes):
    results = []
    lock = threading.Lock()

    def go(code):
        res = TestClient(app).post("/walls/cells/redeem", json={"code": code})
        with lock:
            results.append(res.status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(results)


def test_concurrent_redeems_of_one_code_pair_once(client, monkeypatch):
    oid, headers = _org()
    wall = _wall(client, headers, oid)
    code = _code(client, headers, wall["id"])
    _slow_token(monkeypatch)
    assert _redeem_concurrently([code, code]) == [200, 409]
    assert _screen_count(oid) == 1


def test_concurrent_redeems_cannot_overshoot_the_limit(client, monkeypatch):
    oid, headers = _org(screen_limit=1)
    wall = _wall(client, headers, oid)
    codes = [_code(client, headers, wall["id"], 0, 0), _code(client, headers, wall["id"], 0, 1)]
    _slow_token(monkeypatch)
    assert _redeem_concurrently(codes) == [200, 402]
    assert _screen_count(oid) == 1
