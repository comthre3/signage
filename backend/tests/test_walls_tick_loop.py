import secrets

import walls as walls_mod
from db import execute, utc_now_iso


def test_same_playlist_current_play_frame_shape():
    """current_play_for returns a play frame for a same_playlist mirrored wall."""
    slug = "tick" + secrets.token_hex(3)
    org = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        (f"Tick {slug}", slug, utc_now_iso()),
    )
    pid = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (org, "p", utc_now_iso()),
    )
    m1 = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org, "m1", "a.mp4", "video/mp4", 100, utc_now_iso()),
    )
    m2 = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org, "m2", "b.mp4", "video/mp4", 100, utc_now_iso()),
    )
    execute(
        "INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, m1, 5, 0, utc_now_iso()),
    )
    execute(
        "INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, m2, 7, 1, utc_now_iso()),
    )
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, mirrored_mode, "
        "mirrored_playlist_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (org, "W", "mirrored", 1, 1, "same_playlist", pid, utc_now_iso(), utc_now_iso()),
    )
    cell = {"row_index": 0, "col_index": 0, "wall_id": wid}

    frame = walls_mod.current_play_for(wid, cell)
    assert frame is not None
    assert frame["type"] == "play"
    assert frame["item"]["url"].endswith(".mp4")
    assert frame["duration_ms"] in (5000, 7000)
    assert "started_at_ms" in frame and "playlist_signature" in frame


def test_synced_rotation_slot_duration_is_max():
    """Synced-rotation slot uses the slowest cell's duration."""
    durations_per_cell = [[5, 10, 3], [4, 8, 6]]
    expected = [5000, 10000, 6000]
    assert walls_mod.synced_rotation_slot_durations(durations_per_cell) == expected
