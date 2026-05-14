# Phase 2.5c — Security Hardening — Design

**Date:** 2026-05-09
**Branch:** `feature/security-hardening` (fresh, branched from `main` at `0579eaf`)
**Predecessor:** Phase 2.5b (Arabic/RTL hardening) shipped via PR #4.

---

## 1. Goal

Three independent, low-risk hardenings to admin authentication, shipped as one PR:

1. **Account lockout** — per-username failed-attempt tracking that survives across IPs.
2. **Audit log** — DB table + helper, called from auth and admin-only mutation endpoints, with a paginated Settings UI.
3. **Password policy + HIBP** — stronger rules and a k-anonymity HIBP breach check, applied only when a new password is set.

## 2. Non-Goals (deferred)

- TOTP / 2FA for ongoing logins
- Active-session list with per-session revoke UI
- Forced password rotation for existing users
- Audit log of read-only views or routine playlist/media CRUD
- Configurable lockout / policy thresholds (kept as module constants)

## 3. Existing Security Baseline (already in main, do not regress)

- PBKDF2-SHA256 password hashing, 120 000 iterations, per-user salt
- DB-backed bearer-token sessions with `last_used` and idle cleanup
- Role gates: `admin` / `editor` / `viewer` via `require_roles()`
- `slowapi` per-IP login rate limit `10 / 5 min` (kept; defense-in-depth)
- Existing password policy: ≥8 chars, ≥1 letter, ≥1 number (replaced)
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- Signup OTP with attempts counter
- HMAC + query-secret on billing webhooks
- `must_change_password` flag for admin-created users
- `/docs` disabled in prod

---

## 4. Component A — Account Lockout

### 4.1 Schema

New table:

```sql
CREATE TABLE IF NOT EXISTS login_attempts (
  id            SERIAL PRIMARY KEY,
  username      TEXT NOT NULL,
  attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  success       BOOLEAN NOT NULL,
  ip            TEXT
);
CREATE INDEX idx_login_attempts_username_ts
  ON login_attempts (username, attempted_at DESC);
```

Both successful and failed attempts are logged. Successes are needed to "anchor" the lockout window so a returning legitimate user is not locked out by stale failure noise. They also feed audit-log signal.

### 4.2 Constants

Module-level in `backend/main.py`:

```python
LOGIN_LOCKOUT_WINDOW_SECONDS = 900   # 15 minutes
LOGIN_LOCKOUT_THRESHOLD       = 5
LOGIN_ATTEMPTS_RETENTION_DAYS = 30
```

Not env-configurable. YAGNI.

### 4.3 Logic

In `/auth/login`, before `verify_password`:

```
last_success_ts = SELECT MAX(attempted_at) FROM login_attempts
                  WHERE username = ? AND success = true

failure_count = SELECT COUNT(*) FROM login_attempts
                WHERE username = ?
                  AND success = false
                  AND attempted_at > now() - interval 'WINDOW'
                  AND attempted_at > COALESCE(last_success_ts, epoch)

if failure_count >= THRESHOLD:
    oldest_failure_ts = SELECT MIN(attempted_at) ... (same WHERE)
    retry_after = max(0, oldest_failure_ts + WINDOW - now())
    write audit row: action='auth.login.failure', details={reason: 'account_locked'}
    raise 429 { error: 'account_locked',
                 message_key: 'auth.account_locked',
                 retry_after_seconds: int(retry_after) }

# else: proceed to verify_password
ok = verify_password(payload.password, user.password_hash) if user else False
write login_attempts row (username, success=ok, ip)
if not ok:
    write audit row: action='auth.login.failure', details={reason: 'invalid_credentials'}
    raise 401 invalid_credentials
# success path:
write audit row: action='auth.login.success'
# (existing session creation continues unchanged)
```

After success, log a row with `success=true` so subsequent failures are measured against the new anchor. The per-IP `slowapi` 10/5min limit remains in place.

### 4.4 Username enumeration safety

Lockout applies whether or not the username exists. Bots typing `aaaaaaaa@bot.com` will accumulate rows; the periodic cleanup purges anything older than 30 days. The 429 response is identical regardless of account existence — no info leak.

### 4.5 Cleanup

Inside the existing `cleanup_sessions()` call site (which runs at login and elsewhere on the auth path), add:

```python
execute("DELETE FROM login_attempts WHERE attempted_at < now() - interval '30 days'")
```

### 4.6 Frontend

In the login form's error handler:
- On 429 with `error: 'account_locked'`, format `Khan.t('auth.account_locked', '...').replace('{minutes}', ceil(retry_after_seconds / 60))`
- Disable the submit button for `retry_after_seconds`, show countdown, re-enable when zero
- New i18n key `auth.account_locked` in both EN and AR

### 4.7 Tests (backend)

Added to `backend/tests/test_security.py`:

| Test | Behaviour |
|---|---|
| `test_lockout_after_threshold_failures` | 5 fails in window → 6th returns 429 |
| `test_lockout_window_self_heals` | 5 fails + sleep past window (mocked clock) → 6th allowed |
| `test_lockout_resets_on_success` | 4 fails + 1 success + 4 fails → still allowed (counter reset) |
| `test_lockout_response_has_retry_after` | 429 body has `retry_after_seconds` int |
| `test_lockout_does_not_leak_user_existence` | Lockout response identical for known + unknown username |
| `test_login_attempts_cleanup_purges_old_rows` | Cleanup deletes rows >30 days old |

---

## 5. Component B — Audit Log

### 5.1 Schema

```sql
CREATE TABLE IF NOT EXISTS audit_log (
  id              SERIAL PRIMARY KEY,
  organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
  actor_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
  actor_username  TEXT,
  action          TEXT NOT NULL,
  target_type     TEXT,
  target_id       TEXT,
  ip              TEXT,
  user_agent      TEXT,
  details         JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_log_org_ts ON audit_log (organization_id, created_at DESC);
CREATE INDEX idx_audit_log_actor  ON audit_log (actor_user_id, created_at DESC);
CREATE INDEX idx_audit_log_action ON audit_log (action, created_at DESC);
```

`actor_username` is a snapshot so the row remains readable after a user is deleted (`actor_user_id` becomes NULL via `ON DELETE SET NULL`).

### 5.2 Helper

```python
def audit(
    request: Request | None,
    *,
    action: str,
    actor: dict | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    details: dict | None = None,
    organization_id: int | None = None,
) -> None:
    """Best-effort. Never raises; logs warning on DB failure."""
    try:
        ip = _client_ip(request) if request else None
        ua = (request.headers.get("user-agent") if request else None) or None
        org_id = organization_id or (actor.get("organization_id") if actor else None)
        execute(
            """
            INSERT INTO audit_log (
              organization_id, actor_user_id, actor_username,
              action, target_type, target_id, ip, user_agent, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (org_id,
             actor.get("id") if actor else None,
             actor.get("username") if actor else None,
             action,
             target_type,
             str(target_id) if target_id is not None else None,
             ip, ua,
             json.dumps(details) if details is not None else None,
             utc_now_iso()),
        )
    except Exception as exc:
        logger.warning("audit_failed action=%s err=%s", action, exc)
```

A busted audit table must never break a login or a CRUD endpoint.

### 5.3 Action vocabulary (frozen)

```python
AUDIT_ACTIONS = (
    "auth.login.success",
    "auth.login.failure",       # actor=NULL, details.reason in {invalid_credentials, account_locked}
    "auth.logout",
    "auth.password_change",
    "user.create",              # details: {username, role}
    "user.update",              # details: {before, after}  (role/password_set only; never the hash)
    "user.delete",              # details: {username}
    "screen.pair",              # details: {screen_name, site_id}
    "screen.unpair",            # details: {screen_name}
    "wall.create",              # details: {name, mode, rows, cols}
    "wall.delete",              # details: {name}
    "billing.plan_change",      # details: {from_plan, to_plan}
)
```

`user.update` diffs include only role and the boolean fact that a password was set — never the hash, never the plaintext.

### 5.4 API endpoint

```
GET /audit-log
  Query: limit (default 50, max 200), offset, action, actor_id, since, until
  Auth:  require_roles("admin")
  Scope: organization_id = current admin's org
  Returns: { items: [...], total: int, limit, offset }
```

Each item:
```json
{
  "id": 123,
  "created_at": "2026-05-09T17:21:00Z",
  "actor": { "id": 4, "username": "amal" } | null,
  "action": "user.create",
  "target": { "type": "user", "id": "8" } | null,
  "ip": "1.2.3.4",
  "user_agent": "Mozilla/5.0 ...",
  "details": { "username": "new@x", "role": "editor" }
}
```

### 5.5 Frontend (Settings tab)

- New tab "Audit log" between existing Settings tabs
- Table columns: When · Who · Action · Target · IP · Details (expand row for JSON)
- Filters: action dropdown (12 options + "all"), date-range inputs, actor dropdown (current org users + "all")
- Pagination: 50/page, "Older →" / "← Newer" navigation
- Auto-refresh disabled (read-only audit; no polling)
- All strings i18n; timestamps localized via `Khan.formatDate`
- Permission-gated: tab hidden for editor/viewer

### 5.6 Tests (backend)

| Test | Behaviour |
|---|---|
| `test_audit_helper_writes_full_row` | All fields populated correctly given a typical call |
| `test_audit_helper_swallows_db_error` | Mock `execute` to raise → endpoint still returns 2xx; warning logged |
| `test_audit_log_endpoint_requires_admin` | 403 for editor and viewer |
| `test_audit_log_endpoint_org_scoped` | Admin from org A cannot see org B rows |
| `test_audit_log_endpoint_filters` | `action=`, `actor_id=`, `since=`, `until=` each narrow results |
| `test_audit_log_pagination` | `limit` and `offset` work; `total` correct |
| One integration test per action: login success/failure, logout, password change, user create/update/delete, screen pair/unpair, wall create/delete, billing plan change → row exists in `audit_log` after the call |

### 5.7 Retention

No automatic purge. If the table grows unmanageable, a future phase can add it.

---

## 6. Component C — Password Policy + HIBP

### 6.1 New `validate_password()`

```python
PASSWORD_MIN_LENGTH = 12

def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise http_error(400, "password_too_short",
                         f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if not re.search(r"[a-z]", password):
        raise http_error(400, "password_no_lowercase",
                         "Password must include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        raise http_error(400, "password_no_uppercase",
                         "Password must include an uppercase letter")
    if not re.search(r"\d", password):
        raise http_error(400, "password_no_number",
                         "Password must include a number")
    if check_hibp_breach(password):
        raise http_error(400, "password_breached",
                         "This password has appeared in known data breaches. Choose a different one.")
```

### 6.2 HIBP module — new file `backend/hibp.py`

```python
import hashlib, logging
import requests

log = logging.getLogger(__name__)
HIBP_URL = "https://api.pwnedpasswords.com/range/{prefix}"
HIBP_TIMEOUT_SECONDS = 2.0

def check_hibp_breach(password: str) -> bool:
    """True iff password is in HIBP breach corpus. Fail-open (returns False on any error)."""
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        r = requests.get(
            HIBP_URL.format(prefix=prefix),
            headers={"Add-Padding": "true", "User-Agent": "khanshoof-signage"},
            timeout=HIBP_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
    except Exception as exc:
        log.warning("hibp_unreachable: %s", exc)
        return False  # fail-open
    for line in r.text.splitlines():
        hash_suffix, _, count = line.partition(":")
        if hash_suffix.strip() == suffix:
            return int(count or 0) > 0
    return False
```

K-anonymity: only the first 5 hex chars of the SHA-1 hash are sent (covering ~500 candidate hashes); HIBP returns the matching suffixes with counts; we check locally. The full password never leaves our backend.

### 6.3 Call sites (validate-on-set only)

- `POST /auth/signup/complete` (new user setting first password)
- `POST /auth/change-password`
- `POST /users` (admin creates user with password)
- `PUT /users/{id}` (admin sets new password for user)

Stored hashes are not re-validated. Existing users continue to authenticate with their current passwords; they hit the new policy only on next change.

### 6.4 Audit interaction

- HIBP unreachable → `log.warning` only; not an audit event (sysadmin signal, not security signal).
- Breach hit blocking a password → not audit-logged either; would leak temporal patterns of weak password attempts.
- Successful password change → `auth.password_change` audit row (already in §5.3).

### 6.5 Frontend

- Strength hint under password input fields:
  - EN: "Use at least 12 characters with upper case, lower case, and a number."
  - AR: equivalent MSA wording
- Server errors keyed via `message_key` so `Khan.t(key)` localizes them
- 5 new i18n keys per locale: `password_too_short`, `password_no_lowercase`, `password_no_uppercase`, `password_no_number`, `password_breached`
- Existing `password_no_letter` key is removed (replaced by lowercase + uppercase pair)

### 6.6 Tests

| Test | Behaviour |
|---|---|
| `test_password_rejected_too_short` | 11-char input → 400 `password_too_short` |
| `test_password_rejected_no_lowercase` | `ABCDEFGH1234` → 400 `password_no_lowercase` |
| `test_password_rejected_no_uppercase` | `abcdefgh1234` → 400 `password_no_uppercase` |
| `test_password_rejected_no_digit` | `Abcdefghijkl` → 400 `password_no_number` |
| `test_password_rejected_breached` | Mock HIBP to return matching suffix → 400 `password_breached` |
| `test_password_accepted_valid` | `Khanshoof2026Pass` → 200 |
| `test_hibp_module_returns_true_on_match` | Mock `requests.get` with canned text containing the password's suffix |
| `test_hibp_module_fail_open_on_network_error` | Mock `requests.get` to raise → returns False; warning logged |
| `test_hibp_module_sends_only_5_char_prefix` | Spy on `requests.get` URL; full hash and password absent from URL and headers |
| `test_existing_user_login_still_works` | Pre-migration hash that fails new policy still authenticates (no re-validation on login) |

---

## 7. File Layout

| File | Change |
|---|---|
| `backend/main.py` | Lockout logic in `/auth/login`; `validate_password` rewritten; `audit()` helper; `GET /audit-log`; audit calls inserted into ~12 endpoints |
| `backend/db.py` | Add `login_attempts` and `audit_log` table creation in bootstrap |
| `backend/hibp.py` | **New file.** k-anonymity HIBP check |
| `backend/tests/test_security.py` | Lockout + password policy tests |
| `backend/tests/test_audit_log.py` | **New file.** Audit helper + endpoint + per-action integration tests |
| `frontend/app.js` | Login error → 429 lockout countdown; new Settings "Audit log" tab; password strength hints |
| `frontend/index.html` | Audit-log tab markup |
| `frontend/styles.css` | Audit-log table layout |
| `frontend/i18n/en.json`, `frontend/i18n/ar.json` | New keys: `auth.account_locked`, `password_too_short` etc. (5 policy + 1 lockout = 6 new EN+AR keys); plus audit-log UI strings (action labels, column headers, filters, pagination — ~25 keys); minus removed `password_no_letter` |

## 8. Migration / Rollout

1. Schema additions are `CREATE TABLE IF NOT EXISTS` plus indices — safe in postgres, idempotent.
2. No backfill is required. Empty tables on day one.
3. Existing user passwords untouched. New policy applies on next password set.
4. Lockout activates immediately for everyone — including the deployer. Mitigation: developer keeps a known-good admin credential during deploy, or the `login_attempts` table can be emptied via psql if a cold-start lockout occurs.

## 9. Failure Modes

| Failure | Behaviour |
|---|---|
| HIBP API down | Fail-open; password set succeeds; sysadmin warning logged |
| `audit_log` write fails | Endpoint still succeeds; warning logged |
| `login_attempts` write fails after a successful auth | Login still succeeds; future window slightly off until next successful row writes |
| `login_attempts` SELECT fails before auth | Treat as `failure_count = 0` (fail-open), warning logged. Alternative — fail-closed — risks locking everyone out on a transient DB hiccup. |

## 10. Out of Scope (queued for later phases)

- TOTP / 2FA for ongoing logins
- "My active sessions" view with per-session revoke
- Audit-log retention policy + automatic purge
- Audit-log export (CSV)
- Forced password rotation for legacy users
- Configurable lockout / policy thresholds via env or admin UI
- IP allow-listing or geographic anomaly detection
- Email alerts on lockout

## 11. Next Initiative After This One

Per the user's stated sequence — Arabic [DONE], Security [this PR], **Payment gateway** — once 2.5c lands, the next phase is implementing the existing Niupay/KNET billing spec already in repo (commits `1f2ead1`, `318e970`).
