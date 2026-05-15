# Phase 2.5g — Subscription Renewal Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send three reminder emails (trial day -3, trial day 0, renewal day -7) to org admins via existing Resend integration, with race-safe idempotency.

**Architecture:** New `subscription_reminders` table keyed by `(organization_id, reminder_type, expires_at)` enables claim-then-send semantics via `ON CONFLICT DO NOTHING`. An asyncio background task on a 1-hour interval walks all orgs, computes their state from the Phase 2.5f `subscription_state(org)` helper, claims the reminder row, and sends to all admins via the existing `send_via_resend` wrapper. Templates inlined in `backend/main.py` matching the established signup-OTP-email pattern; bilingual EN/AR per `org.locale`.

**Tech Stack:** FastAPI · asyncio · psycopg with `ON CONFLICT DO NOTHING RETURNING id` · Resend HTTP API · pytest with `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-05-15-subscription-reminders-design.md`
**Branch:** `feature/subscription-reminders` (already created from main `3275580`)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(reminders):` or `test(reminders):`.
2. Backend container source is **baked into the image, not volume-mounted**. After changes, rebuild:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Backend tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
4. **Baseline on `main` is 246 passing** (Phase 2.5f shipped).
5. `db.py` uses `?` placeholders translated to `%s` for psycopg. Always use `?`. BUT — for the `ON CONFLICT DO NOTHING RETURNING id` insert in this phase, prefer `query_one` over `execute` because `db.py`'s `execute` auto-injects `RETURNING id` for INSERTs without one, which conflicts with the explicit RETURNING we want. `query_one` does not rewrite.
6. The `signed_up_org` fixture creates a fresh org each test with `subscription_status='trialing'`, `trial_ends_at = now + 5 days`. Use `db.execute` to override these for state-dependent tests.
7. `send_via_resend(api_key, from_addr, to, subject, html, text)` is from `email_utils` and is already imported in `main.py`. Tests `mock.patch("main.send_via_resend")` rather than the source module so they intercept the call as `main.py` sees it.
8. `subscription_state(org)` from Phase 2.5f is the source of truth for state derivation. Don't re-derive locally.
9. Do NOT modify `.env` or rewrite prod URLs.

---

## Task 1: Schema — `subscription_reminders` table

**Files:**
- Modify: `backend/db.py`
- Create: `backend/tests/test_subscription_reminders.py`

**Goal:** Add the new table to `init_db()`. Single introspection test. No application code yet.

- [ ] **Step 1: Write failing schema test**

Create `backend/tests/test_subscription_reminders.py`:

```python
"""Tests for the Phase 2.5g subscription reminder system."""


def test_subscription_reminders_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("subscription_reminders", "reminder_type"),
    )
    assert row is not None
```

- [ ] **Step 2: Verify failure**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py
```
Expected: FAIL — `assert None is not None`.

- [ ] **Step 3: Add the table to `backend/db.py`**

Find `init_db()`. Locate the end of the existing `cursor.execute(...)` block (after the last DDL statement, before the `connect().commit()` or function return). Insert:

```python
        # ── Phase 2.5g: subscription reminders ──────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_reminders (
              id              SERIAL PRIMARY KEY,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              reminder_type   TEXT NOT NULL,
              expires_at      TIMESTAMPTZ NOT NULL,
              sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (organization_id, reminder_type, expires_at)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscription_reminders_org "
            "ON subscription_reminders (organization_id, reminder_type)"
        )
```

Indentation matches surrounding `cursor.execute(...)` blocks (8 spaces).

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose ps | grep backend
```
Expected: `Up (healthy)`.

- [ ] **Step 5: Run schema test**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py
```
Expected: 1 PASS.

- [ ] **Step 6: Full suite — no regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `247 passed` (246 baseline + 1 new).

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_subscription_reminders.py
git commit -m "$(cat <<'EOF'
feat(reminders): subscription_reminders table + index

UNIQUE (organization_id, reminder_type, expires_at) enables race-safe
claim-then-send idempotency via ON CONFLICT DO NOTHING. expires_at
key handles renewal cycles automatically — each new paid_through_at
generates a fresh claim row.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_claim_reminder` helper + claim tests

**Files:**
- Modify: `backend/main.py` (add helper near `subscription_state`)
- Modify: `backend/tests/test_subscription_reminders.py` (append 4 tests)

**Goal:** Atomic claim function with 4 idempotency tests.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_subscription_reminders.py`:

```python
# ── _claim_reminder ───────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from db import execute, query_one


def _make_test_org(client, suffix="x"):
    """Create a fresh org via signup; return the org id."""
    import uuid
    sfx = uuid.uuid4().hex[:8] + suffix
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Claim {sfx}",
                          "email": f"claim-{sfx}@example.com"})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": f"claim-{sfx}@example.com", "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    assert r.status_code == 200, r.text
    return r.json()["organization"]["id"]


def test_claim_returns_true_on_first_call(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True


def test_claim_returns_false_on_duplicate(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 2, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True
    assert _claim_reminder(org_id, "trial_3day", ts) is False


def test_claim_returns_true_for_different_expires_at(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts1 = datetime(2099, 3, 1, tzinfo=timezone.utc)
    ts2 = datetime(2099, 6, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "renewal_7day", ts1) is True
    # Renewal cycle: new paid_through_at → fresh claim
    assert _claim_reminder(org_id, "renewal_7day", ts2) is True


def test_claim_returns_true_for_different_type_same_expires(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 4, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True
    assert _claim_reminder(org_id, "trial_0day", ts) is True
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py::test_claim_returns_true_on_first_call
```
Expected: `ImportError: cannot import name '_claim_reminder' from 'main'`.

- [ ] **Step 3: Add the helper to `backend/main.py`**

Find the Phase 2.5f `subscription_state` function (search `def subscription_state`). Immediately AFTER its full body, insert:

```python
# ── Subscription reminders (Phase 2.5g) ───────────────────────────────


def _claim_reminder(org_id: int, reminder_type: str, expires_at: datetime) -> bool:
    """Try to claim the right to send this reminder. Returns True iff newly claimed.

    Race-safe across replicas via UNIQUE(org, type, expires_at) + ON CONFLICT.
    A `True` return means "this caller got the row in; you may send." A `False`
    means "someone else already sent this; skip."
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

Verify `datetime` is imported at top of `main.py` (it is — Phase 2.5f added the import).

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run claim tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py
```
Expected: 5 PASS (1 schema + 4 claim).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `251 passed` (246 + 5 new).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_reminders.py
git commit -m "$(cat <<'EOF'
feat(reminders): _claim_reminder atomic test-and-set helper

INSERT ... ON CONFLICT DO NOTHING RETURNING id atomically claims
the right to send a reminder. Returns True iff this caller won
the race. Uses query_one (not execute) because db.py's execute
auto-rewrites bare INSERTs to add RETURNING id — we want explicit
control here.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Email templates + dispatcher

**Files:**
- Modify: `backend/main.py` (add template functions near `_claim_reminder`)
- Modify: `backend/tests/test_subscription_reminders.py` (append 6 tests)

**Goal:** Three template functions returning `(subject, html, text)` tuples + dispatcher + 6 pure tests.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_subscription_reminders.py`:

```python
# ── Email templates ───────────────────────────────────────────────────
import pytest


def _fake_org(locale="en", name="Test Cafe"):
    return {"id": 1, "name": name, "locale": locale}


def test_trial_3day_en_subject():
    from main import _reminder_template
    subject, _html, _text = _reminder_template("trial_3day", _fake_org("en"), "en")
    assert "3 days" in subject.lower() or "trial" in subject.lower()


def test_trial_3day_ar_subject():
    from main import _reminder_template
    subject, _html, _text = _reminder_template("trial_3day", _fake_org("ar"), "ar")
    # Arabic subject must contain a relevant character (one of the words used
    # in the template). We use the Arabic word "تجربة" (trial).
    assert "تجربة" in subject or "أيام" in subject


def test_trial_0day_en_uses_past_tense():
    from main import _reminder_template
    subject, _html, text = _reminder_template("trial_0day", _fake_org("en"), "en")
    # Past-tense phrasing — "ended" or "has ended"
    assert "ended" in subject.lower() or "ended" in text.lower()


def test_renewal_7day_en_includes_billing_link():
    from main import _reminder_template
    _subject, html, text = _reminder_template("renewal_7day", _fake_org("en"), "en")
    # CTA link present in both html and text
    assert "billing" in html or "billing" in text


def test_renewal_7day_ar_includes_billing_link():
    from main import _reminder_template
    _subject, html, text = _reminder_template("renewal_7day", _fake_org("ar"), "ar")
    assert "billing" in html or "billing" in text


def test_unknown_reminder_type_raises():
    from main import _reminder_template
    with pytest.raises(ValueError):
        _reminder_template("not_a_type", _fake_org("en"), "en")
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py -k "template or 3day_en or 3day_ar or 0day or 7day or unknown"
```
Expected: `ImportError: cannot import name '_reminder_template' from 'main'`.

- [ ] **Step 3: Add template functions to `backend/main.py`**

Immediately AFTER `_claim_reminder` (added in Task 2), insert:

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


def _tpl_trial_3day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "تنتهي تجربتك خلال ٣ أيام"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"تنتهي تجربة Khanshoof المجانية خلال ٣ أيام. "
            f"للاستمرار في تعديل المحتوى، اشترك من هنا:\n{cta}\n\n"
            f"الشاشات ستستمر في تشغيل المحتوى المخزّن لديها.\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>تنتهي تجربة Khanshoof المجانية خلال ٣ أيام. "
            f"للاستمرار في تعديل المحتوى، <a href=\"{cta}\">اشترك من هنا</a>.</p>"
            f"<p>الشاشات ستستمر في تشغيل المحتوى المخزّن لديها.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your trial ends in 3 days"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof trial ends in 3 days. "
            f"To keep editing content past then, subscribe here:\n{cta}\n\n"
            f"Your screens will keep playing their current content.\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof trial ends in 3 days. "
            f"To keep editing content past then, <a href=\"{cta}\">subscribe here</a>.</p>"
            f"<p>Your screens will keep playing their current content.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text


def _tpl_trial_0day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "انتهت تجربتك"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"انتهت تجربة Khanshoof المجانية. "
            f"لمتابعة إجراء التغييرات على المحتوى، اشترك من هنا:\n{cta}\n\n"
            f"الشاشات ستستمر في تشغيل المحتوى الحالي بلا انقطاع.\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>انتهت تجربة Khanshoof المجانية. "
            f"لمتابعة إجراء التغييرات على المحتوى، <a href=\"{cta}\">اشترك من هنا</a>.</p>"
            f"<p>الشاشات ستستمر في تشغيل المحتوى الحالي بلا انقطاع.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your trial has ended"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof trial has ended. "
            f"To resume making changes to your content, subscribe here:\n{cta}\n\n"
            f"Your screens are still playing their current content with no interruption.\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof trial has ended. "
            f"To resume making changes to your content, <a href=\"{cta}\">subscribe here</a>.</p>"
            f"<p>Your screens are still playing their current content with no interruption.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text


def _tpl_renewal_7day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "يجدّد اشتراكك خلال ٧ أيام"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"اشتراك Khanshoof الحالي ينتهي خلال ٧ أيام. "
            f"للتجديد قبل أن تفقد القدرة على تعديل المحتوى، اضغط هنا:\n{cta}\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>اشتراك Khanshoof الحالي ينتهي خلال ٧ أيام. "
            f"للتجديد قبل أن تفقد القدرة على تعديل المحتوى، <a href=\"{cta}\">اضغط هنا</a>.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your subscription renews in 7 days"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof subscription ends in 7 days. "
            f"To renew before losing the ability to edit content, visit:\n{cta}\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof subscription ends in 7 days. "
            f"To renew before losing the ability to edit content, <a href=\"{cta}\">visit your billing page</a>.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text
```

Verify `os` is imported at top of `main.py` (it is — used for `os.getenv` elsewhere).

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run template tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py
```
Expected: 11 PASS (1 schema + 4 claim + 6 templates).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `257 passed` (251 + 6 new).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_reminders.py
git commit -m "$(cat <<'EOF'
feat(reminders): bilingual email templates for 3 reminder types

trial_3day, trial_0day, renewal_7day each rendered in EN + AR (MSA).
Inline in main.py mirroring _signup_otp_email_html convention.
CTA points at $APP_URL/?section=billing.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Sender + decision + tick-once

**Files:**
- Modify: `backend/main.py` (add `_send_reminder`, `_maybe_send_reminders_for_org`, `_reminder_check_once`, constants)
- Modify: `backend/tests/test_subscription_reminders.py` (append 10 tests)

**Goal:** Build the orchestration layer end-to-end (minus the asyncio wrapper, which lands in Task 5). The pure-Python `_reminder_check_once()` becomes the testable entrypoint.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_subscription_reminders.py`:

```python
# ── Decision logic + sender ───────────────────────────────────────────
from unittest.mock import patch


def _expire_trial_to(org_id: int, days: int):
    """Set trial_ends_at to now+days (negative for past)."""
    delta = "" if days == 0 else (f" + interval '{days} days'" if days > 0
                                  else f" - interval '{abs(days)} days'")
    execute(
        "UPDATE organizations "
        f"SET subscription_status = 'trialing', "
        f"    trial_ends_at = (now(){delta})::text, "
        f"    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _set_active_with_paid_through(org_id: int, days: int):
    delta = "" if days == 0 else (f" + interval '{days} days'" if days > 0
                                  else f" - interval '{abs(days)} days'")
    execute(
        "UPDATE organizations "
        f"SET subscription_status = 'active', "
        f"    trial_ends_at = NULL, "
        f"    paid_through_at = now(){delta} "
        "WHERE id = ?",
        (org_id,),
    )


def _set_no_expiry(org_id: int):
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'active', "
        "    trial_ends_at = NULL, "
        "    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _count_claim_rows(org_id: int, reminder_type: str = None) -> int:
    if reminder_type:
        row = query_one(
            "SELECT COUNT(*) AS n FROM subscription_reminders "
            "WHERE organization_id = ? AND reminder_type = ?",
            (org_id, reminder_type),
        )
    else:
        row = query_one(
            "SELECT COUNT(*) AS n FROM subscription_reminders WHERE organization_id = ?",
            (org_id,),
        )
    return int(row["n"]) if row else 0


def test_no_reminder_when_trial_has_lots_of_days(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="lots")
    _expire_trial_to(org_id, 10)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    assert _count_claim_rows(org_id) == 0


def test_trial_3day_sends_when_days_le_3(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="3d")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "trial_3day") == 1


def test_trial_3day_does_not_resend_same_window(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="re")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
        first_calls = mock_send.call_count
        _reminder_check_once()
    # Second call must NOT generate additional sends
    assert mock_send.call_count == first_calls
    assert _count_claim_rows(org_id, "trial_3day") == 1


def test_trial_0day_sends_when_state_is_trial_expired(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="0d")
    _expire_trial_to(org_id, -1)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "trial_0day") == 1


def test_renewal_7day_sends_when_active_le_7(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="7d")
    _set_active_with_paid_through(org_id, 5)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "renewal_7day") == 1


def test_renewal_7day_resends_after_new_paid_through_at(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="rew")
    _set_active_with_paid_through(org_id, 5)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
        first_calls = mock_send.call_count
        # Simulate renewal: paid_through_at moves to a new future time
        _set_active_with_paid_through(org_id, 365)
        # Then 7 days before that NEW expiry
        _set_active_with_paid_through(org_id, 5)  # different timestamp again
        _reminder_check_once()
    # The renewal-7day reminder for the NEW expires_at should fire (different
    # row in the table). We verify by checking total claim count for this org.
    assert _count_claim_rows(org_id, "renewal_7day") >= 2
    assert mock_send.call_count > first_calls


def test_no_reminder_when_no_admins(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="noadmin")
    _expire_trial_to(org_id, 2)
    # Remove all admins from this org (the signup creates one)
    execute("UPDATE users SET role = 'viewer', is_admin = 0 WHERE organization_id = ?",
            (org_id,))
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    # No-admins path: send returns 0 BUT the claim row is already inserted
    # by _maybe_send_reminders_for_org. (claim-then-send semantics.) The
    # spec accepts this as the documented tradeoff. Don't assert on count.


def test_no_reminder_when_no_expiry(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="noexp")
    _set_no_expiry(org_id)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    assert _count_claim_rows(org_id) == 0


def test_skipped_when_resend_key_missing(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    org_id = _make_test_org(client, suffix="nokey")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    # No claim row either — we want a retry on the next tick when key is set
    assert _count_claim_rows(org_id) == 0


def test_send_to_all_admins(client, monkeypatch):
    from main import _reminder_check_once
    from main import hash_password
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="multi")
    _expire_trial_to(org_id, 2)
    # Add a second admin user to the org
    import uuid
    second_email = f"second-admin-{uuid.uuid4().hex[:8]}@example.com"
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, now())",
        (org_id, second_email, hash_password("Khanshoof2026Test"), 1, "admin"),
    )
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    # Should have called send_via_resend at least twice (one per admin)
    assert mock_send.call_count >= 2
```

- [ ] **Step 2: Run them — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py::test_trial_3day_sends_when_days_le_3
```
Expected: `ImportError: cannot import name '_reminder_check_once' from 'main'`.

- [ ] **Step 3: Add constants + sender + decision + check-once to `backend/main.py`**

Immediately AFTER the last template function (`_tpl_renewal_7day`), insert:

```python
REMINDER_TICK_SECONDS    = 3600   # 1 hour
TRIAL_3DAY_THRESHOLD     = 3
RENEWAL_7DAY_THRESHOLD   = 7


def _send_reminder(org: dict, reminder_type: str) -> int:
    """Send `reminder_type` email to all admins of `org`. Returns 1 if any
    send succeeded, 0 otherwise.

    When RESEND_API_KEY is missing, returns 0 WITHOUT inserting a claim row.
    Callers that have already claimed must handle the case where this returns 0
    after a successful claim — they keep the claim row (documented tradeoff:
    all-admins-bounced means no retry). Only the no-key path is special.
    """
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


def _maybe_send_reminders_for_org(org: dict) -> int:
    """Send any applicable reminder for this org. Returns count sent (0 or 1).

    Pre-check: when RESEND_API_KEY is missing, skip the claim too — so a
    retry on the next tick (with the key set) will succeed.
    """
    if not os.getenv("RESEND_API_KEY", "").strip():
        # No-key path: don't claim, don't send. Retry next tick.
        return 0

    state = subscription_state(org)
    days = state.get("days_remaining")
    expires_at_iso = state.get("expires_at")
    if expires_at_iso is None:
        return 0   # No-expiry orgs never get reminders
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

    return 0


def _reminder_check_once() -> int:
    """One pass through all orgs. Returns count of reminders sent.
    Pure-Python; testable without the asyncio wrapper."""
    orgs = query_all(
        "SELECT id, name, locale, subscription_status, trial_ends_at, paid_through_at "
        "FROM organizations"
    )
    sent = 0
    for org in orgs:
        try:
            sent += _maybe_send_reminders_for_org(org)
        except Exception as exc:
            logger.warning("reminder_check_org_failed org=%s err=%s",
                           org.get("id"), exc)
    return sent
```

Verify `send_via_resend` is imported in `main.py` (line 28: `from email_utils import is_valid_email, send_via_resend`). Confirmed.

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run the new tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py
```
Expected: 21 PASS (1 schema + 4 claim + 6 templates + 10 decision/sender).

- [ ] **Step 6: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `267 passed` (257 + 10 new).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_reminders.py
git commit -m "$(cat <<'EOF'
feat(reminders): send/decide/check-once orchestration

_send_reminder sends to all org admins via Resend; per-recipient
failures logged but don't break the batch. _maybe_send_reminders_for_org
derives state via subscription_state(), claims the reminder row, then
delegates to _send_reminder. _reminder_check_once walks all orgs and
swallows per-org exceptions.

Skipped-when-no-key path doesn't claim — so retry on next tick with
key set is intentional. Other failure modes (all-bounce, no-admins)
keep the claim row per spec tradeoff.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Background tick loop + startup wire

**Files:**
- Modify: `backend/main.py` (add `_reminder_check_loop` + startup event)
- Modify: `backend/tests/test_subscription_reminders.py` (append 1 startup-existence test)

**Goal:** Wrap `_reminder_check_once` in a long-running asyncio task started at app boot. One sanity test that the startup event registers the task. Manual smoke validates the loop actually fires in deployment.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_subscription_reminders.py`:

```python
# ── Startup wire ──────────────────────────────────────────────────────


def test_reminder_check_loop_is_defined():
    """Existence test — the async loop function must be importable."""
    from main import _reminder_check_loop
    import inspect
    assert inspect.iscoroutinefunction(_reminder_check_loop)
```

- [ ] **Step 2: Run it — confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py::test_reminder_check_loop_is_defined
```
Expected: `ImportError: cannot import name '_reminder_check_loop' from 'main'`.

- [ ] **Step 3: Add the loop + startup hook to `backend/main.py`**

Immediately AFTER `_reminder_check_once` (added in Task 4), insert:

```python
async def _reminder_check_loop():
    """Background task: every REMINDER_TICK_SECONDS, walk all orgs and
    send reminders that haven't been sent yet. Errors swallowed; never
    crashes the app."""
    while True:
        await asyncio.sleep(REMINDER_TICK_SECONDS)
        try:
            _reminder_check_once()
        except Exception as exc:
            logger.warning("reminder_check_loop_failed: %s", exc)


@app.on_event("startup")
async def _start_reminder_loop():
    asyncio.create_task(_reminder_check_loop())
```

Verify `asyncio` is imported at top of `main.py` (it is — used elsewhere).

**Important — placement of the `@app.on_event("startup")` decorator:** the app already has a startup event handler around line 755 (the existing one used for DB init or similar). FastAPI supports multiple startup handlers; adding a second is fine.

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run the existence test**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_subscription_reminders.py::test_reminder_check_loop_is_defined
```
Expected: PASS.

- [ ] **Step 6: Verify startup registration via the logs**

```bash
docker-compose logs --tail=40 backend 2>&1 | grep -iE "(start|reminder)"
```
Expected: no errors during startup. The loop task is launched silently — first reminder check happens 3600s after boot.

- [ ] **Step 7: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `268 passed` (267 + 1 new).

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_subscription_reminders.py
git commit -m "$(cat <<'EOF'
feat(reminders): asyncio tick loop + startup wire

_reminder_check_loop sleeps REMINDER_TICK_SECONDS (3600), then calls
the pure _reminder_check_once. Errors caught at loop level so the
task never dies. @app.on_event("startup") registers it via
asyncio.create_task at app boot.

First reminder check fires ~1 hour after deploy. No spam at rollout.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Regression + push + PR

**Files:** none directly modified.

- [ ] **Step 1: Final backend test run**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `268 passed`.

- [ ] **Step 2: i18n parity (no frontend changes but sanity check)**

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

- [ ] **Step 4: Verify backend health**

```bash
docker-compose ps | grep -E "(backend|frontend|postgres)"
curl -s -o /dev/null -w "backend %{http_code}\n" http://localhost:8000/health
```
Expected: all healthy; backend returns 200.

- [ ] **Step 5: Manual production-style smoke (optional but valuable)**

Run the check-once function directly against a real DB to validate the end-to-end pipeline against Resend's sandbox. From inside the backend container:

```bash
docker-compose exec -T -e RESEND_API_KEY=<your-sandbox-key> -e RESEND_FROM='Khanshoof <noreply@khanshoof.com>' \
  backend python -c "
from main import _reminder_check_once
sent = _reminder_check_once()
print(f'Reminders sent: {sent}')
"
```

(Skip this step if a sandbox key isn't available; the test suite covers the contract.)

- [ ] **Step 6: Push branch**

```bash
git push -u origin feature/subscription-reminders
```

- [ ] **Step 7: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(reminders): Phase 2.5g — trial + renewal email reminders" \
  --body "$(cat <<'EOF'
## Summary
- New `subscription_reminders` table keyed by `(organization_id, reminder_type, expires_at)`. Claim-then-send via `ON CONFLICT DO NOTHING RETURNING id` makes the pipeline race-safe across replicas.
- Asyncio background task (1-hour tick) walks all orgs, computes state via Phase 2.5f's `subscription_state()`, and sends three reminder types:
  - `trial_3day`: `state=trialing AND days_remaining <= 3`
  - `trial_0day`: `state=trial_expired`
  - `renewal_7day`: `state=active AND days_remaining <= 7`
- Email goes to all admins (`role='admin' AND is_admin=1`). Per-recipient failures logged but don't break the batch.
- Bilingual EN/AR templates inlined matching the existing signup-OTP-email convention. CTA points at `$APP_URL/?section=billing`.
- Missing `RESEND_API_KEY` is the only no-claim path — every other failure mode (all-bounce, no-admins, transient send error) keeps the claim row per the spec's documented tradeoff.

## Spec
`docs/superpowers/specs/2026-05-15-subscription-reminders-design.md`

## Plan
`docs/superpowers/plans/2026-05-15-subscription-reminders-plan.md`

## Test Plan
- [x] Backend: 268 passed (246 baseline + 22 new)
- [x] `scripts/check_i18n.py` parity OK
- [x] All four JS files parse
- [x] Container healthy
- [ ] Manual smoke: trigger `_reminder_check_once()` against a sandbox Resend key and verify three test emails land
- [ ] Verify Arabic rendering in a real email client (Gmail web)

## Migration notes
- No backfill. First tick fires ~1 hour after deploy.
- Existing trial-expired orgs will get a `trial_0day` email on first tick. If unwanted, pre-populate `subscription_reminders` rows before deploy (see spec §14).

## Non-goals (queued)
- Re-send / manual trigger from admin UI
- Resend webhook → bounce / complaint tracking
- Per-user opt-out
- Renewal day-0 (lapsed) email — covered by in-app banner from Phase 2.5f

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Save memory**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_subscription_reminders.md`:

```markdown
---
name: Subscription renewal reminders (Phase 2.5g) — branch
description: Three reminders (trial-3, trial-0, renewal-7) via Resend; asyncio 1-hour tick; claim-then-send idempotency in new subscription_reminders table. PR pending.
type: project
---

**Status (2026-05-15):** PR #<TBD> opened against main. Awaiting merge.

**What landed:**
- `subscription_reminders` table — UNIQUE (organization_id, reminder_type, expires_at). Claim row keyed by expires_at so renewal cycles auto-reset without billing-callback cleanup.
- `_claim_reminder(org_id, type, expires_at) -> bool` — atomic test-and-set via `ON CONFLICT DO NOTHING RETURNING id`. Uses `query_one` (not `execute`) because db.py's execute auto-rewrites bare INSERTs.
- Three template functions returning (subject, html, text) tuples, EN + AR (MSA). Inlined per `_signup_otp_email_html` convention.
- `_send_reminder(org, type)` — queries all admins, sends per-recipient via existing `send_via_resend`. Per-recipient errors logged but don't break the batch.
- `_maybe_send_reminders_for_org` decision: trialing+days≤3, trial_expired, active+days≤7. Skip-when-no-RESEND_API_KEY is the only path that doesn't claim.
- `_reminder_check_once` walks all orgs, swallows per-org exceptions.
- `_reminder_check_loop` asyncio task on REMINDER_TICK_SECONDS=3600. Started by `@app.on_event("startup")` hook.

**Test count:** 268 backend tests passing (246 pre-branch + 22 new).

**Plan:** `docs/superpowers/plans/2026-05-15-subscription-reminders-plan.md` — 6 tasks.
**Spec:** `docs/superpowers/specs/2026-05-15-subscription-reminders-design.md`.

**Why claim-then-send (not send-then-claim):** race-safe across replicas. Tradeoff documented in spec §6 — an all-admins-bounce silences the reminder permanently. In-app banner from Phase 2.5f covers the case in v1.

**No frontend changes.** Reminder system is entirely backend.

**Sequence completed:** Arabic [DONE], Security [DONE], Offline [DONE], Dayparting [DONE], Trial expiry [DONE], Subscription reminders [this PR]. Original roadmap exhausted.

**Out of scope (queued):**
- Manual re-send / "test reminder" admin UI
- Resend webhook → bounce / complaint tracking
- Per-user opt-out
- Renewal day-0 (lapsed) email — covered by in-app banner
- Reminder history visible in admin UI
- Trial day -1 or -2 micro-nudges
- Email-preview UI in admin
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
| §5 schema | Task 1 |
| §6 claim helper | Task 2 |
| §7 tick loop | Task 5 |
| §8 decision logic | Task 4 |
| §9 sender | Task 4 |
| §10 templates | Task 3 |
| §11 testing | Tasks 1-5 (each task adds its slice of tests) |
| §12 file layout | All paths match |
| §13 failure modes | Tested in Task 4 (`test_skipped_when_resend_key_missing`, `test_no_reminder_when_no_admins`, etc.); loop-level catch in Task 5 |
| §14 migration | Documented in PR body |

No placeholders. Symbol names + constants consistent across tasks (`REMINDER_TICK_SECONDS`, `TRIAL_3DAY_THRESHOLD`, `RENEWAL_7DAY_THRESHOLD`, `_claim_reminder`, `_reminder_template`, `_send_reminder`, `_maybe_send_reminders_for_org`, `_reminder_check_once`, `_reminder_check_loop`).

Task ordering: 1 (schema) → 2 (claim, depends on table) → 3 (templates, independent) → 4 (orchestration, depends on 2+3) → 5 (tick loop, depends on 4) → 6 (regression + PR).

Each commit leaves the tree green with all tests passing — no inter-task fix-ups required.
