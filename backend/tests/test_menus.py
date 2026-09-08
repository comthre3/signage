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
