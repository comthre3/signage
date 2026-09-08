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
