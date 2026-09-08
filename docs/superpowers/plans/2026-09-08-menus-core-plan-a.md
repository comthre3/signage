# Menus Core (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Menus" as a first-class content type — editable bilingual menu data, three HTML templates, a headless-Chromium renderer service, and a render → media → playlist pipeline — fully usable by hand, with no AI yet.

**Architecture:** Menu data lives in four new Postgres tables and is exposed as one JSON tree by a small `backend/menus.py` domain module. `backend/menu_render.py` turns a tree + template + language + aspect into HTML via Jinja2 and posts it to a new internal `renderer` compose service (Playwright/Chromium) that returns a PNG; each PNG becomes an ordinary `media` row. The dashboard gets a Menus section in a new `frontend/menus.js` module. Plan B adds the AI importer on top of these interfaces.

**Tech Stack:** FastAPI + psycopg (existing), Jinja2 3.1 (already installed transitively; pinned explicitly), httpx (existing) for backend → renderer, Playwright for Python in the renderer, vanilla JS dashboard, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-09-08-ai-menu-import-design.md` — this plan implements §2, §3 (all tables), §4 (`/render` only; `/screenshot` is Plan B), §5, §6.4, §7 (all `/menus/*` endpoints except `import`/`imports`, plus `/ai/capabilities`), §8 (list, editor, renders), §10 (renderer isolation), §11 (backend + renderer + browser tests), §12 (Plan A).

## Global Constraints

- **Branch:** `feature/menus-core`, branched from `main`. If the UI overhaul (`feature/ui-overhaul`) has not merged when you start, the nav button goes into the current `<nav id="main-nav">`; the section markup is identical either way.
- **DOM contract:** `bash scripts/check_ui_contract.sh` must print `UI contract OK` before every frontend commit. It currently fails on `main` for a pre-existing missing key `confirm_dialog.ok`; Task 9 fixes that first.
- **Org scoping:** every query on the new tables filters by `organization_id` (directly, or via `menus.organization_id` joins). Cross-org access returns 404, never 403.
- **Auth:** writes use `require_api_scope("api:rw", session_roles=("admin", "editor"))` + `require_active_subscription`; reads use `require_api_scope("api:read", "api:rw")`.
- **Errors:** `raise http_error(status, "menu.<code>", "<English message>")`. Every code gets `error.menu.<code>` keys in `frontend/i18n/en.json` and `ar.json`.
- **Audit:** every write calls `audit(request, action="menu.<verb>", actor=principal.user, target_type="menu", target_id=<id>, organization_id=principal.organization_id)`.
- **Colours** are `#RRGGBB` (regex `^#[0-9A-Fa-f]{6}$`). **Prices** are `Decimal` with 3 places, `>= 0`, `<= 99999.999`, or null. **Badges** ⊂ `{"new","spicy","vegan","halal","popular"}`. Limits: 60 categories, 400 items per menu, names ≤ 120 chars, descriptions ≤ 300 chars.
- **Kinds/languages/aspects:** `kind ∈ {board, category, promo}`, `language ∈ {en, ar, bi}`, `aspect ∈ {16:9, 9:16}` → `16:9` renders at 1920×1080, `9:16` at 1080×1920, `scale = 1`.
- **Promo boards:** items with badge `new` or `popular`, max 3, in menu order.
- **Templates:** ids `dark-classic`, `cream-cafe`, `luxe`; template `version` is part of the render hash.
- **render_hash** = SHA-256 hex of `canonical_menu_json + "|" + template_id + "|" + template_version + "|" + kind + "|" + language + "|" + aspect + "|" + (category_id or "") + "|" + (item_id or "")`.
- **Renderer:** internal only (no `ports:`), every request carries `X-Renderer-Token: $RENDERER_TOKEN`, max 2 concurrent renders, 60 s queue wait → 503.
- **Env vars:** `RENDERER_URL` (e.g. `http://renderer:8080`), `RENDERER_TOKEN`. Both unset ⇒ `/ai/capabilities` returns `{"menus": false, "menu_import": false}` and the dashboard hides the Menus section.
- **i18n:** every new user-visible string has a key in BOTH `frontend/i18n/en.json` and `ar.json`. Arabic UI uses logical CSS properties; the AR data column is `dir="rtl"` in both UI languages.
- **Tests:** `cd backend && python -m pytest -q` green after every task. Tests run against the `sawwii_test` database via `backend/tests/conftest.py`.
- **Commits:** end every commit message with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File map

| File | Responsibility |
|---|---|
| `backend/db.py` | + tables `menus`, `menu_categories`, `menu_items`, `menu_renders`, `menu_imports` |
| `backend/menus.py` (new) | Pydantic models, validation, tree load/replace, canonical JSON + hash |
| `backend/menu_render.py` (new) | Jinja2 environment, template manifests, `build_html`, `plan_renders`, `run_render_job` (renderer client) |
| `backend/menu_templates/_shared/{board,category,promo}.html` (new) | HTML skeletons shared by all templates |
| `backend/menu_templates/{dark-classic,cream-cafe,luxe}/{template.json,style.css}` (new) | per-template manifest + CSS |
| `backend/main.py` | + `/ai/capabilities`, `/menus/*` endpoints; `showSection`-style wiring only |
| `backend/requirements.txt` | + `jinja2==3.1.6` |
| `backend/tests/test_menus.py`, `test_menu_render.py`, `test_menu_playlist.py` (new) | backend tests |
| `renderer/{Dockerfile,requirements.txt,app.py,smoke.py}` (new) | Playwright render service |
| `docker-compose.yml` | + `renderer` service; backend env |
| `frontend/index.html` | + nav button, `<section id="menus">` |
| `frontend/menus.js` (new) | `Menus` module: list, editor, renders |
| `frontend/app.js` | + `if (id === "menus") Menus.show();` and capability gating |
| `frontend/styles.css` | + `.menu-*` styles |
| `frontend/i18n/{en,ar}.json` | + `menus.*`, `nav.menus`, `error.menu.*`, `confirm_dialog.ok` |

---

### Task 1: Schema

**Files:**
- Modify: `backend/db.py` (inside `init_db`, after the `idx_templates_org` index line)
- Test: `backend/tests/test_menus.py`

**Interfaces:**
- Produces: tables `menus(id, organization_id, name, brand JSONB, template, source JSONB, playlist_id, last_rendered_at, created_at, updated_at)`, `menu_categories(id, menu_id, name_en, name_ar, sort_order)`, `menu_items(id, category_id, name_en, name_ar, description_en, description_ar, price NUMERIC(10,3), price_note, badges JSONB, is_available, sort_order)`, `menu_renders(id, menu_id, kind, category_id, item_id, language, aspect, status, media_id, render_hash, error, created_at)`, `menu_imports(id, organization_id, menu_id, status, step, source JSONB, error JSONB, usage JSONB, created_by, created_at, finished_at)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_menus.py
"""Menus core (Plan A): schema, domain module, CRUD."""
import uuid
from decimal import Decimal

from db import query_one, query_all


def _cols(table):
    rows = query_all(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        (table,),
    )
    return {r["column_name"] for r in rows}


def test_menu_tables_exist():
    assert {"organization_id", "brand", "template", "playlist_id"} <= _cols("menus")
    assert {"menu_id", "name_en", "name_ar", "sort_order"} <= _cols("menu_categories")
    assert {"category_id", "price", "badges", "is_available"} <= _cols("menu_items")
    assert {"kind", "language", "aspect", "status", "render_hash", "media_id"} <= _cols("menu_renders")
    assert {"status", "step", "usage", "created_by"} <= _cols("menu_imports")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_menus.py::test_menu_tables_exist -v`
Expected: FAIL — `assert {...} <= set()` (columns empty because tables don't exist)

- [ ] **Step 3: Add the tables**

In `backend/db.py`, immediately after the line
`cursor.execute("CREATE INDEX IF NOT EXISTS idx_templates_org   ON screen_zone_templates (organization_id)")`, add:

```python
        # ── Menus (Plan A) ──────────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menus (
                id               SERIAL PRIMARY KEY,
                organization_id  INTEGER NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
                name             TEXT NOT NULL,
                brand            JSONB NOT NULL DEFAULT '{}'::jsonb,
                template         TEXT NOT NULL DEFAULT 'dark-classic',
                source           JSONB,
                playlist_id      INTEGER REFERENCES playlists (id) ON DELETE SET NULL,
                last_rendered_at TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_categories (
                id         SERIAL PRIMARY KEY,
                menu_id    INTEGER NOT NULL REFERENCES menus (id) ON DELETE CASCADE,
                name_en    TEXT NOT NULL,
                name_ar    TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id             SERIAL PRIMARY KEY,
                category_id    INTEGER NOT NULL REFERENCES menu_categories (id) ON DELETE CASCADE,
                name_en        TEXT NOT NULL,
                name_ar        TEXT NOT NULL DEFAULT '',
                description_en TEXT,
                description_ar TEXT,
                price          NUMERIC(10,3),
                price_note     TEXT,
                badges         JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_available   BOOLEAN NOT NULL DEFAULT true,
                sort_order     INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_renders (
                id          SERIAL PRIMARY KEY,
                menu_id     INTEGER NOT NULL REFERENCES menus (id) ON DELETE CASCADE,
                kind        TEXT NOT NULL CHECK (kind IN ('board','category','promo')),
                category_id INTEGER REFERENCES menu_categories (id) ON DELETE CASCADE,
                item_id     INTEGER REFERENCES menu_items (id) ON DELETE CASCADE,
                language    TEXT NOT NULL CHECK (language IN ('en','ar','bi')),
                aspect      TEXT NOT NULL CHECK (aspect IN ('16:9','9:16')),
                status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed')),
                media_id    INTEGER REFERENCES media (id) ON DELETE SET NULL,
                render_hash TEXT NOT NULL,
                error       TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_imports (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
                menu_id         INTEGER REFERENCES menus (id) ON DELETE SET NULL,
                status          TEXT NOT NULL DEFAULT 'queued',
                step            TEXT,
                source          JSONB NOT NULL,
                error           JSONB,
                usage           JSONB,
                created_by      INTEGER,
                created_at      TEXT NOT NULL,
                finished_at     TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menus_org          ON menus (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_cats_menu     ON menu_categories (menu_id, sort_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_items_cat     ON menu_items (category_id, sort_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_renders_menu  ON menu_renders (menu_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_imports_org   ON menu_imports (organization_id, created_at DESC)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_menus.py::test_menu_tables_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_menus.py
git commit -m "feat(menus): schema for menus, categories, items, renders, imports

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Domain module — models, validation, tree load/replace, hash

**Files:**
- Create: `backend/menus.py`
- Test: `backend/tests/test_menus.py`

**Interfaces:**
- Produces (all in `backend/menus.py`):
  - `class Brand(BaseModel)`: `name_en: str`, `name_ar: str = ""`, `tagline_en: str | None`, `tagline_ar: str | None`, `primary: str = "#E8794A"`, `accent: str = "#F0A177"`, `background: str = "#14171E"`, `logo_media_id: int | None`, `currency: str = "KWD"`
  - `class ItemIn(BaseModel)`: `id: int | None`, `name_en`, `name_ar = ""`, `description_en`, `description_ar`, `price: Decimal | None`, `price_note: str | None`, `badges: list[str] = []`, `is_available: bool = True`
  - `class CategoryIn(BaseModel)`: `id: int | None`, `name_en`, `name_ar = ""`, `items: list[ItemIn] = []`
  - `class MenuTreeIn(BaseModel)`: `name: str`, `template: str`, `brand: Brand`, `categories: list[CategoryIn] = []`
  - `TEMPLATE_IDS = ("dark-classic", "cream-cafe", "luxe")`, `BADGES = frozenset({...})`
  - `create_menu(org_id, name, template) -> int`
  - `get_menu_tree(org_id, menu_id) -> dict | None` (shape in §3 of spec; `price` serialised as string `"2.750"` or null)
  - `replace_menu_tree(org_id, menu_id, payload: MenuTreeIn) -> dict` (returns the new tree; preserves ids given in payload)
  - `list_menus(org_id) -> list[dict]` with `item_count`, `category_count`
  - `delete_menu(org_id, menu_id) -> bool`
  - `canonical_json(tree) -> str` and `render_hash(tree, template_id, template_version, kind, language, aspect, category_id=None, item_id=None) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_menus.py`:

```python
import pytest
from pydantic import ValidationError


def _org_id(client):
    sfx = uuid.uuid4().hex[:8]
    r = client.post("/auth/signup/request", json={"business_name": f"Biz {sfx}", "email": f"m-{sfx}@example.com"})
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify", json={"email": f"m-{sfx}@example.com", "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete", json={"verification_token": vt, "password": "Khanshoof2026Test"})
    body = r.json()
    return body["token"], body["organization"]["id"]


def test_brand_rejects_bad_colour():
    from menus import Brand
    with pytest.raises(ValidationError):
        Brand(name_en="X", primary="red")


def test_item_rejects_negative_price_and_unknown_badge():
    from menus import ItemIn
    with pytest.raises(ValidationError):
        ItemIn(name_en="Tea", price=Decimal("-1"))
    with pytest.raises(ValidationError):
        ItemIn(name_en="Tea", badges=["glowing"])


def test_tree_roundtrip_preserves_ids(client):
    from menus import MenuTreeIn, Brand, CategoryIn, ItemIn, create_menu, get_menu_tree, replace_menu_tree
    _, org_id = _org_id(client)
    menu_id = create_menu(org_id, "FORNO", "dark-classic")
    tree = replace_menu_tree(org_id, menu_id, MenuTreeIn(
        name="FORNO", template="dark-classic", brand=Brand(name_en="FORNO", name_ar="فورنو"),
        categories=[CategoryIn(name_en="Classics", name_ar="الكلاسيكية", items=[
            ItemIn(name_en="Margherita", name_ar="مارغريتا", price=Decimal("2.750"), badges=["popular"]),
            ItemIn(name_en="Marinara", price=Decimal("2.250")),
        ])],
    ))
    cat_id = tree["categories"][0]["id"]
    item_id = tree["categories"][0]["items"][0]["id"]
    assert tree["categories"][0]["items"][0]["price"] == "2.750"

    # rename the item, keep ids, drop the second item
    tree2 = replace_menu_tree(org_id, menu_id, MenuTreeIn(
        name="FORNO", template="dark-classic", brand=Brand(name_en="FORNO"),
        categories=[CategoryIn(id=cat_id, name_en="Classics", items=[
            ItemIn(id=item_id, name_en="Margherita DOP", price=Decimal("3.000")),
        ])],
    ))
    assert tree2["categories"][0]["id"] == cat_id
    assert tree2["categories"][0]["items"][0]["id"] == item_id
    assert tree2["categories"][0]["items"][0]["name_en"] == "Margherita DOP"
    assert len(tree2["categories"][0]["items"]) == 1
    assert get_menu_tree(org_id + 999999, menu_id) is None  # other org can't see it


def test_render_hash_is_stable_and_sensitive():
    from menus import render_hash
    tree = {"name": "A", "template": "dark-classic", "brand": {"name_en": "A"}, "categories": []}
    h1 = render_hash(tree, "dark-classic", "1", "board", "en", "16:9")
    h2 = render_hash(dict(tree), "dark-classic", "1", "board", "en", "16:9")
    h3 = render_hash(tree, "dark-classic", "1", "board", "ar", "16:9")
    assert h1 == h2 and h1 != h3 and len(h1) == 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_menus.py -v`
Expected: the four new tests FAIL with `ModuleNotFoundError: No module named 'menus'`

- [ ] **Step 3: Write `backend/menus.py`**

```python
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

from pydantic import BaseModel, Field, field_validator

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_menus.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/menus.py backend/tests/test_menus.py
git commit -m "feat(menus): domain module — models, validation, tree replace, render hash

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Menu CRUD endpoints + `/ai/capabilities` + `/menus/templates`

**Files:**
- Modify: `backend/main.py` (append a `# ── Menus (Plan A) ──` block after the playlist endpoints, i.e. after the `add_playlist_item` handler)
- Modify: `backend/requirements.txt` (+ `jinja2==3.1.6`)
- Test: `backend/tests/test_menus.py`

**Interfaces:**
- Consumes: `menus.py` from Task 2; `require_api_scope`, `require_active_subscription`, `http_error`, `audit`, `AuthedPrincipal` from `main.py`.
- Produces: env-derived module constants `RENDERER_URL = os.getenv("RENDERER_URL", "")`, `RENDERER_TOKEN = os.getenv("RENDERER_TOKEN", "")`; endpoints `GET /ai/capabilities`, `GET /menus/templates`, `GET /menus`, `POST /menus`, `GET /menus/{id}`, `PUT /menus/{id}`, `DELETE /menus/{id}`. Templates are described by `menu_render.list_templates()` (Task 4) — until Task 4 lands, `GET /menus/templates` returns the static list below.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_menus.py`:

```python
def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_capabilities_reports_renderer(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "")
    assert client.get("/ai/capabilities").json() == {"menus": False, "menu_import": False}
    monkeypatch.setattr(main, "RENDERER_URL", "http://renderer:8080")
    assert client.get("/ai/capabilities").json()["menus"] is True


def test_menu_crud_and_isolation(client):
    tok, _ = _org_id(client)
    tok2, _ = _org_id(client)
    r = client.post("/menus", json={"name": "FORNO", "template": "dark-classic"}, headers=_auth(tok))
    assert r.status_code == 201, r.text
    menu_id = r.json()["id"]

    r = client.get("/menus/templates", headers=_auth(tok))
    assert {t["id"] for t in r.json()["items"]} == {"dark-classic", "cream-cafe", "luxe"}

    body = {
        "name": "FORNO", "template": "cream-cafe",
        "brand": {"name_en": "FORNO", "name_ar": "فورنو", "primary": "#D9483B", "accent": "#F0A177",
                  "background": "#1B2026", "currency": "KWD"},
        "categories": [{"name_en": "Classics", "name_ar": "الكلاسيكية",
                        "items": [{"name_en": "Margherita", "name_ar": "مارغريتا", "price": "2.750"}]}],
    }
    r = client.put(f"/menus/{menu_id}", json=body, headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert r.json()["template"] == "cream-cafe"
    assert r.json()["categories"][0]["items"][0]["price"] == "2.750"

    r = client.get("/menus", headers=_auth(tok))
    assert r.json()["items"][0]["item_count"] == 1

    # validation error surfaces as menu.invalid
    bad = dict(body); bad["brand"] = dict(body["brand"], primary="red")
    r = client.put(f"/menus/{menu_id}", json=bad, headers=_auth(tok))
    assert r.status_code == 422

    # other org: 404 on read, update, delete
    assert client.get(f"/menus/{menu_id}", headers=_auth(tok2)).status_code == 404
    assert client.put(f"/menus/{menu_id}", json=body, headers=_auth(tok2)).status_code == 404
    assert client.delete(f"/menus/{menu_id}", headers=_auth(tok2)).status_code == 404

    assert client.delete(f"/menus/{menu_id}", headers=_auth(tok)).status_code == 204
    assert client.get(f"/menus/{menu_id}", headers=_auth(tok)).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_menus.py -k "capabilities or crud" -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'RENDERER_URL'` and 404s

- [ ] **Step 3: Add constants, dependency and endpoints**

In `backend/requirements.txt` add a line `jinja2==3.1.6`.

In `backend/main.py`, next to `MAX_UPLOAD_MB` (line ~98) add:

```python
RENDERER_URL   = os.getenv("RENDERER_URL", "").rstrip("/")
RENDERER_TOKEN = os.getenv("RENDERER_TOKEN", "")
```

Add `import menus as menus_domain` to the local imports (next to `from db import ...`).

After the `add_playlist_item` handler, add:

```python
# ── Menus (Plan A) ────────────────────────────────────────────────────────

class MenuCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    template: str = "dark-classic"


@app.get("/ai/capabilities")
def ai_capabilities() -> dict:
    """Feature flags the dashboard uses to hide what isn't configured."""
    return {
        "menus":       bool(RENDERER_URL),
        "menu_import": bool(RENDERER_URL) and bool(os.getenv("ANTHROPIC_API_KEY", "")),
    }


@app.get("/menus/templates")
def list_menu_templates(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    from menu_render import list_templates  # Task 4; static fallback until then
    return {"items": list_templates()}


@app.get("/menus")
def list_menus_endpoint(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    return {"items": menus_domain.list_menus(principal.organization_id)}


@app.post("/menus", status_code=201)
def create_menu_endpoint(
    payload: MenuCreate,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    if payload.template not in menus_domain.TEMPLATE_IDS:
        raise http_error(400, "menu.bad_template", "Unknown template")
    menu_id = menus_domain.create_menu(principal.organization_id, payload.name, payload.template)
    audit(request, action="menu.create", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    return menus_domain.get_menu_tree(principal.organization_id, menu_id)


@app.get("/menus/{menu_id}")
def get_menu_endpoint(
    menu_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    tree = menus_domain.get_menu_tree(principal.organization_id, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    tree["renders"] = _current_renders(menu_id)  # Task 6 fills this; returns [] until then
    return tree


@app.put("/menus/{menu_id}")
def update_menu_endpoint(
    menu_id: int,
    payload: menus_domain.MenuTreeIn,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    tree = menus_domain.replace_menu_tree(principal.organization_id, menu_id, payload)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    audit(request, action="menu.update", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    tree["renders"] = _current_renders(menu_id)
    return tree


@app.delete("/menus/{menu_id}", status_code=204)
def delete_menu_endpoint(
    menu_id: int,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
):
    if not menus_domain.delete_menu(principal.organization_id, menu_id):
        raise http_error(404, "menu.not_found", "Menu not found")
    audit(request, action="menu.delete", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    return Response(status_code=204)


def _current_renders(menu_id: int) -> list[dict]:
    """Latest render per (kind, category, item, language, aspect). Populated by Task 6."""
    rows = query_all(
        """
        SELECT DISTINCT ON (kind, COALESCE(category_id, 0), COALESCE(item_id, 0), language, aspect)
               r.id, r.kind, r.category_id, r.item_id, r.language, r.aspect, r.status,
               r.media_id, r.render_hash, r.error, r.created_at, m.filename
        FROM menu_renders r LEFT JOIN media m ON m.id = r.media_id
        WHERE r.menu_id = ?
        ORDER BY kind, COALESCE(category_id, 0), COALESCE(item_id, 0), language, aspect, r.created_at DESC
        """,
        (menu_id,),
    )
    for r in rows:
        r["url"] = f"/uploads/{r['filename']}" if r.get("filename") else None
        r.pop("filename", None)
    return rows
```

`Response` is `from fastapi import Response` — add it to the existing fastapi import line. Until Task 4 exists, create a stub `backend/menu_render.py` containing only:

```python
"""Menu rendering (Task 4 replaces this stub)."""
TEMPLATES = [
    {"id": "dark-classic", "name_en": "Dark classic", "name_ar": "كلاسيكي داكن", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
    {"id": "cream-cafe", "name_en": "Cream café", "name_ar": "كافيه كريمي", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
    {"id": "luxe", "name_en": "Luxe", "name_ar": "فاخر", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
]


def list_templates() -> list[dict]:
    return TEMPLATES
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_menus.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/menu_render.py backend/requirements.txt backend/tests/test_menus.py
git commit -m "feat(menus): CRUD endpoints, /ai/capabilities, template list

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Templates + HTML builder

**Files:**
- Replace: `backend/menu_render.py` (stub from Task 3 → real module, `list_templates` kept)
- Create: `backend/menu_templates/_shared/board.html`, `category.html`, `promo.html`
- Create: `backend/menu_templates/dark-classic/template.json`, `style.css`; same two files under `cream-cafe/` and `luxe/`
- Test: `backend/tests/test_menu_render.py`

**Interfaces:**
- Produces: `list_templates() -> list[dict]` (reads every `template.json`), `get_template(template_id) -> dict`, `build_html(tree: dict, template_id: str, kind: str, language: str, aspect: str, category_id: int | None = None, item_id: int | None = None) -> str`, `ASPECT_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}`, `featured_items(tree) -> list[dict]` (badge `new`/`popular`, max 3).
- Template context: `menu` (tree), `brand`, `lang` (`en`|`ar`|`bi`), `dir` (`rtl` when `lang == "ar"`), `aspect`, `width`, `height`, `css` (template CSS text), `category` (for kind=category), `item` (for kind=promo), `t(en, ar)` helper returning the string for the language (`bi` → both joined by a `<span class="ar">`), `money(price)` → `"2.750"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_menu_render.py
from decimal import Decimal
import pytest

TREE = {
    "id": 1, "name": "FORNO", "template": "dark-classic",
    "brand": {"name_en": "FORNO", "name_ar": "فورنو", "tagline_en": "Wood-fired · Salmiya",
              "tagline_ar": "فرن حطب · السالمية", "primary": "#D9483B", "accent": "#F0A177",
              "background": "#1B2026", "logo_media_id": None, "currency": "KWD"},
    "categories": [
        {"id": 10, "name_en": "Classics", "name_ar": "الكلاسيكية", "items": [
            {"id": 100, "name_en": "Margherita <script>x</script>", "name_ar": "مارغريتا",
             "description_en": None, "description_ar": None, "price": "2.750",
             "price_note": None, "badges": ["popular"], "is_available": True},
            {"id": 101, "name_en": "Marinara", "name_ar": "مارينارا", "description_en": None,
             "description_ar": None, "price": "2.250", "price_note": None, "badges": [],
             "is_available": False},
        ]},
    ],
}


def test_templates_load_from_manifests():
    from menu_render import list_templates, get_template
    ids = {t["id"] for t in list_templates()}
    assert ids == {"dark-classic", "cream-cafe", "luxe"}
    assert get_template("luxe")["version"] == "1"
    with pytest.raises(KeyError):
        get_template("nope")


def test_board_html_escapes_and_localises():
    from menu_render import build_html
    html_en = build_html(TREE, "dark-classic", "board", "en", "16:9")
    assert "&lt;script&gt;" in html_en and "<script>x" not in html_en
    assert 'dir="ltr"' in html_en and "2.750" in html_en and "Classics" in html_en
    assert "Marinara" not in html_en                      # unavailable items are hidden
    html_ar = build_html(TREE, "dark-classic", "board", "ar", "9:16")
    assert 'dir="rtl"' in html_ar and "الكلاسيكية" in html_ar and "width: 1080px" in html_ar
    html_bi = build_html(TREE, "cream-cafe", "board", "bi", "16:9")
    assert "Classics" in html_bi and "الكلاسيكية" in html_bi


def test_category_and_promo_kinds():
    from menu_render import build_html, featured_items
    assert [i["id"] for i in featured_items(TREE)] == [100]
    html_c = build_html(TREE, "luxe", "category", "en", "16:9", category_id=10)
    assert "Classics" in html_c
    html_p = build_html(TREE, "luxe", "promo", "en", "16:9", item_id=100)
    assert "Margherita" in html_p and "2.750" in html_p
    with pytest.raises(ValueError):
        build_html(TREE, "luxe", "promo", "en", "16:9", item_id=999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_menu_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_template'`

- [ ] **Step 3: Write the shared HTML skeletons**

`backend/menu_templates/_shared/board.html`:

```html
<!doctype html>
<html lang="{{ 'ar' if lang == 'ar' else 'en' }}" dir="{{ dir }}">
<head>
<meta charset="utf-8">
<style>
  :root { --primary: {{ brand.primary }}; --accent: {{ brand.accent }}; --bg: {{ brand.background }}; }
  html, body { margin: 0; padding: 0; }
  body { width: {{ width }}px; height: {{ height }}px; overflow: hidden; }
  {{ css | safe }}
</style>
</head>
<body class="kind-board aspect-{{ '169' if aspect == '16:9' else '916' }} lang-{{ lang }}">
<div class="board">
  <header class="head">
    <div class="brand">
      {% if logo_url %}<img class="logo" src="{{ logo_url }}" alt="">{% endif %}
      <div class="brand-name">{{ t(brand.name_en, brand.name_ar) }}</div>
      {% if brand.tagline_en or brand.tagline_ar %}<div class="tagline">{{ t(brand.tagline_en, brand.tagline_ar) }}</div>{% endif %}
    </div>
  </header>
  <main class="cols cols-{{ [menu.categories | length, 3] | min }}">
    {% for c in menu.categories %}
    <section class="cat">
      <h2 class="cat-name">{{ t(c.name_en, c.name_ar) }}</h2>
      {% for it in c.items if it.is_available %}
      <div class="item{% if 'popular' in it.badges %} popular{% endif %}">
        <div class="item-main">
          <span class="item-name">{{ t(it.name_en, it.name_ar) }}</span>
          {% for b in it.badges %}<span class="badge badge-{{ b }}">{{ badge_label(b) }}</span>{% endfor %}
          <span class="dots"></span>
          <span class="price">{{ money(it.price) }}{% if it.price_note %} <small>{{ it.price_note }}</small>{% endif %}</span>
        </div>
        {% if it.description_en or it.description_ar %}<div class="item-desc">{{ t(it.description_en, it.description_ar) }}</div>{% endif %}
      </div>
      {% endfor %}
    </section>
    {% endfor %}
  </main>
  <footer class="foot">{{ t('Prices in ' ~ brand.currency, 'الأسعار بـ' ~ brand.currency) }}</footer>
</div>
</body>
</html>
```

`backend/menu_templates/_shared/category.html` — identical head/body wrapper, `class="kind-category …"`, and the `<main>` becomes:

```html
  <main class="single">
    <section class="cat">
      <h2 class="cat-name">{{ t(category.name_en, category.name_ar) }}</h2>
      {% for it in category.items if it.is_available %}
      <div class="item{% if 'popular' in it.badges %} popular{% endif %}">
        <div class="item-main">
          <span class="item-name">{{ t(it.name_en, it.name_ar) }}</span>
          {% for b in it.badges %}<span class="badge badge-{{ b }}">{{ badge_label(b) }}</span>{% endfor %}
          <span class="dots"></span>
          <span class="price">{{ money(it.price) }}{% if it.price_note %} <small>{{ it.price_note }}</small>{% endif %}</span>
        </div>
        {% if it.description_en or it.description_ar %}<div class="item-desc">{{ t(it.description_en, it.description_ar) }}</div>{% endif %}
      </div>
      {% endfor %}
    </section>
  </main>
```

`backend/menu_templates/_shared/promo.html` — same wrapper, `class="kind-promo …"`, and:

```html
<div class="board promo">
  <div class="promo-kicker">{{ badge_label('new') if 'new' in item.badges else badge_label('popular') }}</div>
  <div class="promo-name">{{ t(item.name_en, item.name_ar) }}</div>
  {% if item.description_en or item.description_ar %}<div class="promo-desc">{{ t(item.description_en, item.description_ar) }}</div>{% endif %}
  <div class="promo-price">{{ money(item.price) }} <small>{{ brand.currency }}</small></div>
  <div class="promo-brand">{{ t(brand.name_en, brand.name_ar) }}</div>
</div>
```

(Write the full files: copy the `<!doctype …>` through `<body …>` block from `board.html` into the other two, replacing the body class, then the fragment above, then `</body></html>`.)

- [ ] **Step 4: Write the three template manifests and stylesheets**

`backend/menu_templates/dark-classic/template.json`:

```json
{ "id": "dark-classic", "name_en": "Dark classic", "name_ar": "كلاسيكي داكن", "version": "1",
  "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"] }
```

`backend/menu_templates/dark-classic/style.css`:

```css
body { font-family: 'Inter', 'IBM Plex Sans Arabic', sans-serif; background: var(--bg); color: #fff; }
.lang-ar body, body.lang-ar { font-family: 'IBM Plex Sans Arabic', 'Inter', sans-serif; }
.board { box-sizing: border-box; height: 100%; padding: 64px 80px; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 40px; }
.head { display: flex; align-items: flex-end; justify-content: space-between; border-bottom: 2px solid rgba(255,255,255,.14); padding-bottom: 28px; }
.brand { display: flex; align-items: center; gap: 28px; }
.logo { height: 96px; width: auto; }
.brand-name { font-weight: 800; font-size: 96px; letter-spacing: -.02em; line-height: 1; }
.tagline { font-size: 28px; letter-spacing: .14em; text-transform: uppercase; color: var(--accent); font-weight: 600; margin-top: 12px; }
.cols { display: grid; gap: 72px; align-content: start; }
.cols-1 { grid-template-columns: 1fr; } .cols-2 { grid-template-columns: 1fr 1fr; } .cols-3 { grid-template-columns: 1fr 1fr 1fr; }
.aspect-916 .cols { grid-template-columns: 1fr; }
.cat-name { font-size: 30px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: var(--accent); margin: 0 0 18px; }
.item { padding: 10px 0; }
.item-main { display: flex; align-items: baseline; gap: 18px; font-size: 36px; font-weight: 500; }
.item-desc { font-size: 24px; color: rgba(255,255,255,.6); margin-top: 4px; }
.dots { flex: 1; border-bottom: 2px dotted rgba(255,255,255,.3); transform: translateY(-8px); }
.price { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: var(--accent); white-space: nowrap; }
.price small { font-size: .6em; color: rgba(255,255,255,.55); }
.badge { font-size: 18px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; padding: 4px 12px; border-radius: 999px; background: var(--primary); color: #fff; }
.foot { font-size: 24px; color: rgba(255,255,255,.55); letter-spacing: .04em; }
.single .cat-name { font-size: 48px; } .single .item-main { font-size: 52px; } .single .item-desc { font-size: 30px; }
.promo { display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 24px; background: linear-gradient(160deg, var(--primary), color-mix(in srgb, var(--primary) 55%, #000)); }
.promo-kicker { font-size: 32px; letter-spacing: .18em; text-transform: uppercase; font-weight: 700; opacity: .85; }
.promo-name { font-size: 140px; font-weight: 850; letter-spacing: -.03em; line-height: .95; }
.promo-desc { font-size: 40px; opacity: .9; max-width: 70%; }
.promo-price { font-family: 'JetBrains Mono', monospace; font-size: 96px; font-weight: 700; }
.promo-price small { font-size: .4em; }
.promo-brand { position: absolute; bottom: 56px; font-size: 28px; letter-spacing: .2em; text-transform: uppercase; opacity: .7; }
.ar { display: block; font-family: 'IBM Plex Sans Arabic', sans-serif; direction: rtl; font-size: .8em; opacity: .85; }
```

`backend/menu_templates/cream-cafe/template.json` — as above with `"id": "cream-cafe", "name_en": "Cream café", "name_ar": "كافيه كريمي"`.

`backend/menu_templates/cream-cafe/style.css`:

```css
body { font-family: 'Inter', 'IBM Plex Sans Arabic', sans-serif; background: linear-gradient(180deg, #F8F1E3, #F1E4CB); color: #3B2A1E; }
body.lang-ar { font-family: 'IBM Plex Sans Arabic', 'Inter', sans-serif; }
.board { box-sizing: border-box; height: 100%; padding: 72px 88px; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 44px; }
.head { display: flex; align-items: flex-end; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 28px; }
.logo { height: 96px; width: auto; }
.brand-name { font-weight: 800; font-size: 92px; letter-spacing: -.02em; line-height: 1; color: var(--primary); }
.tagline { font-size: 26px; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); font-weight: 600; margin-top: 12px; }
.cols { display: grid; gap: 64px; align-content: start; }
.cols-1 { grid-template-columns: 1fr; } .cols-2 { grid-template-columns: 1fr 1fr; } .cols-3 { grid-template-columns: 1fr 1fr 1fr; }
.aspect-916 .cols { grid-template-columns: 1fr; }
.cat-name { font-size: 30px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); border-bottom: 4px solid var(--accent); padding-bottom: 12px; margin: 0 0 20px; }
.item { padding: 12px 0; }
.item-main { display: flex; align-items: baseline; justify-content: space-between; gap: 18px; font-size: 36px; font-weight: 500; }
.item-desc { font-size: 24px; color: rgba(59,42,30,.65); margin-top: 4px; }
.dots { display: none; }
.price { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: var(--accent); white-space: nowrap; }
.price small { font-size: .6em; color: rgba(59,42,30,.55); }
.badge { font-size: 18px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; padding: 4px 12px; border-radius: 999px; background: var(--primary); color: #fff; margin-inline-start: 12px; }
.foot { font-size: 22px; letter-spacing: .1em; text-transform: uppercase; color: rgba(59,42,30,.5); font-weight: 600; }
.single .cat-name { font-size: 48px; } .single .item-main { font-size: 52px; } .single .item-desc { font-size: 30px; }
.promo { display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 24px; background: var(--primary); color: #FFF8F0; }
.promo-kicker { font-size: 32px; letter-spacing: .18em; text-transform: uppercase; font-weight: 700; opacity: .9; }
.promo-name { font-size: 140px; font-weight: 850; letter-spacing: -.03em; line-height: .95; }
.promo-desc { font-size: 40px; opacity: .9; max-width: 70%; }
.promo-price { font-family: 'JetBrains Mono', monospace; font-size: 96px; font-weight: 700; }
.promo-price small { font-size: .4em; }
.promo-brand { position: absolute; bottom: 56px; font-size: 28px; letter-spacing: .2em; text-transform: uppercase; opacity: .8; }
.ar { display: block; font-family: 'IBM Plex Sans Arabic', sans-serif; direction: rtl; font-size: .8em; opacity: .85; }
```

`backend/menu_templates/luxe/template.json` — `"id": "luxe", "name_en": "Luxe", "name_ar": "فاخر"`.

`backend/menu_templates/luxe/style.css`:

```css
body { font-family: 'Inter', 'IBM Plex Sans Arabic', sans-serif; background: radial-gradient(90% 60% at 50% 20%, color-mix(in srgb, var(--accent) 22%, transparent), transparent 70%), linear-gradient(180deg, #16111D, #07060A); color: #fff; }
body.lang-ar { font-family: 'IBM Plex Sans Arabic', 'Inter', sans-serif; }
.board { box-sizing: border-box; height: 100%; padding: 80px 96px; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 48px; }
.head { text-align: center; }
.brand { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.logo { height: 88px; width: auto; }
.brand-name { font-weight: 300; font-size: 72px; letter-spacing: .28em; text-transform: uppercase; line-height: 1; }
.tagline { font-size: 24px; letter-spacing: .3em; text-transform: uppercase; color: var(--accent); font-weight: 500; }
.cols { display: grid; gap: 80px; align-content: start; }
.cols-1 { grid-template-columns: 1fr; } .cols-2 { grid-template-columns: 1fr 1fr; } .cols-3 { grid-template-columns: 1fr 1fr 1fr; }
.aspect-916 .cols { grid-template-columns: 1fr; }
.cat-name { font-size: 26px; font-weight: 600; letter-spacing: .28em; text-transform: uppercase; color: var(--accent); border-bottom: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); padding-bottom: 16px; margin: 0 0 24px; }
.item { padding: 14px 0; }
.item-main { display: flex; align-items: baseline; justify-content: space-between; gap: 18px; font-size: 38px; font-weight: 400; }
.item-desc { font-size: 24px; color: rgba(255,255,255,.55); margin-top: 6px; }
.dots { display: none; }
.price { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: var(--accent); white-space: nowrap; }
.price small { font-size: .6em; color: rgba(255,255,255,.5); }
.badge { font-size: 16px; font-weight: 600; letter-spacing: .16em; text-transform: uppercase; padding: 4px 12px; border: 1px solid var(--accent); border-radius: 999px; color: var(--accent); margin-inline-start: 12px; }
.foot { font-size: 22px; letter-spacing: .12em; text-transform: uppercase; color: rgba(255,255,255,.45); text-align: center; }
.single .cat-name { font-size: 40px; } .single .item-main { font-size: 54px; } .single .item-desc { font-size: 30px; }
.promo { display: flex; flex-direction: column; justify-content: flex-end; align-items: flex-start; text-align: start; gap: 20px; padding: 120px; }
.promo-kicker { font-size: 28px; letter-spacing: .3em; text-transform: uppercase; color: rgba(255,255,255,.6); }
.promo-name { font-size: 132px; font-weight: 300; letter-spacing: .02em; line-height: 1; }
.promo-desc { font-size: 36px; color: rgba(255,255,255,.65); }
.promo-price { font-family: 'JetBrains Mono', monospace; font-size: 84px; font-weight: 600; color: var(--accent); }
.promo-price small { font-size: .4em; }
.promo-brand { position: absolute; top: 96px; font-size: 26px; letter-spacing: .3em; text-transform: uppercase; opacity: .6; }
.ar { display: block; font-family: 'IBM Plex Sans Arabic', sans-serif; direction: rtl; font-size: .8em; opacity: .85; }
```

- [ ] **Step 5: Replace `backend/menu_render.py` with the real module (rendering half; the job half is Task 6)**

```python
"""Menu rendering: templates → HTML (this task) and HTML → PNG via the renderer (Task 6)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

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

_env = Environment(
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_menu_render.py tests/test_menus.py -v`
Expected: all pass (the `test_menus.py` template test still sees three ids, now from manifests)

- [ ] **Step 7: Commit**

```bash
git add backend/menu_render.py backend/menu_templates backend/tests/test_menu_render.py
git commit -m "feat(menus): three board templates and the Jinja2 HTML builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Renderer service

**Files:**
- Create: `renderer/Dockerfile`, `renderer/requirements.txt`, `renderer/app.py`, `renderer/smoke.py`, `renderer/.dockerignore`
- Modify: `docker-compose.yml` (+ `renderer` service, backend env)

**Interfaces:**
- Produces: HTTP `POST /render` body `{"html": str, "width": int, "height": int, "scale": number}` → `200 image/png`; `401` on bad token; `422` on width/height outside 320–4096; `503` when the queue wait exceeds 60 s. `GET /health` → `{"ok": true}`. Env: `RENDERER_TOKEN` (required), `RENDERER_MAX_CONCURRENCY` (default 2).

- [ ] **Step 1: Find the current Playwright Python image tag and pin it**

Run: `curl -s https://mcr.microsoft.com/v2/playwright/python/tags/list | python3 -c "import sys,json; t=[x for x in json.load(sys.stdin)['tags'] if x.endswith('-noble') and x.startswith('v1.')]; print(sorted(t)[-1])"`
Expected: prints one tag like `v1.5x.0-noble`. Use that exact tag below wherever `<PINNED_TAG>` appears (it is the only value you substitute).

- [ ] **Step 2: Write the service**

`renderer/requirements.txt`:

```
fastapi==0.111.0
uvicorn[standard]==0.30.0
```

(Playwright itself is preinstalled in the base image at the matching version — do not add it here.)

`renderer/Dockerfile`:

```dockerfile
FROM mcr.microsoft.com/playwright/python:<PINNED_TAG>

# Fonts used by the menu templates, from the Google Fonts repo (all SIL OFL).
RUN mkdir -p /usr/share/fonts/truetype/khanshoof && cd /usr/share/fonts/truetype/khanshoof && \
    curl -fsSL -o Inter.ttf "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf" && \
    curl -fsSL -o JetBrainsMono.ttf "https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf" && \
    for w in Regular Medium SemiBold Bold; do \
      curl -fsSL -o "IBMPlexSansArabic-$w.ttf" "https://github.com/google/fonts/raw/main/ofl/ibmplexsansarabic/IBMPlexSansArabic-$w.ttf"; \
    done && fc-cache -f && \
    fc-list | grep -qi "IBM Plex Sans Arabic" && fc-list | grep -qi "Inter" && fc-list | grep -qi "JetBrains Mono"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py smoke.py ./
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

`renderer/.dockerignore`: `__pycache__`

`renderer/app.py`:

```python
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
```

`renderer/smoke.py` (run inside the container after build):

```python
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
```

- [ ] **Step 3: Add the service to `docker-compose.yml`**

Insert after the `backend` service block:

```yaml
  renderer:
    build: ./renderer
    environment:
      - RENDERER_TOKEN=${RENDERER_TOKEN:-dev-renderer-token}
      - RENDERER_MAX_CONCURRENCY=2
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8080/health',timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
```

and in the `backend` service add:

```yaml
    environment:
      - RENDERER_URL=http://renderer:8080
      - RENDERER_TOKEN=${RENDERER_TOKEN:-dev-renderer-token}
    depends_on:
      postgres:
        condition: service_healthy
      renderer:
        condition: service_started
```

(Replace the existing `depends_on:` block of `backend` with the one above; keep everything else.) No `ports:` on `renderer`. Add `RENDERER_TOKEN=<random 32 hex chars>` to the operator's `.env` (not committed) — `python3 -c "import secrets;print(secrets.token_hex(16))"`.

- [ ] **Step 4: Build and smoke-test the renderer only**

Run:
```bash
docker compose build renderer && docker compose up -d renderer && sleep 5 && \
docker compose exec renderer python smoke.py
```
Expected: `OK 1920x1080 <n> bytes`. This starts only the renderer; **do not** rebuild or restart `backend`/`frontend`/`landing` here (the landing container is the live origin for yalla.khanshoof.com).

- [ ] **Step 5: Commit**

```bash
git add renderer docker-compose.yml
git commit -m "feat(renderer): headless Chromium render service with bundled fonts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Render pipeline — plan, run, media rows, endpoints

**Files:**
- Modify: `backend/menu_render.py` (append the job half)
- Modify: `backend/main.py` (+ `POST /menus/{id}/render`, `GET /menus/{id}/renders`)
- Test: `backend/tests/test_menu_render.py`

**Interfaces:**
- Consumes: `build_html`, `get_template`, `featured_items` (Task 4); `menus_domain.render_hash`, `get_menu_tree`; `RENDERER_URL`, `RENDERER_TOKEN`, `UPLOAD_DIR` from `main`.
- Produces: `class RenderSpec(kind, language, aspect, category_id, item_id, render_hash)` (a `dataclass`), `plan_renders(tree, template, languages, aspects, kinds) -> list[RenderSpec]`, `async run_render_job(org_id, menu_id, specs, *, renderer_url, renderer_token, upload_dir) -> None` (writes `menu_renders` rows: inserts `pending`, then `ready` with `media_id` or `failed` with `error`), `class RenderRequest(BaseModel)`: `languages: list[str] = ["en","ar"]`, `aspects: list[str] = ["16:9"]`, `kinds: list[str] = ["board"]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_menu_render.py`:

```python
import base64, uuid
import httpx, respx

# 1x1 transparent PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _org(client):
    sfx = uuid.uuid4().hex[:8]
    r = client.post("/auth/signup/request", json={"business_name": f"Biz {sfx}", "email": f"r-{sfx}@example.com"})
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify", json={"email": f"r-{sfx}@example.com", "otp": otp})
    r = client.post("/auth/signup/complete", json={"verification_token": r.json()["verification_token"], "password": "Khanshoof2026Test"})
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["organization"]["id"]


def _menu_with_items(client, headers):
    menu_id = client.post("/menus", json={"name": "FORNO"}, headers=headers).json()["id"]
    body = {"name": "FORNO", "template": "dark-classic", "brand": {"name_en": "FORNO", "name_ar": "فورنو"},
            "categories": [{"name_en": "Classics", "name_ar": "الكلاسيكية", "items": [
                {"name_en": "Margherita", "name_ar": "مارغريتا", "price": "2.750", "badges": ["popular"]}]}]}
    assert client.put(f"/menus/{menu_id}", json=body, headers=headers).status_code == 200
    return menu_id


def test_plan_renders_counts_and_hashes():
    from menu_render import plan_renders
    from tests.test_menu_render import TREE
    specs = plan_renders(TREE, "dark-classic", ["en", "ar"], ["16:9"], ["board", "category", "promo"])
    kinds = sorted((s.kind, s.language) for s in specs)
    assert kinds == sorted([("board", "en"), ("board", "ar"), ("category", "en"), ("category", "ar"),
                            ("promo", "en"), ("promo", "ar")])
    assert len({s.render_hash for s in specs}) == 6


@respx.mock
def test_render_endpoint_creates_media_and_skips_unchanged(client, monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "http://renderer.test")
    monkeypatch.setattr(main, "RENDERER_TOKEN", "t")
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    route = respx.post("http://renderer.test/render").mock(return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)

    r = client.post(f"/menus/{menu_id}/render", json={"languages": ["en", "ar"], "aspects": ["16:9"], "kinds": ["board"]}, headers=headers)
    assert r.status_code == 202, r.text
    renders = client.get(f"/menus/{menu_id}/renders", headers=headers).json()["items"]
    assert {(x["language"], x["status"]) for x in renders} == {("en", "ready"), ("ar", "ready")}
    assert all(x["url"].startswith("/uploads/") and x["media_id"] for x in renders)
    assert route.call_count == 2
    assert route.calls[0].request.headers["x-renderer-token"] == "t"
    media_names = [m["name"] for m in client.get("/media", headers=headers).json()]
    assert any("FORNO" in n and "(EN" in n for n in media_names)

    # unchanged menu → nothing re-rendered
    client.post(f"/menus/{menu_id}/render", json={"languages": ["en", "ar"], "aspects": ["16:9"], "kinds": ["board"]}, headers=headers)
    assert route.call_count == 2

    # renderer failure → failed row with error, menu untouched
    route.mock(return_value=httpx.Response(503, text="busy"))
    client.post(f"/menus/{menu_id}/render", json={"languages": ["bi"], "aspects": ["9:16"], "kinds": ["board"]}, headers=headers)
    renders = client.get(f"/menus/{menu_id}/renders", headers=headers).json()["items"]
    failed = [x for x in renders if x["language"] == "bi"]
    assert failed and failed[0]["status"] == "failed" and "503" in failed[0]["error"]


def test_render_requires_renderer(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "")
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)
    r = client.post(f"/menus/{menu_id}/render", json={}, headers=headers)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "menu.renderer_unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_menu_render.py -v`
Expected: the three new tests FAIL (`ImportError: plan_renders`, 404s)

- [ ] **Step 3: Append the job half to `backend/menu_render.py`**

```python
# ── render jobs ─────────────────────────────────────────────────────────────
import os
import uuid
from dataclasses import dataclass

import httpx

from db import execute, query_one, utc_now_iso
from menus import render_hash


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


async def run_render_job(org_id: int, menu_id: int, specs: list[RenderSpec], *, renderer_url: str,
                         renderer_token: str, upload_dir: str) -> None:
    """Render each spec whose hash isn't already 'ready'. Rows go pending → ready/failed."""
    from menus import get_menu_tree
    tree = get_menu_tree(org_id, menu_id)
    if not tree:
        return
    logo_url = None
    if tree["brand"].get("logo_media_id"):
        row = query_one("SELECT filename FROM media WHERE id = ? AND organization_id = ?",
                        (tree["brand"]["logo_media_id"], org_id))
        if row:
            p = os.path.join(upload_dir, row["filename"])
            if os.path.exists(p):
                import base64, mimetypes
                mime = mimetypes.guess_type(p)[0] or "image/png"
                logo_url = f"data:{mime};base64," + base64.b64encode(open(p, "rb").read()).decode()
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
                resp = await http.post(f"{renderer_url}/render",
                                       json={"html": html, "width": width, "height": height, "scale": 1},
                                       headers={"X-Renderer-Token": renderer_token})
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
```

- [ ] **Step 4: Add the endpoints to `backend/main.py` (after `delete_menu_endpoint`)**

```python
class RenderRequest(BaseModel):
    languages: list[str] = Field(default_factory=lambda: ["en", "ar"])
    aspects:   list[str] = Field(default_factory=lambda: ["16:9"])
    kinds:     list[str] = Field(default_factory=lambda: ["board"])


@app.post("/menus/{menu_id}/render", status_code=202)
async def render_menu_endpoint(
    menu_id: int,
    payload: RenderRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    from menu_render import plan_renders, run_render_job
    if not RENDERER_URL:
        raise http_error(503, "menu.renderer_unavailable", "Rendering is not configured on this server")
    tree = menus_domain.get_menu_tree(principal.organization_id, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    try:
        specs = plan_renders(tree, tree["template"], payload.languages, payload.aspects, payload.kinds)
    except ValueError as exc:
        raise http_error(400, "menu.bad_render_request", str(exc))
    background_tasks.add_task(run_render_job, principal.organization_id, menu_id, specs,
                              renderer_url=RENDERER_URL, renderer_token=RENDERER_TOKEN, upload_dir=UPLOAD_DIR)
    audit(request, action="menu.render", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id,
          details={"boards": len(specs)})
    return {"queued": len(specs)}


@app.get("/menus/{menu_id}/renders")
def list_menu_renders_endpoint(
    menu_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    if not menus_domain.get_menu_tree(principal.organization_id, menu_id):
        raise http_error(404, "menu.not_found", "Menu not found")
    return {"items": _current_renders(menu_id)}
```

Note `run_render_job` reads `renderer_url` etc. from its arguments, which the endpoint takes from the module constants at call time — that is what lets the tests `monkeypatch.setattr(main, "RENDERER_URL", …)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_menu_render.py tests/test_menus.py -v`
Expected: all pass (TestClient runs the background task before returning, so the renders are `ready` by the time the test reads them)

- [ ] **Step 6: Commit**

```bash
git add backend/menu_render.py backend/main.py backend/tests/test_menu_render.py
git commit -m "feat(menus): render pipeline — plan, renderer client, media rows, endpoints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Draft playlist from current boards

**Files:**
- Modify: `backend/main.py` (+ `POST /menus/{id}/playlist`)
- Test: `backend/tests/test_menu_playlist.py`

**Interfaces:**
- Consumes: `_current_renders`, playlists tables (`playlists(organization_id, name, created_at)`, `playlist_items(playlist_id, media_id, duration_seconds, position, created_at)`), `menus.playlist_id`.
- Produces: `POST /menus/{id}/playlist` → the playlist with `items`; ordering = board(EN) boards, then category boards in menu order, then promos; within each group EN before AR before BI; only `status = 'ready'` renders; 10 s each; idempotent (re-running replaces the items of the same playlist).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_menu_playlist.py
import httpx, respx
from tests.test_menu_render import PNG, _org, _menu_with_items


@respx.mock
def test_playlist_is_created_then_updated(client, monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "http://renderer.test")
    monkeypatch.setattr(main, "RENDERER_TOKEN", "t")
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    respx.post("http://renderer.test/render").mock(return_value=httpx.Response(200, content=PNG))
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)
    client.post(f"/menus/{menu_id}/render", json={"languages": ["en", "ar"], "kinds": ["board", "promo"]}, headers=headers)

    r = client.post(f"/menus/{menu_id}/playlist", headers=headers)
    assert r.status_code == 200, r.text
    pl = r.json()
    assert pl["name"] == "Menu — FORNO"
    names = [i["name"] for i in pl["items"]]
    assert names[0].endswith("(EN, 16:9)") and "Menu" in names[0]
    assert names[1].endswith("(AR, 16:9)") and "Menu" in names[1]
    assert any("Promo" in n for n in names[2:])
    assert all(i["duration_seconds"] == 10 for i in pl["items"])

    # second call reuses the playlist and replaces items (no duplicates)
    r2 = client.post(f"/menus/{menu_id}/playlist", headers=headers)
    assert r2.json()["id"] == pl["id"] and len(r2.json()["items"]) == len(pl["items"])
    assert client.get(f"/menus/{menu_id}", headers=headers).json()["playlist_id"] == pl["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_menu_playlist.py -v`
Expected: FAIL with 404 (route missing)

- [ ] **Step 3: Add the endpoint (after `list_menu_renders_endpoint`)**

```python
_KIND_ORDER = {"board": 0, "category": 1, "promo": 2}
_LANG_ORDER = {"en": 0, "ar": 1, "bi": 2}


@app.post("/menus/{menu_id}/playlist")
def menu_playlist_endpoint(
    menu_id: int,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    oid = principal.organization_id
    tree = menus_domain.get_menu_tree(oid, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    renders = [r for r in _current_renders(menu_id) if r["status"] == "ready" and r["media_id"]]
    if not renders:
        raise http_error(409, "menu.no_boards", "Render the boards before creating a playlist")
    cat_pos = {c["id"]: i for i, c in enumerate(tree["categories"])}
    renders.sort(key=lambda r: (_KIND_ORDER[r["kind"]], cat_pos.get(r["category_id"], 0),
                                r["item_id"] or 0, _LANG_ORDER[r["language"]], r["aspect"]))
    name = f"Menu — {tree['name']}"
    playlist = None
    if tree.get("playlist_id"):
        playlist = query_one("SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
                             (tree["playlist_id"], oid))
    if not playlist:
        pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                      (oid, name, utc_now_iso()))
        execute("UPDATE menus SET playlist_id = ? WHERE id = ?", (pid, menu_id))
    else:
        pid = playlist["id"]
        execute("UPDATE playlists SET name = ? WHERE id = ?", (name, pid))
        execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
    for pos, r in enumerate(renders, start=1):
        execute("INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
                "VALUES (?, ?, 10, ?, ?)", (pid, r["media_id"], pos, utc_now_iso()))
    audit(request, action="menu.playlist", actor=principal.user, target_type="playlist",
          target_id=pid, organization_id=oid, details={"menu_id": menu_id, "boards": len(renders)})
    out = query_one("SELECT * FROM playlists WHERE id = ?", (pid,))
    out["items"] = query_all(
        "SELECT pi.id, pi.duration_seconds, pi.position, m.id AS media_id, m.name, m.filename "
        "FROM playlist_items pi JOIN media m ON m.id = pi.media_id WHERE pi.playlist_id = ? ORDER BY pi.position",
        (pid,))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_menu_playlist.py -v && python -m pytest -q`
Expected: PASS, and the full suite green

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_menu_playlist.py
git commit -m "feat(menus): draft playlist from rendered boards

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Dashboard — nav, section shell, Menus list, create/delete

**Files:**
- Modify: `frontend/index.html` (nav button; `<section id="menus">` before `<section id="api-keys">`; `<script src="menus.js">` after `app.js`)
- Create: `frontend/menus.js`
- Modify: `frontend/app.js` (`showSection` hook; capability gating after login)
- Modify: `frontend/styles.css`, `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Interfaces:**
- Consumes: `api()`, `toast()`, `confirmDialog()`, `escHtml()`, `Khan.t()`, `showSection()` from `app.js`.
- Produces: global `Menus` module with `show()`, `openEditor(menuId)` (Task 9), `refreshList()`; element ids `menus`, `menus-list`, `menu-new-btn`, `menu-import-btn` (hidden in Plan A), `menu-editor` (Task 9), `menu-renders` (Task 10). `state.capabilities` set from `GET /ai/capabilities` in `app.js`; nav button `data-section="menus"` is hidden unless `state.capabilities.menus`.

- [ ] **Step 1: Fix the pre-existing contract failure and add keys**

Add to BOTH locale files (`frontend/i18n/en.json` / `ar.json`):

```
"confirm_dialog.ok": "OK" / "موافق"
"nav.menus": "Menus" / "القوائم"
"menus.title": "Menus" / "القوائم"
"menus.intro": "Bilingual menu boards rendered from your menu data. Edit a price, re-render, done." / "لوحات قوائم ثنائية اللغة تُرسم من بيانات قائمتك. عدّل السعر، أعد التصيير، وانتهى."
"menus.new": "+ New menu" / "+ قائمة جديدة"
"menus.import": "Import with AI" / "استيراد بالذكاء الاصطناعي"
"menus.empty": "No menus yet. Create one to get started." / "لا توجد قوائم بعد. أنشئ واحدة للبدء."
"menus.new_prompt_name": "Menu name" / "اسم القائمة"
"menus.card.items": "{n} items" / "{n} عنصر"
"menus.card.never_rendered": "Not rendered yet" / "لم تُصيَّر بعد"
"menus.card.rendered": "Rendered {when}" / "صُيِّرت {when}"
"menus.card.edit": "Edit" / "تعديل"
"menus.card.delete": "Delete" / "حذف"
"menus.confirm_delete": "Delete this menu? Rendered boards stay in your media library." / "حذف هذه القائمة؟ ستبقى اللوحات المصيَّرة في مكتبة الوسائط."
"menus.error.fetch": "Failed to load menus." / "تعذّر تحميل القوائم."
"error.menu.not_found": "Menu not found." / "القائمة غير موجودة."
"error.menu.bad_template": "Unknown template." / "قالب غير معروف."
"error.menu.renderer_unavailable": "Rendering isn't configured on this server." / "التصيير غير مُعدّ على هذا الخادم."
"error.menu.bad_render_request": "Invalid render options." / "خيارات تصيير غير صالحة."
"error.menu.no_boards": "Render the boards first." / "صيّر اللوحات أولًا."
```

Run: `bash scripts/check_ui_contract.sh` → expected `UI contract OK` (the `confirm_dialog.ok` failure is gone; nothing new referenced yet).

- [ ] **Step 2: Markup**

In `frontend/index.html`, in `<nav id="main-nav">` after the `playlists` button add:

```html
          <button data-section="menus" data-i18n="nav.menus" class="hidden" id="nav-menus-btn">Menus</button>
```

Before `<section id="api-keys" …>` add:

```html
        <section id="menus" class="panel hidden">
          <header class="panel-header">
            <h2 data-i18n="menus.title">Menus</h2>
            <div class="panel-actions">
              <button id="menu-import-btn" class="btn hidden" data-i18n="menus.import">Import with AI</button>
              <button id="menu-new-btn" class="btn" data-i18n="menus.new">+ New menu</button>
            </div>
          </header>
          <p class="muted" data-i18n="menus.intro">Bilingual menu boards rendered from your menu data. Edit a price, re-render, done.</p>
          <div id="menus-list" class="menus-list"></div>
          <div id="menu-editor" class="hidden"></div>
          <div id="menu-renders" class="hidden"></div>
        </section>
```

After `<script src="app.js"></script>` add `<script src="menus.js"></script>`.

- [ ] **Step 3: `frontend/menus.js` — list, create, delete**

```javascript
/* Menus section (Plan A). Depends on api(), toast(), confirmDialog(), escHtml(), Khan.t() from app.js. */
const Menus = (() => {
  const st = { menus: [], current: null, templates: [] };

  async function show() {
    document.getElementById("menu-editor").classList.add("hidden");
    document.getElementById("menu-renders").classList.add("hidden");
    document.getElementById("menus-list").classList.remove("hidden");
    await refreshList();
  }

  async function refreshList() {
    try {
      const body = await api("/menus");
      st.menus = body.items || [];
      renderList();
    } catch (err) {
      toast(Khan.t("menus.error.fetch", "Failed to load menus."), "error");
    }
  }

  function renderList() {
    const el = document.getElementById("menus-list");
    el.innerHTML = "";
    if (!st.menus.length) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = Khan.t("menus.empty", "No menus yet. Create one to get started.");
      el.appendChild(p);
      return;
    }
    st.menus.forEach((m) => {
      const card = document.createElement("div");
      card.className = "menu-card";
      const rendered = m.last_rendered_at
        ? Khan.t("menus.card.rendered", "Rendered {when}").replace("{when}", formatDate(m.last_rendered_at))
        : Khan.t("menus.card.never_rendered", "Not rendered yet");
      card.innerHTML = `
        <h3>${escHtml(m.name)}</h3>
        <div class="menu-card-meta">
          <span class="badge">${escHtml(m.template)}</span>
          <span class="muted">${escHtml(Khan.t("menus.card.items", "{n} items").replace("{n}", m.item_count))}</span>
          <span class="muted">${escHtml(rendered)}</span>
        </div>
        <div class="menu-card-actions">
          <button class="btn" data-edit="${m.id}">${escHtml(Khan.t("menus.card.edit", "Edit"))}</button>
          <button class="btn btn-ghost delete-btn" data-delete="${m.id}">${escHtml(Khan.t("menus.card.delete", "Delete"))}</button>
        </div>`;
      card.querySelector("[data-edit]").addEventListener("click", () => openEditor(m.id));
      card.querySelector("[data-delete]").addEventListener("click", () => removeMenu(m.id));
      el.appendChild(card);
    });
  }

  async function createMenu() {
    const name = prompt(Khan.t("menus.new_prompt_name", "Menu name"));
    if (!name || !name.trim()) return;
    try {
      const menu = await api("/menus", { method: "POST", body: JSON.stringify({ name: name.trim(), template: "dark-classic" }) });
      await openEditor(menu.id);
    } catch (err) { toast(err.message, "error"); }
  }

  async function removeMenu(id) {
    const ok = await confirmDialog(Khan.t("menus.confirm_delete", "Delete this menu? Rendered boards stay in your media library."));
    if (!ok) return;
    try {
      await api(`/menus/${id}`, { method: "DELETE" });
      await refreshList();
    } catch (err) { toast(err.message, "error"); }
  }

  async function openEditor(id) { /* Task 9 */ st.current = await api(`/menus/${id}`); toast(st.current.name, "info"); }

  document.getElementById("menu-new-btn")?.addEventListener("click", createMenu);

  return { show, refreshList, openEditor, _st: st };
})();
```

(`formatDate` and `confirmDialog` already exist in `app.js`; `prompt()` is replaced by the editor's own name field in Task 9 — it is acceptable here only as the interim create path.)

- [ ] **Step 4: Wire `app.js`**

In `showSection`, after `if (id === "api-keys") ApiKeys.show();` add `if (id === "menus") Menus.show();`.

Where the dashboard becomes visible after login (the function that calls `loadScreens()` etc. right after auth — locate with `grep -n "loadScreens()" frontend/app.js` and pick the post-login bootstrap), add:

```javascript
  try {
    const caps = await fetch(`${API_BASE}/ai/capabilities`).then((r) => r.json());
    state.capabilities = caps;
    document.getElementById("nav-menus-btn").classList.toggle("hidden", !caps.menus);
    document.getElementById("menu-import-btn").classList.toggle("hidden", !caps.menu_import);
  } catch (_) { state.capabilities = { menus: false, menu_import: false }; }
```

Add `capabilities: { menus: false, menu_import: false },` to the `state` object literal.

- [ ] **Step 5: Styles** (append to `frontend/styles.css`)

```css
/* ── Menus ─────────────────────────────────────────────── */
.panel-actions { display: flex; gap: 8px; }
.menus-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; margin-top: 16px; }
.menu-card { border: 1px solid var(--cream-border, #E8DCC6); border-radius: 14px; padding: 16px; background: var(--bg-card, #fff); }
.menu-card h3 { margin: 0 0 8px; }
.menu-card-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 13px; margin-bottom: 12px; }
.menu-card-actions { display: flex; gap: 8px; }
```

- [ ] **Step 6: Verify**

```bash
bash scripts/check_ui_contract.sh      # UI contract OK
docker compose build frontend && docker compose up -d frontend
```
Browser (the dashboard on port 3000 is **not** the public landing; it is safe to rebuild): with `RENDERER_URL` set, the Menus nav button appears; create a menu; it lists with "0 items"; delete asks for confirmation. Switch to Arabic: labels translate. Unset `RENDERER_URL` → button hidden.

- [ ] **Step 7: Commit**

```bash
git add frontend/index.html frontend/menus.js frontend/app.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "feat(ui): Menus section — list, create, delete, capability gating

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Dashboard — Menu editor (brand, categories, items, template)

**Files:**
- Modify: `frontend/menus.js` (replace the `openEditor` stub; add editor rendering + save)
- Modify: `frontend/styles.css`, `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Interfaces:**
- Consumes: `GET /menus/{id}`, `PUT /menus/{id}`, `GET /menus/templates`, `MediaPicker.open({ allowedTypes: ["image"] })` (returns an array of picks with `media_id`).
- Produces: `Menus.openEditor(id)` renders into `#menu-editor`; `Menus.save()` PUTs the tree and returns the saved tree; the editor keeps the working tree in `st.current` and mutates it on input events (no re-render per keystroke).

- [ ] **Step 1: i18n keys (both files)**

```
"menus.editor.back": "← All menus" / "→ كل القوائم"
"menus.editor.name": "Menu name" / "اسم القائمة"
"menus.editor.brand": "Brand" / "العلامة"
"menus.editor.brand_name_en": "Business name (English)" / "اسم النشاط (إنجليزي)"
"menus.editor.brand_name_ar": "Business name (Arabic)" / "اسم النشاط (عربي)"
"menus.editor.tagline_en": "Tagline (English)" / "الشعار (إنجليزي)"
"menus.editor.tagline_ar": "Tagline (Arabic)" / "الشعار (عربي)"
"menus.editor.primary": "Primary colour" / "اللون الأساسي"
"menus.editor.accent": "Accent colour" / "لون التمييز"
"menus.editor.background": "Background" / "الخلفية"
"menus.editor.logo": "Logo" / "الشعار"
"menus.editor.logo_pick": "Choose from media" / "اختر من الوسائط"
"menus.editor.logo_clear": "Remove" / "إزالة"
"menus.editor.template": "Template" / "القالب"
"menus.editor.categories": "Categories" / "الفئات"
"menus.editor.add_category": "+ Category" / "+ فئة"
"menus.editor.add_item": "+ Item" / "+ عنصر"
"menus.editor.category_en": "Category (English)" / "الفئة (إنجليزي)"
"menus.editor.category_ar": "Category (Arabic)" / "الفئة (عربي)"
"menus.editor.col.name_en": "Name (EN)" / "الاسم (EN)"
"menus.editor.col.name_ar": "Name (AR)" / "الاسم (AR)"
"menus.editor.col.desc_en": "Description (EN)" / "الوصف (EN)"
"menus.editor.col.desc_ar": "Description (AR)" / "الوصف (AR)"
"menus.editor.col.price": "Price" / "السعر"
"menus.editor.col.note": "Note" / "ملاحظة"
"menus.editor.col.badges": "Badges" / "الشارات"
"menus.editor.col.available": "Available" / "متاح"
"menus.editor.remove": "Remove" / "إزالة"
"menus.editor.move_up": "Move up" / "أعلى"
"menus.editor.move_down": "Move down" / "أسفل"
"menus.editor.save": "Save" / "حفظ"
"menus.editor.saved": "Menu saved." / "تم حفظ القائمة."
"menus.editor.render": "Render boards" / "تصيير اللوحات"
"menus.badge.new": "New" / "جديد"
"menus.badge.spicy": "Spicy" / "حار"
"menus.badge.vegan": "Vegan" / "نباتي"
"menus.badge.halal": "Halal" / "حلال"
"menus.badge.popular": "Popular" / "الأكثر طلبًا"
```

- [ ] **Step 2: Editor implementation** (replace the `openEditor` stub in `frontend/menus.js`; add these functions inside the module)

```javascript
  const BADGES = ["new", "spicy", "vegan", "halal", "popular"];

  async function openEditor(id) {
    try {
      const [menu, tpls] = await Promise.all([api(`/menus/${id}`), api("/menus/templates")]);
      st.current = menu; st.templates = tpls.items || [];
    } catch (err) { toast(err.message, "error"); return; }
    document.getElementById("menus-list").classList.add("hidden");
    document.getElementById("menu-renders").classList.add("hidden");
    const ed = document.getElementById("menu-editor");
    ed.classList.remove("hidden");
    renderEditor();
  }

  function field(labelKey, fallback, value, attrs = "") {
    return `<label class="field"><span>${escHtml(Khan.t(labelKey, fallback))}</span><input ${attrs} value="${escAttr(value ?? "")}" /></label>`;
  }

  function renderEditor() {
    const m = st.current, b = m.brand;
    const ed = document.getElementById("menu-editor");
    ed.innerHTML = `
      <button class="btn btn-ghost" id="menu-back">${escHtml(Khan.t("menus.editor.back", "← All menus"))}</button>
      <div class="menu-editor-grid">
        <section class="panel-sub">
          ${field("menus.editor.name", "Menu name", m.name, 'data-f="name" maxlength="120"')}
          <h3>${escHtml(Khan.t("menus.editor.brand", "Brand"))}</h3>
          ${field("menus.editor.brand_name_en", "Business name (English)", b.name_en, 'data-b="name_en" maxlength="120"')}
          ${field("menus.editor.brand_name_ar", "Business name (Arabic)", b.name_ar, 'data-b="name_ar" dir="rtl" maxlength="120"')}
          ${field("menus.editor.tagline_en", "Tagline (English)", b.tagline_en, 'data-b="tagline_en" maxlength="160"')}
          ${field("menus.editor.tagline_ar", "Tagline (Arabic)", b.tagline_ar, 'data-b="tagline_ar" dir="rtl" maxlength="160"')}
          <div class="colour-row">
            ${field("menus.editor.primary", "Primary colour", b.primary, 'type="color" data-b="primary"')}
            ${field("menus.editor.accent", "Accent colour", b.accent, 'type="color" data-b="accent"')}
            ${field("menus.editor.background", "Background", b.background, 'type="color" data-b="background"')}
          </div>
          <div class="field"><span>${escHtml(Khan.t("menus.editor.logo", "Logo"))}</span>
            <div class="logo-row">
              <span id="menu-logo-preview">${b.logo_media_id ? `#${b.logo_media_id}` : "—"}</span>
              <button class="btn" id="menu-logo-pick">${escHtml(Khan.t("menus.editor.logo_pick", "Choose from media"))}</button>
              <button class="btn btn-ghost" id="menu-logo-clear">${escHtml(Khan.t("menus.editor.logo_clear", "Remove"))}</button>
            </div></div>
          <h3>${escHtml(Khan.t("menus.editor.template", "Template"))}</h3>
          <div class="template-picker">${st.templates.map((t) => `
            <button class="template-card tpl-${t.id}${t.id === m.template ? " selected" : ""}" data-tpl="${t.id}"
                    style="--p:${escAttr(b.primary)};--a:${escAttr(b.accent)};--bg:${escAttr(b.background)}">
              <span class="tpl-preview"><i></i><i></i><i></i></span>
              <span>${escHtml(document.documentElement.lang === "ar" ? t.name_ar : t.name_en)}</span>
            </button>`).join("")}</div>
        </section>
        <section class="panel-sub">
          <div class="row-between"><h3>${escHtml(Khan.t("menus.editor.categories", "Categories"))}</h3>
            <button class="btn" id="menu-add-cat">${escHtml(Khan.t("menus.editor.add_category", "+ Category"))}</button></div>
          <div id="menu-cats">${m.categories.map((c, ci) => catHtml(c, ci)).join("")}</div>
        </section>
      </div>
      <div class="editor-actions">
        <button class="btn btn-primary" id="menu-save">${escHtml(Khan.t("menus.editor.save", "Save"))}</button>
        <button class="btn" id="menu-render">${escHtml(Khan.t("menus.editor.render", "Render boards"))}</button>
      </div>`;
    bindEditor();
  }

  function catHtml(c, ci) {
    return `<div class="menu-cat" data-ci="${ci}">
      <div class="menu-cat-head">
        <input data-c="name_en" placeholder="${escAttr(Khan.t("menus.editor.category_en", "Category (English)"))}" value="${escAttr(c.name_en)}" maxlength="120" />
        <input data-c="name_ar" dir="rtl" placeholder="${escAttr(Khan.t("menus.editor.category_ar", "Category (Arabic)"))}" value="${escAttr(c.name_ar || "")}" maxlength="120" />
        <button class="btn btn-ghost" data-cat-up title="${escAttr(Khan.t("menus.editor.move_up", "Move up"))}">↑</button>
        <button class="btn btn-ghost" data-cat-down title="${escAttr(Khan.t("menus.editor.move_down", "Move down"))}">↓</button>
        <button class="btn btn-ghost delete-btn" data-cat-remove>${escHtml(Khan.t("menus.editor.remove", "Remove"))}</button>
      </div>
      <table class="menu-items">
        <thead><tr>
          <th>${escHtml(Khan.t("menus.editor.col.name_en", "Name (EN)"))}</th><th>${escHtml(Khan.t("menus.editor.col.name_ar", "Name (AR)"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.desc_en", "Description (EN)"))}</th><th>${escHtml(Khan.t("menus.editor.col.desc_ar", "Description (AR)"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.price", "Price"))}</th><th>${escHtml(Khan.t("menus.editor.col.note", "Note"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.badges", "Badges"))}</th><th>${escHtml(Khan.t("menus.editor.col.available", "Available"))}</th><th></th>
        </tr></thead>
        <tbody>${c.items.map((it, ii) => itemHtml(it, ii)).join("")}</tbody>
      </table>
      <button class="btn" data-add-item>${escHtml(Khan.t("menus.editor.add_item", "+ Item"))}</button>
    </div>`;
  }

  function itemHtml(it, ii) {
    return `<tr data-ii="${ii}">
      <td><input data-i="name_en" value="${escAttr(it.name_en)}" maxlength="120" /></td>
      <td><input data-i="name_ar" dir="rtl" value="${escAttr(it.name_ar || "")}" maxlength="120" /></td>
      <td><input data-i="description_en" value="${escAttr(it.description_en || "")}" maxlength="300" /></td>
      <td><input data-i="description_ar" dir="rtl" value="${escAttr(it.description_ar || "")}" maxlength="300" /></td>
      <td><input data-i="price" type="number" step="0.001" min="0" value="${escAttr(it.price ?? "")}" class="price-input" /></td>
      <td><input data-i="price_note" value="${escAttr(it.price_note || "")}" maxlength="60" /></td>
      <td class="badges-cell">${BADGES.map((b) => `<label><input type="checkbox" data-badge="${b}"${it.badges.includes(b) ? " checked" : ""}/>${escHtml(Khan.t(`menus.badge.${b}`, b))}</label>`).join("")}</td>
      <td><input type="checkbox" data-i="is_available"${it.is_available ? " checked" : ""} /></td>
      <td><button class="btn btn-ghost delete-btn" data-item-remove>×</button></td>
    </tr>`;
  }

  function bindEditor() {
    const m = st.current, ed = document.getElementById("menu-editor");
    ed.querySelector("#menu-back").addEventListener("click", show);
    ed.querySelector('[data-f="name"]').addEventListener("input", (e) => { m.name = e.target.value; });
    ed.querySelectorAll("[data-b]").forEach((inp) => inp.addEventListener("input", (e) => {
      m.brand[e.target.dataset.b] = e.target.value === "" ? null : e.target.value;
      if (["primary", "accent", "background"].includes(e.target.dataset.b)) {
        ed.querySelectorAll(".template-card").forEach((c) => c.style.setProperty(`--${e.target.dataset.b === "primary" ? "p" : e.target.dataset.b === "accent" ? "a" : "bg"}`, e.target.value));
      }
    }));
    ed.querySelector("#menu-logo-pick").addEventListener("click", async () => {
      const picks = await MediaPicker.open({ allowedTypes: ["image"] });
      if (picks && picks.length) { m.brand.logo_media_id = picks[0].media_id; ed.querySelector("#menu-logo-preview").textContent = `#${picks[0].media_id}`; }
    });
    ed.querySelector("#menu-logo-clear").addEventListener("click", () => { m.brand.logo_media_id = null; ed.querySelector("#menu-logo-preview").textContent = "—"; });
    ed.querySelectorAll("[data-tpl]").forEach((btn) => btn.addEventListener("click", () => {
      m.template = btn.dataset.tpl;
      ed.querySelectorAll(".template-card").forEach((c) => c.classList.toggle("selected", c.dataset.tpl === m.template));
    }));
    ed.querySelector("#menu-add-cat").addEventListener("click", () => { m.categories.push({ name_en: "", name_ar: "", items: [] }); renderEditor(); });
    ed.querySelectorAll(".menu-cat").forEach((catEl) => {
      const ci = Number(catEl.dataset.ci), c = m.categories[ci];
      catEl.querySelectorAll("[data-c]").forEach((inp) => inp.addEventListener("input", (e) => { c[e.target.dataset.c] = e.target.value; }));
      catEl.querySelector("[data-cat-remove]").addEventListener("click", () => { m.categories.splice(ci, 1); renderEditor(); });
      catEl.querySelector("[data-cat-up]").addEventListener("click", () => { if (ci > 0) { [m.categories[ci - 1], m.categories[ci]] = [m.categories[ci], m.categories[ci - 1]]; renderEditor(); } });
      catEl.querySelector("[data-cat-down]").addEventListener("click", () => { if (ci < m.categories.length - 1) { [m.categories[ci + 1], m.categories[ci]] = [m.categories[ci], m.categories[ci + 1]]; renderEditor(); } });
      catEl.querySelector("[data-add-item]").addEventListener("click", () => { c.items.push({ name_en: "", name_ar: "", price: null, badges: [], is_available: true }); renderEditor(); });
      catEl.querySelectorAll("tr[data-ii]").forEach((row) => {
        const it = c.items[Number(row.dataset.ii)];
        row.querySelectorAll("[data-i]").forEach((inp) => inp.addEventListener("input", (e) => {
          const k = e.target.dataset.i;
          if (k === "is_available") it[k] = e.target.checked;
          else if (k === "price") it[k] = e.target.value === "" ? null : e.target.value;
          else it[k] = e.target.value === "" ? null : e.target.value;
        }));
        row.querySelectorAll("[data-badge]").forEach((cb) => cb.addEventListener("change", (e) => {
          const b = e.target.dataset.badge;
          it.badges = e.target.checked ? [...new Set([...it.badges, b])] : it.badges.filter((x) => x !== b);
        }));
        row.querySelector("[data-item-remove]").addEventListener("click", () => { c.items.splice(Number(row.dataset.ii), 1); renderEditor(); });
      });
    });
    ed.querySelector("#menu-save").addEventListener("click", save);
    ed.querySelector("#menu-render").addEventListener("click", () => Menus.renderBoards()); // Task 10
  }

  function payload() {
    const m = st.current;
    return {
      name: m.name, template: m.template, brand: m.brand,
      categories: m.categories.map((c) => ({
        id: c.id, name_en: c.name_en, name_ar: c.name_ar || "",
        items: c.items.map((it) => ({
          id: it.id, name_en: it.name_en, name_ar: it.name_ar || "", description_en: it.description_en || null,
          description_ar: it.description_ar || null, price: it.price === "" ? null : it.price,
          price_note: it.price_note || null, badges: it.badges || [], is_available: it.is_available !== false,
        })),
      })),
    };
  }

  async function save() {
    try {
      st.current = await api(`/menus/${st.current.id}`, { method: "PUT", body: JSON.stringify(payload()) });
      toast(Khan.t("menus.editor.saved", "Menu saved."), "success");
      renderEditor();
      return st.current;
    } catch (err) { toast(err.message, "error"); return null; }
  }
```

Export `save` and (for Task 10) `renderBoards` from the module's return object: `return { show, refreshList, openEditor, save, renderBoards, _st: st };` — add a placeholder `async function renderBoards() { await save(); }` now; Task 10 replaces it.

- [ ] **Step 3: Styles** (append)

```css
.menu-editor-grid { display: grid; grid-template-columns: 340px 1fr; gap: 24px; margin-top: 14px; }
@media (max-width: 1000px) { .menu-editor-grid { grid-template-columns: 1fr; } }
.panel-sub { border: 1px solid var(--cream-border, #E8DCC6); border-radius: 14px; padding: 16px; }
.colour-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.colour-row input[type="color"] { width: 100%; height: 36px; padding: 0; border: 0; background: none; }
.logo-row { display: flex; gap: 8px; align-items: center; }
.template-picker { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.template-card { border: 2px solid transparent; border-radius: 10px; padding: 6px; display: grid; gap: 6px; font-size: 12px; }
.template-card.selected { border-color: var(--peach-deep, #E09478); }
.tpl-preview { display: grid; gap: 3px; height: 56px; border-radius: 6px; padding: 6px; background: var(--bg); }
.tpl-preview i { display: block; height: 6px; border-radius: 3px; background: var(--a); opacity: .8; }
.tpl-preview i:first-child { height: 12px; background: var(--p); }
.tpl-cream-cafe .tpl-preview { background: #F5E9D2; }
.tpl-luxe .tpl-preview { background: #0F0A14; }
.row-between { display: flex; justify-content: space-between; align-items: center; }
.menu-cat { border-top: 1px dashed var(--cream-border, #E8DCC6); padding: 12px 0; }
.menu-cat-head { display: grid; grid-template-columns: 1fr 1fr auto auto auto; gap: 6px; margin-bottom: 8px; }
.menu-items { width: 100%; border-collapse: collapse; font-size: 13px; }
.menu-items th { text-align: start; font-weight: 600; padding: 4px; }
.menu-items td { padding: 2px; }
.menu-items input:not([type="checkbox"]) { width: 100%; min-width: 80px; }
.price-input { max-width: 90px; }
.badges-cell { white-space: nowrap; }
.badges-cell label { margin-inline-end: 6px; font-size: 12px; }
.editor-actions { display: flex; gap: 8px; margin-top: 16px; }
```

- [ ] **Step 4: Verify**

`bash scripts/check_ui_contract.sh` → OK. Rebuild the frontend container; browser: create a menu, add a category + two items with Arabic names and a `popular` badge, pick a colour and the `luxe` template, Save → toast; reload → data persists with the same ids (check the network response). Arabic UI: the AR inputs are RTL, the EN inputs LTR. Validation error (e.g. empty item name) shows the server message as a toast.

- [ ] **Step 5: Commit**

```bash
git add frontend/menus.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "feat(ui): menu editor — brand, categories, bilingual items, template picker

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Dashboard — render boards, renders grid, create playlist

**Files:**
- Modify: `frontend/menus.js` (real `renderBoards`, renders view with polling)
- Modify: `frontend/styles.css`, `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Interfaces:**
- Consumes: `POST /menus/{id}/render`, `GET /menus/{id}/renders`, `POST /menus/{id}/playlist`.
- Produces: `Menus.renderBoards()` saves, asks for languages/aspects/kinds via checkboxes in a small inline form, POSTs, then polls `/renders` every 2 s until no row is `pending` (max 90 s); `Menus.showRenders()` renders `#menu-renders`.

- [ ] **Step 1: i18n keys (both files)**

```
"menus.render.title": "Boards" / "اللوحات"
"menus.render.options": "What to render" / "ماذا تُصيّر"
"menus.render.lang.en": "English" / "إنجليزي"
"menus.render.lang.ar": "Arabic" / "عربي"
"menus.render.lang.bi": "Bilingual" / "ثنائي اللغة"
"menus.render.aspect.landscape": "Landscape 16:9" / "أفقي 16:9"
"menus.render.aspect.portrait": "Portrait 9:16" / "عمودي 9:16"
"menus.render.kind.board": "Full menu" / "القائمة كاملة"
"menus.render.kind.category": "One board per category" / "لوحة لكل فئة"
"menus.render.kind.promo": "Promos (new / popular items)" / "عروض (العناصر الجديدة / الأكثر طلبًا)"
"menus.render.go": "Render" / "تصيير"
"menus.render.queued": "Rendering {n} boards…" / "جارٍ تصيير {n} لوحة…"
"menus.render.done": "Boards ready." / "اللوحات جاهزة."
"menus.render.stale": "The menu changed after these boards were rendered." / "تغيّرت القائمة بعد تصيير هذه اللوحات."
"menus.render.failed": "Failed" / "فشل"
"menus.render.empty": "No boards yet." / "لا توجد لوحات بعد."
"menus.render.playlist": "Create playlist" / "إنشاء قائمة تشغيل"
"menus.render.playlist_open": "Open playlist" / "فتح قائمة التشغيل"
"menus.render.playlist_done": "Playlist \"{name}\" updated." / "تم تحديث قائمة التشغيل \"{name}\"."
"menus.render.download": "Download" / "تنزيل"
"menus.render.back": "← Editor" / "→ المحرر"
```

- [ ] **Step 2: Implementation** (inside the module; replace the placeholder `renderBoards`)

```javascript
  function renderOptionsHtml() {
    const cb = (name, val, key, fb, checked) => `<label><input type="checkbox" name="${name}" value="${val}"${checked ? " checked" : ""}/> ${escHtml(Khan.t(key, fb))}</label>`;
    return `<div class="render-options">
      <strong>${escHtml(Khan.t("menus.render.options", "What to render"))}</strong>
      <div>${cb("lang", "en", "menus.render.lang.en", "English", true)}${cb("lang", "ar", "menus.render.lang.ar", "Arabic", true)}${cb("lang", "bi", "menus.render.lang.bi", "Bilingual", false)}</div>
      <div>${cb("aspect", "16:9", "menus.render.aspect.landscape", "Landscape 16:9", true)}${cb("aspect", "9:16", "menus.render.aspect.portrait", "Portrait 9:16", false)}</div>
      <div>${cb("kind", "board", "menus.render.kind.board", "Full menu", true)}${cb("kind", "category", "menus.render.kind.category", "One board per category", false)}${cb("kind", "promo", "menus.render.kind.promo", "Promos (new / popular items)", false)}</div>
      <button class="btn btn-primary" id="menu-render-go">${escHtml(Khan.t("menus.render.go", "Render"))}</button>
    </div>`;
  }

  async function renderBoards() {
    const saved = await save();
    if (!saved) return;
    showRenders();
  }

  async function showRenders() {
    document.getElementById("menu-editor").classList.add("hidden");
    const box = document.getElementById("menu-renders");
    box.classList.remove("hidden");
    box.innerHTML = `<button class="btn btn-ghost" id="menu-renders-back">${escHtml(Khan.t("menus.render.back", "← Editor"))}</button>
      <h3>${escHtml(Khan.t("menus.render.title", "Boards"))}</h3>${renderOptionsHtml()}
      <p id="menu-renders-status" class="muted"></p><div id="menu-renders-grid" class="renders-grid"></div>
      <div class="editor-actions"><button class="btn btn-primary" id="menu-playlist-btn">${escHtml(Khan.t("menus.render.playlist", "Create playlist"))}</button></div>`;
    box.querySelector("#menu-renders-back").addEventListener("click", () => { box.classList.add("hidden"); document.getElementById("menu-editor").classList.remove("hidden"); });
    box.querySelector("#menu-render-go").addEventListener("click", startRender);
    box.querySelector("#menu-playlist-btn").addEventListener("click", createPlaylist);
    await refreshRenders();
  }

  function picked(name) { return [...document.querySelectorAll(`#menu-renders input[name="${name}"]:checked`)].map((i) => i.value); }

  async function startRender() {
    const body = { languages: picked("lang"), aspects: picked("aspect"), kinds: picked("kind") };
    try {
      const r = await api(`/menus/${st.current.id}/render`, { method: "POST", body: JSON.stringify(body) });
      document.getElementById("menu-renders-status").textContent = Khan.t("menus.render.queued", "Rendering {n} boards…").replace("{n}", r.queued);
      await pollRenders();
    } catch (err) { toast(err.message, "error"); }
  }

  async function pollRenders() {
    for (let i = 0; i < 45; i++) {
      const items = await refreshRenders();
      if (!items.some((x) => x.status === "pending")) {
        document.getElementById("menu-renders-status").textContent = Khan.t("menus.render.done", "Boards ready.");
        return;
      }
      await new Promise((res) => setTimeout(res, 2000));
    }
  }

  async function refreshRenders() {
    const grid = document.getElementById("menu-renders-grid");
    let items = [];
    try { items = (await api(`/menus/${st.current.id}/renders`)).items || []; } catch (err) { toast(err.message, "error"); return []; }
    grid.innerHTML = items.length ? "" : `<p class="empty-state">${escHtml(Khan.t("menus.render.empty", "No boards yet."))}</p>`;
    const stale = st.current.last_rendered_at && st.current.updated_at > st.current.last_rendered_at;
    if (stale) grid.insertAdjacentHTML("beforebegin", `<p class="muted stale-note">${escHtml(Khan.t("menus.render.stale", "The menu changed after these boards were rendered."))}</p>`);
    items.forEach((x) => {
      const card = document.createElement("div");
      card.className = `render-card status-${x.status}`;
      card.innerHTML = `${x.url ? `<img src="${escAttr(API_BASE + x.url)}" alt="" loading="lazy" />` : `<div class="render-ph">${x.status === "failed" ? escHtml(Khan.t("menus.render.failed", "Failed")) : "…"}</div>`}
        <div class="render-meta"><span class="badge">${escHtml(x.kind)}</span><span class="badge">${escHtml(x.language.toUpperCase())}</span><span class="badge">${escHtml(x.aspect)}</span>
        ${x.url ? `<a class="btn btn-ghost" href="${escAttr(API_BASE + x.url)}" download>${escHtml(Khan.t("menus.render.download", "Download"))}</a>` : ""}</div>
        ${x.error ? `<p class="muted">${escHtml(x.error)}</p>` : ""}`;
      grid.appendChild(card);
    });
    return items;
  }

  async function createPlaylist() {
    try {
      const pl = await api(`/menus/${st.current.id}/playlist`, { method: "POST" });
      st.current.playlist_id = pl.id;
      toast(Khan.t("menus.render.playlist_done", 'Playlist "{name}" updated.').replace("{name}", pl.name), "success");
      document.getElementById("menu-playlist-btn").textContent = Khan.t("menus.render.playlist_open", "Open playlist");
      document.getElementById("menu-playlist-btn").onclick = () => { showSection("playlists"); };
    } catch (err) { toast(err.message, "error"); }
  }
```

Update the module's return: `return { show, refreshList, openEditor, save, renderBoards, showRenders, _st: st };`.

- [ ] **Step 3: Styles** (append)

```css
.render-options { display: grid; gap: 6px; margin: 8px 0 12px; }
.render-options label { margin-inline-end: 12px; }
.renders-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.render-card { border: 1px solid var(--cream-border, #E8DCC6); border-radius: 12px; overflow: hidden; background: #000; }
.render-card img { width: 100%; display: block; }
.render-ph { aspect-ratio: 16 / 9; display: grid; place-items: center; color: #fff; }
.render-card.status-failed .render-ph { background: #5A1F1F; }
.render-meta { display: flex; gap: 6px; align-items: center; padding: 8px; background: var(--bg-card, #fff); flex-wrap: wrap; }
.stale-note { grid-column: 1 / -1; }
```

- [ ] **Step 4: Verify end-to-end in the browser**

Preconditions: renderer running (Task 5), backend restarted with `RENDERER_URL`/`RENDERER_TOKEN` (`docker compose up -d backend` — this is the API container, not the landing site). Flow: Menus → open the menu from Task 9 → **Render boards** → choose EN+AR, 16:9, Full menu + Promos → Render → status counts down, boards appear as real images (open one: text is crisp, Arabic joined correctly, prices in JetBrains Mono) → **Create playlist** → toast, Playlists section lists `Menu — <name>` with the boards → assign it to a screen and confirm the player shows a board. Repeat Render without changes → "Boards ready." immediately, no new media rows. Edit a price → editor → Render → stale note shown before, new board after.

- [ ] **Step 5: Commit**

```bash
git add frontend/menus.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "feat(ui): render boards, renders grid with polling, draft playlist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Docs, contract gate, PR

**Files:**
- Modify: `frontend/api-docs.html` (add the `/menus/*` endpoints to the endpoint list, same format as the playlists entries), `AGENTS.md` (one paragraph: menus exist; AI import is Plan B)
- Modify: `README.md` (renderer service + the two env vars)

- [ ] **Step 1: Docs** — add the endpoints table rows and the env vars. Keep the marketing site untouched; the landing "Coming soon" copy stays until Plan B ships (spec §12).

- [ ] **Step 2: Full gate**

```bash
bash scripts/check_ui_contract.sh                      # UI contract OK
cd backend && python -m pytest -q && cd ..            # all green
docker compose exec renderer python smoke.py          # OK 1920x1080
```

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/menus-core
gh pr create --title "feat(menus): Plan A — Menus content type, templates, renderer, boards → playlist" \
  --body "Implements Plan A of docs/superpowers/specs/2026-09-08-ai-menu-import-design.md: menus/categories/items schema, three bilingual board templates, an internal Playwright renderer service, render → media → playlist pipeline, and the dashboard Menus section. No AI yet (Plan B). Operator: set RENDERER_TOKEN in .env and rebuild backend + renderer.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-review

- **Spec coverage:** §2 product shape ✔ (T2, T8–T10) · §3 tables ✔ (T1, incl. `menu_imports` for Plan B) · §4 renderer `/render`, token, concurrency, fonts, no host port ✔ (T5); `/screenshot` deferred to Plan B as stated · §5 three templates, three kinds, three languages, two aspects, brand CSS variables, no remote images (logo embedded as data URI) ✔ (T4, T6) · §6.4 render + naming + hash skip + playlist ✔ (T6, T7) · §7 all non-AI endpoints + capabilities + audit ✔ (T3, T6, T7); rate limit on `/menus/import` is Plan B · §8 list/editor/renders, capability gating, RTL columns ✔ (T8–T10) · §10 renderer isolation (offline context, no DB/uploads access) ✔ (T5) · §11 backend tests, renderer smoke, browser smoke ✔ (T2–T7, T5 §4, T10 §4) · §12 branch/PR ✔ (T11).
- **Placeholder scan:** the only substitution is `<PINNED_TAG>` in Task 5, resolved by an explicit command in Step 1. No TBD/TODO.
- **Type consistency:** `render_hash(tree, template_id, template_version, kind, language, aspect, category_id, item_id)` used identically in T2/T6; `RenderSpec` fields match `plan_renders`/`run_render_job`; `_current_renders(menu_id)` returns `url`, `media_id`, `status`, `error` consumed by T7 and T10; `Menus.save()` returns the tree or `null`, consumed by `renderBoards`; capability keys `menus`/`menu_import` match T3 and T8.
