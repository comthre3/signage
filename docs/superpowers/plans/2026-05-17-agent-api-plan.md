# Phase 2.5h — Agent API Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Khanshoof org can mint scoped Bearer API keys for agent-driven content management; tier-based rate limits; dual-auth (session OR key) on ~30 endpoints.

**Architecture:** New `api_keys` table holds hashed credentials. New `AuthedPrincipal` carries identity through endpoints. New `get_api_authed` dependency accepts EITHER `Bearer <session-token>` OR `Bearer khan_live_...`. New `require_api_scope` gates by API scope (`api:read`/`api:rw`) for keys and by role (`admin`/`editor`/`viewer`) for sessions, with rate-limit enforcement folded in. ~30 existing endpoints switch from `require_roles` to `require_api_scope`. Four management endpoints + admin UI tab.

**Tech Stack:** FastAPI · `secrets.token_urlsafe` · `hash_password`/`verify_password` (existing PBKDF2 helpers) · `slowapi`-style per-key counters (in-memory for v1) · vanilla-JS frontend with `Khan.t()`.

**Spec:** `docs/superpowers/specs/2026-05-17-agent-api-design.md`
**Branch:** `feature/agent-api` (already created from main `8cff865`)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(api):` or `test(api):`.
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
4. **Baseline on main is 268 passing.**
5. Pytest path inside container is `tests/...` (the container's working directory is `/app`).
6. **Spec deviation noted up front:** the spec's `require_api_scope("session", "api:read", "api:rw")` pattern uses `"session"` as a magic scope and loses role granularity for session-authed callers. This plan implements the cleaner variant: `require_api_scope("api:read", "api:rw", session_roles=("admin", "editor", "viewer"))`. Function signature still hits all the spec's behaviors; role checks for sessions are preserved.
7. The `client` and `signed_up_org` fixtures already exist in `conftest.py`. `signed_up_org` creates a fresh trialing org with username = email + password = `Khanshoof2026Test`.
8. Do NOT modify `.env` or rewrite prod URLs.

---

## Task 1: Schema — `api_keys` table

**Files:**
- Modify: `backend/db.py`
- Create: `backend/tests/test_api_keys.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_api_keys.py`:

```python
"""Tests for the Phase 2.5h agent API platform."""


def test_api_keys_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("api_keys", "key_hash"),
    )
    assert row is not None
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: FAIL — `assert None is not None`.

- [ ] **Step 3: Add the table to `backend/db.py`**

Find `init_db()`. Locate the LAST `cursor.execute(...)` (most recent feature's DDL). Insert AFTER it:

```python
        # ── Phase 2.5h: agent API ────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
              id                 SERIAL PRIMARY KEY,
              organization_id    INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name               TEXT NOT NULL,
              key_prefix         TEXT NOT NULL,
              key_hash           TEXT NOT NULL,
              scope              TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_used_at       TIMESTAMPTZ,
              revoked_at         TIMESTAMPTZ
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_org "
            "ON api_keys (organization_id, revoked_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_prefix "
            "ON api_keys (key_prefix)"
        )
```

8-space indentation matches surrounding `cursor.execute(...)` blocks.

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose ps | grep backend
```
Expected: `Up (healthy)`.

- [ ] **Step 5: Run test**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: 1 PASS.

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `269 passed` (268 baseline + 1 new).

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): api_keys table + 2 indices

Per-org Bearer API key storage. CHECK constraint pins scope to
('api:read', 'api:rw'). key_hash uses PBKDF2-SHA256 same as
users.password_hash. revoked_at IS NULL = active key.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Key helpers — `generate_api_key` + `lookup_api_key`

**Files:**
- Modify: `backend/main.py` (add helpers near auth helpers around line 422-490)
- Modify: `backend/tests/test_api_keys.py` (append 8 tests)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api_keys.py`:

```python
# ── generate_api_key + lookup_api_key ─────────────────────────────────
import re
import uuid
from db import execute, query_one
import time


def _signup_org(client, suffix=None):
    """Helper: create a fresh org via signup. Returns (token, org_id, user_id)."""
    sfx = suffix or uuid.uuid4().hex[:8]
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Biz {sfx}",
                          "email": f"a-{sfx}@example.com"})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": f"a-{sfx}@example.com", "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["organization"]["id"], body["user"]["id"]


def _mint_key_row(org_id: int, scope: str = "api:rw", name: str = "test", creator: int = None):
    """Mint via the low-level helpers (not the HTTP endpoint, which lands in Task 6)."""
    from main import generate_api_key
    full_key, prefix, hashed = generate_api_key()
    execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, name, prefix, hashed, scope, creator),
    )
    return full_key, prefix


def test_generate_api_key_format():
    from main import generate_api_key
    full_key, prefix, hashed = generate_api_key()
    assert re.match(r"^khan_live_[A-Za-z0-9_-]{32,}$", full_key), \
        f"unexpected key format: {full_key}"


def test_generate_api_key_prefix_is_first_12_chars():
    from main import generate_api_key, API_KEY_PREFIX_LEN
    full_key, prefix, _ = generate_api_key()
    assert prefix == full_key[:API_KEY_PREFIX_LEN]
    assert API_KEY_PREFIX_LEN == 12


def test_generate_api_key_hash_not_plaintext():
    from main import generate_api_key
    full_key, _, hashed = generate_api_key()
    assert full_key not in hashed
    # Hash format: salt_hex + "$" + digest_hex
    assert "$" in hashed


def test_lookup_returns_row_for_valid_key(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    row = lookup_api_key(full_key)
    assert row is not None
    assert row["organization_id"] == org_id
    assert row["scope"] == "api:rw"


def test_lookup_returns_none_for_unknown_prefix(client):
    from main import lookup_api_key
    assert lookup_api_key("khan_live_zzzzzzzzzzzzzz") is None


def test_lookup_returns_none_for_bad_scheme(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id)
    # Mangle the prefix scheme so it doesn't start with khan_live_
    mangled = "rats_live_" + full_key[len("khan_live_"):]
    assert lookup_api_key(mangled) is None


def test_lookup_returns_none_for_revoked_key(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    execute("UPDATE api_keys SET revoked_at = now() WHERE key_prefix = ?", (prefix,))
    assert lookup_api_key(full_key) is None


def test_lookup_updates_last_used_at(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    before = query_one("SELECT last_used_at FROM api_keys WHERE key_prefix = ?", (prefix,))
    assert before["last_used_at"] is None
    lookup_api_key(full_key)
    # Small delay then re-read; psycopg may not flush immediately depending on autocommit
    time.sleep(0.1)
    after = query_one("SELECT last_used_at FROM api_keys WHERE key_prefix = ?", (prefix,))
    assert after["last_used_at"] is not None
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: `ImportError: cannot import name 'generate_api_key' from 'main'`.

- [ ] **Step 3: Add helpers to `backend/main.py`**

Find a logical location near `hash_password` (around line 183). After `verify_password` ends (around line 200), insert:

```python
# ── API keys (Phase 2.5h) ─────────────────────────────────────────────

API_KEY_PREFIX_LEN = 12   # "khan_live_" (10 chars) + 2 randomness chars


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Caller stores prefix + hash; returns
    full_key to the operator ONCE (never seen again)."""
    suffix = secrets.token_urlsafe(24)
    full_key = f"khan_live_{suffix}"
    prefix = full_key[:API_KEY_PREFIX_LEN]
    hashed = hash_password(full_key)
    return full_key, prefix, hashed


def lookup_api_key(bearer_token: str) -> Optional[dict]:
    """Return active api_key row if the bearer matches; else None.
    Fire-and-forget update of last_used_at."""
    if not bearer_token or not bearer_token.startswith("khan_live_"):
        return None
    prefix = bearer_token[:API_KEY_PREFIX_LEN]
    candidates = query_all(
        "SELECT id, organization_id, key_hash, scope FROM api_keys "
        "WHERE key_prefix = ? AND revoked_at IS NULL",
        (prefix,),
    )
    for row in candidates:
        if verify_password(bearer_token, row["key_hash"]):
            try:
                execute(
                    "UPDATE api_keys SET last_used_at = now() WHERE id = ?",
                    (row["id"],),
                )
            except Exception as exc:
                logger.warning("api_key_last_used_update_failed id=%s err=%s",
                               row["id"], exc)
            return row
    return None
```

Verify `secrets`, `Optional`, `query_all`, `execute`, `hash_password`, `verify_password`, `logger` are all in scope (they are — all used elsewhere in main.py).

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run new tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: 9 PASS (1 schema + 8 helper).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `277 passed` (269 + 8).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): generate_api_key + lookup_api_key helpers

generate_api_key: secrets.token_urlsafe(24) prefixed with khan_live_,
stored as PBKDF2 hash (same format as users.password_hash). Caller
gets the full key ONCE.

lookup_api_key: prefix-indexed lookup + verify_password() constant-
time compare. Fire-and-forget last_used_at update. Returns None for
revoked or unknown keys, or any non-khan_live_ scheme.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: AuthedPrincipal + `get_api_authed` + `require_api_scope`

**Files:**
- Modify: `backend/main.py` (extract `_session_lookup`, add `AuthedPrincipal`, `get_api_authed`, `require_api_scope`)
- Modify: `backend/tests/test_api_keys.py` (append 9 tests)

**Goal:** Dual-mode auth dependency that accepts session OR API key, plus a scope/role gate that downstream endpoints will use. Rate limiter is wired in Task 5 (placeholder no-op for now).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api_keys.py`:

```python
# ── AuthedPrincipal + get_api_authed + require_api_scope ──────────────


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_get_api_authed_accepts_session_token(client):
    session_token, org_id, _u = _signup_org(client)
    r = client.get("/organization", headers=_bearer(session_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == org_id


def test_get_api_authed_accepts_api_key(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:rw")
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == org_id


def test_get_api_authed_rejects_missing_header(client):
    r = client.get("/organization")
    assert r.status_code == 401


def test_get_api_authed_rejects_bad_scheme(client):
    r = client.get("/organization", headers={"Authorization": "Basic dGVzdA=="})
    assert r.status_code == 401


def test_get_api_authed_rejects_unknown_key(client):
    r = client.get("/organization", headers=_bearer("khan_live_garbage_does_not_exist_xxxxxxxxxx"))
    assert r.status_code == 401


# Scope gates — verified via /organization (GET, read-allowed) and POST /playlists (rw-required)


def test_read_scope_can_GET_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    r = client.get("/playlists", headers=_bearer(full_key))
    assert r.status_code == 200, r.text


def test_read_scope_cannot_POST_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    r = client.post("/playlists", headers=_bearer(full_key),
                    json={"name": "denied"})
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["code"] == "api.insufficient_scope"


def test_rw_scope_can_POST_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:rw")
    r = client.post("/playlists", headers=_bearer(full_key),
                    json={"name": "allowed"})
    assert r.status_code in (200, 201), r.text


def test_session_passes_all_gates(client):
    session_token, org_id, _u = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(session_token),
                    json={"name": "session"})
    assert r.status_code in (200, 201), r.text
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "get_api_authed or scope_can or scope_cannot or session_passes"
```
Expected: failures — the new dependency isn't wired into `/organization` or `/playlists` yet, and `AuthedPrincipal`/`get_api_authed` don't exist.

- [ ] **Step 3: Refactor existing `get_current_user` to extract `_session_lookup`**

Find `def get_current_user(authorization: Optional[str] = Header(None)) -> dict:` (around line 422). Extract the body that resolves a session token into a separate helper `_session_lookup(token: str) -> Optional[dict]` so it can be re-used by `get_api_authed`. Keep `get_current_user` as a thin wrapper for backward compatibility (existing endpoints that aren't being switched still use it).

Replace the function with:

```python
def _session_lookup(token: str) -> Optional[dict]:
    """Look up an active session by bearer token. Returns user dict or None."""
    session = query_one(
        """
        SELECT sessions.token, sessions.user_id, users.username, users.is_admin,
               sessions.created_at, sessions.last_used,
               users.role, users.organization_id
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    )
    if not session:
        return None
    last_used = session.get("last_used") or session.get("created_at")
    if last_used:
        try:
            last_used_dt = datetime.fromisoformat(str(last_used).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last_used_dt).total_seconds() > SESSION_TTL_SECONDS:
                execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
        except (ValueError, TypeError):
            pass
    execute(
        "UPDATE sessions SET last_used = ? WHERE token = ?",
        (utc_now_iso(), token),
    )
    return {
        "id": session["user_id"],
        "username": session["username"],
        "is_admin": bool(session["is_admin"]),
        "role": session.get("role") or ("admin" if session["is_admin"] else "viewer"),
        "organization_id": session.get("organization_id"),
        "token": token,
    }


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")
    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user
```

(The original `get_current_user` body is what becomes `_session_lookup` minus the HTTP-raising. Read the actual file before editing to ensure all branches are preserved exactly.)

- [ ] **Step 4: Add `AuthedPrincipal` + `get_api_authed` + `require_api_scope`**

Immediately after `get_current_user`, insert:

```python
class AuthedPrincipal:
    """Carries whichever identity authenticated the request."""

    def __init__(
        self,
        *,
        user: Optional[dict] = None,
        api_key: Optional[dict] = None,
        organization_id: int,
        scope: str,
    ):
        self.user = user
        self.api_key = api_key
        self.organization_id = organization_id
        self.scope = scope            # "session" | "api:read" | "api:rw"


def get_api_authed(authorization: Optional[str] = Header(None)) -> AuthedPrincipal:
    """Dual-mode auth. Accepts a session bearer token OR an API key.
    Returns AuthedPrincipal with the right identity attached."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    # API key first (cheap prefix check)
    key_row = lookup_api_key(token)
    if key_row:
        return AuthedPrincipal(
            api_key=key_row,
            organization_id=key_row["organization_id"],
            scope=key_row["scope"],
        )

    # Session fall-through
    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return AuthedPrincipal(
        user=user,
        organization_id=user["organization_id"],
        scope="session",
    )


_ALL_SESSION_ROLES = ("admin", "editor", "viewer")


def require_api_scope(
    *allowed_api_scopes: str,
    session_roles: tuple = _ALL_SESSION_ROLES,
):
    """Gate an endpoint:
      - API-keyed: scope must be in allowed_api_scopes
      - Session-authed: user's role must be in session_roles
    Plus rate limit check (no-op until Task 5 wires it).
    """
    def dep(principal: AuthedPrincipal = Depends(get_api_authed)) -> AuthedPrincipal:
        if principal.api_key is not None:
            if principal.scope not in allowed_api_scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "api.insufficient_scope",
                        "message": f"This endpoint requires one of: {', '.join(allowed_api_scopes)}",
                        "scope":   principal.scope,
                    },
                )
        else:
            # session
            role = (principal.user or {}).get("role", "viewer")
            if role not in session_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "insufficient_role",
                        "message": f"Role {role} not permitted",
                    },
                )
        # Rate-limit hook — Task 5 fills this in
        return principal
    return dep
```

Verify imports: `Optional`, `Header`, `HTTPException`, `Depends`, `datetime`, `timezone` are all already imported.

- [ ] **Step 5: Switch `/organization` GET handler to the new dep (proves the wiring)**

Find `@app.get("/organization")` (around line 1005). Change the signature:

```python
# Before:
@app.get("/organization")
def get_organization(user: dict = Depends(get_current_user)) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    ...

# After:
@app.get("/organization")
def get_organization(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (principal.organization_id,))
    ...
```

(Keep the rest of the body unchanged — `subscription_state(org)`, the return dict, etc.)

- [ ] **Step 6: Switch `POST /playlists` to the new dep (proves the rw scope)**

Find `@app.post("/playlists")` (around line 3181). Change the signature:

```python
# Before:
@app.post("/playlists")
def create_playlist(payload: PlaylistCreate,
                    user: dict = Depends(require_roles("admin", "editor")),
                    _sub: dict = Depends(require_active_subscription)) -> dict:
    org = org_id(user)
    ...

# After:
@app.post("/playlists")
def create_playlist(
    payload: PlaylistCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    org = principal.organization_id
    ...
```

(Body unchanged — the `created_at`, `INSERT INTO playlists` SQL etc. stay as-is. Just `org` source switches.)

- [ ] **Step 7: Also switch `GET /playlists`**

Find `@app.get("/playlists")` (around line 3173). Apply the same conversion pattern:

```python
@app.get("/playlists")
def list_playlists(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list:
    org = principal.organization_id
    rows = query_all(
        "SELECT id, name, organization_id, created_at FROM playlists "
        "WHERE organization_id = ? ORDER BY created_at DESC",
        (org,),
    )
    return rows
```

(Adapt to the actual handler body — the SELECT shape may differ from this sketch. The point is: replace `org_id(user)` with `principal.organization_id`.)

- [ ] **Step 8: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 9: Run new tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: 18 PASS (1 schema + 8 helper + 9 auth/scope).

- [ ] **Step 10: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `286 passed` (277 + 9 new).

If any existing test fails, it likely depends on the `user` dict shape returned by an old handler — investigate before committing.

- [ ] **Step 11: Commit**

```bash
git add backend/main.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): AuthedPrincipal + get_api_authed + require_api_scope

Dual-mode auth: Bearer accepts either a session token or a
khan_live_* API key. AuthedPrincipal carries the right identity
through to handlers. require_api_scope gates API keys by their
scope and sessions by role — both run through the same dep so
endpoints have one annotation, not two.

GET /organization and POST/GET /playlists switched to the new dep
as canonical proofs. Full sweep across remaining endpoints lands
in Task 4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Convert remaining endpoints to `require_api_scope`

**Files:**
- Modify: `backend/main.py` (28 endpoint signatures)
- Modify: `backend/tests/test_api_keys.py` (append 2 org-scoping tests)

**Goal:** Mechanical sweep — convert the remaining ~28 endpoints in the agent surface to `require_api_scope`. No new functionality. Existing tests must continue passing.

**Endpoints to convert** (line numbers approximate):

| Method | Path | API scopes | Session roles | Line ~ |
|---|---|---|---|---|
| GET | `/media` | api:read, api:rw | admin, editor, viewer | 3339 |
| GET | `/media/{id}` | api:read, api:rw | admin, editor, viewer | — (find via grep) |
| POST | `/media/upload` | api:rw | admin, editor | 3353 |
| POST | `/media/url` | api:rw | admin, editor | 3394 |
| DELETE | `/media/{id}` | api:rw | admin, editor | 3414 |
| GET | `/playlists/{id}` | api:read, api:rw | admin, editor, viewer | 3194 |
| PUT | `/playlists/{id}` | api:rw | admin, editor | 3222 |
| DELETE | `/playlists/{id}` | api:rw | admin, editor | 3242 |
| POST | `/playlists/{id}/items` | api:rw | admin, editor | 3276 |
| DELETE | `/playlists/{id}/items/{item_id}` | api:rw | admin, editor | 3318 |
| GET | `/sites` | api:read, api:rw | admin, editor, viewer | 1512 |
| GET | `/screens` | api:read, api:rw | admin, editor, viewer | 1591 |
| GET | `/screens/{id}/zones` | api:read, api:rw | admin, editor, viewer | 1740 |
| PUT | `/screens/{id}` | api:rw | admin, editor | 1670 |
| GET | `/walls` | api:read, api:rw | admin, editor, viewer | 2779 |
| GET | `/walls/{id}` | api:read, api:rw | admin, editor, viewer | 2788 |
| GET | `/walls/{wall_id}/canvas-playlist` | api:read, api:rw | admin, editor, viewer | 3019 |
| POST | `/walls/{wall_id}/canvas-playlist/items` | api:rw | admin, editor | 3041 |
| PATCH | `/walls/{wall_id}/canvas-playlist/items/{item_id}` | api:rw | admin, editor | 3077 |
| DELETE | `/walls/{wall_id}/canvas-playlist/items/{item_id}` | api:rw | admin, editor | 3096 |
| GET | `/schedules` | api:read, api:rw | admin, editor, viewer | 1258 |
| GET | `/schedules/{id}` | api:read, api:rw | admin, editor, viewer | 1269 |
| POST | `/schedules` | api:rw | admin, editor | 1247 |
| PUT | `/schedules/{id}` | api:rw | admin, editor | 1278 |
| DELETE | `/schedules/{id}` | api:rw | admin | 1290 |
| PUT | `/schedules/{id}/rules` | api:rw | admin, editor | 1300 |

**Endpoints NOT converted (keep `require_roles` / `get_current_user`):**

| Path | Why |
|---|---|
| `/auth/*` | Out of API surface |
| `/billing/*` | Out of API surface |
| `/users/*`, `/users/{id}/groups`, `/groups/*` | Privilege-escalation risk |
| `POST /sites`, `PUT /sites/{id}`, `DELETE /sites/{id}` | Billing-adjacent |
| `POST /screens`, `DELETE /screens/{id}` | Screen-limit / pair-code logic too sensitive for v1 |
| `PUT /screens/{id}/zones`, `POST /zone-templates`, `POST /screens/{id}/zone-templates/apply` | Advanced — deferred |
| `POST /screens/request_code`, `POST /screens/claim`, `POST /screens/pair`, `GET /screens/poll/{code}`, `POST /walls/{wall_id}/cells/{row}/{col}/pair`, `POST /walls/cells/redeem`, `DELETE /walls/{wall_id}/cells/{row}/{col}/pairing` | Display-side pairing flows; no user auth |
| `POST /walls`, `PATCH /walls/{wall_id}`, `PATCH /walls/{wall_id}/cells`, `DELETE /walls/{wall_id}` | Wall create/delete affects screen layout — deferred |
| `POST /screens/{id}/preview-token` | Internal admin UI feature |
| `PATCH /organizations/me` | Org settings mutation — out of surface |
| `GET /audit-log` | Admin-only via session |
| `GET /screens/{token}/content`, `GET /screens/{token}/layout` | Player endpoints — token-scoped, not user-scoped |
| `GET /health` | Liveness probe |

- [ ] **Step 1: Write 2 cross-cutting org-scoping tests first**

Append to `backend/tests/test_api_keys.py`:

```python
# ── Org-scoping (API keys can only see/modify their own org) ──────────


def test_api_key_cannot_see_other_orgs_playlists(client):
    """An API key for org A must NOT see playlists created by org B."""
    # Org A with a key
    _ta, org_a, _ua = _signup_org(client)
    key_a, _ = _mint_key_row(org_a, scope="api:rw")
    # Org B with a session, creates a playlist
    tb, org_b, _ub = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(tb), json={"name": "B's playlist"})
    assert r.status_code in (200, 201), r.text
    b_pl_id = r.json()["id"]
    # A's key tries to GET B's playlist
    r = client.get("/playlists", headers=_bearer(key_a))
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()]
    assert b_pl_id not in ids


def test_api_key_cannot_modify_other_orgs_playlists(client):
    _ta, org_a, _ua = _signup_org(client)
    key_a, _ = _mint_key_row(org_a, scope="api:rw")
    tb, org_b, _ub = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(tb), json={"name": "B's playlist"})
    b_pl_id = r.json()["id"]
    # Try to delete B's playlist with A's key
    r = client.delete(f"/playlists/{b_pl_id}", headers=_bearer(key_a))
    assert r.status_code == 404, r.text  # 404 from org-scoped lookup
```

- [ ] **Step 2: Run them — expected to FAIL (the unconverted endpoints don't accept the key)**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "org_s"
```

Expected: `test_api_key_cannot_modify_other_orgs_playlists` fails with 401 (DELETE /playlists/{id} still uses `get_current_user`). After Task 4 sweep both pass.

- [ ] **Step 3: Convert each endpoint in the table above**

For each endpoint, apply the pattern:

```python
# BEFORE:
@app.get("/sites")
def list_sites(user: dict = Depends(require_roles("admin", "editor", "viewer"))) -> list:
    return query_all(
        "SELECT * FROM sites WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )

# AFTER:
@app.get("/sites")
def list_sites(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list:
    return query_all(
        "SELECT * FROM sites WHERE organization_id = ? ORDER BY created_at DESC",
        (principal.organization_id,),
    )
```

For write endpoints with subscription gate (which is most of them):

```python
# BEFORE:
@app.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate,
                    user: dict = Depends(require_roles("admin", "editor")),
                    _sub: dict = Depends(require_active_subscription)) -> dict:
    org = org_id(user)
    ...

# AFTER:
@app.post("/schedules", status_code=201)
def create_schedule(
    payload: ScheduleCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    org = principal.organization_id
    ...
```

Body changes per handler:
- Replace `user.get("X")` or `user["X"]` with `principal.user.get("X")` when `principal.user is not None` else handle API-key context (e.g., for audit logging — use prefix as actor name).
- Replace `org_id(user)` with `principal.organization_id`.

For handlers that pass `user` to `audit(...)` (the Phase 2.5c helper), use:

```python
audit_actor = (
    {"id": principal.user["id"], "username": principal.user["username"],
     "organization_id": principal.organization_id}
    if principal.user else
    {"id": None, "username": f"api:{principal.api_key['key_prefix']}",
     "organization_id": principal.organization_id}
)
audit(request, action="...", actor=audit_actor, ...)
```

(Only applies where the handler currently has audit calls — which is mainly the security-hardening phase's wired endpoints.)

For each of the 28 endpoints, read its current signature, replicate the pattern above. Keep the rest of the body intact.

**Important — do NOT touch:**
- `from main import org_id`-style helper calls used outside the surface (e.g., billing or auth endpoints) — they still use `get_current_user`.
- Any handler whose body branches on `user["is_admin"]` (rare but exists) — for API-keyed requests, treat as admin equivalent within the agent surface (the key's scope is the auth gate; role doesn't apply).

- [ ] **Step 4: Rebuild + recreate backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run the 2 org-scoping tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "org_s"
```
Expected: 2 PASS.

- [ ] **Step 6: Full suite — critical check**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `288 passed` (286 + 2 new).

If any pre-existing test fails: a handler's `user`/`org_id` swap introduced a regression. Read the failing test, compare actual to expected, fix the handler.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): convert ~28 endpoints to require_api_scope

Sweep of media (5), playlists (5 remaining), sites/screens/walls
reads, schedules (6), screens write-assign, walls canvas-playlist
mutations. Endpoints now accept either a session bearer OR a
khan_live_* API key with the right scope. Role granularity is
preserved for sessions via the session_roles kwarg.

Cross-org isolation tests prove keys can't read or mutate other
orgs' data — same org_scoped queries as before, just routed via
principal.organization_id instead of org_id(user).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Tier-based rate limiting

**Files:**
- Modify: `backend/main.py` (add `PLAN_API_LIMITS` + `_api_key_rate_check`, wire into `require_api_scope`)
- Modify: `backend/tests/test_api_keys.py` (append 2 tests)

**Goal:** Per-key in-memory rate counters; tier-based limits; 429 with `Retry-After` header. Sessions skip the limiter.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api_keys.py`:

```python
# ── Rate limiting (tier-based) ────────────────────────────────────────


def test_rate_limit_enforced_per_minute(client, monkeypatch):
    # Set a very low limit so we can hit it without firing thousands of requests
    monkeypatch.setattr("main.PLAN_API_LIMITS", {
        "starter": {"per_minute": 3, "per_hour": 100},
    })
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    # 3 requests fine
    for _ in range(3):
        r = client.get("/organization", headers=_bearer(full_key))
        assert r.status_code == 200, r.text
    # 4th request → 429
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 429, r.text


def test_429_includes_retry_after_header(client, monkeypatch):
    monkeypatch.setattr("main.PLAN_API_LIMITS", {
        "starter": {"per_minute": 1, "per_hour": 100},
    })
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 200
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 429
    assert "retry-after" in {h.lower() for h in r.headers.keys()}, r.headers
    assert "x-ratelimit-limit" in {h.lower() for h in r.headers.keys()}
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "rate_limit or retry_after"
```
Expected: tests fail — 4th request returns 200, not 429.

- [ ] **Step 3: Add `PLAN_API_LIMITS` + `_api_key_rate_check`**

Near the API key helpers (after `lookup_api_key` from Task 2), insert:

```python
import time
from collections import defaultdict


PLAN_API_LIMITS = {
    "starter":    {"per_minute": 30,   "per_hour": 500},
    "growth":     {"per_minute": 100,  "per_hour": 5000},
    "business":   {"per_minute": 500,  "per_hour": 25000},
    "pro":        {"per_minute": 2000, "per_hour": 100000},
    "enterprise": {"per_minute": 5000, "per_hour": 250000},
}

_rate_buckets: dict = defaultdict(lambda: {"min": [], "hour": []})


def _api_key_rate_check(principal) -> None:
    """Raise 429 if the principal's API key has exceeded its tier limits.
    Sessions are NOT rate-limited here."""
    if principal.api_key is None:
        return
    key_id = principal.api_key["id"]
    org = query_one("SELECT plan FROM organizations WHERE id = ?",
                    (principal.organization_id,))
    plan = (org or {}).get("plan", "starter")
    limits = PLAN_API_LIMITS.get(plan, PLAN_API_LIMITS["starter"])

    now = time.time()
    bucket = _rate_buckets[key_id]
    bucket["min"]  = [t for t in bucket["min"]  if t > now - 60]
    bucket["hour"] = [t for t in bucket["hour"] if t > now - 3600]

    if len(bucket["min"]) >= limits["per_minute"]:
        oldest = min(bucket["min"])
        retry_after = max(1, int(60 - (now - oldest)))
        raise HTTPException(
            status_code=429,
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     str(limits["per_minute"]),
                "X-RateLimit-Window":    "60",
                "X-RateLimit-Remaining": "0",
            },
            detail={"code": "rate_limited",
                    "message": "Per-minute rate limit exceeded"},
        )

    if len(bucket["hour"]) >= limits["per_hour"]:
        oldest = min(bucket["hour"])
        retry_after = max(1, int(3600 - (now - oldest)))
        raise HTTPException(
            status_code=429,
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     str(limits["per_hour"]),
                "X-RateLimit-Window":    "3600",
                "X-RateLimit-Remaining": "0",
            },
            detail={"code": "rate_limited",
                    "message": "Per-hour rate limit exceeded"},
        )

    bucket["min"].append(now)
    bucket["hour"].append(now)
```

`time` and `defaultdict` should be imported at the top of the file. Add the imports if missing.

- [ ] **Step 4: Wire the rate check into `require_api_scope`**

Find `require_api_scope` (added in Task 3). Replace its body so the rate check runs after auth+scope but before returning:

```python
def require_api_scope(
    *allowed_api_scopes: str,
    session_roles: tuple = _ALL_SESSION_ROLES,
):
    def dep(principal: AuthedPrincipal = Depends(get_api_authed)) -> AuthedPrincipal:
        if principal.api_key is not None:
            if principal.scope not in allowed_api_scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "api.insufficient_scope",
                        "message": f"This endpoint requires one of: {', '.join(allowed_api_scopes)}",
                        "scope":   principal.scope,
                    },
                )
        else:
            role = (principal.user or {}).get("role", "viewer")
            if role not in session_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "insufficient_role",
                        "message": f"Role {role} not permitted",
                    },
                )
        _api_key_rate_check(principal)
        return principal
    return dep
```

The only change is the new `_api_key_rate_check(principal)` line right before `return principal`.

- [ ] **Step 5: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 6: Run rate-limit tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "rate_limit or retry_after"
```
Expected: 2 PASS.

- [ ] **Step 7: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `290 passed` (288 + 2).

Note: the in-memory `_rate_buckets` is process-local. Between tests it carries state. The monkeypatch in each test resets the LIMIT but not the bucket, so tests that use the same key id may bleed. The `_signup_org` helper creates fresh orgs per test → fresh key ids → fresh buckets in practice. If a test starts misbehaving, add explicit bucket-clearing:

```python
# In the test:
from main import _rate_buckets
_rate_buckets.clear()
```

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): per-tier rate limits for API keys

PLAN_API_LIMITS maps plan tier to per-minute + per-hour caps. Counters
are process-local (in-memory) and pruned on every check. 429 carries
Retry-After plus X-RateLimit-Limit/Window/Remaining headers so
agent SDKs can back off cleanly.

Sessions skip the limiter — existing /auth/login slowapi handles login.

Multi-replica caveat: each replica counts independently → effective
limit is N× tighter per-replica, N× looser overall. Documented in
spec §8 as v1 limitation; future fix is Redis-backed counter.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Key management endpoints

**Files:**
- Modify: `backend/main.py` (add 4 endpoints)
- Modify: `backend/tests/test_api_keys.py` (append 4 tests)

**Goal:** `POST/GET/DELETE/POST-rotate` `/api-keys` for the admin UI to call. Session-only (no API key can mint/revoke other API keys).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_api_keys.py`:

```python
# ── Management endpoints ──────────────────────────────────────────────


def test_post_api_keys_returns_full_key_once(client):
    session_token, _org_id, _u = _signup_org(client)
    r = client.post("/api-keys", headers=_bearer(session_token),
                    json={"name": "Zapier", "scope": "api:rw"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert "key" in body
    assert body["key"].startswith("khan_live_")
    assert body["key_prefix"] == body["key"][:12]
    assert body["scope"] == "api:rw"
    # Listing afterwards should NOT include the full key
    r = client.get("/api-keys", headers=_bearer(session_token))
    items = r.json()["items"]
    assert all("key" not in item for item in items), \
        "GET /api-keys must never return the full key, only the prefix"


def test_get_api_keys_lists_only_own_org(client):
    sa, _, _ = _signup_org(client)
    r = client.post("/api-keys", headers=_bearer(sa),
                    json={"name": "A", "scope": "api:read"})
    assert r.status_code in (200, 201)
    sb, _, _ = _signup_org(client)
    r = client.get("/api-keys", headers=_bearer(sb))
    items = r.json()["items"]
    names = [it["name"] for it in items]
    assert "A" not in names


def test_delete_api_keys_revokes_not_deletes(client):
    session_token, org_id, _u = _signup_org(client)
    r = client.post("/api-keys", headers=_bearer(session_token),
                    json={"name": "tmp", "scope": "api:rw"})
    key_id = r.json()["id"]
    r = client.delete(f"/api-keys/{key_id}", headers=_bearer(session_token))
    assert r.status_code in (200, 204)
    # Row still present, revoked_at set
    row = query_one("SELECT revoked_at FROM api_keys WHERE id = ?", (key_id,))
    assert row is not None
    assert row["revoked_at"] is not None


def test_revoked_key_cannot_authenticate(client):
    session_token, _org_id, _u = _signup_org(client)
    r = client.post("/api-keys", headers=_bearer(session_token),
                    json={"name": "tmp", "scope": "api:rw"})
    full_key = r.json()["key"]
    key_id = r.json()["id"]
    # Revoke it
    r = client.delete(f"/api-keys/{key_id}", headers=_bearer(session_token))
    assert r.status_code in (200, 204)
    # Now using the key returns 401
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 401, r.text
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py -k "post_api_keys or get_api_keys_lists or delete_api_keys or revoked_key"
```
Expected: failures — endpoints don't exist (404).

- [ ] **Step 3: Add Pydantic models + endpoints to `backend/main.py`**

Find a location near other admin endpoints (the audit-log endpoint is one good neighbor — around line 1131-ish). Insert:

```python
class ApiKeyCreate(BaseModel):
    name:  str = Field(..., min_length=1, max_length=200)
    scope: str = Field(..., pattern="^api:(read|rw)$")


@app.post("/api-keys", status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    full_key, prefix, hashed = generate_api_key()
    key_id = execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id(user), payload.name, prefix, hashed, payload.scope, user["id"]),
    )
    return {
        "id":         key_id,
        "name":       payload.name,
        "key_prefix": prefix,
        "key":        full_key,   # ← returned ONLY here, never again
        "scope":      payload.scope,
        "created_at": utc_now_iso(),
    }


@app.get("/api-keys")
def list_api_keys(user: dict = Depends(require_roles("admin"))) -> dict:
    rows = query_all(
        """
        SELECT id, name, key_prefix, scope, created_at, last_used_at, revoked_at
        FROM api_keys
        WHERE organization_id = ?
        ORDER BY created_at DESC
        """,
        (org_id(user),),
    )
    items = []
    for r in rows:
        items.append({
            "id":           r["id"],
            "name":         r["name"],
            "key_prefix":   r["key_prefix"],
            "scope":        r["scope"],
            "created_at":   r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "last_used_at": (r["last_used_at"].isoformat()
                             if r["last_used_at"] and hasattr(r["last_used_at"], "isoformat")
                             else r["last_used_at"]),
            "revoked_at":   (r["revoked_at"].isoformat()
                             if r["revoked_at"] and hasattr(r["revoked_at"], "isoformat")
                             else r["revoked_at"]),
        })
    return {"items": items}


@app.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    user: dict = Depends(require_roles("admin")),
) -> None:
    row = query_one(
        "SELECT id FROM api_keys WHERE id = ? AND organization_id = ?",
        (key_id, org_id(user)),
    )
    if not row:
        raise http_error(404, "api_key.not_found", "API key not found")
    execute(
        "UPDATE api_keys SET revoked_at = now() WHERE id = ? AND revoked_at IS NULL",
        (key_id,),
    )


@app.post("/api-keys/{key_id}/rotate")
def rotate_api_key(
    key_id: int,
    user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    """Revoke + create fresh with same name + scope."""
    old = query_one(
        "SELECT id, name, scope FROM api_keys "
        "WHERE id = ? AND organization_id = ? AND revoked_at IS NULL",
        (key_id, org_id(user)),
    )
    if not old:
        raise http_error(404, "api_key.not_found", "API key not found")
    execute("UPDATE api_keys SET revoked_at = now() WHERE id = ?", (key_id,))
    full_key, prefix, hashed = generate_api_key()
    new_id = execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id(user), old["name"], prefix, hashed, old["scope"], user["id"]),
    )
    return {
        "id":          new_id,
        "name":        old["name"],
        "key_prefix":  prefix,
        "key":         full_key,
        "scope":       old["scope"],
        "created_at":  utc_now_iso(),
        "rotated_from": key_id,
    }
```

Verify `BaseModel`, `Field`, `require_roles`, `require_active_subscription` are all in scope.

- [ ] **Step 4: Rebuild + run tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_api_keys.py
```
Expected: 24 PASS (1 + 8 + 9 + 2 + 2 + 4 = 26 — but cumulative). Verify the new 4 management tests pass.

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `294 passed` (290 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_api_keys.py
git commit -m "$(cat <<'EOF'
feat(api): /api-keys CRUD endpoints (admin session-only)

POST returns the full key ONCE (with key_prefix + id). GET lists
metadata only — prefix, scope, timestamps, revoked status. No full
key ever leaked through GET.

DELETE = soft delete (sets revoked_at). Row preserved for audit
history. Once revoked, lookup_api_key returns None → 401 on next
auth attempt.

POST /api-keys/{id}/rotate is a revoke+create combo for credential
rotation. Returns the new full key and references the rotated_from id.

All four endpoints session-only — API keys cannot mint or revoke
other API keys (privilege escalation prevention). Mint + rotate
gate on active subscription per Phase 2.5f.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend — "API Keys" tab

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Goal:** Admin-only "API Keys" nav section. List + create-modal + one-time-reveal + revoke. ~15 i18n keys.

- [ ] **Step 1: Add nav button in `frontend/index.html`**

Find the existing nav button group (around line 25-31). Insert (admin-gated like Users):

```html
<button data-section="api-keys" data-i18n="nav.api_keys">API Keys</button>
```

- [ ] **Step 2: Add section markup in `frontend/index.html`**

After the last existing `<section class="panel...">`, insert:

```html
<section id="api-keys" class="panel hidden">
  <header class="panel-header">
    <h2 data-i18n="api_keys.title">API Keys</h2>
    <button id="api-key-new-btn" class="btn" data-i18n="api_keys.new">+ New key</button>
  </header>

  <p class="muted">
    <span data-i18n="api_keys.intro">Programmatically manage media, playlists, screens, walls, and schedules. </span>
    <a id="api-key-docs-link" href="/api-docs.html" target="_blank" data-i18n="api_keys.docs_link">Read the API docs ↗</a>
  </p>

  <div id="api-keys-list" class="api-keys-list"></div>

  <div id="api-key-create-modal" class="modal hidden" role="dialog" aria-modal="true">
    <div class="modal-card">
      <h3 data-i18n="api_keys.new">+ New key</h3>
      <label class="field">
        <span data-i18n="api_keys.field.name">Name</span>
        <input id="api-key-name" type="text" maxlength="200" placeholder="Zapier" />
      </label>
      <fieldset class="field">
        <legend data-i18n="api_keys.field.scope">Scope</legend>
        <label class="radio-row">
          <input type="radio" name="api-key-scope" value="api:read" />
          <span data-i18n="api_keys.scope.read">Read only</span>
        </label>
        <label class="radio-row">
          <input type="radio" name="api-key-scope" value="api:rw" checked />
          <span data-i18n="api_keys.scope.rw">Read + write</span>
        </label>
      </fieldset>
      <div class="modal-actions">
        <button id="api-key-create-cancel" class="btn" data-i18n="confirm_dialog.cancel">Cancel</button>
        <button id="api-key-create-submit" class="btn btn-primary" data-i18n="api_keys.create">Create key</button>
      </div>
    </div>
  </div>

  <div id="api-key-reveal-modal" class="modal hidden" role="dialog" aria-modal="true">
    <div class="modal-card">
      <h3 data-i18n="api_keys.created.warning">Save this key now — you won't see it again.</h3>
      <pre id="api-key-secret-block" class="api-key-secret-block"></pre>
      <div class="modal-actions">
        <button id="api-key-copy-btn" class="btn" data-i18n="api_keys.created.copy">Copy</button>
        <button id="api-key-reveal-done" class="btn btn-primary" data-i18n="confirm_dialog.ok">Done</button>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Append the `ApiKeys` IIFE to `frontend/app.js`**

Append at the end (after the last existing IIFE — likely `SubscriptionBanner.init();`):

```javascript
// ── API Keys (Phase 2.5h) ───────────────────────────────────────────
const ApiKeys = (() => {
  async function show() {
    document.getElementById("api-key-create-modal").classList.add("hidden");
    document.getElementById("api-key-reveal-modal").classList.add("hidden");
    await refreshList();
  }

  async function refreshList() {
    const container = document.getElementById("api-keys-list");
    try {
      const body = await api("/api-keys");
      renderList(body.items || []);
    } catch (err) {
      toast(Khan.t("api_keys.error.fetch", "Failed to load API keys."), "error");
    }
  }

  function renderList(items) {
    const container = document.getElementById("api-keys-list");
    container.innerHTML = "";
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = Khan.t("api_keys.empty", "No API keys yet. Create one to get started.");
      container.appendChild(p);
      return;
    }
    items.forEach(k => container.appendChild(buildCard(k)));
  }

  function buildCard(k) {
    const card = document.createElement("div");
    card.className = "api-key-card" + (k.revoked_at ? " revoked" : "");

    const h = document.createElement("h3");
    h.textContent = k.name;
    card.appendChild(h);

    const meta = document.createElement("div");
    meta.className = "api-key-meta";
    meta.innerHTML = `
      <code>${escHtml(k.key_prefix)}…</code>
      <span class="badge">${escHtml(k.scope)}</span>
      <span class="muted">${escHtml(Khan.t("api_keys.col.created", "Created"))}: ${formatDate(k.created_at)}</span>
      <span class="muted">${escHtml(Khan.t("api_keys.col.last_used", "Last used"))}: ${k.last_used_at ? formatDate(k.last_used_at) : Khan.t("api_keys.never_used", "never")}</span>
      ${k.revoked_at ? `<span class="muted">${escHtml(Khan.t("api_keys.col.revoked", "Revoked"))}: ${formatDate(k.revoked_at)}</span>` : ""}
    `;
    card.appendChild(meta);

    if (!k.revoked_at) {
      const actions = document.createElement("div");
      actions.className = "api-key-actions";
      const revoke = document.createElement("button");
      revoke.className = "btn btn-danger";
      revoke.textContent = Khan.t("api_keys.col.revoke", "Revoke");
      revoke.addEventListener("click", () => revokeKey(k.id, k.name));
      actions.appendChild(revoke);
      card.appendChild(actions);
    }

    return card;
  }

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }
  function formatDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  function openCreateModal() {
    document.getElementById("api-key-name").value = "";
    const radios = document.querySelectorAll('input[name="api-key-scope"]');
    radios.forEach((r) => { r.checked = r.value === "api:rw"; });
    document.getElementById("api-key-create-modal").classList.remove("hidden");
  }

  async function submitCreate() {
    const name = document.getElementById("api-key-name").value.trim();
    if (!name) {
      toast(Khan.t("api_keys.name_required", "Name is required."), "error");
      return;
    }
    const scopeRadio = document.querySelector('input[name="api-key-scope"]:checked');
    const scope = scopeRadio ? scopeRadio.value : "api:rw";
    try {
      const created = await api("/api-keys", {
        method: "POST",
        body: JSON.stringify({ name, scope }),
      });
      document.getElementById("api-key-create-modal").classList.add("hidden");
      revealNewKey(created);
      await refreshList();
    } catch (err) {
      toast(err.message || Khan.t("api_keys.error.create", "Failed to create key."), "error");
    }
  }

  function revealNewKey(created) {
    document.getElementById("api-key-secret-block").textContent = created.key;
    document.getElementById("api-key-reveal-modal").classList.remove("hidden");
  }

  async function copySecret() {
    const text = document.getElementById("api-key-secret-block").textContent;
    try {
      await navigator.clipboard.writeText(text);
      toast(Khan.t("api_keys.created.copied", "Copied to clipboard."), "success");
    } catch (_) {
      toast(Khan.t("api_keys.created.copy_failed", "Copy failed — select the key and use Ctrl+C."), "error");
    }
  }

  function closeRevealModal() {
    // Clear the secret from the DOM so it doesn't linger if the user navigates back
    document.getElementById("api-key-secret-block").textContent = "";
    document.getElementById("api-key-reveal-modal").classList.add("hidden");
  }

  async function revokeKey(id, name) {
    const ok = await confirmDialog({
      title:        Khan.t("api_keys.revoke_confirm.title", "Revoke API key?"),
      message:      Khan.t("api_keys.revoke_confirm.message",
                           "Revoke API key \"{name}\"? Any agent using it will stop working immediately.")
                       .replace("{name}", name),
      confirmLabel: Khan.t("api_keys.col.revoke", "Revoke"),
      danger:       true,
    });
    if (!ok) return;
    try {
      await api(`/api-keys/${id}`, { method: "DELETE" });
      toast(Khan.t("api_keys.revoked_toast", "API key revoked."), "success");
      await refreshList();
    } catch (err) {
      toast(err.message || Khan.t("api_keys.error.revoke", "Failed to revoke."), "error");
    }
  }

  function init() {
    document.getElementById("api-key-new-btn")?.addEventListener("click", openCreateModal);
    document.getElementById("api-key-create-cancel")?.addEventListener("click", () =>
      document.getElementById("api-key-create-modal").classList.add("hidden"));
    document.getElementById("api-key-create-submit")?.addEventListener("click", submitCreate);
    document.getElementById("api-key-copy-btn")?.addEventListener("click", copySecret);
    document.getElementById("api-key-reveal-done")?.addEventListener("click", closeRevealModal);
  }

  return { show, init };
})();

ApiKeys.init();
```

- [ ] **Step 4: Wire `ApiKeys.show()` into `showSection`**

Find `function showSection(id)` (around line 137). Add at the end of the function body:

```javascript
if (id === "api-keys") ApiKeys.show();
```

- [ ] **Step 5: Admin-only nav gating in `updateAuthUI`**

Find `updateAuthUI()` (around line 221). It already gates the audit-log + schedules buttons. Add the api-keys button to the same admin-gated pattern:

```javascript
const apiKeysBtn = document.querySelector('button[data-section="api-keys"]');
// inside the if (currentUser) branch:
if (apiKeysBtn) apiKeysBtn.classList.toggle("hidden", currentUser.role !== "admin");
// inside the else branch:
if (apiKeysBtn) apiKeysBtn.classList.add("hidden");
```

- [ ] **Step 6: Add CSS to `frontend/styles.css`**

Append:

```css
/* Phase 2.5h — API keys */
.api-keys-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  margin-block-start: 16px;
}
.api-key-card {
  background: var(--surface, #fff8f0);
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 12px;
  padding: 16px;
}
.api-key-card.revoked { opacity: 0.55; }
.api-key-card h3 { margin: 0 0 8px 0; font-size: 16px; }
.api-key-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.api-key-meta code {
  font-family: monospace;
  background: var(--pre-bg, #f5ecd9);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.api-key-meta .badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--accent-bg, #fdf3d6);
  color: var(--accent-fg, #6a4a14);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  width: fit-content;
}
.api-key-actions {
  margin-block-start: 12px;
  display: flex;
  gap: 8px;
}
.api-key-secret-block {
  background: var(--pre-bg, #f5ecd9);
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 8px;
  padding: 12px;
  font-family: monospace;
  font-size: 13px;
  word-break: break-all;
  white-space: pre-wrap;
  margin-block: 16px;
}
.radio-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-block: 6px;
  font-weight: normal;
}
.muted { color: var(--muted, #8b6f5e); }
```

(Several utility classes — `.empty-state`, `.modal`, `.modal-card`, `.modal-actions`, `.btn-danger` — should already exist from prior phases. If `.empty-state` is missing, see Phase 2.5e's styles.css block.)

- [ ] **Step 7: Add 18 i18n keys to `frontend/i18n/en.json`**

Insert before the closing `}`:

```json
  "nav.api_keys": "API Keys",
  "api_keys.title": "API Keys",
  "api_keys.intro": "Programmatically manage media, playlists, screens, walls, and schedules. ",
  "api_keys.docs_link": "Read the API docs ↗",
  "api_keys.new": "+ New key",
  "api_keys.empty": "No API keys yet. Create one to get started.",
  "api_keys.field.name": "Name",
  "api_keys.field.scope": "Scope",
  "api_keys.scope.read": "Read only",
  "api_keys.scope.rw": "Read + write",
  "api_keys.create": "Create key",
  "api_keys.created.warning": "Save this key now — you won't see it again.",
  "api_keys.created.copy": "Copy",
  "api_keys.created.copied": "Copied to clipboard.",
  "api_keys.created.copy_failed": "Copy failed — select the key and use Ctrl+C.",
  "api_keys.col.created": "Created",
  "api_keys.col.last_used": "Last used",
  "api_keys.col.revoked": "Revoked",
  "api_keys.col.revoke": "Revoke",
  "api_keys.never_used": "never",
  "api_keys.name_required": "Name is required.",
  "api_keys.revoke_confirm.title": "Revoke API key?",
  "api_keys.revoke_confirm.message": "Revoke API key \"{name}\"? Any agent using it will stop working immediately.",
  "api_keys.revoked_toast": "API key revoked.",
  "api_keys.error.fetch": "Failed to load API keys.",
  "api_keys.error.create": "Failed to create key.",
  "api_keys.error.revoke": "Failed to revoke."
```

- [ ] **Step 8: Add the same keys to `frontend/i18n/ar.json` (Arabic — MSA)**

```json
  "nav.api_keys": "مفاتيح API",
  "api_keys.title": "مفاتيح API",
  "api_keys.intro": "إدارة الوسائط وقوائم التشغيل والشاشات والجداول برمجياً. ",
  "api_keys.docs_link": "اقرأ توثيق الـ API ↗",
  "api_keys.new": "+ مفتاح جديد",
  "api_keys.empty": "لا توجد مفاتيح API بعد. أنشئ مفتاحاً للبدء.",
  "api_keys.field.name": "الاسم",
  "api_keys.field.scope": "النطاق",
  "api_keys.scope.read": "قراءة فقط",
  "api_keys.scope.rw": "قراءة + كتابة",
  "api_keys.create": "إنشاء مفتاح",
  "api_keys.created.warning": "احفظ هذا المفتاح الآن — لن تراه مرة أخرى.",
  "api_keys.created.copy": "نسخ",
  "api_keys.created.copied": "تم النسخ.",
  "api_keys.created.copy_failed": "تعذّر النسخ — حدّد المفتاح واستخدم Ctrl+C.",
  "api_keys.col.created": "تم الإنشاء",
  "api_keys.col.last_used": "آخر استخدام",
  "api_keys.col.revoked": "تم الإلغاء",
  "api_keys.col.revoke": "إلغاء",
  "api_keys.never_used": "أبداً",
  "api_keys.name_required": "الاسم مطلوب.",
  "api_keys.revoke_confirm.title": "إلغاء مفتاح API؟",
  "api_keys.revoke_confirm.message": "إلغاء مفتاح API \"{name}\"؟ سيتوقف أي عميل يستخدمه عن العمل فوراً.",
  "api_keys.revoked_toast": "تم إلغاء المفتاح.",
  "api_keys.error.fetch": "تعذّر تحميل مفاتيح API.",
  "api_keys.error.create": "تعذّر إنشاء المفتاح.",
  "api_keys.error.revoke": "تعذّر الإلغاء."
```

- [ ] **Step 9: i18n parity check**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: `OK`.

- [ ] **Step 10: JS parse**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 11: Commit**

```bash
cd /home/ahmed/signage
git add frontend/index.html frontend/app.js frontend/styles.css \
        frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(api): admin "API Keys" section

Admin-only nav. List of keys (prefix, scope, created/last-used).
Create modal with name + scope radio (read vs read+write). On
success → one-time reveal modal with copy button + warning that
the key won't be shown again. Revoke flow uses confirmDialog and
soft-deletes (sets revoked_at on the row).

~27 new i18n keys EN+AR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Customer-facing API docs page

**Files:**
- Create: `frontend/api-docs.html`

**Goal:** Hand-written static HTML page at `/api-docs.html` (served by the existing nginx). Quick-start, auth, scopes, rate limits, error codes, endpoint reference.

- [ ] **Step 1: Create `frontend/api-docs.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Khanshoof API — Docs</title>
    <style>
      :root {
        --bg: #fff8f0;
        --fg: #3b2a14;
        --muted: #8b6f5e;
        --border: #e9ddc6;
        --pre-bg: #f5ecd9;
        --link: #b0653e;
        --max: 760px;
      }
      * { box-sizing: border-box; }
      html, body { margin: 0; background: var(--bg); color: var(--fg);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
        line-height: 1.55; }
      main { max-width: var(--max); margin: 0 auto; padding: 48px 24px 96px; }
      h1 { margin-block-start: 0; }
      h2 { margin-block-start: 2em; border-block-end: 1px solid var(--border); padding-block-end: 8px; }
      h3 { margin-block-start: 1.5em; }
      a { color: var(--link); }
      code { font-family: ui-monospace, monospace; font-size: 0.9em;
        background: var(--pre-bg); padding: 1px 6px; border-radius: 4px; }
      pre { background: var(--pre-bg); padding: 16px; border-radius: 8px;
        overflow-x: auto; font-size: 0.9em; line-height: 1.4; }
      pre code { background: transparent; padding: 0; }
      table { border-collapse: collapse; width: 100%; font-size: 0.95em; }
      th, td { padding: 8px 12px; text-align: left; border-block-end: 1px solid var(--border); }
      .muted { color: var(--muted); }
      .badge { display: inline-block; padding: 1px 8px; border-radius: 999px;
        background: var(--pre-bg); font-size: 0.8em; }
    </style>
  </head>
  <body>
    <main>
      <h1>Khanshoof API</h1>
      <p class="muted">v1 — agent-friendly REST API for managing media, playlists, screens, walls, and schedules.</p>

      <h2>Quick start</h2>
      <p>Create an API key from <a href="/">the admin dashboard → API Keys tab</a>. Then:</p>
      <pre><code>curl https://api.khanshoof.com/playlists \
  -H "Authorization: Bearer khan_live_..."</code></pre>

      <h2>Authentication</h2>
      <p>Every request must include an <code>Authorization</code> header:</p>
      <pre><code>Authorization: Bearer khan_live_&lt;your-key&gt;</code></pre>
      <p>Keys are tied to your organization. They start with <code>khan_live_</code> followed by ~32 random characters. Treat them like passwords — never commit them to source control or share them in client-side code.</p>

      <h2>Scopes</h2>
      <table>
        <tr><th>Scope</th><th>Allowed methods</th></tr>
        <tr><td><code>api:read</code></td><td>GET — list/read media, playlists, screens, walls, schedules</td></tr>
        <tr><td><code>api:rw</code></td><td>Everything in <code>api:read</code> plus POST/PUT/PATCH/DELETE on those resources</td></tr>
      </table>
      <p>Each API key is bound to one scope at creation time. Rotate to upgrade — there's no in-place scope change.</p>

      <h2>Rate limits</h2>
      <p>Per-key, by your organization's subscription plan:</p>
      <table>
        <tr><th>Plan</th><th>Per minute</th><th>Per hour</th></tr>
        <tr><td>Starter</td><td>30</td><td>500</td></tr>
        <tr><td>Growth</td><td>100</td><td>5,000</td></tr>
        <tr><td>Business</td><td>500</td><td>25,000</td></tr>
        <tr><td>Pro</td><td>2,000</td><td>100,000</td></tr>
        <tr><td>Enterprise</td><td>5,000</td><td>250,000</td></tr>
      </table>
      <p>When you hit the limit, responses are <code>429</code> with headers <code>Retry-After</code>, <code>X-RateLimit-Limit</code>, <code>X-RateLimit-Window</code>, <code>X-RateLimit-Remaining</code>.</p>

      <h2>Error codes</h2>
      <table>
        <tr><th>HTTP</th><th>Code</th><th>Meaning</th></tr>
        <tr><td>401</td><td>—</td><td>Missing/invalid Authorization header or revoked key</td></tr>
        <tr><td>402</td><td><code>subscription.expired</code> / <code>subscription.trial_expired</code></td><td>Your organization's subscription is expired — renew to make changes</td></tr>
        <tr><td>403</td><td><code>api.insufficient_scope</code></td><td>Key scope doesn't permit this operation (e.g., POST with <code>api:read</code> key)</td></tr>
        <tr><td>404</td><td>—</td><td>Resource not found, or belongs to another organization</td></tr>
        <tr><td>422</td><td>—</td><td>Request body validation failed</td></tr>
        <tr><td>429</td><td><code>rate_limited</code></td><td>Per-minute or per-hour rate limit hit</td></tr>
      </table>

      <h2>Endpoint reference</h2>

      <h3>Media</h3>
      <ul>
        <li><code>GET /media</code> — list your media library</li>
        <li><code>GET /media/{id}</code> — get one media item</li>
        <li><code>POST /media/upload</code> — multipart upload (returns id + url)</li>
        <li><code>POST /media/url</code> — add a URL-based media item</li>
        <li><code>DELETE /media/{id}</code> — delete a media item</li>
      </ul>

      <h3>Playlists</h3>
      <ul>
        <li><code>GET /playlists</code> · <code>GET /playlists/{id}</code></li>
        <li><code>POST /playlists</code> — create</li>
        <li><code>PUT /playlists/{id}</code> · <code>DELETE /playlists/{id}</code></li>
        <li><code>POST /playlists/{id}/items</code> — add item</li>
        <li><code>DELETE /playlists/{id}/items/{item_id}</code> — remove item</li>
      </ul>

      <h3>Sites + Screens</h3>
      <ul>
        <li><code>GET /sites</code></li>
        <li><code>GET /screens</code> · <code>GET /screens/{id}/zones</code></li>
        <li><code>PUT /screens/{id}</code> — assign a playlist or schedule</li>
      </ul>

      <h3>Walls</h3>
      <ul>
        <li><code>GET /walls</code> · <code>GET /walls/{id}</code></li>
        <li><code>GET /walls/{id}/canvas-playlist</code></li>
        <li><code>POST /walls/{id}/canvas-playlist/items</code></li>
        <li><code>PATCH /walls/{id}/canvas-playlist/items/{item_id}</code></li>
        <li><code>DELETE /walls/{id}/canvas-playlist/items/{item_id}</code></li>
      </ul>

      <h3>Schedules (dayparting)</h3>
      <ul>
        <li><code>GET /schedules</code> · <code>GET /schedules/{id}</code></li>
        <li><code>POST /schedules</code> · <code>PUT /schedules/{id}</code> · <code>DELETE /schedules/{id}</code></li>
        <li><code>PUT /schedules/{id}/rules</code> — replace all rules at once</li>
      </ul>

      <h3>Organization</h3>
      <ul>
        <li><code>GET /organization</code> — your org's plan, subscription state, screen limit</li>
      </ul>

      <h2>What's not in the API (yet)</h2>
      <ul>
        <li>Creating users, sites, or screens (paired with screen-limit logic — coming in a later phase)</li>
        <li>Triggering billing or signup flows</li>
        <li>Zone editing (advanced layout)</li>
        <li>Outbound webhooks</li>
        <li>MCP server wrapper (coming soon)</li>
      </ul>

      <h2>Support</h2>
      <p>Email <a href="mailto:support@khanshoof.com">support@khanshoof.com</a> with your <code>key_prefix</code> (the first 12 characters of your key — e.g. <code>khan_live_a1</code>). Never send your full key.</p>
    </main>
  </body>
</html>
```

- [ ] **Step 2: Rebuild the frontend container so the new file is served**

```bash
docker-compose build frontend && docker-compose up -d --force-recreate frontend
sleep 4
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/api-docs.html
```
Expected: `200`.

- [ ] **Step 3: Commit**

```bash
git add frontend/api-docs.html
git commit -m "$(cat <<'EOF'
docs(api): customer-facing API documentation page

Hand-written static page at /api-docs.html. Quick-start, auth,
scopes, rate-limits (plan tier table), error codes, endpoint
reference grouped by resource. Linked from the admin API Keys
section.

EN-only for v1. Translation is a follow-up if customer demand
materializes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Regression + push + PR

**Files:** none directly modified.

- [ ] **Step 1: Final backend test run**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `294 passed` (268 baseline + 26 new).

- [ ] **Step 2: i18n parity**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: OK.

- [ ] **Step 3: JS parse all four**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo "frontend OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/player.js','utf8'))" && echo "player OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/landing/app.js','utf8'))" && echo "landing OK"
```

- [ ] **Step 4: Verify containers healthy**

```bash
docker-compose ps | grep -E "(backend|frontend|player|landing|postgres)"
curl -s -o /dev/null -w "backend %{http_code}\nfrontend %{http_code}\napi-docs %{http_code}\n" \
  http://localhost:8000/health \
  http://localhost:3000/ \
  http://localhost:3000/api-docs.html
```

- [ ] **Step 5: Push branch**

```bash
git push -u origin feature/agent-api
```

- [ ] **Step 6: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(api): Phase 2.5h — agent-accessible API platform" \
  --body "$(cat <<'EOF'
## Summary
- Every Khanshoof org can mint scoped Bearer API keys (`khan_live_*`) from the admin dashboard. Read-only or read+write per key.
- ~30 existing REST endpoints accept either a session token OR an API key. Sessions keep role gating (`require_roles`); keys get `api:read`/`api:rw` gating. Both flow through `require_api_scope`.
- Per-tier rate limits (Starter 30/min → Enterprise 5000/min). 429s carry `Retry-After`, `X-RateLimit-Limit`, etc.
- Admin UI: new "API Keys" section with create-modal + one-time secret reveal + revoke. Customer-facing static docs at `/api-docs.html`.

## Spec
`docs/superpowers/specs/2026-05-17-agent-api-design.md`

## Plan
`docs/superpowers/plans/2026-05-17-agent-api-plan.md`

## Test Plan
- [x] Backend: 294 passed (was 268; +26 new)
- [x] `scripts/check_i18n.py` parity OK
- [x] All four JS files parse
- [x] Containers healthy
- [ ] Browser smoke: admin creates key, sees one-time reveal, revokes
- [ ] Browser smoke: AR locale renders all new strings
- [ ] curl smoke: \`curl -H "Authorization: Bearer khan_live_..." /playlists\` returns the right shape
- [ ] curl smoke: \`api:read\` key on \`POST /playlists\` returns 403 \`api.insufficient_scope\`
- [ ] curl smoke: revoked key returns 401 immediately
- [ ] curl smoke: cross-org isolation — Org A's key cannot read Org B's data

## Justified spec deviation
Spec showed \`require_api_scope("session", "api:read", "api:rw")\` with "session" as a magic scope that loses role granularity. Plan implements \`require_api_scope("api:read", "api:rw", session_roles=(...))\` so role checks are preserved for session-authed callers. Same outward behavior; cleaner internals.

## Non-goals (queued)
- Sandbox/test keys (\`khan_test_*\`)
- OAuth 2.0 client credentials
- HMAC-signed requests
- Per-resource scopes (media:write etc.)
- **MCP server wrapper — natural next phase**
- Outbound webhooks
- Redis-backed rate limiter for multi-replica correctness

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Save memory**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_agent_api.md`:

```markdown
---
name: Agent API platform (Phase 2.5h) — branch
description: Multi-tenant Bearer API keys with scopes + tier-based rate limits. ~30 endpoints dual-auth. PR pending.
type: project
---

**Status (2026-05-17):** PR #<TBD> open against main. Awaiting browser smoke + merge.

**What landed:**
- `api_keys` table: org-scoped, hashed (PBKDF2), scope CHECK constraint, prefix indexed, soft-delete via `revoked_at`.
- `generate_api_key()` + `lookup_api_key()` helpers in main.py. Format `khan_live_<32 chars>`, prefix = first 12 chars, secret stored only as hash.
- `AuthedPrincipal` + `get_api_authed` + `require_api_scope(*scopes, session_roles=)`. Dual-auth: same endpoint accepts session token OR API key. Sessions keep role gating; keys gate by scope.
- ~30 endpoints across media, playlists, sites (read), screens (read + assign), walls (read + canvas-playlist mutations), schedules — all converted to `require_api_scope`.
- `PLAN_API_LIMITS` + `_api_key_rate_check` — process-local in-memory per-key counters. 429 with full `Retry-After` + `X-RateLimit-*` headers.
- `/api-keys` CRUD endpoints (POST returns full key ONCE, GET lists prefix-only, DELETE = soft-revoke, POST `/rotate`). Session-only, admin-role-only, subscription-gated for mint+rotate.
- Admin UI: "API Keys" section (admin-gated) with list cards, create modal, one-time reveal modal with copy button, revoke flow via confirmDialog. ~27 i18n keys EN+AR.
- Customer docs: `/api-docs.html` — hand-written static page with quick-start, auth, scopes, rate limits, error codes, endpoint reference.

**Test count:** 294 backend tests passing (268 pre-branch + 26 new).

**Plan:** `docs/superpowers/plans/2026-05-17-agent-api-plan.md` — 9 tasks.
**Spec:** `docs/superpowers/specs/2026-05-17-agent-api-design.md`.

**Justified spec deviation:** spec used `"session"` as a magic scope; plan switched to a `session_roles=` kwarg on `require_api_scope` to preserve role granularity for session-authed callers. Documented in plan §0 conventions.

**v1 multi-replica caveat:** rate counters are process-local. Multi-replica → tighter per-replica, looser overall. Documented; Redis fix queued.

**Out of scope (queued for future):**
- Sandbox keys (`khan_test_*`)
- OAuth client-credentials flow
- HMAC-signed requests
- Per-resource scopes
- **MCP server wrapper — natural next-phase enabler over this API**
- Outbound webhooks from Khanshoof to customer endpoints
- Per-key IP allow-lists
- Redis-backed rate limiter
- POST/DELETE screens via API (deferred — screen-limit + pair-code logic too sensitive for v1)
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` with a one-line entry pointing at the new file.

- [ ] **Step 8: Final verification**

```bash
git status -sb
~/.local/bin/gh pr view --json number,url,state | head
```
Expected: PR open. Working tree clean except for any untracked items.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §5 schema | Task 1 |
| §6 key format + helpers | Task 2 |
| §7.1 AuthedPrincipal + get_api_authed | Task 3 |
| §7.2 require_api_scope | Task 3 (with `session_roles=` adjustment) |
| §7.3 endpoint annotations pattern | Task 3 (canonical proofs) + Task 4 (full sweep) |
| §7.4 endpoint surface (~30) | Task 4 (explicit table) |
| §8 tier-based rate limits | Task 5 |
| §9 audit-logging | Folded into Task 4 (handlers that already call `audit()` get the api-prefix actor pattern) |
| §10 management endpoints | Task 6 |
| §11 admin UI | Task 7 |
| §12 customer docs page | Task 8 |
| §13 testing | Tasks 1-6 each ship their slice of tests |
| §14 file layout | All paths match |
| §15 failure modes | Tested in Tasks 2, 3, 5, 6; some manual via PR smoke checklist |
| §16 migration | No backfill; documented in plan + PR body |

No placeholders. Symbol names + endpoint paths consistent across tasks.

Task ordering: 1 (schema) → 2 (key helpers, depends on 1) → 3 (auth + scope, depends on 2) → 4 (endpoint sweep, depends on 3) → 5 (rate limit, plugs into 3's `require_api_scope`) → 6 (management endpoints, depends on 2) → 7 (UI, depends on 6) → 8 (docs page, independent) → 9 (regression + PR).

Each task commits with all tests passing.
