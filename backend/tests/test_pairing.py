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


def _login_as_signed_up_org(client) -> dict:
    """Use the OTP signup flow to get an admin session + org + its default screen.

    Returns dict with token, org, user, default_screen, auth (header dict).
    """
    import uuid

    email = f"pair-{uuid.uuid4().hex[:8]}@example.com"
    business_name = f"Pair Biz {uuid.uuid4().hex[:6]}"
    r = client.post(
        "/auth/signup/request",
        json={"business_name": business_name, "email": email},
    )
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    session = r.json()
    auth = {"Authorization": f"Bearer {session['token']}"}
    r = client.post("/screens", json={"name": "Default Display"}, headers=auth)
    assert r.status_code == 200, r.text
    return {
        "token": session["token"],
        "org": session["organization"],
        "user": session["user"],
        "default_screen": r.json(),
        "auth": auth,
    }


def test_claim_requires_auth(client):
    r = client.post("/screens/request_code", json={})
    code = r.json()["code"]
    r2 = client.post("/screens/claim", json={"code": code, "screen_id": 1})
    assert r2.status_code == 401


def test_claim_happy_path_marks_paired_and_poll_returns_token(client):
    ctx = _login_as_signed_up_org(client)
    code = client.post("/screens/request_code", json={}).json()["code"]
    r = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 200, r.text
    claim_data = r.json()
    assert claim_data["screen_id"] == ctx["default_screen"]["id"]
    assert claim_data["screen_name"] == "Default Display"

    r2 = client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "paired"
    assert body["screen_id"] == ctx["default_screen"]["id"]
    assert body["screen_name"] == "Default Display"
    assert body["screen_token"] == ctx["default_screen"]["token"]


def test_claim_rejects_screen_from_other_org(client):
    ctx_a = _login_as_signed_up_org(client)
    ctx_b = _login_as_signed_up_org(client)
    code = client.post("/screens/request_code", json={}).json()["code"]
    r = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx_a["default_screen"]["id"]},
        headers=ctx_b["auth"],
    )
    assert r.status_code == 404


def test_claim_rejects_expired_code(client):
    from db import execute
    ctx = _login_as_signed_up_org(client)
    code = client.post("/screens/request_code", json={}).json()["code"]
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    r = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower()


def test_claim_is_idempotent_same_caller_same_screen(client):
    ctx = _login_as_signed_up_org(client)
    code = client.post("/screens/request_code", json={}).json()["code"]
    r1 = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r2.status_code == 200
    assert r2.json()["screen_id"] == ctx["default_screen"]["id"]
