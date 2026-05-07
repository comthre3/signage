import io

from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_playlist(client: TestClient, headers: dict) -> int:
    r = client.post("/playlists", json={"name": "P1"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload_image(client: TestClient, headers: dict, name: str = "img.png") -> int:
    # Minimal 1x1 PNG so the upload pipeline accepts it.
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


def test_add_playlist_item_without_duration_uses_image_default(
    client: TestClient, signed_up_org: dict
) -> None:
    headers = _auth_headers(signed_up_org["token"])
    playlist_id = _create_playlist(client, headers)
    media_id = _upload_image(client, headers)

    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id},  # NO duration_seconds
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 10  # image default


def test_add_playlist_item_with_explicit_duration_is_respected(
    client: TestClient, signed_up_org: dict
) -> None:
    headers = _auth_headers(signed_up_org["token"])
    playlist_id = _create_playlist(client, headers)
    media_id = _upload_image(client, headers)

    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id, "duration_seconds": 42},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 42
