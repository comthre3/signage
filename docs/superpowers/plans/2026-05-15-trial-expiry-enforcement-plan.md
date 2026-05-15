# Phase 2.5f — Trial Expiry Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trial users can't keep using the product forever for free; lapsed paid customers must renew to keep editing content. Player playback for *existing* screens never goes dark.

**Architecture:** Pure `subscription_state(org)` helper returns a derived state. A FastAPI dependency `require_active_subscription` is added to every write endpoint and raises `HTTP 402` when the org is `trial_expired` or `lapsed`. The `GET /organization` response carries the derived state so the frontend can render a persistent top-of-app banner whose color and CTA vary by state. Player endpoints (`GET /screens/{token}/content` etc.) are explicitly NOT gated — playback always works.

**Tech Stack:** FastAPI (Pydantic + Depends) · psycopg · pytest · vanilla-JS frontend with `Khan.t()` i18n.

**Spec:** `docs/superpowers/specs/2026-05-15-trial-expiry-enforcement-design.md`
**Branch:** `feature/trial-expiry-enforcement` (already created from main `36c9e5a`)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(trial):` or `test(trial):`.
2. Backend container source is **baked into the image, not volume-mounted**. After changes to `backend/*.py`, rebuild:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Backend tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
   Without these env vars only ~110 of the suite pass. **Baseline on main is 224 passing.**
4. `db.py` uses `?` placeholders translated to `%s` for psycopg. Always use `?` in SQL.
5. Errors thrown by endpoints use `raise http_error(status, code, message)` (returns HTTPException with the standard detail shape). For 402 (subscription), we use `raise HTTPException(...)` directly because the response body includes extra `state` + `expires_at` fields beyond the standard `{code, message}`.
6. The frontend's `localizeError(detail)` helper auto-prefixes `error.` to `detail.code`, so backend code `subscription.expired` maps to i18n key `error.subscription.expired`.
7. The `signed_up_org` fixture (in `conftest.py`) creates an org with `subscription_status='trialing'`, `trial_ends_at = now + 5 days`. So it has `can_write=True` by default. Tests that need an expired org will manually `UPDATE organizations` via the `db.execute` helper.
8. Frontend container is volume-mounted; JS/CSS/HTML changes hot-reload on browser refresh.
9. JS parse check: `node -e "new Function(require('fs').readFileSync('frontend/app.js','utf8'))" && echo OK`.
10. i18n parity is gated by `scripts/check_i18n.py`. Run after any i18n change.
11. Do NOT modify `.env` or rewrite prod URLs.

---

## Task 1: Helper — `subscription_state` + `_parse_iso`

**Files:**
- Modify: `backend/main.py` (add helpers near the dayparting resolver, around line 2030)
- Create: `backend/tests/test_subscription_gate.py`

**Goal:** Pure helper that converts raw org columns into a derived state with `can_write`, `days_remaining`, `expires_at`. 10 unit tests. No DB writes, no FastAPI integration in this task.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_subscription_gate.py`:

```python
"""Tests for the subscription_state helper + require_active_subscription dep
+ /organization response shape (Phase 2.5f)."""
from datetime import datetime, timedelta, timezone

from main import subscription_state, _parse_iso


def _now():
    return datetime.now(timezone.utc)


# ── subscription_state ────────────────────────────────────────────────

def test_trialing_in_future():
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       (_now() + timedelta(days=3)).isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trialing"
    assert s["can_write"] is True
    assert s["days_remaining"] == 3


def test_trialing_expired():
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       (_now() - timedelta(days=1)).isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False
    assert s["days_remaining"] == 0


def test_trialing_exact_boundary():
    """trial_ends_at == now → expired (strict)."""
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       _now().isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False


def test_trialing_no_trial_ends_at():
    org = {"subscription_status": "trialing",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False


def test_active_in_future():
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     (_now() + timedelta(days=10)).isoformat(),
    }
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] == 10


def test_active_lapsed():
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     (_now() - timedelta(days=1)).isoformat(),
    }
    s = subscription_state(org)
    assert s["state"] == "lapsed"
    assert s["can_write"] is False


def test_active_no_expiry():
    """status=active + paid_through_at=NULL → no-expiry override (seeded default)."""
    org = {"subscription_status": "active",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] is None


def test_unknown_status():
    """Unknown status → conservative: allow writes."""
    org = {"subscription_status": "failed",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["can_write"] is True


def test_handles_string_trial_ends_at():
    """trial_ends_at stored as TEXT (ISO string) — must parse cleanly."""
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       "2099-01-01T00:00:00+00:00",
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trialing"
    assert s["can_write"] is True


def test_handles_datetime_paid_through():
    """paid_through_at stored as TIMESTAMPTZ — psycopg returns datetime obj."""
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     _now() + timedelta(days=30),
    }
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] == 30
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py 2>&1 | tail -10
```
Expected: `ImportError: cannot import name 'subscription_state' from 'main'`.

If pytest doesn't even collect the new file, rebuild first (`docker-compose build backend && docker-compose up -d --force-recreate backend`).

- [ ] **Step 3: Add helpers to `backend/main.py`**

Find the dayparting helpers block (search `def resolve_active_playlist` or `# ── Dayparting (Phase 2.5e)`). Insert IMMEDIATELY BEFORE that block:

```python
# ── Subscription state (Phase 2.5f) ───────────────────────────────────


def _parse_iso(value) -> Optional[datetime]:
    """Accept str (ISO) or already-parsed datetime; return tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def subscription_state(org: dict) -> dict:
    """Derive subscription state from raw org columns.

    Returns:
      {
        "state":          "trialing" | "trial_expired" | "active" | "lapsed",
        "can_write":      bool,
        "days_remaining": int | None,
        "expires_at":     ISO string | None,
      }

    Convention: subscription_status='active' with paid_through_at IS NULL
    means "no expiry" (seeded default org, admin override).
    """
    status = org.get("subscription_status") or "trialing"
    now = datetime.now(timezone.utc)

    if status == "trialing":
        ts = _parse_iso(org.get("trial_ends_at"))
        if ts and ts > now:
            return {
                "state":          "trialing",
                "can_write":      True,
                "days_remaining": max(0, (ts - now).days),
                "expires_at":     ts.isoformat(),
            }
        return {
            "state":          "trial_expired",
            "can_write":      False,
            "days_remaining": 0,
            "expires_at":     ts.isoformat() if ts else None,
        }

    if status == "active":
        ts = _parse_iso(org.get("paid_through_at"))
        if ts is None:
            return {"state": "active", "can_write": True,
                    "days_remaining": None, "expires_at": None}
        if ts > now:
            return {
                "state":          "active",
                "can_write":      True,
                "days_remaining": max(0, (ts - now).days),
                "expires_at":     ts.isoformat(),
            }
        return {
            "state":          "lapsed",
            "can_write":      False,
            "days_remaining": 0,
            "expires_at":     ts.isoformat(),
        }

    # Unknown status: be conservative — allow writes, surface to admin
    return {"state": status, "can_write": True,
            "days_remaining": None, "expires_at": None}
```

Verify `datetime`, `timezone`, `timedelta`, `Optional` are already imported (they are; the existing code uses them).

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run helper tests, confirm pass**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py
```
Expected: 10 PASS.

- [ ] **Step 6: Run full suite, confirm no regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `234 passed` (224 baseline + 10 new).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_gate.py
git commit -m "$(cat <<'EOF'
feat(trial): subscription_state + _parse_iso helpers

Pure functions: subscription_state(org) returns {state, can_write,
days_remaining, expires_at} derived from (subscription_status,
trial_ends_at, paid_through_at). _parse_iso handles both TEXT
(stored as ISO) and TIMESTAMPTZ (returned as datetime) columns.

Convention: status=active + paid_through_at=NULL means no-expiry
(seeded default org, admin override).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Dependency — `require_active_subscription`

**Files:**
- Modify: `backend/main.py` (add dep near `require_roles`, around line 470)
- Modify: `backend/tests/test_subscription_gate.py` (append 9 dep + response-shape tests)

**Goal:** FastAPI dependency that runs after `get_current_user` and raises 402 when `can_write` is False. Verifies the gate works on one canonical endpoint (POST /playlists) — full sweep applied in Task 3.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_subscription_gate.py`:

```python
# ── require_active_subscription dependency ────────────────────────────
from db import execute


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _expire_trial(org_id: int):
    """Force the org's trial into the past."""
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'trialing', "
        "    trial_ends_at = (now() - interval '1 day')::text, "
        "    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _lapse_paid(org_id: int):
    """Force the org into the lapsed state (was paying, paid_through is past)."""
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'active', "
        "    trial_ends_at = NULL, "
        "    paid_through_at = now() - interval '1 day' "
        "WHERE id = ?",
        (org_id,),
    )


def _set_no_expiry(org_id: int):
    """status=active + paid_through_at=NULL (seeded-default convention)."""
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'active', "
        "    trial_ends_at = NULL, "
        "    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _create_simple_playlist_payload():
    return {"name": "Test playlist"}


def test_write_blocked_when_trial_expired(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["detail"]["code"] == "subscription.trial_expired"
    assert body["detail"]["state"] == "trial_expired"
    assert "expires_at" in body["detail"]


def test_write_blocked_when_active_lapsed(client, signed_up_org):
    _lapse_paid(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["detail"]["code"] == "subscription.expired"
    assert body["detail"]["state"] == "lapsed"


def test_write_allowed_when_active_no_expiry(client, signed_up_org):
    _set_no_expiry(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text


def test_write_allowed_when_trialing(client, signed_up_org):
    # signed_up_org is in trialing state with 5 days remaining
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text


def test_read_allowed_when_expired(client, signed_up_org):
    """Read endpoints work even when expired."""
    _expire_trial(signed_up_org["org"]["id"])
    r = client.get("/playlists", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text


def test_billing_endpoints_allowed_when_expired(client, signed_up_org):
    """User must be able to renew when expired."""
    _expire_trial(signed_up_org["org"]["id"])
    # /billing/checkout requires a payload but reaches the handler; even
    # a 4xx from invalid-payload validation proves the gate did NOT fire.
    r = client.post("/billing/checkout",
                    headers=_bearer(signed_up_org["token"]),
                    json={"tier": "starter", "term_months": 1})
    assert r.status_code != 402, r.text


def test_auth_endpoints_allowed_when_expired(client, signed_up_org):
    """Logout etc. work even when expired."""
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/auth/logout", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text


def test_402_response_includes_state_and_expires_at(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    body = r.json()
    detail = body["detail"]
    assert detail["code"] in ("subscription.trial_expired", "subscription.expired")
    assert "state" in detail
    assert "expires_at" in detail
    assert detail.get("message_key", "").startswith("error.subscription.")


def test_unknown_status_does_not_block(client, signed_up_org):
    execute(
        "UPDATE organizations SET subscription_status = 'failed', "
        "trial_ends_at = NULL, paid_through_at = NULL WHERE id = ?",
        (signed_up_org["org"]["id"],),
    )
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text
```

- [ ] **Step 2: Verify failure** (no dep on /playlists yet, every test that expects 402 fails)

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py -k "blocked or 402" 2>&1 | tail -10
```
Expected: 2-3 fail because expired/lapsed writes return 200 instead of 402.

- [ ] **Step 3: Add `require_active_subscription` to `backend/main.py`**

Find `def require_roles(*roles: str):` (around line 470). Insert IMMEDIATELY AFTER its full definition (and any helpers in that block):

```python
def require_active_subscription(user: dict = Depends(get_current_user)) -> dict:
    """Block write operations when the org's subscription is expired/lapsed.

    Used as a FastAPI dependency, alongside require_roles when both are needed:
        Depends(require_roles("admin"))         # role check
        Depends(require_active_subscription)    # subscription check
    Both run; both must pass.
    """
    org = query_one(
        "SELECT id, subscription_status, trial_ends_at, paid_through_at "
        "FROM organizations WHERE id = ?",
        (org_id(user),),
    )
    if not org:
        raise http_error(403, "no_organization", "No organization for user")

    state = subscription_state(org)
    if not state["can_write"]:
        code = ("subscription.trial_expired" if state["state"] == "trial_expired"
                else "subscription.expired")
        raise HTTPException(
            status_code=402,
            detail={
                "code":        code,
                "message":     "Subscription required to make changes.",
                "message_key": "error." + code,
                "state":       state["state"],
                "expires_at":  state["expires_at"],
            },
        )
    return user
```

- [ ] **Step 4: Apply the dep to `POST /playlists` only** (proves the wiring; full sweep in Task 3)

Find `@app.post("/playlists")` (around line 2756). The handler currently looks like:

```python
@app.post("/playlists")
def create_playlist(payload: PlaylistCreate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    ...
```

Add ONE new dependency parameter:

```python
@app.post("/playlists")
def create_playlist(payload: PlaylistCreate,
                    user: dict = Depends(require_roles("admin", "editor")),
                    _sub: dict = Depends(require_active_subscription)) -> dict:
    ...
```

The `_sub` arg name (underscore prefix) keeps the value un-used in the body — the dep fires for its side effect.

- [ ] **Step 5: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 6: Run dep tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py
```
Expected: 19 PASS (10 helper + 9 new dep).

- [ ] **Step 7: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `243 passed` (234 + 9 new).

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_gate.py
git commit -m "$(cat <<'EOF'
feat(trial): require_active_subscription dep + applied to /playlists

402 Payment Required when org's can_write is False. Response detail
includes state + expires_at so the frontend banner can render the
right tone. Code is subscription.trial_expired (was trialing) or
subscription.expired (was active, paid_through past).

Applied to POST /playlists as the canonical proof; full ~45-endpoint
sweep lands in Task 3.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Apply `require_active_subscription` to all write endpoints

**Files:**
- Modify: `backend/main.py` (~44 endpoint signatures)

**Goal:** Add `_sub: dict = Depends(require_active_subscription)` to every write endpoint that mutates org-scoped data. Mechanical sweep; tests verify nothing breaks.

**Endpoints to gate** (these all already have `Depends(require_roles(...))` or `Depends(get_current_user)` — add the new dep alongside):

```
POST  /media/upload                   line ~2922
POST  /media/url                      line ~2962
DELETE /media/{media_id}              line ~2981
PUT   /playlists/{playlist_id}        line ~2795
DELETE /playlists/{playlist_id}       line ~2814
POST  /playlists/{playlist_id}/items  line ~2847
DELETE /playlists/{playlist_id}/items/{item_id}   line ~2888
POST  /screens                        line ~1560
PUT   /screens/{screen_id}            line ~1605
DELETE /screens/{screen_id}           line ~1658
PUT   /screens/{screen_id}/zones      line ~1689
PUT   /screens/{screen_id}/groups     line ~1933
POST  /zone-templates                 line ~1789
POST  /screens/{screen_id}/zone-templates/apply   line ~1807
POST  /screens/request_code           line ~2140
POST  /screens/claim                  line ~2204
POST  /screens/{screen_id}/preview-token          line ~2705
POST  /walls                          line ~2305
PATCH /walls/{wall_id}                line ~2382
PATCH /walls/{wall_id}/cells          line ~2422
DELETE /walls/{wall_id}               line ~2451
POST  /walls/{wall_id}/cells/{row}/{col}/pair     line ~2475
POST  /walls/cells/redeem             line ~2501
DELETE /walls/{wall_id}/cells/{row}/{col}/pairing line ~2550
POST  /walls/{wall_id}/canvas-playlist/items      line ~2620
PATCH /walls/{wall_id}/canvas-playlist/items/{item_id}   line ~2655
DELETE /walls/{wall_id}/canvas-playlist/items/{item_id}  line ~2673
POST  /schedules                      line ~1193
PUT   /schedules/{sid}                line ~1223
DELETE /schedules/{sid}               line ~1234
PUT   /schedules/{sid}/rules          line ~1243
POST  /users                          line ~1367
PUT   /users/{user_id}                line ~1397
DELETE /users/{user_id}               line ~1435
PUT   /users/{user_id}/groups         line ~1876
POST  /groups                         line ~1841
PUT   /groups/{group_id}              line ~1850
DELETE /groups/{group_id}             line ~1862
POST  /sites                          line ~1459
PUT   /sites/{site_id}                line ~1478
DELETE /sites/{site_id}               line ~1514
PATCH /organizations/me               line ~980
```

**Endpoints to NOT gate** (skip these — they must work when expired):

```
POST /auth/signup/request             line ~778
POST /auth/signup/verify              line ~836
POST /auth/signup/complete            line ~883
POST /auth/login                      line ~995
POST /auth/logout                     line ~1098
POST /auth/change-password            line ~1105
POST /screens/pair                    line ~2246   (display-side, no user auth)
POST /billing/checkout                line ~3035
POST /billing/callback/{trackid}      line ~3124
```

- [ ] **Step 1: Apply the dep to each endpoint listed above**

For each, find the handler. Each currently has a `user: dict = Depends(...)` parameter (typically `require_roles(...)` or `get_current_user`). Add ONE new line in the parameter list — keep existing params, just append `_sub: dict = Depends(require_active_subscription)`. Example:

```python
# Before:
@app.delete("/media/{media_id}")
def delete_media(media_id: int, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    ...

# After:
@app.delete("/media/{media_id}")
def delete_media(media_id: int,
                 user: dict = Depends(require_roles("admin", "editor")),
                 _sub: dict = Depends(require_active_subscription)) -> dict:
    ...
```

(Single-line parameter lists become multi-line for readability.)

The handler at line 980 — `PATCH /organizations/me` — also gets the dep. Note: this is the locale-change endpoint; verify by reading its body before editing. If the actual path differs from `/organizations/me`, adapt accordingly.

**Important — don't accidentally gate auth/billing.** Before each edit, confirm the endpoint is in the "gate" list above, not the "skip" list. The skip endpoints stay untouched.

- [ ] **Step 2: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 3: Verify the existing dep + helper tests still pass**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py
```
Expected: 19 PASS.

- [ ] **Step 4: Run full suite — this is the critical check**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `243 passed` (same as Task 2 — no NEW tests, but every existing test that hits a gated endpoint must still pass because `signed_up_org` is in trialing state with 5 days remaining → `can_write=True`).

**If a test fails:** it's likely a test that hits a gated endpoint with an org in a non-`can_write` state. Investigate:
- If the failing test is in `test_subscription_gate.py`, it's expected behavior — the test set up the state.
- If the failing test is elsewhere and the org should be writable, something in the dep is misbehaving. Check the org row's `subscription_status` and `trial_ends_at` at the time of the failure.

If a test failure is genuine (e.g. a test relies on an org being writable but the test mutated trial_ends_at and forgot to reset), fix it by resetting state at the end of the test or in a fixture.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "$(cat <<'EOF'
feat(trial): gate all write endpoints with require_active_subscription

Adds the dep to ~44 write endpoints across media, playlists, screens,
walls, schedules, users, groups, sites, and org-locale. Auth, billing,
display-side pairing, and all GETs remain ungated.

Existing tests pass because signed_up_org fixture starts in trialing
state with 5 days remaining (can_write=True).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Extend response shape — `GET /organization` + login + signup

**Files:**
- Modify: `backend/main.py` (3 endpoint response bodies)
- Modify: `backend/tests/test_subscription_gate.py` (append 1 response-shape test)

**Goal:** The frontend banner reads derived state from the `GET /organization` response on boot, and from the login + signup responses on first load. All three responses gain `state`, `can_write`, `days_remaining`, `expires_at`.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_subscription_gate.py`:

```python
# ── /organization response shape ──────────────────────────────────────


def test_organization_response_includes_derived_fields(client, signed_up_org):
    r = client.get("/organization", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    # Derived fields from subscription_state must be present:
    assert "state" in body
    assert "can_write" in body
    assert "days_remaining" in body
    assert "expires_at" in body
    # Fresh signup → trialing
    assert body["state"] == "trialing"
    assert body["can_write"] is True
    assert isinstance(body["days_remaining"], int)
    assert body["days_remaining"] >= 0


def test_login_response_includes_derived_fields(client, signed_up_org):
    # signed_up_org provides {token, user, org}. Log in again to get a fresh
    # login response payload and inspect it.
    r = client.post("/auth/login", json={
        "username": signed_up_org["user"]["username"],
        "password": "Khanshoof2026Test",
    })
    assert r.status_code == 200, r.text
    org = r.json().get("organization", {})
    assert "state" in org
    assert "can_write" in org
    assert "days_remaining" in org


def test_signup_response_includes_derived_fields(client, unique_business):
    """Fresh signup → response.organization has state=trialing, can_write=True."""
    r = client.post("/auth/signup/request",
                    json={"business_name": unique_business["business_name"],
                          "email": unique_business["email"]})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": unique_business["email"], "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": unique_business["password"]})
    assert r.status_code == 200, r.text
    org = r.json().get("organization", {})
    assert org.get("state") == "trialing"
    assert org.get("can_write") is True
```

- [ ] **Step 2: Run them, confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py -k "derived"
```
Expected: 3 FAIL (the new fields aren't in the response yet).

- [ ] **Step 3: Update `GET /organization` in `backend/main.py`**

Find `@app.get("/organization")` (search for it). Current body fetches the org and returns a dict. Add the derived state. The new body:

```python
@app.get("/organization")
def get_organization(user: dict = Depends(get_current_user)) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    if not org:
        raise http_error(404, "organization_not_found", "Organization not found")
    state = subscription_state(org)
    return {
        "id":                  org["id"],
        "name":                org["name"],
        "slug":                org["slug"],
        "plan":                org["plan"],
        "screen_limit":        org["screen_limit"],
        "subscription_status": org["subscription_status"],
        "trial_ends_at":       org["trial_ends_at"],
        "paid_through_at":     org["paid_through_at"].isoformat() if org.get("paid_through_at") else None,
        "locale":              org.get("locale", "en"),
        # Phase 2.5f derived fields:
        "state":               state["state"],
        "can_write":           state["can_write"],
        "days_remaining":      state["days_remaining"],
        "expires_at":          state["expires_at"],
    }
```

(If the existing handler is structured differently, preserve the existing fields and only ADD the four derived ones. Don't remove anything that's already there.)

- [ ] **Step 4: Extend `/auth/login` response**

Find `@app.post("/auth/login")` (around line 995). The handler returns a dict with `token, user, organization`. The `organization` sub-dict needs to gain the 4 derived fields.

Find the line that builds the response (search for `"organization":` inside the login handler). The existing return-dict structure typically looks like:

```python
return {
    "token": token,
    "user": {...},
    "organization": {
        "id":                  org["id"],
        "name":                org["name"],
        ...
    },
}
```

In the `"organization"` sub-dict, add the 4 derived fields. Just BEFORE the `}` that closes the `organization` dict, add:

```python
        # Phase 2.5f derived fields:
        "state":               subscription_state(org)["state"],
        "can_write":           subscription_state(org)["can_write"],
        "days_remaining":      subscription_state(org)["days_remaining"],
        "expires_at":          subscription_state(org)["expires_at"],
```

**Inefficient:** that calls the helper 4 times. Cleaner is to compute once and merge. Replace with:

```python
    sub_state = subscription_state(org)
    return {
        "token": token,
        "user": {...},   # keep existing
        "organization": {
            # keep existing fields ...
            "state":          sub_state["state"],
            "can_write":      sub_state["can_write"],
            "days_remaining": sub_state["days_remaining"],
            "expires_at":     sub_state["expires_at"],
        },
    }
```

Read the actual login handler before editing and adapt to its style.

- [ ] **Step 5: Extend `/auth/signup/complete` response**

Find `@app.post("/auth/signup/complete")` (around line 883). The handler returns a dict with `token, user, organization`. Same pattern — compute `subscription_state(org)` once, add the four derived fields to the `organization` sub-dict.

If the `org` variable isn't directly available in this handler (it might be built field-by-field from `new_org_id`, `business_name`, etc.), construct a synthetic org dict for the helper:

```python
fake_org = {
    "subscription_status": "trialing",
    "trial_ends_at":       trial_ends_at,   # already in scope
    "paid_through_at":     None,
}
sub_state = subscription_state(fake_org)
```

Then include the derived fields in the response.

- [ ] **Step 6: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 7: Run response-shape tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_gate.py -k "derived"
```
Expected: 3 PASS.

- [ ] **Step 8: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `246 passed` (243 + 3 new).

- [ ] **Step 9: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_gate.py
git commit -m "$(cat <<'EOF'
feat(trial): extend /organization + login + signup responses

All three include the derived state, can_write, days_remaining,
expires_at fields so the frontend banner can render on boot without
a second API call.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend banner

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Goal:** Persistent top-of-app banner that reads the derived state from `state.org`. Color varies by state (info/warn/error). Dismissable per session. Re-renders after a 402.

- [ ] **Step 1: Add markup in `frontend/index.html`**

Find the opening `<body>` tag. Add the banner immediately inside it, before the existing header / dashboard:

```html
<div id="subscription-banner" class="sub-banner hidden" role="status" aria-live="polite">
  <span id="subscription-banner-text"></span>
  <a id="subscription-banner-cta" href="#" class="sub-banner-cta"></a>
  <button id="subscription-banner-dismiss" class="sub-banner-dismiss" aria-label="Dismiss">×</button>
</div>
```

- [ ] **Step 2: Add CSS in `frontend/styles.css`**

Append:

```css
/* Phase 2.5f — subscription banner */
.sub-banner {
  position: sticky;
  inset-block-start: 0;
  z-index: 20;
  padding: 10px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  border-block-end: 1px solid transparent;
}
.sub-banner-info  { background: #e6f0ff; color: #2c4d80; border-block-end-color: #c8dafa; }
.sub-banner-warn  { background: #fdf3d6; color: #6a4a14; border-block-end-color: #f1d68a; }
.sub-banner-error { background: #fde8e8; color: #7a1f1f; border-block-end-color: #f4b8b8; }
.sub-banner-cta {
  margin-inline-start: auto;
  font-weight: 600;
  text-decoration: underline;
  color: inherit;
}
.sub-banner-dismiss {
  background: transparent; border: 0;
  cursor: pointer; font-size: 18px; line-height: 1;
  color: inherit; padding: 0 4px;
}
.sub-banner.hidden { display: none; }
```

- [ ] **Step 3: Add the `SubscriptionBanner` IIFE to `frontend/app.js`**

Append at the END of `frontend/app.js` (after the last existing IIFE — likely `Schedules.init()` or `AuditLog.init()`):

```javascript
// ── Subscription banner (Phase 2.5f) ─────────────────────────────────
const SubscriptionBanner = (() => {
  const DISMISS_KEY = "khan_sub_banner_dismissed_state";
  let lastState = null;   // captured by init() click handler

  function update(orgData) {
    const banner = document.getElementById("subscription-banner");
    if (!banner) return;

    const subState = orgData?.state;
    const days     = orgData?.days_remaining;
    lastState      = subState;

    // Reset dismiss memory if state has changed since last dismiss
    const dismissedFor = sessionStorage.getItem(DISMISS_KEY);
    if (dismissedFor && dismissedFor !== subState) {
      sessionStorage.removeItem(DISMISS_KEY);
    }
    if (sessionStorage.getItem(DISMISS_KEY) === subState) {
      banner.classList.add("hidden");
      return;
    }

    const cfg = computeBannerConfig(subState, days);
    if (!cfg) { banner.classList.add("hidden"); return; }

    banner.classList.remove("hidden", "sub-banner-info", "sub-banner-warn", "sub-banner-error");
    banner.classList.add(`sub-banner-${cfg.tone}`);
    document.getElementById("subscription-banner-text").textContent = cfg.text;

    const cta = document.getElementById("subscription-banner-cta");
    cta.textContent = cfg.ctaText;
    cta.onclick = (e) => {
      e.preventDefault();
      if (typeof showSection === "function") showSection("billing");
    };
  }

  function computeBannerConfig(state, days) {
    switch (state) {
      case "trialing":
        if (days == null) return null;
        if (days > 3) return {
          tone:    "info",
          text:    Khan.t("sub_banner.trialing", "Trial — {n} days left.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
        return {
          tone:    "warn",
          text:    Khan.t("sub_banner.trialing_urgent", "Trial ends in {n} days.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
      case "trial_expired":
        return {
          tone:    "error",
          text:    Khan.t("sub_banner.trial_expired", "Trial ended — subscribe to make changes."),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
      case "active":
        if (days != null && days <= 7) return {
          tone:    "warn",
          text:    Khan.t("sub_banner.renewal_soon", "Renewal in {n} days.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_manage", "Manage"),
        };
        return null;
      case "lapsed":
        return {
          tone:    "error",
          text:    Khan.t("sub_banner.lapsed", "Subscription expired — renew to make changes."),
          ctaText: Khan.t("sub_banner.cta_renew", "Renew"),
        };
      default:
        return null;
    }
  }

  function init() {
    document.getElementById("subscription-banner-dismiss")
      ?.addEventListener("click", () => {
        if (lastState) sessionStorage.setItem(DISMISS_KEY, lastState);
        document.getElementById("subscription-banner").classList.add("hidden");
      });
  }

  return { update, init };
})();

SubscriptionBanner.init();
```

- [ ] **Step 4: Wire `SubscriptionBanner.update` into boot**

The frontend's existing `bootData()` (or login/signup completion path) saves the org payload to a state object. Find where the org payload first lands in state. Search for `state.org` and `organization` assignments in `frontend/app.js`:

```bash
grep -nE "state\.org\s*=|state\['org'\]" frontend/app.js | head
```

After every assignment of the org data into state, call `SubscriptionBanner.update(state.org)`. Likely 2-3 sites:
- `bootData()` after `GET /organization` resolves
- Login submit handler after success
- Signup completion handler after success

Pattern:
```javascript
state.org = data.organization;
SubscriptionBanner.update(state.org);
```

If the existing code uses `data.organization` directly without storing it in state first, store it (the banner needs to read `state.org.state` on dismiss).

- [ ] **Step 5: Intercept 402 in the `api()` helper**

Find `function api(path, options = {})` (around line 187 of `frontend/app.js`). Inside the existing `if (!res.ok)` block (where errors are constructed), add the subscription-aware branch BEFORE the `throw err` line:

```javascript
if (data?.detail?.code?.startsWith("subscription.")) {
  // Refresh org state so banner re-renders correctly
  try {
    const fresh = await fetch(`${API_BASE}/organization`, { headers });
    if (fresh.ok) {
      state.org = await fresh.json();
      SubscriptionBanner.update(state.org);
    }
  } catch (_) { /* swallow */ }
}
```

(The 402 still throws; the caller decides how to handle it — typically a localized toast via existing `localizeError` + `toast` flow.)

- [ ] **Step 6: Add i18n keys to `frontend/i18n/en.json`**

Insert (preserve alphabetical order if the file uses it):

```json
  "sub_banner.trialing": "Trial — {n} days left.",
  "sub_banner.trialing_urgent": "Trial ends in {n} days.",
  "sub_banner.trial_expired": "Trial ended — subscribe to make changes.",
  "sub_banner.renewal_soon": "Renewal in {n} days.",
  "sub_banner.lapsed": "Subscription expired — renew to make changes.",
  "sub_banner.cta_subscribe": "Subscribe",
  "sub_banner.cta_renew": "Renew",
  "sub_banner.cta_manage": "Manage",
  "error.subscription.trial_expired": "Your trial has ended. Subscribe to continue making changes.",
  "error.subscription.expired": "Your subscription has expired. Renew to continue making changes.",
```

- [ ] **Step 7: Add the same keys to `frontend/i18n/ar.json`** (MSA Arabic)

```json
  "sub_banner.trialing": "التجربة — تبقى {n} أيام.",
  "sub_banner.trialing_urgent": "تنتهي التجربة خلال {n} أيام.",
  "sub_banner.trial_expired": "انتهت التجربة — اشترك لإجراء التغييرات.",
  "sub_banner.renewal_soon": "التجديد خلال {n} أيام.",
  "sub_banner.lapsed": "انتهى الاشتراك — جدّد لإجراء التغييرات.",
  "sub_banner.cta_subscribe": "اشترك",
  "sub_banner.cta_renew": "جدّد",
  "sub_banner.cta_manage": "إدارة",
  "error.subscription.trial_expired": "انتهت تجربتك. اشترك لمتابعة إجراء التغييرات.",
  "error.subscription.expired": "انتهى اشتراكك. جدّد لمتابعة إجراء التغييرات.",
```

- [ ] **Step 8: i18n parity check**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: `OK`.

- [ ] **Step 9: JS parse**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 10: Commit**

```bash
cd /home/ahmed/signage
git add frontend/index.html frontend/app.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(trial): subscription banner + 402-aware api() error path

Top-of-app sticky banner. Color varies by state:
  trialing >3d:    info (blue)
  trialing ≤3d:    warn (amber)
  trial_expired:   error (red)
  active ≤7d:      warn — renewal soon
  active otherwise: hidden
  lapsed:          error (red)

Dismissable per session (sessionStorage). Resets when state changes.
On any 402 with code starting "subscription.", api() helper refetches
/organization and re-renders the banner — so dismissed banners come
back when expiry semantics actually fire.

10 new i18n keys EN+AR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Regression + push + PR

**Files:** none modified.

- [ ] **Step 1: Final backend test run**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `246 passed`.

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
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/i18n.js','utf8'))" && echo "i18n OK"
```

- [ ] **Step 4: Rebuild + recreate containers**

```bash
docker-compose build backend frontend
docker-compose up -d --force-recreate backend frontend
sleep 6
docker-compose ps
```
Expected: all services `(healthy)` or at least responding 200 on health/HTTP. If still "starting", curl-test:

```bash
curl -s -o /dev/null -w "backend %{http_code}\n" http://localhost:8000/health
curl -s -o /dev/null -w "frontend %{http_code}\n" http://localhost:3000/
```
Both should return 200.

- [ ] **Step 5: Manual browser smoke**

| Check | Pass criteria |
|---|---|
| Fresh signup banner | Sign up → banner shows "Trial — 5 days left." in info (blue) tone |
| Approach-expiry banner | `UPDATE organizations SET trial_ends_at = (now() + interval '2 days')::text WHERE id = …` → reload → warn (amber) tone "Trial ends in 2 days." |
| Expired banner | `UPDATE organizations SET trial_ends_at = (now() - interval '1 day')::text WHERE id = …` → reload → error (red) "Trial ended — subscribe to make changes." |
| Write blocked when expired | Try to create a playlist → 402 → toast "Your trial has ended..." + banner re-renders if dismissed |
| CTA navigation | Click banner CTA → lands on billing page |
| Dismiss persists | Dismiss banner → reload → banner returns (per-session, not permanent) |
| State-change re-shows | Dismiss banner in `trial_expired` state → SQL flip back to trialing in future → reload → banner shows new state (info tone) |
| Lapsed | `UPDATE organizations SET subscription_status='active', paid_through_at = now() - interval '1 day' WHERE id = …` → reload → red "Subscription expired" + CTA "Renew" |
| Player still works | Curl the player content endpoint with expired org → 200, items returned |
| AR locale | Toggle to AR → all banner strings localized |

- [ ] **Step 6: Push branch**

```bash
git push -u origin feature/trial-expiry-enforcement
```

- [ ] **Step 7: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(trial): Phase 2.5f — trial expiry + subscription gating" \
  --body "$(cat <<'EOF'
## Summary
- New pure helper `subscription_state(org)` derives `{state, can_write, days_remaining, expires_at}` from raw org columns.
- New FastAPI dep `require_active_subscription` raises 402 with state-aware code when org `can_write` is False.
- Dep applied to ~44 write endpoints across media, playlists, screens, walls, schedules, users, groups, sites, org-locale.
- Auth, billing, display-side pairing, and all GETs intentionally NOT gated — player content endpoints serve cached playlists for free even after expiry.
- `GET /organization` + `/auth/login` + `/auth/signup/complete` responses carry the derived state for first-render banner.
- New persistent top-of-app banner: info/warn/error tone by state, sessionStorage-based dismiss, CTA links to billing page. 402 from any write triggers re-fetch + re-render.

## Spec
`docs/superpowers/specs/2026-05-15-trial-expiry-enforcement-design.md`

## Plan
`docs/superpowers/plans/2026-05-15-trial-expiry-enforcement-plan.md`

## Test Plan
- [x] Backend: 246 passed (was 224 baseline; +22 new)
- [x] `scripts/check_i18n.py` parity OK
- [x] All four JS files parse
- [x] Containers rebuilt + healthy
- [ ] Browser smoke: trialing banner appears with correct copy + tone
- [ ] Browser smoke: SQL → trial_expired → red banner + write returns 402
- [ ] Browser smoke: SQL → lapsed → red banner + write returns 402
- [ ] Browser smoke: SQL → active no-expiry → no banner, writes work
- [ ] Browser smoke: dismiss persists across reload within session
- [ ] Browser smoke: dismiss resets when state changes
- [ ] Browser smoke: player /content still serves playlist when org is expired
- [ ] Browser smoke: AR locale strings render

## Non-goals (queued)
- Email reminders at day -3, day 0 of trial; day -7 of renewal (Resend)
- Admin "extend trial" / "manually mark as paid" UI
- Self-serve cancellation
- Hard lockout (block login itself) after N days expired
- Audit-log entries for state transitions

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Save memory file**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_trial_expiry.md`:

```markdown
---
name: Trial expiry enforcement (Phase 2.5f) — branch
description: subscription_state helper + require_active_subscription dep on ~44 write endpoints + top-of-app banner. PR pending.
type: project
---

**Status (2026-05-15):** PR #<TBD> opened against main. Awaiting browser smoke + merge.

**What landed:**
- Pure helper `subscription_state(org) -> {state, can_write, days_remaining, expires_at}` covering trialing/trial_expired/active/lapsed plus the no-expiry override (status=active + paid_through_at=NULL = seeded default org).
- `require_active_subscription` FastAPI dependency. Raises HTTP 402 with code `subscription.trial_expired` or `subscription.expired`. Response includes `state` and `expires_at` so the frontend banner can re-render.
- Dep applied to ~44 write endpoints: media (3), playlists (5), screens (8), walls (10), schedules (4), users (4), groups (3), sites (3), org-locale (1), preview-token (1), zone-templates (2).
- NOT applied: `/auth/*` (login, logout, signup, change-password), `/billing/*` (must be able to renew), `/screens/pair` (display-side handshake), all GETs.
- `GET /organization` + `/auth/login` + `/auth/signup/complete` responses gain the 4 derived fields.
- Frontend `SubscriptionBanner` IIFE: sticky top banner, color by state (info/warn/error), dismissable per session via sessionStorage, resets on state change. 402 in `api()` helper triggers refetch + re-render. 10 i18n keys EN+AR.

**Test count:** 246 backend tests passing (was 224 pre-branch; +22 new).

**Plan:** `docs/superpowers/plans/2026-05-15-trial-expiry-enforcement-plan.md` — 6 tasks.
**Spec:** `docs/superpowers/specs/2026-05-15-trial-expiry-enforcement-design.md`.

**Why no grace period:** user explicitly chose strict cutoff. Renewal flow exists (`POST /billing/checkout`); if the user is in lapsed state they can immediately re-checkout.

**Convention to preserve:** `status=active` + `paid_through_at IS NULL` = no-expiry override (seeded default org, manual admin marker). Don't break this. Tests assert it explicitly.

**Sequence:** Arabic [DONE], Security [DONE], Offline [DONE], Dayparting [DONE], Trial expiry [this PR], **Subscription renewal reminders** (C — next).

**Out of scope (queued):**
- Email reminders (trial day -3, day 0; renewal day -7) — phase C
- Admin "extend trial" / "mark as paid" UI
- Self-serve cancellation
- Hard lockout (block login) after N days expired
- Audit-log entries for subscription state transitions
- Mid-cycle upgrade/downgrade
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` index with a one-line entry pointing at the new file.

- [ ] **Step 9: Final verification**

```bash
git status -sb
~/.local/bin/gh pr view --json number,url,state | head
```
Expected: PR open. Working tree clean except for any leftover untracked items.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §5 `subscription_state` helper | Task 1 |
| §5 `_parse_iso` helper | Task 1 |
| §6 `require_active_subscription` dep | Task 2 |
| §7 endpoint coverage (gated list) | Task 3 |
| §7 endpoint coverage (exempt list) | Task 3 (preserves auth/billing/GETs) |
| §8 `GET /organization` response | Task 4 |
| §8 login + signup responses | Task 4 |
| §9 banner markup | Task 5 step 1 |
| §9 banner styles | Task 5 step 2 |
| §9 banner IIFE | Task 5 step 3 |
| §9 wiring into bootData | Task 5 step 4 |
| §9 wiring into api() 402 path | Task 5 step 5 |
| §9 i18n keys | Task 5 steps 6-7 |
| §10 backend tests | Tasks 1, 2, 4 |
| §10 manual smoke | Task 6 step 5 |
| §11 file layout | All file paths match |
| §12 failure modes | Documented in tests + spec |
| §13 no migration | No schema changes |

No placeholders. Method names + state literal strings consistent across tasks (`trialing`, `trial_expired`, `active`, `lapsed`; `subscription.trial_expired`, `subscription.expired`; `can_write`, `days_remaining`, `expires_at`).

Task ordering: 1 (helper) → 2 (dep + canonical wiring) → 3 (full sweep) → 4 (response shape) → 5 (frontend) → 6 (regression + PR). Each task ends green with all tests passing.
