"""Schema for phone-placed (free-layout) video walls and probed media sizes.

All additive ALTERs in db.init_db: free walls (walls.layout), each TV's box,
reported screen size and highlight (wall_cells.place_* / screen_*_px /
highlight_until_ms), and media size and length (media.width_px / height_px /
duration_seconds).
"""
import secrets

import psycopg
import pytest

from db import execute, init_db, query_one, utc_now_iso


def _column(table: str, column: str) -> dict | None:
    return query_one(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ? AND column_name = ?",
        (table, column),
    )


def _org() -> int:
    slug = "fs" + secrets.token_hex(3)
    return execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"FS {slug}", slug, "active", 10, utc_now_iso()),
    )


def _insert_wall(oid: int, **extra) -> int:
    cols = ["organization_id", "name", "mode", "rows", "cols", "created_at", "updated_at"]
    vals = [oid, "W", "spanned", 1, 1, utc_now_iso(), utc_now_iso()]
    for key, value in extra.items():
        cols.append(key)
        vals.append(value)
    placeholders = ", ".join("?" for _ in cols)
    return execute(
        f"INSERT INTO walls ({', '.join(cols)}) VALUES ({placeholders})", tuple(vals),
    )


@pytest.mark.parametrize("table,column,data_type", [
    ("wall_cells", "place_x", "real"),
    ("wall_cells", "place_y", "real"),
    ("wall_cells", "place_w", "real"),
    ("wall_cells", "place_h", "real"),
    ("wall_cells", "screen_w_px", "integer"),
    ("wall_cells", "screen_h_px", "integer"),
    ("wall_cells", "highlight_until_ms", "bigint"),
    ("media", "duration_seconds", "real"),
    ("media", "width_px", "integer"),
    ("media", "height_px", "integer"),
])
def test_new_columns_are_nullable_with_no_default(table, column, data_type):
    col = _column(table, column)
    assert col is not None, f"{table}.{column} is missing"
    assert col["data_type"] == data_type
    assert col["is_nullable"] == "YES"
    assert col["column_default"] is None


def test_walls_layout_is_text_not_null_defaulting_to_grid():
    col = _column("walls", "layout")
    assert col is not None, "walls.layout is missing"
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "NO"
    assert col["column_default"] == "'grid'::text"


def test_a_wall_inserted_without_layout_is_a_grid_wall():
    wid = _insert_wall(_org())
    assert query_one("SELECT layout FROM walls WHERE id = ?", (wid,))["layout"] == "grid"


def test_a_wall_can_be_free_with_any_canvas_size():
    wid = _insert_wall(_org(), layout="free", canvas_width_px=3840, canvas_height_px=720)
    row = query_one(
        "SELECT layout, canvas_width_px, canvas_height_px FROM walls WHERE id = ?", (wid,),
    )
    assert row == {"layout": "free", "canvas_width_px": 3840, "canvas_height_px": 720}


def test_layout_check_rejects_other_values():
    oid = _org()
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_wall(oid, layout="other")
    n = query_one("SELECT COUNT(*) AS n FROM walls WHERE organization_id = ?", (oid,))["n"]
    assert n == 0


def test_cell_box_screen_size_and_highlight_round_trip():
    wid = _insert_wall(_org(), layout="free")
    cid = execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
        "place_h, screen_w_px, screen_h_px, highlight_until_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        # 1762000030000 needs BIGINT: it is past 2**31.
        (wid, 0, 0, 0.25, -0.5, 0.5, 2.0, 960, 540, 1762000030000, utc_now_iso()),
    )
    cell = query_one(
        "SELECT place_x, place_y, place_w, place_h, screen_w_px, screen_h_px, "
        "highlight_until_ms FROM wall_cells WHERE id = ?", (cid,),
    )
    assert cell == {
        "place_x": 0.25, "place_y": -0.5, "place_w": 0.5, "place_h": 2.0,
        "screen_w_px": 960, "screen_h_px": 540, "highlight_until_ms": 1762000030000,
    }


def test_media_size_and_length_round_trip():
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "pano.mp4", "pano.mp4", "video/mp4", 100, utc_now_iso(), 5760, 1080, 20.5),
    )
    row = query_one(
        "SELECT width_px, height_px, duration_seconds FROM media WHERE id = ?", (mid,),
    )
    assert row == {"width_px": 5760, "height_px": 1080, "duration_seconds": 20.5}


def test_init_db_twice_keeps_one_layout_check():
    init_db()
    init_db()
    n = query_one(
        "SELECT COUNT(*) AS n FROM pg_constraint "
        "WHERE conrelid = 'walls'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) LIKE ?",
        ("%layout%",),
    )["n"]
    assert n == 1
    assert _column("walls", "layout")["column_default"] == "'grid'::text"
