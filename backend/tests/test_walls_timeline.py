"""Wall timeline robustness: the tick loop, multi-worker agreement, and "bye".

The backend runs several uvicorn workers and a wall's cells may connect to
different ones, so what a wall plays must not depend on which worker computes
it, the tick loop must never die quietly, and a REST unpair (which runs in a
worker thread) must still reach the TV.
"""
import asyncio
import json
import secrets
import uuid

import anyio
import pytest
from fastapi.testclient import TestClient

import walls as walls_mod
from db import execute, query_one, utc_now_iso
from main import app, hash_password


class FakeWS:
    """Stands in for a starlette WebSocket held in walls._connections."""

    def __init__(self):
        self.frames = []
        self.closed_with = None

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_with = code

    def of_type(self, kind):
        return [f for f in self.frames if f.get("type") == kind]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    walls_mod._connections.clear()
    walls_mod._tick_tasks.clear()


def _org():
    slug = "tl" + secrets.token_hex(3)
    return execute(
        "INSERT INTO organizations (name, slug, subscription_status, created_at) "
        "VALUES (?, ?, ?, ?)",
        (f"TL {slug}", slug, "active", utc_now_iso()),
    )


def _admin(oid):
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{uuid.uuid4().hex[:8]}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return {"Authorization": f"Bearer {tok}"}


def _playlist(oid, durations):
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (oid, "p", utc_now_iso()),
    )
    for pos, dur in enumerate(durations):
        mid = execute(
            "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (oid, f"m{pos}", f"{uuid.uuid4().hex}.png", "image/png", 10, utc_now_iso()),
        )
        execute(
            "INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, mid, dur, pos, utc_now_iso()),
        )
    return pid


def _same_playlist_wall(oid, durations, rows=1, cols=1):
    pid = _playlist(oid, durations)
    return execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, mirrored_mode, "
        "mirrored_playlist_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "W", "mirrored", rows, cols, "same_playlist", pid, utc_now_iso(), utc_now_iso()),
    )


def _synced_wall_with_empty_cell(oid):
    """1x2 synced-rotation wall: cell (0,0) has two items, cell (0,1) has none."""
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, mirrored_mode, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "S", "mirrored", 1, 2, "synced_rotation", utc_now_iso(), utc_now_iso()),
    )
    pid = _playlist(oid, [5, 7])
    execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, playlist_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)", (wid, 0, 0, pid, utc_now_iso()),
    )
    execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, created_at) "
        "VALUES (?, ?, ?, ?)", (wid, 0, 1, utc_now_iso()),
    )
    return wid


async def _run_tick_briefly(wall_id, seconds=0.4):
    try:
        await asyncio.wait_for(walls_mod._tick_loop(wall_id), timeout=seconds)
    except asyncio.TimeoutError:
        pass


# ---- finding 11: synced rotation math + a tick loop that survives errors ----

def test_slot_durations_ignore_empty_cells():
    assert walls_mod.synced_rotation_slot_durations([[5, 10], []]) == [5000, 10000]
    assert walls_mod.synced_rotation_slot_durations([[], [4, 6]]) == [4000, 6000]
    assert walls_mod.synced_rotation_slot_durations([[], []]) == []


def test_slot_durations_cycle_shorter_cells():
    # The one-item cell repeats its item in every slot.
    assert walls_mod.synced_rotation_slot_durations([[5, 10, 3], [4]]) == [5000, 10000, 4000]


def test_synced_rotation_with_an_empty_cell_still_plays():
    wid = _synced_wall_with_empty_cell(_org())
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(_run_tick_briefly(wid))
    plays = ws.of_type("play")
    assert plays, f"no play frame sent; got {ws.frames}"
    assert plays[0]["item"]["url"].startswith("/uploads/")


def test_tick_loop_survives_an_exception(monkeypatch):
    wid = _same_playlist_wall(_org(), [5, 7])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    real_query_one = walls_mod.query_one
    calls = {"n": 0}

    def flaky_query_one(sql, params=()):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return real_query_one(sql, params)

    monkeypatch.setattr(walls_mod, "query_one", flaky_query_one)
    monkeypatch.setattr(walls_mod, "TICK_ERROR_BACKOFF_S", 0.01, raising=False)
    asyncio.run(_run_tick_briefly(wid, seconds=0.5))
    assert ws.of_type("play"), "tick loop died on the first exception"


# ---- finding 14: every worker derives the same timeline ----

def test_timeline_slot_is_a_pure_function_of_time():
    cycle = 12_000
    at = cycle * 1000 + 6_000          # 6s into a 5s+7s cycle -> second slot
    idx, start, end = walls_mod.timeline_slot([5_000, 7_000], at)
    assert (idx, start, end) == (1, cycle * 1000 + 5_000, cycle * 1000 + 12_000)


def test_fresh_worker_agrees_with_running_worker(monkeypatch):
    """A worker that has never ticked must report the same started_at_ms."""
    wid = _same_playlist_wall(_org(), [5, 7])
    cell = {"row_index": 0, "col_index": 0, "wall_id": wid}
    t0 = 1_800_000_000_123
    monkeypatch.setattr(walls_mod, "now_ms", lambda: t0)
    first = walls_mod.current_play_for(wid, cell)
    # Later, still inside the same slot, as computed by "another worker".
    monkeypatch.setattr(walls_mod, "now_ms", lambda: first["started_at_ms"] + 1)
    second = walls_mod.current_play_for(wid, cell)
    assert second["item"]["id"] == first["item"]["id"]
    assert second["started_at_ms"] == first["started_at_ms"]


def test_zero_or_missing_durations_do_not_spin(monkeypatch):
    """A 0s item must not make the tick loop broadcast in a tight loop."""
    wid = _same_playlist_wall(_org(), [0])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(_run_tick_briefly(wid, seconds=0.3))
    assert 1 <= len(ws.of_type("play")) <= 2


# ---- finding 12: "bye" from a REST handler's worker thread ----

def test_broadcast_bye_from_worker_thread_is_delivered():
    ws = FakeWS()

    async def scenario():
        walls_mod._connections[4242][(0, 0)] = ws
        await anyio.to_thread.run_sync(walls_mod.broadcast_bye, 4242, 0, 0, "cell_unpaired")
        for _ in range(100):
            if ws.frames:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    byes = ws.of_type("bye")
    assert byes and byes[0]["reason"] == "cell_unpaired"


def test_tick_loop_says_bye_to_a_socket_whose_cell_was_unpaired(client):
    """Unpair may run on a different worker than the cell's socket."""
    oid = _org()
    headers = _admin(oid)
    pid = _playlist(oid, [5])
    wall = client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": 1, "cols": 1,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=headers).json()
    code = client.post(f"/walls/{wall['id']}/cells/0/0/pair", headers=headers).json()["code"]
    token = client.post("/walls/cells/redeem", json={"code": code}).json()["screen_token"]
    screen = query_one("SELECT * FROM screens WHERE token = ?", (token,))
    ws = FakeWS()
    walls_mod._register(wall["id"], (0, 0), ws, screen["id"], token)
    # The unpair happens elsewhere: only the database knows.
    execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (screen["id"],))
    execute("UPDATE wall_cells SET screen_id = NULL WHERE wall_id = ?", (wall["id"],))
    asyncio.run(_run_tick_briefly(wall["id"]))
    assert ws.of_type("bye") or ws.of_type("wall_left"), ws.frames
    assert ws.closed_with is not None
