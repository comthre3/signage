"""End-to-end video wall test, driven through the API (the phone UI comes
later). Run it with run.sh --wall, which starts the stack: the backend built
from this tree with 4 workers (as in production), its own Postgres and Redis,
and a player container whose API_BASE_URL is that backend.

Loopback mode. The TVs load the player from http://127.0.0.1:13022 and talk
to the API at http://127.0.0.1:18020 directly, WebSockets and video Range
requests included. Playwright's routing never sees WebSockets: a TV loaded
as https://play.khanshoof.com would open its wall socket to production.
Every request to a khanshoof.com host, or to a port production publishes on
this host's loopback (8000, 3000, 3001, 3003: the player and the dashboard
fall back to <page host>:8000 when API_BASE_URL is empty), is refused and
recorded instead, and the test fails if there was one.

Three TVs, each in its own browser context, with Date.now() shifted the way
a TV with a wrong clock would be:
  TV 1  1920x1080           -3000 ms
  TV 2   960x540 at DPR 2       0 ms   (a high-DPR TV reports CSS px)
  TV 3  1920x1080           +2000 ms
The 5760x1080 test video (shots/wall-test.mp4, made by run.sh) has red,
green and blue thirds with large black digits 1, 2, 3 and a white bar
sweeping across. The backend transcodes it to 3840x720.

Checks, in the order they run:
  1. TV 1's QR decodes to https://app.khanshoof.com/pair?code=<its code>
  2. the upload is transcoded to 3840x720 (the 3840 px cap), 20 s long
  3. POST /walls/free with TV 1's code, then POST /walls/{id}/tvs for TV 2
     and TV 3: each TV leaves its pairing screen and plays the wall video
  b. crop: with the boxes on the red, green and blue thirds, each TV's
     .wall-canvas transform is scale(screen / box) translate(-box), and its
     screenshot is mostly its own band's colour, with its digit in the
     middle and no black at the edges; each TV reported its screen size
  c. live move: a placement PUT reaches TV 2's transform in under 1 s, five
     times out of five. The tick loop's fallback takes up to 5 s, and with 4
     workers the PUT and TV 2's socket are usually on different ones, so
     this is the Redis path. Then one move onto the blue third: within 1 s
     the transform follows and TV 2's picture turns from green to blue
  d. highlight: "TV 2" shows while highlight=true and goes within 1 s of a
     highlight=false PUT (the same Redis path); TV 1 and TV 3 show none
  e. sync: the TVs' video.currentTime agree within 100 ms (median of 12
     samples, each 6 s or more into the loop), despite the shifted clocks
  f. DELETE TV 3's pairing: TV 3 shows a new pairing code; the wall keeps
     two cells, and TV 1 and TV 2 stay in wall mode
  a. every TV's WebSockets went to ws://127.0.0.1:18020/walls/<id>/ws, and
     no request went to a khanshoof.com host or to production's loopback ports
Screenshots: shots/wall-*.png (wall-fail-tv<n>.png when a check fails).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.environ.get("E2E_PYLIB", "/w/.pylib"))
import zxingcpp  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

API = os.environ.get("E2E_API", "http://127.0.0.1:18020").rstrip("/")
LAN_PLAY = os.environ.get("E2E_PLAY_LAN", "http://127.0.0.1:13022").rstrip("/")
OUT = os.environ.get("E2E_OUT", "/w/shots")
USER = os.environ.get("E2E_USER", "demo")
PASSWORD = os.environ.get("E2E_PASSWORD", "Khanshoof-Demo-2026")
VIDEO = os.environ.get("E2E_WALL_VIDEO", os.path.join(OUT, "wall-test.mp4"))
# What the TV's QR points at: the player container's APP_URL (run.sh). The
# test never loads it.
APP_URL = "https://app.khanshoof.com"
# Where every wall socket must go: the local API, http -> ws.
WS_PREFIX = "ws" + API[len("http"):] + "/walls/"

KHAN = re.compile(r"^(https?|wss?)://([^/]*\.)?khanshoof\.com(:\d+)?(/|$)")
# Production publishes its API, dashboard, player and landing page on this
# host's loopback. The player and the dashboard fall back to <page host>:8000
# when API_BASE_URL is empty, so a misconfigured container would reach
# production's API at 127.0.0.1:8000. Refused and recorded like KHAN.
PROD_LOCAL = re.compile(r"^(https?|wss?)://(127\.0\.0\.1|localhost|\[::1\]):(8000|3000|3001|3003)(/|$)")
FONTS = re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/")
SKEW_JS = ("(() => { const off = %d; const real = Date.now; "
           "Date.now = function () { return real.call(Date) + off; }; })();")
BLOCKED = []   # khanshoof.com and production-loopback URLs some page asked for (all refused)

THIRD = 1.0 / 3.0
# Which channel is high and which two are low, per band.
BANDS = {"red": (0, 1, 2), "green": (1, 0, 2), "blue": (2, 0, 1)}
TVS = [
    {"n": 1, "size": (1920, 1080), "scale": 1, "skew_ms": -3000, "band": "red", "x": 0.0},
    {"n": 2, "size": (960, 540), "scale": 2, "skew_ms": 0, "band": "green", "x": THIRD},
    {"n": 3, "size": (1920, 1080), "scale": 1, "skew_ms": 2000, "band": "blue", "x": 2 * THIRD},
]

# page.evaluate, not wait_for_function: the latter evaluates a string, which
# the player's CSP (script-src 'self') refuses, and the CSP stays on.
TRANSFORM_JS = """() => {
  const c = document.querySelector('#content .wall-canvas');
  return c ? getComputedStyle(c).transform : null;
}"""
PLAYING_JS = """() => {
  const v = document.querySelector('#content .wall-canvas video');
  return v ? {src: v.currentSrc, ready: v.readyState, paused: v.paused} : null;
}"""
HIGHLIGHT_JS = """() => {
  const h = document.getElementById('wall-highlight');
  if (!h) return null;
  const label = document.getElementById('wall-highlight-label');
  return {shown: !h.classList.contains('hidden') && getComputedStyle(h).display !== 'none',
          label: label ? label.textContent.trim() : ''};
}"""
SAMPLE_JS = """() => {
  const v = document.querySelector('#content .wall-canvas video');
  if (!v) return null;
  return {t: v.currentTime, at: performance.timeOrigin + performance.now(),
          ok: !v.paused && !v.seeking && !v.ended && v.readyState >= 3,
          rate: v.playbackRate, dur: v.duration};
}"""
# This page's Date.now() against the real clock: the shift SKEW_JS applied.
SKEW_READ_JS = "() => Date.now() - (performance.timeOrigin + performance.now())"
MODE_JS = "() => (typeof mode === 'undefined' ? null : mode)"


def api(method, path, token=None, body=None, raw=None, content_type=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data, headers["Content-Type"] = raw, content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path} -> {e.code}: {e.read()[:500]!r}") from None


def multipart(field, filename, payload, mime):
    boundary = "----e2e-scan-to-pair"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    return head + payload + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def decode_qr(element, name):
    path = f"{OUT}/{name}"
    element.screenshot(path=path)
    found = zxingcpp.read_barcodes(Image.open(path))
    assert found, "no QR code found in the TV's pixels"
    return found[0].text


def wait_for_code(page, previous=None, timeout_s=20):
    """The pairing code the TV shows, once its pairing screen is up. With
    previous, waits for a different one: a TV sent back to pairing keeps
    its old code in the DOM until the new one arrives."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if page.is_visible("#pairing"):
            code = page.inner_text("#pairing-code").strip()
            if re.fullmatch(r"[A-Z2-9]{6}", code) and code != previous:
                page.wait_for_timeout(1200)   # fonts, QR image decode
                return code
        page.wait_for_timeout(250)
    raise AssertionError(f"TV never showed a {'new ' if previous else ''}pairing code")


def _refuse_khanshoof(route):
    BLOCKED.append(route.request.url)
    return route.abort()


def lan_context(browser, locale="en-US", size=(1920, 1080), scale=1, touch=False,
                skew_ms=0, device=None, block_fonts=True):
    """A context for loopback mode. Only requests to khanshoof.com and to
    production's loopback ports (PROD_LOCAL) are routed: refused and recorded
    in BLOCKED. The test's own loopback traffic goes straight to the
    containers. skew_ms shifts Date.now() from before the page's first
    script runs (add_init_script is not subject to the CSP) and survives
    the player's own reloads. block_fonts refuses Google Fonts: this host's
    DNS is unreliable, and a hung font stylesheet would hold up the page's
    load event for nothing the checks look at."""
    opts = dict(device) if device else {
        "viewport": {"width": size[0], "height": size[1]},
        "device_scale_factor": scale, "has_touch": touch}
    ctx = browser.new_context(locale=locale, service_workers="block", **opts)
    ctx.route(KHAN, _refuse_khanshoof)
    ctx.route(PROD_LOCAL, _refuse_khanshoof)
    if block_fonts:
        ctx.route(FONTS, lambda route: route.abort())
    if skew_ms:
        ctx.add_init_script(script=SKEW_JS % skew_ms)
    return ctx


def wait_eval(page, js, pred, timeout_s, what="the condition", interval_ms=100):
    """Polls page.evaluate(js) until pred(value) holds; returns the value."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = page.evaluate(js)
        if pred(value):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what}; "
                                 f"last value: {value!r}")
        page.wait_for_timeout(interval_ms)


def _classify(px):
    for name, (hi, lo1, lo2) in BANDS.items():
        if px[hi] >= 150 and px[lo1] <= 100 and px[lo2] <= 100:
            return name
    if max(px[:3]) <= 60:
        return "dark"
    return "other"


def _grid_counts(path, box=(0.0, 0.0, 1.0, 1.0), cols=64, rows=36):
    """Classifies a cols x rows grid of points spread over box (fractions
    of the picture): red, green, blue, dark or other."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = box
    counts = {}
    for j in range(rows):
        for i in range(cols):
            x = min(w - 1, int((x0 + (x1 - x0) * (i + 0.5) / cols) * w))
            y = min(h - 1, int((y0 + (y1 - y0) * (j + 0.5) / rows) * h))
            name = _classify(img.getpixel((x, y)))
            counts[name] = counts.get(name, 0) + 1
    return counts, cols * rows


def dominant_color(path):
    """The band colour most of a screenshot shows, and its share of the
    sample points. Thresholds, not exact values: pure green comes back as
    about (0, 216, 2) after yuv420. The black digit, the white bar and the
    highlight's outline count as no band."""
    counts, total = _grid_counts(path)
    bands = {k: v for k, v in counts.items() if k in BANDS}
    if not bands:
        return None, 0.0
    best = max(bands, key=bands.get)
    return best, bands[best] / total


def dark_share(path, box):
    """Share of near-black sample points in box (fractions of the picture)."""
    counts, total = _grid_counts(path, box, cols=32, rows=24)
    return counts.get("dark", 0) / total


def save_montage(paths, out):
    tiles = [Image.open(p).convert("RGB").resize((640, 360)) for p in paths]
    sheet = Image.new("RGB", (640 * len(tiles), 360))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (640 * i, 0))
    sheet.save(out)


def parse_matrix(value):
    """getComputedStyle(el).transform -> [a, b, c, d, e, f], or None."""
    m = re.fullmatch(r"matrix\(([^)]*)\)", (value or "").strip())
    return [float(v) for v in m.group(1).split(",")] if m else None


def expected_matrix(box, canvas, viewport):
    """The crop the player must set for box (fractions of the canvas):
    scale(vw / box_w_px, vh / box_h_px) translate(-box_x_px, -box_y_px)
    with transform-origin 0 0, which computes to
    matrix(sx, 0, 0, sy, -x_px * sx, -y_px * sy)."""
    cw, ch = canvas
    vw, vh = viewport
    sx = vw / (box["w"] * cw)
    sy = vh / (box["h"] * ch)
    return [sx, 0.0, 0.0, sy, -box["x"] * cw * sx + 0.0, -box["y"] * ch * sy + 0.0]   # + 0.0: no -0.0


def matrix_close(got, want):
    return got is not None and len(got) == 6 and all(
        abs(g - w) <= (0.002 if i < 4 else 1.0) for i, (g, w) in enumerate(zip(got, want)))


def sample_sync_spread_ms(pages, samples=12, gap_ms=250, max_rounds=100, settle_s=6.0, end_s=1.0):
    """Median spread, in ms, of the pages' video.currentTime at one instant;
    returns (median, the spreads sorted).

    Each round reads every page once, a few ms apart, and moves each reading
    back to the first page's instant along its own playback rate. The
    instants come from performance.timeOrigin + performance.now(), the real
    clock, which the Date.now() shift leaves alone. A round counts only while
    every video plays (not paused, seeking, ended or buffering) and is at
    least settle_s into its loop and end_s before its end. The one-item wall
    playlist restarts the video every slot with a seek, which leaves each TV
    up to a few hundred ms off until the drift corrector (every 2 s) has
    pulled it back within 50 ms; and a round straddling the restart would
    compare 19.9 s with 0.1 s.
    """
    spreads = []
    for _ in range(max_rounds):
        if len(spreads) >= samples:
            break
        s = [pg.evaluate(SAMPLE_JS) for pg in pages]
        if all(x and x["ok"] for x in s):
            ref = s[0]["at"]
            adj = [x["t"] - (x["at"] - ref) / 1000.0 * x["rate"] for x in s]
            dur = s[0]["dur"] or 20.0
            if all(settle_s <= t <= dur - end_s for t in adj):
                spreads.append((max(adj) - min(adj)) * 1000.0)
        pages[0].wait_for_timeout(gap_ms)
    assert len(spreads) >= samples, f"only {len(spreads)} usable sync samples in {max_rounds} rounds"
    spreads.sort()
    return spreads[len(spreads) // 2], spreads


def placement_path(wall_id, cell):
    return f"/walls/{wall_id}/cells/{cell['row_index']}/{cell['col_index']}/placement"


def box_body(x, highlight):
    return {"x": x, "y": 0.0, "w": THIRD, "h": 1.0, "highlight": highlight}


def wait_transcoded(auth, media_id, timeout_s=180):
    """The media row once transcode_video has swapped in the .opt.mp4. The
    same UPDATE stores the transcoded file's probed size and length."""
    deadline = time.monotonic() + timeout_s
    row = None
    while time.monotonic() < deadline:
        row = next((m for m in api("GET", "/media", auth) if m["id"] == media_id), None)
        if row and row["filename"].endswith(".opt.mp4"):
            return row
        time.sleep(1)
    raise AssertionError(f"media {media_id} was never transcoded: {row!r}")


def wait_playing(page, n, timeout_s=40):
    page.wait_for_selector("#pairing", state="hidden", timeout=timeout_s * 1000)
    return wait_eval(page, PLAYING_JS, lambda v: bool(v) and v["ready"] >= 3 and not v["paused"],
                     timeout_s, f"TV {n} to play the wall video")


def timed_move(page, auth, wall_id, tv, x, canvas, timeout_s=7.0):
    """PUTs the TV's box to x, highlight on (as the phone does while
    dragging), and returns the seconds until the TV's transform shows it.
    Waits past the tick loop's 5 s fallback, so a slow move is measured
    rather than lost."""
    want = expected_matrix({"x": x, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(x, True))
    while True:
        got = page.evaluate(TRANSFORM_JS)
        dt = time.monotonic() - t0
        if matrix_close(parse_matrix(got), want):
            return dt
        if dt > timeout_s:
            raise AssertionError(f"TV {tv['n']} never moved to x={x:.4f}: "
                                 f"transform {got}, expected {want}")
        page.wait_for_timeout(50)


def timed_colour_move(page, auth, wall_id, tv, x, band, canvas, path, timeout_s=7.0):
    """Like timed_move, onto a box over another band: PUTs the TV's box to x
    (highlight on) and returns (seconds until the transform shows it, seconds
    until a screenshot of the TV is mostly band, that share). So the picture
    itself is checked, not only the matrix. The screenshots are at CSS scale
    (960x540 for TV 2), which is quicker; each one replaces path."""
    want = expected_matrix({"x": x, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
    t_matrix, got, share = None, None, 0.0
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(x, True))
    while True:
        if t_matrix is None and matrix_close(parse_matrix(page.evaluate(TRANSFORM_JS)), want):
            t_matrix = time.monotonic() - t0
        if t_matrix is not None:
            page.screenshot(path=path, scale="css")
            got, share = dominant_color(path)
            # b.'s 75%. The outline and "TV n" are up while placing; with them
            # a blue third still measured 92% (96% without).
            if got == band and share >= 0.75:
                return t_matrix, time.monotonic() - t0, share
        if time.monotonic() - t0 > timeout_s:
            moved = "never" if t_matrix is None else f"after {t_matrix:.2f} s"
            raise AssertionError(f"TV {tv['n']} never showed {band} at x={x:.4f} within {timeout_s} s: "
                                 f"transform {moved}, picture {got} ({share:.0%})")
        page.wait_for_timeout(50)


def api_wall_checks(auth, media, tvs, timings, t_upload):
    tv1, tv2, tv3 = tvs

    # 1. TV 1's QR
    code1 = wait_for_code(tv1["page"])
    tv1["page"].screenshot(path=f"{OUT}/wall-tv1-pairing.png")
    qr = decode_qr(tv1["page"].locator("#pairing-qr"), "wall-tv1-qr.png")
    assert qr == f"{APP_URL}/pair?code={code1}", (qr, code1)
    print(f"1. TV 1 shows {code1}; its QR decodes to {qr}")

    # 2. The transcode caps the 5760x1080 upload at 3840 px.
    row = wait_transcoded(auth, media["id"])
    timings["upload_to_transcoded_s"] = round(time.monotonic() - t_upload, 1)
    assert (row["width_px"], row["height_px"]) == (3840, 720), row
    assert abs((row["duration_seconds"] or 0) - 20.0) < 0.2, row
    print(f"2. upload transcoded to {row['width_px']}x{row['height_px']}, "
          f"{row['duration_seconds']:.2f} s, {timings['upload_to_transcoded_s']} s after the upload")

    # 3. The wall, TV by TV.
    tv1["code"] = code1
    t0 = time.monotonic()
    wall = api("POST", "/walls/free", auth,
               body={"name": "E2E wall", "media_id": media["id"], "code": code1})
    wall_id = wall["id"]
    assert wall["layout"] == "free" and wall["mode"] == "spanned", wall
    canvas = (wall["canvas_width_px"], wall["canvas_height_px"])
    assert canvas == (3840, 720), canvas
    tv1["cell"] = next(c for c in wall["cells"] if c["col_index"] == 0)
    wait_playing(tv1["page"], 1)
    timings["tv1_claim_to_playing_s"] = round(time.monotonic() - t0, 1)
    for tv in (tv2, tv3):
        tv["code"] = wait_for_code(tv["page"])
        t0 = time.monotonic()
        tv["cell"] = api("POST", f"/walls/{wall_id}/tvs", auth, body={"code": tv["code"]})
        assert tv["cell"]["col_index"] == tv["n"] - 1, tv["cell"]
        wait_playing(tv["page"], tv["n"])
        timings[f"tv{tv['n']}_claim_to_playing_s"] = round(time.monotonic() - t0, 1)
    t_all_playing = time.monotonic()
    for tv in tvs:
        src = tv["page"].evaluate(PLAYING_JS)["src"]
        assert src == f"{API}/uploads/{row['filename']}", (tv["n"], src)
    print(f"3. wall {wall_id}: TV 1 joined by POST /walls/free, TV 2 and TV 3 by /tvs; "
          f"claim to playing {timings['tv1_claim_to_playing_s']} / "
          f"{timings['tv2_claim_to_playing_s']} / {timings['tv3_claim_to_playing_s']} s")

    # b. Crop: boxes on the red, green and blue thirds.
    for tv in tvs:
        api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(tv["x"], False))
    shots = []
    for tv in tvs:
        want = expected_matrix({"x": tv["x"], "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
        got = wait_eval(tv["page"], TRANSFORM_JS,
                        lambda v, want=want: matrix_close(parse_matrix(v), want),
                        6, f"TV {tv['n']}'s crop matrix{tuple(round(v, 3) for v in want)}")
        tv["page"].wait_for_timeout(300)
        path = f"{OUT}/wall-tv{tv['n']}.png"
        tv["page"].screenshot(path=path)
        shots.append(path)
        band, share = dominant_color(path)
        centre = dark_share(path, (0.35, 0.25, 0.65, 0.75))
        edges = max(dark_share(path, (0.0, 0.0, 0.2, 1.0)), dark_share(path, (0.0, 0.85, 1.0, 1.0)))
        assert band == tv["band"] and share >= 0.75, \
            f"TV {tv['n']} shows mostly {band} ({share:.0%}), expected {tv['band']}"
        assert centre >= 0.08, f"TV {tv['n']}: no digit in the middle (dark share {centre:.2f})"
        assert edges <= 0.02, \
            f"TV {tv['n']}: black at the edges ({edges:.2f}), so the slice does not fill the screen"
        print(f"b. TV {tv['n']} ({tv['size'][0]}x{tv['size'][1]} @{tv['scale']}x): {got}; "
              f"{share:.0%} {band}, digit in the middle")
    save_montage(shots, f"{OUT}/wall-montage.png")
    want_sizes = {tv["cell"]["col_index"]: tv["size"] for tv in tvs}
    deadline = time.monotonic() + 10
    while True:
        cells = {c["col_index"]: c for c in api("GET", f"/walls/{wall_id}", auth)["cells"]}
        got_sizes = {k: (cells[k]["screen_w_px"], cells[k]["screen_h_px"])
                     for k in want_sizes if k in cells}
        if got_sizes == want_sizes:
            break
        assert time.monotonic() < deadline, \
            f"screen sizes the TVs reported: {got_sizes}, expected {want_sizes}"
        time.sleep(0.5)
    print(f"b. reported screen sizes (CSS px) by cell: {got_sizes}")
    # b. Decision 6: a box may hang off the video while 5% of it overlaps, and
    # the TV shows black where it is off. TV 1's box, half off the left edge:
    half_off = {"x": -THIRD / 2, "y": 0.0, "w": THIRD, "h": 1.0}
    api("PUT", placement_path(wall_id, tv1["cell"]), auth, body=dict(half_off, highlight=False))
    want_off = expected_matrix(half_off, canvas, tv1["size"])
    wait_eval(tv1["page"], TRANSFORM_JS, lambda v: matrix_close(parse_matrix(v), want_off), 6,
              f"TV 1's half-off crop matrix{tuple(round(v, 3) for v in want_off)}")
    tv1["page"].wait_for_timeout(300)
    half_path = f"{OUT}/wall-tv1-half-off.png"
    tv1["page"].screenshot(path=half_path)
    off = dark_share(half_path, (0.0, 0.0, 0.45, 1.0))
    assert off >= 0.95, f"TV 1: the part of its box off the video is not black ({off:.2f})"
    api("PUT", placement_path(wall_id, tv1["cell"]), auth, body=box_body(0.0, False))
    want_back = expected_matrix({"x": 0.0, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv1["size"])
    wait_eval(tv1["page"], TRANSFORM_JS, lambda v: matrix_close(parse_matrix(v), want_back), 6,
              "TV 1's crop back on the red third")
    print(f"b. TV 1 with half its box off the video: {off:.0%} black on that side")

    # c. Live move: five placement PUTs for TV 2, one onto blue, then back onto green.
    moves = []
    for i in range(5):
        x = THIRD + (0.05 if i % 2 == 0 else -0.05)
        moves.append(timed_move(tv2["page"], auth, wall_id, tv2, x, canvas))
    median_move = sorted(moves)[len(moves) // 2]
    timings["live_moves_s"] = [round(m, 3) for m in moves]
    timings["live_move_median_s"] = round(median_move, 3)
    assert max(moves) < 1.0, \
        f"a move took {max(moves):.2f} s to reach TV 2 ({timings['live_moves_s']}): " \
        "the Redis path did not deliver it, the tick loop's fallback did"
    print(f"c. live move: PUT to TV 2's transform, median {median_move * 1000:.0f} ms "
          f"(all: {[round(m * 1000) for m in moves]} ms)")
    # Those moves keep TV 2 on green. Onto the blue third the picture itself
    # must change too, not only the matrix.
    t_matrix, t_colour, share = timed_colour_move(
        tv2["page"], auth, wall_id, tv2, 2 * THIRD, "blue", canvas, f"{OUT}/wall-tv2-moved-blue.png")
    timings["live_colour_move_s"] = round(t_colour, 3)
    assert t_colour < 1.0, \
        f"TV 2's picture turned blue {t_colour:.2f} s after its box moved there (transform after " \
        f"{t_matrix:.2f} s): the Redis path did not deliver it, or the picture did not repaint"
    print(f"c. live move onto blue: TV 2's transform after {t_matrix * 1000:.0f} ms, "
          f"picture {share:.0%} blue after {t_colour * 1000:.0f} ms")
    timed_move(tv2["page"], auth, wall_id, tv2, THIRD, canvas)

    # d. Highlight: on since the moves (highlight=true), off within 1 s of the
    # highlight=false PUT (the phone's Done), which rides the moves' Redis path.
    hl = wait_eval(tv2["page"], HIGHLIGHT_JS, lambda v: bool(v) and v["shown"], 3,
                   "TV 2's highlight")
    assert hl["label"] == "TV 2", hl
    for tv in (tv1, tv3):
        other = tv["page"].evaluate(HIGHLIGHT_JS)
        assert other is not None and not other["shown"], (tv["n"], other)
    tv2["page"].screenshot(path=f"{OUT}/wall-tv2-highlight.png")
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv2["cell"]), auth, body=box_body(THIRD, False))
    wait_eval(tv2["page"], HIGHLIGHT_JS, lambda v: bool(v) and not v["shown"], 6,
              "TV 2's highlight to go")
    off_s = time.monotonic() - t0
    timings["highlight_off_s"] = round(off_s, 2)
    # The 6 s wait is only a deadline, so a slow value is measured, not lost.
    assert off_s < 1.0, \
        f"TV 2's highlight went {off_s:.2f} s after highlight=false (bound 1 s): " \
        "the Redis path did not deliver it, the tick loop's fallback did"
    print(f"d. highlight '{hl['label']}' on TV 2 only; gone {timings['highlight_off_s']} s "
          "after highlight=false")

    # e. Sync, despite the shifted clocks.
    for tv in tvs:
        skew = tv["page"].evaluate(SKEW_READ_JS)
        assert abs(skew - tv["skew_ms"]) < 250, (tv["n"], skew, tv["skew_ms"])
    rest_s = 6.0 - (time.monotonic() - t_all_playing)
    if rest_s > 0:
        tv1["page"].wait_for_timeout(int(rest_s * 1000))
    median_ms, spreads = sample_sync_spread_ms([tv["page"] for tv in tvs])
    timings["sync_median_ms"] = round(median_ms, 1)
    assert median_ms < 100, f"currentTime spread: median {median_ms:.0f} ms of {spreads}"
    print(f"e. sync: median spread {median_ms:.0f} ms over {len(spreads)} samples "
          f"(max {spreads[-1]:.0f} ms); TV clocks shifted -3000 / 0 / +2000 ms")

    # f. Remove TV 3: it goes back to a fresh pairing code.
    t0 = time.monotonic()
    api("DELETE", f"/walls/{wall_id}/cells/{tv3['cell']['row_index']}/"
                  f"{tv3['cell']['col_index']}/pairing", auth)
    new_code = wait_for_code(tv3["page"], previous=tv3["code"], timeout_s=30)
    timings["unpair_to_new_code_s"] = round(time.monotonic() - t0, 1)
    tv3["page"].screenshot(path=f"{OUT}/wall-tv3-unpaired.png")
    left = api("GET", f"/walls/{wall_id}", auth)
    assert sorted(c["col_index"] for c in left["cells"]) == [0, 1] and left["cols"] == 2, left
    for tv in (tv1, tv2):
        assert tv["page"].evaluate(MODE_JS) == "wall", (tv["n"], tv["page"].evaluate(MODE_JS))
    print(f"f. TV 3 removed: shows {new_code} {timings['unpair_to_new_code_s']} s later; "
          "the wall keeps TV 1 and TV 2")

    # a. Nothing reached production.
    for tv in tvs:
        assert tv["ws"], f"TV {tv['n']} opened no WebSocket"
        stray = [u for u in tv["ws"] if not u.startswith(f"{WS_PREFIX}{wall_id}/ws?")]
        assert not stray, (tv["n"], stray)
    assert not BLOCKED, f"pages asked for khanshoof.com or production's loopback ports (refused): {BLOCKED}"
    print(f"a. {sum(len(tv['ws']) for tv in tvs)} TV sockets, all to {WS_PREFIX}{wall_id}/ws; "
          "no request to khanshoof.com or production's loopback ports")


def api_wall(browser, auth):
    timings = {}
    # Upload first: the transcode (about 10 s) runs while the TVs start.
    with open(VIDEO, "rb") as f:
        raw, ctype = multipart("file", "wall-test.mp4", f.read(), "video/mp4")
    t_upload = time.monotonic()
    media = api("POST", "/media/upload", auth, raw=raw, content_type=ctype)

    tvs = []
    try:
        for spec in TVS:
            ctx = lan_context(browser, size=spec["size"], scale=spec["scale"],
                              skew_ms=spec["skew_ms"])
            tv = dict(spec, ctx=ctx, page=ctx.new_page(), ws=[])
            tv["page"].on("websocket", lambda w, urls=tv["ws"]: urls.append(w.url))
            tvs.append(tv)
            tv["page"].goto(LAN_PLAY + "/")
        api_wall_checks(auth, media, tvs, timings, t_upload)
    except Exception:
        for tv in tvs:
            try:
                tv["page"].screenshot(path=f"{OUT}/wall-fail-tv{tv['n']}.png")
            except Exception:
                pass
        raise
    finally:
        for tv in tvs:
            tv["ctx"].close()
    return timings


def main():
    os.makedirs(OUT, exist_ok=True)
    auth = api("POST", "/auth/login", body={"username": USER, "password": PASSWORD})["token"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        timings = api_wall(browser, auth)
        browser.close()
    print("timings:", json.dumps(timings))
    print("E2E WALL OK")


if __name__ == "__main__":
    main()
