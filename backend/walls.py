"""Wall WebSocket fanout + lazy per-wall tick loop registry.

Production runs several uvicorn workers (WEB_CONCURRENCY, default 4) and the
cells of one wall can be connected to different ones. The socket registry and
tick tasks below are per-process, so none of them may *decide* anything that
other workers must agree on:

* What a wall plays at any instant is a pure function of its playlist and the
  wall clock (``timeline_slot``): the timeline is anchored at the Unix epoch,
  so every worker's tick loop -- and every hello frame -- names the same item
  with the same ``started_at_ms`` without any coordination, shared memory,
  Redis or extra tables. Workers share one host clock; TVs correct for their
  own clock skew from ``server_now_ms`` on every frame.
* A worker only fans frames out to the sockets it holds. Membership changes
  (unpair, re-pair, wall deleted) are noticed by every worker's tick loop
  re-reading the cell bindings from Postgres, so a "bye" reaches the TV no
  matter which worker handled the REST call. ``broadcast_bye`` remains the
  fast path when the socket happens to live on the same worker.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

import anyio
from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from db import query_one, query_all, execute, utc_now_iso

logger = logging.getLogger(__name__)

router = APIRouter()

# wall_id -> {(row, col): WebSocket}
_connections: Dict[int, Dict[Tuple[int, int], WebSocket]] = defaultdict(dict)
# WebSocket -> (screen_id, screen_token) it authenticated with
_conn_identity: Dict[object, Tuple[int, str]] = {}
# wall_id -> asyncio.Task
_tick_tasks: Dict[int, asyncio.Task] = {}
# Strong references to fire-and-forget sends (asyncio keeps only weak ones).
_background: set = set()
# The event loop serving this worker's sockets, for callers on other threads.
_server_loop: Optional[asyncio.AbstractEventLoop] = None

# An item with no usable duration (NULL, 0, negative) still holds its slot for
# this long; a 0s slot would make the tick loop rebroadcast in a tight loop.
DEFAULT_ITEM_SECONDS = 10
# Re-read the wall at least this often, so playlist edits, unpairs and mode
# changes reach the TVs within seconds even during a long item.
TICK_MAX_SLEEP_S = 5.0
TICK_MIN_SLEEP_S = 0.05
TICK_ERROR_BACKOFF_S = 2.0
# Keep idle sockets alive through Cloudflare (closes after ~100s of silence)
# and surface dead ones to _send_safe.
PING_INTERVAL_MS = 25_000
# Every worker anchors timelines here, which is what makes them agree.
TIMELINE_EPOCH_MS = 0


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
    base = {
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
    if wall["mode"] == "spanned":
        cw = wall["canvas_width_px"]
        ch = wall["canvas_height_px"]
        h_pct = float(wall.get("bezel_h_pct") or 0)
        v_pct = float(wall.get("bezel_v_pct") or 0)
        cols, rows = wall["cols"], wall["rows"]
        gap_w = (h_pct / 100.0) * cw
        gap_h = (v_pct / 100.0) * ch
        cell_w = (cw - (cols - 1) * gap_w) / cols
        cell_h = (ch - (rows - 1) * gap_h) / rows
        base["canvas"] = {"w": cw, "h": ch}
        base["bezel"] = {"h_pct": h_pct, "v_pct": v_pct}
        base["cell_geometry"] = {
            "x": cell["col_index"] * (cell_w + gap_w),
            "y": cell["row_index"] * (cell_h + gap_h),
            "w": cell_w,
            "h": cell_h,
        }
    return base


def _hello_config(wall: dict) -> tuple:
    """Everything a hello frame tells the TV about the wall's shape."""
    return tuple(wall.get(k) for k in (
        "mode", "mirrored_mode", "rows", "cols",
        "canvas_width_px", "canvas_height_px", "bezel_h_pct", "bezel_v_pct",
    ))


# ---- socket registry --------------------------------------------------------

def _register(wall_id: int, key: Tuple[int, int], ws, screen_id: int, token: str) -> None:
    _connections[wall_id][key] = ws
    _conn_identity[ws] = (screen_id, token)


def _unregister(wall_id: int, key: Tuple[int, int], ws) -> None:
    """Forget ``ws`` -- but never a newer socket that replaced it."""
    conns = _connections.get(wall_id)
    if conns is not None and conns.get(key) is ws:
        conns.pop(key, None)
    _conn_identity.pop(ws, None)


async def _send_safe(ws: WebSocket, frame: dict) -> bool:
    try:
        await ws.send_text(json.dumps(frame))
        return True
    except Exception:
        return False


async def _close_quietly(ws, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


async def broadcast(wall_id: int, frame: dict, exclude: Tuple[int, int] | None = None) -> None:
    for key, ws in list(_connections.get(wall_id, {}).items()):
        if exclude and key == exclude:
            continue
        if not await _send_safe(ws, frame):
            _unregister(wall_id, key, ws)


def _bye_frame(reason: str) -> dict:
    return {"type": "bye", "reason": reason, "server_now_ms": now_ms()}


def _keep(task: asyncio.Task) -> None:
    _background.add(task)
    task.add_done_callback(_background.discard)


def _run_on_server_loop(coro) -> bool:
    """Start ``coro`` on the loop that owns the sockets, from any thread.

    REST handlers that call in here are sync FastAPI endpoints, so they run in
    an AnyIO worker thread where there is no current event loop:
    asyncio.get_event_loop() raises there, which used to be swallowed and the
    frame silently dropped.
    """
    try:
        _keep(asyncio.get_running_loop().create_task(coro))
        return True
    except RuntimeError:
        pass                               # not on an event-loop thread
    try:
        anyio.from_thread.run_sync(lambda: _keep(asyncio.get_running_loop().create_task(coro)))
        return True
    except RuntimeError:
        pass                               # not an AnyIO worker thread either
    loop = _server_loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
        return True
    coro.close()
    return False


def broadcast_bye(wall_id: int, row: int, col: int, reason: str) -> None:
    """Sync entry-point used by REST handlers (e.g. unpair). Best-effort.

    Only reaches a socket held by this worker; the tick loop of whichever
    worker holds it otherwise notices the unpair within TICK_MAX_SLEEP_S.
    """
    ws = _connections.get(wall_id, {}).get((row, col))
    if not ws:
        return

    async def _say_bye():
        await _send_safe(ws, _bye_frame(reason))
        await _close_quietly(ws, 4000)

    _run_on_server_loop(_say_bye())


# ---- playlist loading -------------------------------------------------------

def _playlist_signature(wall_id: int) -> str:
    rows = query_all(
        """SELECT pi.id, pi.media_id, pi.duration_seconds,
                  COALESCE(pi.duration_override_seconds, pi.duration_seconds) AS dur,
                  COALESCE(pi.fit_mode, 'fit') AS fit_mode, pi.position
           FROM playlist_items pi
           JOIN walls w ON (w.mirrored_playlist_id = pi.playlist_id
                          OR w.spanned_playlist_id  = pi.playlist_id)
           WHERE w.id = ?
           ORDER BY pi.position""",
        (wall_id,),
    )
    blob = "|".join(f"{r['id']}:{r['media_id']}:{r['dur']}:{r['fit_mode']}" for r in rows)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def _with_urls(rows: list[dict]) -> list[dict]:
    for it in rows:
        if it["mime_type"] == "text/url":
            it["url"] = it["filename"]
        else:
            it["url"] = f"/uploads/{it['filename']}"
    return rows


def _load_same_playlist_items(wall_id: int) -> list[dict]:
    # The org joins here and in _load_per_cell_items are a backstop: writes
    # already refuse another org's playlist (main.require_org_rows), but this
    # is what reaches the TVs, so it must never follow a bad reference.
    rows = query_all(
        """SELECT pi.id, pi.media_id,
                  COALESCE(pi.duration_override_seconds, pi.duration_seconds) AS duration_seconds,
                  pi.position, m.name, m.filename, m.mime_type
           FROM playlist_items pi
           JOIN walls w ON w.mirrored_playlist_id = pi.playlist_id
           JOIN playlists p ON p.id = pi.playlist_id
                           AND p.organization_id = w.organization_id
           JOIN media m ON m.id = pi.media_id
           WHERE w.id = ?
           ORDER BY pi.position, pi.id""",
        (wall_id,),
    )
    return _with_urls(rows)


def _load_per_cell_items(wall_id: int) -> dict[tuple[int, int], list[dict]]:
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (wall_id,))
    out: dict[tuple[int, int], list[dict]] = {}
    for cell in cells:
        if not cell["playlist_id"]:
            out[(cell["row_index"], cell["col_index"])] = []
            continue
        rows = query_all(
            """SELECT pi.id, pi.media_id,
                      COALESCE(pi.duration_override_seconds, pi.duration_seconds) AS duration_seconds,
                      pi.position, m.name, m.filename, m.mime_type
               FROM playlist_items pi JOIN media m ON m.id = pi.media_id
               JOIN playlists p ON p.id = pi.playlist_id
               WHERE pi.playlist_id = ?
                 AND p.organization_id = (SELECT organization_id FROM walls WHERE id = ?)
               ORDER BY pi.position, pi.id""",
            (cell["playlist_id"], wall_id),
        )
        out[(cell["row_index"], cell["col_index"])] = _with_urls(rows)
    return out


def _load_canvas_items(wall_id: int, wall: dict) -> list[dict]:
    """Load the spanned wall's canvas-playlist items.

    PDF items expand to one pseudo-item per rendered PNG page; the URL
    points to the rasterized PNG for the wall's canvas resolution.
    Each pseudo-item carries the parent's duration_seconds (override-aware)
    and fit_mode.
    """
    if not wall.get("spanned_playlist_id"):
        return []
    rows = query_all(
        """SELECT pi.id, pi.media_id, pi.position,
                  COALESCE(pi.duration_override_seconds, pi.duration_seconds, 5) AS dur,
                  COALESCE(pi.fit_mode, 'fit') AS fit_mode,
                  m.name, m.filename, m.mime_type
           FROM playlist_items pi JOIN media m ON m.id = pi.media_id
           WHERE pi.playlist_id = ?
           ORDER BY pi.position ASC, pi.id ASC""",
        (wall["spanned_playlist_id"],),
    )
    from pathlib import Path
    import os
    upload_dir = os.getenv("UPLOAD_DIR", "./uploads")
    expanded: list[dict] = []
    for r in rows:
        if r["mime_type"] == "application/pdf":
            page_dir = (Path(upload_dir) / "pdf_pages" / str(r["media_id"])
                        / f"canvas_{wall['canvas_width_px']}x{wall['canvas_height_px']}")
            if not page_dir.exists():
                continue  # rasterization not done yet
            page_files = sorted(p.name for p in page_dir.iterdir() if p.suffix == ".png")
            for page_name in page_files:
                expanded.append({
                    "id": f"{r['id']}#{page_name}",
                    "url": f"/uploads/pdf_pages/{r['media_id']}/"
                           f"canvas_{wall['canvas_width_px']}x{wall['canvas_height_px']}/{page_name}",
                    "mime_type": "image/png",
                    "name": f"{r['name']} ({page_name})",
                    "duration_seconds": r["dur"],
                    "fit_mode": r["fit_mode"],
                })
        else:
            expanded.append({
                "id": r["id"],
                "url": f"/uploads/{r['filename']}",
                "mime_type": r["mime_type"],
                "name": r["name"],
                "duration_seconds": r["dur"],
                "fit_mode": r["fit_mode"],
            })
    return expanded


# ---- timeline ---------------------------------------------------------------

def _seconds_to_ms(seconds) -> int:
    try:
        value = float(seconds) if seconds is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        value = DEFAULT_ITEM_SECONDS
    return int(round(value * 1000))


def _item_ms(item: dict) -> int:
    return _seconds_to_ms(item.get("duration_seconds"))


def synced_rotation_slot_durations(durations_per_cell: list[list[int]]) -> list[int]:
    """Slot i's duration (ms) = max over cells of that cell's item for slot i.

    Cells without items are not part of the rotation. A cell with fewer items
    than the longest one repeats its own list (item i % len), so a 2x2 wall
    with one unconfigured cell, or unequal playlists, still rotates.
    """
    cells = [c for c in durations_per_cell if c]
    if not cells:
        return []
    n = max(len(c) for c in cells)
    return [max(_seconds_to_ms(c[i % len(c)]) for c in cells) for i in range(n)]


def timeline_slot(slot_ms: list[int], at_ms: int,
                  epoch_ms: int = TIMELINE_EPOCH_MS) -> tuple[int, int, int]:
    """Which slot of a repeating timeline is on at ``at_ms``.

    Returns (index, slot_started_at_ms, slot_ends_at_ms). Pure: any worker
    asking about the same playlist at the same instant gets the same answer.
    """
    total = sum(slot_ms)
    if not slot_ms or total <= 0:
        raise ValueError("timeline needs at least one positive slot")
    offset = (at_ms - epoch_ms) % total
    cycle_start = at_ms - offset
    acc = 0
    for i, dur in enumerate(slot_ms):
        if offset < acc + dur:
            return i, cycle_start + acc, cycle_start + acc + dur
        acc += dur
    # Unreachable (offset < total), kept for type-checkers.
    return len(slot_ms) - 1, cycle_start + total - slot_ms[-1], cycle_start + total


def _wall_plan(wall: dict, at_ms: int) -> dict | None:
    """What each cell of ``wall`` shows at ``at_ms``, or None if nothing can play.

    {"signature", "started_at_ms", "ends_at_ms",
     "shared": item for every cell | None, "per_cell": {(r, c): item}}
    """
    shared = None
    per_cell: dict = {}
    if wall["mode"] == "spanned":
        items = _load_canvas_items(wall["id"], wall)
        if not items:
            return None
        idx, start, end = timeline_slot([_item_ms(i) for i in items], at_ms)
        shared = items[idx]
    elif wall["mode"] == "mirrored" and wall.get("mirrored_mode") == "same_playlist":
        items = _load_same_playlist_items(wall["id"])
        if not items:
            return None
        idx, start, end = timeline_slot([_item_ms(i) for i in items], at_ms)
        shared = items[idx]
    elif wall["mode"] == "mirrored" and wall.get("mirrored_mode") == "synced_rotation":
        by_cell = {k: v for k, v in _load_per_cell_items(wall["id"]).items() if v}
        if not by_cell:
            return None
        slots = synced_rotation_slot_durations(
            [[x["duration_seconds"] for x in v] for v in by_cell.values()])
        idx, start, end = timeline_slot(slots, at_ms)
        per_cell = {k: v[idx % len(v)] for k, v in by_cell.items()}
    else:
        return None
    return {"signature": _playlist_signature(wall["id"]), "started_at_ms": start,
            "ends_at_ms": end, "shared": shared, "per_cell": per_cell}


def _build_play_frame(item: dict, started_at_ms: int, signature: str,
                      duration_ms: int | None = None) -> dict:
    return {
        "type": "play",
        "item": {"id": item["id"], "url": item["url"],
                 "mime_type": item["mime_type"], "name": item["name"]},
        "started_at_ms": started_at_ms,
        "duration_ms": duration_ms if duration_ms is not None else _item_ms(item),
        "playlist_signature": signature,
        "fit_mode": item.get("fit_mode", "fit"),
        "server_now_ms": now_ms(),
    }


def _frame_for(plan: dict | None, key: Tuple[int, int]) -> dict | None:
    if not plan:
        return None
    item = plan["shared"] if plan["shared"] is not None else plan["per_cell"].get(key)
    if item is None:
        return None
    return _build_play_frame(item, plan["started_at_ms"], plan["signature"],
                             duration_ms=plan["ends_at_ms"] - plan["started_at_ms"])


def current_play_for(wall_id: int, cell: dict) -> dict | None:
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        return None
    return _frame_for(_wall_plan(wall, now_ms()), (cell["row_index"], cell["col_index"]))


# ---- tick loop --------------------------------------------------------------

def _cell_bindings(wall_id: int) -> dict:
    """(row, col) -> (screen_id, token, screen.wall_cell_id, cell.id) for the wall's cells."""
    rows = query_all(
        """SELECT c.id AS cell_id, c.row_index, c.col_index, c.screen_id,
                  s.token, s.wall_cell_id
           FROM wall_cells c LEFT JOIN screens s ON s.id = c.screen_id
           WHERE c.wall_id = ?""",
        (wall_id,),
    )
    return {(r["row_index"], r["col_index"]): r for r in rows}


async def _drop_unbound(wall_id: int, conns: dict) -> None:
    """Tell sockets whose screen no longer owns their cell, and drop them.

    "bye" (the TV forgets its token and shows a pairing code) when the
    screen's identity is gone -- deleted, re-paired onto another TV, token
    rotated. "wall_left" when the screen itself still exists but is no longer
    part of this wall, so the TV falls back to its own content.
    """
    known = {k: ws for k, ws in conns.items() if ws in _conn_identity}
    if not known:
        return
    bindings = _cell_bindings(wall_id)
    for key, ws in known.items():
        screen_id, token = _conn_identity[ws]
        b = bindings.get(key)
        if b and b["screen_id"] == screen_id and b["token"] == token \
                and b["wall_cell_id"] == b["cell_id"]:
            continue
        screen = query_one("SELECT token FROM screens WHERE id = ?", (screen_id,))
        if screen is None or screen["token"] != token:
            await _send_safe(ws, _bye_frame("cell_unpaired"))
        else:
            await _send_safe(ws, {"type": "wall_left", "server_now_ms": now_ms()})
        _unregister(wall_id, key, ws)
        await _close_quietly(ws, 4000)


async def _tick_once(wall_id: int, state: dict) -> float | None:
    """One pass of the tick loop. Returns seconds to sleep, or None to stop."""
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        # Its cells are gone with it; the TVs' screens survive as standalone.
        for key, ws in list(_connections.get(wall_id, {}).items()):
            await _send_safe(ws, {"type": "wall_left", "server_now_ms": now_ms()})
            _unregister(wall_id, key, ws)
            await _close_quietly(ws, 4000)
        return None
    await _drop_unbound(wall_id, dict(_connections.get(wall_id, {})))
    conns = dict(_connections.get(wall_id, {}))
    if not conns:
        return None

    now = now_ms()
    plan = _wall_plan(wall, now)
    sent: dict = state.setdefault("sent", {})
    config = _hello_config(wall)
    if state.get("config") not in (None, config):
        # Mode / grid / canvas changed under connected TVs: re-introduce the
        # wall so they rebuild their geometry instead of mis-cropping.
        sent.clear()
        for (r, c), ws in conns.items():
            hello = _hello_frame(wall, {"row_index": r, "col_index": c},
                                 _frame_for(plan, (r, c)))
            if not await _send_safe(ws, hello):
                _unregister(wall_id, (r, c), ws)
            elif hello["current_play"]:
                cp = hello["current_play"]
                sent[(r, c)] = (cp["item"]["id"], cp["started_at_ms"])
    state["config"] = config

    for key, ws in conns.items():
        frame = _frame_for(plan, key)
        if frame is None:
            continue
        mark = (frame["item"]["id"], frame["started_at_ms"])
        if sent.get(key) == mark:
            continue
        if await _send_safe(ws, frame):
            sent[key] = mark
        else:
            _unregister(wall_id, key, ws)

    if now - state.get("last_ping_ms", 0) >= PING_INTERVAL_MS:
        state["last_ping_ms"] = now
        await broadcast(wall_id, {"type": "ping", "server_now_ms": now})

    if plan:
        until_next = (plan["ends_at_ms"] - now_ms()) / 1000.0
    else:
        until_next = TICK_MAX_SLEEP_S
    return max(TICK_MIN_SLEEP_S, min(TICK_MAX_SLEEP_S, until_next))


async def _tick_loop(wall_id: int):
    """One asyncio task per wall with sockets on this worker.

    Wakes at each slot boundary (and at least every TICK_MAX_SLEEP_S) and sends
    each local socket the frame for the current slot if it has not had it.
    An exception in one pass is logged and retried: a dead loop would freeze
    the wall until some TV happened to reconnect.
    """
    state: dict = {}
    while True:
        try:
            delay = await _tick_once(wall_id, state)
        except Exception:
            logger.exception("wall_tick_failed wall_id=%s", wall_id)
            delay = TICK_ERROR_BACKOFF_S
        if delay is None:
            return
        await asyncio.sleep(delay)


async def _ensure_tick_loop(wall_id: int) -> None:
    if wall_id in _tick_tasks and not _tick_tasks[wall_id].done():
        return
    _tick_tasks[wall_id] = asyncio.create_task(_tick_loop(wall_id))


@router.websocket("/walls/{wall_id}/ws")
async def wall_ws(websocket: WebSocket, wall_id: int,
                  screen_token: str = Query(..., min_length=8)):
    global _server_loop
    _server_loop = asyncio.get_running_loop()
    info = _wall_for_token(wall_id, screen_token)
    if info is None:
        # Accept first: a close before accept becomes an HTTP 403 on the
        # upgrade, which browsers report as 1006 -- indistinguishable from a
        # network blip -- so the player never learned it had left the wall.
        await websocket.accept()
        await websocket.close(code=4401)
        return
    wall, cell, screen = info
    await websocket.accept()
    key = (cell["row_index"], cell["col_index"])
    old = _connections[wall_id].get(key)
    if old is not None:
        _unregister(wall_id, key, old)
        await _close_quietly(old, 4000)
    _register(wall_id, key, websocket, screen["id"], screen_token)

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
        _unregister(wall_id, key, websocket)
        if not _connections.get(wall_id):
            _connections.pop(wall_id, None)
            t = _tick_tasks.pop(wall_id, None)
            if t and not t.done():
                t.cancel()


def attach_walls(app: FastAPI) -> None:
    app.include_router(router)
