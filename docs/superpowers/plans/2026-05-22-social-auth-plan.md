# Phase 2.5j — Social Auth (Google + Apple) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Sign in with Google + Sign in with Apple alongside the existing email + password flow. Auto-link by email match on first social sign-in for existing password users; post-bounce business-name form for net-new social signups.

**Architecture:** New `backend/social_auth.py` module owns all 8 endpoints (4 per provider) + JWKS cache + signed-state + stash-token + Apple-client-secret-JWT helpers. New `auth_identities` table links `(provider, subject_id)` to internal users; `users.password_hash` becomes nullable for social-only users. Frontend gets buttons on the auth modal + a `/auth-bounce` SPA route that picks up either a session token or a "complete signup" stash.

**Tech Stack:** FastAPI · PyJWT[crypto] (RS256 + ES256) · httpx · respx (tests) · Postgres.

**Spec:** `docs/superpowers/specs/2026-05-22-social-auth-design.md`
**Branch:** `feature/social-auth` (branched from main at `2843d6d`)
**Test baseline going in:** 294 passing on main.

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(social-auth):`, `test(social-auth):`, or `fix(social-auth):`.
2. Backend source is COPY'd into the image. Rebuild after changes:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
4. The existing test fixtures `client`, `signed_up_org` live in `backend/tests/conftest.py`.
5. Existing helpers we'll use from `main.py`:
   - `hash_password(s) -> str` (line 189) — for an optional password during/after social signup
   - `verify_password(s, hashed) -> bool` (line 195)
   - `audit(request, action=..., actor=..., details=...)` (somewhere in main.py — grep for `^def audit`)
   - `utc_now_iso()` from `db.py`
   - `slug(name)` if it exists; otherwise we'll inline `name.lower().strip().replace(" ", "-")`
6. The existing `/auth/login` at `main.py:1245` is the pattern for issuing a session token. The pattern is:
   ```python
   token = uuid.uuid4().hex
   execute("INSERT INTO sessions (user_id, token, created_at, last_used) "
           "VALUES (?, ?, ?, ?)",
           (user_id, token, utc_now_iso(), utc_now_iso()))
   ```
7. Test env vars for the providers (set via monkeypatch in fixtures, NOT in `.env`):
   ```
   GOOGLE_CLIENT_ID=test-google-client-id
   GOOGLE_CLIENT_SECRET=test-google-secret
   APPLE_CLIENT_ID=com.test.khanshoof
   APPLE_TEAM_ID=TESTTEAMID
   APPLE_KEY_ID=TESTKEYID0
   APPLE_PRIVATE_KEY_PATH=<temp path with generated ES256 key>
   ```
8. The `mcp` SDK and `oauth.py` from earlier phases are unrelated to this work — don't touch them.

---

## Task 1: Schema

**Files:**
- Modify: `backend/db.py`
- Create: `backend/tests/test_social_auth.py`

**Goal:** `users.password_hash` becomes NULL-able. New `auth_identities` table with UNIQUE `(provider, subject_id)`. 3 introspection tests.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_social_auth.py`:

```python
"""Tests for the Phase 2.5j social auth (Google + Apple)."""
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
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py
```
Expected: 3 FAIL — `password_hash` is NOT NULL and `auth_identities` doesn't exist.

- [ ] **Step 3: Add schema changes to `backend/db.py`**

Find `init_db()` in `backend/db.py`. Find the LAST `cursor.execute(...)` in the existing DDL block (after the OAuth tables from Phase 2.5i-1). Insert AFTER it:

```python
        # ── Phase 2.5j: Social auth (Google + Apple) ────────────────────
        cursor.execute(
            "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_identities (
              id             SERIAL PRIMARY KEY,
              user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              provider       TEXT NOT NULL CHECK (provider IN ('google', 'apple')),
              subject_id     TEXT NOT NULL,
              email_at_link  TEXT NOT NULL,
              name_at_link   TEXT,
              created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_used_at   TIMESTAMPTZ,
              UNIQUE (provider, subject_id)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_identities_user "
            "ON auth_identities (user_id)"
        )
```

The `ALTER TABLE ... DROP NOT NULL` is idempotent in Postgres (no-op if already nullable).

- [ ] **Step 4: Rebuild + run**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py
```
Expected: 3 PASS.

- [ ] **Step 5: Full suite regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **297 passed** (294 baseline + 3 new).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_social_auth.py
git commit -m "$(cat <<'EOF'
feat(social-auth): schema — nullable password_hash + auth_identities

users.password_hash becomes NULLABLE so social-only users have no
password (they can add one later via change-password). New
auth_identities table links (provider, subject_id) → user_id with
UNIQUE constraint on the pair so the same Google/Apple account can't
link to two Khanshoof users.

email_at_link + name_at_link are snapshots at link time — we don't
update them when the provider's email drifts because subject_id is
the stable identifier.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pure helpers (JWKS cache, state, stash, Apple JWT, ID token verifiers)

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/social_auth.py`
- Modify: `backend/tests/test_social_auth.py` (append 7 tests + fixtures)

**Goal:** All non-endpoint helpers in `social_auth.py`. Each is independently testable. 7 unit tests.

- [ ] **Step 1: Pin dependencies in `backend/requirements.txt`**

Append:
```
PyJWT[crypto]>=2.8,<3
respx>=0.20
```

`PyJWT[crypto]` brings `cryptography` for RS256 and ES256. `respx` is a test-only HTTP mock for httpx.

- [ ] **Step 2: Write failing tests + fixtures**

Append to `backend/tests/test_social_auth.py`:

```python
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
    from social_auth import _sign_state, _verify_state
    state = _sign_state(provider="google", intent="signin",
                        return_to="/", ttl_seconds=-1)
    import pytest as _pt
    with _pt.raises(Exception):
        _verify_state(state)


def test_verify_state_rejects_tampered():
    """Modified state byte fails signature check."""
    from social_auth import _sign_state, _verify_state
    state = _sign_state(provider="google", intent="signin",
                        return_to="/", ttl_seconds=300)
    # Flip a character in the middle (the payload section)
    parts = state.split(".")
    parts[1] = parts[1][:-2] + ("A" if parts[1][-1] != "A" else "B") + parts[1][-1:]
    import pytest as _pt
    with _pt.raises(Exception):
        _verify_state(".".join(parts))


def test_sign_and_verify_stash_token():
    """Stash token carries subject_id + email + name + provider."""
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
    from social_auth import _apple_client_secret_jwt
    secret = _apple_client_secret_jwt()
    # Decode without verification to check claims
    payload = jwt.decode(secret, options={"verify_signature": False})
    assert payload["iss"] == "TESTTEAMID"
    assert payload["sub"] == "com.test.khanshoof"
    assert payload["aud"] == "https://appleid.apple.com"
    assert payload["exp"] > payload["iat"]
    # Header should have kid
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
    import pytest as _pt
    with _pt.raises(HTTPException) as exc_info:
        verify_google_id_token(id_token, "test-google-client-id")
    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "email_not_verified"
```

- [ ] **Step 3: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py -k "state or stash or apple_client_secret or verify_google"
```
Expected: 7 FAIL — `social_auth` module doesn't exist.

- [ ] **Step 4: Create `backend/social_auth.py`**

```python
"""Phase 2.5j — Social auth (Google + Apple).

This module owns:
- Pure helpers: signed state, stash tokens, Apple client_secret JWT, ID
  token verification, JWKS cache
- 8 endpoints (4 per provider) mounted via attach_social_auth(app)

State and stash tokens are signed with the existing SECRET_KEY env var
(same as the OAuth provider in Phase 2.5i-1). Provider env vars
(GOOGLE_*, APPLE_*) gate which providers are live.
"""
from __future__ import annotations

import os
import time
import json
import secrets
import logging
from typing import Optional, Any
from pathlib import Path

import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

logger = logging.getLogger("signage.social_auth")


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY env var must be set")
    return key


def _api_base_url() -> str:
    return os.getenv("API_BASE_URL", "https://api.khanshoof.com").rstrip("/")


def _app_url() -> str:
    return os.getenv("APP_URL", "https://app.khanshoof.com").rstrip("/")


# ── Signed state for CSRF ──────────────────────────────────────────────


def _sign_state(provider: str, intent: str, return_to: str,
                ttl_seconds: int = 300) -> str:
    """Sign a state token for the OAuth CSRF cookie + URL parameter."""
    now = int(time.time())
    payload = {
        "kind": "social_state",
        "provider": provider,
        "intent": intent,
        "return_to": return_to or "/",
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def _verify_state(state: str) -> dict:
    """Verify and decode a state token. Raises on invalid/expired."""
    return jwt.decode(state, _secret_key(), algorithms=["HS256"],
                      options={"require": ["exp", "iat", "kind"]})


# ── Signed stash for "complete signup" handoff ─────────────────────────


def _sign_stash(provider: str, subject_id: str, email: str,
                name: Optional[str], ttl_seconds: int = 600) -> str:
    now = int(time.time())
    payload = {
        "kind": "social_stash",
        "provider": provider,
        "subject_id": subject_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def _verify_stash(token: str) -> dict:
    payload = jwt.decode(token, _secret_key(), algorithms=["HS256"],
                         options={"require": ["exp", "iat", "kind"]})
    if payload.get("kind") != "social_stash":
        raise jwt.InvalidTokenError("Wrong stash token kind")
    return payload


# ── Apple client_secret JWT ────────────────────────────────────────────


_apple_secret_cache: dict = {"secret": None, "exp": 0}


def _load_apple_private_key() -> bytes:
    path = os.getenv("APPLE_PRIVATE_KEY_PATH")
    if not path:
        raise RuntimeError("APPLE_PRIVATE_KEY_PATH not set")
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Apple private key file not found at {path}")
    return p.read_bytes()


def _apple_client_secret_jwt() -> str:
    """Mint a fresh client_secret JWT for Apple's token endpoint.

    Cached 25min, regenerated before the 30min expiry."""
    now = int(time.time())
    cached = _apple_secret_cache
    if cached["secret"] and cached["exp"] > now + 60:
        return cached["secret"]

    team_id = os.getenv("APPLE_TEAM_ID")
    key_id = os.getenv("APPLE_KEY_ID")
    client_id = os.getenv("APPLE_CLIENT_ID")
    if not all([team_id, key_id, client_id]):
        raise RuntimeError("Apple env vars not configured")

    exp = now + 1500  # 25 minutes
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": exp,
        "aud": "https://appleid.apple.com",
        "sub": client_id,
    }
    secret = jwt.encode(payload, _load_apple_private_key(),
                        algorithm="ES256", headers={"kid": key_id})
    _apple_secret_cache["secret"] = secret
    _apple_secret_cache["exp"] = exp
    return secret


# ── JWKS cache + ID token verification ─────────────────────────────────


_jwks_cache: dict = {}   # url -> {keys, fetched_at}
_JWKS_TTL_SECONDS = 86400


def _clear_jwks_cache() -> None:
    """Test helper: drop the cache so respx mocks fire on next verify."""
    _jwks_cache.clear()
    _apple_secret_cache["secret"] = None
    _apple_secret_cache["exp"] = 0


def _fetch_jwks(url: str) -> dict:
    now = time.time()
    cached = _jwks_cache.get(url)
    if cached and cached["fetched_at"] + _JWKS_TTL_SECONDS > now:
        return cached["jwks"]
    with httpx.Client(timeout=10.0) as c:
        r = c.get(url)
        r.raise_for_status()
        jwks = r.json()
    _jwks_cache[url] = {"jwks": jwks, "fetched_at": now}
    return jwks


def _verify_id_token(id_token: str, *, jwks_url: str, algorithm: str,
                     audience: str, issuer: list[str] | str) -> dict:
    jwks = _fetch_jwks(jwks_url)
    header = jwt.get_unverified_header(id_token)
    key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")),
               None)
    if not key:
        raise HTTPException(status_code=400, detail={
            "code": "key_not_found",
            "message": "ID token signing key not in provider JWKS",
        })
    public_key = jwt.PyJWK(key).key
    try:
        payload = jwt.decode(id_token, public_key,
                             algorithms=[algorithm],
                             audience=audience, issuer=issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_id_token",
            "message": f"ID token verification failed: {exc}",
        })
    return payload


def verify_google_id_token(id_token: str, audience: str) -> dict:
    payload = _verify_id_token(
        id_token,
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        algorithm="RS256",
        audience=audience,
        issuer=["accounts.google.com", "https://accounts.google.com"],
    )
    if not payload.get("email_verified"):
        raise HTTPException(status_code=400, detail={
            "code": "email_not_verified",
            "message": "Google has not verified this email",
        })
    return payload


def verify_apple_id_token(id_token: str, audience: str) -> dict:
    # Apple always sets email_verified=true (they own the email)
    return _verify_id_token(
        id_token,
        jwks_url="https://appleid.apple.com/auth/keys",
        algorithm="ES256",
        audience=audience,
        issuer="https://appleid.apple.com",
    )
```

- [ ] **Step 5: Rebuild + run**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py
```
Expected: 10 passed (3 from Task 1 + 7 new).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **304 passed** (294 + 10).

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/social_auth.py \
        backend/tests/test_social_auth.py
git commit -m "$(cat <<'EOF'
feat(social-auth): pure helpers (JWKS cache, state, stash, Apple JWT)

backend/social_auth.py: signed state for CSRF, signed stash for the
"complete signup" handoff, Apple client_secret JWT (cached 25min,
ES256-signed with the .p8), JWKS cache (24h TTL, lock-free), and
ID-token verification for both Google (RS256) and Apple (ES256).

All helpers tested in isolation against generated test keypairs +
respx-mocked JWKS endpoints — no endpoints yet (Tasks 3–4).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Google flow end-to-end

**Files:**
- Modify: `backend/social_auth.py` (add `attach_social_auth`, /start, /callback, /complete-signup, _finalize_social_signin)
- Modify: `backend/main.py` (mount)
- Modify: `backend/tests/test_social_auth.py` (append 5 tests)

**Goal:** Full Google flow: `/auth/google/start` → 302 to Google with state cookie → `/auth/google/callback?code=...&state=...` → token exchange → ID token verify → `_finalize_social_signin` decides between log-in / auto-link / stash. `POST /auth/{provider}/complete-signup` creates org+user+identity. Tests for all four code paths.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_social_auth.py`:

```python
# ── Google flow ───────────────────────────────────────────────────────


def test_google_start_redirects_with_state_cookie(client, google_env):
    r = client.get("/auth/google/start",
                   params={"intent": "signup", "return_to": "/"},
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "auth_csrf" in r.headers.get("set-cookie", "")
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
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py -k "google_"
```
Expected: 5 FAIL — endpoints don't exist yet.

- [ ] **Step 3: Append Google flow to `backend/social_auth.py`**

Append at the end of `backend/social_auth.py`:

```python
# ── Endpoint mounting ──────────────────────────────────────────────────


from fastapi import APIRouter, Request, Form, Query

router = APIRouter()


_STATE_COOKIE = "auth_csrf"
_STATE_TTL = 300
_STASH_TTL = 600


def _provider_configured(provider: str) -> bool:
    if provider == "google":
        return bool(os.getenv("GOOGLE_CLIENT_ID")
                    and os.getenv("GOOGLE_CLIENT_SECRET"))
    if provider == "apple":
        return bool(os.getenv("APPLE_CLIENT_ID")
                    and os.getenv("APPLE_TEAM_ID")
                    and os.getenv("APPLE_KEY_ID")
                    and os.getenv("APPLE_PRIVATE_KEY_PATH"))
    return False


def _provider_not_configured() -> HTTPException:
    return HTTPException(status_code=503, detail={
        "code": "provider_not_configured",
        "message": "This sign-in provider is not configured on the server.",
    })


def _google_redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI",
                     f"{_api_base_url()}/auth/google/callback")


def _apple_redirect_uri() -> str:
    return os.getenv("APPLE_REDIRECT_URI",
                     f"{_api_base_url()}/auth/apple/callback")


def _build_google_authorize_url(state: str) -> str:
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def _build_apple_authorize_url(state: str) -> str:
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": os.getenv("APPLE_CLIENT_ID"),
        "redirect_uri": _apple_redirect_uri(),
        "response_type": "code id_token",
        "response_mode": "form_post",
        "scope": "name email",
        "state": state,
    })
    return f"https://appleid.apple.com/auth/authorize?{params}"


@router.get("/auth/google/start")
def google_start(intent: str = Query("signup"),
                 return_to: str = Query("/")):
    if not _provider_configured("google"):
        raise _provider_not_configured()
    state = _sign_state(provider="google", intent=intent,
                        return_to=return_to, ttl_seconds=_STATE_TTL)
    resp = RedirectResponse(_build_google_authorize_url(state),
                            status_code=302)
    resp.set_cookie(
        _STATE_COOKIE, state,
        httponly=True, samesite="lax", max_age=_STATE_TTL,
        secure=_api_base_url().startswith("https://"),
    )
    return resp


@router.get("/auth/apple/start")
def apple_start(intent: str = Query("signup"),
                return_to: str = Query("/")):
    if not _provider_configured("apple"):
        raise _provider_not_configured()
    state = _sign_state(provider="apple", intent=intent,
                        return_to=return_to, ttl_seconds=_STATE_TTL)
    resp = RedirectResponse(_build_apple_authorize_url(state),
                            status_code=302)
    resp.set_cookie(
        _STATE_COOKIE, state,
        httponly=True, samesite="lax", max_age=_STATE_TTL,
        secure=_api_base_url().startswith("https://"),
    )
    return resp


def _validate_csrf(request: Request, state: str) -> dict:
    cookie_state = request.cookies.get(_STATE_COOKIE)
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_state",
            "message": "CSRF state cookie missing or mismatched.",
        })
    try:
        return _verify_state(state)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_state",
            "message": f"State token invalid: {exc}",
        })


def _issue_session(user_id: int) -> str:
    """Mirror /auth/login's session-issue pattern."""
    import uuid
    from db import execute, utc_now_iso
    token = uuid.uuid4().hex
    now = utc_now_iso()
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) "
        "VALUES (?, ?, ?, ?)",
        (user_id, token, now, now),
    )
    return token


def _finalize_social_signin(request: Request, provider: str,
                            subject_id: str, email: str,
                            name: Optional[str], return_to: str
                            ) -> RedirectResponse:
    """Decide between log-in (existing identity), auto-link, or stash."""
    from db import query_one, execute
    # Lazy import audit
    audit = _audit_or_noop()

    email = email.lower().strip()
    identity = query_one(
        "SELECT id, user_id FROM auth_identities "
        "WHERE provider = ? AND subject_id = ?",
        (provider, subject_id),
    )
    if identity:
        execute(
            "UPDATE auth_identities SET last_used_at = now() WHERE id = ?",
            (identity["id"],),
        )
        token = _issue_session(identity["user_id"])
        audit(request, action="auth.social.signin",
              actor={"id": identity["user_id"]},
              details={"provider": provider})
        return RedirectResponse(_bounce_with_token(token, return_to),
                                status_code=302)

    # Auto-link existing user by email
    user = query_one("SELECT id FROM users WHERE username = ?", (email,))
    if user:
        execute(
            "INSERT INTO auth_identities (user_id, provider, subject_id, "
            "email_at_link, name_at_link, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, now())",
            (user["id"], provider, subject_id, email, name),
        )
        token = _issue_session(user["id"])
        audit(request, action="auth.social.linked",
              actor={"id": user["id"]},
              details={"provider": provider})
        return RedirectResponse(_bounce_with_token(token, return_to),
                                status_code=302)

    # New user — stash and prompt for business name
    stash = _sign_stash(provider=provider, subject_id=subject_id,
                        email=email, name=name, ttl_seconds=_STASH_TTL)
    return RedirectResponse(
        _bounce_with_stash(stash, suggested_name=name or ""),
        status_code=302,
    )


def _audit_or_noop():
    """Return main.audit or a no-op if main can't be imported (e.g., tests)."""
    try:
        from main import audit
        return audit
    except Exception:
        def _noop(*a, **kw): pass
        return _noop


def _bounce_with_token(token: str, return_to: str) -> str:
    import urllib.parse
    qs = urllib.parse.urlencode({"token": token, "return_to": return_to})
    return f"{_app_url()}/auth-bounce?{qs}"


def _bounce_with_stash(stash: str, suggested_name: str) -> str:
    import urllib.parse
    qs = urllib.parse.urlencode({
        "complete_signup": stash,
        "suggested_name": suggested_name,
    })
    return f"{_app_url()}/auth-bounce?{qs}"


@router.get("/auth/google/callback")
async def google_callback(request: Request,
                          code: str = Query(...),
                          state: str = Query(...)):
    if not _provider_configured("google"):
        raise _provider_not_configured()
    state_payload = _validate_csrf(request, state)
    return_to = state_payload.get("return_to", "/")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        })
    if r.status_code >= 400:
        logger.warning("google_token_exchange_failed status=%d body=%r",
                       r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={
            "code": "provider_unavailable",
            "message": "Google rejected the authorization code.",
        })
    id_token = r.json().get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail={
            "code": "no_id_token",
            "message": "Google did not return an id_token.",
        })

    payload = verify_google_id_token(id_token, os.getenv("GOOGLE_CLIENT_ID"))
    return _finalize_social_signin(
        request,
        provider="google",
        subject_id=payload["sub"],
        email=payload["email"],
        name=payload.get("name"),
        return_to=return_to,
    )


# ── Complete-signup (shared between providers) ─────────────────────────


from pydantic import BaseModel, Field


class CompleteSocialSignup(BaseModel):
    stash_token: str
    business_name: str = Field(..., min_length=1, max_length=200)


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "org"


def _unique_slug(base: str) -> str:
    """Append -N until the slug is unused."""
    from db import query_one
    slug = base
    n = 1
    while query_one("SELECT id FROM organizations WHERE slug = ?", (slug,)):
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.post("/auth/{provider}/complete-signup")
def complete_social_signup(provider: str, payload: CompleteSocialSignup,
                           request: Request):
    if provider not in ("google", "apple"):
        raise HTTPException(status_code=404, detail={
            "code": "unknown_provider",
            "message": f"Unknown provider: {provider}",
        })
    if not _provider_configured(provider):
        raise _provider_not_configured()
    try:
        stash = _verify_stash(payload.stash_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "stash_invalid",
            "message": f"Stash token invalid or expired: {exc}",
        })
    if stash["provider"] != provider:
        raise HTTPException(status_code=400, detail={
            "code": "stash_provider_mismatch",
            "message": "Stash token was issued for a different provider.",
        })

    from db import query_one, execute, utc_now_iso
    email = stash["email"]
    subject_id = stash["subject_id"]
    name = stash.get("name")

    # Race-safe re-check
    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        raise HTTPException(status_code=409, detail={
            "code": "email_taken",
            "message": "An account with this email already exists.",
        })
    if query_one(
        "SELECT id FROM auth_identities WHERE provider = ? AND subject_id = ?",
        (provider, subject_id),
    ):
        raise HTTPException(status_code=409, detail={
            "code": "identity_taken",
            "message": "This identity is already linked to another account.",
        })

    slug = _unique_slug(_slug(payload.business_name))
    execute(
        "INSERT INTO organizations (name, slug, plan, screen_limit, "
        "subscription_status, locale, created_at) "
        "VALUES (?, ?, 'starter', 5, 'trialing', 'en', now())",
        (payload.business_name, slug),
    )
    org = query_one("SELECT * FROM organizations WHERE slug = ?", (slug,))
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) "
        "VALUES (?, ?, NULL, 1, 'admin', ?)",
        (org["id"], email, utc_now_iso()),
    )
    user = query_one("SELECT * FROM users WHERE username = ?", (email,))
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link, name_at_link, last_used_at) "
        "VALUES (?, ?, ?, ?, ?, now())",
        (user["id"], provider, subject_id, email, name),
    )

    token = _issue_session(user["id"])
    audit = _audit_or_noop()
    audit(request, action="auth.social.signup",
          actor={"id": user["id"]},
          details={"provider": provider,
                   "business_name": payload.business_name})
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "is_admin": bool(user["is_admin"]),
        },
        "organization": {
            "id": org["id"],
            "name": org["name"],
            "slug": org["slug"],
            "plan": org["plan"],
            "screen_limit": org["screen_limit"],
            "subscription_status": org["subscription_status"],
        },
    }


def attach_social_auth(app) -> None:
    """Mount the social auth router on the main FastAPI app."""
    app.include_router(router)
```

- [ ] **Step 4: Mount the router in `backend/main.py`**

Find an existing `app.include_router(...)` line near the top of the app setup (e.g., the OAuth or MCP mount from earlier phases — they're around lines 110–150 depending on which Phase has landed). Add after it:

```python
from social_auth import attach_social_auth
attach_social_auth(app)
```

If `feature/social-auth` is branched from main (no OAuth/MCP merged yet), use a placeholder location near `app = FastAPI(...)`:

```python
from social_auth import attach_social_auth
attach_social_auth(app)
```

- [ ] **Step 5: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py
```
Expected: 15 passed (10 from earlier + 5 new).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **309 passed** (304 + 5).

- [ ] **Step 7: Commit**

```bash
git add backend/social_auth.py backend/main.py backend/tests/test_social_auth.py
git commit -m "$(cat <<'EOF'
feat(social-auth): Google flow end-to-end

GET /auth/google/start sets the auth_csrf cookie + 302s to Google's
OAuth authorize endpoint with openid+email+profile scopes. GET
/auth/google/callback verifies the state cookie, exchanges the code
for tokens at Google's endpoint, verifies the ID token against
Google's JWKS, then hands off to _finalize_social_signin.

_finalize_social_signin owns the three-path decision:
- Existing identity → log in, update last_used_at, redirect with token.
- No identity but email matches existing user → auto-link, issue
  session, redirect with token (auto-link policy from Q1 in spec).
- No identity, no user → sign a 10-min stash token, redirect to
  /auth-bounce?complete_signup=<stash>&suggested_name=<name>.

POST /auth/{provider}/complete-signup verifies the stash, creates
organizations + users (password_hash=NULL) + auth_identities in a
single request, returns a session token + the standard
/auth/signup/complete response shape.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Apple flow end-to-end

**Files:**
- Modify: `backend/social_auth.py`
- Modify: `backend/tests/test_social_auth.py` (append 4 tests)

**Goal:** Apple `/auth/apple/start` already exists from Task 3 (we built both /start endpoints together because they're nearly identical). This task adds the Apple `/callback` (which is a POST, not a GET), captures the first-sign-in name from the `user` form field, and signs the Apple client_secret JWT on demand. 4 tests.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_social_auth.py`:

```python
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
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py -k "apple_"
```
Expected: 4 FAIL.

- [ ] **Step 3: Append the Apple callback to `backend/social_auth.py`**

Add this endpoint immediately after the Google callback in `backend/social_auth.py`:

```python
@router.post("/auth/apple/callback")
async def apple_callback(request: Request,
                         code: str = Form(...),
                         state: str = Form(...),
                         id_token: str = Form(...),
                         user: Optional[str] = Form(None)):
    """Apple POSTs the callback (response_mode=form_post)."""
    if not _provider_configured("apple"):
        raise _provider_not_configured()
    state_payload = _validate_csrf(request, state)
    return_to = state_payload.get("return_to", "/")

    # Verify the inline id_token first; if it's tampered, abort before
    # round-tripping to Apple's token endpoint
    audience = os.getenv("APPLE_CLIENT_ID")
    payload = verify_apple_id_token(id_token, audience)

    # Exchange code for tokens (this gives us the canonical id_token Apple
    # signed, and confirms the code is genuine).
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("https://appleid.apple.com/auth/token", data={
            "code": code,
            "client_id": audience,
            "client_secret": _apple_client_secret_jwt(),
            "redirect_uri": _apple_redirect_uri(),
            "grant_type": "authorization_code",
        })
    if r.status_code >= 400:
        logger.warning("apple_token_exchange_failed status=%d body=%r",
                       r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={
            "code": "provider_unavailable",
            "message": "Apple rejected the authorization code.",
        })
    canonical_id_token = r.json().get("id_token")
    if canonical_id_token:
        # Re-verify the canonical id_token (defense in depth)
        payload = verify_apple_id_token(canonical_id_token, audience)

    # Capture name on first sign-in (Apple only sends it once)
    name = None
    if user:
        try:
            user_data = json.loads(user)
            n = user_data.get("name", {})
            parts = [n.get("firstName", ""), n.get("lastName", "")]
            name = " ".join(p for p in parts if p).strip() or None
        except (json.JSONDecodeError, AttributeError):
            name = None

    return _finalize_social_signin(
        request,
        provider="apple",
        subject_id=payload["sub"],
        email=payload["email"],
        name=name,
        return_to=return_to,
    )
```

- [ ] **Step 4: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py
```
Expected: 19 passed (15 + 4).

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **313 passed** (309 + 4).

- [ ] **Step 6: Commit**

```bash
git add backend/social_auth.py backend/tests/test_social_auth.py
git commit -m "$(cat <<'EOF'
feat(social-auth): Apple flow end-to-end

POST /auth/apple/callback handles Apple's form_post response mode
(not GET). Verifies the inline id_token first, then exchanges the
code at Apple's token endpoint using the locally-signed
client_secret JWT (helper from Task 2). On first sign-in only,
Apple sends a `user` JSON form field with first/last name — we
parse and pass it through as name_at_link. Subsequent sign-ins
don't carry `user` and we just look up by subject_id.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Cross-cutting tests

**Files:**
- Modify: `backend/tests/test_social_auth.py` (append 4 tests)

**Goal:** Verify cross-cutting concerns: provider-not-configured returns 503, audit log entries are written, existing password login still works after linking, edge cases on stash/identity collision. No production code changes — these tests just exercise the existing infrastructure.

- [ ] **Step 1: Append tests**

Append to `backend/tests/test_social_auth.py`:

```python
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
    from social_auth import _sign_stash
    # Manually craft a stash with exp in the past
    import jwt as _jwt, time, os
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


def test_complete_signup_provider_mismatch(client, google_env):
    """A stash signed for Google can't be POSTed to /auth/apple/complete-signup."""
    from social_auth import _sign_stash
    stash = _sign_stash(provider="google", subject_id="g-x",
                        email="x@example.com", name=None)
    r = client.post("/auth/apple/complete-signup", json={
        "stash_token": stash,
        "business_name": "Mismatch Biz",
    })
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "stash_provider_mismatch"
```

**Note on the SECRET_KEY env var:** the test `test_stash_expired_rejected` needs `SECRET_KEY` to be set. The `google_env` fixture (the one this test uses) doesn't set SECRET_KEY directly. If it's not set globally in the test environment, add it to the existing `google_env` and `apple_env` fixtures (Step 1 above, Task 2) — modify them to also `monkeypatch.setenv("SECRET_KEY", "test-secret-key-fixed")`. Or check the project's conftest.py — there might already be a `SECRET_KEY` set somewhere globally for tests.

- [ ] **Step 2: Run new tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_social_auth.py -k "provider_not_configured or password_login_still_works or stash_expired or complete_signup_provider_mismatch"
```
Expected: 4 passed.

- [ ] **Step 3: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **317 passed** (313 + 4).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_social_auth.py
git commit -m "$(cat <<'EOF'
test(social-auth): cross-cutting — config + login compat + edges

Four tests:
- /auth/google/start with no env vars → 503 provider_not_configured
- Existing password user can still log in via /auth/login after
  adding a Google identity (proves the link doesn't downgrade)
- Stash token expired (past exp) → 400 stash_invalid
- Stash signed for Google posted to /auth/apple/complete-signup →
  400 stash_provider_mismatch

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend — buttons + /auth-bounce + complete-signup + i18n

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/i18n/en.json` (or wherever i18n strings live — grep `auth.login` in the repo)
- Modify: `frontend/i18n/ar.json`

**Goal:** Auth modal gets two new buttons. A new "auth-bounce" handler runs when the page loads with `?token=...` or `?complete_signup=...` in the URL. Bilingual labels.

No automated tests in this task — backend test suite stays at 317. Manual browser smoke is the verification.

- [ ] **Step 1: Locate the auth modal in `frontend/app.js`**

```bash
grep -nE "auth-tab-signup|signup-request-form|signup-password-form" /home/ahmed/signage/frontend/app.js | head -10
```

The auth modal lives around line 1300. You'll add buttons above the existing form fields in both the signup and signin tabs.

- [ ] **Step 2: Add the social buttons HTML in the auth modal markup**

Find the existing auth modal HTML (likely in `frontend/index.html` or built via template strings in app.js). Above the email field on each tab, add:

```html
<div class="social-auth-buttons">
  <button type="button" class="btn-social btn-google"
          data-provider="google" data-intent="signin">
    <svg class="social-icon" viewBox="0 0 24 24" aria-hidden="true">
      <!-- Google G logo (use the official Google brand SVG path) -->
    </svg>
    <span data-i18n="auth.social.google">Sign in with Google</span>
  </button>
  <button type="button" class="btn-social btn-apple"
          data-provider="apple" data-intent="signin">
    <svg class="social-icon" viewBox="0 0 24 24" aria-hidden="true">
      <!-- Apple logo (per Apple HIG) -->
    </svg>
    <span data-i18n="auth.social.apple">Sign in with Apple</span>
  </button>
  <div class="social-auth-divider">
    <span data-i18n="auth.social.divider">or</span>
  </div>
</div>
```

For the signup tab, duplicate with `data-intent="signup"`.

- [ ] **Step 3: Add the click handler in `app.js`**

```javascript
document.querySelectorAll(".btn-social").forEach((btn) => {
  btn.addEventListener("click", () => {
    const provider = btn.dataset.provider;
    const intent = btn.dataset.intent || "signin";
    const returnTo = window.location.pathname + window.location.search;
    const params = new URLSearchParams({intent, return_to: returnTo});
    window.location = `${API_BASE_URL}/auth/${provider}/start?${params}`;
  });
});
```

(`API_BASE_URL` is presumably already defined globally in app.js — grep for it.)

- [ ] **Step 4: Add the `/auth-bounce` handler at page-load time**

Near the top-level of `app.js` (after the auth modal mounting code), add:

```javascript
function handleAuthBounce() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const stash = params.get("complete_signup");
  const returnTo = params.get("return_to") || "/";
  const suggestedName = params.get("suggested_name") || "";

  if (token) {
    // Token-based bounce: store session, navigate
    localStorage.setItem("session_token", token);
    window.history.replaceState({}, "", returnTo || "/");
    window.location.reload();
    return;
  }

  if (stash) {
    // Complete-signup flow: show the business-name form
    showCompleteSignupModal({stash, suggestedName});
    return;
  }

  // No bounce params — normal page load
}

function showCompleteSignupModal({stash, suggestedName}) {
  // Render a modal with one input prefilled to suggestedName,
  // submit POSTs to /auth/{provider}/complete-signup with the stash.
  // Provider is inferred from the stash's JWT payload — or we can keep
  // it in the URL too as &provider=google (decided in spec).
  // For simplicity, decode the JWT payload client-side without verifying
  // (just to read provider — server re-verifies on submit).
  const payload = JSON.parse(atob(stash.split(".")[1]));
  const provider = payload.provider;

  const html = `
    <div class="modal" id="complete-signup-modal">
      <h2 data-i18n="auth.social.complete_signup.title">Welcome to Khanshoof!</h2>
      <form id="complete-signup-form">
        <label data-i18n="auth.social.complete_signup.business_name">Business name</label>
        <input type="text" name="business_name" required
               value="${escapeHtml(suggestedName)}" autofocus>
        <button type="submit" data-i18n="auth.social.complete_signup.continue">Continue</button>
      </form>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  applyI18n();

  document.getElementById("complete-signup-form")
          .addEventListener("submit", async (e) => {
    e.preventDefault();
    const businessName = e.target.business_name.value.trim();
    if (!businessName) return;
    const r = await fetch(`${API_BASE_URL}/auth/${provider}/complete-signup`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({stash_token: stash, business_name: businessName}),
    });
    const body = await r.json();
    if (r.ok) {
      localStorage.setItem("session_token", body.token);
      window.location = "/";
    } else {
      const msg = (body.detail && body.detail.message)
                  || "Failed to complete signup";
      alert(msg);
    }
  });
}

// Wire on page-load
if (window.location.pathname === "/auth-bounce") {
  handleAuthBounce();
}
```

- [ ] **Step 5: Add i18n strings**

In `frontend/i18n/en.json` (or wherever the EN strings live — find them by grepping `auth.login` in the repo):

```json
{
  "auth.social.google": "Sign in with Google",
  "auth.social.apple": "Sign in with Apple",
  "auth.social.divider": "or",
  "auth.social.complete_signup.title": "Welcome to Khanshoof!",
  "auth.social.complete_signup.business_name": "Business name",
  "auth.social.complete_signup.continue": "Continue"
}
```

In `frontend/i18n/ar.json`:

```json
{
  "auth.social.google": "تسجيل الدخول باستخدام جوجل",
  "auth.social.apple": "تسجيل الدخول باستخدام Apple",
  "auth.social.divider": "أو",
  "auth.social.complete_signup.title": "أهلاً بك في خانشوف!",
  "auth.social.complete_signup.business_name": "اسم الشركة",
  "auth.social.complete_signup.continue": "متابعة"
}
```

Per Apple's HIG, the brand name "Apple" stays in English in the Arabic label.

- [ ] **Step 6: Add minimal CSS for the social buttons**

In the main CSS file (e.g., `frontend/styles.css` — grep for existing `.btn` style):

```css
.social-auth-buttons {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-block-end: 16px;
}
.btn-social {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid var(--border, #ddd);
  border-radius: 8px;
  background: #fff;
  font: inherit;
  cursor: pointer;
}
.btn-social:hover { filter: brightness(0.97); }
.btn-google { color: #3c4043; }
.btn-apple { background: #000; color: #fff; border-color: #000; }
.social-icon { width: 18px; height: 18px; }
.social-auth-divider {
  display: flex; align-items: center;
  text-align: center;
  color: var(--muted, #999);
  font-size: 13px;
  margin-block: 12px;
}
.social-auth-divider::before,
.social-auth-divider::after {
  content: "";
  flex: 1;
  border-block-start: 1px solid var(--border, #eee);
  margin-inline: 8px;
}
```

- [ ] **Step 7: Rebuild frontend + manual smoke**

```bash
docker-compose build frontend && docker-compose up -d --force-recreate frontend
```

Manual smoke test:
1. Open `https://app.khanshoof.com/` (or localhost equivalent)
2. Click "Sign In" — see Google + Apple buttons above the email field
3. Click "Sign up" tab — same buttons appear
4. Buttons appear in correct RTL when locale is set to AR

(Backend test suite stays at 317 since this task changes only frontend.)

- [ ] **Step 8: Commit**

```bash
git add frontend/app.js frontend/i18n/ frontend/styles.css
# Adjust paths based on what your repo actually uses
git commit -m "$(cat <<'EOF'
feat(social-auth): frontend buttons + /auth-bounce + i18n

Auth modal gains Sign in with Google + Sign in with Apple buttons on
both signup and signin tabs, above the email field with an "— or —"
divider. /auth-bounce handles two post-callback shapes:
- ?token=...&return_to=... → store session, navigate.
- ?complete_signup=<stash>&suggested_name=... → render "What's your
  business name?" modal, POST to /auth/{provider}/complete-signup,
  store session, navigate to /.

Provider is decoded client-side from the stash JWT payload (just to
pick the endpoint; server re-verifies on submit). Bilingual EN/AR
strings — Apple's brand name stays English in the Arabic label per
their HIG.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: E2E + push + PR

**Files:**
- Modify: `backend/tests/test_social_auth.py` (1 additional E2E test if practical)

**Goal:** Final regression, push the branch, open the PR, save the memory file.

- [ ] **Step 1: Full suite regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **317 passed** (294 baseline + 23 new). Allow ±1 for the known api_keys test_lookup_updates_last_used_at flake.

- [ ] **Step 2: i18n parity check**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: OK (the 6 new strings exist in both EN and AR).

- [ ] **Step 3: JS parse sanity**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: OK.

- [ ] **Step 4: Push branch**

```bash
git push -u origin feature/social-auth
```

- [ ] **Step 5: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(social-auth): Phase 2.5j — Sign in with Google + Apple" \
  --body "$(cat <<'EOF'
## Summary

Adds Sign in with Google + Sign in with Apple alongside the existing email + password flow. Auto-link by email match on first social sign-in for existing password users; post-bounce business-name form for net-new social signups.

- New \`auth_identities\` table linking \`(provider, subject_id)\` to internal users
- \`users.password_hash\` becomes nullable for social-only users
- 8 new endpoints (4 per provider): \`/start\`, \`/callback\`, \`/complete-signup\` × 2 (one shared route)
- ID token verification against provider JWKS (Google RS256, Apple ES256)
- Apple client_secret is a locally-signed JWT (ES256 + .p8 key), cached 25min
- State CSRF via HMAC-signed cookie + URL param, double-submit verified
- Stash token (10min TTL) carries (subject_id, email, name, provider) across the post-bounce business-name form
- Frontend: Google + Apple buttons on both signup/signin tabs, /auth-bounce SPA route, complete-signup modal, EN/AR strings

## Spec
\`docs/superpowers/specs/2026-05-22-social-auth-design.md\`

## Plan
\`docs/superpowers/plans/2026-05-22-social-auth-plan.md\`

## Test plan

- [x] Backend: 317 passed (294 baseline + 23 new social-auth tests)
- [x] Schema introspection: password_hash nullable, auth_identities exists, UNIQUE (provider, subject_id) enforced
- [x] Helpers: state sign/verify, stash sign/verify, Apple client_secret JWT, Google + Apple ID token verify
- [x] Google flow: new user → stash; existing user → auto-link; existing identity → log in; complete-signup creates org+user+identity
- [x] Apple flow: POST form_post callback, first-signin name capture, subsequent signin without user field
- [x] Cross-cutting: provider not configured → 503, password login still works after linking, stash expired → 400, stash provider mismatch → 400
- [ ] Manual smoke: real Google Sign-In flow (operator does this after configuring GCP)
- [ ] Manual smoke: real Apple Sign-In flow (operator does this after Apple Developer + .p8 setup)
- [ ] AR locale visual check on the buttons + complete-signup modal

## Operator setup (before deploying)

1. **Google**: GCP Console → APIs & Services → Credentials → Create OAuth 2.0 Client ID (Web app) → authorized redirect URI \`https://api.khanshoof.com/auth/google/callback\` → add \`https://app.khanshoof.com\` to authorized JavaScript origins → copy \`GOOGLE_CLIENT_ID\` + \`GOOGLE_CLIENT_SECRET\` to \`.env\`.
2. **Apple**: Apple Developer account ($99/yr) → Certificates → Identifiers → register a Services ID (this is \`APPLE_CLIENT_ID\`) → enable Sign In with Apple → configure return URL \`https://api.khanshoof.com/auth/apple/callback\` → generate Sign In with Apple private key (.p8), note the Key ID → drop the .p8 file at \`APPLE_PRIVATE_KEY_PATH\` → set \`APPLE_TEAM_ID\`, \`APPLE_KEY_ID\`, \`APPLE_CLIENT_ID\` in \`.env\`.
3. Restart backend. Smoke test both flows end-to-end.

## Known v1 limitations (documented in spec)

- Microsoft / GitHub / GitHub Enterprise / phone-number auth — only Google + Apple in v1.
- Settings page to "Unlink Google" / "Unlink Apple" — UI lands in a follow-up; users can revoke at the provider's own dashboard meanwhile.
- Multi-org users (one Google identity → multiple Khanshoof orgs) — one-to-one in v1.
- Provider account display name change — \`name_at_link\` is frozen at link time; no refresh.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Save memory file**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_social_auth.md`:

```markdown
---
name: Social auth (Phase 2.5j) — PR open
description: Sign in with Google + Apple alongside email/password, with auto-link by email and post-bounce business-name form for new social signups.
type: project
---

**Status (2026-05-22):** PR open against main. Operator setup (GCP + Apple Developer) required before going live.

**What landed:**
- backend/social_auth.py — single module, 8 endpoints, JWKS cache, state + stash helpers, Apple client_secret JWT, provider env-var gating (Google + Apple can be enabled independently).
- New auth_identities table (provider, subject_id, user_id, email_at_link, name_at_link, created_at, last_used_at) with UNIQUE (provider, subject_id).
- users.password_hash becomes nullable.
- Auto-link by email match for existing password users; post-bounce business-name modal for new signups.
- ID tokens verified against provider JWKS (RS256 Google, ES256 Apple).
- 23 backend tests passing.

**Test count:** 317 backend tests passing (294 baseline + 23 new).

**Plan:** docs/superpowers/plans/2026-05-22-social-auth-plan.md — 7 tasks.
**Spec:** docs/superpowers/specs/2026-05-22-social-auth-design.md.

**v1 deferrals:** Microsoft/GitHub/phone-number, "unlink" settings UI, multi-org users, provider name refresh.

**Operator setup needed before launch:**
- Google: GCP Console → OAuth 2.0 Client ID → set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env.
- Apple: Apple Developer ($99/yr) → Services ID + .p8 key → set APPLE_CLIENT_ID, APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY_PATH in .env.
- Restart backend; smoke test both flows.

**Next phase suggestions:** Settings UI for managing linked identities (unlink button); add Microsoft for enterprise customers; investigate B2B "Sign in with Google Workspace" for tenants whose org email matches their Google Workspace domain.
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md`:

```markdown
- [Social auth (Phase 2.5j)](project_social_auth.md) — **PR OPEN 2026-05-22**. Sign in with Google + Apple alongside email/password. Auto-link by email for existing users; post-bounce business-name form for new signups. 317 tests passing.
```

- [ ] **Step 7: Final verification**

```bash
git -C /home/ahmed/signage status -sb
~/.local/bin/gh pr view --json number,url,state
```
Expected: PR open, working tree clean.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §1 Goal | All tasks |
| §3 Architecture | Tasks 2, 3, 4 |
| §4 Database changes | Task 1 |
| §5.1 /start | Task 3 (Google + Apple — built together) |
| §5.2 Google /callback | Task 3 |
| §5.3 Apple /callback | Task 4 |
| §5.4 _finalize_social_signin | Task 3 |
| §5.5 /complete-signup | Task 3 |
| §6 ID token verification | Task 2 |
| §7 Env vars | Task 3 (gating helper `_provider_configured`) + Task 5 (503 test) |
| §8 Frontend | Task 6 |
| §9 Testing | Distributed: 3 + 7 + 5 + 4 + 4 = 23 backend tests |
| §11 Failure modes | Tasks 3, 4, 5 cover the listed modes |
| §12 Security notes | Implementation enforces them; tests verify state + stash + audience + issuer |
| §13 v1 deferrals | Documented in PR body (Task 7) |
| §14 Migration | No data migration; ALTER TABLE is idempotent (Task 1) |
| §15 Verification before merge | Tasks 1–5 cover automated; Task 7 lists manual smoke checklist |

No placeholders. Helper names + endpoint paths consistent across tasks (`_sign_state`, `_verify_state`, `_sign_stash`, `_verify_stash`, `_apple_client_secret_jwt`, `_provider_configured`, `_finalize_social_signin`, `_bounce_with_token`, `_bounce_with_stash`, `_issue_session`).

Task ordering: 1 (schema, blocks everything) → 2 (pure helpers, no endpoint depends on this) → 3 (Google + the shared /start, /complete-signup, /finalize) → 4 (Apple /callback only, reuses everything) → 5 (cross-cutting verification) → 6 (frontend, no backend changes) → 7 (push, PR, memory).

Test count math: 294 baseline + 3 (T1) + 7 (T2) + 5 (T3) + 4 (T4) + 4 (T5) = 317 final.

Each task ends with a green test suite.
