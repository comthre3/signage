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
