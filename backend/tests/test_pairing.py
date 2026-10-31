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
    assert data["expires_in_seconds"] == 300
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
        json={"verification_token": vt, "password": "Testpass2026x"},
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


def test_claim_rejects_rebind_to_different_screen_same_org(client):
    ctx = _login_as_signed_up_org(client)
    screen_b = client.post(
        "/screens", json={"name": "Second Display"}, headers=ctx["auth"]
    ).json()
    code = client.post("/screens/request_code", json={}).json()["code"]
    r1 = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": screen_b["id"]},
        headers=ctx["auth"],
    )
    assert r2.status_code == 400


def test_full_flow_request_claim_poll(client):
    # 1. Player requests a code (unauthenticated)
    r = client.post("/screens/request_code", json={})
    assert r.status_code == 200
    code = r.json()["code"]

    # 2. Player polls — still pending
    r = client.get(f"/screens/poll/{code}")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

    # 3. Admin signs up, logs in, creates a display, and claims the code
    ctx = _login_as_signed_up_org(client)
    r = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 200

    # 4. Player's next poll returns the paired screen's token
    r = client.get(f"/screens/poll/{code}")
    body = r.json()
    assert body["status"] == "paired"
    token = body["screen_token"]
    assert isinstance(token, str) and token

    # 5. Player can now fetch content with that token
    r = client.get(f"/screens/{token}/content")
    assert r.status_code == 200
    assert "items" in r.json()


# ── Legacy /screens/pair: the screen card's ?code= link ────────────────
# A hit returns the screen's token (full content access) to an anonymous
# caller, and the 6-digit code never expires, so guessing must be throttled
# and the codes themselves unpredictable.

def test_legacy_pair_endpoint_is_rate_limited():
    # Tests run with RATE_LIMITS_ENABLED=0, so assert the registered limit.
    import main
    limits = main.limiter._route_limits.get("main.pair_screen", [])
    assert [str(lim.limit) for lim in limits] == ["10 per 1 minute"]


def test_legacy_pair_code_shape():
    from main import generate_pair_code
    for _ in range(50):
        code = generate_pair_code()
        assert re.fullmatch(r"[1-9]\d{5}", code), code


def test_legacy_pair_code_does_not_come_from_the_seedable_prng():
    """random is a Mersenne Twister: its output is recoverable from earlier
    outputs and repeats under a fixed seed. Codes must not follow it."""
    import random
    from main import generate_pair_code
    random.seed(1234)
    first = [generate_pair_code() for _ in range(5)]
    random.seed(1234)
    second = [generate_pair_code() for _ in range(5)]
    assert first != second


def test_legacy_pair_still_returns_the_screen_token(client):
    ctx = _login_as_signed_up_org(client)
    screen = ctx["default_screen"]
    r = client.post("/screens/pair", json={"pair_code": screen["pair_code"]})
    assert r.status_code == 200, r.text
    assert r.json()["token"] == screen["token"]
    assert client.post("/screens/pair", json={"pair_code": "000000"}).status_code == 404


# ── /screens/poll after a claim ────────────────────────────────────────
# Polling is anonymous: whoever holds a claimed code gets the screen's token.
# The TV polls every few seconds, so it only needs that answer briefly; a code
# read off the screen or a photo later must not still unlock the screen.

def _claimed_code(client) -> tuple[str, dict]:
    ctx = _login_as_signed_up_org(client)
    code = client.post("/screens/request_code", json={}).json()["code"]
    r = client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 200, r.text
    return code, ctx


def _set_claimed_at(code: str, value) -> None:
    from db import execute
    execute("UPDATE pairing_codes SET claimed_at = ? WHERE code = ?", (value, code))


def test_poll_still_returns_token_just_inside_the_window(client):
    from main import PAIR_CODE_TTL_SECONDS
    code, ctx = _claimed_code(client)
    recent = datetime.now(timezone.utc) - timedelta(seconds=PAIR_CODE_TTL_SECONDS - 30)
    _set_claimed_at(code, recent.isoformat())
    body = client.get(f"/screens/poll/{code}").json()
    assert body["status"] == "paired"
    assert body["screen_token"] == ctx["default_screen"]["token"]


def test_poll_stops_returning_token_once_the_window_has_passed(client):
    from main import PAIR_CODE_TTL_SECONDS
    code, _ctx = _claimed_code(client)
    old = datetime.now(timezone.utc) - timedelta(seconds=PAIR_CODE_TTL_SECONDS + 1)
    _set_claimed_at(code, old.isoformat())
    r = client.get(f"/screens/poll/{code}")
    assert r.status_code == 200
    assert r.json() == {"status": "expired"}


def test_poll_of_a_paired_code_without_claimed_at_is_expired(client):
    code, _ctx = _claimed_code(client)
    _set_claimed_at(code, None)
    assert client.get(f"/screens/poll/{code}").json() == {"status": "expired"}
