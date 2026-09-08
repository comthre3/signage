"""Khanshoof renderer: HTML → PNG with headless Chromium. Internal service only."""
import asyncio
import os

from fastapi import FastAPI, Header, HTTPException, Response
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

TOKEN = os.environ.get("RENDERER_TOKEN", "")
MAX_CONCURRENCY = int(os.environ.get("RENDERER_MAX_CONCURRENCY", "2"))
QUEUE_WAIT_SECONDS = 60
RENDER_TIMEOUT_MS = 20_000

app = FastAPI(docs_url=None, redoc_url=None)
_pw = None
_browser = None
_sem = asyncio.Semaphore(MAX_CONCURRENCY)


class RenderIn(BaseModel):
    html: str = Field(..., max_length=2_000_000)
    width: int = Field(..., ge=320, le=4096)
    height: int = Field(..., ge=320, le=4096)
    scale: float = Field(1.0, ge=0.5, le=3.0)


@app.on_event("startup")
async def _start():
    global _pw, _browser
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])


@app.on_event("shutdown")
async def _stop():
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()


def _check_token(token: str | None):
    if not TOKEN or token != TOKEN:
        raise HTTPException(status_code=401, detail="bad renderer token")


@app.get("/health")
async def health():
    return {"ok": _browser is not None}


@app.post("/render")
async def render(body: RenderIn, x_renderer_token: str | None = Header(None)):
    _check_token(x_renderer_token)
    try:
        await asyncio.wait_for(_sem.acquire(), timeout=QUEUE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="renderer busy")
    try:
        context = await _browser.new_context(
            viewport={"width": body.width, "height": body.height},
            device_scale_factor=body.scale,
            java_script_enabled=False,      # templates are static; no scripts needed
            offline=True,                   # never reach the network from generated HTML
        )
        page = await context.new_page()
        page.set_default_timeout(RENDER_TIMEOUT_MS)
        await page.set_content(body.html, wait_until="load")
        await page.evaluate("document.fonts.ready")
        png = await page.screenshot(type="png", full_page=False)
        await context.close()
        return Response(content=png, media_type="image/png")
    finally:
        _sem.release()
