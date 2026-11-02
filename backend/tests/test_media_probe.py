"""media_probe: pixel size and length of uploaded images and videos.

ffmpeg/ffprobe are in the backend image; tests that need them generate their
input at test time and skip if either binary is missing.
"""
import shutil
import subprocess

import pytest

import media_probe

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)

NULLS = {"width_px": None, "height_px": None, "duration_seconds": None}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _make_video(path, size: str, *, seconds: float = 1, pix_fmt: str = "yuv420p") -> str:
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=10", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, str(path))
    return str(path)


def test_constants_match_the_contract():
    assert media_probe.PROBE_TIMEOUT_SECONDS == 20
    assert media_probe.VIDEO_LONG_SIDE_MAX == 3840
    assert media_probe.TRANSCODE_SCALE_FILTER == (
        "scale='if(gt(iw,ih),min(3840,trunc(iw/2)*2),-2)'"
        ":'if(gt(iw,ih),-2,min(3840,trunc(ih/2)*2))'"
    )


@needs_ffmpeg
def test_probe_reads_a_wide_video(tmp_path):
    path = _make_video(tmp_path / "pano.mp4", "5760x1080", seconds=2)
    got = media_probe.probe_media(path, "video/mp4")
    assert (got["width_px"], got["height_px"]) == (5760, 1080)
    assert got["duration_seconds"] == pytest.approx(2.0, abs=0.15)


@needs_ffmpeg
@pytest.mark.parametrize("rotation", ["90", "-90"])
def test_probe_swaps_the_size_of_a_video_stored_sideways(tmp_path, rotation):
    """Phones store portrait video as landscape frames plus a rotation."""
    land = _make_video(tmp_path / "land.mp4", "1920x1080")
    rotated = tmp_path / "rot.mov"
    _ffmpeg("-display_rotation", rotation, "-i", land, "-c", "copy", str(rotated))
    got = media_probe.probe_media(str(rotated), "video/quicktime")
    assert (got["width_px"], got["height_px"]) == (1080, 1920)
    assert got["duration_seconds"] == pytest.approx(1.0, abs=0.15)


@needs_ffmpeg
def test_probe_takes_a_mixed_case_mime(tmp_path):
    path = _make_video(tmp_path / "hd.mp4", "1280x720")
    got = media_probe.probe_media(path, "VIDEO/MP4")
    assert (got["width_px"], got["height_px"]) == (1280, 720)


def test_probe_reads_an_image_with_pillow(tmp_path):
    from PIL import Image
    path = tmp_path / "still.png"
    Image.new("RGB", (800, 600), (200, 0, 0)).save(path)
    assert media_probe.probe_media(str(path), "image/png") == {
        "width_px": 800, "height_px": 600, "duration_seconds": None,
    }


def test_probe_swaps_a_jpeg_with_exif_orientation_6(tmp_path):
    from PIL import Image
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6          # stored turned 90 degrees
    Image.new("RGB", (400, 300), (0, 0, 200)).save(path, exif=exif.tobytes())
    got = media_probe.probe_media(str(path), "image/jpeg")
    assert (got["width_px"], got["height_px"]) == (300, 400)
    assert got["duration_seconds"] is None


@pytest.mark.parametrize("name,body,mime", [
    ("garbage.3gp", b"\x00\x00\x00\x18ftyp", "video/3gpp"),
    ("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"),
    ("menu.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    ("note.txt", b"hello", "text/url"),
])
def test_probe_returns_nulls_for_what_it_cannot_read(tmp_path, name, body, mime):
    path = tmp_path / name
    path.write_bytes(body)
    assert media_probe.probe_media(str(path), mime) == NULLS


def test_probe_of_a_missing_file_returns_nulls(tmp_path):
    assert media_probe.probe_media(str(tmp_path / "gone.mp4"), "video/mp4") == NULLS
    assert media_probe.probe_media(str(tmp_path / "gone.png"), "image/png") == NULLS


def test_probe_passes_its_timeout_and_survives_a_hung_ffprobe(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 16)
    seen = []

    def hang(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(media_probe.subprocess, "run", hang)
    assert media_probe.probe_media(str(path), "video/mp4") == NULLS
    assert seen == [20]
    assert media_probe.probe_media(str(path), "video/mp4", timeout=3) == NULLS
    assert seen == [20, 3]


def test_probe_ignores_nonsense_ffprobe_values(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 16)
    out = (b'{"streams":[{"width":0,"height":"x","duration":"N/A"}],'
           b'"format":{"duration":"nan"}}')
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(media_probe.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, b""))
    assert media_probe.probe_media(str(path), "video/mp4") == NULLS
