"""Menu rendering: templates → HTML (this task) and HTML → PNG via the renderer (Task 6)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape


class _DictSubscriptEnvironment(Environment):
    """`foo.bar` resolves `foo['bar']` first for dict/mapping context values.

    Plain Jinja2 tries `getattr(obj, attribute)` before `obj[attribute]`, so a
    dict key like "items" (our category/item lists) collides with the
    built-in `dict.items` method and returns the bound method instead of the
    value. The menu tree is plain dicts throughout, so subscript access is
    tried first here; real attribute access is still the fallback for
    everything else.
    """

    def getattr(self, obj, attribute):
        if isinstance(obj, dict):
            try:
                return obj[attribute]
            except KeyError:
                pass
        return super().getattr(obj, attribute)


TEMPLATES_DIR = Path(__file__).parent / "menu_templates"
ASPECT_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
KINDS = ("board", "category", "promo")
LANGUAGES = ("en", "ar", "bi")
FEATURED_BADGES = ("new", "popular")
MAX_PROMOS = 3

_BADGE_LABELS = {
    "new": ("New", "جديد"), "spicy": ("Spicy", "حار"), "vegan": ("Vegan", "نباتي"),
    "halal": ("Halal", "حلال"), "popular": ("Popular", "الأكثر طلبًا"),
}

_env = _DictSubscriptEnvironment(
    loader=FileSystemLoader(str(TEMPLATES_DIR / "_shared")),
    autoescape=select_autoescape(["html"]),
)


def list_templates() -> list[dict]:
    out = []
    for manifest in sorted(TEMPLATES_DIR.glob("*/template.json")):
        out.append(json.loads(manifest.read_text(encoding="utf-8")))
    return out


def get_template(template_id: str) -> dict:
    for t in list_templates():
        if t["id"] == template_id:
            return t
    raise KeyError(template_id)


def featured_items(tree: dict) -> list[dict]:
    found = []
    for c in tree.get("categories", []):
        for it in c.get("items", []):
            if it.get("is_available", True) and any(b in it.get("badges", []) for b in FEATURED_BADGES):
                found.append(it)
    return found[:MAX_PROMOS]


def _find_category(tree: dict, category_id: int) -> dict:
    for c in tree.get("categories", []):
        if c.get("id") == category_id:
            return c
    raise ValueError(f"category {category_id} not in menu")


def _find_item(tree: dict, item_id: int) -> dict:
    for c in tree.get("categories", []):
        for it in c.get("items", []):
            if it.get("id") == item_id:
                return it
    raise ValueError(f"item {item_id} not in menu")


def build_html(tree: dict, template_id: str, kind: str, language: str, aspect: str,
               category_id: Optional[int] = None, item_id: Optional[int] = None,
               logo_url: Optional[str] = None) -> str:
    if kind not in KINDS or language not in LANGUAGES or aspect not in ASPECT_SIZES:
        raise ValueError("bad kind/language/aspect")
    tpl = get_template(template_id)
    css = (TEMPLATES_DIR / template_id / "style.css").read_text(encoding="utf-8")
    width, height = ASPECT_SIZES[aspect]

    def t(en, ar):
        en, ar = (en or "").strip(), (ar or "").strip()
        if language == "ar":
            return ar or en
        if language == "bi" and ar and ar != en:
            return Markup(f"{escape(en or ar)}<span class=\"ar\">{escape(ar)}</span>")
        return en or ar

    def money(price) -> str:
        return "" if price in (None, "") else f"{Decimal(str(price)):.3f}"

    def badge_label(b: str) -> str:
        en, ar = _BADGE_LABELS.get(b, (b, b))
        return ar if language == "ar" else en

    ctx = {
        "menu": tree, "brand": tree.get("brand", {}), "lang": language,
        "dir": "rtl" if language == "ar" else "ltr", "aspect": aspect,
        "width": width, "height": height, "css": css, "logo_url": logo_url,
        "t": t, "money": money, "badge_label": badge_label, "template": tpl,
        "category": _find_category(tree, category_id) if kind == "category" else None,
        "item": _find_item(tree, item_id) if kind == "promo" else None,
    }
    if kind == "category" and category_id is None:
        raise ValueError("category_id required")
    if kind == "promo" and item_id is None:
        raise ValueError("item_id required")
    return _env.get_template(f"{kind}.html").render(**ctx)


# ── render jobs ─────────────────────────────────────────────────────────────
import asyncio
import logging
import os
import uuid
from dataclasses import dataclass

import httpx

from db import execute, query_one, utc_now_iso
from menus import render_hash

MAX_LOGO_BYTES = 512 * 1024


@dataclass(frozen=True)
class RenderSpec:
    kind: str
    language: str
    aspect: str
    category_id: Optional[int]
    item_id: Optional[int]
    render_hash: str


def plan_renders(tree: dict, template_id: str, languages: list[str], aspects: list[str],
                 kinds: list[str]) -> list[RenderSpec]:
    tpl = get_template(template_id)
    specs: list[RenderSpec] = []
    for kind in kinds:
        if kind not in KINDS:
            raise ValueError(f"bad kind {kind}")
        targets: list[tuple[Optional[int], Optional[int]]]
        if kind == "board":
            targets = [(None, None)]
        elif kind == "category":
            targets = [(c["id"], None) for c in tree.get("categories", [])]
        else:
            targets = [(None, it["id"]) for it in featured_items(tree)]
        for language in languages:
            if language not in LANGUAGES:
                raise ValueError(f"bad language {language}")
            for aspect in aspects:
                if aspect not in ASPECT_SIZES:
                    raise ValueError(f"bad aspect {aspect}")
                for category_id, item_id in targets:
                    specs.append(RenderSpec(kind, language, aspect, category_id, item_id,
                                            render_hash(tree, template_id, tpl["version"], kind, language,
                                                        aspect, category_id, item_id)))
    return specs


def _media_name(tree: dict, spec: RenderSpec) -> str:
    brand = tree.get("brand", {}).get("name_en") or tree.get("name")
    lang = spec.language.upper()
    if spec.kind == "board":
        what = "Menu"
    elif spec.kind == "category":
        what = _find_category(tree, spec.category_id)["name_en"]
    else:
        what = "Promo · " + _find_item(tree, spec.item_id)["name_en"]
    return f"{brand} — {what} ({lang}, {spec.aspect})"


def resolve_logo_data_uri(org_id: int, menu_id: int, brand: dict,
                          upload_dir: str) -> Optional[str]:
    """Brand logo as a self-contained data: URI, or None.

    Inlining rather than linking keeps generated menu HTML standalone: the
    render container has no access to our upload directory, and the browser
    preview would otherwise need a cross-origin image fetch that CSP governs.

    Every rejection path is a warning and a None, never an exception -- a menu
    with an unusable logo should still render without one. Shared by the render
    job and the live preview so the path-traversal, MIME and size checks below
    exist exactly once.
    """
    logger = logging.getLogger(__name__)
    logo_media_id = (brand or {}).get("logo_media_id")
    if not logo_media_id:
        return None
    row = query_one("SELECT filename, mime_type FROM media WHERE id = ? AND organization_id = ?",
                    (logo_media_id, org_id))
    if not row:
        logger.warning("menu %s logo %s: media row not found", menu_id, logo_media_id)
        return None
    if not (row["mime_type"] or "").startswith("image/"):
        logger.warning("menu %s logo %s: mime_type %r is not an image, skipping",
                       menu_id, logo_media_id, row["mime_type"])
        return None
    real_upload_dir = os.path.realpath(upload_dir)
    path = os.path.realpath(os.path.join(upload_dir, row["filename"]))
    if not path.startswith(real_upload_dir + os.sep):
        logger.warning("menu %s logo %s: filename %r resolves outside upload_dir, skipping",
                       menu_id, logo_media_id, row["filename"])
        return None
    if not os.path.exists(path):
        logger.warning("menu %s logo %s: file %s does not exist, skipping",
                       menu_id, logo_media_id, path)
        return None
    if os.path.getsize(path) > MAX_LOGO_BYTES:
        logger.warning("menu %s logo %s: file %s is %d bytes, exceeds MAX_LOGO_BYTES=%d, skipping",
                       menu_id, logo_media_id, path, os.path.getsize(path), MAX_LOGO_BYTES)
        return None
    import base64
    with open(path, "rb") as f:
        data = f.read()
    return f"data:{row['mime_type']};base64," + base64.b64encode(data).decode()


def build_preview_html(org_id: int, menu_id: int, kind: str, language: str,
                       aspect: str, upload_dir: str) -> Optional[str]:
    """Menu as standalone HTML, with no renderer involved.

    Same output the render job hands to Playwright, so what an author previews
    is what a screen will show. Returns None when the menu does not exist or
    does not belong to this organization.
    """
    tree = get_menu_tree(org_id, menu_id)
    if not tree:
        return None
    logo_url = resolve_logo_data_uri(org_id, menu_id, tree["brand"], upload_dir)
    return build_html(tree, tree["template"], kind, language, aspect, logo_url=logo_url)


async def run_render_job(org_id: int, menu_id: int, specs: list[RenderSpec], *, renderer_url: str,
                         renderer_token: str, upload_dir: str) -> None:
    """Render each spec whose hash isn't already 'ready'. Rows go pending → ready/failed."""
    from menus import get_menu_tree
    tree = get_menu_tree(org_id, menu_id)
    if not tree:
        return
    logger = logging.getLogger(__name__)
    logo_url = resolve_logo_data_uri(org_id, menu_id, tree["brand"], upload_dir)
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as http:
        for spec in specs:
            existing = query_one(
                "SELECT id FROM menu_renders WHERE menu_id = ? AND render_hash = ? AND status = 'ready'",
                (menu_id, spec.render_hash))
            if existing:
                continue
            row_id = execute(
                "INSERT INTO menu_renders (menu_id, kind, category_id, item_id, language, aspect, status, "
                "render_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (menu_id, spec.kind, spec.category_id, spec.item_id, spec.language, spec.aspect,
                 spec.render_hash, utc_now_iso()))
            try:
                html = build_html(tree, tree["template"], spec.kind, spec.language, spec.aspect,
                                  category_id=spec.category_id, item_id=spec.item_id, logo_url=logo_url)
                width, height = ASPECT_SIZES[spec.aspect]
                retry_delays = [2, 5]  # extra attempts after a 503 (renderer busy), then give up
                attempt = 0
                while True:
                    resp = await http.post(f"{renderer_url}/render",
                                           json={"html": html, "width": width, "height": height, "scale": 1},
                                           headers={"X-Renderer-Token": renderer_token})
                    if resp.status_code == 503 and attempt < len(retry_delays):
                        await asyncio.sleep(retry_delays[attempt])
                        attempt += 1
                        continue
                    break
                if resp.status_code != 200:
                    raise RuntimeError(f"renderer returned {resp.status_code}: {resp.text[:200]}")
                filename = f"{uuid.uuid4().hex}.png"
                with open(os.path.join(upload_dir, filename), "wb") as f:
                    f.write(resp.content)
                media_id = execute(
                    "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
                    "VALUES (?, ?, ?, 'image/png', ?, ?)",
                    (org_id, _media_name(tree, spec), filename, len(resp.content), utc_now_iso()))
                execute("UPDATE menu_renders SET status = 'ready', media_id = ? WHERE id = ?", (media_id, row_id))
            except Exception as exc:  # noqa: BLE001 — recorded on the row, never raised into the job
                execute("UPDATE menu_renders SET status = 'failed', error = ? WHERE id = ?",
                        (str(exc)[:500], row_id))
    execute("UPDATE menus SET last_rendered_at = ? WHERE id = ?", (utc_now_iso(), menu_id))
