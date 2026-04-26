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
