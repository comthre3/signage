# Phase 2.5f — Trial Expiry Enforcement — Design

**Date:** 2026-05-15
**Branch:** `feature/trial-expiry-enforcement` (fresh, branched from `main` at `36c9e5a`)
**Predecessors merged:** Phase 2.5c security (`79b2174`), Phase 2.5d offline (`4a236db`), Phase 2.5e dayparting (`36c9e5a`).

---

## 1. Goal

Trial users can't keep using the product forever for free; lapsed paid customers must renew to keep editing content. Player playback for *existing* screens never goes dark — that is a hard constraint for signage (customer-facing screens visibly going to "subscription expired" is a brand-destroying failure mode).

## 2. Existing State (the gap)

The schema already has all the columns needed; the code never reads them for gating.

- `organizations.subscription_status` — values written today: `'trialing'` (signup) and `'active'` (after CAPTURED Niupay payment). Never set to anything else.
- `organizations.trial_ends_at` — `TEXT` (ISO string), set at signup to `now + 5 days`.
- `organizations.paid_through_at` — `TIMESTAMPTZ`, set after CAPTURED payment to `now + term_months × 30 days`. May be NULL for the seeded "Default" org and for any manual admin override.

**No code anywhere checks whether `trial_ends_at` or `paid_through_at` has passed.** Trial users keep full access forever; lapsed paid orgs keep full access too. This is the bug Phase 2.5f closes.

## 3. Design Choices (recap from brainstorm)

1. **Block level:** read-only admin, player keeps playing. All `GET *` and `/billing/*` and `/auth/*` work; all write endpoints return 402 when expired.
2. **Grace period:** none. Strict cutoff at the timestamp.
3. **Banner UX:** persistent top-of-app banner above the nav. Color varies by state (info / warn / error). Dismissable per session.
4. **Enforcement:** single FastAPI dependency `require_active_subscription` applied to every write endpoint (~35 endpoints).

## 4. Non-Goals (deferred)

- Email reminders (trial day -3, day 0; renewal day -7 — Resend integration)
- Admin "extend trial" / "manually mark as paid" UI
- Self-serve cancellation flow
- Pro-rata refunds
- Hard lockout (block login itself) after N days expired
- Audit-log entries for state transitions (could extend Phase 2.5c)
- Renewal automation (KNET is one-shot per term; re-checkout is the renewal flow)

## 5. Component A — `subscription_state` Helper

Pure function in `backend/main.py` near the dayparting resolver helpers (around line 2030).

```python
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
```

**Why a single helper and not a class:** all callers want the same 4 fields. No state, no lifecycle. Stays a one-shot pure function.

**Why `_parse_iso` exists:** `trial_ends_at` is `TEXT` (ISO string) in the schema; `paid_through_at` is `TIMESTAMPTZ` (returned by psycopg as a `datetime`). The helper smooths over both.

## 6. Component B — `require_active_subscription` Dependency

```python
def require_active_subscription(user: dict = Depends(get_current_user)) -> dict:
    """Block write operations when the org's subscription is expired/lapsed.

    Used as a FastAPI dependency, alongside require_roles when both are needed:
        Depends(require_roles("admin"))
        Depends(require_active_subscription)
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

`402 Payment Required` is the right HTTP status. The frontend's existing `localizeError(detail)` helper prefixes `error.` to the code automatically when looking up i18n keys, so `subscription.expired` → `error.subscription.expired`.

## 7. Component C — Endpoint Coverage

### Gated (apply `Depends(require_active_subscription)`)

About **35 write endpoints** mutating org-scoped data:

| Resource | Endpoints |
|---|---|
| Media | `POST /media/upload`, `POST /media/url`, `DELETE /media/{id}` |
| Playlists | `POST /playlists`, `PUT /playlists/{id}`, `DELETE /playlists/{id}`, `POST /playlists/{id}/items`, `DELETE /playlists/{id}/items/{item_id}` |
| Screens | `POST /screens`, `PUT /screens/{id}`, `DELETE /screens/{id}`, `PUT /screens/{id}/zones`, `PUT /screens/{id}/groups`, `POST /screens/request_code`, `POST /screens/claim`, `POST /zone-templates`, `POST /screens/{id}/zone-templates/apply` |
| Walls | `POST /walls`, `PUT /walls/{id}`, `DELETE /walls/{id}`, `POST /walls/{id}/cells/{r}/{c}/pair`, `POST /walls/cells/redeem`, `DELETE /walls/{id}/cells/{r}/{c}/pairing`, `POST /walls/{id}/canvas-playlist/items`, `DELETE /walls/{id}/canvas-playlist/items/{item_id}` |
| Schedules | `POST /schedules`, `PUT /schedules/{id}`, `DELETE /schedules/{id}`, `PUT /schedules/{id}/rules` |
| Users | `POST /users`, `PUT /users/{id}`, `DELETE /users/{id}`, `PUT /users/{id}/groups` |
| Groups | `POST /groups`, `PUT /groups/{id}`, `DELETE /groups/{id}` |
| Sites | `POST /sites`, `PUT /sites/{id}`, `DELETE /sites/{id}` |
| Org | `PATCH /organization`, `PATCH /organization/locale` |
| Preview | `POST /screens/{id}/preview-token` |

### Not gated (explicitly exempt)

| Endpoint | Why |
|---|---|
| `POST /auth/login`, `POST /auth/logout`, `POST /auth/signup/*`, `POST /auth/change-password` | Auth must always work |
| `POST /billing/checkout`, `POST /billing/callback/{trackid}`, `GET /billing/status/{trackid}`, `GET /billing/history` | Must be able to renew |
| All `GET *` endpoints | Read-only allowed |
| `GET /screens/{token}/content`, `GET /screens/{token}/layout` | Player — playback must never go dark |
| `POST /screens/pair` | Display-side pairing handshake |
| `GET /preview/{preview_token}/*` | Preview tokens are read-only |
| `GET /audit-log` | Read-only |
| `GET /health` | Liveness probe |

### Implementation pattern

```python
# Before:
@app.post("/playlists")
def create_playlist(payload: PlaylistCreate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    ...

# After:
@app.post("/playlists")
def create_playlist(payload: PlaylistCreate,
                    user: dict = Depends(require_roles("admin", "editor")),
                    _:    dict = Depends(require_active_subscription)) -> dict:
    ...
```

The underscore arg name keeps the value un-used in the handler body — both deps fire for their side effects.

## 8. Component D — `GET /organization` Response Extension

The existing endpoint returns the org row. Extend the response payload to include the derived state:

```python
@app.get("/organization")
def get_organization(user: dict = Depends(get_current_user)) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    if not org:
        raise http_error(404, "organization_not_found", "Organization not found")
    state = subscription_state(org)
    return {
        # existing fields:
        "id":                  org["id"],
        "name":                org["name"],
        "slug":                org["slug"],
        "plan":                org["plan"],
        "screen_limit":        org["screen_limit"],
        "subscription_status": org["subscription_status"],
        "trial_ends_at":       org["trial_ends_at"],
        "paid_through_at":     org["paid_through_at"].isoformat() if org.get("paid_through_at") else None,
        "locale":              org.get("locale", "en"),
        # new derived fields (Phase 2.5f):
        "state":               state["state"],
        "can_write":           state["can_write"],
        "days_remaining":      state["days_remaining"],
        "expires_at":          state["expires_at"],
    }
```

The login and signup response bodies already include subscription metadata; they also gain `state`, `can_write`, `days_remaining`, `expires_at` for first-load banner rendering.

## 9. Component E — Frontend Banner

### Markup (`frontend/index.html`)

Add inside `<body>` before the existing dashboard / nav:

```html
<div id="subscription-banner" class="sub-banner hidden" role="status" aria-live="polite">
  <span id="subscription-banner-text"></span>
  <a id="subscription-banner-cta" href="#" class="sub-banner-cta"></a>
  <button id="subscription-banner-dismiss" class="sub-banner-dismiss" aria-label="Dismiss">×</button>
</div>
```

### Styles (`frontend/styles.css`)

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

### IIFE (`frontend/app.js`)

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

### Wiring

1. **At app boot.** `bootData()` calls `GET /organization` to populate `state.org`. Immediately after that resolves, call `SubscriptionBanner.update(state.org)`.

2. **On 402 from a write attempt.** Extend the existing `api()` helper's error path: when `data?.detail?.code` starts with `subscription.`, re-fetch `/organization`, update `state.org`, call `SubscriptionBanner.update(state.org)`. The 402 still throws so the calling site can show a localized toast.

### i18n keys (10 new, each locale)

```
sub_banner.trialing
sub_banner.trialing_urgent
sub_banner.trial_expired
sub_banner.renewal_soon
sub_banner.lapsed
sub_banner.cta_subscribe
sub_banner.cta_renew
sub_banner.cta_manage
error.subscription.trial_expired
error.subscription.expired
```

The two `error.*` keys are for inline toasts when a blocked write throws.

## 10. Testing

### Backend tests (`backend/tests/test_subscription_gate.py`, ~20 tests)

**Helper (10):**
- `test_trialing_in_future`
- `test_trialing_expired`
- `test_trialing_exact_boundary`
- `test_trialing_no_trial_ends_at`
- `test_active_in_future`
- `test_active_lapsed`
- `test_active_no_expiry`
- `test_unknown_status`
- `test_handles_string_trial_ends_at`
- `test_handles_datetime_paid_through`

**Dependency (9):**
- `test_write_blocked_when_trial_expired` → 402, code `subscription.trial_expired`
- `test_write_blocked_when_active_lapsed` → 402, code `subscription.expired`
- `test_write_allowed_when_active_no_expiry` (seeded default) → 200
- `test_write_allowed_when_trialing` → 200
- `test_read_allowed_when_expired` → 200
- `test_billing_endpoints_allowed_when_expired` → 200
- `test_auth_endpoints_allowed_when_expired` → 200
- `test_player_endpoint_allowed_when_expired` → 200
- `test_402_response_includes_state_and_expires_at` (response shape)

**`/organization` shape (1):**
- `test_organization_response_includes_derived_fields` — verifies new keys present

### Manual smoke (PR body checklist)

- Sign up fresh org → banner shows "Trial — 5 days left."
- `UPDATE organizations SET trial_ends_at = now() - interval '1 day' WHERE id = …;` → reload → red banner "Trial ended"
- Try to create a playlist → 402 → toast "Subscription required" + banner re-renders
- Click banner CTA → lands on billing page
- Dismiss banner → gone for the rest of the session
- Reload → banner returns
- Simulate lapsed paid: `UPDATE organizations SET subscription_status='active', paid_through_at = now() - interval '1 day' WHERE id = …;` → red banner, same blocking behavior
- Player `/content` endpoint still serves cached playlist (`curl` it)
- Switch to AR locale → all banner strings localized

## 11. File Layout

| File | Change |
|---|---|
| `backend/main.py` | Add `subscription_state` + `_parse_iso` helpers; add `require_active_subscription` dep; extend `GET /organization` response; add the dep to ~35 write endpoints; extend login + signup responses with derived fields |
| `backend/tests/test_subscription_gate.py` | NEW — ~20 tests |
| `frontend/index.html` | Add `<div id="subscription-banner">` markup |
| `frontend/app.js` | Add `SubscriptionBanner` IIFE; wire `update()` into `bootData()` and into the `api()` error path |
| `frontend/styles.css` | Add `.sub-banner` rules + 3 tone variants |
| `frontend/i18n/en.json`, `frontend/i18n/ar.json` | 10 new keys |

## 12. Failure Modes

| Failure | Behavior |
|---|---|
| `trial_ends_at` malformed | `_parse_iso` returns None → treated as `trial_expired` (block writes). Cautious. |
| `paid_through_at` in future but `status='trialing'` (unlikely) | Helper looks at `trial_ends_at` only when status is `trialing`. `paid_through_at` ignored in that branch. |
| Banner state changes between boot and a 402 | The 402 catch in `api()` re-fetches `/organization` and re-renders. |
| User has no org | Existing 403 from `org_id(user)` fires before the subscription dep runs |
| Clock skew (client vs server) | Server is authoritative. Banner `days_remaining` may be off by ±1 day at boundaries but server never blocks writes incorrectly. |
| Default seeded org (`status=active`, `paid_through_at=NULL`) | Treated as no-expiry; banner hidden; writes allowed |
| Unknown `subscription_status` value (e.g., 'failed') | Conservative: allow writes. Banner shows nothing. Admin presumably wants to see what's happening. |

## 13. Migration / Rollout

No schema changes. All needed columns already exist (`subscription_status`, `trial_ends_at`, `paid_through_at`). No data backfill required.

**Behavior change for existing users:**
- Trial users currently in the system who have already passed `trial_ends_at` will immediately see read-only mode after this deploys. Plan to notify them before deploy if any are active.
- The seeded "Default" org and any manually-tagged `paid_through_at=NULL` orgs continue working unchanged.

## 14. Out of Scope (queued for later phase)

- Email reminders at day -3, day 0 of trial; day -7 of renewal (Resend integration)
- Admin "extend trial" / "manually mark as paid" UI
- Self-serve cancellation flow
- Pro-rata refunds
- Hard lockout (block login itself) after N days expired
- Audit-log entries for state transitions (could extend Phase 2.5c)
- Renewal automation (KNET is one-shot per term; re-checkout is the renewal flow today)
- Mid-cycle plan upgrade/downgrade flow

## 15. Next Initiative After This One

Per user's stated sequence: A (land remaining PRs — done) → **B (this PR)** → C (subscription renewal reminders). After this lands, the next phase covers the email-reminder side of trial/renewal, which complements this blocking feature.
