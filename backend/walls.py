"""Wall WebSocket fanout + lazy per-wall tick loop registry.

This module assumes a single-uvicorn-worker deployment. The connection
registry and tick-loop tasks live in process memory; if we ever scale
out workers, every wall's WebSocket connections must land on the same
worker (sticky routing) OR we must add a Redis pub/sub layer between
workers. Out of scope for v1.
"""

import asyncio
import json
import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from db import query_one, query_all, execute, utc_now_iso

router = APIRouter()

# wall_id -> {(row, col): WebSocket}
_connections: Dict[int, Dict[Tuple[int, int], WebSocket]] = defaultdict(dict)
# wall_id -> asyncio.Task
_tick_tasks: Dict[int, asyncio.Task] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def _wall_for_token(wall_id: int, screen_token: str):
    """Return (wall, cell, screen) if token belongs to a screen in this wall, else None."""
    screen = query_one("SELECT * FROM screens WHERE token = ?", (screen_token,))
    if not screen or not screen.get("wall_cell_id"):
        return None
    cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (screen["wall_cell_id"],))
    if not cell or cell["wall_id"] != wall_id:
        return None
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        return None
    return wall, cell, screen


def _hello_frame(wall: dict, cell: dict, current_play: dict | None) -> dict:
    return {
        "type": "hello",
        "wall_id": wall["id"],
        "mode": wall["mode"],
        "cell": {
            "row": cell["row_index"], "col": cell["col_index"],
            "rows": wall["rows"], "cols": wall["cols"],
        },
        "current_play": current_play,
        "server_now_ms": now_ms(),
    }


async def _send_safe(ws: WebSocket, frame: dict) -> bool:
    try:
        await ws.send_text(json.dumps(frame))
        return True
    except Exception:
        return False


async def broadcast(wall_id: int, frame: dict, exclude: Tuple[int, int] | None = None) -> None:
    dead = []
    for key, ws in list(_connections[wall_id].items()):
        if exclude and key == exclude:
            continue
        ok = await _send_safe(ws, frame)
        if not ok:
            dead.append(key)
    for key in dead:
        _connections[wall_id].pop(key, None)


def broadcast_bye(wall_id: int, row: int, col: int, reason: str) -> None:
    """Sync entry-point used by REST handlers (e.g. unpair). Best-effort."""
    ws = _connections.get(wall_id, {}).get((row, col))
    if not ws:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_send_safe(ws, {"type": "bye", "reason": reason,
                                          "server_now_ms": now_ms()}))
    except RuntimeError:
        pass


async def _ensure_tick_loop(wall_id: int) -> None:
    """Lazily create the tick loop. Implementation in Task 5 — for now, no-op."""
    if wall_id in _tick_tasks and not _tick_tasks[wall_id].done():
        return
    return


def current_play_for(wall_id: int, cell: dict) -> dict | None:
    """Return the current play frame for this wall+cell, or None.

    Task 5 fills this in. Until then we return None — clients still get
    a valid hello frame and can fall back to HTTP polling.
    """
    return None


@router.websocket("/walls/{wall_id}/ws")
async def wall_ws(websocket: WebSocket, wall_id: int,
                  screen_token: str = Query(..., min_length=8)):
    info = _wall_for_token(wall_id, screen_token)
    if info is None:
        await websocket.close(code=4401)
        return
    wall, cell, screen = info
    await websocket.accept()
    key = (cell["row_index"], cell["col_index"])
    old = _connections[wall_id].pop(key, None)
    if old is not None:
        try:
            await old.close(code=4000)
        except Exception:
            pass
    _connections[wall_id][key] = websocket

    await _ensure_tick_loop(wall_id)
    await _send_safe(websocket, _hello_frame(wall, cell, current_play_for(wall_id, cell)))

    execute("UPDATE screens SET last_seen = ? WHERE id = ?", (utc_now_iso(), screen["id"]))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "pong":
                continue
            if msg.get("type") == "ready":
                continue
    except WebSocketDisconnect:
        pass
    finally:
        if _connections[wall_id].get(key) is websocket:
            _connections[wall_id].pop(key, None)
        if not _connections[wall_id]:
            t = _tick_tasks.pop(wall_id, None)
            if t and not t.done():
                t.cancel()


def attach_walls(app: FastAPI) -> None:
    app.include_router(router)
