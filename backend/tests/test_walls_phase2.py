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


import secrets, uuid
from fastapi.testclient import TestClient
from db import execute, query_one, utc_now_iso


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def admin_token():
    from main import hash_password
    org_slug = "p2" + secrets.token_hex(3)
    org_id = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        (f"P2Test {org_slug}", org_slug, utc_now_iso()),
    )
    user_id = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, f"u_{org_slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
        (user_id, token, utc_now_iso()),
    )
    return {"token": token, "org_id": org_id, "user_id": user_id}


def _auth(t):
    return {"Authorization": f"Bearer {t['token']}"}


def test_create_spanned_wall_creates_canvas_playlist(client, admin_token):
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "Lobby", "mode": "spanned", "rows": 2, "cols": 2,
        "canvas_width_px": 3840, "canvas_height_px": 2160,
        "bezel_h_pct": 2.0, "bezel_v_pct": 1.0,
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["mode"] == "spanned"
    assert body["spanned_playlist_id"] is not None
    pl = query_one("SELECT * FROM playlists WHERE id = ?", (body["spanned_playlist_id"],))
    assert pl["kind"] == "wall_canvas"
    assert pl["organization_id"] == admin_token["org_id"]


def test_create_spanned_wall_rejects_bad_canvas_resolution(client, admin_token):
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "Bad", "mode": "spanned", "rows": 2, "cols": 2,
        "canvas_width_px": 1234, "canvas_height_px": 567,
    })
    assert res.status_code == 400
    body = res.json()
    assert body["detail"]["code"] == "wall.canvas_resolution_invalid"


def test_create_spanned_wall_rejects_bezel_pct_too_high(client, admin_token):
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "Bad", "mode": "spanned", "rows": 2, "cols": 2,
        "canvas_width_px": 3840, "canvas_height_px": 2160,
        "bezel_h_pct": 60.0,  # 60% × 2 cols = 120% — collapses
    })
    assert res.status_code == 422 or res.status_code == 400
    # Pydantic 422 (le=10 violation) OR our 400 — either is correct rejection.


def test_create_spanned_wall_with_visible_collapse_bezel_too_large(client, admin_token):
    # h_pct=8 × 2 cols = 16% gap — visible per cell = 84%/2 = 42%. OK.
    # But pick something that passes pydantic le=10 yet still fails the
    # cols * h_pct < 100 check: not possible with cols<=8 and pct<=10
    # (8 × 10 = 80 < 100). So this test case verifies graceful path.
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "Tight", "mode": "spanned", "rows": 1, "cols": 8,
        "canvas_width_px": 7680, "canvas_height_px": 4320,
        "bezel_h_pct": 9.0, "bezel_v_pct": 0,
    })
    # 8 × 9 = 72%; visible 28% / 8 cols = 3.5% per cell. Tight but valid.
    assert res.status_code == 201


def test_create_mirrored_wall_unchanged_phase1_path(client, admin_token):
    # Create a playlist first to satisfy mirrored_playlist_required.
    pl_id = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p1", utc_now_iso()),
    )
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "Mirror", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pl_id,
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["spanned_playlist_id"] is None  # not auto-created for mirrored
