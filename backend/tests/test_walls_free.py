"""Video walls set up from a phone ("free" walls).

POST /walls/free starts a wall from the first TV's pairing QR and a video;
POST /walls/{id}/tvs adds the next TV. Each TV is the ordinary standalone
pairing code (/screens/request_code): once the phone claims it, the TV's own
poll hands it its token and /screens/{token}/content sends it into wall mode.
Boxes are fractions of the canvas, which is the video's size.
"""
import math
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import psycopg
import ratelimit
import redis
import wall_layout
import walls as walls_mod
from db import execute, get_dsn, query_all, query_one, utc_now_iso
from psycopg.rows import dict_row
from main import app, hash_password


def _org(screen_limit=10):
    slug = "fw" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"FW {slug}", slug, "active", screen_limit, utc_now_iso()),
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


def _tv_code(client):
    """The code a TV in pairing mode shows in its QR."""
    res = client.post("/screens/request_code", json={})
    assert res.status_code == 200, res.text
    return res.json()["code"]


def _media(oid, width=5760, height=1080, duration=20.0, mime="video/mp4"):
    """A media row as upload_media leaves it once probed (no file needed)."""
    ext = {"image/png": ".png", "application/pdf": ".pdf"}.get(mime, ".mp4")
    return execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, "
        "width_px, height_px, duration_seconds, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "clip", secrets.token_hex(8) + ext, mime, 100, width, height, duration,
         utc_now_iso()),
    )


def _create(client, headers, media_id, code, name="Lobby"):
    return client.post("/walls/free", json={"name": name, "media_id": media_id, "code": code},
                       headers=headers)


def _free_wall(client, headers, oid, name="Lobby", **media):
    res = _create(client, headers, _media(oid, **media), _tv_code(client), name)
    assert res.status_code == 201, res.text
    return res.json()


def _add_tv(client, headers, wall_id, code=None):
    return client.post(f"/walls/{wall_id}/tvs", json={"code": code or _tv_code(client)},
                       headers=headers)


def _grid_wall(client, headers):
    res = client.post("/walls", json={
        "name": "Grid", "mode": "spanned", "rows": 1, "cols": 2,
        "canvas_width_px": 1920, "canvas_height_px": 1080}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _cells(wall_id):
    return query_all("SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY col_index", (wall_id,))


def _cols(wall_id):
    return query_one("SELECT cols FROM walls WHERE id = ?", (wall_id,))["cols"]


def _items(client, headers, wall_id):
    res = client.get(f"/walls/{wall_id}/canvas-playlist", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["items"]


def _code_status(code):
    return query_one("SELECT status FROM pairing_codes WHERE code = ?", (code,))["status"]


def _left_behind(oid):
    """Rows a refused request must not have created."""
    return {t: query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE organization_id = ?",
                         (oid,))["n"]
            for t in ("walls", "playlists", "screens")}


def _unknown(client):
    return "000000"          # 0 is not in PAIR_CODE_CHARSET: no TV can show it


def _expired(client):
    code = _tv_code(client)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    return code


def _used(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET status = 'paired' WHERE code = ?", (code,))
    return code


BAD_CODES = [
    (_unknown, 404, "pair.code_unknown"),
    (_expired, 400, "pair.code_expired"),
    (_used, 409, "pair.code_used"),
]


def _limits(func_name):
    """The rate limits registered on main.<func_name>. Tests run with
    RATE_LIMITS_ENABLED=0, so no request ever hits one: assert the registration."""
    return [str(lim.limit) for lim in main.limiter._route_limits.get(f"main.{func_name}", [])]


# ---- POST /walls/free ----


def test_create_binds_the_first_tv_and_its_poll_returns_the_token(client):
    oid, h = _org()
    mid = _media(oid, width=5760, height=1080, duration=19.2)
    code = _tv_code(client)
    res = _create(client, h, mid, code, name="Lobby wall")
    assert res.status_code == 201, res.text
    wall = res.json()
    assert (wall["mode"], wall["layout"], wall["rows"], wall["cols"]) == ("spanned", "free", 1, 1)
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == (5760, 1080)
    assert (wall["bezel_h_pct"], wall["bezel_v_pct"]) == (0, 0)
    [cell] = wall["cells"]
    assert (cell["row_index"], cell["col_index"]) == (0, 0)
    assert cell["screen_id"] and cell["online"] is False
    filename = query_one("SELECT filename FROM media WHERE id = ?", (mid,))["filename"]
    assert wall["video"] == {"media_id": mid, "name": "clip", "url": f"/uploads/{filename}",
                             "mime_type": "video/mp4", "width_px": 5760, "height_px": 1080}
    assert [(i["media_id"], i["duration_seconds"], i["fit_mode"])
            for i in _items(client, h, wall["id"])] == [(mid, 20, "fit")]

    # The TV, still polling its QR's code, collects its token ...
    poll = client.get(f"/screens/poll/{code}").json()
    assert poll["status"] == "paired"
    assert poll["screen_id"] == cell["screen_id"]
    assert poll["screen_name"] == "Lobby wall · TV 1"
    # ... and its content sends it into wall mode.
    content = client.get(f"/screens/{poll['screen_token']}/content").json()
    assert content["wall_id"] == wall["id"]
    assert content["wall_cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 1}
    assert content["wall_mode"] == "spanned"
    # The poll was the TV checking in.
    assert client.get(f"/walls/{wall['id']}", headers=h).json()["cells"][0]["online"] is True


def test_first_box_is_the_left_third_of_a_5760x1080_video(client):
    oid, h = _org()
    [cell] = _free_wall(client, h, oid, width=5760, height=1080)["cells"]
    # Full height at the left edge, 16:9 until the TV reports: 1920 of 5760 px.
    assert (cell["place_x"], cell["place_y"]) == (0, 0)
    assert cell["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_h"] == pytest.approx(1.0)


@pytest.mark.parametrize("width, height, duration, mime, canvas, seconds", [
    (5760, 1080, 19.2, "video/mp4", (5760, 1080), 20),
    (1920, 1080, None, "image/png", (1920, 1080), 10),
    (None, None, None, "video/mp4", (3840, 2160), 10),     # probe failed: 4K, 10 s
    (1080, 1920, 6.0, "video/mp4", (1080, 1920), 6),       # portrait: the box is wider than it
])
def test_create_sizes_the_canvas_and_item_from_the_media(client, width, height, duration,
                                                         mime, canvas, seconds):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=width, height=height, duration=duration, mime=mime)
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == canvas
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [seconds]
    cell = wall["cells"][0]
    w = wall_layout.DEFAULT_TV_ASPECT * canvas[1] / canvas[0]
    assert cell["place_w"] == pytest.approx(w, abs=1e-6)
    # R1: a box wider than the video is centred on it; otherwise it starts at the left edge.
    assert cell["place_x"] == pytest.approx((1 - w) / 2 if w > 1 else 0.0, abs=1e-6)


@pytest.mark.parametrize("make_code, status, err", BAD_CODES)
def test_create_refuses_a_bad_code_and_leaves_nothing(client, make_code, status, err):
    oid, h = _org()
    res = _create(client, h, _media(oid), make_code(client))
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == err
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}


def test_create_refuses_another_orgs_media(client):
    oid, h = _org()
    other, _other_h = _org()
    code = _tv_code(client)
    res = _create(client, h, _media(other), code)
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "media.not_found"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}


def test_create_refuses_a_pdf(client):
    oid, h = _org()
    pdf = _media(oid, width=None, height=None, duration=None, mime="application/pdf")
    res = _create(client, h, pdf, _tv_code(client))
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "wall.media_type_blocked"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}


def test_create_at_the_plan_limit_is_402_and_leaves_nothing(client):
    oid, h = _org(screen_limit=1)
    execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (oid, "Front desk", secrets.token_hex(3), "tok_" + secrets.token_hex(8), utc_now_iso()),
    )
    mid = _media(oid)
    code = _tv_code(client)
    res = _create(client, h, mid, code)
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "plan_limit"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 1}
    # Refused, not consumed: the TV keeps waiting, and the code works after an upgrade.
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}
    execute("UPDATE organizations SET screen_limit = 2 WHERE id = ?", (oid,))
    assert _create(client, h, mid, code).status_code == 201


# ---- POST /walls/{id}/tvs ----


def test_next_tv_takes_the_next_index_flush_right_of_the_last(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, name="Lobby")
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 201, res.text
    cell = res.json()
    assert (cell["row_index"], cell["col_index"]) == (0, 1)
    assert cell["place_x"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_y"] == 0
    assert cell["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_h"] == pytest.approx(1.0)
    assert cell["online"] is False
    poll = client.get(f"/screens/poll/{code}").json()
    assert (poll["status"], poll["screen_id"], poll["screen_name"]) == (
        "paired", cell["screen_id"], "Lobby · TV 2")

    third = _add_tv(client, h, wall["id"])
    assert third.status_code == 201, third.text
    assert third.json()["col_index"] == 2
    assert third.json()["place_x"] == pytest.approx(2 / 3, abs=1e-6)
    after = client.get(f"/walls/{wall['id']}", headers=h).json()
    assert after["cols"] == 3
    assert [c["col_index"] for c in after["cells"]] == [0, 1, 2]


def test_next_tv_takes_the_last_boxs_height_and_top_in_16_9(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    execute("UPDATE wall_cells SET place_x = 0.1, place_y = 0.2, place_w = 0.25, place_h = 0.5 "
            "WHERE wall_id = ?", (wall["id"],))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    # R3: the last box's height (540 px) in the default 16:9 shape (960 px of
    # 5760), flush to its right edge (0.1 + 0.25) and top-aligned.
    assert box == pytest.approx({"x": 0.35, "y": 0.2, "w": 1 / 6, "h": 0.5}, abs=1e-6)
    assert wall_layout.has_default_shape(box, 5760, 1080)


def test_next_to_a_portrait_tv_the_new_box_is_16_9(client):
    """R3: the new TV's box keeps the default shape, not its neighbour's, so
    the new TV's own screen report can still refit it (Task 6 refits only a
    box that has_default_shape)."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    # TV 1 reported a 1080x1920 (portrait) screen and was refit to it.
    portrait = wall_layout.fit_box_to_screen({"x": 0.0, "y": 0.0, "w": 1 / 3, "h": 1.0},
                                             1080, 1920, 5760, 1080)
    execute("UPDATE wall_cells SET place_x = ?, place_y = ?, place_w = ?, place_h = ? "
            "WHERE wall_id = ?",
            (portrait["x"], portrait["y"], portrait["w"], portrait["h"], wall["id"]))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    assert box["h"] == pytest.approx(portrait["h"], rel=1e-5)
    assert box["y"] == pytest.approx(portrait["y"], rel=1e-5)
    assert box["x"] == pytest.approx(portrait["x"] + portrait["w"], rel=1e-5)
    assert wall_layout.has_default_shape(box, 5760, 1080)
    assert not wall_layout.has_default_shape(portrait, 5760, 1080)


def test_next_tv_is_nudged_back_onto_the_video(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    # Flush right of this box would be wholly off the video (x = 1.2).
    execute("UPDATE wall_cells SET place_x = 0.9, place_y = 0.0, place_w = 0.3, place_h = 1.0 "
            "WHERE wall_id = ?", (wall["id"],))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    assert 0.9 < box["x"] < 1.0
    # Full height in the 16:9 shape (R3), whatever the last box's width was.
    assert (box["w"], box["h"]) == pytest.approx((1 / 3, 1.0), abs=1e-6)
    assert wall_layout.overlap_fraction(box) == pytest.approx(wall_layout.MIN_OVERLAP, abs=1e-4)


def test_ninth_tv_is_refused_wall_full(client):
    oid, h = _org(screen_limit=20)          # the plan must not be what stops it
    wall = _free_wall(client, h, oid)
    for expected in range(1, 8):
        res = _add_tv(client, h, wall["id"])
        assert res.status_code == 201, res.text
        assert res.json()["col_index"] == expected
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "wall.full"
    assert len(_cells(wall["id"])) == 8
    assert _cols(wall["id"]) == 8
    assert _code_status(code) == "pending"


def test_adding_to_a_grid_wall_is_refused_not_free(client):
    oid, h = _org()
    grid = _grid_wall(client, h)
    code = _tv_code(client)
    res = _add_tv(client, h, grid["id"], code)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "wall.not_free"
    assert len(_cells(grid["id"])) == 2
    assert _code_status(code) == "pending"


def test_adding_to_another_orgs_wall_is_404(client):
    oid, h = _org()
    _other, other_h = _org()
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    res = _add_tv(client, other_h, wall["id"], code)
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "wall.not_found"
    assert len(_cells(wall["id"])) == 1
    assert _code_status(code) == "pending"


@pytest.mark.parametrize("make_code, status, err", BAD_CODES)
def test_adding_with_a_bad_code_is_refused_and_leaves_nothing(client, make_code, status, err):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    res = _add_tv(client, h, wall["id"], make_code(client))
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == err
    assert [c["col_index"] for c in _cells(wall["id"])] == [0]
    assert _left_behind(oid)["screens"] == 1


def test_adding_past_the_plan_limit_is_402_and_leaves_nothing(client):
    oid, h = _org(screen_limit=1)
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "plan_limit"
    assert [c["col_index"] for c in _cells(wall["id"])] == [0]
    assert _cols(wall["id"]) == 1
    assert _left_behind(oid)["screens"] == 1
    assert _code_status(code) == "pending"


def _slow_token(monkeypatch):
    real = main.generate_unique_token

    def slow():
        time.sleep(0.4)        # widen the race window inside the transaction
        return real()

    monkeypatch.setattr(main, "generate_unique_token", slow)


def _add_tvs_concurrently(wall_id, headers, codes):
    results = []
    lock = threading.Lock()

    def go(code):
        res = TestClient(app).post(f"/walls/{wall_id}/tvs", json={"code": code},
                                   headers=headers)
        with lock:
            results.append(res)

    threads = [threading.Thread(target=go, args=(c,)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(results, key=lambda r: r.status_code)


def test_concurrent_tvs_with_two_codes_get_distinct_indexes(client, monkeypatch):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    codes = [_tv_code(client), _tv_code(client)]
    _slow_token(monkeypatch)
    results = _add_tvs_concurrently(wall["id"], h, codes)
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert sorted(r.json()["col_index"] for r in results) == [1, 2]
    assert [c["col_index"] for c in _cells(wall["id"])] == [0, 1, 2]
    assert _cols(wall["id"]) == 3


def test_concurrent_claims_of_one_code_pair_it_once(client, monkeypatch):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    _slow_token(monkeypatch)
    results = _add_tvs_concurrently(wall["id"], h, [code, code])
    assert [r.status_code for r in results] == [201, 409], [r.text for r in results]
    assert results[1].json()["detail"]["code"] == "pair.code_used"
    assert len(_cells(wall["id"])) == 2
    assert _left_behind(oid)["screens"] == 2


def test_a_cell_left_by_a_deleted_screen_is_reused(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    second = _add_tv(client, h, wall["id"]).json()
    assert _add_tv(client, h, wall["id"]).status_code == 201
    # Deleted from the Screens page: its cell stays behind with screen_id NULL.
    assert client.delete(f"/screens/{second['screen_id']}", headers=h).status_code == 200
    assert [c["screen_id"] is None for c in _cells(wall["id"])] == [False, True, False]

    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    assert res.json()["col_index"] == 1
    cells = _cells(wall["id"])
    assert [c["col_index"] for c in cells] == [0, 1, 2]
    assert all(c["screen_id"] for c in cells)
    assert _cols(wall["id"]) == 3


def test_create_and_add_tv_are_rate_limited():
    # Both claim whichever TV's pairing code they are given, so the limit is
    # what caps a script guessing codes (R4). One phone adds at most 8 TVs.
    assert _limits("create_free_wall") == ["30 per 1 minute"]
    assert _limits("add_wall_tv") == ["30 per 1 minute"]


# ---- after the transcode (D3) ----


def test_transcode_refresh_resizes_the_canvas_and_item(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=19.2)
    other = _free_wall(client, h, oid, width=1920, height=1080, duration=8.0)
    mid = wall["video"]["media_id"]
    grid = _grid_wall(client, h)
    res = client.post(f"/walls/{grid['id']}/canvas-playlist/items",
                      json={"media_id": mid, "position": 0}, headers=h)
    assert res.status_code == 201, res.text
    # What transcode_video's re-probe stores once the file is capped at 3840 px.
    execute("UPDATE media SET width_px = 3840, height_px = 720, duration_seconds = 20.4 "
            "WHERE id = ?", (mid,))

    main._refresh_free_walls_for_media(mid)

    after = client.get(f"/walls/{wall['id']}", headers=h).json()
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (3840, 720)
    # Boxes are fractions of the canvas: still the left third of the picture.
    assert after["cells"][0]["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [21]
    # A free wall playing other media, and a grid wall playing this one, are left alone.
    untouched = client.get(f"/walls/{other['id']}", headers=h).json()
    assert (untouched["canvas_width_px"], untouched["canvas_height_px"]) == (1920, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, other["id"])] == [8]
    grid_after = client.get(f"/walls/{grid['id']}", headers=h).json()
    assert (grid_after["canvas_width_px"], grid_after["canvas_height_px"]) == (1920, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, grid["id"])] == [5]


def test_transcode_refresh_skips_a_wall_swapped_to_another_video_meanwhile(client, monkeypatch):
    """The wall's video is swapped (PUT /video) after the refresh began but
    before its UPDATE. The refresh must not write the old file's canvas onto
    the wall that now plays another video: every box is a fraction of the
    canvas, and nothing would repair the crop."""
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=19.2)
    old_mid = wall["video"]["media_id"]
    new_mid = _media(oid, width=1920, height=1080, duration=8.0)
    execute("UPDATE media SET width_px = 3840, height_px = 720 WHERE id = ?", (old_mid,))
    real = wall_layout.canvas_size_for

    def swap_then_size(w, hh):
        # Lands in the window between the refresh's reads and its writes.
        execute("UPDATE playlist_items SET media_id = ?, duration_seconds = 8 "
                "WHERE playlist_id = ?", (new_mid, wall["spanned_playlist_id"]))
        return real(w, hh)

    monkeypatch.setattr(wall_layout, "canvas_size_for", swap_then_size)
    main._refresh_free_walls_for_media(old_mid)
    row = query_one("SELECT canvas_width_px, canvas_height_px FROM walls WHERE id = ?",
                    (wall["id"],))
    assert (row["canvas_width_px"], row["canvas_height_px"]) == (5760, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [8]


def test_transcode_refresh_writes_both_updates_in_one_transaction(client, monkeypatch):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=19.2)
    mid = wall["video"]["media_id"]
    execute("UPDATE media SET width_px = 3840, height_px = 720 WHERE id = ?", (mid,))
    real_execute = main.execute

    def failing_second(sql, params=()):
        if sql.startswith("UPDATE playlist_items"):
            raise RuntimeError("boom")
        return real_execute(sql, params)

    monkeypatch.setattr(main, "execute", failing_second)
    main._refresh_free_walls_for_media(mid)       # best effort: logged, never raised
    monkeypatch.setattr(main, "execute", real_execute)
    row = query_one("SELECT canvas_width_px FROM walls WHERE id = ?", (wall["id"],))
    assert row["canvas_width_px"] == 5760         # the canvas write rolled back with it


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg (in the backend image)")
def test_transcode_video_refreshes_free_walls_playing_it(client, tmp_path):
    oid, h = _org()
    src = tmp_path / "pano.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=5760x1080:rate=10", "-t", "1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True, timeout=120,
    )
    # As probed at upload. The 7 s stands in for a length the re-probe of the
    # 1 s output must replace.
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, "
        "width_px, height_px, duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "pano", src.name, "video/mp4", src.stat().st_size, 5760, 1080, 7.0,
         utc_now_iso()),
    )
    res = _create(client, h, mid, _tv_code(client))
    assert res.status_code == 201, res.text
    wall = res.json()
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == (5760, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [7]

    main.transcode_video(str(src), mid)

    media = query_one("SELECT * FROM media WHERE id = ?", (mid,))
    assert (media["width_px"], media["height_px"]) == (3840, 720)
    after = query_one("SELECT canvas_width_px, canvas_height_px FROM walls WHERE id = ?",
                      (wall["id"],))
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (3840, 720)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [
        max(1, math.ceil(media["duration_seconds"]))]


# ---- PUT /walls/{id}/cells/{row}/{col}/placement ----


@pytest.fixture
def published(monkeypatch):
    """Calls to walls.publish_geometry, recorded instead of sent."""
    calls = []
    monkeypatch.setattr(walls_mod, "publish_geometry",
                        lambda wall_id, row, col: calls.append((wall_id, row, col)))
    return calls


def _place(client, headers, wall_id, box, row=0, col=0):
    return client.put(f"/walls/{wall_id}/cells/{row}/{col}/placement", json=box,
                      headers=headers)


def _box(cell):
    return {"x": cell["place_x"], "y": cell["place_y"],
            "w": cell["place_w"], "h": cell["place_h"]}


def _committed_cell(wall_id, row, col):
    """The cell as another connection sees it: only what has been committed.
    The handler's own connection would also see its open transaction (R5)."""
    with psycopg.connect(get_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM wall_cells WHERE wall_id = %s AND row_index = %s AND col_index = %s",
            (wall_id, row, col),
        ).fetchone()


def test_placement_is_saved_highlighted_and_published(client, monkeypatch, published):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1_762_000_000_000)
    # Partly above and below the video is fine: that part shows black.
    res = _place(client, h, wall["id"], {"x": 0.25, "y": -0.1, "w": 0.4, "h": 1.2})
    assert res.status_code == 204, res.text
    assert res.content == b""
    cell = _cells(wall["id"])[0]
    assert _box(cell) == pytest.approx({"x": 0.25, "y": -0.1, "w": 0.4, "h": 1.2}, abs=1e-6)
    assert cell["highlight_until_ms"] == 1_762_000_000_000 + walls_mod.HIGHLIGHT_SECONDS * 1000
    assert published == [(wall["id"], 0, 0)]


def test_done_clears_the_highlight(client, published):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    box = {"x": 0.0, "y": 0.0, "w": 0.3, "h": 1.0}
    assert _place(client, h, wall["id"], box).status_code == 204
    assert _cells(wall["id"])[0]["highlight_until_ms"] is not None
    assert _place(client, h, wall["id"], {**box, "highlight": False}).status_code == 204
    assert _cells(wall["id"])[0]["highlight_until_ms"] is None
    assert published == [(wall["id"], 0, 0), (wall["id"], 0, 0)]


@pytest.mark.parametrize("box", [
    {"x": 1.0, "y": 0.0, "w": 0.5, "h": 1.0},      # right of the video
    {"x": 0.0, "y": -1.0, "w": 0.5, "h": 1.0},     # above it
    {"x": 0.99, "y": 0.0, "w": 1.0, "h": 1.0},     # 1% on it (< 5%)
    {"x": 0.0, "y": 0.0, "w": 0.0, "h": 1.0},      # w must be > 0
    {"x": 0.0, "y": 0.0, "w": -0.3, "h": 1.0},     # ... so not negative either
    {"x": 0.0, "y": 0.0, "w": 5.0, "h": 1.0},      # w must be <= 4
    {"x": 0.0, "y": 0.0, "w": 0.3, "h": 4.5},      # h must be <= 4
])
def test_invalid_boxes_are_422_and_change_nothing(client, published, box):
    """R2: WallPlacement has plain floats, so validate_box decides and every
    refusal carries the code the phone translates (no Pydantic list detail)."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    before = _cells(wall["id"])[0]
    res = _place(client, h, wall["id"], box)
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    after = _cells(wall["id"])[0]
    assert _box(after) == _box(before)
    assert after["highlight_until_ms"] == before["highlight_until_ms"]
    assert published == []


@pytest.mark.parametrize("body", [
    b'{"x": NaN, "y": 0, "w": 0.3, "h": 1}',
    b'{"x": 0, "y": 0, "w": Infinity, "h": 1}',
])
def test_a_non_finite_box_is_refused(client, published, body):
    # Python's json, and so FastAPI, accepts NaN and Infinity, and Pydantic
    # lets them into a float: validate_box refuses them, with the same code (R2).
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    res = client.put(f"/walls/{wall['id']}/cells/0/0/placement", content=body,
                     headers={**h, "Content-Type": "application/json"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    assert published == []


@pytest.mark.parametrize("body", [
    pytest.param(b'{"x": 0, "y": 0, "w": 1' + b"0" * 400 + b', "h": 1}', id="huge-int-w"),
    pytest.param(b'{"x": -1' + b"0" * 400 + b', "y": 0, "w": 0.3, "h": 1}', id="huge-negative-int-x"),
    pytest.param(b'{"x": 0, "y": 0, "w": 1e999, "h": 1}', id="float-overflowing-to-inf"),
])
def test_a_box_beyond_float_range_is_refused_with_the_same_code(client, published, body):
    """R2: a JSON integer too big for a float is refused like any other box
    that is not on the video (422 wall.placement_invalid), not with Pydantic's
    own list-detail 422 and not with a 500 from validate_box."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    before = _cells(wall["id"])[0]
    res = client.put(f"/walls/{wall['id']}/cells/0/0/placement", content=body,
                     headers={**h, "Content-Type": "application/json"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    assert _box(_cells(wall["id"])[0]) == _box(before)
    assert published == []


def test_whole_number_coordinates_are_accepted(client, published):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    res = _place(client, h, wall["id"], {"x": 0, "y": 0, "w": 1, "h": 1})
    assert res.status_code == 204, res.text
    assert _box(_cells(wall["id"])[0]) == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    assert published == [(wall["id"], 0, 0)]


def test_placement_keeps_the_tvs_reported_shape(client, published):
    """R8: once the TV has reported its screen, a box from a phone that has not
    caught up (16:9 here, for a portrait TV) is stored in the TV's shape, with
    the same width and centre. An invalid request is still refused: fitting it
    first would nudge it back onto the video."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    execute("UPDATE wall_cells SET screen_w_px = 1080, screen_h_px = 1920 WHERE wall_id = ?",
            (wall["id"],))
    res = _place(client, h, wall["id"], {"x": 0.25, "y": 0.0, "w": 1 / 3, "h": 1.0})
    assert res.status_code == 204, res.text
    stored = _box(_cells(wall["id"])[0])
    assert stored["w"] == pytest.approx(1 / 3, rel=1e-5)
    assert stored["x"] == pytest.approx(0.25, rel=1e-5)
    assert stored["y"] + stored["h"] / 2 == pytest.approx(0.5, abs=1e-5)
    assert (stored["w"] * 5760) / (stored["h"] * 1080) == pytest.approx(1080 / 1920, rel=1e-4)
    assert published == [(wall["id"], 0, 0)]
    # Wholly right of the video. Fitted first, it would be nudged back to 5% on it.
    res = _place(client, h, wall["id"], {"x": 1.0, "y": 0.0, "w": 0.5, "h": 1.0})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    assert _box(_cells(wall["id"])[0]) == stored
    assert published == [(wall["id"], 0, 0)]


def test_geometry_is_published_once_the_box_is_committed(client, monkeypatch):
    """R5: the local push and the other workers re-read the box from the
    database, so publish_geometry runs only after the write has committed."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    seen = []
    monkeypatch.setattr(walls_mod, "publish_geometry",
                        lambda wall_id, row, col: seen.append(_committed_cell(wall_id, row, col)))
    box = {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0}
    assert _place(client, h, wall["id"], box).status_code == 204
    assert len(seen) == 1
    assert _box(seen[0]) == pytest.approx(box, abs=1e-6)
    assert seen[0]["highlight_until_ms"] is not None


def test_placement_is_refused_on_grid_walls_other_orgs_and_missing_cells(client, published):
    oid, h = _org()
    _other, other_h = _org()
    wall = _free_wall(client, h, oid)
    grid = _grid_wall(client, h)
    before = _cells(wall["id"])[0]
    box = {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0}
    for url, headers, status, code in (
        (f"/walls/{grid['id']}/cells/0/0/placement", h, 409, "wall.not_free"),
        (f"/walls/{wall['id']}/cells/0/0/placement", other_h, 404, "wall.not_found"),
        (f"/walls/{wall['id']}/cells/0/5/placement", h, 404, "wall.cell_not_found"),
    ):
        res = client.put(url, json=box, headers=headers)
        assert (res.status_code, res.json()["detail"]["code"]) == (status, code), url
    # Nothing changed: not the free wall's box or highlight, not the grid's cells.
    after = _cells(wall["id"])[0]
    assert _box(after) == _box(before)
    assert after["highlight_until_ms"] == before["highlight_until_ms"]
    assert all(c["place_x"] is None and c["highlight_until_ms"] is None
               for c in _cells(grid["id"]))
    assert published == []


class _DeadRedis:
    """What ratelimit.get_redis() hands back while Redis is down: from_url
    never connects, so the first command is the one that fails."""

    def __init__(self):
        self.publishes = 0

    def publish(self, channel, message):
        self.publishes += 1
        raise redis.exceptions.ConnectionError("Error 111 connecting to redis:6379.")


@pytest.fixture
def fresh_redis_state():
    # publish_geometry may mark Redis unhealthy for 10 s; later tests must not inherit that.
    ratelimit.reset_for_tests()
    yield
    ratelimit.reset_for_tests()


def test_placement_still_204_when_redis_is_down(client, monkeypatch, fresh_redis_state):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    dead = _DeadRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: dead)
    res = _place(client, h, wall["id"], {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0})
    assert res.status_code == 204, res.text
    assert dead.publishes == 1          # the Redis path was tried, and its failure swallowed
    assert _cells(wall["id"])[0]["place_x"] == pytest.approx(0.5, abs=1e-6)
    # In the cooldown after a failure, get_redis() returns None: also fine.
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    res = _place(client, h, wall["id"], {"x": 0.6, "y": 0.0, "w": 0.3, "h": 1.0})
    assert res.status_code == 204, res.text
    assert _cells(wall["id"])[0]["place_x"] == pytest.approx(0.6, abs=1e-6)


def test_placement_and_pairing_rate_limits():
    # The phone keeps one PUT in flight while dragging: several a second.
    assert _limits("put_wall_placement") == ["1800 per 1 minute"]
    # Up to 8 TVs behind one venue NAT, each polling every 3 s while it waits
    # (8 x 20 = 160 a minute), plus the phone's code checks from the same NAT.
    assert _limits("poll_pair_code") == ["240 per 1 minute"]
    assert _limits("request_pair_code") == ["30 per 1 minute"]


# ---- PUT /walls/{id}/video and the canvas playlist ----


def test_video_swap_replaces_the_item_and_follows_the_new_size(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=20.0)
    [old_item] = _items(client, h, wall["id"])
    new_mid = _media(oid, width=1920, height=1080, duration=12.5)
    res = client.put(f"/walls/{wall['id']}/video", json={"media_id": new_mid}, headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["canvas_width_px"], body["canvas_height_px"]) == (1920, 1080)
    assert body["video"]["media_id"] == new_mid
    [item] = _items(client, h, wall["id"])
    # A new row, not an update: its new id makes the TVs switch at once.
    assert item["id"] != old_item["id"]
    assert (item["media_id"], item["duration_seconds"], item["fit_mode"]) == (new_mid, 13, "fit")
    # Boxes are fractions of the canvas, so each TV keeps its part of the picture.
    assert _box(body["cells"][0]) == _box(wall["cells"][0])


def test_video_swap_refuses_foreign_media_pdfs_other_orgs_and_grid_walls(client):
    oid, h = _org()
    other, other_h = _org()
    wall = _free_wall(client, h, oid)
    url = f"/walls/{wall['id']}/video"
    pdf = _media(oid, width=None, height=None, duration=None, mime="application/pdf")
    for media_id, headers, status, code in (
        (_media(other), h, 404, "media.not_found"),
        (pdf, h, 400, "wall.media_type_blocked"),
        (_media(other), other_h, 404, "wall.not_found"),   # the other org, on this org's wall
    ):
        res = client.put(url, json={"media_id": media_id}, headers=headers)
        assert (res.status_code, res.json()["detail"]["code"]) == (status, code)
    grid = _grid_wall(client, h)
    res = client.put(f"/walls/{grid['id']}/video", json={"media_id": _media(oid)}, headers=h)
    assert (res.status_code, res.json()["detail"]["code"]) == (409, "wall.not_free")
    # Nothing changed: the same one item and the same canvas.
    assert [i["media_id"] for i in _items(client, h, wall["id"])] == [wall["video"]["media_id"]]
    after = query_one("SELECT canvas_width_px, canvas_height_px FROM walls WHERE id = ?",
                      (wall["id"],))
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (5760, 1080)


def test_canvas_item_edits_are_refused_on_free_walls(client):
    """D8: a free wall plays exactly one item, which only PUT /walls/{id}/video
    changes. Reading the canvas playlist stays allowed."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    [item] = _items(client, h, wall["id"])
    base = f"/walls/{wall['id']}/canvas-playlist/items"
    for res in (
        client.post(base, json={"media_id": _media(oid), "position": 1}, headers=h),
        client.patch(f"{base}/{item['id']}", json={"fit_mode": "fill"}, headers=h),
        client.delete(f"{base}/{item['id']}", headers=h),
    ):
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "wall.free_layout"
    assert _items(client, h, wall["id"]) == [item]


def test_generic_playlist_endpoints_refuse_a_free_walls_canvas_playlist(client):
    """D8 also holds through the generic playlist endpoints. GET /playlists
    lists the wall's 'Canvas: <name>' playlist like any other, and the old
    Playlists page and the MCP playlist tools act on it by id: adding a second
    item, deleting the one video or deleting the playlist would leave the wall
    rotating an extra item through the TVs' boxes or playing nothing."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    pid = wall["spanned_playlist_id"]
    [item] = _items(client, h, wall["id"])
    for res in (
        client.post(f"/playlists/{pid}/items", json={"media_id": _media(oid)}, headers=h),
        client.delete(f"/playlists/{pid}/items/{item['id']}", headers=h),
        client.delete(f"/playlists/{pid}", headers=h),
    ):
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "wall.free_layout"
    # Nothing changed: the same one item, the playlist still there and still the wall's.
    assert _items(client, h, wall["id"]) == [item]
    assert query_one("SELECT spanned_playlist_id FROM walls WHERE id = ?",
                     (wall["id"],))["spanned_playlist_id"] == pid
    assert query_one("SELECT COUNT(*) AS n FROM playlist_items WHERE playlist_id = ?",
                     (pid,))["n"] == 1
    # Reading it stays allowed, and PUT /video still swaps the one item.
    assert client.get(f"/playlists/{pid}", headers=h).status_code == 200
    new_mid = _media(oid, width=1920, height=1080, duration=12.5)
    assert client.put(f"/walls/{wall['id']}/video", json={"media_id": new_mid},
                      headers=h).status_code == 200
    assert [i["media_id"] for i in _items(client, h, wall["id"])] == [new_mid]


def test_generic_playlist_endpoints_still_edit_grid_walls_and_plain_playlists(client):
    """The free-wall guard keys on a free wall pointing at the playlist: a grid
    wall's canvas playlist and an ordinary playlist behave as before."""
    oid, h = _org()
    grid = _grid_wall(client, h)
    pid = grid["spanned_playlist_id"]
    added = client.post(f"/playlists/{pid}/items", json={"media_id": _media(oid)}, headers=h)
    assert added.status_code == 200, added.text
    res = client.delete(f"/playlists/{pid}/items/{added.json()['id']}", headers=h)
    assert res.status_code == 200, res.text
    plain = client.post("/playlists", json={"name": "Plain"}, headers=h).json()["id"]
    added = client.post(f"/playlists/{plain}/items", json={"media_id": _media(oid)}, headers=h)
    assert added.status_code == 200, added.text
    assert client.delete(f"/playlists/{plain}/items/{added.json()['id']}",
                         headers=h).status_code == 200
    assert client.delete(f"/playlists/{plain}", headers=h).status_code == 200


# ---- PATCH /walls/{id} and removing a TV ----


def test_patch_renames_a_free_wall_but_refuses_layout_changes(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    url = f"/walls/{wall['id']}"
    # A form that sends the unchanged values along still renames.
    res = client.patch(url, json={"name": "Foyer", "mode": "spanned", "canvas_width_px": 5760,
                                  "canvas_height_px": 1080, "bezel_h_pct": 0}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Foyer"
    for change in ({"mode": "mirrored", "mirrored_mode": "synced_rotation"},
                   {"canvas_width_px": 3840, "canvas_height_px": 2160},
                   {"bezel_h_pct": 2.0},
                   {"bezel_enabled": True}):
        res = client.patch(url, json=change, headers=h)
        assert res.status_code == 409, (change, res.text)
        assert res.json()["detail"]["code"] == "wall.free_layout"
    after = query_one("SELECT * FROM walls WHERE id = ?", (wall["id"],))
    assert (after["name"], after["mode"], after["layout"], after["canvas_width_px"],
            after["bezel_h_pct"], after["spanned_playlist_id"]) == (
        "Foyer", "spanned", "free", 5760, 0, wall["spanned_playlist_id"])
    assert query_one("SELECT id FROM playlists WHERE id = ?", (wall["spanned_playlist_id"],))


def test_removing_tvs_deletes_cells_and_screens_and_frees_indexes(client):
    oid, h = _org()
    wid = _free_wall(client, h, oid, name="Lobby")["id"]
    second = _add_tv(client, h, wid).json()
    assert _add_tv(client, h, wid).status_code == 201
    token = query_one("SELECT token FROM screens WHERE id = ?", (second["screen_id"],))["token"]

    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 204
    assert [c["col_index"] for c in _cells(wid)] == [0, 2]
    assert _cols(wid) == 3                      # highest index left + 1
    assert query_one("SELECT id FROM screens WHERE id = ?", (second["screen_id"],)) is None
    assert client.get(f"/screens/{token}/content").status_code == 404

    code = _tv_code(client)
    res = _add_tv(client, h, wid, code)
    assert res.status_code == 201, res.text
    assert res.json()["col_index"] == 1         # the freed index, and so "TV 2" again
    assert client.get(f"/screens/poll/{code}").json()["screen_name"] == "Lobby · TV 2"

    for col in (2, 1, 0):
        assert client.delete(f"/walls/{wid}/cells/0/{col}/pairing", headers=h).status_code == 204
    assert _cells(wid) == []
    assert _cols(wid) == 1                      # walls.cols is CHECKed 1..8
    assert _left_behind(oid)["screens"] == 0
    # R18: the wall stays, empty, for the next TV a phone adds to it.
    assert query_one("SELECT id FROM walls WHERE id = ?", (wid,)) is not None


def test_bye_goes_out_once_the_cell_is_gone(client, monkeypatch):
    """R5: broadcast_bye runs after the removal has committed, outside its
    transaction, as publish_geometry does after a move."""
    oid, h = _org()
    wid = _free_wall(client, h, oid)["id"]
    assert _add_tv(client, h, wid).status_code == 201
    seen = []
    monkeypatch.setattr(walls_mod, "broadcast_bye",
                        lambda wall_id, row, col, reason: seen.append(
                            (reason, _committed_cell(wall_id, row, col))))
    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 204
    assert seen == [("cell_unpaired", None)]


def test_a_second_unpair_of_a_gone_cell_sends_no_bye(client, monkeypatch):
    """A double tap or a second phone: the other request removed the cell
    between this one's first read and its locked re-read. The index may by now
    belong to a new TV, which must not be told 'cell_unpaired'."""
    oid, h = _org()
    wid = _free_wall(client, h, oid)["id"]
    assert _add_tv(client, h, wid).status_code == 201
    seen = []
    monkeypatch.setattr(walls_mod, "broadcast_bye",
                        lambda wall_id, row, col, reason: seen.append((row, col)))
    real_tx = main.transaction

    def tx_after_a_rival_delete():
        # The rival request commits its removal just before this one locks.
        execute("DELETE FROM wall_cells WHERE wall_id = ? AND col_index = 1", (wid,))
        return real_tx()

    monkeypatch.setattr(main, "transaction", tx_after_a_rival_delete)
    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 204
    assert seen == []
    monkeypatch.undo()
    # A plain repeat is still a 404 and sends nothing either.
    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 404


# ---- Roles and subscription on the four new endpoints (spec Section 2) ----


def _session(oid, role):
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"{role}_{secrets.token_hex(3)}@x.com", hash_password("pw"), 0, role, utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)", (uid, tok, utc_now_iso()))
    return {"Authorization": f"Bearer {tok}"}


def test_new_endpoints_need_an_editor_and_a_live_subscription(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    mid = _media(oid)
    calls = [
        ("post", "/walls/free", {"name": "X", "media_id": mid, "code": _tv_code(client)}),
        ("post", f"/walls/{wall['id']}/tvs", {"code": _tv_code(client)}),
        ("put", f"/walls/{wall['id']}/cells/0/0/placement", {"x": 0.0, "y": 0.0, "w": 0.3, "h": 1.0}),
        ("put", f"/walls/{wall['id']}/video", {"media_id": mid}),
    ]
    viewer = _session(oid, "viewer")
    for method, path, body in calls:
        res = getattr(client, method)(path, json=body, headers=viewer)
        assert res.status_code == 403, (path, res.text)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    execute("UPDATE organizations SET subscription_status = 'trialing', trial_ends_at = ? WHERE id = ?",
            (past, oid))
    for method, path, body in calls:
        res = getattr(client, method)(path, json=body, headers=h)
        assert res.status_code == 402, (path, res.text)
        assert res.json()["detail"]["code"] == "subscription.trial_expired"
    assert len(_cells(wall["id"])) == 1
