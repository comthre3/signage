"""Khanshoof renderer: HTML → PNG with headless Chromium. Internal service only."""
import asyncio
import logging
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
    # Permit safety under cancellation (no leaked semaphore slot if this task
    # is cancelled right after being woken) relies on CPython >= 3.11.1
    # semantics for asyncio.Semaphore.acquire(); this image runs 3.12.
    try:
        async with asyncio.timeout(QUEUE_WAIT_SECONDS):
            await _sem.acquire()
    except TimeoutError:
        raise HTTPException(status_code=503, detail="renderer busy")
    try:
        context = await _browser.new_context(
            viewport={"width": body.width, "height": body.height},
            device_scale_factor=body.scale,
            java_script_enabled=False,      # templates are static; no scripts needed
            offline=True,                   # never reach the network from generated HTML
        )
        try:
            page = await context.new_page()
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            await page.set_content(body.html, wait_until="load")
            await asyncio.wait_for(
                page.evaluate("document.fonts.ready"),
                timeout=RENDER_TIMEOUT_MS / 1000,
            )
            png = await page.screenshot(type="png", full_page=False)
            return Response(content=png, media_type="image/png")
        except HTTPException:
            raise
        except Exception:
            logging.exception("render failed")
            raise HTTPException(status_code=500, detail="render failed")
        finally:
            await context.close()
    finally:
        _sem.release()
