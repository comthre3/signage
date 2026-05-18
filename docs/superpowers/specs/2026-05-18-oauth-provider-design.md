# Phase 2.5i-1 — OAuth 2.1 + PKCE Authorization Server — Design

**Date:** 2026-05-18
**Branch:** `feature/oauth-provider` (fresh, branched from `main` at `2843d6d`)
**Predecessor merged:** Phase 2.5h agent API (PR #11 squash `2843d6d`).
**Successor (queued):** Phase 2.5i-2 — MCP server wrapping this OAuth provider.

---

## 1. Goal

Build an OAuth 2.1 + PKCE authorization server that issues access + refresh tokens scoped to a Khanshoof organization. Mounted in the existing FastAPI backend at `/oauth/*` and `/.well-known/*`. The tokens it issues will be consumed by the Phase 2.5i-2 MCP server, but the OAuth provider itself is independently useful and verifiable.

## 2. Existing State

- Phase 2.5h shipped a Bearer API key system (`khan_live_*`) with `api:read` / `api:rw` scopes. Auth dependency `get_api_authed` + scope dep `require_api_scope` exist.
- Session auth via `/auth/login` returns a bearer session token stored in `sessions` table.
- `hash_password` / `verify_password` PBKDF2 helpers and `secrets.token_urlsafe` pattern from Phase 2.5h are reusable.
- No OAuth provider, no third-party app concept, no consent UI.

## 3. Design Choices (recap from brainstorm)

1. **Hosting:** mounted in existing FastAPI (`/oauth/*` + `/.well-known/*`). Same container, same DB, same deploy.
2. **Transport:** standard HTTPS endpoints. SSE only in the MCP phase (2.5i-2).
3. **PKCE-only, no client secrets** — all clients are public per OAuth 2.1 and MCP guidance. S256 challenge method required; `plain` rejected.
4. **Dynamic client registration** (RFC 7591) — `POST /oauth/register` self-serves any MCP client at runtime. Rate-limited per IP.
5. **Pre-registered clients** with friendly names: `claude-desktop`, `claude-code`, `cursor`, `zed`. Inserted at `init_db()` seed time.
6. **Scopes:** reuse `api:read` and `api:rw` from Phase 2.5h. One concept, two flavors.
7. **Token format:** opaque random 32 bytes, base64url-encoded. Hashed in DB (PBKDF2 same as `api_keys`).
8. **Token lifetimes:** access 1h, refresh 90d sliding (refresh expiry resets on each use).
9. **Refresh token rotation:** OAuth 2.1 mandates rotation. Each refresh issues a new access AND new refresh, invalidating both old tokens.
10. **Consent UI:** server-rendered HTML pages (Jinja2 templates served from FastAPI), branded to match dashboard.

## 4. Non-Goals for THIS phase

- **MCP server, SSE endpoint, tool definitions** — Phase 2.5i-2.
- **Admin "Connections" tab** — Phase 2.5i-3.
- **Token introspection endpoint (RFC 7662)** — useful for resource servers; not needed when the auth server and resource server are the same FastAPI app. Queued for later if MCP needs it.
- **Pushed authorization requests (RFC 9126)** — advanced feature, no v1 driver.
- **Per-tool consent** — single scope choice (read vs rw) at consent time.
- **Token binding** — out of scope.
- **JAR / signed authorization requests** — out of scope.
- **Multi-org support per token** — every token is org-scoped. A user who admin's multiple orgs needs separate consent grants per org.

## 5. Component A — Schema (3 new tables)

```sql
-- Registered OAuth clients (both dynamic and pre-registered)
CREATE TABLE IF NOT EXISTS oauth_clients (
  id              SERIAL PRIMARY KEY,
  client_id       TEXT NOT NULL UNIQUE,           -- e.g., "claude-desktop", "dyn_a1b2c3..."
  client_name     TEXT NOT NULL,                  -- display name for consent screen
  redirect_uris   JSONB NOT NULL,                 -- list of exact-match strings
  pre_registered  BOOLEAN NOT NULL DEFAULT false,
  registered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_client_id ON oauth_clients (client_id);

-- Authorization codes (single-use, ~10-min TTL)
CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
  id              SERIAL PRIMARY KEY,
  code_hash       TEXT NOT NULL UNIQUE,           -- PBKDF2(code)
  client_id       TEXT NOT NULL,                  -- references oauth_clients.client_id by string
  organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope           TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
  redirect_uri    TEXT NOT NULL,                  -- the one used during /authorize
  code_challenge  TEXT NOT NULL,                  -- PKCE S256 challenge
  expires_at      TIMESTAMPTZ NOT NULL,
  consumed_at     TIMESTAMPTZ,                    -- single-use enforcement
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_hash ON oauth_authorization_codes (code_hash);

-- Access + refresh tokens
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
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_access  ON oauth_tokens (access_token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh ON oauth_tokens (refresh_token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_org     ON oauth_tokens (organization_id, revoked_at);
```

Why three tables and not two:
- Codes are short-lived (10 min), single-use, very different semantics from tokens.
- Tokens are long-lived, refreshable, queryable for the future admin connections UI.
- Mixing them produces a column-zoo with NULLs everywhere.

`user_id` on `oauth_tokens` is `ON DELETE SET NULL` — if a user is deleted, their tokens remain queryable in the audit trail but become unusable (the auth check would refuse a token whose user no longer exists).

`pending_auth` (the in-flight state between `/authorize` and `/authorize/decision`) lives in-memory only — keyed by a random `request_id`, 10-min TTL, no DB row. Lost on process restart, but the failure mode is benign (customer clicks Connect again).

## 6. Component B — Discovery Endpoints

### 6.1 `GET /.well-known/oauth-authorization-server` (RFC 8414)

Returns JSON describing this authorization server:

```json
{
  "issuer": "https://api.khanshoof.com",
  "authorization_endpoint": "https://api.khanshoof.com/oauth/authorize",
  "token_endpoint": "https://api.khanshoof.com/oauth/token",
  "revocation_endpoint": "https://api.khanshoof.com/oauth/revoke",
  "registration_endpoint": "https://api.khanshoof.com/oauth/register",
  "scopes_supported": ["api:read", "api:rw"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["none"],
  "code_challenge_methods_supported": ["S256"],
  "service_documentation": "https://app.khanshoof.com/api-docs.html"
}
```

The `issuer` value is read from `APP_URL` env var (default `https://api.khanshoof.com`). Public, no auth.

### 6.2 `GET /.well-known/oauth-protected-resource` (RFC 9728)

Returns metadata declaring the API + MCP routes are OAuth-protected:

```json
{
  "resource": "https://api.khanshoof.com",
  "authorization_servers": ["https://api.khanshoof.com"],
  "scopes_supported": ["api:read", "api:rw"],
  "bearer_methods_supported": ["header"]
}
```

This is what MCP clients fetch to discover the OAuth server.

## 7. Component C — Dynamic Client Registration

### 7.1 `POST /oauth/register` (RFC 7591)

Request body:

```json
{
  "client_name": "My MCP Integration",
  "redirect_uris": [
    "http://localhost:3000/oauth/callback",
    "claude-desktop://oauth/callback"
  ]
}
```

Server response (201 Created):

```json
{
  "client_id": "dyn_aBcDeFgHiJkLmNoPqRsTuV",
  "client_name": "My MCP Integration",
  "redirect_uris": [...],
  "client_id_issued_at": 1747500000,
  "token_endpoint_auth_method": "none"
}
```

No `client_secret` returned — all clients are public per OAuth 2.1 + PKCE.

Validation:
- `client_name` 1-200 chars
- `redirect_uris` is a non-empty list of strings
- Each URI is one of:
  - `http://localhost[:PORT]/...` (for local development MCP clients)
  - `http://127.0.0.1[:PORT]/...`
  - Any `https://` URL
  - A custom scheme matching `^[a-z][a-z0-9+\-.]*://`
- Reject `data:`, `file:`, `javascript:` schemes outright
- Maximum 10 redirect_uris per client

Rate limit: 5 registrations per IP per minute (slowapi key on `get_remote_address`).

### 7.2 Pre-registered clients (seed)

At `init_db()` time, INSERT (if not present) rows for known MCP clients:

```python
PRE_REGISTERED_CLIENTS = [
    ("claude-desktop", "Claude Desktop", ["claude-desktop://oauth/callback", "http://localhost:5173/oauth/callback"]),
    ("claude-code",    "Claude Code",    ["claude-code://oauth/callback"]),
    ("cursor",         "Cursor",         ["cursor://oauth/callback"]),
    ("zed",            "Zed",            ["zed://oauth/callback"]),
]
```

The `redirect_uris` for pre-registered clients should be conservative — exact-match localhost + their custom scheme. If we get this wrong for a real client, we can fix it later (the client just dynamically re-registers on first try).

## 8. Component D — Authorization Endpoint

### 8.1 `GET /oauth/authorize`

Query params:
- `response_type=code` (required, only value supported)
- `client_id` (required)
- `redirect_uri` (required, must exact-match one of the client's registered URIs)
- `scope` (required, `api:read` or `api:rw`)
- `state` (required, opaque CSRF token chosen by client)
- `code_challenge` (required, base64url SHA256)
- `code_challenge_method=S256` (required, `plain` rejected)

Flow:
1. Validate `client_id` exists in `oauth_clients`. If not, render an error page (don't redirect — the redirect_uri can't be trusted yet).
2. Validate `redirect_uri` matches one of the client's. If not, error page.
3. Validate `response_type=code`, `code_challenge_method=S256`. If invalid, redirect to `redirect_uri` with `error=invalid_request`.
4. Validate `scope` is `api:read` or `api:rw`. If invalid, redirect with `error=invalid_scope`.
5. Generate a random `request_id`; store the params in the in-memory pending-auth dict with 10-min TTL.
6. Check for a session cookie:
   - If valid → skip to step 7 with the user already identified.
   - If absent or invalid → render the login template, action POST to `/oauth/login`, hidden field carries `request_id`.
7. Render `templates/oauth_consent.html`:
   - Shows `client_name` (from `oauth_clients`)
   - Shows the org name the user is currently signed in as
   - Shows the requested scope label (radio: "Read only" / "Read + write", defaulting to whatever the client requested)
   - Allow / Deny buttons
   - Hidden field with `request_id`
   - Form POSTs to `/oauth/authorize/decision`

### 8.2 `POST /oauth/login`

Handles the in-flow login when the user wasn't already signed in:
- Form fields: `username`, `password`, `request_id`
- Looks up the user, verifies password (reuses existing helpers + lockout logic from Phase 2.5c)
- On success: sets an HTTP-only session cookie + 302-redirects back to `/oauth/authorize?resume=<request_id>`
- On failure: re-renders login with error message

`/oauth/authorize` is re-entrant: when called with `resume=<request_id>`, it looks up the pending-auth entry (still valid for the full 10-min window), confirms the now-existing session cookie matches a user, and skips straight to rendering the consent page (step 7 of 8.1). All original query params are recovered from the pending-auth entry — the client's authorize URL never needs to be reconstructed by `/oauth/login`.

The session cookie reuses the existing session token pattern. It's set as HTTP-only so the consent flow reads it server-side without exposing the token to JS.

**Important:** Phase 2.5c lockout still applies — 5 failed attempts in 15 min triggers 429, same as the JSON `/auth/login` endpoint.

### 8.3 `POST /oauth/authorize/decision`

Form body:
- `request_id`
- `decision`: `allow` or `deny`
- `scope`: `api:read` or `api:rw` (the user's chosen scope, defaults to what client requested)

Flow:
1. Look up the pending-auth entry by `request_id`. If missing or expired → 400.
2. Verify the session cookie matches a user.
3. If `decision == "deny"`:
   - Redirect to `redirect_uri` with `error=access_denied&state=<state>`.
4. If `decision == "allow"`:
   - Generate a 32-byte random `code` (base64url-encoded)
   - Insert `oauth_authorization_codes` row: hash of code, client_id, org, user, scope, redirect_uri (the exact one from the authorize request), code_challenge, `expires_at = now + 10min`.
   - Delete the pending-auth entry.
   - Redirect to `redirect_uri?code=<code>&state=<state>`.

## 9. Component E — Token Endpoint

### 9.1 `POST /oauth/token`

Content type: `application/x-www-form-urlencoded` (standard OAuth)

**Grant: authorization_code**

Form fields:
- `grant_type=authorization_code`
- `code` (the value from the authorize redirect)
- `redirect_uri` (must exact-match the one used in /authorize)
- `client_id`
- `code_verifier` (the PKCE verifier — server hashes and compares to stored challenge)

Flow:
1. Look up `oauth_authorization_codes` by hash of provided `code`.
2. Validate:
   - Row exists, `consumed_at IS NULL`, `expires_at > now()`
   - `client_id` matches the stored value
   - `redirect_uri` matches the stored value (exact string)
   - `BASE64URL(SHA256(code_verifier))` matches the stored `code_challenge`
3. On any failure → 400 `invalid_grant`.
4. Mark code consumed: `UPDATE oauth_authorization_codes SET consumed_at = now() WHERE id = ?`.
5. Generate access_token + refresh_token (32 bytes each), hash both.
6. Insert `oauth_tokens` row.
7. Return `{access_token, refresh_token, token_type: "Bearer", expires_in: 3600, scope}`.

**Grant: refresh_token**

Form fields:
- `grant_type=refresh_token`
- `refresh_token`
- `client_id`

Flow:
1. Look up `oauth_tokens` by hash of provided refresh_token.
2. Validate `revoked_at IS NULL`, `refresh_expires_at > now()`.
3. **Refresh token rotation (OAuth 2.1 mandatory):**
   - Mark current row revoked: `UPDATE oauth_tokens SET revoked_at = now() WHERE id = ?`
   - Generate fresh access + refresh tokens (32 bytes each).
   - Insert new `oauth_tokens` row with extended `refresh_expires_at = now + 90d`.
4. Return new tokens.

**Replay attack note:** if an attacker steals a refresh token and uses it, then the legitimate user later tries to use the same refresh token, both succeed → at most one wins the race for that one rotation, the other gets `invalid_grant`. This is acceptable because: (a) PKCE prevents intermediaries from grabbing tokens; (b) we hash tokens at rest; (c) tokens transit over HTTPS only. A more defensive design would invalidate ALL tokens for the org on refresh-token-reuse detection — future work.

## 10. Component F — Revocation Endpoint

### 10.1 `POST /oauth/revoke` (RFC 7009)

Form fields:
- `token` (either access or refresh)
- `client_id`
- `token_type_hint=access_token|refresh_token` (optional)

Flow:
1. Hash the token. Look up by access_token_hash first, then refresh_token_hash (or use hint).
2. If found: `UPDATE oauth_tokens SET revoked_at = now() WHERE id = ? AND revoked_at IS NULL`. Both access and refresh effectively dead now (the auth check excludes revoked rows).
3. Return 200 OK always — per RFC 7009, the server MUST NOT reveal whether the token was valid.

## 11. Component G — Auth Integration (read-side)

The new `oauth_tokens` table is also a valid Bearer source. Extend `get_api_authed` from Phase 2.5h to recognize OAuth access tokens:

```python
def get_api_authed(authorization: Optional[str] = Header(None)) -> AuthedPrincipal:
    ...
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(...)

    # Try API key first (existing — prefix khan_live_)
    key_row = lookup_api_key(token)
    if key_row:
        return AuthedPrincipal(api_key=key_row, ...)

    # Try OAuth access token (NEW — opaque random, no prefix)
    oauth_row = lookup_oauth_access_token(token)
    if oauth_row:
        return AuthedPrincipal(
            api_key=None,
            user=None,
            organization_id=oauth_row["organization_id"],
            scope=oauth_row["scope"],   # api:read / api:rw — same gate as keys
        )

    # Fall through to session
    user = _session_lookup(token)
    ...
```

`lookup_oauth_access_token(token)`:
- Hash token; look up `oauth_tokens` by `access_token_hash`.
- Verify `revoked_at IS NULL AND access_expires_at > now()`.
- Fire-and-forget update `last_used_at = now()`.
- Return row or None.

**Important — OAuth tokens are not API keys but share the same gate.** Any endpoint behind `require_api_scope("api:read", ...)` accepts OAuth tokens with that scope. Phase 2.5h's whole agent surface becomes accessible via OAuth automatically.

The existing `lookup_api_key` continues to handle `khan_live_*`-prefixed tokens. OAuth tokens don't have a prefix (just opaque random) so the order of try-API-key-first is safe — non-matching prefix returns None instantly.

## 12. Component H — Consent UI

Two minimal Jinja2 templates in `backend/templates/`:

### 12.1 `oauth_login.html`

```html
<!doctype html>
<html lang="{{ locale }}">
  <head>
    <meta charset="utf-8">
    <title>Sign in to Khanshoof</title>
    <link rel="stylesheet" href="/oauth-consent.css">
  </head>
  <body class="oauth-page">
    <main class="oauth-card">
      <h1>Sign in to Khanshoof</h1>
      <p class="muted">to continue authorizing <strong>{{ client_name }}</strong></p>
      {% if error %}<p class="error">{{ error }}</p>{% endif %}
      <form method="POST" action="/oauth/login">
        <input type="hidden" name="request_id" value="{{ request_id }}">
        <label>
          Email
          <input type="email" name="username" required autofocus>
        </label>
        <label>
          Password
          <input type="password" name="password" required>
        </label>
        <button type="submit" class="btn btn-primary">Sign in</button>
      </form>
    </main>
  </body>
</html>
```

### 12.2 `oauth_consent.html`

```html
<!doctype html>
<html lang="{{ locale }}">
  <head>
    <meta charset="utf-8">
    <title>Authorize {{ client_name }}</title>
    <link rel="stylesheet" href="/oauth-consent.css">
  </head>
  <body class="oauth-page">
    <main class="oauth-card">
      <h1>Authorize <strong>{{ client_name }}</strong>?</h1>
      <p>
        {{ client_name }} is requesting access to your Khanshoof organization
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

### 12.3 `oauth-consent.css`

Served as a static file from the frontend container. Minimal, brand-matching styles. Reuses CSS variables defined in `/api-docs.html` for consistency.

The CSS file lives in `frontend/oauth-consent.css` (served by frontend nginx at `https://app.khanshoof.com/oauth-consent.css`). Templates load it cross-origin from there — fine because it's just CSS, no JS.

## 13. Testing

`backend/tests/test_oauth.py` — ~40 tests organized by component:

**Schema (3):**
- `test_oauth_clients_table_exists`
- `test_oauth_authorization_codes_table_exists`
- `test_oauth_tokens_table_exists`

**Pre-registered clients (2):**
- `test_pre_registered_clients_seeded`
- `test_pre_registered_clients_have_friendly_names`

**Discovery (3):**
- `test_well_known_authorization_server_metadata`
- `test_well_known_protected_resource_metadata`
- `test_well_known_no_auth_required`

**Dynamic registration (5):**
- `test_register_creates_client_with_dyn_prefix`
- `test_register_validates_redirect_uri_schemes`
- `test_register_rejects_data_url_scheme`
- `test_register_rate_limited_per_ip`
- `test_register_returns_no_client_secret`

**Authorization endpoint (8):**
- `test_authorize_validates_response_type`
- `test_authorize_validates_pkce_challenge_method`
- `test_authorize_validates_redirect_uri_matches_registered`
- `test_authorize_redirects_unknown_redirect_uri_to_error_page`
- `test_authorize_shows_login_when_no_session`
- `test_authorize_shows_consent_when_logged_in`
- `test_authorize_consent_allow_redirects_with_code`
- `test_authorize_consent_deny_redirects_with_error`

**Token endpoint — authorization_code (8):**
- `test_token_exchanges_code_for_tokens`
- `test_token_rejects_consumed_code`
- `test_token_rejects_expired_code`
- `test_token_rejects_mismatched_redirect_uri`
- `test_token_rejects_bad_pkce_verifier`
- `test_token_rejects_unknown_code`
- `test_token_response_includes_expected_fields`
- `test_token_access_token_works_on_api_endpoint`

**Token endpoint — refresh_token (5):**
- `test_refresh_issues_new_pair`
- `test_refresh_rotates_invalidates_old_pair`
- `test_refresh_extends_sliding_expiry`
- `test_refresh_rejects_revoked`
- `test_refresh_rejects_expired`

**Revocation (3):**
- `test_revoke_marks_pair_revoked`
- `test_revoke_returns_200_for_unknown_token`
- `test_revoke_revoked_token_no_longer_authenticates`

**End-to-end (3):**
- `test_full_flow_register_authorize_token_refresh_revoke` (single test exercising the whole flow)
- `test_oauth_token_can_GET_playlists`
- `test_oauth_token_cannot_POST_with_read_only_scope`

## 14. File Layout

| File | Change |
|---|---|
| `backend/db.py` | 3 new tables + pre-registered clients seed in `init_db()` |
| `backend/oauth.py` | NEW — all OAuth endpoints + helpers + lookup, mounted as a FastAPI router |
| `backend/main.py` | Mount the OAuth router; extend `get_api_authed` to recognize OAuth access tokens |
| `backend/templates/` | NEW directory — `oauth_login.html`, `oauth_consent.html`, `oauth_error.html` (Jinja2) |
| `backend/tests/test_oauth.py` | NEW — ~40 tests |
| `frontend/oauth-consent.css` | NEW — brand-matching styles for consent pages |

## 15. Failure Modes

| Failure | Behavior |
|---|---|
| Unknown `client_id` at `/authorize` | Render error page (no redirect — `redirect_uri` untrusted) |
| `redirect_uri` doesn't match registered | Render error page |
| Invalid `response_type` / `code_challenge_method` | Redirect to known `redirect_uri` with `error=invalid_request` |
| Authorization code already consumed | 400 `invalid_grant`; existing tokens for this user-client pair are NOT auto-revoked (limitation; future work) |
| PKCE verifier mismatch | 400 `invalid_grant` |
| Expired authorization code (10 min) | 400 `invalid_grant` |
| Refresh token reuse after rotation | The old refresh token is `revoked_at IS NOT NULL` → 400 `invalid_grant`. The NEW token still works. Acceptable for v1. |
| User has multiple admin orgs | Each org needs a separate consent grant. No "switch org" in consent flow. |
| Pending-auth in-memory dict lost on restart | Customer clicks Connect again. 10-min TTL anyway. |
| Session cookie expired during consent | Re-render login. Lose only the consent step. |
| Token DB row removed (e.g., manual SQL) | Subsequent auth → 401, customer reconnects. |

## 16. Migration / Rollout

- 3 `CREATE TABLE IF NOT EXISTS` — idempotent.
- Pre-registered clients INSERT uses `ON CONFLICT (client_id) DO NOTHING` — safe to re-run.
- No existing data needs migration.
- Existing API key + session bearer auth continues to work unchanged. OAuth is additive.
- `frontend/oauth-consent.css` requires a frontend container rebuild (it's a new static asset).
- `backend/templates/*.html` requires a backend container rebuild (Jinja2 template loader looks them up at startup).

## 17. Security Notes

- All tokens hashed at rest (PBKDF2 same as `api_keys` and `users.password_hash`).
- PKCE S256-only — `plain` rejected to prevent downgrade.
- Authorization codes single-use via `consumed_at`. Replay window: 10 min max.
- Refresh tokens rotate per OAuth 2.1.
- Redirect URI exact-match only — no globs, no wildcards.
- `client_secret` not issued (public clients only).
- Rate limit on `/oauth/register` to prevent enumeration / DoS.
- `/oauth/login` reuses Phase 2.5c lockout logic.
- Tokens transit over HTTPS only (enforced by Cloudflare + nginx).
- CSRF on consent form: `request_id` is essentially a CSRF token (random + scoped to user session at issuance time).

## 18. Future Phases (queued)

- **Phase 2.5i-2 — MCP server.** SSE endpoint, MCP protocol handshake, ~26 tool wrappers. Built on top of the OAuth tokens this phase issues.
- **Phase 2.5i-3 — Admin "MCP Connections" tab.** List `oauth_tokens` rows for the org, show client name + scope + last used, per-token revoke.
- Token introspection endpoint (RFC 7662) — once MCP servers external to api.khanshoof.com exist.
- All-tokens-revoked-on-refresh-reuse defense (refresh-token replay detection).
- Per-tool consent screens (richer scope selection).
- OAuth 2.0 server-to-server client credentials grant (no user involved).
- "Switch org" inside the consent flow for users who admin multiple orgs.
