"""wall_layout: box arithmetic for phone-placed (free-layout) video walls.

Pure functions; no database. Boxes are fractions of the canvas: x, y the
top-left corner, w, h the size.
"""
import pytest

import wall_layout as wl


def _box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def _pixel_aspect(box, canvas_w, canvas_h):
    return (box["w"] * canvas_w) / (box["h"] * canvas_h)


def test_constants():
    assert wl.DEFAULT_TV_ASPECT == pytest.approx(16 / 9)
    assert wl.MIN_OVERLAP == 0.05
    assert wl.MAX_TVS == 8
    assert wl.FALLBACK_CANVAS == (3840, 2160)
    assert wl.MAX_BOX_SIDE == 4.0


# ── canvas_size_for ────────────────────────────────────────────────────────

def test_canvas_size_is_the_media_size():
    assert wl.canvas_size_for(3840, 720) == (3840, 720)
    assert wl.canvas_size_for(1080, 1920) == (1080, 1920)


@pytest.mark.parametrize("w,h", [
    (None, None), (None, 720), (3840, None), (0, 720), (3840, -1), (True, 720), ("3840", "720"),
    (float("nan"), 720),
])
def test_canvas_size_falls_back_when_the_size_is_unknown(w, h):
    assert wl.canvas_size_for(w, h) == (3840, 2160)


# ── first_box ──────────────────────────────────────────────────────────────

def test_first_box_on_a_panorama_is_the_left_third():
    box = wl.first_box(3840, 720)
    assert box == pytest.approx(_box(0.0, 0.0, 1 / 3, 1.0))
    assert wl.box_to_px(box, 3840, 720) == pytest.approx(_box(0.0, 0.0, 1280.0, 720.0))


def test_first_box_on_a_16_9_canvas_is_the_whole_canvas():
    assert wl.first_box(3840, 2160) == pytest.approx(_box(0.0, 0.0, 1.0, 1.0))


def test_first_box_takes_the_tvs_own_shape():
    box = wl.first_box(3840, 720, tv_aspect=1080 / 1920)
    assert box["h"] == 1.0
    assert _pixel_aspect(box, 3840, 720) == pytest.approx(1080 / 1920)


def test_first_box_on_a_portrait_video_is_wider_than_the_canvas_and_centred():
    box = wl.first_box(1080, 1920)
    w = (16 / 9) * 1920 / 1080
    # Ruling R1: a box wider than the video is centred on it, so the picture
    # sits in the middle of the TV instead of at its left edge.
    assert box == pytest.approx(_box((1 - w) / 2, 0.0, w, 1.0))
    assert box["x"] + box["w"] / 2 == pytest.approx(0.5)
    assert wl.validate_box(box)


def test_first_box_exactly_as_wide_as_the_canvas_stays_at_the_left_edge():
    assert wl.first_box(3840, 2160)["x"] == 0.0
    assert wl.first_box(3840, 720)["x"] == 0.0


def test_first_box_on_a_very_tall_video_is_shrunk_to_the_size_limit():
    box = wl.first_box(1080, 5760)            # full height would be 9.48 canvases wide
    assert box["w"] == pytest.approx(4.0)
    assert box["x"] == pytest.approx(-1.5)    # centred: (1 - 4) / 2
    assert box["y"] == 0.0
    assert box["h"] < 1.0
    assert _pixel_aspect(box, 1080, 5760) == pytest.approx(16 / 9)
    assert wl.validate_box(box)


def test_first_box_with_an_unusable_canvas_uses_the_fallback():
    assert wl.first_box(0, 0) == pytest.approx(_box(0.0, 0.0, 1.0, 1.0))


# ── overlap_fraction ───────────────────────────────────────────────────────

@pytest.mark.parametrize("box,expected", [
    (_box(0.0, 0.0, 1.0, 1.0), 1.0),
    (_box(0.25, 0.25, 0.5, 0.5), 1.0),
    (_box(0.75, 0.0, 0.5, 1.0), 0.5),          # half hangs over the right edge
    (_box(0.5, 0.5, 1.0, 1.0), 0.25),          # only the top-left quarter is on it
    (_box(-1.0, -1.0, 3.0, 3.0), 1 / 9),       # bigger than the canvas
    (_box(1.0, 0.0, 0.5, 1.0), 0.0),           # touching the edge is not overlapping
    (_box(5.0, 5.0, 0.5, 0.5), 0.0),
    (_box(0.0, 0.0, 0.0, 1.0), 0.0),           # no area
])
def test_overlap_fraction(box, expected):
    assert wl.overlap_fraction(box) == pytest.approx(expected)


# ── nudge_into_canvas ──────────────────────────────────────────────────────

def test_nudge_leaves_a_box_with_enough_overlap_alone():
    box = _box(0.9, 0.1, 1.0, 0.5)             # 10% of it is on the canvas
    assert wl.nudge_into_canvas(box) == pytest.approx(box)


def test_nudge_pulls_a_box_back_from_the_right():
    box = wl.nudge_into_canvas(_box(5.0, 0.0, 0.5, 0.5))
    assert box == pytest.approx(_box(1.0 - 0.025, 0.0, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_pushes_a_box_in_from_the_left():
    box = wl.nudge_into_canvas(_box(-3.0, 0.0, 0.5, 0.5))
    assert box == pytest.approx(_box(0.025 - 0.5, 0.0, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_moves_y_only_when_the_box_misses_the_canvas_vertically():
    box = wl.nudge_into_canvas(_box(0.2, 3.0, 0.5, 0.5))     # below the canvas
    assert box == pytest.approx(_box(0.2, 1.0 - 0.025, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_a_box_off_a_corner_moves_both_axes():
    box = wl.nudge_into_canvas(_box(5.0, 5.0, 0.5, 0.5))
    assert box["w"] == 0.5 and box["h"] == 0.5
    assert wl.overlap_fraction(box) == pytest.approx(0.05)
    assert wl.validate_box(box)


def test_nudge_a_four_canvas_wide_box():
    box = wl.nudge_into_canvas(_box(10.0, 0.0, 4.0, 1.0))
    assert box == pytest.approx(_box(0.8, 0.0, 4.0, 1.0))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


# ── next_box ───────────────────────────────────────────────────────────────

def test_next_box_is_flush_right_of_the_previous_one():
    first = wl.first_box(3840, 720)
    second = wl.next_box(first, 3840, 720)
    third = wl.next_box(second, 3840, 720)
    assert second == pytest.approx(_box(1 / 3, 0.0, 1 / 3, 1.0))
    assert third == pytest.approx(_box(2 / 3, 0.0, 1 / 3, 1.0))
    assert wl.box_to_px(second, 3840, 720) == pytest.approx(_box(1280.0, 0.0, 1280.0, 720.0))


def test_next_box_keeps_the_previous_height_and_top_in_the_default_shape():
    # Ruling R3: the previous box's height, the width from 16:9 (not the
    # previous width), flush right of it and top-aligned.
    box = wl.next_box(_box(0.1, 0.2, 0.25, 0.5), 5760, 1080)
    assert box == pytest.approx(_box(0.35, 0.2, (16 / 9) * 0.5 * 1080 / 5760, 0.5))
    assert wl.has_default_shape(box, 5760, 1080)
    assert wl.next_box(_box(0.2, -0.1, 0.3, 0.8), 3840, 2160) == pytest.approx(
        _box(0.5, -0.1, 0.8, 0.8))


def test_next_box_after_a_portrait_neighbour_is_16_9_again():
    # TV 1 reported a portrait screen, so its box was refit to that shape; TV 2
    # starts 16:9, so its own screen report can still refit it.
    portrait = wl.fit_box_to_screen(wl.first_box(3840, 720), 1080, 1920, 3840, 720)
    box = wl.next_box(portrait, 3840, 720)
    assert box["x"] == pytest.approx(portrait["x"] + portrait["w"])
    assert box["y"] == pytest.approx(portrait["y"])
    assert box["h"] == pytest.approx(portrait["h"])
    assert _pixel_aspect(box, 3840, 720) == pytest.approx(16 / 9)
    assert wl.has_default_shape(box, 3840, 720)
    assert wl.validate_box(box)


def test_next_box_on_a_very_tall_video_is_shrunk_to_the_size_limit():
    box = wl.next_box(_box(0.0, 0.0, 0.2, 1.0), 1080, 5760)   # 16:9 at h = 1 is 9.48 wide
    assert box["w"] == pytest.approx(4.0)
    assert box["h"] == pytest.approx(4.0 / ((16 / 9) * 5760 / 1080))
    assert (box["x"], box["y"]) == pytest.approx((0.2, 0.0))
    assert _pixel_aspect(box, 1080, 5760) == pytest.approx(16 / 9)
    assert wl.validate_box(box)


def test_next_box_with_an_unusable_canvas_uses_the_fallback():
    assert wl.next_box(_box(0.0, 0.0, 0.25, 0.5), 0, None) == pytest.approx(
        _box(0.25, 0.0, 0.5, 0.5))               # 16:9 at h = 0.5 on 3840x2160


def test_a_fourth_tv_on_a_three_tv_panorama_is_nudged_back_to_5_percent():
    third = _box(2 / 3, 0.0, 1 / 3, 1.0)
    fourth = wl.next_box(third, 3840, 720)     # would start at x = 1.0, off the canvas
    assert fourth == pytest.approx(_box(1.0 - 0.05 / 3, 0.0, 1 / 3, 1.0))
    assert wl.overlap_fraction(fourth) == pytest.approx(0.05)


def test_second_full_size_box_on_a_16_9_canvas_is_nudged_back_to_5_percent():
    first = wl.first_box(3840, 2160)
    second = wl.next_box(first, 3840, 2160)
    assert second == pytest.approx(_box(0.95, 0.0, 1.0, 1.0))
    assert wl.overlap_fraction(second) == pytest.approx(0.05)
    assert wl.validate_box(second)


# ── validate_box ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("box", [
    _box(0.0, 0.0, 1 / 3, 1.0),
    _box(0.95, 0.0, 1.0, 1.0),                 # exactly 5% on the canvas
    _box(-1.5, -1.5, 4.0, 4.0),                # the largest size
    _box(0, 0, 1, 1),                          # ints are numbers too
])
def test_validate_box_accepts(box):
    assert wl.validate_box(box) is True


@pytest.mark.parametrize("box", [
    _box(0.0, 0.0, 0.0, 1.0),                  # no width
    _box(0.0, 0.0, 1.0, -0.5),
    _box(0.0, 0.0, 4.01, 1.0),                 # past the size limit
    _box(0.0, 0.0, 1.0, 4.5),
    _box(0.951, 0.0, 1.0, 1.0),                # 4.9% on the canvas
    _box(2.0, 2.0, 0.5, 0.5),                  # off the canvas
    _box(float("nan"), 0.0, 0.5, 0.5),
    _box(0.0, float("inf"), 0.5, 0.5),
    _box(True, 0.0, 0.5, 0.5),                 # a bool is not a coordinate
    _box("0.1", 0.0, 0.5, 0.5),
    _box(None, 0.0, 0.5, 0.5),
    {"x": 0.0, "y": 0.0, "w": 0.5},            # missing h
    None,
])
def test_validate_box_rejects(box):
    assert wl.validate_box(box) is False


# ── box_to_px ──────────────────────────────────────────────────────────────

def test_box_to_px_gives_floats_in_canvas_pixels():
    px = wl.box_to_px(_box(0.25, -0.5, 0.5, 2.0), 3840, 720)
    assert px == pytest.approx(_box(960.0, -360.0, 1920.0, 1440.0))
    assert all(isinstance(v, float) for v in px.values())


# ── fit_box_to_screen ──────────────────────────────────────────────────────

def test_fit_to_a_16_9_screen_keeps_a_16_9_box():
    box = wl.first_box(3840, 720)
    assert wl.fit_box_to_screen(box, 960, 540, 3840, 720) == pytest.approx(box)


def test_fit_to_a_16_10_screen_keeps_width_and_centre():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 1280, 800, 3840, 720)
    assert fitted == pytest.approx(_box(0.0, 0.5 - 5 / 9, 1 / 3, 10 / 9))
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(1280 / 800)
    assert wl.box_to_px(fitted, 3840, 720)["w"] == pytest.approx(1280.0)
    assert wl.box_to_px(fitted, 3840, 720)["h"] == pytest.approx(800.0)


def test_fit_to_a_portrait_screen_makes_a_tall_box_around_the_same_centre():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 1080, 1920, 3840, 720)
    h = 1280 * 1920 / 1080 / 720
    assert fitted == pytest.approx(_box(0.0, 0.5 - h / 2, 1 / 3, h))
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(1080 / 1920)
    assert wl.validate_box(fitted)


def test_fit_keeps_the_centre_of_a_box_away_from_the_origin():
    fitted = wl.fit_box_to_screen(_box(0.5, 0.25, 0.25, 0.5), 1920, 1080, 3840, 2160)
    assert fitted == pytest.approx(_box(0.5, 0.375, 0.25, 0.25))


def test_fit_to_an_extreme_screen_stays_within_the_size_limit():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 100, 10000, 3840, 720)
    assert fitted["h"] == pytest.approx(4.0)
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(100 / 10000)
    assert fitted["x"] + fitted["w"] / 2 == pytest.approx(1 / 6)     # same centre
    assert fitted["y"] + fitted["h"] / 2 == pytest.approx(0.5)
    assert wl.validate_box(fitted)


def test_fit_with_an_unusable_screen_size_returns_the_box_unchanged():
    box = wl.first_box(3840, 720)
    assert wl.fit_box_to_screen(box, 0, 540, 3840, 720) == pytest.approx(box)


# ── has_default_shape ──────────────────────────────────────────────────────

def test_has_default_shape():
    box = wl.first_box(3840, 720)
    assert wl.has_default_shape(box, 3840, 720) is True
    near = _box(0.0, 0.0, box["w"] * 1.009, 1.0)          # 0.9% wider: still 16:9
    assert wl.has_default_shape(near, 3840, 720) is True
    off = _box(0.0, 0.0, box["w"] * 1.02, 1.0)            # 2% wider: not
    assert wl.has_default_shape(off, 3840, 720) is False
    fitted = wl.fit_box_to_screen(box, 1280, 800, 3840, 720)
    assert wl.has_default_shape(fitted, 3840, 720) is False
    assert wl.has_default_shape(_box(0.0, 0.0, 0.5, 0.0), 3840, 720) is False


# ── lowest_free_index ──────────────────────────────────────────────────────

@pytest.mark.parametrize("used,expected", [
    ([], 0),
    ([0, 1, 3], 2),
    ({1, 2}, 0),
    (range(7), 7),
    ([8, 9], 0),                               # out-of-range values do not block 0..7
    ((i for i in [0, 2]), 1),                  # any iterable
])
def test_lowest_free_index(used, expected):
    assert wl.lowest_free_index(used) == expected


def test_lowest_free_index_is_none_when_full():
    assert wl.lowest_free_index(range(8)) is None
    assert wl.lowest_free_index([7, 6, 5, 4, 3, 2, 1, 0]) is None


def test_module_is_pure():
    # main imports walls, and walls will import this module: importing main
    # (or db) here would be circular, and the helpers must stay testable alone.
    source = open(wl.__file__, encoding="utf-8").read()
    for name in ("import main", "from main", "import db", "from db", "import walls", "from walls"):
        assert name not in source
