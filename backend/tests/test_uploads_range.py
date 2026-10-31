"""Byte-range requests on /uploads.

Video elements seek with Range requests, and iOS Safari will not play video at
all from a server that cannot answer them. Starlette 0.37's StaticFiles ignores
Range and always sends the whole file with a 200; a wall cell seeking to the
shared timeline position of an uncached video jumped back to the start.
"""
import os
import uuid

import pytest

import main

BODY = bytes(range(256)) * 4  # 1024 distinct-ish bytes


@pytest.fixture
def upload():
    name = f"range-{uuid.uuid4().hex}.mp4"
    path = os.path.join(main.UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(BODY)
    yield f"/uploads/{name}"
    os.remove(path)


def test_full_file_advertises_ranges(client, upload):
    r = client.get(upload)
    assert r.status_code == 200
    assert r.content == BODY
    assert r.headers.get("accept-ranges") == "bytes"


@pytest.mark.parametrize("header,start,end", [
    ("bytes=2-5", 2, 5),
    ("bytes=1000-", 1000, 1023),
    ("bytes=-3", 1021, 1023),
    ("bytes=10-99999", 10, 1023),
])
def test_range_is_answered_with_206(client, upload, header, start, end):
    r = client.get(upload, headers={"Range": header})
    assert r.status_code == 206, r.text
    assert r.content == BODY[start:end + 1]
    assert r.headers["content-range"] == f"bytes {start}-{end}/{len(BODY)}"
    assert int(r.headers["content-length"]) == end - start + 1
    assert r.headers["content-type"].startswith("video/mp4")


def test_unsatisfiable_range_is_416(client, upload):
    r = client.get(upload, headers={"Range": "bytes=5000-6000"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(BODY)}"


def test_malformed_or_multi_range_falls_back_to_the_whole_file(client, upload):
    for header in ("bytes=abc", "items=0-5", "bytes=0-1,4-5"):
        r = client.get(upload, headers={"Range": header})
        assert r.status_code == 200, header
        assert r.content == BODY


def test_traversal_is_still_refused(client):
    r = client.get("/uploads/%2e%2e/main.py", headers={"Range": "bytes=0-10"})
    assert r.status_code == 404
