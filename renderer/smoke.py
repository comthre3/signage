"""Smoke test: render a bilingual board and check the PNG size. Run inside the container."""
import os, struct, sys, urllib.request

html = """<!doctype html><html dir="rtl"><body style="margin:0;width:1920px;height:1080px;background:#111;color:#fff;font:64px 'IBM Plex Sans Arabic'"><div style="padding:80px">قائمة الطعام — Menu 2.750</div></body></html>"""
req = urllib.request.Request(
    "http://localhost:8080/render", method="POST",
    data=f'{{"html": {__import__("json").dumps(html)}, "width": 1920, "height": 1080, "scale": 1}}'.encode(),
    headers={"Content-Type": "application/json", "X-Renderer-Token": os.environ["RENDERER_TOKEN"]},
)
png = urllib.request.urlopen(req, timeout=60).read()
assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
w, h = struct.unpack(">II", png[16:24])
assert (w, h) == (1920, 1080), (w, h)
print(f"OK {w}x{h} {len(png)} bytes")
