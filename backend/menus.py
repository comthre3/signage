"""Menus domain: validation, tree load/replace, canonical JSON and render hashes.

A menu is stored across menus / menu_categories / menu_items and exposed to the API
as one JSON tree. Everything here is organisation-scoped: callers pass org_id and
get None/False for menus that belong to someone else.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from db import execute, query_all, query_one, utc_now_iso

TEMPLATE_IDS = ("dark-classic", "cream-cafe", "luxe")
BADGES = frozenset({"new", "spicy", "vegan", "halal", "popular"})
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_CATEGORIES = 60
MAX_ITEMS = 400


class Brand(BaseModel):
    name_en: str = Field(..., min_length=1, max_length=120)
    name_ar: str = Field("", max_length=120)
    tagline_en: Optional[str] = Field(None, max_length=160)
    tagline_ar: Optional[str] = Field(None, max_length=160)
    primary: str = "#E8794A"
    accent: str = "#F0A177"
    background: str = "#14171E"
    logo_media_id: Optional[int] = None
    currency: str = Field("KWD", min_length=3, max_length=3)

    @field_validator("primary", "accent", "background")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError("colour must be #RRGGBB")
        return v.upper()


class ItemIn(BaseModel):
    id: Optional[int] = None
    name_en: str = Field(..., min_length=1, max_length=120)
    name_ar: str = Field("", max_length=120)
    description_en: Optional[str] = Field(None, max_length=300)
    description_ar: Optional[str] = Field(None, max_length=300)
    price: Optional[Decimal] = Field(None, ge=0, le=Decimal("99999.999"))
    price_note: Optional[str] = Field(None, max_length=60)
    badges: list[str] = Field(default_factory=list)
    is_available: bool = True

    @field_validator("badges")
    @classmethod
    def _badges(cls, v: list[str]) -> list[str]:
        bad = [b for b in v if b not in BADGES]
        if bad:
            raise ValueError(f"unknown badges: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("price")
    @classmethod
    def _quantize(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        return None if v is None else v.quantize(Decimal("0.001"))


class CategoryIn(BaseModel):
    id: Optional[int] = None
    name_en: str = Field(..., min_length=1, max_length=120)
    name_ar: str = Field("", max_length=120)
    items: list[ItemIn] = Field(default_factory=list)


class MenuTreeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    template: str
    brand: Brand
    categories: list[CategoryIn] = Field(default_factory=list)

    @field_validator("template")
    @classmethod
    def _template(cls, v: str) -> str:
        if v not in TEMPLATE_IDS:
            raise ValueError(f"template must be one of {TEMPLATE_IDS}")
        return v

    @field_validator("categories")
    @classmethod
    def _limits(cls, v: list[CategoryIn]) -> list[CategoryIn]:
        if len(v) > MAX_CATEGORIES:
            raise ValueError(f"at most {MAX_CATEGORIES} categories")
        if sum(len(c.items) for c in v) > MAX_ITEMS:
            raise ValueError(f"at most {MAX_ITEMS} items")
        return v

    @model_validator(mode="after")
    def _no_duplicate_ids(self) -> "MenuTreeIn":
        seen_cats: set[int] = set()
        seen_items: set[int] = set()
        for cat in self.categories:
            if cat.id is not None:
                if cat.id in seen_cats:
                    raise ValueError(f"duplicate category id {cat.id}")
                seen_cats.add(cat.id)
            for item in cat.items:
                if item.id is not None:
                    if item.id in seen_items:
                        raise ValueError(f"duplicate item id {item.id}")
                    seen_items.add(item.id)
        return self


# ── persistence ────────────────────────────────────────────────────────────

def _price_out(v) -> Optional[str]:
    return None if v is None else f"{Decimal(v):.3f}"


def _loads(v, default):
    if v is None:
        return default
    return v if isinstance(v, (dict, list)) else json.loads(v)


def create_menu(org_id: int, name: str, template: str) -> int:
    if template not in TEMPLATE_IDS:
        raise ValueError("bad template")
    now = utc_now_iso()
    brand = Brand(name_en=name).model_dump()
    return execute(
        "INSERT INTO menus (organization_id, name, brand, template, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, name, json.dumps(brand), template, now, now),
    )


def list_menus(org_id: int) -> list[dict]:
    return query_all(
        """
        SELECT m.id, m.name, m.template, m.playlist_id, m.last_rendered_at, m.updated_at,
               (SELECT COUNT(*) FROM menu_categories c WHERE c.menu_id = m.id) AS category_count,
               (SELECT COUNT(*) FROM menu_items i JOIN menu_categories c ON c.id = i.category_id
                 WHERE c.menu_id = m.id) AS item_count
        FROM menus m WHERE m.organization_id = ? ORDER BY m.updated_at DESC
        """,
        (org_id,),
    )


def get_menu_tree(org_id: int, menu_id: int) -> Optional[dict]:
    menu = query_one(
        "SELECT * FROM menus WHERE id = ? AND organization_id = ?", (menu_id, org_id)
    )
    if not menu:
        return None
    cats = query_all(
        "SELECT * FROM menu_categories WHERE menu_id = ? ORDER BY sort_order, id", (menu_id,)
    )
    items = query_all(
        """
        SELECT i.* FROM menu_items i JOIN menu_categories c ON c.id = i.category_id
        WHERE c.menu_id = ? ORDER BY i.sort_order, i.id
        """,
        (menu_id,),
    )
    by_cat: dict[int, list] = {c["id"]: [] for c in cats}
    for it in items:
        by_cat[it["category_id"]].append({
            "id": it["id"], "name_en": it["name_en"], "name_ar": it["name_ar"],
            "description_en": it["description_en"], "description_ar": it["description_ar"],
            "price": _price_out(it["price"]), "price_note": it["price_note"],
            "badges": _loads(it["badges"], []), "is_available": bool(it["is_available"]),
            "sort_order": it["sort_order"],
        })
    return {
        "id": menu["id"], "name": menu["name"], "template": menu["template"],
        "brand": _loads(menu["brand"], {}), "source": _loads(menu["source"], None),
        "playlist_id": menu["playlist_id"], "last_rendered_at": menu["last_rendered_at"],
        "updated_at": menu["updated_at"],
        "categories": [
            {"id": c["id"], "name_en": c["name_en"], "name_ar": c["name_ar"],
             "sort_order": c["sort_order"], "items": by_cat[c["id"]]}
            for c in cats
        ],
    }


def replace_menu_tree(org_id: int, menu_id: int, payload: MenuTreeIn) -> Optional[dict]:
    menu = query_one(
        "SELECT id FROM menus WHERE id = ? AND organization_id = ?", (menu_id, org_id)
    )
    if not menu:
        return None
    existing_cats = {r["id"] for r in query_all(
        "SELECT id FROM menu_categories WHERE menu_id = ?", (menu_id,))}
    kept_cats: list[int] = []
    for ci, cat in enumerate(payload.categories):
        if cat.id in existing_cats:
            execute("UPDATE menu_categories SET name_en = ?, name_ar = ?, sort_order = ? WHERE id = ?",
                    (cat.name_en, cat.name_ar, ci, cat.id))
            cat_id = cat.id
        else:
            cat_id = execute(
                "INSERT INTO menu_categories (menu_id, name_en, name_ar, sort_order) VALUES (?, ?, ?, ?)",
                (menu_id, cat.name_en, cat.name_ar, ci))
        kept_cats.append(cat_id)
        existing_items = {r["id"] for r in query_all(
            "SELECT id FROM menu_items WHERE category_id = ?", (cat_id,))}
        kept_items: list[int] = []
        for ii, it in enumerate(cat.items):
            vals = (it.name_en, it.name_ar, it.description_en, it.description_ar,
                    None if it.price is None else str(it.price), it.price_note,
                    json.dumps(it.badges), it.is_available, ii)
            if it.id in existing_items:
                execute(
                    "UPDATE menu_items SET name_en=?, name_ar=?, description_en=?, description_ar=?, "
                    "price=?, price_note=?, badges=?, is_available=?, sort_order=? WHERE id=?",
                    vals + (it.id,))
                item_id = it.id
            else:
                item_id = execute(
                    "INSERT INTO menu_items (name_en, name_ar, description_en, description_ar, price, "
                    "price_note, badges, is_available, sort_order, category_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", vals + (cat_id,))
            kept_items.append(item_id)
        for stale in existing_items - set(kept_items):
            execute("DELETE FROM menu_items WHERE id = ?", (stale,))
    for stale in existing_cats - set(kept_cats):
        execute("DELETE FROM menu_categories WHERE id = ?", (stale,))
    execute(
        "UPDATE menus SET name = ?, template = ?, brand = ?, updated_at = ? WHERE id = ?",
        (payload.name, payload.template, json.dumps(payload.brand.model_dump()), utc_now_iso(), menu_id),
    )
    return get_menu_tree(org_id, menu_id)


def delete_menu(org_id: int, menu_id: int) -> bool:
    menu = query_one("SELECT id FROM menus WHERE id = ? AND organization_id = ?", (menu_id, org_id))
    if not menu:
        return False
    execute("DELETE FROM menus WHERE id = ?", (menu_id,))
    return True


# ── hashing ────────────────────────────────────────────────────────────────

def canonical_json(tree: dict) -> str:
    """Only the fields that affect a rendered board, sorted, so hashes are stable."""
    slim = {
        "name": tree.get("name"),
        "brand": tree.get("brand"),
        "categories": [
            {"id": c.get("id"), "name_en": c.get("name_en"), "name_ar": c.get("name_ar"),
             "items": [
                 {k: it.get(k) for k in ("id", "name_en", "name_ar", "description_en", "description_ar",
                                          "price", "price_note", "badges", "is_available")}
                 for it in c.get("items", [])]}
            for c in tree.get("categories", [])
        ],
    }
    return json.dumps(slim, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def render_hash(tree: dict, template_id: str, template_version: str, kind: str, language: str,
                aspect: str, category_id: Optional[int] = None, item_id: Optional[int] = None) -> str:
    raw = "|".join([canonical_json(tree), template_id, str(template_version), kind, language, aspect,
                    str(category_id or ""), str(item_id or "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
