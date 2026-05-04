import pytest
from db import connect


def _columns(table: str) -> set[str]:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s
        """, (table,))
        return {row["column_name"] for row in cur.fetchall()}


def test_walls_has_bezel_pct_columns():
    cols = _columns("walls")
    assert "bezel_h_pct" in cols
    assert "bezel_v_pct" in cols


def test_media_has_pdf_pages_status():
    assert "pdf_pages_status" in _columns("media")


def test_playlist_items_has_phase2_columns():
    cols = _columns("playlist_items")
    assert "duration_override_seconds" in cols
    assert "fit_mode" in cols
