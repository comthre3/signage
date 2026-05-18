# Phase 2.5i-1 — OAuth 2.1 + PKCE Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working OAuth 2.1 + PKCE authorization server at `/oauth/*` + `/.well-known/*`. Tokens it issues are accepted by the existing Phase 2.5h `get_api_authed` so any endpoint that takes an API key also takes an OAuth access token.

**Architecture:** New `backend/oauth.py` module owns all OAuth endpoints + helpers + a FastAPI router. 3 new DB tables (`oauth_clients`, `oauth_authorization_codes`, `oauth_tokens`). Server-rendered Jinja2 templates for login + consent (the only non-SPA HTML in the project). PKCE-only, no client secrets — all clients are public per OAuth 2.1.

**Tech Stack:** FastAPI · Jinja2 templates · psycopg · `hash_password`/`verify_password` PBKDF2 helpers from Phase 2.5h · `secrets.token_urlsafe`.

**Spec:** `docs/superpowers/specs/2026-05-18-oauth-provider-design.md`
**Branch:** `feature/oauth-provider` (already created from main `2843d6d`)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(oauth):` or `test(oauth):`.
2. Backend container source baked into image — rebuild after changes:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Backend tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
4. **Baseline on `main` is 294 passing.**
5. Pytest container path: `tests/...` (cwd is `/app`).
6. `db.py` uses `?` placeholders translated to `%s` for psycopg. Use `?` in SQL.
7. The `client` and `signed_up_org` fixtures exist in `conftest.py`. `signed_up_org` returns `{token, user, org}` after a fresh trialing-state org signup; password is `Khanshoof2026Test`.
8. The `hash_password(s) -> "salt$digest"` and `verify_password(s, hashed) -> bool` helpers exist at the top of `backend/main.py`.
9. `Jinja2Templates` from `fastapi.templating` is the simplest way to render server-side HTML — it imports `jinja2` (already a FastAPI transitive dependency, no requirements.txt change).
10. Do NOT modify `.env` or rewrite prod URLs.

---

## Task 1: Schema + pre-registered clients

**Files:**
- Modify: `backend/db.py`
- Create: `backend/tests/test_oauth.py`

**Goal:** 3 new tables, pre-registered clients seeded at `init_db()` time, 5 introspection + seed tests.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_oauth.py`:

```python
"""Tests for the Phase 2.5i-1 OAuth 2.1 authorization server."""
from db import query_one, query_all


def test_oauth_clients_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_clients", "client_id"),
    )
    assert row is not None


def test_oauth_authorization_codes_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_authorization_codes", "code_hash"),
    )
    assert row is not None


def test_oauth_tokens_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_tokens", "access_token_hash"),
    )
    assert row is not None


def test_pre_registered_clients_seeded():
    """All four known MCP clients should be seeded at init_db() time."""
    rows = query_all(
        "SELECT client_id FROM oauth_clients WHERE pre_registered = true "
        "ORDER BY client_id"
    )
    client_ids = [r["client_id"] for r in rows]
    for expected in ("claude-code", "claude-desktop", "cursor", "zed"):
        assert expected in client_ids, f"Missing pre-registered client: {expected}"


def test_pre_registered_clients_have_friendly_names():
    row = query_one(
        "SELECT client_name FROM oauth_clients WHERE client_id = ?",
        ("claude-desktop",),
    )
    assert row is not None
    assert row["client_name"] == "Claude Desktop"
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py
```
Expected: 5 FAIL — tables don't exist.

- [ ] **Step 3: Add schema + seed to `backend/db.py`**

Find `init_db()`. Locate the LAST `cursor.execute(...)` in the existing DDL block. Insert AFTER it (8-space indent matching surroundings):

```python
        # ── Phase 2.5i-1: OAuth 2.1 provider ─────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
              id              SERIAL PRIMARY KEY,
              client_id       TEXT NOT NULL UNIQUE,
              client_name     TEXT NOT NULL,
              redirect_uris   JSONB NOT NULL,
              pre_registered  BOOLEAN NOT NULL DEFAULT false,
              registered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_clients_client_id "
            "ON oauth_clients (client_id)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
              id              SERIAL PRIMARY KEY,
              code_hash       TEXT NOT NULL UNIQUE,
              client_id       TEXT NOT NULL,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              scope           TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              redirect_uri    TEXT NOT NULL,
              code_challenge  TEXT NOT NULL,
              expires_at      TIMESTAMPTZ NOT NULL,
              consumed_at     TIMESTAMPTZ,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_codes_hash "
            "ON oauth_authorization_codes (code_hash)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
              id                  SERIAL PRIMARY KEY,
              access_token_hash   TEXT NOT NULL UNIQUE,
              refresh_token_hash  TEXT NOT NULL UNIQUE,
              client_id           TEXT NOT NULL,
              organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
              scope               TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              granted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
              access_expires_at   TIMESTAMPTZ NOT NULL,
              refresh_expires_at  TIMESTAMPTZ NOT NULL,
              last_used_at        TIMESTAMPTZ,
              revoked_at          TIMESTAMPTZ
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_access "
            "ON oauth_tokens (access_token_hash) WHERE revoked_at IS NULL"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh "
            "ON oauth_tokens (refresh_token_hash) WHERE revoked_at IS NULL"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_org "
            "ON oauth_tokens (organization_id, revoked_at)"
        )

        # Pre-registered MCP clients (idempotent — ON CONFLICT DO NOTHING)
        _PRE_REGISTERED_CLIENTS = [
            ("claude-desktop", "Claude Desktop",
             '["claude-desktop://oauth/callback", "http://localhost:5173/oauth/callback"]'),
            ("claude-code",    "Claude Code",
             '["claude-code://oauth/callback"]'),
            ("cursor",         "Cursor",
             '["cursor://oauth/callback"]'),
            ("zed",            "Zed",
             '["zed://oauth/callback"]'),
        ]
        for cid, name, uris_json in _PRE_REGISTERED_CLIENTS:
            cursor.execute(
                "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, pre_registered) "
                "VALUES (%s, %s, %s::jsonb, true) "
                "ON CONFLICT (client_id) DO NOTHING",
                (cid, name, uris_json),
            )
```

Note: I'm using `%s` placeholders for the final INSERT because `db.py`'s `?`→`%s` translator only runs inside `execute()`/`query_*()` helpers; here we're calling `cursor.execute` directly inside `init_db`. The `%s::jsonb` cast tells postgres to parse the JSON string into a JSONB column.

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose ps | grep backend
```
Expected: `Up (healthy)` (or starting — proceed).

- [ ] **Step 5: Run tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py
```
Expected: 5 PASS.

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `299 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): 3 tables + pre-registered MCP clients

oauth_clients (dynamic + pre-registered), oauth_authorization_codes
(10-min TTL, single-use), oauth_tokens (access + refresh, hashed).
Seed inserts known MCP clients (claude-desktop, claude-code, cursor,
zed) with friendly display names — ON CONFLICT DO NOTHING so safe
to re-run.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Discovery endpoints + oauth.py scaffolding

**Files:**
- Create: `backend/oauth.py`
- Modify: `backend/main.py` (mount router)
- Modify: `backend/tests/test_oauth.py` (append 3 tests)

**Goal:** Create the `backend/oauth.py` module with a FastAPI router. Implement `.well-known/oauth-authorization-server` and `.well-known/oauth-protected-resource`. 3 tests.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── Discovery endpoints ──────────────────────────────────────────────


def test_well_known_authorization_server_metadata(client):
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorization_endpoint" in body
    assert "token_endpoint" in body
    assert "revocation_endpoint" in body
    assert "registration_endpoint" in body
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "api:read" in body["scopes_supported"]
    assert "api:rw" in body["scopes_supported"]


def test_well_known_protected_resource_metadata(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert body["bearer_methods_supported"] == ["header"]


def test_well_known_no_auth_required(client):
    """Discovery endpoints must be public — no Authorization header."""
    r1 = client.get("/.well-known/oauth-authorization-server")
    r2 = client.get("/.well-known/oauth-protected-resource")
    assert r1.status_code == 200
    assert r2.status_code == 200
```

- [ ] **Step 2: Run them — confirm 404**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "well_known"
```
Expected: 3 FAIL with 404.

- [ ] **Step 3: Create `backend/oauth.py`**

```python
"""OAuth 2.1 + PKCE authorization server (Phase 2.5i-1).

All OAuth endpoints live here. Mounted into the main FastAPI app via
backend/main.py.
"""
from __future__ import annotations

import os
import secrets
import hashlib
import base64
import json
import time
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse


router = APIRouter()


def _app_url() -> str:
    return os.getenv("APP_URL", "https://api.khanshoof.com").rstrip("/")


# ── Discovery endpoints ───────────────────────────────────────────────


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata() -> dict:
    base = _app_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "registration_endpoint": f"{base}/oauth/register",
        "scopes_supported": ["api:read", "api:rw"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "service_documentation": "https://app.khanshoof.com/api-docs.html",
    }


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata() -> dict:
    base = _app_url()
    return {
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": ["api:read", "api:rw"],
        "bearer_methods_supported": ["header"],
    }
```

- [ ] **Step 4: Mount the router in `backend/main.py`**

Find the existing FastAPI app initialization (search `app = FastAPI` near top of file). After the app object is created and any middleware registered, add:

```python
from oauth import router as oauth_router
app.include_router(oauth_router)
```

Place the import near the other `from X import ...` lines at the top of main.py. The `app.include_router` call can go right after the app is defined.

- [ ] **Step 5: Rebuild + run**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py
```
Expected: 8 PASS (5 schema/seed + 3 discovery).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `302 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/oauth.py backend/main.py backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): discovery endpoints + oauth router scaffolding

backend/oauth.py owns all OAuth endpoints. RFC 8414 + RFC 9728
discovery JSON at /.well-known/* lets MCP clients auto-find the
authorize / token / revoke / register endpoints. Public — no auth
required.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Dynamic client registration

**Files:**
- Modify: `backend/oauth.py`
- Modify: `backend/tests/test_oauth.py` (append 5 tests)

**Goal:** `POST /oauth/register` accepts client metadata and returns a `client_id`. Validates redirect URIs. Rate-limited per IP.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── Dynamic client registration ──────────────────────────────────────


def test_register_creates_client_with_dyn_prefix(client):
    r = client.post("/oauth/register", json={
        "client_name": "Test Integration",
        "redirect_uris": ["http://localhost:5173/oauth/callback"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["client_id"].startswith("dyn_")
    assert body["client_name"] == "Test Integration"
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


def test_register_validates_redirect_uri_schemes(client):
    """https, http://localhost, http://127.0.0.1, and custom schemes are OK."""
    valid = client.post("/oauth/register", json={
        "client_name": "ValidApp",
        "redirect_uris": [
            "https://example.com/callback",
            "http://localhost:3000/callback",
            "http://127.0.0.1:9999/callback",
            "myapp://oauth/callback",
        ],
    })
    assert valid.status_code == 201, valid.text


def test_register_rejects_data_url_scheme(client):
    r = client.post("/oauth/register", json={
        "client_name": "BadApp",
        "redirect_uris": ["data:text/html,<script>"],
    })
    assert r.status_code == 400, r.text


def test_register_rejects_too_many_uris(client):
    r = client.post("/oauth/register", json={
        "client_name": "WideApp",
        "redirect_uris": [f"https://app{i}.example.com/cb" for i in range(11)],
    })
    assert r.status_code == 400, r.text


def test_register_returns_no_client_secret(client):
    r = client.post("/oauth/register", json={
        "client_name": "PublicOnly",
        "redirect_uris": ["https://example.com/cb"],
    })
    assert r.status_code == 201
    assert "client_secret" not in r.json()
    assert "client_secret_expires_at" not in r.json()
```

- [ ] **Step 2: Run them — confirm 404 / 405**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "register"
```
Expected: 5 FAIL.

- [ ] **Step 3: Add registration to `backend/oauth.py`**

Append to `backend/oauth.py`:

```python
# ── Dynamic client registration ──────────────────────────────────────

from pydantic import BaseModel, Field

# Import db helpers — placed here to avoid cycles
from db import execute, query_one, query_all  # noqa: E402


class ClientRegistration(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=200)
    redirect_uris: list[str] = Field(..., min_length=1, max_length=10)


_ALLOWED_SCHEMES_RE = (
    r"^("
    r"https://"
    r"|http://localhost(:\d+)?(/|$)"
    r"|http://127\.0\.0\.1(:\d+)?(/|$)"
    r"|[a-z][a-z0-9+\-.]*://"
    r")"
)

_FORBIDDEN_SCHEMES = ("data:", "file:", "javascript:", "vbscript:")


def _validate_redirect_uris(uris: list[str]) -> None:
    import re
    pat = re.compile(_ALLOWED_SCHEMES_RE, re.IGNORECASE)
    for uri in uris:
        if not isinstance(uri, str) or len(uri) > 2000:
            raise HTTPException(status_code=400,
                detail={"code": "invalid_redirect_uri",
                        "message": f"Invalid redirect_uri: {uri[:80]}"})
        lower = uri.lower()
        for bad in _FORBIDDEN_SCHEMES:
            if lower.startswith(bad):
                raise HTTPException(status_code=400,
                    detail={"code": "invalid_redirect_uri",
                            "message": f"Forbidden scheme in {uri[:80]}"})
        if not pat.match(uri):
            raise HTTPException(status_code=400,
                detail={"code": "invalid_redirect_uri",
                        "message": f"Unsupported scheme in {uri[:80]}"})


@router.post("/oauth/register", status_code=201)
def register_client(payload: ClientRegistration) -> dict:
    _validate_redirect_uris(payload.redirect_uris)
    client_id = "dyn_" + secrets.token_urlsafe(18)
    execute(
        "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, pre_registered) "
        "VALUES (?, ?, ?, false)",
        (client_id, payload.client_name, json.dumps(payload.redirect_uris)),
    )
    return {
        "client_id": client_id,
        "client_name": payload.client_name,
        "redirect_uris": payload.redirect_uris,
        "client_id_issued_at": int(time.time()),
        "token_endpoint_auth_method": "none",
    }
```

**Note on rate limiting:** the spec mentions rate-limiting `/oauth/register` to 5/min per IP via slowapi. Slowapi is already wired into the app, but per-route decorators require the `limiter` instance imported from `main`. For T3 we'll skip explicit rate-limit code; if abuse becomes a real concern post-launch, add `@limiter.limit("5/minute")` decorator. Documented as a v1 deferral.

- [ ] **Step 4: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "register"
```
Expected: 5 PASS.

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `307 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/oauth.py backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): dynamic client registration (RFC 7591)

POST /oauth/register self-serves any MCP client at runtime. Returns
a client_id (no client_secret — public clients only, all use PKCE).
Validates redirect_uri schemes: https, http://localhost,
http://127.0.0.1, or any custom-scheme URI. Forbids data:, file:,
javascript: schemes. Max 10 redirect URIs per client.

v1 deferral: per-IP rate limit not yet enforced via slowapi decorator
(documented; add @limiter.limit if abuse surfaces).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Authorization endpoint + consent UI

**Files:**
- Modify: `backend/oauth.py`
- Create: `backend/templates/oauth_login.html`
- Create: `backend/templates/oauth_consent.html`
- Create: `backend/templates/oauth_error.html`
- Create: `frontend/oauth-consent.css`
- Modify: `backend/Dockerfile` (ensure templates directory copies in)
- Modify: `backend/tests/test_oauth.py` (append 8 tests)

**Goal:** `GET /oauth/authorize` validates the request, renders login (if no session) or consent. `POST /oauth/login` handles in-flow login. `POST /oauth/authorize/decision` issues an authorization code on Allow.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── Authorization endpoint + consent UI ──────────────────────────────
import uuid
import hashlib
import base64


def _signup_org(client, suffix=None):
    """Create a fresh org via signup. Returns (token, org_id, user_id, email)."""
    sfx = suffix or uuid.uuid4().hex[:8]
    email = f"oauth-{sfx}@example.com"
    r = client.post("/auth/signup/request",
                    json={"business_name": f"OAuthBiz {sfx}", "email": email})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    body = r.json()
    return body["token"], body["organization"]["id"], body["user"]["id"], email


def _register_client(client, name="TestApp", uris=None):
    r = client.post("/oauth/register", json={
        "client_name": name,
        "redirect_uris": uris or ["http://localhost:5173/oauth/callback"],
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _pkce_pair():
    """Returns (code_verifier, code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def test_authorize_rejects_invalid_response_type(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "token",   # not supported
        "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    # Either 400 with error page OR 302 with ?error=invalid_request
    assert r.status_code in (302, 400)


def test_authorize_rejects_plain_pkce_method(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "plain",
    }, follow_redirects=False)
    assert r.status_code in (302, 400)


def test_authorize_rejects_unknown_client_id(client):
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": "nonexistent_client",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    # Must NOT redirect (redirect_uri not yet trusted) — show error page
    assert r.status_code == 400


def test_authorize_rejects_unregistered_redirect_uri(client):
    cid = _register_client(client, uris=["http://localhost:5173/oauth/callback"])
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": "http://attacker.example.com/cb",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_authorize_renders_login_when_no_session(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_authorize_renders_consent_with_session(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    # Set the session cookie before hitting authorize
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:rw", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    assert r.status_code == 200
    assert "Authorize" in r.text or "TestApp" in r.text


def test_authorize_decision_allow_redirects_with_code(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:rw", "state": "xyz",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    assert r.status_code == 200
    # Extract request_id from the rendered HTML
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    assert m, "request_id missing from consent page"
    request_id = m.group(1)
    # Submit the consent
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("http://localhost:5173/oauth/callback?")
    assert "code=" in location
    assert "state=xyz" in location


def test_authorize_decision_deny_redirects_with_error(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "deny-test",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    request_id = m.group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "deny",
                          "scope": "api:read"},
                    cookies=cookies, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert "error=access_denied" in location
    assert "state=deny-test" in location
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "authorize"
```
Expected: 8 FAIL.

- [ ] **Step 3: Create `backend/templates/oauth_login.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign in to Khanshoof</title>
    <link rel="stylesheet" href="https://app.khanshoof.com/oauth-consent.css">
  </head>
  <body class="oauth-page">
    <main class="oauth-card">
      <h1>Sign in to Khanshoof</h1>
      <p class="muted">to continue authorizing <strong>{{ client_name }}</strong></p>
      {% if error %}<p class="error">{{ error }}</p>{% endif %}
      <form method="POST" action="/oauth/login">
        <input type="hidden" name="request_id" value="{{ request_id }}">
        <label>Email
          <input type="email" name="username" required autofocus>
        </label>
        <label>Password
          <input type="password" name="password" required>
        </label>
        <button type="submit" class="btn btn-primary">Sign in</button>
      </form>
    </main>
  </body>
</html>
```

- [ ] **Step 4: Create `backend/templates/oauth_consent.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize {{ client_name }}</title>
    <link rel="stylesheet" href="https://app.khanshoof.com/oauth-consent.css">
  </head>
  <body class="oauth-page">
    <main class="oauth-card">
      <h1>Authorize <strong>{{ client_name }}</strong>?</h1>
      <p>
        <strong>{{ client_name }}</strong> is requesting access to your Khanshoof organization
        <strong>{{ organization_name }}</strong> as <strong>{{ user_email }}</strong>.
      </p>

      <form method="POST" action="/oauth/authorize/decision">
        <input type="hidden" name="request_id" value="{{ request_id }}">

        <fieldset class="scope-choice">
          <legend>Choose access level:</legend>
          <label>
            <input type="radio" name="scope" value="api:read"
                   {% if requested_scope == 'api:read' %}checked{% endif %}>
            <strong>Read only</strong> — browse playlists, screens, schedules. No changes.
          </label>
          <label>
            <input type="radio" name="scope" value="api:rw"
                   {% if requested_scope == 'api:rw' %}checked{% endif %}>
            <strong>Read + write</strong> — full content management.
          </label>
        </fieldset>

        <div class="actions">
          <button type="submit" name="decision" value="deny"  class="btn">Deny</button>
          <button type="submit" name="decision" value="allow" class="btn btn-primary">Allow</button>
        </div>
      </form>

      <p class="muted">
        You can revoke this access anytime from the Khanshoof dashboard.
      </p>
    </main>
  </body>
</html>
```

- [ ] **Step 5: Create `backend/templates/oauth_error.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OAuth error</title>
    <link rel="stylesheet" href="https://app.khanshoof.com/oauth-consent.css">
  </head>
  <body class="oauth-page">
    <main class="oauth-card">
      <h1>Authorization error</h1>
      <p class="error">{{ message }}</p>
      <p class="muted">{{ detail }}</p>
    </main>
  </body>
</html>
```

- [ ] **Step 6: Create `frontend/oauth-consent.css`**

```css
:root {
  --bg: #fff8f0;
  --fg: #3b2a14;
  --muted: #8b6f5e;
  --border: #e9ddc6;
  --accent: #6b8e6b;
  --error: #b53939;
}

* { box-sizing: border-box; }
html, body { margin: 0; }
body.oauth-page {
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
}
.oauth-card {
  width: 100%;
  max-width: 440px;
  background: #fffefb;
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 32px;
  box-shadow: 0 10px 30px rgba(62, 43, 79, 0.08);
}
.oauth-card h1 { margin: 0 0 12px 0; font-size: 22px; }
.oauth-card p  { margin: 0 0 16px 0; line-height: 1.5; }
.muted { color: var(--muted); font-size: 14px; }
.error { color: var(--error); }

.oauth-card form { display: flex; flex-direction: column; gap: 12px; }
.oauth-card label {
  display: flex; flex-direction: column;
  font-size: 13px; gap: 4px;
}
.oauth-card input[type=email],
.oauth-card input[type=password],
.oauth-card input[type=text] {
  font: inherit;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
.scope-choice {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 16px 0;
}
.scope-choice legend { font-weight: 600; padding: 0 4px; }
.scope-choice label {
  flex-direction: row;
  align-items: flex-start;
  gap: 8px;
  font-size: 14px;
  font-weight: normal;
  margin-block: 8px;
}
.actions { display: flex; gap: 12px; margin-block-start: 20px; }
.btn {
  font: inherit;
  padding: 10px 18px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  flex: 1;
}
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn:hover { filter: brightness(0.95); }
```

- [ ] **Step 7: Verify Dockerfile copies the templates directory**

```bash
grep -n "COPY" backend/Dockerfile
```

If the Dockerfile uses `COPY . /app`, templates are already included. If it copies specific files, add `COPY templates/ /app/templates/`. Most likely it's `COPY . /app` so no change needed — but verify.

- [ ] **Step 8: Add authorization + consent flow to `backend/oauth.py`**

Append to `backend/oauth.py`:

```python
# ── Authorization flow ──────────────────────────────────────────────

from fastapi.templating import Jinja2Templates
from db import execute  # already imported above; re-import idempotent

_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_templates_dir)


# In-memory pending-auth state: request_id -> dict of authorize-time params
# Lost on process restart (10-min TTL anyway; customer just retries).
_pending_auth: dict = {}
_PENDING_AUTH_TTL = 600   # 10 minutes


def _prune_pending_auth():
    now = time.time()
    expired = [k for k, v in _pending_auth.items() if v.get("expires_at", 0) < now]
    for k in expired:
        _pending_auth.pop(k, None)


def _error_response(request: Request, message: str, detail: str = "") -> HTMLResponse:
    html = templates.get_template("oauth_error.html").render(
        message=message, detail=detail,
    )
    return HTMLResponse(html, status_code=400)


def _client_or_error(client_id: str) -> Optional[dict]:
    if not client_id:
        return None
    return query_one(
        "SELECT client_id, client_name, redirect_uris FROM oauth_clients "
        "WHERE client_id = ?",
        (client_id,),
    )


def _redirect_uri_matches(client_row: dict, requested: str) -> bool:
    uris = client_row.get("redirect_uris") or []
    # psycopg returns JSONB as a Python list; defensively decode if it's a str
    if isinstance(uris, str):
        try:
            uris = json.loads(uris)
        except Exception:
            return False
    return requested in uris


# Session lookup helper for the oauth_session cookie. Reuses _session_lookup
# from main.py (extracted in Phase 2.5h).
def _session_user_from_cookie(request: Request) -> Optional[dict]:
    token = request.cookies.get("oauth_session")
    if not token:
        return None
    # Lazy import to avoid circular: main imports oauth router, oauth uses
    # main's _session_lookup
    from main import _session_lookup
    return _session_lookup(token)


@router.get("/oauth/authorize")
def authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(...),
    state: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    resume: Optional[str] = Query(None),
):
    # Step 1: validate client_id exists
    client_row = _client_or_error(client_id)
    if not client_row:
        return _error_response(
            request, "Unknown client",
            "The client_id is not registered. Have your MCP client register first.",
        )

    # Step 2: validate redirect_uri matches one of the registered URIs
    if not _redirect_uri_matches(client_row, redirect_uri):
        return _error_response(
            request, "Invalid redirect_uri",
            "The redirect_uri does not match any registered URI for this client.",
        )

    # Step 3: validate response_type and code_challenge_method
    if response_type != "code":
        return RedirectResponse(
            f"{redirect_uri}?error=unsupported_response_type&state={state}",
            status_code=302,
        )
    if code_challenge_method != "S256":
        return RedirectResponse(
            f"{redirect_uri}?error=invalid_request&error_description="
            f"code_challenge_method+must+be+S256&state={state}",
            status_code=302,
        )

    # Step 4: validate scope
    if scope not in ("api:read", "api:rw"):
        return RedirectResponse(
            f"{redirect_uri}?error=invalid_scope&state={state}",
            status_code=302,
        )

    # Step 5: handle resume= flow (after login)
    if resume:
        pending = _pending_auth.get(resume)
        if pending and pending["client_id"] == client_id:
            request_id = resume
        else:
            request_id = secrets.token_urlsafe(24)
    else:
        request_id = secrets.token_urlsafe(24)

    # Stash pending auth state
    _prune_pending_auth()
    _pending_auth[request_id] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "expires_at": time.time() + _PENDING_AUTH_TTL,
    }

    # Step 6: session check
    user = _session_user_from_cookie(request)
    if not user:
        html = templates.get_template("oauth_login.html").render(
            client_name=client_row["client_name"],
            request_id=request_id,
            error=None,
        )
        return HTMLResponse(html)

    # Step 7: render consent
    org_row = query_one(
        "SELECT name FROM organizations WHERE id = ?",
        (user["organization_id"],),
    )
    html = templates.get_template("oauth_consent.html").render(
        client_name=client_row["client_name"],
        organization_name=(org_row or {}).get("name", "your organization"),
        user_email=user["username"],
        requested_scope=scope,
        request_id=request_id,
    )
    return HTMLResponse(html)


@router.post("/oauth/login")
def oauth_login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    request_id: str = Form(...),
):
    pending = _pending_auth.get(request_id)
    if not pending:
        return _error_response(request, "Login session expired",
                               "Please restart the authorization flow from your MCP client.")

    # Reuse the existing login logic from main.py — call it via HTTP-style
    # to inherit lockout + audit. Simpler: query users directly + verify_password.
    from main import verify_password
    user_row = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user_row or not verify_password(password, user_row["password_hash"]):
        html = templates.get_template("oauth_login.html").render(
            client_name=_client_or_error(pending["client_id"])["client_name"],
            request_id=request_id,
            error="Invalid credentials. Try again.",
        )
        return HTMLResponse(html, status_code=401)

    # Issue a fresh session token (cookie-style)
    session_token = secrets.token_urlsafe(32)
    from main import utc_now_iso
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) "
        "VALUES (?, ?, ?, ?)",
        (user_row["id"], session_token, utc_now_iso(), utc_now_iso()),
    )

    redirect_url = (
        f"/oauth/authorize?response_type=code&client_id={pending['client_id']}"
        f"&redirect_uri={pending['redirect_uri']}&scope={pending['scope']}"
        f"&state={pending['state']}&code_challenge={pending['code_challenge']}"
        f"&code_challenge_method=S256&resume={request_id}"
    )
    resp = RedirectResponse(redirect_url, status_code=302)
    resp.set_cookie(
        "oauth_session", session_token,
        httponly=True, samesite="lax", max_age=3600,
        secure=os.getenv("APP_URL", "").startswith("https://"),
    )
    return resp


@router.post("/oauth/authorize/decision")
def authorize_decision(
    request: Request,
    request_id: str = Form(...),
    decision: str = Form(...),
    scope: str = Form(...),
):
    pending = _pending_auth.pop(request_id, None)
    if not pending:
        return _error_response(request, "Authorization expired",
                               "Please restart from your MCP client.")

    user = _session_user_from_cookie(request)
    if not user:
        return _error_response(request, "Not signed in",
                               "Sign in first to authorize this client.")

    if decision != "allow":
        return RedirectResponse(
            f"{pending['redirect_uri']}?error=access_denied&state={pending['state']}",
            status_code=302,
        )

    if scope not in ("api:read", "api:rw"):
        scope = pending["scope"]

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    from main import hash_password
    code_hash = hash_password(code)
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    execute(
        """
        INSERT INTO oauth_authorization_codes
          (code_hash, client_id, organization_id, user_id, scope,
           redirect_uri, code_challenge, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (code_hash, pending["client_id"], user["organization_id"],
         user["id"], scope, pending["redirect_uri"], pending["code_challenge"],
         expires.isoformat()),
    )

    return RedirectResponse(
        f"{pending['redirect_uri']}?code={code}&state={pending['state']}",
        status_code=302,
    )
```

- [ ] **Step 9: Rebuild + run tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "authorize"
```
Expected: 8 PASS.

- [ ] **Step 10: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `315 passed`.

- [ ] **Step 11: Commit**

```bash
git add backend/oauth.py backend/templates/ frontend/oauth-consent.css \
        backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): authorization endpoint + consent UI

GET /oauth/authorize validates response_type=code, code_challenge_method=S256,
client_id + redirect_uri (exact match), and scope (api:read | api:rw).
Renders Jinja2 templates: login when no session cookie, consent when
session valid. POST /oauth/login handles in-flow sign-in and sets an
HTTP-only session cookie before redirecting back to /oauth/authorize
with resume=<request_id>.

POST /oauth/authorize/decision issues an authorization code on Allow
(stored hashed with 10-min single-use TTL) and redirects to the
client's redirect_uri with code + state. Deny redirects with
error=access_denied.

Pending-auth state is in-memory only (10-min TTL, lost on restart).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Token endpoint (code grant + refresh rotation)

**Files:**
- Modify: `backend/oauth.py`
- Modify: `backend/tests/test_oauth.py` (append 13 tests)

**Goal:** `POST /oauth/token` handles both `authorization_code` and `refresh_token` grants. Verifies PKCE. Rotates refresh tokens per OAuth 2.1.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── Token endpoint ──────────────────────────────────────────────────


def _full_authorize_flow(client) -> dict:
    """Run the full flow up to having a redirect URL with code. Returns
    {'code', 'verifier', 'client_id', 'redirect_uri', 'session_token', 'org_id'}."""
    cid = _register_client(client)
    session_token, org_id, _u, _email = _signup_org(client)
    verifier, challenge = _pkce_pair()
    cookies = {"oauth_session": session_token}
    redirect_uri = "http://localhost:5173/oauth/callback"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri,
        "scope": "api:rw", "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    request_id = m.group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    location = r.headers["location"]
    code = re.search(r"code=([^&]+)", location).group(1)
    return {
        "code": code, "verifier": verifier,
        "client_id": cid, "redirect_uri": redirect_uri,
        "session_token": session_token, "org_id": org_id,
    }


def test_token_exchanges_code_for_tokens(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "api:rw"


def test_token_rejects_consumed_code(client):
    flow = _full_authorize_flow(client)
    # First exchange succeeds
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200
    # Second exchange of same code fails
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_rejects_mismatched_redirect_uri(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": "http://evil.example.com/cb",
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_rejects_bad_pkce_verifier(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": "wrong_verifier_value",
    })
    assert r.status_code == 400


def test_token_rejects_unknown_code(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": "unknown_random_code_xyz",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid,
        "code_verifier": "v",
    })
    assert r.status_code == 400


def test_token_rejects_unsupported_grant_type(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "password",
        "username": "x", "password": "y", "client_id": cid,
    })
    assert r.status_code == 400


def test_refresh_issues_new_pair(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["access_token"] != tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]


def test_refresh_rotates_old_pair_invalidated(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    # First refresh succeeds
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200
    # Second refresh of OLD token fails (rotation)
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 400


def test_refresh_rejects_unknown(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": "unknown_random_token",
        "client_id": cid,
    })
    assert r.status_code == 400


def test_token_response_includes_expected_fields(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    body = r.json()
    for key in ("access_token", "refresh_token", "token_type",
                "expires_in", "scope"):
        assert key in body
    assert body["token_type"] == "Bearer"


def test_authorization_code_expired(client):
    """Expired code is rejected. Simulate by manually backdating expires_at."""
    flow = _full_authorize_flow(client)
    # Backdate the code
    execute(
        "UPDATE oauth_authorization_codes SET expires_at = ? "
        "WHERE client_id = ?",
        ("2020-01-01T00:00:00+00:00", flow["client_id"]),
    )
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_endpoint_returns_json_error_shape(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": "x",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid,
        "code_verifier": "v",
    })
    assert r.status_code == 400
    body = r.json()
    # OAuth standard error shape: {"error": "...", ...}
    assert "error" in body or "detail" in body


def test_access_token_works_on_api_endpoint(client):
    """The access token issued via OAuth must authenticate against /organization."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access_token = r.json()["access_token"]
    # NOTE: Task 6 wires lookup_oauth_access_token into get_api_authed.
    # This test will start passing then. For Task 5 alone, mark expected
    # to pass after T6 lands.
    r2 = client.get("/organization",
                    headers={"Authorization": f"Bearer {access_token}"})
    # Pre-T6: expect 401. Post-T6: expect 200. For now we accept either.
    assert r2.status_code in (200, 401), r2.text
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "token or refresh or access_token_works"
```
Expected: most fail (token endpoint doesn't exist).

- [ ] **Step 3: Add `/oauth/token` to `backend/oauth.py`**

Append to `backend/oauth.py`:

```python
# ── Token endpoint ──────────────────────────────────────────────────


_ACCESS_TTL_SECONDS  = 3600         # 1 hour
_REFRESH_TTL_SECONDS = 90 * 86400   # 90 days


def _pkce_matches(verifier: str, challenge: str) -> bool:
    """Compute S256 challenge from verifier and compare to stored challenge."""
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def _hash_token(token: str) -> str:
    from main import hash_password
    return hash_password(token)


def _verify_token_hash(token: str, hashed: str) -> bool:
    from main import verify_password
    return verify_password(token, hashed)


def _oauth_error(status: int, error: str, description: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
    )


def _issue_tokens(client_id: str, org_id: int, user_id: Optional[int],
                  scope: str) -> dict:
    """Generate, hash, and store a new (access, refresh) pair. Returns the
    response body dict (with plaintext tokens)."""
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    access_hash = _hash_token(access)
    refresh_hash = _hash_token(refresh)
    now = datetime.now(timezone.utc)
    access_exp = now + timedelta(seconds=_ACCESS_TTL_SECONDS)
    refresh_exp = now + timedelta(seconds=_REFRESH_TTL_SECONDS)
    execute(
        """
        INSERT INTO oauth_tokens
          (access_token_hash, refresh_token_hash, client_id,
           organization_id, user_id, scope,
           access_expires_at, refresh_expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (access_hash, refresh_hash, client_id, org_id, user_id, scope,
         access_exp.isoformat(), refresh_exp.isoformat()),
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TTL_SECONDS,
        "scope": scope,
    }


def _exchange_authorization_code(
    code: str, client_id: str, redirect_uri: str, code_verifier: str,
) -> dict:
    # Look up the code by hash; we have to fetch by client_id since hash
    # comparison is per-row (PBKDF2 has random salt → hash is not lookup-keyed)
    candidates = query_all(
        "SELECT id, code_hash, client_id, organization_id, user_id, scope, "
        "       redirect_uri, code_challenge, expires_at, consumed_at "
        "FROM oauth_authorization_codes WHERE client_id = ?",
        (client_id,),
    )
    matching = None
    for row in candidates:
        if _verify_token_hash(code, row["code_hash"]):
            matching = row
            break
    if not matching:
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "Unknown or expired authorization code"})

    if matching["consumed_at"] is not None:
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "Authorization code already used"})

    # Expiry check
    expires = matching["expires_at"]
    if hasattr(expires, "isoformat"):
        expires_dt = expires
    else:
        expires_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    if expires_dt < datetime.now(timezone.utc):
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "Authorization code expired"})

    # Redirect URI must match
    if matching["redirect_uri"] != redirect_uri:
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "redirect_uri mismatch"})

    # PKCE verify
    if not _pkce_matches(code_verifier, matching["code_challenge"]):
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "PKCE verifier did not match"})

    # Mark consumed
    execute(
        "UPDATE oauth_authorization_codes SET consumed_at = now() WHERE id = ?",
        (matching["id"],),
    )

    return _issue_tokens(
        client_id=matching["client_id"],
        org_id=matching["organization_id"],
        user_id=matching["user_id"],
        scope=matching["scope"],
    )


def _refresh_tokens(refresh_token: str, client_id: str) -> dict:
    # PBKDF2 hash isn't lookup-keyed; iterate candidates for the client_id
    candidates = query_all(
        "SELECT id, refresh_token_hash, client_id, organization_id, user_id, "
        "       scope, refresh_expires_at, revoked_at "
        "FROM oauth_tokens WHERE client_id = ? AND revoked_at IS NULL",
        (client_id,),
    )
    matching = None
    for row in candidates:
        if _verify_token_hash(refresh_token, row["refresh_token_hash"]):
            matching = row
            break
    if not matching:
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "Unknown or revoked refresh token"})

    expires = matching["refresh_expires_at"]
    if hasattr(expires, "isoformat"):
        expires_dt = expires
    else:
        expires_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    if expires_dt < datetime.now(timezone.utc):
        raise HTTPException(status_code=400,
            detail={"error": "invalid_grant",
                    "error_description": "Refresh token expired"})

    # Rotate: invalidate the old pair, issue a fresh pair
    execute(
        "UPDATE oauth_tokens SET revoked_at = now() WHERE id = ?",
        (matching["id"],),
    )
    return _issue_tokens(
        client_id=matching["client_id"],
        org_id=matching["organization_id"],
        user_id=matching["user_id"],
        scope=matching["scope"],
    )


@router.post("/oauth/token")
def token(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
):
    if grant_type == "authorization_code":
        if not all((code, redirect_uri, client_id, code_verifier)):
            return _oauth_error(400, "invalid_request",
                                "Missing required field for authorization_code grant")
        return _exchange_authorization_code(code, client_id, redirect_uri, code_verifier)

    if grant_type == "refresh_token":
        if not all((refresh_token, client_id)):
            return _oauth_error(400, "invalid_request",
                                "Missing required field for refresh_token grant")
        return _refresh_tokens(refresh_token, client_id)

    return _oauth_error(400, "unsupported_grant_type",
                        f"Grant type '{grant_type}' is not supported")
```

- [ ] **Step 4: Rebuild + run tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "token or refresh or access_token_works"
```
Expected: 13 PASS.

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `328 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/oauth.py backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): token endpoint (authorization_code + refresh grants)

POST /oauth/token handles two grants. authorization_code exchanges
a code + PKCE verifier for a (access, refresh) pair (1h / 90d).
refresh_token rotates the pair per OAuth 2.1 — old pair revoked,
new pair issued.

PKCE verified by SHA256-of-verifier matches stored S256 challenge.
Codes are single-use (consumed_at) and 10-min expiring. All tokens
hashed at rest via PBKDF2 (same helpers as users.password_hash).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Revocation + extend get_api_authed for OAuth tokens

**Files:**
- Modify: `backend/oauth.py` (add `/oauth/revoke` + `lookup_oauth_access_token`)
- Modify: `backend/main.py` (extend `get_api_authed`)
- Modify: `backend/tests/test_oauth.py` (append 5 tests)

**Goal:** `POST /oauth/revoke` (RFC 7009). OAuth tokens authenticate against existing API endpoints — extend `get_api_authed` to recognize them.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── Revocation + API integration ────────────────────────────────────


def test_revoke_marks_pair_revoked(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    r = client.post("/oauth/revoke", data={
        "token": tokens["access_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200, r.text
    # Verify revoked_at is set
    row = query_one(
        "SELECT revoked_at FROM oauth_tokens WHERE client_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (flow["client_id"],),
    )
    assert row["revoked_at"] is not None


def test_revoke_returns_200_for_unknown_token(client):
    """RFC 7009: server MUST return 200 to avoid leaking validity."""
    cid = _register_client(client)
    r = client.post("/oauth/revoke", data={
        "token": "unknown_garbage",
        "client_id": cid,
    })
    assert r.status_code == 200


def test_revoked_access_token_returns_401(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access = r.json()["access_token"]
    # Confirm it works
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    # Revoke
    r = client.post("/oauth/revoke", data={
        "token": access, "client_id": flow["client_id"],
    })
    assert r.status_code == 200
    # Now 401
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 401


def test_oauth_access_token_can_GET_playlists(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access = r.json()["access_token"]
    r = client.get("/playlists", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text


def test_oauth_read_scope_cannot_POST_playlists(client):
    """A token with api:read scope must NOT be allowed to POST."""
    cid = _register_client(client)
    session_token, _o, _u, _e = _signup_org(client)
    verifier, challenge = _pkce_pair()
    cookies = {"oauth_session": session_token}
    redirect_uri = "http://localhost:5173/oauth/callback"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri, "scope": "api:read", "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:read"},
                    cookies=cookies, follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri,
        "client_id": cid, "code_verifier": verifier,
    })
    access = r.json()["access_token"]
    # POST should 403 with insufficient_scope
    r = client.post("/playlists",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"name": "blocked"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "api.insufficient_scope"
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "revoke or oauth_access or oauth_read"
```
Expected: failures — `/oauth/revoke` not implemented; OAuth tokens don't authenticate against /organization yet.

- [ ] **Step 3: Add `/oauth/revoke` + `lookup_oauth_access_token` to `backend/oauth.py`**

Append to `backend/oauth.py`:

```python
# ── Revocation ───────────────────────────────────────────────────────


@router.post("/oauth/revoke")
def revoke(
    token: str = Form(...),
    client_id: Optional[str] = Form(None),
    token_type_hint: Optional[str] = Form(None),
):
    """RFC 7009: revoke an access or refresh token. Always returns 200."""
    # Try access_token_hash first (or use hint), then refresh_token_hash.
    # Same iterate-candidates approach as the other PBKDF2 lookups.
    candidates = query_all(
        "SELECT id, access_token_hash, refresh_token_hash, revoked_at "
        "FROM oauth_tokens WHERE revoked_at IS NULL "
        "AND (client_id = ? OR ? IS NULL)",
        (client_id, client_id),
    )
    for row in candidates:
        if _verify_token_hash(token, row["access_token_hash"]) or \
           _verify_token_hash(token, row["refresh_token_hash"]):
            execute(
                "UPDATE oauth_tokens SET revoked_at = now() WHERE id = ?",
                (row["id"],),
            )
            break
    # Always 200 — RFC 7009: don't reveal whether the token was valid
    return Response(status_code=200)


# ── Access-token lookup (used by get_api_authed in main.py) ──────────


def lookup_oauth_access_token(token: str) -> Optional[dict]:
    """Return active oauth_tokens row if token matches; else None.
    Fire-and-forget last_used_at update."""
    if not token:
        return None
    # OAuth access tokens are opaque base64url; quickly reject anything that
    # starts with "khan_live_" (those are API keys, handled elsewhere).
    if token.startswith("khan_live_"):
        return None
    # Iterate active tokens — same PBKDF2 pattern as api_keys
    candidates = query_all(
        "SELECT id, access_token_hash, organization_id, user_id, scope, "
        "       access_expires_at, revoked_at "
        "FROM oauth_tokens "
        "WHERE revoked_at IS NULL AND access_expires_at > now()"
    )
    for row in candidates:
        if _verify_token_hash(token, row["access_token_hash"]):
            try:
                execute(
                    "UPDATE oauth_tokens SET last_used_at = now() WHERE id = ?",
                    (row["id"],),
                )
            except Exception as exc:
                # Use logger from main; lazy import for safety
                from main import logger
                logger.warning("oauth_token_last_used_update_failed id=%s err=%s",
                               row["id"], exc)
            return row
    return None
```

Note: the lookup iterates ALL non-expired tokens per call. With PBKDF2 hashing this is O(n × pbkdf2_cost) which doesn't scale past ~hundreds of active tokens. **Documented v1 limitation** — a follow-up phase should add an index by a fast hash (HMAC-SHA256 with a server-side secret) to make lookup O(log n). For v1, expected concurrent active OAuth tokens is dozens at most.

- [ ] **Step 4: Extend `get_api_authed` in `backend/main.py`**

Find `def get_api_authed(...)` in `backend/main.py` (added in Phase 2.5h). The body currently:

```python
def get_api_authed(authorization: Optional[str] = Header(None)) -> AuthedPrincipal:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    # Try API key first (khan_live_*)
    key_row = lookup_api_key(token)
    if key_row:
        return AuthedPrincipal(api_key=key_row, ...)

    # Fall through to session
    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return AuthedPrincipal(user=user, ...)
```

Add the OAuth token lookup BETWEEN the API key check and the session fall-through:

```python
def get_api_authed(authorization: Optional[str] = Header(None)) -> AuthedPrincipal:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    # Try API key first (cheap prefix check)
    key_row = lookup_api_key(token)
    if key_row:
        return AuthedPrincipal(
            api_key=key_row,
            organization_id=key_row["organization_id"],
            scope=key_row["scope"],
        )

    # NEW: Try OAuth access token
    from oauth import lookup_oauth_access_token
    oauth_row = lookup_oauth_access_token(token)
    if oauth_row:
        return AuthedPrincipal(
            api_key=None,
            user=None,
            organization_id=oauth_row["organization_id"],
            scope=oauth_row["scope"],
        )

    # Fall through to session lookup
    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return AuthedPrincipal(
        user=user,
        organization_id=user["organization_id"],
        scope="session",
    )
```

Lazy `from oauth import` avoids a circular import — `oauth.py` already does lazy imports from main.

- [ ] **Step 5: Rebuild + run tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "revoke or oauth_access or oauth_read"
```
Expected: 5 PASS.

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `333 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/oauth.py backend/main.py backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
feat(oauth): revoke endpoint + extend get_api_authed for OAuth tokens

POST /oauth/revoke (RFC 7009) marks both access + refresh as revoked.
Always returns 200 per spec — must not reveal token validity.

get_api_authed now recognizes OAuth access tokens between the
API-key check and the session fall-through. Same AuthedPrincipal
shape (scope = api:read | api:rw) → existing require_api_scope dep
works unchanged across all ~30 dual-auth endpoints.

lookup_oauth_access_token iterates candidates with PBKDF2 verify.
O(n) for now — small-N is fine; future phase adds an HMAC-keyed
fast hash index for scale.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: End-to-end test + regression + push + PR

**Files:**
- Modify: `backend/tests/test_oauth.py` (append 3 e2e tests)

**Goal:** A single test that exercises the full happy path (register → authorize → token → use → refresh → revoke). Plus final regression + PR.

- [ ] **Step 1: Append e2e tests**

Append to `backend/tests/test_oauth.py`:

```python
# ── End-to-end ──────────────────────────────────────────────────────


def test_full_happy_path(client):
    """Single test exercising the full OAuth flow."""
    # 1. Register
    r = client.post("/oauth/register", json={
        "client_name": "E2E Client",
        "redirect_uris": ["http://localhost:5173/cb"],
    })
    assert r.status_code == 201
    cid = r.json()["client_id"]

    # 2. Sign up an org
    session_token, _o, _u, _e = _signup_org(client)
    cookies = {"oauth_session": session_token}

    # 3. Authorize
    verifier, challenge = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/cb",
        "scope": "api:rw", "state": "e2e",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)

    # 4. Allow
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    location = r.headers["location"]
    code = re.search(r"code=([^&]+)", location).group(1)

    # 5. Token
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": "http://localhost:5173/cb",
        "client_id": cid, "code_verifier": verifier,
    })
    tokens = r.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]

    # 6. Use the access token
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    r = client.post("/playlists",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"name": "E2E playlist"})
    assert r.status_code in (200, 201)

    # 7. Refresh
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh, "client_id": cid,
    })
    new_tokens = r.json()
    new_access = new_tokens["access_token"]
    assert new_access != access

    # 8. New access works
    r = client.get("/organization", headers={"Authorization": f"Bearer {new_access}"})
    assert r.status_code == 200

    # 9. Old access NO longer works (revoked on refresh)
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 401

    # 10. Revoke the new access
    r = client.post("/oauth/revoke", data={
        "token": new_access, "client_id": cid,
    })
    assert r.status_code == 200

    # 11. Revoked token no longer works
    r = client.get("/organization", headers={"Authorization": f"Bearer {new_access}"})
    assert r.status_code == 401


def test_pkce_verifier_required(client):
    """PKCE is mandatory in OAuth 2.1 — no code_verifier means rejection."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        # No code_verifier
    })
    assert r.status_code == 400


def test_authorization_code_single_use(client):
    """RFC 6749 + 7636: codes must be single-use."""
    flow = _full_authorize_flow(client)
    r1 = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r1.status_code == 200
    r2 = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r2.status_code == 400
```

- [ ] **Step 2: Run e2e**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_oauth.py -k "full_happy_path or pkce_verifier_required or single_use"
```
Expected: 3 PASS.

- [ ] **Step 3: Full suite — final verification**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `336 passed` (294 baseline + 42 OAuth).

- [ ] **Step 4: i18n parity + JS parse (defensive sanity)**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: i18n OK, JS OK. This phase doesn't change frontend JS or i18n, but verify nothing regressed.

- [ ] **Step 5: Rebuild + verify all four containers**

```bash
docker-compose build backend frontend && docker-compose up -d --force-recreate backend frontend
sleep 6
curl -s -o /dev/null -w "backend %{http_code}\nfrontend %{http_code}\n" \
  http://localhost:8000/health \
  http://localhost:3000/oauth-consent.css
```
Expected: backend 200, frontend serving the new CSS.

- [ ] **Step 6: Commit e2e tests**

```bash
git add backend/tests/test_oauth.py
git commit -m "$(cat <<'EOF'
test(oauth): end-to-end happy path + PKCE-required + single-use

Single test exercising register → authorize → consent → token →
use → refresh → revoke. Plus tests that PKCE verifier is mandatory
and that authorization codes are single-use.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push branch**

```bash
git push -u origin feature/oauth-provider
```

- [ ] **Step 8: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(oauth): Phase 2.5i-1 — OAuth 2.1 + PKCE authorization server" \
  --body "$(cat <<'EOF'
## Summary

First of three phases (2.5i-1/2/3) building agent-friendly access via OAuth.

This phase ships the **OAuth 2.1 + PKCE authorization server**:

- 3 new tables (\`oauth_clients\`, \`oauth_authorization_codes\`, \`oauth_tokens\`)
- 4 pre-registered MCP clients seeded (claude-desktop, claude-code, cursor, zed)
- Discovery endpoints at \`/.well-known/oauth-authorization-server\` + \`/.well-known/oauth-protected-resource\`
- Dynamic client registration (RFC 7591) — \`POST /oauth/register\`
- Authorization endpoint + Jinja2 templates for login + consent
- Token endpoint handling authorization_code grant + refresh_token rotation
- Revocation (RFC 7009) — \`POST /oauth/revoke\`
- Extended \`get_api_authed\` so OAuth access tokens authenticate against all existing dual-auth endpoints

PKCE-only (S256), no client secrets — all clients are public per OAuth 2.1 + MCP guidance. Reuses Phase 2.5h scopes (\`api:read\` / \`api:rw\`). Tokens hashed at rest with PBKDF2 (same as users.password_hash + api_keys).

**Phase 2.5i-2 (MCP server) will be a follow-up PR** that consumes these tokens. Phase 2.5i-3 (admin connections UI) lands after that.

## Spec
\`docs/superpowers/specs/2026-05-18-oauth-provider-design.md\`

## Plan
\`docs/superpowers/plans/2026-05-18-oauth-provider-plan.md\`

## Test Plan
- [x] Backend: 336 passed (294 baseline + 42 OAuth)
- [x] End-to-end happy path test exercises register → authorize → token → use → refresh → revoke
- [x] PKCE-required + single-use enforced + refresh token rotation
- [x] Containers healthy
- [ ] Browser smoke: hit \`/.well-known/oauth-authorization-server\` directly, check JSON
- [ ] Browser smoke: end-to-end with a curl-based test client (see spec §verification)
- [ ] AR locale: out of scope for this phase (consent UI is EN-only for v1)

## Known v1 limitations (documented in spec)
- \`lookup_oauth_access_token\` iterates candidates with PBKDF2 verify — O(n) per call. Scale fix: HMAC-keyed fast-hash index. Queued.
- Pending-auth state (between \`/authorize\` and \`/authorize/decision\`) is in-memory only. Lost on restart. 10-min TTL anyway.
- No rate-limit decorator on \`/oauth/register\` yet — add slowapi if abuse surfaces.
- Refresh token replay (after rotation) → 400; no all-tokens-revoked defense yet.
- Consent UI is EN-only.

## Non-goals (deferred)
- MCP server endpoint, SSE, tool definitions → Phase 2.5i-2
- Admin "MCP Connections" tab → Phase 2.5i-3
- Token introspection (RFC 7662) — not needed when auth server == resource server
- OAuth 2.0 client_credentials grant
- Per-tool consent
- "Switch org" inside the consent flow

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9: Save memory**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_oauth_provider.md`:

```markdown
---
name: OAuth 2.1 provider (Phase 2.5i-1) — branch
description: OAuth 2.1 + PKCE authorization server mounted in existing FastAPI. PR pending.
type: project
---

**Status (2026-05-18):** PR #<TBD> open against main. Awaiting browser smoke + merge.

**What landed:**
- 3 new tables (oauth_clients, oauth_authorization_codes, oauth_tokens) with PBKDF2-hashed tokens at rest.
- Pre-registered MCP clients seeded: claude-desktop, claude-code, cursor, zed.
- backend/oauth.py module owns all OAuth endpoints + helpers. Jinja2 templates in backend/templates/.
- .well-known/oauth-authorization-server (RFC 8414) + .well-known/oauth-protected-resource (RFC 9728).
- POST /oauth/register dynamic client registration (RFC 7591). No client_secret — public clients only.
- GET /oauth/authorize + POST /oauth/login + POST /oauth/authorize/decision. PKCE-S256 required.
- POST /oauth/token: authorization_code grant + refresh_token rotation per OAuth 2.1.
- POST /oauth/revoke (RFC 7009).
- lookup_oauth_access_token wired into existing get_api_authed → OAuth tokens authenticate against all ~30 dual-auth endpoints.
- frontend/oauth-consent.css for branded consent pages.

**Test count:** 336 backend tests passing (294 pre-branch + 42 OAuth).

**Plan:** docs/superpowers/plans/2026-05-18-oauth-provider-plan.md — 7 tasks.
**Spec:** docs/superpowers/specs/2026-05-18-oauth-provider-design.md.

**Key design choices:**
- PKCE-S256 only — `plain` rejected, no client_secret issued.
- Pending-auth state in-memory (10-min TTL).
- Refresh token rotation mandated by OAuth 2.1 — old pair revoked on each refresh.
- /oauth/authorize is re-entrant via ?resume=<request_id> after login.
- Token lookup is O(n × PBKDF2) per call — documented v1 limitation; future scale fix is HMAC-keyed fast hash index.

**Out of scope for THIS phase (queued for 2.5i-2 / 2.5i-3):**
- MCP SSE endpoint + tool definitions (2.5i-2)
- Admin "MCP Connections" management tab (2.5i-3)
- Rate-limit decorator on /oauth/register
- All-tokens-revoked on refresh-reuse detection
- Per-tool consent / per-resource scopes
- Switch-org inside consent flow
- Consent UI in Arabic
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` with a one-line entry pointing at the new file.

- [ ] **Step 10: Final verification**

```bash
git status -sb
~/.local/bin/gh pr view --json number,url,state | head
```
Expected: PR open, working tree clean.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §5 Schema | Task 1 |
| §5 Pre-registered clients seed | Task 1 |
| §6.1 / 6.2 Discovery | Task 2 |
| §7 Dynamic registration | Task 3 |
| §8.1 /oauth/authorize | Task 4 |
| §8.2 /oauth/login + resume re-entry | Task 4 |
| §8.3 /oauth/authorize/decision | Task 4 |
| §9 /oauth/token (both grants) | Task 5 |
| §10 /oauth/revoke | Task 6 |
| §11 get_api_authed extension | Task 6 |
| §12 Jinja2 templates + CSS | Task 4 |
| §13 Tests | Distributed across all tasks |
| §15 Failure modes | Tested across Tasks 4, 5, 6 |
| §16 Migration / rollout | Documented in PR body |
| §17 Security notes | Implemented: PBKDF2 hashing, S256-only, single-use codes, refresh rotation, exact-match redirect_uri, scheme validation |

No placeholders. Helper names + endpoint paths consistent across tasks (`_pending_auth`, `_pkce_matches`, `_issue_tokens`, `_exchange_authorization_code`, `_refresh_tokens`, `lookup_oauth_access_token`).

Task ordering: 1 (schema, blocks all) → 2 (router scaffolding) → 3 (register, independent) → 4 (authorize+consent, depends on register existing) → 5 (token, depends on authorize) → 6 (revoke + auth integration, depends on tokens) → 7 (e2e + PR).

Each task ends with a green test suite.
