"""Box arithmetic for phone-placed ("free layout") video walls.

A free wall's canvas is its video. Each TV's box is stored as fractions of the
canvas -- x, y the top-left corner, w, h the size -- so 0..1 covers the canvas
and replacing the video keeps every box in the same relative place. A box may
hang over the canvas edges (that part shows black on the TV) as long as
MIN_OVERLAP of its own area stays on it.

Pure functions, no database and no I/O: used by the REST endpoints in main.py
and the wall socket in walls.py, and never imports either.
"""
import math

DEFAULT_TV_ASPECT = 16 / 9
MIN_OVERLAP = 0.05            # fraction of the box's own area that must lie on the canvas
MAX_TVS = 8
FALLBACK_CANVAS = (3840, 2160)
MAX_BOX_SIDE = 4.0            # w and h are at most 4 canvas widths / heights
SHAPE_TOLERANCE = 0.01        # pixel shapes within 1% of each other count as the same
_EPS = 1e-9                   # float slack when comparing an overlap with MIN_OVERLAP


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _xywh(box: dict) -> tuple:
    return float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])


def _axis_fraction(start: float, size: float) -> float:
    """Share of [start, start + size] that lies inside [0, 1]."""
    inside = min(start + size, 1.0) - max(start, 0.0)
    return max(0.0, inside) / size


def _move_axis(start: float, size: float, fraction: float) -> float:
    """New start for [start, start + size], moved the shortest way toward [0, 1]
    until `fraction` of its length lies inside."""
    length = fraction * size
    if start + size / 2 > 0.5:        # hangs past the far edge: pull it back
        return 1.0 - length
    return length - size              # hangs past the near edge: push it forward


def canvas_size_for(width_px, height_px) -> tuple:
    """The media's pixel size as a canvas, or FALLBACK_CANVAS when it is unknown."""
    if (_is_number(width_px) and _is_number(height_px)
            and width_px >= 1 and height_px >= 1):
        return int(width_px), int(height_px)
    return FALLBACK_CANVAS


def first_box(canvas_w: int, canvas_h: int, tv_aspect: float = DEFAULT_TV_ASPECT) -> dict:
    """The first TV's box: the full canvas height in the TV's shape, at the left
    edge (centred when wider than the canvas).

    On a 3840x720 panorama a 16:9 TV gets exactly the left third. A box that
    would be wider than MAX_BOX_SIDE canvases (a very tall video) is shrunk,
    keeping its shape.
    """
    if not (_is_number(canvas_w) and _is_number(canvas_h) and canvas_w > 0 and canvas_h > 0):
        canvas_w, canvas_h = FALLBACK_CANVAS
    if not (_is_number(tv_aspect) and tv_aspect > 0):
        tv_aspect = DEFAULT_TV_ASPECT
    w = tv_aspect * canvas_h / canvas_w
    h = 1.0
    if w > MAX_BOX_SIDE:
        h = MAX_BOX_SIDE / w
        w = MAX_BOX_SIDE
    # Ruling R1: a box wider than the video (a portrait video on a 16:9 TV) is
    # centred, so the picture sits in the middle of that TV.
    x = (1.0 - w) / 2 if w > 1.0 else 0.0
    return {"x": x, "y": 0.0, "w": w, "h": h}


def overlap_fraction(box: dict) -> float:
    """Share of the box's own area that lies on the canvas, 0..1."""
    x, y, w, h = _xywh(box)
    if not (w > 0 and h > 0):
        return 0.0
    return _axis_fraction(x, w) * _axis_fraction(y, h)


def nudge_into_canvas(box: dict) -> dict:
    """The box moved the shortest way onto the canvas when less than MIN_OVERLAP
    of it is on it; otherwise unchanged. Its size never changes.

    Only x moves, unless the box is so far above or below the canvas that x
    alone cannot reach MIN_OVERLAP; then y first moves just far enough.
    """
    x, y, w, h = _xywh(box)
    if not (w > 0 and h > 0) or overlap_fraction(box) >= MIN_OVERLAP - _EPS:
        return {"x": x, "y": y, "w": w, "h": h}
    fx_max = min(1.0, 1.0 / w)
    fy_max = min(1.0, 1.0 / h)
    fy = _axis_fraction(y, h)
    if fy * fx_max < MIN_OVERLAP - _EPS:
        y = _move_axis(y, h, min(fy_max, MIN_OVERLAP / fx_max))
        fy = _axis_fraction(y, h)
    if _axis_fraction(x, w) * fy < MIN_OVERLAP - _EPS:
        x = _move_axis(x, w, min(fx_max, MIN_OVERLAP / fy))
    return {"x": x, "y": y, "w": w, "h": h}


def next_box(prev: dict, canvas_w: int, canvas_h: int) -> dict:
    """A newly added TV's box: the previous box's height in the default 16:9
    shape, flush to its right edge and top-aligned, nudged back if that leaves
    too little of it on the canvas.

    Ruling R3: the default shape, not the previous box's own shape, so a TV
    added next to a refit portrait TV still has_default_shape and is refit to
    its own screen when it reports. For identical 16:9 TVs it is the previous
    box's size. A box that would be wider than MAX_BOX_SIDE is shrunk, keeping
    the shape.
    """
    x, y, w, h = _xywh(prev)
    if not (_is_number(canvas_w) and _is_number(canvas_h) and canvas_w > 0 and canvas_h > 0):
        canvas_w, canvas_h = FALLBACK_CANVAS
    new_h = h
    new_w = DEFAULT_TV_ASPECT * h * canvas_h / canvas_w
    if new_w > MAX_BOX_SIDE:
        new_h = h * MAX_BOX_SIDE / new_w
        new_w = MAX_BOX_SIDE
    return nudge_into_canvas({"x": x + w, "y": y, "w": new_w, "h": new_h})


def validate_box(box: dict) -> bool:
    """True for finite numbers with 0 < w, h <= MAX_BOX_SIDE and at least
    MIN_OVERLAP of the box on the canvas."""
    try:
        values = [box[key] for key in ("x", "y", "w", "h")]
    except (KeyError, TypeError):
        return False
    if not all(_is_number(v) for v in values):
        return False
    _x, _y, w, h = (float(v) for v in values)
    if not (0 < w <= MAX_BOX_SIDE and 0 < h <= MAX_BOX_SIDE):
        return False
    return overlap_fraction(box) >= MIN_OVERLAP - _EPS


def box_to_px(box: dict, canvas_w: int, canvas_h: int) -> dict:
    """The box in canvas pixels (floats): what the TV crops and scales."""
    x, y, w, h = _xywh(box)
    cw, ch = float(canvas_w), float(canvas_h)
    return {"x": x * cw, "y": y * ch, "w": w * cw, "h": h * ch}


def fit_box_to_screen(box: dict, screen_w: int, screen_h: int,
                      canvas_w: int, canvas_h: int) -> dict:
    """The box reshaped to the TV's screen: same width and centre, the height
    that gives it the screen's pixel shape.

    A very tall screen on a very wide canvas would need a box taller than
    MAX_BOX_SIDE; it is then shrunk around the same centre, keeping the
    screen's shape. The result is nudged back onto the canvas if needed.
    """
    x, y, w, h = _xywh(box)
    if not (screen_w > 0 and screen_h > 0 and canvas_w > 0 and canvas_h > 0
            and w > 0 and h > 0):
        return {"x": x, "y": y, "w": w, "h": h}
    cx, cy = x + w / 2, y + h / 2
    new_h = w * canvas_w * screen_h / (screen_w * canvas_h)
    if new_h > MAX_BOX_SIDE:
        w = w * MAX_BOX_SIDE / new_h
        new_h = MAX_BOX_SIDE
    return nudge_into_canvas({"x": cx - w / 2, "y": cy - new_h / 2, "w": w, "h": new_h})


def has_default_shape(box: dict, canvas_w: int, canvas_h: int) -> bool:
    """True while the box's pixel shape is within SHAPE_TOLERANCE of 16:9, the
    shape a TV's box has until the TV reports its real screen size."""
    _x, _y, w, h = _xywh(box)
    if not (w > 0 and h > 0 and canvas_w > 0 and canvas_h > 0):
        return False
    aspect = (w * canvas_w) / (h * canvas_h)
    return abs(aspect / DEFAULT_TV_ASPECT - 1.0) <= SHAPE_TOLERANCE


def lowest_free_index(used) -> int | None:
    """The lowest TV index 0..MAX_TVS-1 not in `used`, or None when the wall is full."""
    taken = set(used)
    for index in range(MAX_TVS):
        if index not in taken:
            return index
    return None
