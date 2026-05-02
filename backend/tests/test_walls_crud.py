from db import connect, init_db


def test_walls_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'walls'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "organization_id", "name", "mode", "rows", "cols",
        "canvas_width_px", "canvas_height_px", "bezel_enabled",
        "spanned_playlist_id", "mirrored_mode", "mirrored_playlist_id",
        "created_at", "updated_at",
    }
    missing = expected - cols
    assert not missing, f"walls table missing columns: {missing}"


def test_wall_cells_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_cells'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "wall_id", "row_index", "col_index", "screen_id",
        "screen_size_inches",
        "bezel_top_mm", "bezel_right_mm", "bezel_bottom_mm", "bezel_left_mm",
        "playlist_id", "created_at",
    }
    missing = expected - cols
    assert not missing, f"wall_cells table missing columns: {missing}"


def test_wall_pairing_codes_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_pairing_codes'
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    assert {"id", "code", "wall_id", "row_index", "col_index", "status",
            "expires_at", "created_at", "claimed_at"}.issubset(cols)


def test_screens_has_wall_cell_id_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'screens' AND column_name = 'wall_cell_id'
        """)
        assert cur.fetchone() is not None


def test_organizations_has_walls_enabled_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'organizations' AND column_name = 'walls_enabled'
        """)
        assert cur.fetchone() is not None


def test_playlists_has_kind_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'playlists' AND column_name = 'kind'
        """)
        assert cur.fetchone() is not None
