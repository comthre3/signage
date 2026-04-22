import re

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
