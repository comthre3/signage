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


import pytest
from pathlib import Path

MINIMAL_PDF_PATH = Path(__file__).parent / "fixtures" / "two_page.pdf"


def test_pdf_render_two_page_to_png_sequence(tmp_path):
    from pdf_render import rasterize_pdf
    out_dir = tmp_path / "pages"
    pages = rasterize_pdf(str(MINIMAL_PDF_PATH), str(out_dir), width_px=1920, height_px=1080)
    assert pages == ["page_01.png", "page_02.png"]
    assert (out_dir / "page_01.png").exists()
    assert (out_dir / "page_02.png").exists()
    from PIL import Image
    with Image.open(out_dir / "page_01.png") as im:
        assert im.size == (1920, 1080)


def test_pdf_render_corrupt_input_raises(tmp_path):
    from pdf_render import rasterize_pdf, PdfRenderError
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a real pdf")
    with pytest.raises(PdfRenderError):
        rasterize_pdf(str(bad), str(tmp_path / "out"), width_px=1920, height_px=1080)
