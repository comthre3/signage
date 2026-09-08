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
