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
    """Two keys, as with the real Apple: our EC P-256 .p8 (written to disk)
    signs the client_secret JWT, and Apple's own RSA key -- published in
    its JWKS, like https://appleid.apple.com/auth/keys -- signs ID tokens
    with RS256."""
    p8 = ec.generate_private_key(ec.SECP256R1())
    p8_path = tmp_path_factory.mktemp("apple") / "key.p8"
    p8_path.write_bytes(p8.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = priv.public_key().public_numbers()

    def _b64url_uint(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwks = {"keys": [{
        "kty": "RSA",
        "kid": "apple-idtoken-test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(pub.n),
        "e": _b64url_uint(pub.e),
    }]}
    return {
        "priv_pem": priv_pem,
        "p8_path": str(p8_path),
        "jwks": jwks,
        "kid": "apple-idtoken-test-kid",
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
                      algorithm="RS256",
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
    import asyncio
    import respx
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    id_token = _sign_google_id_token(google_env, sub="g-123",
                                     email="alice@example.com")
    from social_auth import verify_google_id_token, _clear_jwks_cache
    _clear_jwks_cache()
    payload = asyncio.run(verify_google_id_token(id_token, "test-google-client-id"))
    assert payload["sub"] == "g-123"
    assert payload["email"] == "alice@example.com"
    assert payload["email_verified"] is True


def test_verify_google_id_token_rejects_unverified_email(google_env, respx_mock):
    """email_verified=False is rejected with 400 email_not_verified."""
    import asyncio
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
        asyncio.run(verify_google_id_token(id_token, "test-google-client-id"))
    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "email_not_verified"


# ── Google flow ───────────────────────────────────────────────────────


def test_google_start_redirects_with_state_cookie(client, google_env):
    r = client.get("/auth/google/start",
                   params={"intent": "signup", "return_to": "/"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "auth_csrf" in r.headers.get("set-cookie", "")
    # Google returns with a top-level GET, which Lax cookies ride along on.
    assert "samesite=lax" in r.headers["set-cookie"].lower()
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=test-google-client-id" in loc
    assert "state=" in loc
    assert "scope=openid" in loc and "email" in loc


def test_google_callback_new_user_returns_stash(client, google_env,
                                                respx_mock):
    """No existing user with this email → stash token in /auth-bounce URL."""
    import respx, uuid
    sub = f"g-newuser-{uuid.uuid4().hex[:8]}"
    email = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
    id_token = _sign_google_id_token(google_env, sub=sub, email=email,
                                     name="New User")

    # Mock Google's JWKS + token endpoints
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    respx_mock.post("https://oauth2.googleapis.com/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token,
                                                    "access_token": "x"})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    # First /start to get the state cookie
    r = client.get("/auth/google/start",
                   params={"intent": "signup", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    # Extract the state from the redirect URL
    import urllib.parse
    parsed = urllib.parse.urlparse(r.headers["location"])
    state = dict(urllib.parse.parse_qsl(parsed.query))["state"]

    # Callback
    r = client.get("/auth/google/callback",
                   params={"code": "any-code", "state": state},
                   cookies={"auth_csrf": cookie},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/auth-bounce" in loc
    assert "complete_signup=" in loc


def test_google_callback_existing_user_auto_links(client, google_env,
                                                  respx_mock):
    """Existing user with matching email → identity created, redirected with token."""
    import respx, uuid

    # Pre-create a user with a password
    sfx = uuid.uuid4().hex[:8]
    email = f"existing-{sfx}@example.com"
    r = client.post("/auth/signup/request",
                    json={"business_name": f"ExBiz {sfx}", "email": email})
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    assert r.status_code == 200

    # Now sign in via Google with the same email
    sub = f"g-existing-{sfx}"
    id_token = _sign_google_id_token(google_env, sub=sub, email=email)

    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    respx_mock.post("https://oauth2.googleapis.com/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    r = client.get("/auth/google/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    import urllib.parse
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]

    r = client.get("/auth/google/callback",
                   params={"code": "any", "state": state},
                   cookies={"auth_csrf": cookie},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/auth-bounce" in loc
    assert "token=" in loc
    # Identity row must exist
    from db import query_one
    identity = query_one(
        "SELECT * FROM auth_identities "
        "WHERE provider = 'google' AND subject_id = ?", (sub,)
    )
    assert identity is not None
    assert identity["email_at_link"] == email


def test_google_callback_existing_identity_logs_in(client, google_env,
                                                   respx_mock):
    """Existing auth_identities row → log in directly, update last_used_at."""
    import respx, uuid
    sfx = uuid.uuid4().hex[:8]

    # Build a user + pre-existing identity
    from db import query_one, execute
    execute(
        "INSERT INTO organizations (name, slug, plan, screen_limit, "
        "subscription_status, locale, created_at) "
        "VALUES (?, ?, 'starter', 5, 'trialing', 'en', now())",
        (f"PreExist {sfx}", f"preexist-{sfx}"),
    )
    org = query_one(
        "SELECT id FROM organizations WHERE slug = ?", (f"preexist-{sfx}",)
    )
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) "
        "VALUES (?, ?, NULL, 1, 'admin', now())",
        (org["id"], f"preexist-{sfx}@example.com"),
    )
    user = query_one(
        "SELECT id FROM users WHERE username = ?",
        (f"preexist-{sfx}@example.com",),
    )
    sub = f"g-preexist-{sfx}"
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link) VALUES (?, 'google', ?, ?)",
        (user["id"], sub, f"preexist-{sfx}@example.com"),
    )

    # Sign in
    id_token = _sign_google_id_token(google_env, sub=sub,
                                     email=f"preexist-{sfx}@example.com")
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    respx_mock.post("https://oauth2.googleapis.com/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    r = client.get("/auth/google/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    import urllib.parse
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    r = client.get("/auth/google/callback",
                   params={"code": "any", "state": state},
                   cookies={"auth_csrf": cookie},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "token=" in r.headers["location"]

    row = query_one(
        "SELECT last_used_at FROM auth_identities "
        "WHERE provider = 'google' AND subject_id = ?", (sub,)
    )
    assert row["last_used_at"] is not None


def test_complete_signup_creates_org_user_identity(client, google_env):
    """POST /auth/google/complete-signup with a valid stash creates everything."""
    import uuid
    from social_auth import _sign_stash
    sfx = uuid.uuid4().hex[:8]
    email = f"completer-{sfx}@example.com"
    sub = f"g-complete-{sfx}"
    stash = _sign_stash(provider="google", subject_id=sub, email=email,
                        name="C Ompleter")

    r = client.post("/auth/google/complete-signup", json={
        "stash_token": stash,
        "business_name": f"CompleterBiz {sfx}",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["username"] == email
    assert body["organization"]["name"] == f"CompleterBiz {sfx}"

    from db import query_one
    identity = query_one(
        "SELECT * FROM auth_identities "
        "WHERE provider = 'google' AND subject_id = ?", (sub,)
    )
    assert identity is not None


def test_complete_signup_returns_trial_fields(client, google_env):
    """complete_signup response must include trial_ends_at + can_write etc."""
    import uuid
    from social_auth import _sign_stash
    sfx = uuid.uuid4().hex[:8]
    stash = _sign_stash(provider="google",
                        subject_id=f"g-trial-{sfx}",
                        email=f"trial-{sfx}@example.com",
                        name="Trial Test")
    r = client.post("/auth/google/complete-signup", json={
        "stash_token": stash,
        "business_name": f"TrialBiz {sfx}",
    })
    assert r.status_code == 200, r.text
    org = r.json()["organization"]
    # Must match /auth/signup/complete shape:
    for key in ("trial_ends_at", "locale", "state", "can_write",
                "days_remaining", "expires_at"):
        assert key in org, f"Missing {key} in response.organization"
    assert org["trial_ends_at"] is not None
    assert org["can_write"] is True  # trial should be active


def test_complete_signup_gets_the_starter_plan_screen_limit(client, google_env):
    """A social signup lands on the same Starter plan as an email signup:
    PLANS["starter"]'s screen limit, not one of its own."""
    import uuid
    from main import PLANS
    from social_auth import _sign_stash
    sfx = uuid.uuid4().hex[:8]
    stash = _sign_stash(provider="google", subject_id=f"g-limit-{sfx}",
                        email=f"limit-{sfx}@example.com", name=None)
    r = client.post("/auth/google/complete-signup", json={
        "stash_token": stash,
        "business_name": f"LimitBiz {sfx}",
    })
    assert r.status_code == 200, r.text
    org = r.json()["organization"]
    assert org["plan"] == "starter"
    assert org["screen_limit"] == PLANS["starter"]["screen_limit"]
    row = query_one("SELECT plan, screen_limit FROM organizations WHERE id = ?",
                    (org["id"],))
    assert row["screen_limit"] == PLANS["starter"]["screen_limit"]


def test_google_callback_user_cancels_redirects_gracefully(client, google_env):
    """Google sends ?error=access_denied when user cancels → 302 to auth-bounce."""
    r = client.get("/auth/google/callback",
                   params={"error": "access_denied"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/auth-bounce" in loc
    assert "error=access_denied" in loc


# ── return_to: a same-origin path, never somewhere else ───────────────


def _google_signin_bounce(client, google_env, respx_mock, return_to: str) -> dict:
    """Run start -> callback for a user with a linked Google identity and
    return the query of the /auth-bounce URL the callback redirects to."""
    import respx, uuid, urllib.parse
    sfx = uuid.uuid4().hex[:8]
    email = f"bounce-{sfx}@example.com"
    sub = f"g-bounce-{sfx}"
    execute(
        "INSERT INTO organizations (name, slug, plan, screen_limit, "
        "subscription_status, locale, created_at) "
        "VALUES (?, ?, 'starter', 3, 'trialing', 'en', now())",
        (f"Bounce {sfx}", f"bounce-{sfx}"),
    )
    org = query_one("SELECT id FROM organizations WHERE slug = ?",
                    (f"bounce-{sfx}",))
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) VALUES (?, ?, NULL, 1, 'admin', now())",
        (org["id"], email),
    )
    user = query_one("SELECT id FROM users WHERE username = ?", (email,))
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link) VALUES (?, 'google', ?, ?)",
        (user["id"], sub, email),
    )
    id_token = _sign_google_id_token(google_env, sub=sub, email=email)
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    respx_mock.post("https://oauth2.googleapis.com/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    r = client.get("/auth/google/start",
                   params={"intent": "signin", "return_to": return_to},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    cookie = r.cookies["auth_csrf"]
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    r = client.get("/auth/google/callback",
                   params={"code": "any", "state": state},
                   cookies={"auth_csrf": cookie},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urllib.parse.urlparse(r.headers["location"])
    assert loc.path == "/auth-bounce"
    return dict(urllib.parse.parse_qsl(loc.query))


_UNSAFE_RETURN_TO = [
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example/",
    "\\\\evil.example/",
    "/\t/evil.example/",     # browsers drop the tab: "//evil.example/"
    "/\n/evil.example/",
    # Dot-segments: a URL parser resolves each of these to the path
    # "//evil.example/", which is protocol-relative wherever it is reused.
    "/..//evil.example/",
    "/%2e%2e//evil.example/",
    "/%2E%2E//evil.example/",
    "/a/../..//evil.example/",
    "/.//evil.example/",
    "javascript:alert(1)",
    "evil.example",
    "",
]

# The dashboard sends the page it was on plus a one-time nonce it checks
# when the bounce comes back, so a query string must survive untouched.
_SAFE_RETURN_TO = [
    "/",
    "/billing",
    "/?sa_nonce=0f1e2d3c4b5a69788796a5b4c3d2e1f0",
    "/billing?tab=plans&sa_nonce=0f1e2d3c4b5a69788796a5b4c3d2e1f0",
]


@pytest.mark.parametrize("raw", _UNSAFE_RETURN_TO)
def test_unsafe_return_to_falls_back_to_root(raw):
    from social_auth import _safe_return_to
    assert _safe_return_to(raw) == "/"


@pytest.mark.parametrize("raw", _SAFE_RETURN_TO)
def test_same_origin_return_to_is_kept(raw):
    from social_auth import _safe_return_to
    assert _safe_return_to(raw) == raw


def test_google_start_does_not_sign_offsite_return_to(client, google_env):
    import urllib.parse
    from social_auth import _verify_state
    r = client.get("/auth/google/start",
                   params={"intent": "signin",
                           "return_to": "https://evil.example/phish"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    assert _verify_state(state)["return_to"] == "/"


def test_bounce_never_carries_offsite_return_to(google_env):
    """Even a state signed before this check existed is not bounced
    off-site: the redirect builder applies it as well."""
    import urllib.parse
    from social_auth import _bounce_with_token
    url = _bounce_with_token("tok", "//evil.example/")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["return_to"] == "/"


def test_google_signin_offsite_return_to_bounces_to_root(client, google_env,
                                                         respx_mock):
    q = _google_signin_bounce(client, google_env, respx_mock,
                              "https://evil.example/phish")
    assert q["token"]
    assert q["return_to"] == "/"


def test_google_signin_keeps_dashboard_nonce_return_to(client, google_env,
                                                       respx_mock):
    rt = "/billing?tab=plans&sa_nonce=0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    q = _google_signin_bounce(client, google_env, respx_mock, rt)
    assert q["token"]
    assert q["return_to"] == rt


# ── Apple flow ────────────────────────────────────────────────────────


def test_apple_start_redirects_with_state_cookie(client, apple_env):
    r = client.get("/auth/apple/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "auth_csrf" in r.headers.get("set-cookie", "")
    loc = r.headers["location"]
    assert loc.startswith("https://appleid.apple.com/auth/authorize?")
    assert "client_id=com.test.khanshoof" in loc
    assert "response_mode=form_post" in loc


def test_apple_state_cookie_is_sent_on_apples_cross_site_post(client, apple_env,
                                                              monkeypatch):
    """Apple returns with a cross-site POST from appleid.apple.com
    (response_mode=form_post). Browsers withhold SameSite=Lax cookies from
    that, so every callback would fail its CSRF check: the Apple state
    cookie is SameSite=None, which browsers only accept with Secure. Apple
    redirect URIs are always https, so Secure holds even when API_BASE_URL
    is a plain-http dev URL."""
    monkeypatch.setenv("API_BASE_URL", "http://localhost:8000")
    r = client.get("/auth/apple/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    set_cookie = r.headers["set-cookie"].lower()
    assert set_cookie.startswith("auth_csrf=")
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie
    assert "httponly" in set_cookie


def test_apple_callback_still_requires_matching_state_cookie(client, apple_env):
    """SameSite=None lets the cookie travel; the check itself is unchanged:
    a callback whose cookie is missing or differs from state is refused."""
    import urllib.parse
    r = client.get("/auth/apple/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    client.cookies.clear()
    form = {"code": "any", "state": state, "id_token": "unused"}
    r = client.post("/auth/apple/callback", data=form, follow_redirects=False)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "invalid_state"
    r = client.post("/auth/apple/callback", data=form,
                    cookies={"auth_csrf": "someone-elses-state"},
                    follow_redirects=False)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "invalid_state"


def test_verify_apple_id_token_accepts_rs256(apple_env, respx_mock):
    """Apple signs ID tokens with RS256 (its JWKS holds RSA keys)."""
    import asyncio
    import respx
    respx_mock.get("https://appleid.apple.com/auth/keys").mock(
        return_value=respx.MockResponse(200, json=apple_env["jwks"])
    )
    id_token = _sign_apple_id_token(apple_env, sub="a-rs256",
                                    email="rs256@privaterelay.appleid.com")
    assert jwt.get_unverified_header(id_token)["alg"] == "RS256"
    from social_auth import verify_apple_id_token, _clear_jwks_cache
    _clear_jwks_cache()
    payload = asyncio.run(verify_apple_id_token(id_token, "com.test.khanshoof"))
    assert payload["sub"] == "a-rs256"


def test_apple_callback_post_form_new_user(client, apple_env, respx_mock):
    """Apple POSTs the callback (not GETs). First sign-in includes `user` JSON."""
    import respx, uuid
    sfx = uuid.uuid4().hex[:8]
    sub = f"a-new-{sfx}"
    email = f"apple-new-{sfx}@privaterelay.appleid.com"
    id_token = _sign_apple_id_token(apple_env, sub=sub, email=email)

    respx_mock.get("https://appleid.apple.com/auth/keys").mock(
        return_value=respx.MockResponse(200, json=apple_env["jwks"])
    )
    respx_mock.post("https://appleid.apple.com/auth/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    r = client.get("/auth/apple/start",
                   params={"intent": "signup", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    import urllib.parse
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]

    # Apple POSTs form-encoded
    user_json = json.dumps({"name": {"firstName": "Apple",
                                     "lastName": "Tester"},
                            "email": email})
    r = client.post("/auth/apple/callback",
                    data={"code": "any-code", "state": state,
                          "id_token": id_token, "user": user_json},
                    cookies={"auth_csrf": cookie},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/auth-bounce" in loc
    assert "complete_signup=" in loc
    assert "Apple+Tester" in loc or "Apple%20Tester" in loc


def test_apple_callback_subsequent_signin_without_user_field(client, apple_env,
                                                              respx_mock):
    """Second sign-in: Apple doesn't send `user`. Still works because we
    look up by subject_id, not by re-reading the name."""
    import respx, uuid
    sfx = uuid.uuid4().hex[:8]
    sub = f"a-second-{sfx}"

    # Pre-create user + identity (simulating an earlier first sign-in)
    from db import query_one, execute
    execute(
        "INSERT INTO organizations (name, slug, plan, screen_limit, "
        "subscription_status, locale, created_at) "
        "VALUES (?, ?, 'starter', 5, 'trialing', 'en', now())",
        (f"AppleSec {sfx}", f"applesec-{sfx}"),
    )
    org = query_one("SELECT id FROM organizations WHERE slug = ?",
                    (f"applesec-{sfx}",))
    email = f"apple-sec-{sfx}@privaterelay.appleid.com"
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) "
        "VALUES (?, ?, NULL, 1, 'admin', now())",
        (org["id"], email),
    )
    user = query_one("SELECT id FROM users WHERE username = ?", (email,))
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link, name_at_link) "
        "VALUES (?, 'apple', ?, ?, 'Apple Tester')",
        (user["id"], sub, email),
    )

    # Apple sign-in without `user` field
    id_token = _sign_apple_id_token(apple_env, sub=sub, email=email)
    respx_mock.get("https://appleid.apple.com/auth/keys").mock(
        return_value=respx.MockResponse(200, json=apple_env["jwks"])
    )
    respx_mock.post("https://appleid.apple.com/auth/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()

    r = client.get("/auth/apple/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    import urllib.parse
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    r = client.post("/auth/apple/callback",
                    data={"code": "any", "state": state,
                          "id_token": id_token},   # no `user`
                    cookies={"auth_csrf": cookie},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "token=" in r.headers["location"]


def test_apple_complete_signup(client, apple_env):
    """POST /auth/apple/complete-signup creates org + user + identity."""
    import uuid
    from social_auth import _sign_stash
    sfx = uuid.uuid4().hex[:8]
    email = f"apple-completer-{sfx}@privaterelay.appleid.com"
    sub = f"a-complete-{sfx}"
    stash = _sign_stash(provider="apple", subject_id=sub, email=email,
                        name="Apple Completer")

    r = client.post("/auth/apple/complete-signup", json={
        "stash_token": stash,
        "business_name": f"AppleCompleterBiz {sfx}",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["username"] == email

    from db import query_one
    identity = query_one(
        "SELECT * FROM auth_identities "
        "WHERE provider = 'apple' AND subject_id = ?", (sub,)
    )
    assert identity is not None


# ── Cross-cutting ──────────────────────────────────────────────────────


def test_provider_not_configured_returns_503(client, monkeypatch):
    """If GOOGLE_CLIENT_ID is missing, /auth/google/start 503s with
    provider_not_configured."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    r = client.get("/auth/google/start",
                   params={"intent": "signup", "return_to": "/"})
    assert r.status_code == 503, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "provider_not_configured"


def test_password_login_still_works_after_social_link(client, google_env,
                                                     respx_mock):
    """Existing password user signs in via Google → identity linked.
    Then they can still log in via /auth/login with their password."""
    import respx, uuid
    sfx = uuid.uuid4().hex[:8]
    email = f"hybrid-{sfx}@example.com"
    password = "Khanshoof2026Test"

    # 1. Sign up via password
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Hybrid {sfx}", "email": email})
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt, "password": password})
    assert r.status_code == 200

    # 2. Sign in via Google with the same email → auto-link
    sub = f"g-hybrid-{sfx}"
    id_token = _sign_google_id_token(google_env, sub=sub, email=email)
    respx_mock.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=respx.MockResponse(200, json=google_env["jwks"])
    )
    respx_mock.post("https://oauth2.googleapis.com/token").mock(
        return_value=respx.MockResponse(200, json={"id_token": id_token})
    )
    from social_auth import _clear_jwks_cache
    _clear_jwks_cache()
    r = client.get("/auth/google/start",
                   params={"intent": "signin", "return_to": "/"},
                   follow_redirects=False)
    cookie = r.cookies["auth_csrf"]
    import urllib.parse
    state = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(r.headers["location"]).query
    ))["state"]
    r = client.get("/auth/google/callback",
                   params={"code": "any", "state": state},
                   cookies={"auth_csrf": cookie},
                   follow_redirects=False)
    assert r.status_code == 302

    # 3. Password login still works
    r = client.post("/auth/login",
                    json={"username": email, "password": password})
    assert r.status_code == 200, r.text
    assert "token" in r.json()


def test_stash_expired_rejected(client, google_env):
    """A stash token older than 10min is rejected at complete-signup."""
    # Manually craft a stash with exp in the past
    import jwt as _jwt, time, os
    os.environ.setdefault("SECRET_KEY", "test-secret-key-fixed")
    past_payload = {
        "kind": "social_stash",
        "provider": "google",
        "subject_id": "g-stale",
        "email": "stale@example.com",
        "name": "Stale",
        "iat": int(time.time()) - 1000,
        "exp": int(time.time()) - 1,  # expired 1s ago
    }
    stash = _jwt.encode(past_payload, os.environ["SECRET_KEY"],
                        algorithm="HS256")
    r = client.post("/auth/google/complete-signup", json={
        "stash_token": stash,
        "business_name": "Stale Biz",
    })
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "stash_invalid"


def test_complete_signup_provider_mismatch(client, google_env, monkeypatch):
    """A stash signed for Google can't be POSTed to /auth/apple/complete-signup."""
    from social_auth import _sign_stash
    stash = _sign_stash(provider="google", subject_id="g-x",
                        email="x@example.com", name=None)
    # Apple needs to be configured so the endpoint doesn't 503 first.
    monkeypatch.setenv("APPLE_CLIENT_ID", "com.test.khanshoof")
    monkeypatch.setenv("APPLE_TEAM_ID", "TESTTEAMID")
    monkeypatch.setenv("APPLE_KEY_ID", "apple-test-kid")
    monkeypatch.setenv("APPLE_PRIVATE_KEY_PATH", "/tmp/nonexistent.p8")
    r = client.post("/auth/apple/complete-signup", json={
        "stash_token": stash,
        "business_name": "Mismatch Biz",
    })
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "stash_provider_mismatch"
