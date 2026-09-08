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
