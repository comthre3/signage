"""Pin the contract that the player relies on for offline prefetch:
playlist items must expose `url` starting with /uploads/."""
import io

from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload_image(client: TestClient, headers: dict, name: str = "offline_test.png") -> int:
    """Upload a minimal valid PNG and return media id."""
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    r = client.post(
        "/media/upload",
        files={"file": (name, io.BytesIO(png), "image/png")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_playlist_with_media(client: TestClient, signed_up_org: dict) -> dict:
    """Create a playlist with one media item, attach to a screen.

    Returns {screen_token}.
    """
    bearer = _auth_headers(signed_up_org["token"])

    # 1. Upload a media file
    media_id = _upload_image(client, bearer)

    # 2. Create a playlist
    r = client.post("/playlists", json={"name": "Offline test playlist"}, headers=bearer)
    assert r.status_code == 200, r.text
    playlist_id = r.json()["id"]

    # 3. Add the media item to the playlist
    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id},
        headers=bearer,
    )
    assert r.status_code == 200, r.text

    # 4. Create a screen (site_id is optional, skip for simplicity)
    r = client.post("/screens", json={"name": "Offline Screen"}, headers=bearer)
    assert r.status_code == 200, r.text
    screen = r.json()
    screen_id = screen["id"]
    screen_token = screen["token"]

    # 5. Attach the playlist to the screen via PUT
    r = client.put(
        f"/screens/{screen_id}",
        json={"playlist_id": playlist_id},
        headers=bearer,
    )
    assert r.status_code == 200, r.text

    return {"screen_token": screen_token}


def test_playlist_response_items_have_uploads_url(client, signed_up_org):
    info = _create_playlist_with_media(client, signed_up_org)
    r = client.get(f"/screens/{info['screen_token']}/content")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("items") or []
    assert len(items) >= 1, "expected at least one playlist item"
    for item in items:
        assert "url" in item, f"item missing 'url' field: {item}"
        assert item["url"].startswith("/uploads/"), (
            f"expected /uploads/ prefix, got {item['url']!r}"
        )
