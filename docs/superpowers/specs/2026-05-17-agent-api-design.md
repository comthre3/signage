# Phase 2.5h — Agent-Accessible API Platform — Design

**Date:** 2026-05-17
**Branch:** `feature/agent-api` (fresh, branched from `main` at `8cff865`)
**Predecessor merged:** Phase 2.5g subscription reminders (PR #9 squash `8cff865`).

---

## 1. Goal

Every Khanshoof customer org can mint API keys to let agents — Claude via MCP, Zapier, n8n, custom scripts — programmatically manage their media, playlists, screens, walls, and schedules. Read-only or read+write scope per key. Rate-limited by subscription tier so heavier integrations pay for heavier limits.

This is **plumbing for a programmable signage platform** — the HTTP API is the substrate. A future MCP server wrapping it is straightforward to add later.

## 2. Existing State

- ~80 REST endpoints behind session bearer auth (`/auth/login` → token). Sessions are user-scoped, 24h idle TTL.
- `require_roles(...)` dependency gates writes by role (admin/editor/viewer).
- `require_active_subscription` dependency from Phase 2.5f gates writes by subscription state.
- Per-IP slowapi rate limit on `/auth/login` (10/5min) — the only existing limit.
- `audit_log` table from Phase 2.5c — admin actions captured.
- `hash_password` / `verify_password` PBKDF2-SHA256 helpers already in main.py.

## 3. Design Choices (recap from brainstorm)

1. **Audience:** product feature — every customer org gets API keys.
2. **Surface:** content + screens subset (~30 endpoints). Excluded: `/auth/*`, `/billing/*`, `/users/*`, org-settings mutations, sites create/delete, signup, lockout.
3. **Scopes:** two — `api:read` and `api:rw`.
4. **Credential format:** Bearer API key, prefix `khan_live_<32-char base64url>`.
5. **Rate limits:** tier-based — Starter 30/min, Growth 100/min, Business 500/min, Pro 2000/min.

## 4. Non-Goals (deferred)

- Sandbox/test keys (`khan_test_*`) — YAGNI for v1; customers iterate against their own dev org.
- OAuth 2.0 client credentials flow.
- HMAC-signed requests.
- Per-resource scopes (`media:write`, `playlists:read`, etc.).
- MCP server wrapper — separate follow-up; HTTP API is the substrate.
- Outbound webhooks from Khanshoof to customer endpoints.
- Per-key expiration / TTL.
- IP allow-lists per key.
- Streaming / Server-Sent Events.

## 5. Component A — Schema

```sql
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
);
CREATE INDEX idx_api_keys_org    ON api_keys (organization_id, revoked_at);
CREATE INDEX idx_api_keys_prefix ON api_keys (key_prefix);
```

- `key_prefix`: first 12 chars (e.g., `khan_live_a1`), indexed for O(log n) lookup.
- `key_hash`: PBKDF2-SHA256, 120k iterations, 16-byte salt — same format as `users.password_hash`.
- `revoked_at IS NULL` means active. We never DELETE rows — preserves history.

## 6. Component B — Key Format + Helpers

```
khan_live_<32 chars from secrets.token_urlsafe(24)>
```

Example: `khan_live_8YqK7v3pNw2dQfR4tEa6XmZ_9LbCs0HjPgVuI1KoBnA`

- `khan_` — vendor identifier (helps secret scanners recognize Khanshoof keys).
- `live_` — environment marker (only `live` in v1; `test` reserved for future).
- 32 chars of base64url-safe entropy (~190 bits).

**`backend/main.py` helpers:**

```python
import secrets

API_KEY_PREFIX_LEN = 12   # "khan_live_" + 2 chars from the random suffix


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Caller stores prefix + hash; returns
    full_key to the operator ONCE (never seen again)."""
    suffix = secrets.token_urlsafe(24)
    full_key = f"khan_live_{suffix}"
    prefix = full_key[:API_KEY_PREFIX_LEN]
    hashed = hash_password(full_key)  # re-uses existing PBKDF2 helper
    return full_key, prefix, hashed


def lookup_api_key(bearer_token: str) -> Optional[dict]:
    """Return active api_key row if the bearer matches; else None.
    Fire-and-forget update of last_used_at."""
    if not bearer_token.startswith("khan_live_"):
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
                logger.warning("api_key_last_used_update_failed id=%s err=%s", row["id"], exc)
            return row
    return None
```

The fire-and-forget `last_used_at` write swallows DB errors so a transient hiccup doesn't 500 the API call.

## 7. Component C — Auth + Scope Gates

### 7.1 `get_api_authed` — dual-mode auth dependency

Replaces `get_current_user` only on endpoints we want to expose to keys. Existing admin-only endpoints keep `get_current_user`.

```python
class AuthedPrincipal:
    """Carries whichever identity authenticated the request."""
    def __init__(self, *, user=None, api_key=None, organization_id: int, scope: str):
        self.user = user                  # dict | None
        self.api_key = api_key            # dict | None
        self.organization_id = organization_id
        self.scope = scope                # "session" | "api:read" | "api:rw"


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

    # Fall through to session lookup (existing logic)
    user = _session_lookup(token)   # extracted from existing get_current_user body
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return AuthedPrincipal(
        user=user,
        organization_id=user["organization_id"],
        scope="session",
    )
```

### 7.2 `require_api_scope` — second-layer scope check

```python
def require_api_scope(*allowed: str):
    """allowed: subset of {'session', 'api:read', 'api:rw'}."""
    def dep(principal: AuthedPrincipal = Depends(get_api_authed)) -> AuthedPrincipal:
        if principal.scope not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "code":    "api.insufficient_scope",
                    "message": f"This endpoint requires one of: {', '.join(allowed)}",
                    "scope":   principal.scope,
                },
            )
        return principal
    return dep
```

### 7.3 Endpoint annotations

For each endpoint in the agent surface:

```python
# Before:
@app.get("/playlists")
def list_playlists(user: dict = Depends(require_roles("admin", "editor", "viewer"))) -> list:
    ...

# After:
@app.get("/playlists")
def list_playlists(
    principal: AuthedPrincipal = Depends(require_api_scope("session", "api:read", "api:rw")),
) -> list:
    user = principal.user  # may be None when API-keyed
    ...
```

Handler bodies that read `user.get("organization_id")` swap to `principal.organization_id` — same value either way. Audit-log calls that need a username substitute `api:<prefix>` when `principal.user is None`.

### 7.4 Endpoints in the agent surface (~30)

Read endpoints (`api:read` + `api:rw` + `session`):
- `GET /media`, `GET /media/{id}`
- `GET /playlists`, `GET /playlists/{id}`
- `GET /sites`, `GET /sites/{id}`
- `GET /screens`, `GET /screens/{id}`, `GET /screens/{id}/zones`
- `GET /walls`, `GET /walls/{id}`
- `GET /schedules`, `GET /schedules/{id}`
- `GET /organization` (the calling org only — no cross-org access)

Write endpoints (`api:rw` + `session`):
- `POST /media/upload`, `POST /media/url`, `DELETE /media/{id}`
- `POST /playlists`, `PUT /playlists/{id}`, `DELETE /playlists/{id}`
- `POST /playlists/{id}/items`, `DELETE /playlists/{id}/items/{item_id}`
- `PUT /screens/{id}` (assign playlist / schedule — NOT general settings)
- `POST /walls/{id}/canvas-playlist/items`, `PATCH /walls/{id}/canvas-playlist/items/{item_id}`, `DELETE /walls/{id}/canvas-playlist/items/{item_id}`
- `POST /schedules`, `PUT /schedules/{id}`, `DELETE /schedules/{id}`, `PUT /schedules/{id}/rules`

Excluded (still session-only via `require_roles` + existing `get_current_user`):
- `/auth/*`, `/billing/*`, `/users/*`
- `POST /sites`, `DELETE /sites/{id}` (sites are billing-adjacent)
- `POST /screens`, `DELETE /screens/{id}` (creating/deleting screens triggers pairing flow + screen-limit checks — surface area too sensitive for v1)
- `POST /screens/request_code`, `POST /screens/claim`, `POST /walls/cells/redeem`, `POST /screens/pair` (display-side / pairing flows)
- All `PUT /screens/{id}/zones` etc. (advanced; future phase)

This is a conscious narrow cut — covers the 95% case of agent-driven content management without exposing org-restructuring power.

## 8. Component D — Tier-Based Rate Limits

`slowapi` keyed on the API key ID. Limits read from `PLAN_API_LIMITS` constant:

```python
PLAN_API_LIMITS = {
    "starter":    {"per_minute": 30,   "per_hour": 500},
    "growth":     {"per_minute": 100,  "per_hour": 5000},
    "business":   {"per_minute": 500,  "per_hour": 25000},
    "pro":        {"per_minute": 2000, "per_hour": 100000},
    "enterprise": {"per_minute": 5000, "per_hour": 250000},
}
```

### 8.1 Implementation pattern

Custom key function + per-call check (we don't use slowapi's decorator because the rate depends on the org's plan):

```python
import time
from collections import defaultdict

_rate_buckets = defaultdict(lambda: {"min": [], "hour": []})


def _api_key_rate_check(principal: AuthedPrincipal):
    """Raise 429 if the principal's API key has exceeded its tier limits.
    Sessions are NOT rate-limited here (existing slowapi covers login)."""
    if principal.api_key is None:
        return  # session — out of scope for this limiter
    key_id = principal.api_key["id"]
    org = query_one("SELECT plan FROM organizations WHERE id = ?",
                    (principal.organization_id,))
    plan = (org or {}).get("plan", "starter")
    limits = PLAN_API_LIMITS.get(plan, PLAN_API_LIMITS["starter"])

    now = time.time()
    bucket = _rate_buckets[key_id]
    # Drop entries older than each window
    bucket["min"]  = [t for t in bucket["min"]  if t > now - 60]
    bucket["hour"] = [t for t in bucket["hour"] if t > now - 3600]

    if len(bucket["min"]) >= limits["per_minute"]:
        oldest = min(bucket["min"])
        retry_after = max(1, int(60 - (now - oldest)))
        raise HTTPException(status_code=429, headers={
            "Retry-After":           str(retry_after),
            "X-RateLimit-Limit":     str(limits["per_minute"]),
            "X-RateLimit-Window":    "60",
            "X-RateLimit-Remaining": "0",
        }, detail={"code": "rate_limited", "message": "Per-minute rate limit exceeded"})

    if len(bucket["hour"]) >= limits["per_hour"]:
        oldest = min(bucket["hour"])
        retry_after = max(1, int(3600 - (now - oldest)))
        raise HTTPException(status_code=429, headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit":     str(limits["per_hour"]),
            "X-RateLimit-Window":    "3600",
            "X-RateLimit-Remaining": "0",
        }, detail={"code": "rate_limited", "message": "Per-hour rate limit exceeded"})

    bucket["min"].append(now)
    bucket["hour"].append(now)
```

The `_rate_buckets` is a process-local in-memory store. **Multi-replica caveat:** each replica counts independently → effective limits are N× tighter in a single-replica setup, N× looser overall. For v1 (single replica) this is fine. Future fix: Redis.

### 8.2 Wiring

`require_api_scope` is extended to call `_api_key_rate_check(principal)` before returning. Single point of enforcement. Session-authed requests skip the check (existing slowapi covers login; everything else is web-app UX).

## 9. Component E — Audit Logging

The existing `audit()` helper from Phase 2.5c receives an extra discriminator. When a request is API-keyed, audit rows use:

- `actor_user_id`: NULL (the principal isn't a user)
- `actor_username`: `api:<prefix>` (e.g., `api:khan_live_a1`)
- `details.api_key_id`: the numeric id

That makes audit traces filterable by API key in the existing Audit Log admin UI.

New audit action types:
- `api_key.create` — key minted
- `api_key.revoke` — key revoked

(These are admin actions, audit-logged with the admin's normal user identity.)

## 10. Component F — Key Management Endpoints

```
POST   /api-keys                       admin only (session) — mint a key
GET    /api-keys                       admin only (session) — list (prefix only, no secret)
DELETE /api-keys/{id}                  admin only (session) — revoke (sets revoked_at)
POST   /api-keys/{id}/rotate           admin only (session) — revoke + create fresh with same name + scope
```

These endpoints are **session-only** — an API key cannot mint or revoke other API keys (privilege-escalation prevention). All require `role='admin'`. Mint + rotate also gate on `require_active_subscription` from Phase 2.5f, so an expired-trial org can't mint usable keys; revoke + list stay accessible even when expired so admins can clean up.

**Response shape:**

```jsonc
// POST /api-keys returns ONCE only:
{
  "id": 7,
  "name": "Zapier integration",
  "key_prefix": "khan_live_a1",
  "key": "khan_live_a1b2c3D4e5F6...",   // ← full key, returned ONCE
  "scope": "api:rw",
  "created_at": "2026-05-17T10:00:00Z"
}

// GET /api-keys returns:
{
  "items": [
    {
      "id": 7,
      "name": "Zapier integration",
      "key_prefix": "khan_live_a1",     // ← prefix only; full key never re-shown
      "scope": "api:rw",
      "created_at": "2026-05-17T10:00:00Z",
      "last_used_at": "2026-05-17T10:42:31Z",
      "revoked_at": null
    }
  ]
}
```

## 11. Component G — Admin UI

New "API Keys" tab in admin nav (admin-only, hidden for editor/viewer/null org). Position: between "Audit log" and "Billing".

Section markup:
- Page header: "API Keys" + "+ New key" button
- List view: card per key with name, prefix, scope, created/last-used timestamps, "Revoke" button
- New-key modal:
  - Name input (required)
  - Scope radio (`Read only` / `Read + write`)
  - "Create key" submit
  - On success → modal shifts to "Save this key now — you won't see it again" view with the full key, a copy button, and a "Done" button. Clicking Done clears the key from memory.
- Revoke confirms via existing `confirmDialog`

Visible cues:
- Active keys show their prefix as a `<code>` block
- Revoked keys grey out + show "Revoked <date>"
- Empty state explains the API briefly + links to `/api/docs`

i18n keys (~15 new, EN + AR):

```
nav.api_keys
api_keys.title
api_keys.new
api_keys.field.name
api_keys.field.scope
api_keys.scope.read
api_keys.scope.rw
api_keys.create
api_keys.created.warning            "Save this key now — you won't see it again."
api_keys.created.copy
api_keys.col.prefix
api_keys.col.scope
api_keys.col.created
api_keys.col.last_used
api_keys.col.revoke
api_keys.revoke_confirm.title
api_keys.revoke_confirm.message
api_keys.never_used
api_keys.docs_link
```

## 12. Component H — Customer-Facing Docs

A new static page at `https://app.khanshoof.com/api-docs` (served by frontend nginx). Initially:

- Quick-start: "Mint a key, set `Authorization: Bearer ...`, hit `https://api.khanshoof.com/playlists`."
- Authentication section: bearer format, scopes
- Rate limits table (per-plan)
- Error codes: 401 invalid_token, 403 api.insufficient_scope, 429 rate_limited
- Endpoint reference: link to OpenAPI JSON at `https://api.khanshoof.com/api/openapi.json` (FastAPI already produces this; we just need to enable it in prod for the agent surface only)

Initial doc page is hand-written HTML. Future: a Stripe-style live doc explorer. YAGNI.

## 13. Testing

`backend/tests/test_api_keys.py`, ~25 tests:

**Schema + key format (4):**
- `test_api_keys_table_exists`
- `test_generate_api_key_format`
- `test_generate_api_key_prefix_is_first_12_chars`
- `test_create_api_key_stores_hash_not_plaintext`

**Lookup (4):**
- `test_lookup_returns_row_for_valid_key`
- `test_lookup_returns_none_for_unknown_prefix`
- `test_lookup_returns_none_for_revoked_key`
- `test_lookup_updates_last_used_at`

**Auth dependency (5):**
- `test_get_api_authed_accepts_session_token`
- `test_get_api_authed_accepts_api_key`
- `test_get_api_authed_rejects_missing_header`
- `test_get_api_authed_rejects_bad_scheme`
- `test_get_api_authed_rejects_unknown_key`

**Scope gate (4):**
- `test_read_scope_can_GET_playlists`
- `test_read_scope_cannot_POST_playlists`
- `test_rw_scope_can_POST_playlists`
- `test_session_passes_all_scope_gates`

**Org scoping (2):**
- `test_api_key_cannot_see_other_orgs_playlists`
- `test_api_key_cannot_modify_other_orgs_playlists`

**Management endpoints (4):**
- `test_post_api_keys_returns_full_key_once`
- `test_get_api_keys_never_returns_full_key`
- `test_delete_api_keys_revokes_not_deletes`
- `test_revoked_key_cannot_authenticate`

**Rate limiting (2):**
- `test_rate_limit_enforced_per_minute`
- `test_429_includes_retry_after_header`

## 14. File Layout

| File | Change |
|---|---|
| `backend/db.py` | New `api_keys` table + 2 indices |
| `backend/main.py` | `generate_api_key`, `lookup_api_key`, `AuthedPrincipal`, `get_api_authed`, `require_api_scope`, `_api_key_rate_check`, `PLAN_API_LIMITS`, 4 management endpoints; convert ~30 existing endpoints to use the new auth dep |
| `backend/tests/test_api_keys.py` | NEW — ~25 tests |
| `frontend/index.html` | Nav button + new section + new-key modal |
| `frontend/app.js` | `ApiKeys` IIFE (~200 lines) — list, create-modal flow with one-time secret reveal |
| `frontend/styles.css` | `.api-key-list`, `.api-key-card`, `.api-key-modal-reveal`, `.api-key-secret-block` |
| `frontend/i18n/en.json`, `frontend/i18n/ar.json` | ~15 new keys each |
| `frontend/api-docs.html` | NEW — hand-written customer docs page |

## 15. Failure Modes

| Failure | Behavior |
|---|---|
| API key with valid prefix but tampered suffix | `verify_password` fails → 401 |
| Revoked key reused | `revoked_at IS NOT NULL` filters it out at query time → 401 |
| `last_used_at` UPDATE fails | Swallow + log warning; auth still succeeds |
| `_rate_buckets` grows unbounded | Per-process memory; rows are pruned on every check (only entries within 1-hour window kept). Pathological case: 2000 keys × 2000 timestamps each ≈ 4M floats ≈ 32 MB. Acceptable for v1. |
| Org plan unknown / missing | Fall back to `starter` limits |
| User deletes key creator | `ON DELETE SET NULL` on `created_by_user_id` — key stays active |
| Org deleted | `ON DELETE CASCADE` — all keys go with the org |
| Multi-replica race on counter | Each replica counts independently → effective limits looser per-replica. Documented as v1 limitation. |
| Audit log write fails on key create/revoke | Best-effort via existing audit helper; never blocks the management op |
| API key hits a subscription-gated endpoint when org's trial expired | Phase 2.5f's `require_active_subscription` still fires → 402 (consistent with session behavior) |

## 16. Migration / Rollout

- `CREATE TABLE IF NOT EXISTS` — idempotent.
- No existing data needs migration; the feature is opt-in per org (no API keys until they mint one).
- Existing session-authed users unaffected — they keep using their bearer-session tokens.
- After deploy, customers who land on the new "API Keys" tab discover the feature naturally.
- The conversion of ~30 endpoints from `require_roles` to `require_api_scope` preserves session-auth behavior — every existing test should continue to pass.

## 17. Future Phases (queued)

- **MCP server** — Anthropic's Model Context Protocol wrapper over this API. Single binary that customers run locally (or we host) bridging Claude Desktop / Claude Code to their Khanshoof org. Trivial layer once HTTP is solid.
- **Sandbox keys** (`khan_test_*`) — separate org-shadow for safe iteration.
- **Outbound webhooks** — Khanshoof POSTs to customer URLs on events (`screen.online`, `playlist.changed`, etc.).
- **Per-resource scopes** — `media:write`, `playlists:read`, etc.
- **OAuth 2.0 client credentials** — for enterprise customers.
- **Streaming / SSE** — live `screen.online`/`screen.offline` events for monitoring dashboards.
- **Redis-backed rate limiter** — multi-replica correctness.
- **IP allow-lists per key** — enterprise security feature.
