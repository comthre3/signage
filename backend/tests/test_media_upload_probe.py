"""POST /media/upload stores the probed size and length; transcode_video caps
the long side at 3840 px and stores the size of the file it made; standalone
video playlist items keep their 10 s default (plan decision D2).

TestClient runs a response's background tasks before client.post returns, so a
real video upload here also runs the real transcode.
"""
import asyncio
import io
import os
import secrets
import shutil
import subprocess
import uuid

import pytest

import main
import media_probe
from db import execute, query_one, utc_now_iso
from main import hash_password

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)

NULLS = {"width_px": None, "height_px": None, "duration_seconds": None}


def _org_headers(screen_limit: int = 10):
    slug = "mp" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"MP {slug}", slug, "active", screen_limit, utc_now_iso()),
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


def _upload(client, headers, name, body, mime):
    return client.post("/media/upload", headers=headers,
                       files={"file": (name, io.BytesIO(body), mime)})


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _make_video(path, size: str, *, seconds: float = 1, pix_fmt: str = "yuv420p") -> str:
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=10", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, str(path))
    return str(path)


def _dims(path) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, check=True, timeout=20,
    ).stdout.decode().strip()
    width, height = out.split("x")
    return int(width), int(height)


def _media_row(media_id: int) -> dict:
    return query_one(
        "SELECT filename, mime_type, width_px, height_px, duration_seconds "
        "FROM media WHERE id = ?", (media_id,),
    )


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_upload_stores_an_images_size(client):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (0, 120, 0)).save(buf, format="PNG")
    _, headers = _org_headers()
    r = _upload(client, headers, "still.png", buf.getvalue(), "image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["width_px"], body["height_px"], body["duration_seconds"]) == (640, 360, None)
    row = _media_row(body["id"])
    assert (row["width_px"], row["height_px"], row["duration_seconds"]) == (640, 360, None)


def test_upload_reads_the_file_off_the_event_loop_and_stores_nulls_when_unreadable(
    client, monkeypatch,
):
    monkeypatch.setattr(main, "transcode_video", lambda *a, **k: None)
    real = media_probe.probe_media
    calls = []

    def spy(path, mime, *args, **kwargs):
        calls.append((mime, _on_event_loop()))
        return real(path, mime, *args, **kwargs)

    monkeypatch.setattr(media_probe, "probe_media", spy)
    _, headers = _org_headers()
    r = _upload(client, headers, "clip.3gp", b"\x00\x00\x00\x18ftyp", "video/3gpp")
    assert r.status_code == 200, r.text
    # Probed once, in a worker thread: a 20 s ffprobe must not stall the loop
    # that carries this worker's wall sockets.
    assert calls == [("video/3gpp", False)]
    row = _media_row(r.json()["id"])
    assert (row["width_px"], row["height_px"], row["duration_seconds"]) == (None, None, None)


def test_upload_queues_the_transcode_for_a_mixed_case_video_claim(client, monkeypatch):
    queued = []
    monkeypatch.setattr(main, "transcode_video", lambda path, media_id: queued.append(media_id))
    _, headers = _org_headers()
    r = _upload(client, headers, "clip.mp4", b"\x00\x00\x00\x18ftyp", "VIDEO/MP4")
    assert r.status_code == 200, r.text
    assert queued == [r.json()["id"]]


def test_upload_does_not_transcode_images(client, monkeypatch):
    queued = []
    monkeypatch.setattr(main, "transcode_video", lambda path, media_id: queued.append(media_id))
    _, headers = _org_headers()
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")
    r = _upload(client, headers, "a.png", buf.getvalue(), "image/png")
    assert r.status_code == 200, r.text
    assert queued == []


@needs_ffmpeg
def test_upload_then_transcode_caps_a_panorama_at_3840(client, tmp_path):
    src = _make_video(tmp_path / "pano.mp4", "5760x1080", seconds=1)
    _, headers = _org_headers()
    with open(src, "rb") as f:
        r = _upload(client, headers, "pano.mp4", f.read(), "video/mp4")
    assert r.status_code == 200, r.text
    body = r.json()
    # The answer describes the upload as probed, before the transcode ran.
    assert (body["width_px"], body["height_px"]) == (5760, 1080)
    assert body["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    # The background transcode has finished by now: the row is the capped file.
    row = _media_row(body["id"])
    assert row["filename"].endswith(".opt.mp4")
    assert row["mime_type"] == "video/mp4"
    assert (row["width_px"], row["height_px"]) == (3840, 720)
    assert row["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    assert _dims(os.path.join(main.UPLOAD_DIR, row["filename"])) == (3840, 720)


@needs_ffmpeg
def test_transcode_puts_the_scale_filter_right_after_the_input(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, b"", b"stub failure")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    src = str(tmp_path / "in.mov")
    main.transcode_video(src, 0)
    cmd = seen[0]
    assert cmd[0] == "ffmpeg"
    i = cmd.index("-i")
    assert cmd[i + 1] == src
    assert cmd[i + 2:i + 4] == ["-vf", media_probe.TRANSCODE_SCALE_FILTER]


@needs_ffmpeg
def test_transcode_rounds_an_odd_sized_video_to_even(tmp_path):
    # libx264 refuses odd sizes with yuv420p, so the source is yuv444p.
    src = _make_video(tmp_path / "odd.mp4", "1281x721", pix_fmt="yuv444p")
    oid, _ = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "odd.mp4", "odd.mp4", "video/mp4", os.path.getsize(src), utc_now_iso(), 1281, 721),
    )
    main.transcode_video(src, mid)
    row = _media_row(mid)
    assert row["filename"] == "odd.opt.mp4"
    assert (row["width_px"], row["height_px"]) == (1280, 720)
    assert row["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    assert _dims(tmp_path / "odd.opt.mp4") == (1280, 720)


@needs_ffmpeg
def test_transcode_keeps_upload_time_values_the_output_read_lacks(tmp_path, monkeypatch):
    src = _make_video(tmp_path / "hd.mp4", "1280x720")
    oid, _ = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "hd.mp4", "hd.mp4", "video/mp4", os.path.getsize(src), utc_now_iso(), 1280, 720),
    )
    # The read of the output knows the length but not the size.
    monkeypatch.setattr(media_probe, "probe_media", lambda *a, **k: {
        "width_px": None, "height_px": None, "duration_seconds": 7.5,
    })
    main.transcode_video(src, mid)
    row = _media_row(mid)
    assert row["filename"] == "hd.opt.mp4"
    assert (row["width_px"], row["height_px"]) == (1280, 720)
    assert row["duration_seconds"] == pytest.approx(7.5)


def test_default_duration_ignores_the_stored_video_length():
    assert main._default_duration_seconds({"mime_type": "video/mp4", "duration_seconds": 42.5}) == 10
    assert main._default_duration_seconds({"mime_type": "video/mp4", "duration_seconds": None}) == 10
    assert main._default_duration_seconds({"mime_type": "image/png"}) == 10
    assert main._default_duration_seconds({"mime_type": "application/pdf"}) == 30


def test_standalone_video_item_still_defaults_to_ten_seconds(client):
    oid, headers = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "long.mp4", f"{uuid.uuid4().hex}.mp4", "video/mp4", 100, utc_now_iso(),
         1920, 1080, 42.5),
    )
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (oid, "p", utc_now_iso()))
    r = client.post(f"/playlists/{pid}/items", headers=headers, json={"media_id": mid})
    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 10
