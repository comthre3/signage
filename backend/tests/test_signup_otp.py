import uuid

import pytest
from fastapi.testclient import TestClient

from main import generate_otp, hash_otp, verify_otp, app


@pytest.fixture
def otp_client() -> TestClient:
    return TestClient(app)


def _fresh_email() -> str:
    return f"otp-{uuid.uuid4().hex[:8]}@example.com"


def test_generate_otp_is_six_digits_numeric():
    otp = generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()


def test_otp_hash_roundtrip():
    otp = "123456"
    stored = hash_otp(otp)
    assert stored != otp
    assert verify_otp(otp, stored) is True
    assert verify_otp("000000", stored) is False


def test_verify_otp_none_stored_returns_false():
    assert verify_otp("123456", None) is False


def test_signup_request_happy_path_returns_dev_otp(otp_client):
    email = _fresh_email()
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "otp_sent"
    assert "dev_otp" in data
    assert len(data["dev_otp"]) == 6 and data["dev_otp"].isdigit()


def test_signup_request_rejects_invalid_email(otp_client):
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": "notanemail"},
    )
    assert r.status_code == 400


def test_signup_request_rejects_already_registered_email(otp_client):
    email = _fresh_email()
    from db import execute
    from main import hash_password, utc_now_iso
    org_id = execute(
        """
        INSERT INTO organizations (name, slug, plan, screen_limit, subscription_status, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (f"Seed {email}", f"seed-{uuid.uuid4().hex[:6]}", "starter", 3, "trialing", "en", utc_now_iso()),
    )
    execute(
        """
        INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (org_id, email, hash_password("seeded1x"), 1, "admin", utc_now_iso()),
    )
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "email_taken"


def test_signup_request_cooldown_blocks_rapid_resend(otp_client):
    email = _fresh_email()
    r1 = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r1.status_code == 200
    r2 = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "otp_cooldown"


def _request_otp(client, email: str) -> str:
    r = client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 200, r.text
    return r.json()["dev_otp"]


def test_signup_verify_happy_path_returns_token(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["verification_token"]) >= 32
    assert data["business_name"] == "Kebab Corner"


def test_signup_verify_wrong_otp_increments_attempts(otp_client):
    email = _fresh_email()
    _request_otp(otp_client, email)
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "otp_incorrect"


def test_signup_verify_locks_after_max_attempts(otp_client):
    email = _fresh_email()
    _request_otp(otp_client, email)
    for _ in range(5):
        otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "otp_attempts_exceeded"


def test_signup_verify_unknown_email_fails(otp_client):
    r = otp_client.post(
        "/auth/signup/verify",
        json={"email": "nobody@example.com", "otp": "123456"},
    )
    assert r.status_code == 400


def _verify_otp(client, email: str, otp: str) -> str:
    r = client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    return r.json()["verification_token"]


def test_signup_complete_happy_path_returns_session(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"]
    assert data["user"]["username"] == email
    assert data["user"]["role"] == "admin"
    assert data["organization"]["plan"] == "starter"
    assert data["organization"]["subscription_status"] == "trialing"


def test_signup_complete_rejects_invalid_token(otp_client):
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": "deadbeef" * 4, "password": "testpass1"},
    )
    assert r.status_code == 400


def test_signup_complete_rejects_reused_token(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r1 = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r1.status_code == 200
    r2 = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r2.status_code == 400


def test_signup_complete_enforces_password_policy(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "short"},
    )
    assert r.status_code == 400
