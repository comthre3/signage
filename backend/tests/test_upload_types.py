"""POST /media/upload accepts only what the player can render.

/uploads is served from the API origin, which also hosts the OAuth consent
flow and its session cookie. Anything a browser would execute there -- an
.html page, a script, an SVG with <script> -- is stored XSS, so the upload
must refuse it and the static mount must neutralise what it does serve.
"""
import io

import pytest
from fastapi.testclient import TestClient

import main
from db import query_one


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
       b'<script>alert(document.cookie)</script></svg>')


@pytest.fixture(autouse=True)
def _no_transcode(monkeypatch):
    # Video uploads queue an ffmpeg transcode; these bytes are not a video.
    monkeypatch.setattr(main, "transcode_video", lambda *a, **k: None)


def _upload(client: TestClient, org: dict, name: str, body: bytes, content_type: str):
    return client.post(
        "/media/upload",
        headers={"Authorization": f"Bearer {org['token']}"},
        files={"file": (name, io.BytesIO(body), content_type)},
    )


def _media_count(org: dict) -> int:
    row = query_one("SELECT COUNT(*) AS n FROM media WHERE organization_id = ?",
                    (org["org"]["id"],))
    return row["n"]


@pytest.mark.parametrize("name,content_type", [
    ("evil.html", "text/html"),
    ("evil.html", "image/png"),           # lying Content-Type, scripty extension
    ("evil.js", "application/javascript"),
    ("evil.xhtml", "application/xhtml+xml"),
    ("evil.png", "text/html"),            # image extension, non-media claim
    ("doc.pdf", "video/mp4"),             # both media, but not the same class
    ("clip.mp4", "image/png"),
    ("noext", "image/png"),
])
def test_upload_rejects_unsupported_or_mismatched_types(
    client: TestClient, signed_up_org: dict, name: str, content_type: str,
) -> None:
    before = _media_count(signed_up_org)
    r = _upload(client, signed_up_org, name, b"<script>alert(1)</script>", content_type)
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["code"] == "media.unsupported_type"
    assert _media_count(signed_up_org) == before, "a rejected upload left a media row"


@pytest.mark.parametrize("name,content_type", [
    ("a.png", "image/png"),
    ("a.jpeg", "image/jpeg"),
    ("a.webp", "image/webp"),
    ("a.svg", "image/svg+xml"),
    ("a.mp4", "video/mp4"),
    ("a.mov", "video/quicktime"),
    ("a.pdf", "application/pdf"),
])
def test_upload_accepts_images_videos_and_pdfs(
    client: TestClient, signed_up_org: dict, name: str, content_type: str,
) -> None:
    r = _upload(client, signed_up_org, name, PNG, content_type)
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"] == content_type


def test_upload_extension_is_matched_case_insensitively_and_stored_lowercase(
    client: TestClient, signed_up_org: dict,
) -> None:
    r = _upload(client, signed_up_org, "PHOTO.PNG", PNG, "image/png")
    assert r.status_code == 200, r.text
    assert r.json()["filename"].endswith(".png")


def test_upload_without_a_content_type_claim_takes_it_from_the_extension(
    client: TestClient, signed_up_org: dict,
) -> None:
    """curl -F sends application/octet-stream for .mp4; that is no claim at all.

    Storing octet-stream would also leave the player showing "Unsupported
    media", so the row gets the extension's own type instead.
    """
    r = _upload(client, signed_up_org, "clip.mp4", PNG, "application/octet-stream")
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"] == "video/mp4"


def test_served_svg_cannot_run_script(client: TestClient, signed_up_org: dict) -> None:
    r = _upload(client, signed_up_org, "logo.svg", SVG, "image/svg+xml")
    assert r.status_code == 200, r.text
    served = client.get(r.json()["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    csp = served.headers["content-security-policy"]
    assert "sandbox" in csp
    assert "default-src 'none'" in csp
    assert "script-src" not in csp


def test_served_pdf_is_not_sandboxed(client: TestClient, signed_up_org: dict) -> None:
    """The player shows PDFs in an iframe; a sandboxed PDF viewer renders nothing."""
    r = _upload(client, signed_up_org, "menu.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
    assert r.status_code == 200, r.text
    served = client.get(r.json()["url"])
    assert served.status_code == 200
    assert "sandbox" not in served.headers.get("content-security-policy", "")


def test_served_webp_has_an_image_content_type(client: TestClient, signed_up_org: dict) -> None:
    """The image's mimetypes table has no .webp; served as text/plain behind
    nosniff, a cross-origin <img> on the player is blocked outright."""
    r = _upload(client, signed_up_org, "a.webp", PNG, "image/webp")
    assert r.status_code == 200, r.text
    served = client.get(r.json()["url"])
    assert served.headers["content-type"].startswith("image/webp")


def test_served_pdf_can_be_framed_by_our_own_apps_only(client: TestClient, signed_up_org: dict,
                                                       monkeypatch) -> None:
    """X-Frame-Options: DENY on every response blocked the player's PDF iframe
    outright. Framing is limited to the dashboard and the player instead."""
    monkeypatch.setattr(main, "_UPLOAD_FRAME_ANCESTORS",
                        "'self' https://app.example https://play.example")
    r = _upload(client, signed_up_org, "menu.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
    served = client.get(r.json()["url"])
    assert "x-frame-options" not in served.headers
    assert ("frame-ancestors 'self' https://app.example https://play.example"
            in served.headers["content-security-policy"])
    assert client.get("/health").headers["x-frame-options"] == "DENY"


def test_uploads_are_readable_cross_origin(client: TestClient, signed_up_org: dict) -> None:
    """The player's service worker fetches uploads with CORS to cache them;
    they are public files, so any origin may read them (without credentials)."""
    r = _upload(client, signed_up_org, "a.png", PNG, "image/png")
    served = client.get(r.json()["url"], headers={"Origin": "https://tv.example"})
    assert served.headers["access-control-allow-origin"] in ("*", "https://tv.example")


@pytest.mark.parametrize("name,body,ctype,sandboxed", [
    ("logo.svg", SVG, "image/svg+xml", True),
    ("menu.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf", False),
])
def test_a_304_keeps_the_same_policy(client: TestClient, signed_up_org: dict,
                                     name, body, ctype, sandboxed) -> None:
    """A 304 carries no Content-Type; deciding the policy from it sandboxed
    every revalidated PDF (and the browser stores the 304's headers)."""
    r = _upload(client, signed_up_org, name, body, ctype)
    first = client.get(r.json()["url"])
    again = client.get(r.json()["url"], headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert ("sandbox" in again.headers.get("content-security-policy", "")) is sandboxed


@pytest.mark.parametrize("name,ctype", [("clip.3gp", "video/3gpp"), ("clip.wmv", "video/x-ms-wmv"),
                                        ("clip.mpg", "video/mpeg"), ("clip.ts", "video/mp2t")])
def test_videos_ffmpeg_can_transcode_are_accepted(client: TestClient, signed_up_org: dict, name, ctype) -> None:
    """Phones and older cameras produce these; the transcode makes them playable."""
    r = _upload(client, signed_up_org, name, b"\x00\x00\x00\x18ftyp", ctype)
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"] == ctype
