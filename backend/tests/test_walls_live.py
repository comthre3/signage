"""Live geometry for phone-placed ("free") video walls.

A free wall's TV shows its own box of the wall's video. hello carries the
box; "geometry" frames move it live. The placement PUT calls
publish_geometry: the worker holding the TV's socket pushes the frame, the
other workers hear about it on Redis, and the tick loop is the fallback. A TV
also reports its screen size, and a box still at its 16:9 default takes the
TV's shape.

No pytest-asyncio: async code runs under asyncio.run() against FakeWS
sockets. TestClient's receive_json() has no timeout, so real sockets are read
through _recv_until().
"""
import asyncio
import json
import logging
import queue
import secrets
import time
import uuid

import anyio
import pytest
import redis
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import ratelimit
import walls as walls_mod
from db import execute, query_one, utc_now_iso
from main import app

PLACE = ("place_x", "place_y", "place_w", "place_h")


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


def _recv_until(ws, type_, timeout=2.0):
    """The next frame of type ``type_`` from a TestClient websocket, or None.

    Starlette 0.37.2's WebSocketTestSession.receive_json() blocks on
    ``_send_queue.get()`` with no timeout, so a frame that never comes would
    hang the suite. This reads the same queue.Queue with a deadline and skips
    frames of other types (hello, play, ping). The queue holds ASGI messages:
    {"type": "websocket.send", "text": ...}, {"type": "websocket.close", ...},
    or an exception the app raised.
    """
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        try:
            message = ws._send_queue.get(timeout=left)
        except queue.Empty:
            return None
        if isinstance(message, BaseException):
            raise message
        if message["type"] == "websocket.close":
            raise WebSocketDisconnect(code=message.get("code", 1000))
        if message["type"] != "websocket.send":
            continue
        frame = json.loads(message["text"])
        if frame.get("type") == type_:
            return frame


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    walls_mod._connections.clear()
    walls_mod._conn_identity.clear()
    walls_mod._tick_tasks.clear()
    walls_mod._geom_sent.clear()
    task, walls_mod._geometry_task = walls_mod._geometry_task, None
    if task is not None and not task.done():
        try:
            task.get_loop().call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass                        # its loop is closed: nothing left to cancel


def _org():
    slug = "lv" + secrets.token_hex(3)
    return execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"LV {slug}", slug, "active", 10, utc_now_iso()),
    )


def _free_wall(boxes, canvas=(3840, 720)):
    """A free wall with no playlist; cell i sits at (0, i) with box boxes[i] = (x, y, w, h)."""
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, layout, rows, cols, canvas_width_px, "
        "canvas_height_px, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "Live", "spanned", "free", 1, len(boxes), canvas[0], canvas[1],
         utc_now_iso(), utc_now_iso()),
    )
    cids = [
        execute(
            "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
            "place_h, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, 0, i, x, y, w, h, utc_now_iso()),
        )
        for i, (x, y, w, h) in enumerate(boxes)
    ]
    return wid, cids


def _grid_wall():
    """A 1x2 spanned grid wall; its cell (0, 0) carries a stray box that must be ignored."""
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, canvas_width_px, "
        "canvas_height_px, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "Grid", "spanned", 1, 2, 3840, 2160, utc_now_iso(), utc_now_iso()),
    )
    cid = execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
        "place_h, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, 0, 0, 0.0, 0.0, 0.5, 1.0, utc_now_iso()),
    )
    return wid, cid


def _bind_tv(wid, cid):
    """A TV screen paired into cell ``cid``, linked both ways as redeem leaves it."""
    oid = query_one("SELECT organization_id FROM walls WHERE id = ?", (wid,))["organization_id"]
    token = uuid.uuid4().hex
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, wall_cell_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, "TV", "lv" + secrets.token_hex(6), token, cid, utc_now_iso()),
    )
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (sid, cid))
    return sid, token


def _cell(cid):
    return query_one("SELECT * FROM wall_cells WHERE id = ?", (cid,))


def _key(cid):
    cell = _cell(cid)
    return (cell["row_index"], cell["col_index"])


def _registered(wid, cid):
    """A FakeWS registered for cell ``cid`` as wall_ws registers a paired TV."""
    sid, token = _bind_tv(wid, cid)
    ws = FakeWS()
    walls_mod._register(wid, _key(cid), ws, sid, token)
    return ws


def _held(wid, cid):
    """A FakeWS this worker holds for cell ``cid``, already sent its box (as by hello)."""
    ws = FakeWS()
    walls_mod._connections[wid][_key(cid)] = ws
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wid,))
    walls_mod._geom_sent[ws] = walls_mod._geom_sig(wall, _cell(cid))
    return ws


# ---- hello and geometry frames ----------------------------------------------

FREE_WALL = {"id": 9, "mode": "spanned", "layout": "free", "rows": 1, "cols": 3,
             "canvas_width_px": 3840, "canvas_height_px": 720,
             "bezel_h_pct": 0.0, "bezel_v_pct": 0.0}


def test_free_wall_hello_carries_the_box_in_canvas_px():
    cell = {"row_index": 0, "col_index": 1, "place_x": 0.25, "place_y": -0.5,
            "place_w": 0.5, "place_h": 2.0, "highlight_until_ms": 1762000030000}
    frame = walls_mod._hello_frame(FREE_WALL, cell, current_play=None)
    assert frame["mode"] == "spanned"
    assert frame["cell"] == {"row": 0, "col": 1, "rows": 1, "cols": 3}
    assert frame["canvas"] == {"w": 3840, "h": 720}
    assert frame["bezel"] == {"h_pct": 0.0, "v_pct": 0.0}
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 960.0, "y": -360.0, "w": 1920.0, "h": 1440.0})
    assert frame["tv_number"] == 2
    assert frame["highlight_until_ms"] == 1762000030000
    json.dumps(frame)                   # _send_safe drops a frame json.dumps refuses


def test_free_wall_hello_without_a_highlight_says_null():
    cell = {"row_index": 0, "col_index": 0, "place_x": 0.0, "place_y": 0.0,
            "place_w": 1 / 3, "place_h": 1.0, "highlight_until_ms": None}
    frame = walls_mod._hello_frame(FREE_WALL, cell, current_play=None)
    assert frame["highlight_until_ms"] is None
    assert frame["tv_number"] == 1
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})


def test_grid_hello_ignores_a_stored_box():
    """Guards the grid branch (R22: today's hello, key for key); passes before and after this task."""
    wall = dict(FREE_WALL, layout="grid", cols=2, canvas_height_px=2160)
    cell = {"row_index": 0, "col_index": 1, "place_x": 0.1, "place_y": 0.1,
            "place_w": 0.2, "place_h": 0.2, "highlight_until_ms": 5}
    frame = walls_mod._hello_frame(wall, cell, current_play=None)
    assert frame["cell_geometry"] == {"x": 1920.0, "y": 0.0, "w": 1920.0, "h": 2160.0}
    assert "tv_number" not in frame and "highlight_until_ms" not in frame
    assert set(frame) == {"type", "wall_id", "mode", "cell", "current_play", "server_now_ms",
                          "canvas", "bezel", "cell_geometry"}


def test_free_wall_cell_without_a_box_gets_its_grid_slice():
    """Guards the fallback for a cell with no box; passes before and after this task."""
    frame = walls_mod._hello_frame(FREE_WALL, {"row_index": 0, "col_index": 2},
                                   current_play=None)
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 2560.0, "y": 0.0, "w": 1280.0, "h": 720.0})
    assert "tv_number" not in frame


def test_geometry_frame_has_the_box_highlight_and_tv_number(monkeypatch):
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1762000000000)
    cell = {"row_index": 0, "col_index": 1, "place_x": 1 / 3, "place_y": 0.0,
            "place_w": 1 / 3, "place_h": 1.0, "highlight_until_ms": 1762000030000}
    frame = walls_mod._geometry_frame(FREE_WALL, cell)
    assert frame == {
        "type": "geometry",
        "canvas": {"w": 3840, "h": 720},
        "cell_geometry": pytest.approx({"x": 1280.0, "y": 0.0, "w": 1280.0, "h": 720.0}),
        "highlight_until_ms": 1762000030000,
        "tv_number": 2,
        "server_now_ms": 1762000000000,
    }
    json.dumps(frame)


def test_free_cell_geometry_needs_all_four_box_fields():
    cell = {"row_index": 0, "col_index": 0, "place_x": 0.0, "place_y": 0.0,
            "place_w": 0.5, "place_h": None}
    assert walls_mod.free_cell_geometry(FREE_WALL, cell) is None
    assert walls_mod.free_cell_geometry(FREE_WALL, dict(cell, place_h=1.0)) == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 720.0})


def test_geom_sig_follows_box_canvas_and_highlight_only():
    wall = {"canvas_width_px": 3840, "canvas_height_px": 720}
    cell = {"place_x": 0.0, "place_y": 0.0, "place_w": 0.5, "place_h": 1.0,
            "highlight_until_ms": None, "screen_w_px": 1920, "screen_h_px": 1080}
    sig = walls_mod._geom_sig(wall, cell)
    assert sig == (3840, 720, 0.0, 0.0, 0.5, 1.0, None)
    assert walls_mod._geom_sig(wall, dict(cell, place_x=0.1)) != sig
    assert walls_mod._geom_sig(wall, dict(cell, highlight_until_ms=5)) != sig
    assert walls_mod._geom_sig(dict(wall, canvas_width_px=5760), cell) != sig
    assert walls_mod._geom_sig(wall, dict(cell, screen_w_px=1080)) == sig   # size is not geometry


def test_tv_number_and_highlight_length():
    assert walls_mod.tv_number({"row_index": 0, "col_index": 4}) == 5
    assert walls_mod.HIGHLIGHT_SECONDS == 30


@pytest.mark.parametrize("url,channel", [
    ("redis://redis:6379/0", "khan:wall-geometry:db0"),
    ("redis://redis:6379", "khan:wall-geometry:db0"),
    ("redis://redis:6379/5", "khan:wall-geometry:db5"),
    ("redis://:secret@redis:6379/12", "khan:wall-geometry:db12"),
    ("redis://redis:6379/4?db=3", "khan:wall-geometry:db3"),
])
def test_geometry_channel_is_per_redis_database(monkeypatch, url, channel):
    monkeypatch.setattr(ratelimit, "REDIS_URL", url)
    assert walls_mod.geometry_channel() == channel


def test_test_runs_never_use_productions_channel():
    # scripts/test-backend-in-docker.sh refuses KHAN_TEST_REDIS_DB=0.
    assert walls_mod.geometry_channel() != f"{walls_mod.GEOMETRY_CHANNEL_BASE}:db0"


# ---- tick-loop fallback -----------------------------------------------------

def _tick(wid, state, ws):
    """One tick-loop pass; returns the geometry frames it sent ``ws``."""
    ws.frames.clear()
    asyncio.run(walls_mod._tick_once(wid, state))
    return ws.of_type("geometry")


def test_tick_sends_geometry_once_per_change_of_box_or_highlight(monkeypatch):
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = _registered(wid, cid)
    state = {}
    first = _tick(wid, state, ws)          # no hello went through wall_ws: nothing recorded
    assert len(first) == 1
    assert first[0]["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})
    assert first[0]["tv_number"] == 1
    assert _tick(wid, state, ws) == []     # unchanged: nothing

    execute("UPDATE wall_cells SET place_x = ? WHERE id = ?", (0.5, cid))
    moved = _tick(wid, state, ws)
    assert len(moved) == 1
    assert moved[0]["cell_geometry"]["x"] == pytest.approx(1920.0)
    assert _tick(wid, state, ws) == []

    execute("UPDATE wall_cells SET highlight_until_ms = ? WHERE id = ?", (1762000030000, cid))
    assert [g["highlight_until_ms"] for g in _tick(wid, state, ws)] == [1762000030000]
    # R9: the deadline passing changes nothing stored. The TV's own timer hides
    # the outline, so the server sends nothing then.
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1762000030000 + 60_000)
    assert _tick(wid, state, ws) == []
    assert ws.closed_with is None          # still the cell's TV: _drop_unbound kept it


def test_tick_sends_the_stored_box_not_the_passs_older_copy(monkeypatch):
    """A box pushed through Redis during a pass is newer than the pass's cell rows."""
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = _registered(wid, cid)
    stale = _cell(cid)                     # what the pass's cell query will return
    execute("UPDATE wall_cells SET place_x = ? WHERE id = ?", (0.5, cid))   # moved meanwhile
    real_query_all = walls_mod.query_all

    def cells_from_before_the_move(sql, params=()):
        if sql.startswith("SELECT * FROM wall_cells WHERE wall_id"):
            return [stale]
        return real_query_all(sql, params)

    monkeypatch.setattr(walls_mod, "query_all", cells_from_before_the_move)
    state = {}
    assert [g["cell_geometry"]["x"] for g in _tick(wid, state, ws)] == [pytest.approx(1920.0)]
    # The TV has the newest box now; the pass's older copy must not undo it.
    assert _tick(wid, state, ws) == []


def test_re_hello_after_a_wall_change_carries_the_tvs_box():
    wid, (c0, c1) = _free_wall([(0.0, 0.0, 1 / 3, 1.0), (0.4, 0.1, 0.3, 0.9)])
    ws = _registered(wid, c1)
    state = {}
    _tick(wid, state, ws)                  # records the wall's config
    execute("UPDATE walls SET cols = 3 WHERE id = ?", (wid,))     # a third TV joined
    assert _tick(wid, state, ws) == []     # the hello carried the box: no geometry repeats it
    hellos = ws.of_type("hello")
    assert len(hellos) == 1
    assert hellos[0]["cell_geometry"] == pytest.approx(
        {"x": 1536.0, "y": 72.0, "w": 1152.0, "h": 648.0}, rel=1e-5)
    assert hellos[0]["tv_number"] == 2


def test_tick_sends_no_geometry_on_grid_walls(monkeypatch):
    """Guards the layout check (R22: a grid pass is today's); passes before and after this task."""
    wid, cid = _grid_wall()
    ws = _registered(wid, cid)
    queries = []
    real_query_all = walls_mod.query_all

    def recording_query_all(sql, params=()):
        queries.append(sql)
        return real_query_all(sql, params)

    monkeypatch.setattr(walls_mod, "query_all", recording_query_all)
    assert _tick(wid, {}, ws) == []
    assert "SELECT * FROM wall_cells WHERE wall_id = ?" not in queries   # no box read for grid walls


def test_unregister_forgets_what_the_socket_was_sent():
    ws = FakeWS()
    walls_mod._register(4343, (0, 0), ws, 1, "t" * 32)
    walls_mod._geom_sent[ws] = ("sig",)
    walls_mod._unregister(4343, (0, 0), ws)
    assert ws not in walls_mod._geom_sent


# ---- the live path: push, publish, subscribe --------------------------------

class _RecordingRedis:
    """Stands in for ratelimit.get_redis()'s client; records what was published."""

    def __init__(self):
        self.published = []

    def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return 0


def test_push_geometry_sends_each_box_once():
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))     # Redis echo of the local push
    assert len(ws.of_type("geometry")) == 1
    execute("UPDATE wall_cells SET place_x = ?, highlight_until_ms = ? WHERE id = ?",
            (0.25, 1762000030000, cid))
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    frames = ws.of_type("geometry")
    assert len(frames) == 2
    assert frames[1]["cell_geometry"]["x"] == pytest.approx(960.0)
    assert frames[1]["highlight_until_ms"] == 1762000030000


def test_push_geometry_reads_nothing_for_a_tv_held_elsewhere(monkeypatch):
    def no_database(*args, **kwargs):
        raise AssertionError("read the database for a socket this worker does not hold")

    monkeypatch.setattr(walls_mod, "query_one", no_database)
    asyncio.run(walls_mod._push_geometry(424242, 0, 0))


def test_push_geometry_sends_nothing_on_grid_walls():
    wid, cid = _grid_wall()
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    assert ws.frames == []


def test_publish_geometry_from_a_rest_thread_reaches_a_local_tv_and_redis(monkeypatch):
    fake = _RecordingRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()

    async def scenario():
        walls_mod._connections[wid][(0, 0)] = ws
        # REST handlers are sync endpoints, run in AnyIO worker threads.
        await anyio.to_thread.run_sync(walls_mod.publish_geometry, wid, 0, 0)
        for _ in range(100):
            if ws.frames:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert len(ws.of_type("geometry")) == 1
    assert fake.published == [
        (walls_mod.geometry_channel(), {"wall_id": wid, "row": 0, "col": 0})]


def test_publish_geometry_for_a_tv_held_elsewhere_only_publishes(monkeypatch):
    fake = _RecordingRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    scheduled = []

    def record(coro):
        scheduled.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(walls_mod, "_run_on_server_loop", record)
    walls_mod.publish_geometry(434343, 0, 1)
    assert scheduled == []
    assert fake.published == [
        (walls_mod.geometry_channel(), {"wall_id": 434343, "row": 0, "col": 1})]


def test_publish_geometry_never_raises_when_redis_is_down(monkeypatch):
    marked = []

    class _DownRedis:
        def publish(self, channel, message):
            raise redis.exceptions.ConnectionError("Error 111 connecting to redis:6379")

    monkeypatch.setattr(ratelimit, "get_redis", lambda: _DownRedis())
    monkeypatch.setattr(ratelimit, "_mark_unhealthy", marked.append)
    walls_mod.publish_geometry(434343, 0, 0)
    assert len(marked) == 1 and isinstance(marked[0], redis.exceptions.ConnectionError)

    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)   # in its cooldown
    walls_mod.publish_geometry(434343, 0, 0)
    assert len(marked) == 1


def test_geometry_subscriber_starts_once_per_event_loop(monkeypatch):
    loops = []

    async def idle_subscriber():
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(3600)

    monkeypatch.setattr(walls_mod, "_geometry_subscriber", idle_subscriber)

    async def two_sockets_connect():
        walls_mod._ensure_geometry_subscriber()
        first = walls_mod._geometry_task
        walls_mod._ensure_geometry_subscriber()
        assert walls_mod._geometry_task is first
        await asyncio.sleep(0)                   # let it start
        return first

    first = asyncio.run(two_sockets_connect())
    second = asyncio.run(two_sockets_connect())  # a new loop: the old task ended with its loop
    assert first is not second
    assert len(loops) == 2 and loops[0] is not loops[1]


def test_subscriber_forwards_only_to_tvs_this_worker_holds(monkeypatch):
    channel = f"khan:wall-geometry:test-{uuid.uuid4().hex}"   # nobody else listens here
    monkeypatch.setattr(walls_mod, "geometry_channel", lambda: channel)
    wid, _ = _free_wall([(0.0, 0.0, 1 / 3, 1.0), (1 / 3, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws                 # only TV 1 is on this worker
    pushed = []
    real_push = walls_mod._push_geometry

    async def spy_push(wall_id, row, col):
        pushed.append((wall_id, row, col))
        await real_push(wall_id, row, col)

    monkeypatch.setattr(walls_mod, "_push_geometry", spy_push)
    publisher = redis.Redis.from_url(ratelimit.REDIS_URL, decode_responses=True,
                                     socket_connect_timeout=2, socket_timeout=2)

    async def scenario():
        task = asyncio.get_running_loop().create_task(walls_mod._geometry_subscriber())
        try:
            for _ in range(200):
                if dict(publisher.pubsub_numsub(channel)).get(channel, 0) >= 1:
                    break
                await asyncio.sleep(0.025)
            else:
                raise AssertionError("the subscriber never subscribed")
            publisher.publish(channel, "not json")
            publisher.publish(channel, json.dumps({"wall_id": str(wid), "row": 0, "col": 0}))
            publisher.publish(channel, json.dumps({"wall_id": wid, "row": 0, "col": 1}))
            publisher.publish(channel, json.dumps({"wall_id": wid, "row": 0, "col": 0}))
            for _ in range(200):
                if ws.frames:
                    break
                await asyncio.sleep(0.01)
            assert not task.done(), "a bad message ended the subscriber"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    finally:
        publisher.close()
    assert pushed == [(wid, 0, 0)]
    frames = ws.of_type("geometry")
    assert len(frames) == 1
    assert frames[0]["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})


def test_subscriber_keeps_retrying_while_redis_is_down(monkeypatch, caplog):
    monkeypatch.setattr(ratelimit, "REDIS_URL", "redis://127.0.0.1:1/0")   # nothing listens
    monkeypatch.setattr(walls_mod, "geometry_channel",
                        lambda: f"khan:wall-geometry:test-{uuid.uuid4().hex}")
    monkeypatch.setattr(walls_mod, "GEOMETRY_RETRY_MIN_S", 0.01)
    caplog.set_level(logging.WARNING, logger="walls")
    with pytest.raises(asyncio.TimeoutError):           # still running when cut off
        asyncio.run(asyncio.wait_for(walls_mod._geometry_subscriber(), timeout=0.5))
    failures = [r for r in caplog.records
                if "wall_geometry_subscriber_failed" in r.getMessage()]
    assert len(failures) >= 2
