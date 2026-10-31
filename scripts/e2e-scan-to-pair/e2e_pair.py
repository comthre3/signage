"""End-to-end scan-to-pair test. Run it with run.sh, which starts the stack.

The TV loads https://play.khanshoof.com and the phone https://app.khanshoof.com,
but every request to a khanshoof.com host is answered by local containers
(E2E_API, E2E_APP, E2E_PLAY); anything else on khanshoof.com is refused, so
nothing reaches production. The QR code is decoded from the TV's pixels.

Checks, in order:
  1. the TV shows a code and a QR that decodes to app.khanshoof.com/pair?code=<it>
  2. the phone, signed out, opens that URL, signs in and lands on the pair
     form with the code prefilled; pairing a new screen succeeds
  3. the TV leaves the pairing screen by itself and shows "You're connected!"
  4. assigning a playlist puts the menu on the TV
  5. Arabic: pairing screen and "connected" welcome
  6. a screen on a schedule with nothing on right now stays black
  7. the pairing screen and its QR work at 1280x720
  8. an upright 9:16 touch display (tablets, portrait panels): pairing, QR, welcome
  9. a 16:10 landscape tablet: pairing and QR
 10. a phone: pairing and QR
 11. D-pad focus (the TV remote moves focus like Tab) is clearly visible
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.environ.get("E2E_PYLIB", "/w/.pylib"))
import zxingcpp  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

API = os.environ.get("E2E_API", "http://127.0.0.1:18000")
HOSTS = {
    "api.khanshoof.com": API,
    "app.khanshoof.com": os.environ.get("E2E_APP", "http://127.0.0.1:13000"),
    "play.khanshoof.com": os.environ.get("E2E_PLAY", "http://127.0.0.1:13001"),
}
OUT = os.environ.get("E2E_OUT", "/w/shots")
HERE = os.path.dirname(os.path.abspath(__file__))
USER = os.environ.get("E2E_USER", "demo")
PASSWORD = os.environ.get("E2E_PASSWORD", "Khanshoof-Demo-2026")


def route_local(route):
    m = re.match(r"^https://([^/]+)(/.*)?$", route.request.url)
    host = m.group(1) if m else ""
    if host in HOSTS:
        resp = route.fetch(url=HOSTS[host] + (m.group(2) or "/"))
        return route.fulfill(response=resp)
    if host.endswith("khanshoof.com"):
        return route.abort()   # never let anything reach production
    return route.continue_()


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
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


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


def tv_context(browser, locale="en-US", size=(1920, 1080), scale=1, touch=False):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale=locale,
                              device_scale_factor=scale, has_touch=touch, service_workers="block")
    ctx.route("**/*", route_local)
    return ctx


def wait_for_code(page):
    # Polled from here: wait_for_function evals a string, which the player's
    # CSP (script-src 'self') rightly refuses, and the CSP stays on.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        code = page.inner_text("#pairing-code").strip()
        if re.fullmatch(r"[A-Z2-9]{6}", code):
            page.wait_for_timeout(1200)   # fonts, QR image decode
            return code
        page.wait_for_timeout(250)
    raise AssertionError("TV never showed a pairing code")


def main():
    os.makedirs(OUT, exist_ok=True)
    timings = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Demo content for the TV (a fictional cafe's menu board).
        demo = browser.new_page(viewport={"width": 1920, "height": 1080})
        demo.goto(f"file://{HERE}/demo_menu.html", wait_until="networkidle")
        demo.screenshot(path=f"{OUT}/demo_menu.png")
        demo.close()

        # 1. TV: pairing screen with QR
        tv = tv_context(browser)
        tv_page = tv.new_page()
        tv_page.goto("https://play.khanshoof.com/")
        code = wait_for_code(tv_page)
        tv_page.screenshot(path=f"{OUT}/01-tv-pairing.png")
        qr_url = decode_qr(tv_page.locator("#pairing-qr"), "01b-tv-qr.png")
        expected = f"https://app.khanshoof.com/pair?code={code}"
        assert qr_url == expected, (qr_url, expected)
        print(f"1. TV shows {code}; QR decodes to {qr_url}")

        # 2. Phone: scan, sign in, pair
        phone = browser.new_context(**p.devices["Pixel 7"], service_workers="block")
        phone.route("**/*", route_local)
        ph = phone.new_page()
        ph.goto(qr_url)
        ph.wait_for_selector("#login-form", state="visible", timeout=15000)
        ph.wait_for_timeout(800)
        ph.screenshot(path=f"{OUT}/02-phone-signin.png")
        ph.fill("#login-username", USER)
        ph.fill("#login-password", PASSWORD)
        ph.click("#login-form button[type=submit]")
        ph.wait_for_selector("#pair-form", state="visible", timeout=15000)
        prefilled = ph.input_value("#pair-code-input")
        assert prefilled == code, (prefilled, code)
        if ph.is_visible("#pair-new-name"):
            ph.fill("#pair-new-name", "Counter TV")
        ph.wait_for_timeout(500)
        ph.screenshot(path=f"{OUT}/03-phone-pair-form.png")
        t_tap = time.monotonic()
        ph.click("#pair-submit")
        ph.wait_for_selector("#pair-success", state="visible", timeout=15000)
        ph.wait_for_timeout(500)
        ph.screenshot(path=f"{OUT}/04-phone-paired.png")
        print(f"2. phone: signed in, code prefilled ({prefilled}), paired")

        # 3. TV leaves pairing by itself and shows the welcome
        tv_page.wait_for_selector("#pairing", state="hidden", timeout=15000)
        timings["tap_to_tv_paired_s"] = round(time.monotonic() - t_tap, 1)
        tv_page.wait_for_selector("#empty-state", state="visible", timeout=20000)
        tv_page.wait_for_timeout(1000)
        tv_page.screenshot(path=f"{OUT}/05-tv-connected.png")
        assert tv_page.evaluate("localStorage.getItem('screen_token')"), "TV kept no screen token"
        print(f"3. TV paired {timings['tap_to_tv_paired_s']}s after the tap; welcome shown")

        # 4. Assign content (as the dashboard would); the TV picks it up
        auth = api("POST", "/auth/login", body={"username": USER, "password": PASSWORD})["token"]
        screen = next(s for s in api("GET", "/screens", auth) if s["name"] == "Counter TV")
        with open(f"{OUT}/demo_menu.png", "rb") as f:
            raw, ctype = multipart("file", "karak-menu.png", f.read(), "image/png")
        media = api("POST", "/media/upload", auth, raw=raw, content_type=ctype)
        playlist = api("POST", "/playlists", auth, body={"name": "Morning menu"})
        api("POST", f"/playlists/{playlist['id']}/items", auth,
            body={"media_id": media["id"], "duration_seconds": 15})
        t_assign = time.monotonic()
        api("PUT", f"/screens/{screen['id']}", auth, body={"playlist_id": playlist["id"]})
        tv_page.wait_for_selector("#empty-state", state="hidden", timeout=30000)
        tv_page.wait_for_selector("#content img", state="visible", timeout=30000)
        tv_page.wait_for_timeout(1500)
        timings["assign_to_tv_playing_s"] = round(time.monotonic() - t_assign, 1)
        tv_page.screenshot(path=f"{OUT}/06-tv-playing.png")
        print(f"4. menu on the TV {timings['assign_to_tv_playing_s']}s after assigning it")
        tv.close()
        phone.close()

        # 5. Arabic TV: pairing, then the welcome
        tv_ar = tv_context(browser, locale="ar")
        pa = tv_ar.new_page()
        pa.goto("https://play.khanshoof.com/")
        code_ar = wait_for_code(pa)
        pa.screenshot(path=f"{OUT}/07-tv-pairing-ar.png")
        lobby = api("POST", "/screens", auth, body={"name": "Lobby TV"})
        api("POST", "/screens/claim", auth, body={"code": code_ar, "screen_id": lobby["id"]})
        pa.wait_for_selector("#empty-state", state="visible", timeout=20000)
        pa.wait_for_timeout(1000)
        pa.screenshot(path=f"{OUT}/08-tv-connected-ar.png")
        print("5. Arabic pairing and welcome")

        # 6. A schedule with nothing on now: the operator chose black
        schedule = api("POST", "/schedules", auth, body={"name": "Closed at night"})
        api("PUT", f"/screens/{lobby['id']}", auth, body={"schedule_id": schedule["id"]})
        pa.wait_for_selector("#empty-state", state="hidden", timeout=30000)
        assert not pa.is_visible("#content img"), "nothing should be playing"
        print("6. schedule gap: welcome hidden, screen black")
        tv_ar.close()

        # 7. 720p (many cheap TV boxes run their UI at 1280x720)
        tv720 = tv_context(browser, size=(1280, 720))
        p720 = tv720.new_page()
        p720.goto("https://play.khanshoof.com/")
        wait_for_code(p720)
        p720.screenshot(path=f"{OUT}/09-tv-pairing-720p.png")
        assert decode_qr(p720.locator("#pairing-qr"), "09b-tv-qr-720p.png").startswith(
            "https://app.khanshoof.com/pair?code=")
        print("7. 720p pairing screen and QR")
        tv720.close()

        # 8. Upright touch display: big Android tablets and portrait panels
        upright_ctx = tv_context(browser, size=(720, 1280), scale=2, touch=True)
        up = upright_ctx.new_page()
        up.goto("https://play.khanshoof.com/")
        code_up = wait_for_code(up)
        up.screenshot(path=f"{OUT}/10-tablet-portrait-pairing.png")
        assert decode_qr(up.locator("#pairing-qr"), "10b-tablet-portrait-qr.png") == \
            f"https://app.khanshoof.com/pair?code={code_up}"
        upright = api("POST", "/screens", auth, body={"name": "Upright tablet"})
        api("POST", "/screens/claim", auth, body={"code": code_up, "screen_id": upright["id"]})
        up.wait_for_selector("#empty-state", state="visible", timeout=20000)
        up.wait_for_timeout(1000)
        up.screenshot(path=f"{OUT}/11-tablet-portrait-connected.png")
        print("8. upright 9:16 touch display: pairing, QR and welcome")
        upright_ctx.close()

        # 9. Landscape 16:10 tablet
        land_ctx = tv_context(browser, size=(1280, 800), scale=2, touch=True)
        land = land_ctx.new_page()
        land.goto("https://play.khanshoof.com/")
        wait_for_code(land)
        land.screenshot(path=f"{OUT}/12-tablet-landscape-pairing.png")
        assert decode_qr(land.locator("#pairing-qr"), "12b-tablet-landscape-qr.png").startswith(
            "https://app.khanshoof.com/pair?code=")
        print("9. 16:10 landscape tablet: pairing and QR")
        land_ctx.close()

        # 10. Phone (Play offers the app to phones too)
        phone_ctx = tv_context(browser, size=(412, 915), scale=2.625, touch=True)
        pp = phone_ctx.new_page()
        pp.goto("https://play.khanshoof.com/")
        wait_for_code(pp)
        pp.screenshot(path=f"{OUT}/13-phone-pairing.png")
        assert decode_qr(pp.locator("#pairing-qr"), "13b-phone-qr.png").startswith(
            "https://app.khanshoof.com/pair?code=")
        overflow = pp.evaluate("document.documentElement.scrollWidth > innerWidth || "
                               "document.getElementById('pairing').scrollHeight > innerHeight")
        assert not overflow, "pairing screen doesn't fit a phone"
        print("10. phone: pairing and QR fit")
        phone_ctx.close()

        # 11. D-pad focus: a TV remote moves focus the way Tab does
        tvf = tv_context(browser)
        pf = tvf.new_page()
        pf.goto("https://play.khanshoof.com/")
        wait_for_code(pf)
        pf.keyboard.press("Tab")
        focus = pf.evaluate("(() => { const e = document.activeElement, s = getComputedStyle(e);"
                            " return [e.id, s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
        assert focus[0] == "admin-code-toggle" and focus[1] == "solid" and focus[2] >= 3, focus
        pf.screenshot(path=f"{OUT}/14-tv-dpad-focus.png")
        print("11. D-pad focus ring visible on", focus[0])
        tvf.close()

        browser.close()
    print("timings:", json.dumps(timings))
    print("E2E OK")


if __name__ == "__main__":
    main()
