# Phase 2.5j — Social Auth (Google + Apple) Design

**Status:** Approved 2026-05-22 (brainstorm)
**Branch:** `feature/social-auth`
**Independent of:** PR #12 (OAuth provider) and PR #13 (MCP server) — branched from main, no dependency on those.

---

## 1. Goal

Let users sign up or sign in to Khanshoof with **Sign in with Google** or **Sign in with Apple** alongside the existing email + password flow. No provider replaces the password path; users with existing password accounts can add a social identity, and new users can pick whichever method they prefer at signup time.

## 2. Constraints (decided in brainstorm)

| # | Decision | Rationale |
|---|---|---|
| Q1 | Auto-link by email match — silent | Google + Apple ID tokens are high-assurance; industry standard |
| Q2 | Post-bounce "business name" form for new social signups | Tenant name is load-bearing identity; one extra click is worth it |
| Q3 | `auth_identities` table keyed on `(provider, subject_id)`, separate from `users` | Provider email can drift; subject_id is the stable identifier |
| Schema | `users.password_hash` becomes NULLABLE | Social-only users have no password (can add one later via existing change-password flow) |
| Providers in scope | Google + Apple only | Microsoft / GitHub / phone deferred to follow-up |
| Auth on existing | Existing email/password flow unchanged | Backwards compatible |

## 3. Architecture

```
auth modal (sign in / signup)
  ┌───────────────────────────────┐
  │ [Sign in with Google]         │
  │ [Sign in with Apple]          │
  │ ─── or ───                    │
  │ email + password form         │
  └─────────┬─────────────────────┘
            │
            │ 1. click → GET /auth/{provider}/start
            ▼
  302 to provider with OAuth params
            │
            │ 2. user authenticates at provider
            ▼
  provider redirects back to /auth/{provider}/callback
  ┌───────────────────────────────┐
  │ backend/social_auth.py        │
  │  - verify state cookie        │
  │  - exchange code for ID token │
  │  - verify ID token JWT        │
  │  - extract sub + email + name │
  └─────────┬─────────────────────┘
            │
            ▼ finalize logic
        ┌───┴─────────────────────────────┐
        ▼                                 ▼
  identity exists                  identity does not exist
  (auth_identities row found       (no auth_identities row)
   for this subject_id)
        │                                 │
        ▼                                 ▼
  log in, update            ┌──────────────┴──────────┐
  last_used_at,             ▼                         ▼
  redirect to              user with matching         no user with
  return_to                username (email) exists    this email
                            │                         │
                            ▼                         ▼
                      auto-link:                stash signed token
                      INSERT auth_identities    render "business name?"
                      log in, redirect          form → POST complete-signup
                                                creates org + user + identity
```

## 4. Database changes

### 4.1 `users.password_hash` → NULLABLE

```sql
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
```

Run inside `init_db()` (idempotent — Postgres `ALTER COLUMN ... DROP NOT NULL` is a no-op when already nullable).

### 4.2 New `auth_identities` table

```sql
CREATE TABLE IF NOT EXISTS auth_identities (
  id            SERIAL PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider      TEXT NOT NULL CHECK (provider IN ('google', 'apple')),
  subject_id    TEXT NOT NULL,
  email_at_link TEXT NOT NULL,
  name_at_link  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ,
  UNIQUE (provider, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_auth_identities_user
  ON auth_identities (user_id);
```

Notes:
- `UNIQUE (provider, subject_id)` is the load-bearing constraint — one slot per Google/Apple account.
- `email_at_link` / `name_at_link` are **snapshots at link time**, not authoritative — the provider's email can drift but we link by `subject_id`.
- Cascade delete from `users` is intentional — deleting a user removes all their social identities.

## 5. Endpoints

Eight new endpoints — four per provider, all on the existing FastAPI app under `/auth/{provider}/...`.

### 5.1 `GET /auth/{provider}/start?intent=signup|signin&return_to=<path>`

Builds the provider's OAuth URL and 302s the browser. Sets a short-lived HTTP-only cookie `auth_csrf` containing an HMAC-signed state value for CSRF protection.

```python
state = jwt_sign({
    "provider": provider,
    "intent": intent,
    "return_to": return_to,
    "nonce": secrets.token_urlsafe(16),
    "exp": now() + 300,  # 5 min
}, SECRET_KEY)

# Set as cookie
response.set_cookie("auth_csrf", state,
    httponly=True, samesite="lax", secure=True, max_age=300)

# Google URL:
google_url = (
    "https://accounts.google.com/o/oauth2/v2/auth?"
    f"client_id={GOOGLE_CLIENT_ID}&"
    f"redirect_uri={GOOGLE_REDIRECT_URI}&"
    "response_type=code&"
    "scope=openid%20email%20profile&"
    f"state={state}&"
    f"nonce={nonce_from_state}"
)

# Apple URL: same shape but `response_mode=form_post`
# (Apple POSTs the callback, not GETs)
```

`intent` is informational — it determines the post-callback UX message ("Welcome!" vs "Welcome back!") but doesn't change the actual flow logic. `return_to` is the path to redirect to after completion (defaults to `/`).

### 5.2 `GET /auth/google/callback?code=...&state=...`

```python
# 1. Validate state vs cookie
cookie_state = request.cookies.get("auth_csrf")
if not cookie_state or cookie_state != state:
    raise http_error(400, "invalid_state", "Invalid CSRF state")
state_payload = jwt_verify(state, SECRET_KEY)  # raises on expired/tampered

# 2. Exchange code for tokens
r = httpx.post("https://oauth2.googleapis.com/token", data={
    "code": code,
    "client_id": GOOGLE_CLIENT_ID,
    "client_secret": GOOGLE_CLIENT_SECRET,
    "redirect_uri": GOOGLE_REDIRECT_URI,
    "grant_type": "authorization_code",
}, timeout=10.0)
id_token = r.json()["id_token"]

# 3. Verify ID token against Google's JWKS (cached 24h)
payload = verify_google_id_token(id_token, GOOGLE_CLIENT_ID)
# payload has: sub, email, email_verified, name, etc.

# 4. Hand to finalize logic
return _finalize_social_signin(
    provider="google",
    subject_id=payload["sub"],
    email=payload["email"].lower(),
    name=payload.get("name"),
    return_to=state_payload["return_to"],
)
```

### 5.3 `POST /auth/apple/callback`

Apple POSTs `application/x-www-form-urlencoded` to the callback (not GETs).

```python
form = await request.form()
code = form["code"]
state = form["state"]
id_token = form["id_token"]
user_json = form.get("user")  # JSON string, FIRST sign-in ONLY

# state cookie check (same as Google)
# verify id_token against Apple's JWKS at https://appleid.apple.com/auth/keys
payload = verify_apple_id_token(id_token, APPLE_CLIENT_ID)

# Apple first-sign-in name capture
name = None
if user_json:
    user_data = json.loads(user_json)
    n = user_data.get("name", {})
    if n.get("firstName") or n.get("lastName"):
        name = f"{n.get('firstName','')} {n.get('lastName','')}".strip()

return _finalize_social_signin(
    provider="apple",
    subject_id=payload["sub"],
    email=payload["email"].lower(),  # Apple still sends email every time
    name=name,                       # may be None on subsequent sign-ins
    return_to=...,
)
```

**Apple client_secret** is a JWT we sign ourselves. Helper:

```python
def _apple_client_secret_jwt() -> str:
    """Sign a fresh client_secret JWT for Apple's token endpoint.
    Cached 25min, regenerated before expiry."""
    now = int(time.time())
    return jwt.encode({
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 1800,    # 30 min, well under Apple's 6 month max
        "aud": "https://appleid.apple.com",
        "sub": APPLE_CLIENT_ID,
    }, _load_apple_private_key(), algorithm="ES256",
       headers={"kid": APPLE_KEY_ID})
```

### 5.4 `_finalize_social_signin()` (internal, shared)

```python
def _finalize_social_signin(provider, subject_id, email, name, return_to):
    # 1. Existing identity?
    identity = query_one(
        "SELECT * FROM auth_identities "
        "WHERE provider = ? AND subject_id = ?",
        (provider, subject_id),
    )
    if identity:
        execute(
            "UPDATE auth_identities SET last_used_at = now() WHERE id = ?",
            (identity["id"],),
        )
        token = _issue_session(identity["user_id"])
        audit(action="auth.social.signin",
              actor={"id": identity["user_id"]},
              details={"provider": provider})
        return RedirectResponse(
            f"{APP_URL}/auth-bounce?token={token}&return_to={return_to}",
        )

    # 2. Existing user by email (auto-link)
    user = query_one("SELECT * FROM users WHERE username = ?", (email,))
    if user:
        execute(
            "INSERT INTO auth_identities "
            "(user_id, provider, subject_id, email_at_link, name_at_link) "
            "VALUES (?, ?, ?, ?, ?)",
            (user["id"], provider, subject_id, email, name),
        )
        token = _issue_session(user["id"])
        audit(action="auth.social.linked",
              actor={"id": user["id"]},
              details={"provider": provider})
        return RedirectResponse(
            f"{APP_URL}/auth-bounce?token={token}&return_to={return_to}",
        )

    # 3. New user — stash and prompt for business_name
    stash = jwt_sign({
        "provider": provider,
        "subject_id": subject_id,
        "email": email,
        "name": name,
        "exp": now() + 600,  # 10 min
    }, SECRET_KEY)
    return RedirectResponse(
        f"{APP_URL}/auth-bounce?complete_signup={stash}&suggested_name={name or ''}",
    )
```

The `/auth-bounce` path is a small SPA route in `frontend/app.js` that picks up either:
- `?token=...` → stores the session cookie, navigates to `return_to`
- `?complete_signup=...&suggested_name=...` → renders the business-name form, pre-filled from `suggested_name`, then POSTs to `/auth/{provider}/complete-signup`

### 5.5 `POST /auth/{provider}/complete-signup`

```python
class CompleteSocialSignup(BaseModel):
    stash_token: str
    business_name: str = Field(..., min_length=1, max_length=200)

@app.post("/auth/{provider}/complete-signup")
def complete_social_signup(provider: str, payload: CompleteSocialSignup):
    stash = jwt_verify(payload.stash_token, SECRET_KEY)
    if stash["provider"] != provider:
        raise http_error(400, "stash_provider_mismatch")

    # Belt + suspenders: re-check user doesn't exist
    if query_one("SELECT id FROM users WHERE username = ?", (stash["email"],)):
        raise http_error(409, "email_taken",
                         "An account with this email already exists")
    if query_one(
        "SELECT id FROM auth_identities "
        "WHERE provider = ? AND subject_id = ?",
        (provider, stash["subject_id"]),
    ):
        raise http_error(409, "identity_taken",
                         "This identity is already linked to a different account")

    # Create org + user + identity in a transaction
    with transaction():
        org_id = execute_returning(
            "INSERT INTO organizations (name, slug, plan, screen_limit, "
            "subscription_status, locale, created_at) "
            "VALUES (?, ?, 'starter', 5, 'trialing', 'en', now()) RETURNING id",
            (payload.business_name, slug(payload.business_name)),
        )
        user_id = execute_returning(
            "INSERT INTO users (organization_id, username, password_hash, "
            "is_admin, role, created_at) "
            "VALUES (?, ?, NULL, 1, 'admin', now()) RETURNING id",
            (org_id, stash["email"]),
        )
        execute(
            "INSERT INTO auth_identities "
            "(user_id, provider, subject_id, email_at_link, name_at_link) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, provider, stash["subject_id"], stash["email"],
             stash.get("name")),
        )

    token = _issue_session(user_id)
    audit(action="auth.social.signup",
          actor={"id": user_id},
          details={"provider": provider, "business_name": payload.business_name})
    return {"token": token, "user": ..., "organization": ...}  # same shape as /auth/signup/complete
```

## 6. ID token verification

### 6.1 Google

Google publishes their JWKS at `https://www.googleapis.com/oauth2/v3/certs`. Cache 24h.

```python
def verify_google_id_token(id_token: str, audience: str) -> dict:
    jwks = _cached_jwks("https://www.googleapis.com/oauth2/v3/certs", ttl=86400)
    header = jwt.get_unverified_header(id_token)
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = jwt.PyJWK(key).key
    payload = jwt.decode(
        id_token, public_key,
        algorithms=["RS256"],
        audience=audience,
        issuer=["accounts.google.com", "https://accounts.google.com"],
    )
    if not payload.get("email_verified"):
        raise http_error(400, "email_not_verified",
                         "Google has not verified this email")
    return payload
```

### 6.2 Apple

Apple's JWKS at `https://appleid.apple.com/auth/keys`. Same shape, ES256 instead of RS256.

```python
def verify_apple_id_token(id_token: str, audience: str) -> dict:
    jwks = _cached_jwks("https://appleid.apple.com/auth/keys", ttl=86400)
    header = jwt.get_unverified_header(id_token)
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = jwt.PyJWK(key).key
    payload = jwt.decode(
        id_token, public_key,
        algorithms=["ES256"],
        audience=audience,
        issuer="https://appleid.apple.com",
    )
    # Apple always sets email_verified=true (they own the verification)
    return payload
```

### 6.3 JWKS cache

Single in-process dict keyed on URL, value is `{keys, fetched_at}`. On each verify call, if `fetched_at + ttl < now()`, refetch. Lock-free — at-worst N parallel fetches on cold cache. Acceptable.

## 7. Environment variables

| Var | Description | Required |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 web-app client ID | If Google is enabled |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | If Google is enabled |
| `GOOGLE_REDIRECT_URI` | Defaults to `${API_BASE_URL}/auth/google/callback` | No |
| `APPLE_CLIENT_ID` | Services ID (e.g. `com.khanshoof.signage`) | If Apple is enabled |
| `APPLE_TEAM_ID` | 10-char Apple Team ID | If Apple is enabled |
| `APPLE_KEY_ID` | 10-char Key ID of the .p8 private key | If Apple is enabled |
| `APPLE_PRIVATE_KEY_PATH` | Filesystem path to the .p8 file | If Apple is enabled |
| `APPLE_REDIRECT_URI` | Defaults to `${API_BASE_URL}/auth/apple/callback` | No |

If a provider's required vars are missing at startup, that provider's routes are still mounted but `/auth/{provider}/start` returns 503 with `{"error": "provider_not_configured"}`. Google and Apple can be enabled independently.

## 8. Frontend changes

### 8.1 Auth modal

In `frontend/app.js`'s auth-modal section (~line 1300):
- Add a `social-auth-buttons` div above the existing email field on both the sign-in and signup tabs.
- Render two buttons using official provider styles:
  - Google: use the official "Google Identity Services" widget OR a hand-styled button with the Google G logo (license-compliant). Recommended: hand-styled — fewer third-party scripts, simpler bilingual handling.
  - Apple: hand-styled with Apple's branded button per their HIG. Text is always "Sign in with Apple" — Apple's HIG mandates the brand name stays as "Apple" in any language.
- Click handler: `window.location = '/auth/google/start?intent=' + intent + '&return_to=' + encodeURIComponent(currentPath)`.
- Add a `— or —` divider between the social buttons and the email field.

### 8.2 `/auth-bounce` route

New SPA-internal route handler (no separate page). On load:
- If `?token=<jwt>` in URL: store session cookie, hide auth modal, navigate to `return_to`. Same effect as a successful `/auth/login`.
- If `?complete_signup=<stash>&suggested_name=<name>` in URL: render the "What's your business name?" view inside the auth modal. Single text field pre-filled with `suggested_name`, "Continue" button POSTs to `/auth/{provider}/complete-signup`. On success, store cookie + navigate home.
- If `?error=<code>&error_description=<msg>` in URL (e.g. user cancelled at provider): show the error inline on the auth modal and remain on the sign-in screen.

### 8.3 i18n strings

Append to `frontend/i18n/en.json` and `frontend/i18n/ar.json`:

```json
{
  "auth.social.google": "Sign in with Google",
  "auth.social.apple": "Sign in with Apple",
  "auth.social.divider": "or",
  "auth.social.complete_signup.title": "Welcome to Khanshoof!",
  "auth.social.complete_signup.business_name": "Business name",
  "auth.social.complete_signup.continue": "Continue",
  "auth.social.error.provider_unavailable": "{provider} sign-in is temporarily unavailable. Try email + password.",
  "auth.social.error.email_taken": "An account with this email already exists. Sign in with your password to link.",
  "auth.social.error.identity_taken": "This {provider} account is already linked to another Khanshoof account."
}
```

Arabic translations: "تسجيل الدخول باستخدام جوجل" for Google. For Apple, per Apple's HIG, the brand name must stay as "Apple" — Arabic label is "تسجيل الدخول باستخدام Apple".

## 9. Testing

Test file: `backend/tests/test_social_auth.py`. ~20 tests.

### 9.1 Mocks

- `httpx.post("https://oauth2.googleapis.com/token")` and `httpx.post("https://appleid.apple.com/auth/token")` → mocked with `respx`.
- JWKS endpoints → mocked, returning a fixture JWKS with a known test key.
- ID tokens are signed by the test key in test fixtures (so `jwt.decode` actually verifies against our mocked JWKS).

### 9.2 Test surface

**State + CSRF (3 tests)**
- `/auth/google/start` sets `auth_csrf` cookie + 302s to Google with `state` matching cookie
- Callback with missing cookie → 400 `invalid_state`
- Callback with mismatched state → 400 `invalid_state`

**ID token verification (4 tests)**
- Valid Google ID token → payload extracted correctly
- Expired ID token → 400
- Wrong audience → 400
- Tampered signature → 400

**Finalize flow — Google (4 tests)**
- Existing identity → log in, redirect with token, `last_used_at` updated
- No identity but email matches user → auto-link, log in, redirect with token, audit entry written
- No identity, no user → stash token issued, redirect to `/auth-bounce?complete_signup=...`
- Complete-signup happy path: org + user + identity created, session issued

**Apple-specific (3 tests)**
- First sign-in with `user` field → `name_at_link` captured
- Subsequent sign-in without `user` field → still works, name unchanged
- Apple POST form-encoded callback (not GET) → handled correctly

**Edge cases (4 tests)**
- Provider not configured (no env vars) → `/auth/{provider}/start` returns 503
- Complete-signup with stale stash token (>10min) → 400
- Complete-signup with stash mismatch (Google stash POSTed to apple endpoint) → 400
- Complete-signup race: another user signs up with the same email between callback and complete-signup → 409 `email_taken`

**Integration with existing flows (2 tests)**
- Existing password user can still log in via `/auth/login` after adding a Google identity
- Audit log records `auth.social.signin`, `auth.social.linked`, `auth.social.signup` distinctly

## 10. File layout

| File | Change | Lines |
|---|---|---|
| `backend/social_auth.py` | NEW — module with all 8 endpoints + helpers | ~600 |
| `backend/main.py` | `from social_auth import attach_social_auth; attach_social_auth(app)` | +2 |
| `backend/db.py` | `ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL` + `auth_identities` table + indexes | +35 |
| `backend/tests/test_social_auth.py` | NEW — ~20 tests + JWKS fixtures | ~700 |
| `backend/requirements.txt` | Add `PyJWT[crypto]>=2.8` (for ES256 + JWKS), `respx` (test-only) | +2 |
| `frontend/app.js` | Auth modal buttons + /auth-bounce route + complete-signup view | +200 |
| `frontend/i18n/en.json`, `ar.json` | ~10 new strings each | +30 |

Total new code: ~1,500 lines, of which ~700 are tests.

## 11. Failure modes

| Mode | Behavior |
|---|---|
| Provider down (token exchange fails) | 503 to user, `error=provider_unavailable` shown inline |
| JWKS cache miss + endpoint timeout | Same as above — 503 |
| Apple .p8 key file missing | Startup logs warning; `/auth/apple/start` returns 503 `provider_not_configured` |
| User cancels at provider | Provider returns to callback with `?error=access_denied`; show on auth modal |
| Email at provider differs from user's username after link | Link still works (subject_id is canonical); display in settings uses `users.username` (the original email) |
| Stash token expired (>10min between callback and complete-signup) | 400 with `code: "stash_expired"` — user is sent back to social auth start |
| Two concurrent sign-ups for the same Google account (race) | UNIQUE constraint on `(provider, subject_id)` rejects the second; second user sees 409 |

## 12. Security notes

- **State CSRF**: HMAC-signed state in cookie + URL, double-submit verified.
- **ID token verification**: against provider JWKS, audience check, issuer check, signature check, expiry check. No shortcut "trust the email field".
- **Apple client_secret JWT**: cached 25min, regenerated before 30min expiry, signed locally with .p8 (never sent over network).
- **Stash token**: short-lived (10min), signed by SECRET_KEY, single-use enforced by re-checking user/identity existence at complete-signup time.
- **Email matching for auto-link**: only triggers when `email_verified` is true on the provider's side. Google + Apple both verify emails before issuing tokens.
- **No password downgrade attack**: setting `password_hash` to NULL does NOT make existing password users vulnerable — they still have their hash, and login still requires it.
- **Session token issued by `_issue_session`**: same flow as `/auth/login`, so all downstream auth (sessions table, audit, lockout-on-password-failure) works identically.

## 13. v1 deferrals (documented in PR)

- **Microsoft / GitHub / GitHub Enterprise / phone-number auth** — only Google + Apple in v1.
- **Settings page to unlink identities** — UI lands in a follow-up. Users can revoke at the provider's own dashboard in the meantime (Google: myaccount.google.com → Security; Apple: appleid.apple.com → Sign in with Apple).
- **Multi-org users** — one Google identity → one Khanshoof user → one organization. Multi-tenancy of a single identity is out of scope.
- **Provider account display name change** — if the user changes their Google name, we don't pull updates; `name_at_link` is frozen at link time.
- **Email change at provider** — link still works (subject_id is canonical), but `users.username` stays as the original email. Settings UI for changing the canonical email is out of scope.

## 14. Migration / rollout

- No data migration: `auth_identities` is empty on day one. Existing password users are unaffected.
- The `ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL` runs idempotently on next `init_db()` call.
- `requirements.txt` change picks up `PyJWT[crypto]` on next backend rebuild.
- Buttons appear in the auth modal as soon as the frontend container is rebuilt — but clicking returns 503 until the relevant env vars are set.
- Operator-facing checklist (lives in the PR description, not in code):
  1. Register Google OAuth client in GCP Console
  2. Register Apple Services ID + private key in Apple Developer portal
  3. Set the 7 env vars in `.env`
  4. Restart backend
  5. Smoke test both flows end-to-end before announcing

## 15. Verification before merge

- [ ] ~20 unit/integration tests in `test_social_auth.py` passing (mocked providers, no real network)
- [ ] Existing test suite still green (no regression in `/auth/signup/*` or `/auth/login`)
- [ ] Manual smoke (operator): Sign in with Google on a fresh org → business name form → org created → can pair a screen
- [ ] Manual smoke (operator): Sign in with Apple on a fresh org → same
- [ ] Manual smoke: existing password user clicks "Sign in with Google" with same email → silently linked → can still log in with password too
- [ ] AR locale: buttons + business name form render with correct RTL (Arabic label for Google; brand name "Apple" stays in English per HIG)
