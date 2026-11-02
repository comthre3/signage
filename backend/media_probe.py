"""Pixel size and length of an uploaded image or video.

POST /media/upload probes every file before it answers, and transcode_video
probes its output again, because the 3840 px cap (TRANSCODE_SCALE_FILTER) can
change a video's size. A free video wall's canvas is the probed size of its
video, so this is what lets each TV's box crop the right part of the picture.

Pure file inspection, no database access. probe_media never raises: anything
it cannot read comes back as None, and the caller stores NULL.
"""
import json
import logging
import math
import shutil
import subprocess

logger = logging.getLogger("signage.media_probe")

PROBE_TIMEOUT_SECONDS = 20
# Cheap TV hardware decoders often stop at 4096 px wide, and a file a TV cannot
# decode shows nothing at all, so every transcode caps the long side here.
VIDEO_LONG_SIDE_MAX = 3840
# The long side becomes min(3840, itself rounded down to even) and the other
# side follows the aspect (-2: rounded to even). Never upscales. libx264
# refuses odd sizes, so rounding also lets a 1281x721 source transcode at all.
# In an argv list the single quotes are ffmpeg's own filtergraph quoting.
TRANSCODE_SCALE_FILTER = (
    f"scale='if(gt(iw,ih),min({VIDEO_LONG_SIDE_MAX},trunc(iw/2)*2),-2)'"
    f":'if(gt(iw,ih),-2,min({VIDEO_LONG_SIDE_MAX},trunc(ih/2)*2))'"
)

_NO_PROBE = {"width_px": None, "height_px": None, "duration_seconds": None}
_MAX_SIDE_PX = 100000
_EXIF_ORIENTATION = 0x0112
_EXIF_QUARTER_TURNS = (5, 6, 7, 8)   # stored turned by 90 or 270 degrees


def _pos_int(value):
    """A whole number of pixels in 1.._MAX_SIDE_PX, else None."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= _MAX_SIDE_PX else None


def _pos_float(value):
    """A finite number above 0, else None (ffprobe writes "N/A" when it does not know)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _ffprobe_json(path: str, timeout: float):
    """ffprobe's view of the first video stream and the container, or None."""
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,duration:stream_side_data=rotation:format=duration",
        "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _pillow_size(path: str):
    """(width, height) as the picture is shown. Raises if Pillow cannot read it."""
    from PIL import Image
    with Image.open(path) as im:          # header only; pixels are never decoded
        width, height = im.size
        if im.getexif().get(_EXIF_ORIENTATION) in _EXIF_QUARTER_TURNS:
            width, height = height, width
    return width, height


def probe_media(path: str, mime: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """{"width_px", "height_px", "duration_seconds"} of an image or video file.

    Images are read with Pillow, falling back to ffprobe for what Pillow has no
    plugin for (AVIF). Videos are read with ffprobe; one stored sideways
    (rotation side data of +-90, as phones record portrait video) reports its
    shown size. The container's duration is preferred, since webm and mkv carry
    none per stream. PDFs, URLs and anything unreadable give all None.
    """
    out = dict(_NO_PROBE)
    major = (mime or "").split(";")[0].strip().lower().split("/")[0]
    if major not in ("image", "video"):
        return out
    try:
        if major == "image":
            try:
                size = _pillow_size(path)
            except Exception:
                size = None   # AVIF, SVG, truncated or huge files: let ffprobe try
            if size is not None:
                out["width_px"], out["height_px"] = _pos_int(size[0]), _pos_int(size[1])
                return out
        data = _ffprobe_json(path, timeout)
        if not data:
            return out
        streams = data.get("streams") or []
        stream = streams[0] if streams and isinstance(streams[0], dict) else {}
        width, height = _pos_int(stream.get("width")), _pos_int(stream.get("height"))
        rotation = 0
        for side in stream.get("side_data_list") or []:
            if isinstance(side, dict) and "rotation" in side:
                try:
                    rotation = int(float(side["rotation"]))
                except (TypeError, ValueError):
                    pass
        if rotation % 180 != 0 and rotation % 90 == 0:
            width, height = height, width
        out["width_px"], out["height_px"] = width, height
        if major == "video":
            fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
            out["duration_seconds"] = (_pos_float(fmt.get("duration"))
                                       or _pos_float(stream.get("duration")))
    except Exception:
        logger.exception("media probe failed for %s", path)
        return dict(_NO_PROBE)
    return out
