"""Tests for the Phase 2.5j social auth (Google + Apple)."""
# ── Fixtures: generate Google + Apple test JWKS + sign test tokens ─────

import os
import json
import time
import base64
import pytest
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives import serialization

from db import query_one, query_all, execute


def test_users_password_hash_is_nullable():
    row = query_one(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("users", "password_hash"),
    )
    assert row is not None
    assert row["is_nullable"] == "YES", row


def test_auth_identities_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("auth_identities", "subject_id"),
    )
    assert row is not None


def test_auth_identities_unique_constraint_on_provider_subject():
    """Two rows with the same (provider, subject_id) must conflict."""
    # Insert a sentinel user to FK to
    row = query_one(
        "SELECT id FROM users LIMIT 1"
    )
    if not row:
        # If the test DB is empty, create one
        execute(
            "INSERT INTO organizations (name, slug, plan, screen_limit, "
            "subscription_status, locale, created_at) "
            "VALUES ('SchemaTest', 'schematest', 'starter', 5, "
            "'trialing', 'en', now())"
        )
        org_id = query_one("SELECT id FROM organizations "
                           "WHERE slug = 'schematest'")["id"]
        execute(
            "INSERT INTO users (organization_id, username, password_hash, "
            "is_admin, role, created_at) "
            "VALUES (?, 'schematest@example.com', NULL, 1, 'admin', now())",
            (org_id,),
        )
        row = query_one(
            "SELECT id FROM users WHERE username = 'schematest@example.com'"
        )
    uid = row["id"]

    # First insert succeeds
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link) VALUES (?, 'google', 'duplicate-sub', "
        "'test@example.com')",
        (uid,),
    )
    # Second insert with same (provider, subject_id) must raise
    import psycopg
    raised = False
    try:
        execute(
            "INSERT INTO auth_identities (user_id, provider, subject_id, "
            "email_at_link) VALUES (?, 'google', 'duplicate-sub', "
            "'other@example.com')",
            (uid,),
        )
    except psycopg.errors.UniqueViolation:
        raised = True
    except Exception as exc:
        # Some db.py wrappers re-raise as a different type
        raised = "duplicate" in str(exc).lower() or "unique" in str(exc).lower()
    assert raised, "Expected unique violation on (provider, subject_id)"

    # Cleanup
    execute(
        "DELETE FROM auth_identities WHERE subject_id = 'duplicate-sub'"
    )


@pytest.fixture(scope="session")
def google_test_keypair():
    """Generate an RSA keypair for signing Google-like test ID tokens."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()

    def _b64url_uint(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwks = {"keys": [{
        "kty": "RSA",
        "kid": "google-test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(pub_numbers.n),
        "e": _b64url_uint(pub_numbers.e),
    }]}
    return {"priv_pem": priv_pem, "jwks": jwks, "kid": "google-test-kid"}


@pytest.fixture(scope="session")
def apple_test_keypair(tmp_path_factory):
    """Generate an EC P-256 keypair for Apple signing + write .p8 to disk."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p8_path = tmp_path_factory.mktemp("apple") / "key.p8"
    p8_path.write_bytes(priv_pem)
    pub = priv.public_key().public_numbers()

    def _b64url_uint(n: int, size: int) -> str:
        b = n.to_bytes(size, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwks = {"keys": [{
        "kty": "EC",
        "kid": "apple-test-kid",
        "use": "sig",
        "alg": "ES256",
        "crv": "P-256",
        "x": _b64url_uint(pub.x, 32),
        "y": _b64url_uint(pub.y, 32),
    }]}
    return {
        "priv_pem": priv_pem,
        "p8_path": str(p8_path),
        "jwks": jwks,
        "kid": "apple-test-kid",
    }


@pytest.fixture
def google_env(monkeypatch, google_test_keypair):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI",
                       "https://api.khanshoof.com/auth/google/callback")
    monkeypatch.setenv("APP_URL", "https://app.khanshoof.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-fixed")
    yield google_test_keypair


@pytest.fixture
def apple_env(monkeypatch, apple_test_keypair):
    monkeypatch.setenv("APPLE_CLIENT_ID", "com.test.khanshoof")
    monkeypatch.setenv("APPLE_TEAM_ID", "TESTTEAMID")
    monkeypatch.setenv("APPLE_KEY_ID", "apple-test-kid")
    monkeypatch.setenv("APPLE_PRIVATE_KEY_PATH",
                       apple_test_keypair["p8_path"])
    monkeypatch.setenv("APPLE_REDIRECT_URI",
                       "https://api.khanshoof.com/auth/apple/callback")
    monkeypatch.setenv("APP_URL", "https://app.khanshoof.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-fixed")
    yield apple_test_keypair


def _sign_google_id_token(google_test_keypair, *, sub: str, email: str,
                          email_verified: bool = True, name: str | None = None,
                          audience: str = "test-google-client-id",
                          issuer: str = "https://accounts.google.com",
                          exp_delta: int = 600) -> str:
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_delta,
    }
    if name:
        payload["name"] = name
    return jwt.encode(payload, google_test_keypair["priv_pem"],
                      algorithm="RS256",
                      headers={"kid": google_test_keypair["kid"]})


def _sign_apple_id_token(apple_test_keypair, *, sub: str, email: str,
                         audience: str = "com.test.khanshoof",
                         issuer: str = "https://appleid.apple.com",
                         exp_delta: int = 600) -> str:
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(payload, apple_test_keypair["priv_pem"],
                      algorithm="ES256",
                      headers={"kid": apple_test_keypair["kid"]})


# ── Helper tests ──────────────────────────────────────────────────────


def test_sign_and_verify_state_token():
    """Signed state round-trips with intent, return_to, nonce, exp."""
    os.environ.setdefault("SECRET_KEY", "test-secret-key-fixed")
    from social_auth import _sign_state, _verify_state
    state = _sign_state(provider="google", intent="signup",
                        return_to="/playlists", ttl_seconds=300)
    payload = _verify_state(state)
    assert payload["provider"] == "google"
    assert payload["intent"] == "signup"
    assert payload["return_to"] == "/playlists"
    assert "nonce" in payload
    assert payload["exp"] > int(time.time())


def test_verify_state_rejects_expired():
    """An expired state token raises."""
    os.environ.setdefault("SECRET_KEY", "test-secret-key-fixed")
    from social_auth import _sign_state, _verify_state
    state = _sign_state(provider="google", intent="signin",
                        return_to="/", ttl_seconds=-1)
    with pytest.raises(Exception):
        _verify_state(state)


def test_verify_state_rejects_tampered():
    """Modified state byte fails signature check."""
    os.environ.setdefault("SECRET_KEY", "test-secret-key-fixed")
    from social_auth import _sign_state, _verify_state
    state = _sign_state(provider="google", intent="signin",
                        return_to="/", ttl_seconds=300)
    parts = state.split(".")
    parts[1] = parts[1][:-2] + ("A" if parts[1][-1] != "A" else "B") + parts[1][-1:]
    with pytest.raises(Exception):
        _verify_state(".".join(parts))


def test_sign_and_verify_stash_token():
    """Stash token carries subject_id + email + name + provider."""
    os.environ.setdefault("SECRET_KEY", "test-secret-key-fixed")
    from social_auth import _sign_stash, _verify_stash
    token = _sign_stash(provider="google", subject_id="abc123",
                        email="foo@example.com", name="Foo Bar",
                        ttl_seconds=600)
    payload = _verify_stash(token)
    assert payload["provider"] == "google"
    assert payload["subject_id"] == "abc123"
    assert payload["email"] == "foo@example.com"
    assert payload["name"] == "Foo Bar"


def test_apple_client_secret_jwt(apple_env):
    """Apple client_secret JWT has the right claims + ES256 signature."""
    from social_auth import _apple_client_secret_jwt, _clear_jwks_cache
    _clear_jwks_cache()
    secret = _apple_client_secret_jwt()
    # Decode without verification to check claims
    payload = jwt.decode(secret, options={"verify_signature": False})
    assert payload["iss"] == "TESTTEAMID"
    assert payload["sub"] == "com.test.khanshoof"
    assert payload["aud"] == "https://appleid.apple.com"
    assert payload["exp"] > payload["iat"]
    header = jwt.get_unverified_header(secret)
    assert header["kid"] == "apple-test-kid"
    assert header["alg"] == "ES256"


def test_verify_google_id_token_happy_path(google_env, respx_mock):
    """Valid Google ID token verifies and returns the payload."""
    import respx
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    id_token = _sign_google_id_token(google_env, sub="g-123",
                                     email="alice@example.com")
    from social_auth import verify_google_id_token, _clear_jwks_cache
    _clear_jwks_cache()
    payload = verify_google_id_token(id_token, "test-google-client-id")
    assert payload["sub"] == "g-123"
    assert payload["email"] == "alice@example.com"
    assert payload["email_verified"] is True


def test_verify_google_id_token_rejects_unverified_email(google_env, respx_mock):
    """email_verified=False is rejected with 400 email_not_verified."""
    import respx
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    id_token = _sign_google_id_token(google_env, sub="g-456",
                                     email="bob@example.com",
                                     email_verified=False)
    from social_auth import verify_google_id_token, _clear_jwks_cache
    from fastapi import HTTPException
    _clear_jwks_cache()
    with pytest.raises(HTTPException) as exc_info:
        verify_google_id_token(id_token, "test-google-client-id")
    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "email_not_verified"
