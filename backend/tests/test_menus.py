"""Menus core (Plan A): schema, domain module, CRUD."""
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

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


def test_tree_rejects_duplicate_ids():
    from menus import MenuTreeIn, Brand, CategoryIn, ItemIn

    with pytest.raises(ValidationError):
        MenuTreeIn(
            name="FORNO", template="dark-classic", brand=Brand(name_en="FORNO"),
            categories=[CategoryIn(name_en="Classics", items=[
                ItemIn(id=1, name_en="Margherita"),
                ItemIn(id=1, name_en="Marinara"),
            ])],
        )

    with pytest.raises(ValidationError):
        MenuTreeIn(
            name="FORNO", template="dark-classic", brand=Brand(name_en="FORNO"),
            categories=[
                CategoryIn(id=1, name_en="Classics"),
                CategoryIn(id=1, name_en="Drinks"),
            ],
        )


def test_list_and_delete_menu(client):
    from menus import (
        MenuTreeIn, Brand, CategoryIn, ItemIn,
        create_menu, list_menus, delete_menu, get_menu_tree, replace_menu_tree,
    )

    _, org_id = _org_id(client)
    _, other_org_id = _org_id(client)

    with pytest.raises(ValueError):
        create_menu(org_id, "X", "nope")

    menu_id = create_menu(org_id, "FORNO", "dark-classic")
    other_menu_id = create_menu(org_id, "EMPTY", "dark-classic")

    replace_menu_tree(org_id, menu_id, MenuTreeIn(
        name="FORNO", template="dark-classic", brand=Brand(name_en="FORNO"),
        categories=[CategoryIn(name_en="Classics", items=[
            ItemIn(name_en="Margherita"),
            ItemIn(name_en="Marinara"),
        ])],
    ))

    menus = {m["id"]: m for m in list_menus(org_id)}
    assert set(menus) == {menu_id, other_menu_id}
    assert menus[menu_id]["category_count"] == 1
    assert menus[menu_id]["item_count"] == 2
    assert menus[other_menu_id]["category_count"] == 0
    assert menus[other_menu_id]["item_count"] == 0

    assert delete_menu(other_org_id, menu_id) is False

    assert delete_menu(org_id, menu_id) is True
    assert get_menu_tree(org_id, menu_id) is None
    assert query_all("SELECT id FROM menu_categories WHERE menu_id = ?", (menu_id,)) == []


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
