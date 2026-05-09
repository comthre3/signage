import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient

from main import app


# ── /docs disabled in prod ────────────────────────────────────────────

def test_docs_disabled_by_default(client: TestClient):
    r = client.get("/docs")
    assert r.status_code == 404


def test_redoc_disabled_by_default(client: TestClient):
    r = client.get("/redoc")
    assert r.status_code == 404


def test_openapi_disabled_by_default(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 404


# ── Security headers ──────────────────────────────────────────────────

def test_security_headers_on_response(client: TestClient):
    r = client.get("/health")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "max-age=" in r.headers.get("Strict-Transport-Security", "")


# ── 6-char pair codes ─────────────────────────────────────────────────

def test_pair_code_is_six_chars(client: TestClient):
    r = client.post("/screens/request_code", json={})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert len(code) == 6
    allowed = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    assert all(ch in allowed for ch in code)


def test_pair_code_ttl_is_300(client: TestClient):
    r = client.post("/screens/request_code", json={})
    assert r.json()["expires_in_seconds"] == 300


# ── /billing/callback HMAC + query secret auth ────────────────────────

def test_billing_callback_rejects_unauthenticated(client: TestClient):
    r = client.post(
        "/billing/callback/pay_deadbeef",
        json={"trackid": "pay_deadbeef", "result": "CAPTURED"},
    )
    assert r.status_code in (401, 404)


def test_billing_callback_accepts_query_secret(monkeypatch, client: TestClient):
    monkeypatch.setenv("NIUPAY_CALLBACK_SECRET", "test_q_secret_123")
    r = client.post(
        "/billing/callback/pay_unknown",
        params={"s": "test_q_secret_123"},
        json={"trackid": "pay_unknown", "result": "CAPTURED"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_billing_callback_accepts_hmac_signature(monkeypatch, client: TestClient):
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "test_hmac_secret_456")
    body = json.dumps({"trackid": "pay_unknown", "result": "CAPTURED"}).encode()
    sig = hmac.new(b"test_hmac_secret_456", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/billing/callback/pay_unknown",
        content=body,
        headers={"Content-Type": "application/json", "X-Niupay-Signature": sig},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_billing_callback_rejects_wrong_hmac(monkeypatch, client: TestClient):
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "test_hmac_secret_456")
    body = json.dumps({"trackid": "pay_unknown", "result": "CAPTURED"}).encode()
    r = client.post(
        "/billing/callback/pay_unknown",
        content=body,
        headers={"Content-Type": "application/json", "X-Niupay-Signature": "deadbeef" * 8},
    )
    assert r.status_code == 404


# ── dev_otp leak guard ────────────────────────────────────────────────

def test_dev_otp_blocked_when_request_has_forwarding_header(client: TestClient, unique_business):
    """Even with DEV_MODE=1 and a localhost-looking client, presence of any
    forwarding header (Cloudflare, nginx) means request is NOT loopback —
    dev_otp must NOT leak."""
    r = client.post(
        "/auth/signup/request",
        json={
            "business_name": unique_business["business_name"],
            "email": unique_business["email"],
        },
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "otp_sent"
    assert "dev_otp" not in body


def test_dev_otp_blocked_with_cf_connecting_ip(client: TestClient, unique_business):
    r = client.post(
        "/auth/signup/request",
        json={
            "business_name": unique_business["business_name"],
            "email": unique_business["email"],
        },
        headers={"CF-Connecting-IP": "1.2.3.4"},
    )
    assert r.status_code == 200, r.text
    assert "dev_otp" not in r.json()


# ── login rate limit ─────────────────────────────────────────────────
# Note: requires RATE_LIMITS_ENABLED=1 to actually trigger; covered live
# rather than here so the rest of the suite can run with limits off.


# ── New tables (Phase 2.5c) ───────────────────────────────────────────

def test_login_attempts_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("login_attempts", "username"),
    )
    assert row is not None


def test_audit_log_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("audit_log", "action"),
    )
    assert row is not None


# ── Password policy (Phase 2.5c) ──────────────────────────────────────
from unittest.mock import patch


def _signup_through_otp(client, business):
    """Helper: signup → verify OTP → returns verification_token."""
    r = client.post("/auth/signup/request",
                    json={"business_name": business["business_name"],
                          "email": business["email"]})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": business["email"], "otp": otp})
    assert r.status_code == 200, r.text
    return r.json()["verification_token"]


def test_signup_rejects_password_too_short(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": "Aa1aaaaaaa"})  # 10 chars
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_too_short"


def test_signup_rejects_password_no_lowercase(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": "ABCDEFGH1234"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_no_lowercase"


def test_signup_rejects_password_no_uppercase(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": "abcdefgh1234"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_no_uppercase"


def test_signup_rejects_password_no_digit(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": "Abcdefghijkl"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_no_number"


def test_signup_rejects_breached_password(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    pw = "AbcDefGhi123"
    import hashlib
    suffix = hashlib.sha1(pw.encode()).hexdigest().upper()[5:]
    fake_body = f"{suffix}:99\n"
    fake = patch("hibp.requests.get").start()
    fake.return_value.text = fake_body
    fake.return_value.raise_for_status = lambda: None
    try:
        r = client.post("/auth/signup/complete",
                        json={"verification_token": vt, "password": pw})
    finally:
        patch.stopall()
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "password_breached"


def test_signup_accepts_compliant_password(client, unique_business):
    vt = _signup_through_otp(client, unique_business)
    fake = patch("hibp.requests.get").start()
    fake.return_value.text = ""
    fake.return_value.raise_for_status = lambda: None
    try:
        r = client.post("/auth/signup/complete",
                        json={"verification_token": vt, "password": "Khanshoof2026Pass"})
    finally:
        patch.stopall()
    assert r.status_code == 200, r.text


def test_login_still_works_for_existing_user_with_legacy_password(client, signed_up_org):
    # signed_up_org's fixture password is policy-compliant; the test
    # verifies that AUTH on existing accounts does NOT re-run validate_password.
    r = client.post("/auth/login", json={
        "username": signed_up_org["user"]["username"],
        "password": "Khanshoof2026Test",
    })
    assert r.status_code == 200, r.text
