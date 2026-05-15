# Phase 2.5g — Subscription Renewal Reminders — Design

**Date:** 2026-05-15
**Branch:** `feature/subscription-reminders` (fresh, branched from `main` at `3275580`)
**Predecessor merged:** Phase 2.5f trial expiry enforcement (PR #8 squash `3275580`).

---

## 1. Goal

Org admins get a heads-up email before their access changes — three reminders, all bilingual, all idempotent. Complements Phase 2.5f's in-app banner + 402 blocking by giving advance warning *before* writes get blocked.

## 2. Existing State

- `email_utils.send_via_resend(api_key, from_addr, to, subject, html, text) -> str` exists. Pure HTTP wrapper around Resend's `/emails` endpoint. Used today by signup OTP.
- `RESEND_API_KEY` + `RESEND_FROM` (default `"Khanshoof <noreply@khanshoof.com>"`) env vars established.
- Existing inline-template pattern (`_signup_otp_email_html`, `_signup_otp_email_text`) — same shape we'll follow.
- No scheduler / cron infrastructure. Only asyncio task today is the per-wall tick in `backend/walls.py` (lazy, per-wall).
- `subscription_state(org)` helper from Phase 2.5f returns `{state, can_write, days_remaining, expires_at}` — we re-use this to drive the decision.

## 3. Design Choices (recap from brainstorm)

1. **Reminder set:** trial day -3, trial day 0 (expired), renewal day -7. No lapsed (day-0 renewal) reminder in v1 — the in-app banner already covers it.
2. **Scheduling:** asyncio background task started at app `startup`. Sleeps 3600 s, then walks orgs once.
3. **Recipients:** all users with `role='admin'` AND `is_admin=1` in that org.
4. **Idempotency:** new `subscription_reminders` table keyed by `(organization_id, reminder_type, expires_at)`. Claim-then-send.
5. **Content:** short heads-up (3-4 sentences) with a single CTA link. EN/AR per `org.locale`.

## 4. Non-Goals (deferred)

- Re-send / manual trigger from admin UI
- Resend webhook → bounce / complaint tracking
- Per-user opt-out from reminders
- HTML email preview UI in admin
- Renewal day-0 (lapsed) email — explicitly skipped per Q1
- Reminder-tracking visible in admin ("trial-3 sent 2026-05-12")
- Trial day -1 or -2 micro-nudges
- Per-language A/B-test variants

## 5. Component A — Schema

```sql
CREATE TABLE IF NOT EXISTS subscription_reminders (
  id              SERIAL PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  reminder_type   TEXT NOT NULL,          -- 'trial_3day' | 'trial_0day' | 'renewal_7day'
  expires_at      TIMESTAMPTZ NOT NULL,   -- the expiry this reminder is "about"
  sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, reminder_type, expires_at)
);
CREATE INDEX IF NOT EXISTS idx_subscription_reminders_org
  ON subscription_reminders (organization_id, reminder_type);
```

`expires_at` is the value being expired at the time of the reminder:
- For `trial_3day` and `trial_0day`: the org's `trial_ends_at`
- For `renewal_7day`: the org's `paid_through_at`

The unique key `(organization_id, reminder_type, expires_at)` means:
- A second send of the same reminder for the same expiry is impossible.
- A renewal cycle (new `paid_through_at` after a payment) creates a fresh row — no special cleanup step needed in the billing callback.

## 6. Component B — Idempotency: claim-then-send

```python
def _claim_reminder(org_id: int, reminder_type: str, expires_at: datetime) -> bool:
    """Try to claim the right to send this reminder. Returns True iff newly claimed.

    Uses postgres ON CONFLICT DO NOTHING + RETURNING to atomically test-and-set.
    Race-safe across replicas.
    """
    row = query_one(
        """
        INSERT INTO subscription_reminders (organization_id, reminder_type, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT (organization_id, reminder_type, expires_at) DO NOTHING
        RETURNING id
        """,
        (org_id, reminder_type, expires_at),
    )
    return row is not None
```

A `True` return means "this replica got the row in; you may send the email." A `False` means "someone else already sent this, skip."

**Tradeoff captured explicitly:** claim-then-send means an all-admins email failure permanently silences that reminder for that expiry. The in-app banner from Phase 2.5f covers the case. If outages become a real problem in production, switch to "claim row only after at least one send succeeded."

## 7. Component C — Tick Loop

```python
REMINDER_TICK_SECONDS    = 3600   # 1 hour
TRIAL_3DAY_THRESHOLD     = 3
RENEWAL_7DAY_THRESHOLD   = 7


async def _reminder_check_loop():
    """Background task: every hour, walk orgs and send reminders that
    haven't been sent yet. Errors swallowed; never crashes the app."""
    while True:
        await asyncio.sleep(REMINDER_TICK_SECONDS)
        try:
            _reminder_check_once()
        except Exception as exc:
            logger.warning("reminder_check_failed: %s", exc)


def _reminder_check_once() -> int:
    """One pass through all orgs. Returns count of reminders sent.
    Pure-Python; testable without the asyncio wrapper."""
    orgs = query_all(
        "SELECT id, name, locale, subscription_status, trial_ends_at, paid_through_at "
        "FROM organizations"
    )
    sent = 0
    for org in orgs:
        sent += _maybe_send_reminders_for_org(org)
    return sent


@app.on_event("startup")
async def _start_reminder_loop():
    asyncio.create_task(_reminder_check_loop())
```

## 8. Component D — Decision Logic

```python
def _maybe_send_reminders_for_org(org: dict) -> int:
    """Send any applicable reminder for this org. Returns count sent (0 or 1)."""
    state = subscription_state(org)
    days = state.get("days_remaining")
    expires_at_iso = state.get("expires_at")
    if expires_at_iso is None:
        return 0   # No-expiry orgs (seeded default) never get reminders
    expires_at = _parse_iso(expires_at_iso)

    if state["state"] == "trialing":
        if days is not None and days <= TRIAL_3DAY_THRESHOLD:
            if _claim_reminder(org["id"], "trial_3day", expires_at):
                return _send_reminder(org, "trial_3day")
        return 0

    if state["state"] == "trial_expired":
        if _claim_reminder(org["id"], "trial_0day", expires_at):
            return _send_reminder(org, "trial_0day")
        return 0

    if state["state"] == "active":
        if days is not None and days <= RENEWAL_7DAY_THRESHOLD:
            if _claim_reminder(org["id"], "renewal_7day", expires_at):
                return _send_reminder(org, "renewal_7day")
        return 0

    # lapsed → no email (out of scope per spec §3)
    return 0
```

**Why `days <= threshold` (not `== threshold`):**
- The tick runs hourly, not at a precise time-of-day. A user with `days_remaining == 1` wouldn't hit `== 3` exactly.
- `<= threshold` catches them on first run inside the window; the claim table prevents repeats.

**Why `trial_0day` fires on `state == "trial_expired"` (not `days == 0`):**
- The state is unambiguous (trial_ends_at has passed).
- Avoids edge cases at the precise expiry boundary.

## 9. Component E — Sender

```python
def _send_reminder(org: dict, reminder_type: str) -> int:
    """Send `reminder_type` email to all admins of `org`. Returns 1 if any
    send succeeded, 0 otherwise."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.info("reminder_skipped_no_resend_key org=%s type=%s",
                    org.get("id"), reminder_type)
        return 0

    admins = query_all(
        "SELECT username FROM users WHERE organization_id = ? "
        "AND role = 'admin' AND is_admin = 1",
        (org["id"],),
    )
    if not admins:
        logger.warning("reminder_no_admins org=%s type=%s",
                       org.get("id"), reminder_type)
        return 0

    locale = (org.get("locale") or "en").lower()
    subject, html, text = _reminder_template(reminder_type, org, locale)
    from_addr = os.getenv("RESEND_FROM", "Khanshoof <noreply@khanshoof.com>")

    any_ok = False
    for admin in admins:
        to_email = admin["username"]  # username field carries email in this app
        try:
            send_via_resend(
                api_key=api_key, from_addr=from_addr, to=to_email,
                subject=subject, html=html, text=text,
            )
            any_ok = True
        except Exception as exc:
            logger.error("reminder_send_failed org=%s type=%s to=%s err=%s",
                         org.get("id"), reminder_type, to_email, exc)
    return 1 if any_ok else 0
```

**Note on missing API key:** when `RESEND_API_KEY` is absent we skip the send AND skip the claim. That means the reminder will retry on the next tick. This is the only branch where the claim doesn't fire — every other path (including all-admins-bounced) claims the row.

## 10. Component F — Email Templates

All inline in `backend/main.py`, mirroring the existing `_signup_otp_email_html` style.

```python
def _reminder_template(reminder_type: str, org: dict, locale: str) -> tuple[str, str, str]:
    """Return (subject, html, text) for the given reminder + locale."""
    is_ar = locale == "ar"
    biz   = org.get("name") or ("شاشاتك" if is_ar else "your business")
    base  = os.getenv("APP_URL", "https://app.khanshoof.com").rstrip("/")
    cta   = f"{base}/?section=billing"
    if reminder_type == "trial_3day":
        return _tpl_trial_3day(biz, cta, is_ar)
    if reminder_type == "trial_0day":
        return _tpl_trial_0day(biz, cta, is_ar)
    if reminder_type == "renewal_7day":
        return _tpl_renewal_7day(biz, cta, is_ar)
    raise ValueError(f"Unknown reminder_type: {reminder_type}")
```

**Three template functions** — see brainstorm Section 4 for the exact subject/html/text bodies. Summary of copy:

| Type | EN subject | AR subject |
|---|---|---|
| `trial_3day` | "Your trial ends in 3 days" | "تنتهي تجربتك خلال ٣ أيام" |
| `trial_0day` | "Your trial has ended" | "انتهت تجربتك" |
| `renewal_7day` | "Your subscription renews in 7 days" | "يجدّد اشتراكك خلال ٧ أيام" |

Each body: greeting line, the news, a single CTA link to the billing page, a reassurance line that screens keep playing (where applicable). Signed "— The Khanshoof team" / "— فريق Khanshoof".

## 11. Testing

### Backend tests (`backend/tests/test_subscription_reminders.py`, ~21 tests)

**Schema (1):**
- `test_subscription_reminders_table_exists`

**Claim semantics (4):**
- `test_claim_returns_true_on_first_call`
- `test_claim_returns_false_on_duplicate`
- `test_claim_returns_true_for_different_expires_at`
- `test_claim_returns_true_for_different_type_same_expires`

**Template rendering (6, pure):**
- `test_trial_3day_en_subject`
- `test_trial_3day_ar_subject`
- `test_trial_0day_en_uses_past_tense`
- `test_renewal_7day_en_includes_billing_link`
- `test_renewal_7day_ar_includes_billing_link`
- `test_unknown_reminder_type_raises`

**Decision logic (10, mock `send_via_resend`):**
- `test_no_reminder_when_trial_has_lots_of_days`
- `test_trial_3day_sends_when_days_le_3`
- `test_trial_3day_does_not_resend_same_window`
- `test_trial_0day_sends_when_state_is_trial_expired`
- `test_renewal_7day_sends_when_active_le_7`
- `test_renewal_7day_resends_after_new_paid_through_at`
- `test_no_reminder_when_no_admins`
- `test_no_reminder_when_no_expiry`
- `test_skipped_when_resend_key_missing`
- `test_send_to_all_admins`

All hermetic — mock `send_via_resend`, use real DB for the claim table.

### Manual smoke (PR body checklist)

- Manually run `_reminder_check_once()` in a Python shell against a real DB and watch the logs.
- Verify three test emails arrive at the configured Resend sandbox / verified address.
- Verify Arabic rendering in a real email client (Gmail web, Apple Mail) — RTL flow, font fallback.
- Verify the claim table has the expected rows.
- Verify a subsequent `_reminder_check_once()` is a no-op.

## 12. File Layout

| File | Change |
|---|---|
| `backend/db.py` | Add `subscription_reminders` table + index in `init_db()` |
| `backend/main.py` | Add `_claim_reminder`, `_reminder_check_loop`, `_reminder_check_once`, `_maybe_send_reminders_for_org`, `_send_reminder`, `_reminder_template`, `_tpl_trial_3day`, `_tpl_trial_0day`, `_tpl_renewal_7day`, constants, `@app.on_event("startup")` task launcher |
| `backend/tests/test_subscription_reminders.py` | NEW — ~21 tests |

No frontend changes. No new env vars beyond the existing `RESEND_API_KEY`, `RESEND_FROM`, `APP_URL`.

## 13. Failure Modes

| Failure | Behavior |
|---|---|
| `RESEND_API_KEY` missing | Skip send AND skip claim. Retry next tick. Info log. |
| One admin's email send fails | Logged per-recipient; other admins still get email; claim row stays. |
| All admin sends fail | Claim row stays; reminder not retried. Documented tradeoff. |
| Tick task crashes during one org's iteration | `try/except` in `_reminder_check_loop` catches; logs warning; continues next tick. |
| Backend restarts mid-iteration | Whichever orgs got their claim row keep it; remaining orgs picked up next tick. |
| Multi-replica race | DB `UNIQUE` prevents double-claim. Only one replica's INSERT succeeds. |
| Org with no admins | Logged warning; no email; no claim row (a future admin add can still get it). |
| Arabic encoding | Sent via Resend with UTF-8 JSON body. Resend handles bidi mail per RFC 6532. |
| New paid_through_at after renewal | Claim row keyed by (org, type, expires_at); new value → new row → new email allowed. |
| Tick fires before first hour passes (boot timing) | `asyncio.sleep(3600)` runs BEFORE the first iteration. Boot doesn't trigger a send; first send is ~1 hour after deploy. |

## 14. Migration / Rollout

1. `CREATE TABLE IF NOT EXISTS` is idempotent. No backfill.
2. First tick fires ~1 hour after deploy. No spam on rollout — the existing orgs that have already passed thresholds won't get bombarded because the tick runs once per hour and each org gets only one reminder per type per expiry.
3. **Caveat:** existing orgs whose `trial_ends_at` is in the past will get a `trial_0day` email on the first tick. That's correct behavior — they did expire — but they may have already moved on. If unwanted, run a SQL pre-populate before deploy: `INSERT INTO subscription_reminders SELECT nextval(...), id, 'trial_0day', trial_ends_at, now() FROM organizations WHERE subscription_status = 'trialing' AND trial_ends_at < now()`.
4. Same caveat applies to active orgs with `paid_through_at` within 7 days. Pre-populate similarly if needed.
5. No frontend changes; no container rebuild beyond backend.

## 15. Out of Scope (queued)

- Manual re-send / "test reminder" admin UI
- Resend webhook → bounce / complaint tracking
- Per-user opt-out from reminders (e.g., `users.email_opt_out_at`)
- HTML email preview UI in admin
- Renewal day-0 (lapsed) email
- Trial day -1 or -2 micro-nudges
- Reminder history visible in admin
- Translating sender name (currently hardcoded "Khanshoof" in `RESEND_FROM`)
- A/B variants

## 16. Next Initiative After This One

User's stated sequence — Arabic [DONE], Security [DONE], Offline [DONE], Dayparting [DONE], Trial expiry [DONE], Subscription renewal reminders [this PR] — exhausts the original roadmap. Next is open: the user will pick what comes after, likely from gaps surfaced by production smoke or a new product priority.
