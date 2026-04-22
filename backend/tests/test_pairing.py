import re
from datetime import datetime, timedelta, timezone

from main import (
    PAIR_CODE_CHARSET,
    PAIR_CODE_LENGTH,
    generate_pair_code_v2,
)


VALID_CODE = re.compile(f"^[{re.escape(PAIR_CODE_CHARSET)}]{{{PAIR_CODE_LENGTH}}}$")


def test_pair_code_charset_excludes_confusables():
    for ch in "O0I1L":
        assert ch not in PAIR_CODE_CHARSET


def test_generate_pair_code_v2_shape():
    code = generate_pair_code_v2()
    assert VALID_CODE.match(code), code


def test_request_code_returns_code_and_device_id(client):
    r = client.post("/screens/request_code", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert VALID_CODE.match(data["code"]), data
    assert len(data["device_id"]) == 32
    assert data["expires_in_seconds"] == 600
    assert "expires_at" in data


def test_request_code_each_call_is_unique(client):
    r1 = client.post("/screens/request_code", json={}).json()
    r2 = client.post("/screens/request_code", json={}).json()
    assert r1["code"] != r2["code"]
    assert r1["device_id"] != r2["device_id"]


def test_request_code_accepts_empty_body(client):
    r = client.post("/screens/request_code")
    assert r.status_code == 200


def test_poll_returns_pending_for_fresh_code(client):
    r = client.post("/screens/request_code", json={})
    code = r.json()["code"]
    r2 = client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "pending"


def test_poll_unknown_code_returns_404(client):
    r = client.get("/screens/poll/ZZZZZ")
    assert r.status_code == 404


def test_poll_expired_code_returns_expired_status(client):
    from db import execute
    r = client.post("/screens/request_code", json={})
    code = r.json()["code"]
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    r2 = client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "expired"


def test_poll_malformed_code_returns_404(client):
    r = client.get("/screens/poll/toolong1234")
    assert r.status_code == 404
