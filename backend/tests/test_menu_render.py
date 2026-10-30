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


def test_bilingual_prints_each_language_once():
    import copy
    from menu_render import build_html
    tree = copy.deepcopy(TREE)
    tree["brand"]["tagline_en"] = ""                       # English optional, Arabic given
    item = tree["categories"][0]["items"][0]
    item["description_en"], item["description_ar"] = None, "عجينة مخمرة ٤٨ ساعة"
    for kind, kw in (("board", {}), ("promo", {"item_id": 100})):
        html = build_html(tree, "dark-classic", kind, "bi", "16:9", **kw)
        assert html.count("عجينة مخمرة ٤٨ ساعة") == 1, kind
        # Arabic-only text keeps the .ar span, which is what sets direction: rtl
        assert '<span class="ar">عجينة مخمرة ٤٨ ساعة</span>' in html, kind
    html = build_html(tree, "dark-classic", "board", "bi", "16:9")
    assert html.count("فرن حطب · السالمية") == 1
    assert html.count("Classics") == 1 and html.count("الكلاسيكية") == 1


def test_bilingual_escapes_both_languages():
    import copy
    from menu_render import build_html
    tree = copy.deepcopy(TREE)
    item = tree["categories"][0]["items"][0]
    item["name_ar"] = "<img src=x onerror=alert(1)>"
    item["description_ar"] = "<b>حار</b>"                 # no English: the Arabic-only path
    html = build_html(tree, "dark-classic", "board", "bi", "16:9")
    assert "<script>x" not in html and "<img src=x" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;b&gt;حار&lt;/b&gt;" in html


def test_category_and_promo_kinds():
    from menu_render import build_html, featured_items
    assert [i["id"] for i in featured_items(TREE)] == [100]
    html_c = build_html(TREE, "luxe", "category", "en", "16:9", category_id=10)
    assert "Classics" in html_c
    html_p = build_html(TREE, "luxe", "promo", "en", "16:9", item_id=100)
    assert "Margherita" in html_p and "2.750" in html_p
    with pytest.raises(ValueError):
        build_html(TREE, "luxe", "promo", "en", "16:9", item_id=999)


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
    # the PNG must really be on disk under UPLOAD_DIR, with the bytes the renderer returned
    import os
    for x in renders:
        path = os.path.join(str(tmp_path), x["url"].split("/uploads/")[1])
        assert os.path.exists(path), path
        with open(path, "rb") as f:
            assert f.read() == PNG
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


def _renderer(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "http://renderer.test")
    monkeypatch.setattr(main, "RENDERER_TOKEN", "t")
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))


def _board(client, headers, menu_id):
    """The en/16:9 board the API calls current -- what the playlist is built from."""
    renders = client.get(f"/menus/{menu_id}/renders", headers=headers).json()["items"]
    return next(r for r in renders if (r["kind"], r["language"], r["aspect"]) == ("board", "en", "16:9"))


def _set_price(client, headers, menu_id, price):
    tree = client.get(f"/menus/{menu_id}", headers=headers).json()
    tree["categories"][0]["items"][0]["price"] = price
    r = client.put(f"/menus/{menu_id}", json=tree, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


BOARD_EN = {"languages": ["en"], "aspects": ["16:9"], "kinds": ["board"]}


@respx.mock
def test_reverted_menu_renders_again_and_its_board_becomes_current(client, monkeypatch, tmp_path):
    # 2.750 → 3.000 → back to 2.750. The original 2.750 row is still 'ready',
    # but the newest row for the slot (the one served) is the 3.000 board.
    import json
    from menu_render import plan_renders
    _renderer(monkeypatch, tmp_path)
    prices_rendered = []

    def render(request):
        html = json.loads(request.content)["html"]
        prices_rendered.append("3.000" if "3.000" in html else "2.750")
        return httpx.Response(200, content=PNG)

    respx.post("http://renderer.test/render").mock(side_effect=render)
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)

    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)
    _set_price(client, headers, menu_id, "3.000")
    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)
    three = _board(client, headers, menu_id)
    tree = _set_price(client, headers, menu_id, "2.750")
    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)

    assert prices_rendered == ["2.750", "3.000", "2.750"]
    board = _board(client, headers, menu_id)
    want = plan_renders(tree, tree["template"], ["en"], ["16:9"], ["board"])[0].render_hash
    assert board["status"] == "ready" and board["render_hash"] == want
    assert board["media_id"] and board["media_id"] != three["media_id"]
    pl = client.post(f"/menus/{menu_id}/playlist", headers=headers).json()
    assert [i["media_id"] for i in pl["items"]] == [board["media_id"]]

    # and rendering the reverted menu again is still a no-op
    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)
    assert len(prices_rendered) == 3


@respx.mock
def test_board_whose_png_was_deleted_renders_again(client, monkeypatch, tmp_path):
    # menu_renders.media_id is ON DELETE SET NULL: deleting the PNG from the
    # media library leaves a 'ready' row with no media behind it.
    _renderer(monkeypatch, tmp_path)
    route = respx.post("http://renderer.test/render").mock(return_value=httpx.Response(200, content=PNG))
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)

    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)
    gone = _board(client, headers, menu_id)["media_id"]
    assert client.delete(f"/media/{gone}", headers=headers).status_code == 200
    client.post(f"/menus/{menu_id}/render", json=BOARD_EN, headers=headers)

    assert route.call_count == 2
    board = _board(client, headers, menu_id)
    assert board["status"] == "ready" and board["media_id"] and board["media_id"] != gone
    r = client.post(f"/menus/{menu_id}/playlist", headers=headers)
    assert r.status_code == 200, r.text
    assert [i["media_id"] for i in r.json()["items"]] == [board["media_id"]]


def test_render_requires_renderer(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "RENDERER_URL", "")
    headers, _ = _org(client)
    menu_id = _menu_with_items(client, headers)
    r = client.post(f"/menus/{menu_id}/render", json={}, headers=headers)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "menu.renderer_unavailable"
