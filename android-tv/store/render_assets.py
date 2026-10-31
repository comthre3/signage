"""Render the TV app's images and the Play Store graphics from the brand art.

Runs in the Playwright image, from the repository root:

    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
      -v "$PWD":/repo -w /repo --entrypoint python \
      signage_renderer:latest android-tv/store/render_assets.py

Writes the app's launcher icons, TV banner and overlay face into
android-tv/app/src/main/res, and the listing graphics into android-tv/store.
Play wants the listing graphics without transparency, so they are JPEGs.
"""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
RES = REPO / "android-tv/app/src/main/res"
STORE = REPO / "android-tv/store"
# Inline: a page made with set_content() may not load file:// images.
FACE = "data:image/png;base64," + base64.b64encode(
    (REPO / "player/assets/faces/v1_smile.png").read_bytes()).decode()

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@500;600"
         "&family=IBM+Plex+Sans+Arabic:wght@500;600&family=IBM+Plex+Serif:wght@700&display=block")

# The player's pairing-screen palette and background (player/styles.css).
BASE_CSS = """
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
body {
  background:
    radial-gradient(60% 80% at 75% 25%, #FDF3D6 0%, transparent 70%),
    radial-gradient(55% 75% at 15% 90%, #F4B9A1 0%, transparent 65%),
    #FFF8F0;
  color: #3E2B4F;
  font-family: 'IBM Plex Sans', sans-serif;
  display: flex; align-items: center; justify-content: center;
}
.word { font-family: 'IBM Plex Serif', Georgia, serif; font-weight: 700; line-height: 1; }
.ar { font-family: 'IBM Plex Sans Arabic', sans-serif; font-weight: 600; color: #5D4A66; }
img { display: block; }
"""


def page_html(body, extra_css=""):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<link rel='stylesheet' href='{FONTS}'>"
            f"<style>{BASE_CSS}{extra_css}</style></head><body>{body}</body></html>")


def banner(scale):
    # 320x180 at scale 1; the same layout makes Play's 1280x720 TV banner.
    s = scale
    return page_html(
        f"<div style='display:flex;align-items:center;gap:{12*s}px'>"
        f"<img src='{FACE}' style='width:{92*s}px;height:{81*s}px'>"
        f"<div><div class='word' style='font-size:{34*s}px'>Khanshoof</div>"
        f"<div class='ar' style='font-size:{20*s}px;margin-top:{6*s}px'>خنشوف</div></div></div>")


def icon(tile=False):
    # Play masks its 512px icon itself, so that one fills the square. Launcher
    # icons bring their own shape: a rounded tile with transparent corners.
    look = "background: radial-gradient(70% 70% at 50% 35%, #FDF3D6, #FFF8F0 70%);"
    if not tile:
        return page_html(f"<img src='{FACE}' style='width:80%;height:auto'>", f"body {{ {look} }}")
    return page_html(
        f"<div class='tile'><img src='{FACE}' style='width:80%;height:auto'></div>",
        "body { background: transparent; }"
        f".tile {{ {look} width: 90%; height: 90%; border-radius: 22%;"
        " display: flex; align-items: center; justify-content: center;"
        " box-shadow: inset 0 0 0 1px rgba(62, 43, 79, 0.08); }")


def feature_graphic():
    return page_html(
        f"<div style='display:flex;align-items:center;gap:48px'>"
        f"<img src='{FACE}' style='width:300px;height:264px'>"
        f"<div><div class='word' style='font-size:88px'>Khanshoof</div>"
        f"<div style='font-size:34px;color:#5D4A66;margin-top:18px'>Your menu on every screen</div>"
        f"<div class='ar' style='font-size:34px;margin-top:10px' dir='rtl'>قائمتك على كل شاشة</div>"
        f"</div></div>")


def adaptive_foreground():
    # Adaptive icons are a 108dp square of which launchers show the middle
    # 72dp, cropped to their own shape; keep the mascot inside the safe circle.
    return page_html(f"<img src='{FACE}' style='width:56%;height:auto'>",
                     "body { background: transparent; }")


def face_only():
    return page_html(f"<img src='{FACE}' style='width:100%;height:100%'>",
                     "body { background: transparent; }")


def render(browser, html, path, width, height, **shot):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_content(html, wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), **shot)
    page.close()
    print(f"wrote {path.relative_to(REPO)} ({width}x{height})")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # App resources
        render(browser, banner(1), RES / "drawable-xhdpi/banner.png", 320, 180)
        # 80dp rather than the usual 48dp: Android TV wants an app icon of at
        # least 160x160 px at xhdpi (TV-LB); launchers scale it down anyway.
        for folder, size in {"mdpi": 80, "hdpi": 120, "xhdpi": 160, "xxhdpi": 240, "xxxhdpi": 320}.items():
            render(browser, icon(tile=True), RES / f"mipmap-{folder}/ic_launcher.png", size, size,
                   omit_background=True)
        for folder, size in {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}.items():
            render(browser, adaptive_foreground(), RES / f"mipmap-{folder}/ic_launcher_foreground.png",
                   size, size, omit_background=True)
        render(browser, face_only(), RES / "drawable-nodpi/face_smile.png", 320, 282, omit_background=True)

        # Play listing graphics
        render(browser, icon(), STORE / "play-icon-512.png", 512, 512)
        render(browser, feature_graphic(), STORE / "play-feature-graphic-1024x500.jpg", 1024, 500,
               type="jpeg", quality=92)
        render(browser, banner(4), STORE / "play-tv-banner-1280x720.jpg", 1280, 720,
               type="jpeg", quality=92)
        browser.close()


if __name__ == "__main__":
    main()
