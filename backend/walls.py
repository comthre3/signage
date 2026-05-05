"""Wall WebSocket fanout + lazy per-wall tick loop registry.

This module assumes a single-uvicorn-worker deployment. The connection
registry and tick-loop tasks live in process memory; if we ever scale
out workers, every wall's WebSocket connections must land on the same
worker (sticky routing) OR we must add a Redis pub/sub layer between
workers. Out of scope for v1.
"""

import asyncio
import hashlib
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
# wall_id -> dict carrying timeline state
_timeline_state: Dict[int, dict] = {}


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


def _load_same_playlist_items(wall_id: int) -> list[dict]:
    rows = query_all(
        """SELECT pi.id, pi.media_id, pi.duration_seconds, pi.position,
                  m.name, m.filename, m.mime_type
           FROM playlist_items pi
           JOIN walls w ON w.mirrored_playlist_id = pi.playlist_id
           JOIN media m ON m.id = pi.media_id
           WHERE w.id = ?
           ORDER BY pi.position""",
        (wall_id,),
    )
    for it in rows:
        if it["mime_type"] == "text/url":
            it["url"] = it["filename"]
        else:
            it["url"] = f"/uploads/{it['filename']}"
    return rows


def _load_per_cell_items(wall_id: int) -> dict[tuple[int, int], list[dict]]:
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (wall_id,))
    out: dict[tuple[int, int], list[dict]] = {}
    for cell in cells:
        if not cell["playlist_id"]:
            out[(cell["row_index"], cell["col_index"])] = []
            continue
        rows = query_all(
            """SELECT pi.id, pi.media_id, pi.duration_seconds, pi.position,
                      m.name, m.filename, m.mime_type
               FROM playlist_items pi JOIN media m ON m.id = pi.media_id
               WHERE pi.playlist_id = ? ORDER BY pi.position""",
            (cell["playlist_id"],),
        )
        for it in rows:
            if it["mime_type"] == "text/url":
                it["url"] = it["filename"]
            else:
                it["url"] = f"/uploads/{it['filename']}"
        out[(cell["row_index"], cell["col_index"])] = rows
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


def synced_rotation_slot_durations(durations_per_cell: list[list[int]]) -> list[int]:
    """Slot i's duration (ms) = max over cells of items[i].duration * 1000."""
    if not durations_per_cell:
        return []
    n = len(durations_per_cell[0])
    return [max(c[i] for c in durations_per_cell) * 1000 for i in range(n)]


def _build_play_frame(item: dict, started_at_ms: int, signature: str) -> dict:
    return {
        "type": "play",
        "item": {"id": item["id"], "url": item["url"],
                 "mime_type": item["mime_type"], "name": item["name"]},
        "started_at_ms": started_at_ms,
        "duration_ms": item["duration_seconds"] * 1000,
        "playlist_signature": signature,
        "fit_mode": item.get("fit_mode", "fit"),
        "server_now_ms": now_ms(),
    }


def current_play_for(wall_id: int, cell: dict) -> dict | None:
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        return None
    sig = _playlist_signature(wall_id)
    state = _timeline_state.get(wall_id)
    started_at_ms = state["item_started_at_ms"] if state else now_ms()
    index = state["index"] if state else 0

    if wall["mode"] == "mirrored":
        if wall["mirrored_mode"] == "same_playlist":
            items = _load_same_playlist_items(wall_id)
            if not items:
                return None
            return _build_play_frame(items[index % len(items)], started_at_ms, sig)
        if wall["mirrored_mode"] == "synced_rotation":
            items_by_cell = _load_per_cell_items(wall_id)
            my = items_by_cell.get((cell["row_index"], cell["col_index"]), [])
            if not my:
                return None
            return _build_play_frame(my[index % len(my)], started_at_ms, sig)
        return None
    if wall["mode"] == "spanned":
        items = _load_canvas_items(wall_id, wall)
        if not items:
            return None
        return _build_play_frame(items[index % len(items)], started_at_ms, sig)
    return None


async def _tick_loop(wall_id: int):
    """One asyncio task per active wall.

    same_playlist: walks shared timeline; broadcasts play to all cells.
    synced_rotation: per-cell items, slot duration = max over cells.
    """
    try:
        while True:
            wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
            if not wall:
                return
            sig = _playlist_signature(wall_id)
            if wall["mode"] == "spanned":
                items = _load_canvas_items(wall_id, wall)
                if not items:
                    await asyncio.sleep(2)
                    continue
                state = _timeline_state.setdefault(
                    wall_id, {"index": 0, "item_started_at_ms": now_ms()})
                idx = state["index"] % len(items)
                state["item_started_at_ms"] = now_ms()
                frame = _build_play_frame(items[idx], state["item_started_at_ms"], sig)
                await broadcast(wall_id, frame)
                await asyncio.sleep(items[idx]["duration_seconds"])
                state["index"] = (state["index"] + 1) % len(items)
            elif wall["mirrored_mode"] == "same_playlist":
                items = _load_same_playlist_items(wall_id)
                if not items:
                    await asyncio.sleep(2)
                    continue
                state = _timeline_state.setdefault(
                    wall_id, {"index": 0, "item_started_at_ms": now_ms()})
                idx = state["index"] % len(items)
                state["item_started_at_ms"] = now_ms()
                frame = _build_play_frame(items[idx], state["item_started_at_ms"], sig)
                await broadcast(wall_id, frame)
                await asyncio.sleep(items[idx]["duration_seconds"])
                state["index"] = (state["index"] + 1) % len(items)
            elif wall["mirrored_mode"] == "synced_rotation":
                items_by_cell = _load_per_cell_items(wall_id)
                if not items_by_cell:
                    await asyncio.sleep(2)
                    continue
                lengths = {k: len(v) for k, v in items_by_cell.items() if v}
                if not lengths or len(set(lengths.values())) != 1:
                    await asyncio.sleep(5)
                    continue
                n = next(iter(lengths.values()))
                state = _timeline_state.setdefault(
                    wall_id, {"index": 0, "item_started_at_ms": now_ms()})
                idx = state["index"] % n
                state["item_started_at_ms"] = now_ms()
                slot_durations = synced_rotation_slot_durations(
                    [[x["duration_seconds"] for x in v] for v in items_by_cell.values()]
                )
                for (r, c), ws in list(_connections[wall_id].items()):
                    items = items_by_cell.get((r, c), [])
                    if not items:
                        continue
                    frame = _build_play_frame(items[idx], state["item_started_at_ms"], sig)
                    await _send_safe(ws, frame)
                await asyncio.sleep(slot_durations[idx] / 1000.0)
                state["index"] = (state["index"] + 1) % n
            else:
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        return


async def _ensure_tick_loop(wall_id: int) -> None:
    if wall_id in _tick_tasks and not _tick_tasks[wall_id].done():
        return
    _tick_tasks[wall_id] = asyncio.create_task(_tick_loop(wall_id))


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
