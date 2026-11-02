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
import wall_layout
from db import execute, query_all, query_one, utc_now_iso
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
