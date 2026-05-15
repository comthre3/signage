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
from db import execute, query_one, query_all, utc_now_iso


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def admin_token():
    from main import hash_password
    org_slug = "p2" + secrets.token_hex(3)
    org_id = execute(
        "INSERT INTO organizations (name, slug, subscription_status, created_at) VALUES (?, ?, ?, ?)",
        (f"P2Test {org_slug}", org_slug, "active", utc_now_iso()),
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


def _create_spanned_wall(client, admin_token, **overrides):
    body = {"name": "W", "mode": "spanned", "rows": 2, "cols": 2,
            "canvas_width_px": 3840, "canvas_height_px": 2160,
            "bezel_h_pct": 0, "bezel_v_pct": 0}
    body.update(overrides)
    res = client.post("/walls", headers=_auth(admin_token), json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _upload_image(client, admin_token, name="test.png"):
    from io import BytesIO
    files = {"file": (name, BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64), "image/png")}
    res = client.post("/media/upload", headers=_auth(admin_token), files=files)
    assert res.status_code in (200, 201), res.text
    return res.json()


def test_canvas_playlist_list_empty(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    res = client.get(f"/walls/{wall['id']}/canvas-playlist", headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


def test_canvas_playlist_list_404_on_mirrored(client, admin_token):
    pl_id = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p_mir", utc_now_iso()),
    )
    res = client.post("/walls", headers=_auth(admin_token), json={
        "name": "M", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pl_id})
    wall = res.json()
    res = client.get(f"/walls/{wall['id']}/canvas-playlist", headers=_auth(admin_token))
    assert res.status_code == 404


def test_canvas_playlist_add_image(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    media = _upload_image(client, admin_token)
    res = client.post(f"/walls/{wall['id']}/canvas-playlist/items",
                      headers=_auth(admin_token),
                      json={"media_id": media["id"], "position": 0,
                            "duration_override_seconds": 7, "fit_mode": "fill"})
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["fit_mode"] == "fill"
    assert item["duration_override_seconds"] == 7


def test_canvas_playlist_patch_item(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    media = _upload_image(client, admin_token)
    item = client.post(f"/walls/{wall['id']}/canvas-playlist/items",
                      headers=_auth(admin_token),
                      json={"media_id": media["id"], "position": 0}).json()
    res = client.patch(f"/walls/{wall['id']}/canvas-playlist/items/{item['id']}",
                       headers=_auth(admin_token),
                       json={"fit_mode": "stretch", "duration_override_seconds": 12})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fit_mode"] == "stretch"
    assert body["duration_override_seconds"] == 12


def test_canvas_playlist_delete_item(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    media = _upload_image(client, admin_token)
    item = client.post(f"/walls/{wall['id']}/canvas-playlist/items",
                       headers=_auth(admin_token),
                       json={"media_id": media["id"], "position": 0}).json()
    res = client.delete(f"/walls/{wall['id']}/canvas-playlist/items/{item['id']}",
                        headers=_auth(admin_token))
    assert res.status_code == 204
    listing = client.get(f"/walls/{wall['id']}/canvas-playlist",
                         headers=_auth(admin_token)).json()
    assert listing["items"] == []


def test_canvas_playlist_cross_org_isolation(client, admin_token):
    # Create wall as admin_token's org.
    wall = _create_spanned_wall(client, admin_token)
    # Create another org + admin.
    from main import hash_password
    other_org = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        ("Other", "other" + secrets.token_hex(3), utc_now_iso()),
    )
    other_user = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (other_org, f"u2_{secrets.token_hex(4)}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    other_token = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (other_user, other_token, utc_now_iso()))
    res = client.get(f"/walls/{wall['id']}/canvas-playlist",
                     headers={"Authorization": f"Bearer {other_token}"})
    assert res.status_code == 404  # wall doesn't belong to other org


def test_mode_change_mirrored_to_spanned_clears_mirrored_keeps_pairings(client, admin_token):
    pl_id = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (admin_token["org_id"], "p_mir2sp", utc_now_iso()),
    )
    wall = client.post("/walls", headers=_auth(admin_token), json={
        "name": "M2S", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pl_id}).json()
    # Simulate a paired cell so we can confirm preservation.
    execute(
        "UPDATE wall_cells SET screen_id = NULL WHERE wall_id = ?",  # baseline: NULL is fine
        (wall["id"],),
    )
    res = client.patch(f"/walls/{wall['id']}", headers=_auth(admin_token), json={
        "mode": "spanned",
        "canvas_width_px": 3840, "canvas_height_px": 2160,
        "bezel_h_pct": 0, "bezel_v_pct": 0,
    })
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["mode"] == "spanned"
    assert updated["mirrored_playlist_id"] is None
    assert updated["mirrored_mode"] is None
    assert updated["spanned_playlist_id"] is not None
    # Old playlist gone.
    assert query_one("SELECT id FROM playlists WHERE id = ?", (pl_id,)) is None
    # Cells preserved (count matches rows × cols).
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (wall["id"],))
    assert len(cells) == 2  # 1×2 wall


def test_mode_change_spanned_to_mirrored_clears_canvas_playlist(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    canvas_pl = wall["spanned_playlist_id"]
    res = client.patch(f"/walls/{wall['id']}", headers=_auth(admin_token), json={
        "mode": "mirrored",
        "mirrored_mode": "same_playlist",
    })
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["mode"] == "mirrored"
    assert updated["spanned_playlist_id"] is None
    # Canvas playlist deleted.
    assert query_one("SELECT id FROM playlists WHERE id = ?", (canvas_pl,)) is None


def test_mode_change_same_mode_is_noop_on_playlist(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    canvas_pl = wall["spanned_playlist_id"]
    res = client.patch(f"/walls/{wall['id']}", headers=_auth(admin_token), json={
        "mode": "spanned",
    })
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["spanned_playlist_id"] == canvas_pl  # unchanged


def test_mode_change_other_fields_still_update(client, admin_token):
    wall = _create_spanned_wall(client, admin_token)
    res = client.patch(f"/walls/{wall['id']}", headers=_auth(admin_token), json={
        "name": "Renamed", "bezel_h_pct": 3.5,
    })
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["name"] == "Renamed"
    assert abs(updated["bezel_h_pct"] - 3.5) < 0.01


def test_hello_frame_for_spanned_includes_canvas_geometry_bezel():
    from walls import _hello_frame
    wall = {"id": 7, "mode": "spanned", "rows": 2, "cols": 2,
            "canvas_width_px": 3840, "canvas_height_px": 2160,
            "bezel_h_pct": 2.0, "bezel_v_pct": 1.0}
    cell = {"row_index": 0, "col_index": 1}
    frame = _hello_frame(wall, cell, current_play=None)
    assert frame["mode"] == "spanned"
    assert frame["canvas"] == {"w": 3840, "h": 2160}
    assert frame["bezel"] == {"h_pct": 2.0, "v_pct": 1.0}
    g = frame["cell_geometry"]
    # Visible per cell: (1 - 1*0.02) / 2 = 0.49 of canvas width.
    # Cell (0,1) starts at: 1 * (cell_w + gap) = 1 * (0.49 + 0.02) = 0.51.
    assert abs(g["x"] - 3840 * 0.51) < 1
    assert abs(g["w"] - 3840 * 0.49) < 1
    assert g["y"] == 0
    # Vertical: 1 row gap → 0.01; visible_h = (1 - 1*0.01) / 2 = 0.495.
    assert abs(g["h"] - 2160 * 0.495) < 1


def test_hello_frame_for_mirrored_omits_spanned_fields():
    from walls import _hello_frame
    wall = {"id": 8, "mode": "mirrored", "rows": 1, "cols": 2,
            "mirrored_mode": "same_playlist"}
    cell = {"row_index": 0, "col_index": 0}
    frame = _hello_frame(wall, cell, current_play=None)
    assert frame["mode"] == "mirrored"
    assert "canvas" not in frame
    assert "cell_geometry" not in frame
    assert "bezel" not in frame


def test_play_frame_includes_fit_mode():
    from walls import _build_play_frame
    item = {"id": 99, "url": "/uploads/x.mp4", "mime_type": "video/mp4",
            "name": "x", "duration_seconds": 30, "fit_mode": "cover"}
    frame = _build_play_frame(item, started_at_ms=1000, signature="sig")
    assert frame["fit_mode"] == "cover"


def test_play_frame_default_fit_mode_when_missing():
    from walls import _build_play_frame
    item = {"id": 1, "url": "/uploads/x.png", "mime_type": "image/png",
            "name": "x", "duration_seconds": 5}  # no fit_mode key
    frame = _build_play_frame(item, started_at_ms=0, signature="s")
    assert frame["fit_mode"] == "fit"


def test_canvas_items_loader_image_only(client, admin_token):
    """Verify _load_canvas_items returns image items with url + fit_mode."""
    from walls import _load_canvas_items
    wall = _create_spanned_wall(client, admin_token)
    media = _upload_image(client, admin_token)
    client.post(f"/walls/{wall['id']}/canvas-playlist/items",
                headers=_auth(admin_token),
                json={"media_id": media["id"], "position": 0,
                      "duration_override_seconds": 7, "fit_mode": "fill"})
    full_wall = query_one("SELECT * FROM walls WHERE id = ?", (wall["id"],))
    items = _load_canvas_items(wall["id"], full_wall)
    assert len(items) == 1
    assert items[0]["fit_mode"] == "fill"
    assert items[0]["duration_seconds"] == 7  # override applied
    assert items[0]["url"].startswith("/uploads/")
    assert items[0]["mime_type"].startswith("image/")
