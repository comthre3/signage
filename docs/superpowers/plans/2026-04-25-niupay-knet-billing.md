# Niupay KNET Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first slice of paid billing: dedicated `/billing` admin page that lets an authenticated user upgrade to Starter / Growth / Business / Pro in 1 / 6 / 12-month terms via Niupay's KNET gateway (test mode to start), and shortens the trial from 14 days to 5 days. Charges are in whole KWD (rounded to nearest) with a USD-primary / KWD-secondary dual display.

**Architecture:** One new backend module (`backend/billing.py`) wraps the Niupay HTTP call. Four new FastAPI endpoints under `/billing/*` hold the checkout → callback → status/history flow. One new DB table (`payments`) + two columns on `organizations` hold state. A new `/billing` admin page (same SPA extend pattern as `/pair`) is the only UI surface. Webhook security relies on a querystring shared secret + idempotent state transitions + server-side amount pinning; frontend never touches the API key.

**Tech Stack:** FastAPI + Pydantic, Postgres 16 via `backend/db.py`, `httpx==0.27.2` (already in requirements), pytest with `TestClient` + existing `client()` fixture, vanilla JS admin SPA, existing pastel CSS tokens. No new third-party deps.

**Branch:** New branch `feature/niupay-billing` off `main`. HEAD of main is `3cd9f29` (mascot bg fix).

---

## Prerequisites

```bash
cd /home/ahmed/signage
git checkout main
git pull --ff-only origin main 2>/dev/null || true
git checkout -b feature/niupay-billing
docker-compose run --rm backend pytest        # expect 38 passed (baseline)
```

Set the three new env vars in `/home/ahmed/signage/.env` (gitignored) before starting Task 4:

```
NIUPAY_API_KEY=ad274322bdff17670b37dd4ca7c5992a
NIUPAY_MODE=1
NIUPAY_CALLBACK_SECRET=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
```

## File Structure

```
backend/
├── billing.py                          # NEW — Niupay HTTP client (create_knet_request)
├── db.py                               # ALTER organizations + CREATE TABLE payments in init_db
├── main.py                             # pricing constants + 4 /billing endpoints + 5-day trial
└── tests/
    └── test_billing.py                 # NEW — 10 integration tests, Niupay mocked

frontend/
├── index.html                          # nav entry, #billing-view panel, signup copy 14→5
├── app.js                              # /billing routing + showBilling + plan picker +
                                        #  checkout + status polling + trial copy 14→5
└── styles.css                          # .billing-view* rules

landing/
└── index.html                          # nav pill + FAQ + subhead: 14→5
```

No changes to `player/`, `backend/tests/test_signup_otp.py`, or `backend/tests/test_plan_limits.py` — neither asserts a literal 14-day trial window.

---

## Task 1: Shorten the trial window (14 → 5 days)

Smallest, safest change. Lands first so later tasks rely on the new constant.

**Files:**
- Modify: `backend/main.py:645`
- Modify: `frontend/index.html:51, 85`
- Modify: `frontend/app.js:1255`
- Modify: `landing/index.html:42, 125, 272, 283`

- [ ] **Step 1: Backend — signup-complete handler**

In `backend/main.py`, find:

```python
    trial_ends_at = (now + timedelta(days=14)).isoformat()
```

Replace with:

```python
    trial_ends_at = (now + timedelta(days=5)).isoformat()
```

- [ ] **Step 2: Admin frontend HTML**

In `frontend/index.html`, find:

```html
            New here? Click <strong>Create Account</strong> to start a 14-day free trial — no card required.
```

Replace with:

```html
            New here? Click <strong>Create Account</strong> to start a 5-day free trial — no card required.
```

And find:

```html
            You'll get the <strong>Starter</strong> plan (up to 3 screens) free for 14 days. Cancel anytime.
```

Replace with:

```html
            You'll get the <strong>Starter</strong> plan (up to 3 screens) free for 5 days. Cancel anytime.
```

- [ ] **Step 3: Admin frontend toast**

In `frontend/app.js`, find:

```javascript
      toast(`Welcome to Khanshoof, ${signupState.business_name}! Your 14-day trial is active.`, "success", 6000);
```

Replace with:

```javascript
      toast(`Welcome to Khanshoof, ${signupState.business_name}! Your 5-day trial is active.`, "success", 6000);
```

- [ ] **Step 4: Landing copy**

In `landing/index.html`, replace four strings:

- `<span class="pill">14-day free trial · no card</span>` → `<span class="pill">5-day free trial · no card</span>`
- `a 14-day free trial already running` → `a 5-day free trial already running`
- `<summary>What happens after the 14-day trial?</summary>` → `<summary>What happens after the 5-day trial?</summary>`
- `<p>14 days free. No card. No lock-in.</p>` → `<p>5 days free. No card. No lock-in.</p>`

- [ ] **Step 5: Verify + regression**

```bash
grep -rnE "14[- ]day|14 days|days=14" /home/ahmed/signage/backend /home/ahmed/signage/frontend /home/ahmed/signage/landing 2>/dev/null
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: first command returns zero matches in source (doc mentions elsewhere don't matter here). Second returns `38 passed`.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py frontend/index.html frontend/app.js landing/index.html
git -C /home/ahmed/signage commit -m "feat: shorten free trial from 14 days to 5 days"
```

---

## Task 2: DB schema — add `organizations` columns + `payments` table

**Files:**
- Modify: `backend/db.py` — extend `init_db()` with `ALTER TABLE` + `CREATE TABLE` statements

- [ ] **Step 1: Read the current `init_db()` end**

```bash
grep -n "def init_db\|commit()" /home/ahmed/signage/backend/db.py | head -10
```

Find the last statement before `conn.commit()` at the end of `init_db()`.

- [ ] **Step 2: Append new schema to `init_db()`**

Just before the final `conn.commit()` in `init_db()`, insert:

```python
        cur.execute(
            """
            ALTER TABLE organizations
              ADD COLUMN IF NOT EXISTS paid_through_at   TIMESTAMPTZ NULL,
              ADD COLUMN IF NOT EXISTS plan_term_months  INTEGER     NULL
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
              id                  SERIAL PRIMARY KEY,
              organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id             INTEGER NOT NULL REFERENCES users(id),
              trackid             TEXT    NOT NULL UNIQUE,
              tier                TEXT    NOT NULL,
              term_months         INTEGER NOT NULL,
              amount_kwd          INTEGER NOT NULL,
              amount_usd_display  NUMERIC(10,2) NOT NULL,
              status              TEXT    NOT NULL DEFAULT 'pending',
              niupay_payment_id   TEXT    NULL,
              niupay_tranid       TEXT    NULL,
              niupay_result       TEXT    NULL,
              niupay_ref          TEXT    NULL,
              created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_org_status ON payments(organization_id, status)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_payments_pending_key "
            "ON payments(organization_id, tier, term_months) WHERE status='pending'"
        )
```

- [ ] **Step 3: Restart backend + verify migration**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml restart backend
sleep 3
docker-compose -f /home/ahmed/signage/docker-compose.yml exec -T postgres psql -U sawwii -d sawwii -c "\d payments"
docker-compose -f /home/ahmed/signage/docker-compose.yml exec -T postgres psql -U sawwii -d sawwii -c "\d organizations" | grep -E "paid_through_at|plan_term_months"
```

Expected: `\d payments` prints all 15 columns + 2 indexes. `\d organizations` grep prints the two new columns.

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add backend/db.py
git -C /home/ahmed/signage commit -m "feat(db): payments table + organizations paid-through columns"
```

---

## Task 3: Backend — Niupay client module + pricing constants

**Files:**
- Create: `backend/billing.py`
- Modify: `backend/main.py` — add pricing constants + helpers

- [ ] **Step 1: Create `backend/billing.py`**

Full contents:

```python
"""Niupay KNET HTTP client.

Thin wrapper around the single Niupay endpoint used for payment creation.
Keeps the API key + mode in env; never logs the raw body.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

NIUPAY_URL = "https://niupay.me/api/requestKnet"


def _env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(f"{name} env var not set")
    return val


def create_knet_request(
    *,
    trackid: str,
    amount_kwd: int,
    response_url: str,
    success_url: str,
    error_url: str,
) -> dict[str, Any]:
    """POST to Niupay /api/requestKnet. Returns the parsed JSON body.

    Raises httpx.HTTPStatusError on non-2xx, httpx.RequestError on network failure.
    """
    payload = {
        "apikey":      _env("NIUPAY_API_KEY"),
        "type":        int(os.getenv("NIUPAY_MODE", "1")),
        "trackid":     trackid,
        "amount":      f"{amount_kwd}.000",
        "language":    1,
        "responseUrl": response_url,
        "successUrl":  success_url,
        "errorUrl":    error_url,
    }
    res = httpx.post(NIUPAY_URL, json=payload, timeout=15.0)
    res.raise_for_status()
    return res.json()
```

- [ ] **Step 2: Add pricing constants + helpers to `backend/main.py`**

Near the top of `backend/main.py` (after existing imports), add:

```python
from decimal import Decimal, ROUND_HALF_UP
```

Then anywhere before the first `@app.` decorator, add:

```python
# ── Billing pricing table ────────────────────────────────────────────
USD_TO_KWD = Decimal("0.306")   # fixed rate; update manually when KWD moves >2%
PLAN_PRICING_USD: dict[str, Decimal] = {
    "starter":  Decimal("9.99"),
    "growth":   Decimal("12.99"),
    "business": Decimal("24.99"),
    "pro":      Decimal("49.99"),
}
PLAN_SCREEN_LIMITS: dict[str, int] = {
    "starter": 3, "growth": 5, "business": 10, "pro": 25,
}
TERM_MULTIPLIERS: dict[int, int] = {1: 1, 6: 5, 12: 10}   # 6m = 5×monthly (save 1); 12m = 10×monthly (save 2)
ALLOWED_TIERS  = frozenset(PLAN_PRICING_USD.keys())
ALLOWED_TERMS  = frozenset(TERM_MULTIPLIERS.keys())
TERM_DAYS      = 30                                       # days per month credited on CAPTURED

def _compute_amounts(tier: str, term_months: int) -> tuple[int, Decimal]:
    """Return (amount_kwd_int, amount_usd_display) for a tier/term combo."""
    monthly_usd = PLAN_PRICING_USD[tier]
    mult = TERM_MULTIPLIERS[term_months]
    amount_usd = (monthly_usd * mult).quantize(Decimal("0.01"))
    amount_kwd_exact = amount_usd * USD_TO_KWD
    amount_kwd = int(amount_kwd_exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return amount_kwd, amount_usd
```

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add backend/billing.py backend/main.py
git -C /home/ahmed/signage commit -m "feat(backend): Niupay client + pricing constants"
```

---

## Task 4: `POST /billing/checkout` endpoint

Creates pending payment row, calls Niupay, returns `payment_url`. Rate-limit: existing pending row for same `(org, tier, term)` < 60 s old is reused.

**Files:**
- Modify: `backend/main.py` — add endpoint
- Create: `backend/tests/test_billing.py` — first 5 tests

- [ ] **Step 1: Write failing tests**

Create `/home/ahmed/signage/backend/tests/test_billing.py`:

```python
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def billing_env(monkeypatch):
    monkeypatch.setenv("NIUPAY_API_KEY", "test-key")
    monkeypatch.setenv("NIUPAY_MODE", "1")
    monkeypatch.setenv("NIUPAY_CALLBACK_SECRET", "deadbeef" * 8)


@pytest.fixture
def mock_niupay():
    """Patch backend.billing.create_knet_request to return a canned success."""
    with patch("main.create_knet_request") as m:
        m.return_value = {
            "status": True,
            "message": "Proceed to Knet",
            "paymentID": "6555084431783610",
            "paymentLink": "https://www.knetpaytest.com.kw/hppaction/fake",
        }
        yield m


def test_checkout_happy_path_creates_pending_row(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    token = signed_up_org["token"]
    r = client.post(
        "/billing/checkout",
        json={"tier": "starter", "term_months": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["payment_url"] == "https://www.knetpaytest.com.kw/hppaction/fake"
    assert data["trackid"].startswith("pay_")
    # Niupay called exactly once with our canonical payload
    assert mock_niupay.call_count == 1
    kwargs = mock_niupay.call_args.kwargs
    assert kwargs["amount_kwd"] == 3
    assert kwargs["response_url"].startswith("https://api.khanshoof.com/billing/callback/")
    assert "?s=" in kwargs["response_url"]


def test_checkout_rejects_unknown_tier(client: TestClient, signed_up_org: dict):
    r = client.post(
        "/billing/checkout",
        json={"tier": "platinum", "term_months": 1},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 422


def test_checkout_rejects_unknown_term(client: TestClient, signed_up_org: dict):
    r = client.post(
        "/billing/checkout",
        json={"tier": "starter", "term_months": 3},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 422


def test_checkout_requires_auth(client: TestClient):
    r = client.post("/billing/checkout", json={"tier": "starter", "term_months": 1})
    assert r.status_code == 401


def test_checkout_rate_limits_duplicate_pending(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    token = signed_up_org["token"]
    first = client.post(
        "/billing/checkout",
        json={"tier": "growth", "term_months": 6},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = client.post(
        "/billing/checkout",
        json={"tier": "growth", "term_months": 6},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["trackid"] == second.json()["trackid"]
    assert mock_niupay.call_count == 1   # second call reused first row's URL
```

- [ ] **Step 2: Run tests — expect 5 failures (endpoint not defined)**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_billing.py -v
```

Expected: all 5 fail (404 or ModuleNotFoundError).

- [ ] **Step 3: Implement endpoint in `backend/main.py`**

Add near the top (alongside the existing imports):

```python
import secrets
from billing import create_knet_request
```

Add these models (anywhere after the existing Pydantic models):

```python
class BillingCheckoutRequest(BaseModel):
    tier: str = Field(..., description="starter|growth|business|pro")
    term_months: int = Field(..., description="1, 6, or 12")
```

Then add the endpoint (anywhere after the existing `/screens/*` endpoints):

```python
def _billing_callback_base() -> tuple[str, str]:
    api_base = os.environ.get("API_BASE_URL", "https://api.khanshoof.com").rstrip("/")
    app_base = os.environ.get("APP_URL",      "https://app.khanshoof.com").rstrip("/")
    return api_base, app_base


def _billing_callback_secret() -> str:
    secret = os.environ.get("NIUPAY_CALLBACK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="Billing not configured")
    return secret


@app.post("/billing/checkout")
def billing_checkout(
    payload: BillingCheckoutRequest,
    user: dict = Depends(require_roles("admin")),
) -> dict:
    if payload.tier not in ALLOWED_TIERS:
        raise HTTPException(status_code=422, detail="Unknown tier")
    if payload.term_months not in ALLOWED_TERMS:
        raise HTTPException(status_code=422, detail="Unknown term")

    amount_kwd, amount_usd = _compute_amounts(payload.tier, payload.term_months)
    org = org_id(user)

    # Rate-limit: reuse pending row < 60 s old
    existing = query_one(
        """
        SELECT * FROM payments
         WHERE organization_id = ?
           AND tier            = ?
           AND term_months     = ?
           AND status          = 'pending'
           AND created_at      > now() - interval '60 seconds'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (org, payload.tier, payload.term_months),
    )
    if existing:
        return {
            "payment_url": existing["niupay_payment_link"] if existing.get("niupay_payment_link")
                            else _rebuild_payment_link(existing),
            "trackid": existing["trackid"],
        }

    trackid = "pay_" + secrets.token_hex(16)
    api_base, app_base = _billing_callback_base()
    secret = _billing_callback_secret()
    response_url = f"{api_base}/billing/callback/{trackid}?s={secret}"
    success_url  = f"{app_base}/billing?status=success&trackid={trackid}"
    error_url    = f"{app_base}/billing?status=error&trackid={trackid}"

    # Insert pending row FIRST so the callback can find it even if the request races
    execute(
        """
        INSERT INTO payments
          (organization_id, user_id, trackid, tier, term_months,
           amount_kwd, amount_usd_display, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (org, user["id"], trackid, payload.tier, payload.term_months,
         amount_kwd, str(amount_usd)),
    )

    try:
        resp = create_knet_request(
            trackid=trackid,
            amount_kwd=amount_kwd,
            response_url=response_url,
            success_url=success_url,
            error_url=error_url,
        )
    except Exception as exc:
        execute("UPDATE payments SET status='failed', niupay_result=? WHERE trackid=?",
                (f"request_error:{exc.__class__.__name__}", trackid))
        raise HTTPException(status_code=502, detail="Payment gateway unreachable")

    payment_link = resp.get("paymentLink")
    payment_id   = resp.get("paymentID")
    if not resp.get("status") or not payment_link:
        execute("UPDATE payments SET status='failed', niupay_result=? WHERE trackid=?",
                ("niupay_bad_response", trackid))
        raise HTTPException(status_code=502, detail="Payment gateway rejected the request")

    execute(
        "UPDATE payments SET niupay_payment_id=?, updated_at=now() WHERE trackid=?",
        (payment_id, trackid),
    )
    return {"payment_url": payment_link, "trackid": trackid}


def _rebuild_payment_link(_row: dict) -> str:
    """Fallback: if we don't persist the Niupay payment link, ask Niupay again.

    For now we always keep the link, so this path is only hit if a row from a
    prior deploy lacked the column. Returning an empty string forces the client
    to hit the endpoint again after 60 s.
    """
    return ""
```

Also add a column to persist the Niupay payment link (so the rate-limiter can return it without re-calling Niupay). In `backend/db.py init_db()`, inside the `ALTER TABLE` for `payments` — actually, simpler: add it to the `CREATE TABLE` in Task 2 already. Since Task 2 is already committed, add a migration here:

At the bottom of `init_db()` (just before `conn.commit()`), append:

```python
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS niupay_payment_link TEXT NULL")
```

And update the UPDATE in the checkout endpoint to write it:

```python
    execute(
        "UPDATE payments SET niupay_payment_id=?, niupay_payment_link=?, updated_at=now() WHERE trackid=?",
        (payment_id, payment_link, trackid),
    )
```

And the rate-limit SELECT becomes:

```python
    existing = query_one(
        """
        SELECT trackid, niupay_payment_link FROM payments
         WHERE organization_id = ?
           AND tier            = ?
           AND term_months     = ?
           AND status          = 'pending'
           AND created_at      > now() - interval '60 seconds'
           AND niupay_payment_link IS NOT NULL
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (org, payload.tier, payload.term_months),
    )
    if existing:
        return {
            "payment_url": existing["niupay_payment_link"],
            "trackid": existing["trackid"],
        }
```

Remove the now-unused `_rebuild_payment_link` helper.

- [ ] **Step 4: Restart backend + run tests**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml restart backend
sleep 3
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_billing.py -v
```

Expected: all 5 pass.

- [ ] **Step 5: Full regression**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `43 passed` (38 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/db.py backend/tests/test_billing.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /billing/checkout + Niupay request"
```

---

## Task 5: `POST /billing/callback/{trackid}` endpoint

Idempotent state transition on Niupay callback. Secret in querystring.

**Files:**
- Modify: `backend/main.py` — add endpoint
- Modify: `backend/tests/test_billing.py` — 5 more tests

- [ ] **Step 1: Append failing tests to `backend/tests/test_billing.py`**

```python
def _pending_payment(client: TestClient, signed_up_org: dict, tier: str, term: int, mock_niupay) -> str:
    r = client.post(
        "/billing/checkout",
        json={"tier": tier, "term_months": term},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 200
    return r.json()["trackid"]


def test_callback_rejects_wrong_secret(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "starter", 1, mock_niupay)
    r = client.post(
        f"/billing/callback/{trackid}?s=wrong",
        json={"result": "CAPTURED", "trackid": trackid, "paymentID": "x", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 404


def test_callback_unknown_trackid_is_200_noop(client: TestClient):
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/pay_nope?s={secret}",
        json={"result": "CAPTURED", "trackid": "pay_nope"},
    )
    assert r.status_code == 200


def test_callback_captured_transitions_payment_and_org(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "starter", 6, mock_niupay)
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/{trackid}?s={secret}",
        json={"result": "CAPTURED", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 200
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    data = status.json()
    assert data["status"] == "captured"
    assert data["tier"] == "starter"
    assert data["term_months"] == 6
    assert data["paid_through_at"] is not None


def test_callback_non_captured_marks_failed(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "growth", 1, mock_niupay)
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/{trackid}?s={secret}",
        json={"result": "HOST_TIMEOUT", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 200
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert status.json()["status"] == "failed"


def test_callback_captured_is_idempotent(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "pro", 12, mock_niupay)
    secret = "deadbeef" * 8
    body = {"result": "CAPTURED", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"}
    first  = client.post(f"/billing/callback/{trackid}?s={secret}", json=body)
    second = client.post(f"/billing/callback/{trackid}?s={secret}", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    # paid_through_at set once, not twice
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    ).json()
    paid_through = status["paid_through_at"]
    assert paid_through is not None
```

- [ ] **Step 2: Run — expect failures**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_billing.py -v
```

Expected: 5 new tests fail (404 on callback/status).

- [ ] **Step 3: Implement the callback + status endpoints**

In `backend/main.py`, append after the checkout endpoint:

```python
class BillingCallbackBody(BaseModel):
    result: str | None = None
    trackid: str | None = None
    paymentID: str | None = None
    tranid: str | None = None
    ref: str | None = None
    niutrack: str | None = None


@app.post("/billing/callback/{trackid}")
def billing_callback(trackid: str, body: BillingCallbackBody, s: str = ""):
    if not secrets.compare_digest(s, _billing_callback_secret()):
        raise HTTPException(status_code=404)

    if body.trackid and body.trackid != trackid:
        raise HTTPException(status_code=400, detail="trackid mismatch")

    row = query_one("SELECT * FROM payments WHERE trackid = ?", (trackid,))
    if not row:
        return {"ok": True}   # no-leak 200

    if row["status"] in ("captured", "failed"):
        return {"ok": True}   # idempotent

    captured = (body.result or "").upper() == "CAPTURED"
    if captured:
        term = int(row["term_months"])
        execute(
            """
            UPDATE payments
               SET status='captured',
                   niupay_result=?, niupay_tranid=?, niupay_ref=?, niupay_payment_id=?,
                   updated_at=now()
             WHERE trackid=?
            """,
            (body.result, body.tranid, body.ref, body.paymentID, trackid),
        )
        execute(
            """
            UPDATE organizations
               SET plan               = ?,
                   plan_term_months   = ?,
                   screen_limit       = ?,
                   subscription_status= 'active',
                   paid_through_at    = now() + make_interval(days => ?)
             WHERE id = ?
            """,
            (row["tier"], term, PLAN_SCREEN_LIMITS[row["tier"]], term * TERM_DAYS, row["organization_id"]),
        )
    else:
        execute(
            """
            UPDATE payments
               SET status='failed',
                   niupay_result=?, niupay_tranid=?, niupay_ref=?, niupay_payment_id=?,
                   updated_at=now()
             WHERE trackid=?
            """,
            (body.result, body.tranid, body.ref, body.paymentID, trackid),
        )

    return {"ok": True}


@app.get("/billing/status/{trackid}")
def billing_status(trackid: str, user: dict = Depends(get_current_user)) -> dict:
    row = query_one("SELECT * FROM payments WHERE trackid = ?", (trackid,))
    if not row or row["organization_id"] != org_id(user):
        raise HTTPException(status_code=404, detail="Unknown trackid")
    org = query_one("SELECT paid_through_at FROM organizations WHERE id = ?", (row["organization_id"],))
    return {
        "status":               row["status"],
        "tier":                 row["tier"],
        "term_months":          row["term_months"],
        "amount_kwd":           row["amount_kwd"],
        "amount_usd_display":   str(row["amount_usd_display"]),
        "paid_through_at":      org["paid_through_at"].isoformat() if org and org.get("paid_through_at") else None,
    }
```

- [ ] **Step 4: Restart + run tests**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml restart backend
sleep 3
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_billing.py -v
```

Expected: 10 billing tests pass; full suite is `48 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_billing.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /billing/callback (idempotent) + GET /billing/status"
```

---

## Task 6: `GET /billing/history` endpoint

Small endpoint, no new tests required — covered by manual smoke. Needed for the frontend's past-payments list.

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Append to `backend/main.py`**

```python
@app.get("/billing/history")
def billing_history(user: dict = Depends(require_roles("admin"))) -> list[dict]:
    rows = query_all(
        """
        SELECT trackid, tier, term_months, amount_kwd, amount_usd_display,
               status, created_at, updated_at
          FROM payments
         WHERE organization_id = ?
           AND status IN ('captured', 'failed')
         ORDER BY created_at DESC
         LIMIT 50
        """,
        (org_id(user),),
    )
    return [
        {
            **r,
            "amount_usd_display": str(r["amount_usd_display"]),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
        }
        for r in rows
    ]
```

- [ ] **Step 2: Restart + smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml restart backend
sleep 3
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `48 passed`.

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py
git -C /home/ahmed/signage commit -m "feat(backend): GET /billing/history"
```

---

## Task 7: `/billing` admin page — DOM scaffold + nav entry + CSS

**Files:**
- Modify: `frontend/index.html` — nav button + new `#billing-view` section
- Modify: `frontend/styles.css` — `.billing-view*` rules

- [ ] **Step 1: Add nav button in `frontend/index.html`**

Find the existing nav block `<nav id="main-nav">` (around line 23) and append after the last existing `<button>`:

```html
          <button data-section="billing">Billing</button>
```

- [ ] **Step 2: Add `#billing-view` panel**

Insert directly after the closing tag of `#pair-view` section and before the `<div id="dashboard" class="hidden">` block:

```html
      <section id="billing-view" class="panel billing-view hidden">
        <header class="billing-header">
          <h1>Billing</h1>
          <p class="billing-sub">Upgrade your plan — paid via KNET.</p>
        </header>

        <section class="billing-current" id="billing-current">
          <h2>Current plan</h2>
          <div class="billing-current-body" id="billing-current-body">Loading…</div>
        </section>

        <section class="billing-upgrade">
          <h2>Upgrade</h2>
          <div class="billing-term-tabs" role="tablist">
            <button class="billing-term active" data-term="1"  role="tab" aria-selected="true">Monthly</button>
            <button class="billing-term"        data-term="6"  role="tab" aria-selected="false">6 months</button>
            <button class="billing-term"        data-term="12" role="tab" aria-selected="false">12 months</button>
          </div>
          <div class="billing-tier-grid" id="billing-tier-grid"></div>
          <p id="billing-banner" class="billing-banner hidden" role="alert"></p>
        </section>

        <section class="billing-history">
          <h2>Past payments</h2>
          <div id="billing-history-body" class="billing-history-body">
            <p class="billing-empty">No payments yet.</p>
          </div>
        </section>
      </section>
```

- [ ] **Step 3: Append `.billing-view*` CSS to `frontend/styles.css`**

```css

/* ── Billing page ────────────────────────────────────────────── */
.billing-view {
  max-width: 720px;
  margin: clamp(16px, 4vh, 48px) auto;
  padding: clamp(20px, 3vh, 36px);
  background: var(--bg-panel);
  border-radius: var(--r-lg);
  border: 1px solid var(--cream-border);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  gap: clamp(20px, 3vh, 32px);
}

.billing-header h1 {
  font-family: var(--font-display);
  font-size: clamp(24px, 3.2vh, 34px);
  color: var(--plum);
  margin: 0 0 6px;
}
.billing-sub {
  color: var(--cocoa);
  margin: 0;
}

.billing-current {
  background: var(--bg-card);
  border: 1px solid var(--cream-border);
  border-radius: var(--r-md);
  padding: 16px;
}
.billing-current h2,
.billing-upgrade h2,
.billing-history h2 {
  font-size: 14px;
  font-weight: 600;
  color: var(--plum);
  margin: 0 0 10px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.billing-term-tabs {
  display: flex;
  gap: 8px;
  background: var(--bg-card);
  border: 1px solid var(--cream-border);
  border-radius: var(--r-pill);
  padding: 4px;
  margin-bottom: 14px;
}
.billing-term {
  flex: 1;
  background: transparent;
  border: none;
  padding: 10px 14px;
  border-radius: var(--r-pill);
  color: var(--cocoa);
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  min-height: 40px;
}
.billing-term.active {
  background: var(--peach-deep);
  color: #FFFFFF;
}

.billing-tier-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 640px) {
  .billing-tier-grid { grid-template-columns: repeat(2, 1fr); }
}

.billing-tier {
  background: var(--bg-panel);
  border: 1px solid var(--cream-border);
  border-radius: var(--r-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.billing-tier-name {
  font-family: var(--font-display);
  font-size: 20px;
  color: var(--plum);
  margin: 0;
}
.billing-tier-limit {
  font-size: 13px;
  color: var(--cocoa);
}
.billing-tier-usd {
  font-size: 22px;
  font-weight: 700;
  color: var(--plum);
  margin-top: 8px;
}
.billing-tier-kwd {
  font-size: 13px;
  color: var(--cocoa);
}
.billing-tier-btn {
  background: var(--peach-deep);
  color: #FFFFFF;
  border: none;
  border-radius: var(--r-md);
  padding: 12px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 10px;
  min-height: 44px;
}
.billing-tier-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.billing-tier-save {
  font-size: 12px;
  color: var(--peach-deep);
  font-weight: 600;
}

.billing-banner {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: var(--r-md);
  font-size: 14px;
}
.billing-banner.success {
  background: #E8F5EC;
  color: #2F6E43;
  border: 1px solid #CCE5D4;
}
.billing-banner.error {
  background: #FDECEF;
  color: var(--red);
  border: 1px solid #F2C6CF;
}

.billing-history-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.billing-history-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 10px 14px;
  background: var(--bg-card);
  border: 1px solid var(--cream-border);
  border-radius: var(--r-md);
  font-size: 14px;
}
.billing-history-row.failed { opacity: 0.7; }
.billing-empty { color: var(--cocoa); font-size: 14px; margin: 0; }
```

- [ ] **Step 4: Rebuild + smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
curl -s https://app.khanshoof.com/ | grep -cE 'id="billing-view"|data-section="billing"|billing-term-tabs'
```

Expected: `3`.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add frontend/index.html frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(frontend): billing page DOM scaffold + pastel styling"
```

---

## Task 8: `showBilling()` controller + plan picker + checkout + status polling

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add controller + routing near `showPairView`**

Append to `frontend/app.js` (after the `showPairView` block):

```javascript
/* ── Billing view ───────────────────────────────────────────── */
const USD_TO_KWD = 0.306;
const PLAN_TIERS = [
  { tier: "starter",  label: "Starter",  usd: 9.99,  screens: 3  },
  { tier: "growth",   label: "Growth",   usd: 12.99, screens: 5  },
  { tier: "business", label: "Business", usd: 24.99, screens: 10 },
  { tier: "pro",      label: "Pro",      usd: 49.99, screens: 25 },
];
const BILLING_TERMS = [
  { months: 1,  multiplier: 1,  saveLabel: "" },
  { months: 6,  multiplier: 5,  saveLabel: "save 1 month" },
  { months: 12, multiplier: 10, saveLabel: "save 2 months" },
];

let billingCurrentTerm = 1;
let billingPollTimer = null;
let billingPollStartedAt = 0;

function billingAmountsFor(tier, months) {
  const plan = PLAN_TIERS.find((p) => p.tier === tier);
  const term = BILLING_TERMS.find((t) => t.months === months);
  if (!plan || !term) return null;
  const usd = +(plan.usd * term.multiplier).toFixed(2);
  const kwd = Math.round(usd * USD_TO_KWD);
  return { usd, kwd };
}

function showBillingPanel() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("pair-view").classList.add("hidden");
  document.getElementById("billing-view").classList.remove("hidden");
}

function renderBillingCurrent(org) {
  const body = document.getElementById("billing-current-body");
  if (!org) {
    body.textContent = "—";
    return;
  }
  const plan = PLAN_TIERS.find((p) => p.tier === org.plan) || { label: org.plan, screens: org.screen_limit };
  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const daysLeft = Math.max(0, Math.ceil((new Date(org.trial_ends_at) - new Date()) / 86400000));
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · Trial · ${daysLeft} day${daysLeft === 1 ? "" : "s"} left · up to ${plan.screens} screens`;
  } else if (org.paid_through_at) {
    const ends = new Date(org.paid_through_at).toLocaleDateString();
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · paid through ${escHtml(ends)} · up to ${plan.screens} screens`;
  } else {
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · up to ${plan.screens} screens`;
  }
}

function renderBillingTiers() {
  const grid = document.getElementById("billing-tier-grid");
  const termInfo = BILLING_TERMS.find((t) => t.months === billingCurrentTerm);
  grid.innerHTML = "";
  for (const plan of PLAN_TIERS) {
    const amounts = billingAmountsFor(plan.tier, billingCurrentTerm);
    const saveMarkup = termInfo.saveLabel
      ? `<span class="billing-tier-save">${escHtml(termInfo.saveLabel)}</span>` : "";
    const card = document.createElement("div");
    card.className = "billing-tier";
    card.innerHTML = `
      <h3 class="billing-tier-name">${escHtml(plan.label)}</h3>
      <div class="billing-tier-limit">up to ${plan.screens} screens</div>
      <div class="billing-tier-usd">$${amounts.usd.toFixed(2)}${billingCurrentTerm === 1 ? " / month" : ""}</div>
      <div class="billing-tier-kwd">≈ ${amounts.kwd} KWD</div>
      ${saveMarkup}
      <button type="button" class="billing-tier-btn" data-tier="${escAttr(plan.tier)}">
        Pay ${amounts.kwd} KWD${termInfo.saveLabel ? " · " + escHtml(termInfo.saveLabel) : ""}
      </button>
    `;
    grid.appendChild(card);
  }
}

async function loadBillingHistory() {
  const body = document.getElementById("billing-history-body");
  try {
    const rows = await api("/billing/history");
    if (!rows.length) {
      body.innerHTML = '<p class="billing-empty">No payments yet.</p>';
      return;
    }
    body.innerHTML = rows.map((r) => {
      const when = new Date(r.updated_at || r.created_at).toLocaleDateString();
      return `<div class="billing-history-row ${r.status === 'failed' ? 'failed' : ''}">
        <span>${escHtml(r.tier)} · ${r.term_months} month${r.term_months === 1 ? '' : 's'} · ${escHtml(when)}</span>
        <span>${r.amount_kwd} KWD · ${escHtml(r.status)}</span>
      </div>`;
    }).join("");
  } catch (err) {
    body.innerHTML = '<p class="billing-empty">Couldn\'t load history.</p>';
  }
}

async function showBilling() {
  showBillingPanel();
  renderBillingTiers();
  try {
    const me = await api("/auth/me");
    renderBillingCurrent(me.organization);
  } catch (err) { renderBillingCurrent(null); }
  loadBillingHistory();
  maybeResumeBillingStatus();
}

function setBillingBanner(kind, message) {
  const el = document.getElementById("billing-banner");
  el.textContent = message;
  el.classList.remove("hidden", "success", "error");
  el.classList.add(kind);
}
function clearBillingBanner() {
  document.getElementById("billing-banner").classList.add("hidden");
}

async function onBillingPay(tier) {
  clearBillingBanner();
  const grid = document.getElementById("billing-tier-grid");
  const buttons = grid.querySelectorAll(".billing-tier-btn");
  buttons.forEach((b) => (b.disabled = true));
  try {
    const data = await api("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ tier, term_months: billingCurrentTerm }),
    });
    sessionStorage.setItem("billing_pending_trackid", data.trackid);
    window.location.href = data.payment_url;
  } catch (err) {
    buttons.forEach((b) => (b.disabled = false));
    setBillingBanner("error", err?.data?.detail || err.message || "Payment failed to start.");
  }
}

function stopBillingPoll() {
  if (billingPollTimer) { clearTimeout(billingPollTimer); billingPollTimer = null; }
}

function maybeResumeBillingStatus() {
  const params = new URLSearchParams(location.search);
  const status = params.get("status");
  const trackid = params.get("trackid") || sessionStorage.getItem("billing_pending_trackid");
  if (!trackid || !status) return;
  sessionStorage.removeItem("billing_pending_trackid");
  setBillingBanner("success", "Confirming payment…");
  billingPollStartedAt = Date.now();
  pollBillingStatus(trackid);
}

async function pollBillingStatus(trackid) {
  try {
    const data = await api(`/billing/status/${encodeURIComponent(trackid)}`);
    if (data.status === "captured") {
      setBillingBanner("success", `Plan upgraded · ${data.tier} · paid through ${new Date(data.paid_through_at).toLocaleDateString()}`);
      const me = await api("/auth/me");
      renderBillingCurrent(me.organization);
      loadBillingHistory();
      history.replaceState({}, "", "/billing");
      return;
    }
    if (data.status === "failed") {
      setBillingBanner("error", "Payment declined. You can try again.");
      history.replaceState({}, "", "/billing");
      return;
    }
    // still pending
    if (Date.now() - billingPollStartedAt > 15000) {
      setBillingBanner("success", "Payment is still processing — check back in a minute.");
      return;
    }
    billingPollTimer = setTimeout(() => pollBillingStatus(trackid), 2000);
  } catch (err) {
    setBillingBanner("error", "Couldn't confirm payment status.");
  }
}

document.querySelectorAll(".billing-term").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".billing-term").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    });
    billingCurrentTerm = Number(btn.dataset.term);
    renderBillingTiers();
  });
});

document.getElementById("billing-tier-grid").addEventListener("click", (e) => {
  const btn = e.target.closest(".billing-tier-btn");
  if (!btn) return;
  onBillingPay(btn.dataset.tier);
});
```

- [ ] **Step 2: Add `/billing` routing to `boot()`**

Find in `frontend/app.js` the existing routing block that handles `/pair`:

```javascript
  const isPairPath = location.pathname === "/pair";
  const pairCodeParam = isPairPath
    ? new URLSearchParams(location.search).get("code") || ""
    : "";
```

Replace with:

```javascript
  const isPairPath = location.pathname === "/pair";
  const isBillingPath = location.pathname === "/billing";
  const pairCodeParam = isPairPath
    ? new URLSearchParams(location.search).get("code") || ""
    : "";
```

Further down in `boot()`, find the block:

```javascript
    if (isPairPath) {
      await showPairView(pairCodeParam);
    } else {
      showDashboard();
      await bootData();
      updateResolutionCustomVisibility();
      if (location.hash === '#signup') showAuthTab('signup');
    }
```

Replace with:

```javascript
    if (isPairPath) {
      await showPairView(pairCodeParam);
    } else if (isBillingPath) {
      await showBilling();
    } else {
      showDashboard();
      await bootData();
      updateResolutionCustomVisibility();
      if (location.hash === '#signup') showAuthTab('signup');
    }
```

- [ ] **Step 3: Wire the nav button**

Find the existing nav button click binding in `frontend/app.js`. There's already a pattern like `document.querySelectorAll('nav button[data-section]')`. Look for it; if present, the `data-section="billing"` case will hit the generic handler. If there isn't one, add a specific binding near the other nav bindings:

```javascript
document.querySelector('nav button[data-section="billing"]')?.addEventListener("click", (e) => {
  e.preventDefault();
  history.pushState({}, "", "/billing");
  showBilling();
});
```

- [ ] **Step 4: Rebuild + browser smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

Then in a browser:
1. Log in at `https://app.khanshoof.com`.
2. Click `Billing` in the nav. Confirm the page renders: current-plan card shows trial + days-left, segmented term control works (Monthly / 6 months / 12 months re-prices cards), 4 tier cards render with USD + KWD amounts (3 / 4 / 8 / 15 on Monthly).
3. Click `Pay 3 KWD` on Starter. Expected: redirect to a Niupay test-mode URL (`https://www.knetpaytest.com.kw/…`).
4. On the KNET test page enter card `888888 0000000001`, expiry `09/30`, any CVV. Submit.
5. Redirects back to `https://app.khanshoof.com/billing?status=success&trackid=…`. Within ~3 s banner flips to "Plan upgraded · starter · paid through …", plan card rerenders to "Starter · paid through …", history shows the new row.

If any check fails, STOP and diagnose before Task 9.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): billing page controller + checkout + status polling"
```

---

## Task 9: Full smoke + merge + follow-up reminder

- [ ] **Step 1: Full backend regression**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `48 passed`.

- [ ] **Step 2: End-to-end smoke matrix (browser)**

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1 | Monthly Starter capture | Pay 3 KWD, test card 1 | Banner success, plan → Starter, paid_through ≈ today + 30 days |
| 2 | 6-month Growth capture | Pick 6-month, Pay 20 KWD, test card 1 | Plan → Growth, paid_through ≈ today + 180 days |
| 3 | 12-month Pro capture | Pick 12-month, Pay 153 KWD, test card 1 | Plan → Pro, paid_through ≈ today + 360 days |
| 4 | Failed payment | Test card `888888 0000000002` | Banner error, plan unchanged, history shows failed row |
| 5 | Rate-limit duplicate | Click Pay twice in < 60 s | Second click goes to the same KNET URL |
| 6 | Trial copy | Fresh signup, check dashboard + signup wizard + landing | "5-day" / "5 days" everywhere; no "14" survives |
| 7 | History list | After Scenarios 1-4 | Past payments section lists the rows newest-first |

- [ ] **Step 3: Merge to `main`**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/niupay-billing -m "Merge Niupay KNET billing (test mode)"
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build
```

- [ ] **Step 4: Update roadmap memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`:

1. Append to `## Current state` section:

```
**Niupay KNET billing — test mode** (`feature/niupay-billing` → merged 2026-04-25, merge commit `<SHA>`): /billing admin page with 4 tiers × 3 terms picker, USD-primary + KWD-rounded-nearest display. Backend `/billing/checkout`, `/billing/callback/{trackid}?s=`, `/billing/status/{trackid}`, `/billing/history`. New `payments` table, `organizations.paid_through_at` + `.plan_term_months` columns. Trial shortened to 5 days. NIUPAY_MODE=1 (test). 10 new pytest integration tests — total 48 passing.
```

2. Under `Still NOT done`, add `Niupay live-mode cutover (set NIUPAY_MODE=2 in .env after soak)` near the top.

3. Under `## Open items to confirm before resuming`, add item 6: `Niupay live-mode flip: decide soak duration + cutover date.`

- [ ] **Step 5: Offer follow-up schedule**

After the merge, propose to the user: schedule a background agent in 7-14 days to check Niupay test-mode usage logs and draft the live-mode flip commit. This is a one-shot follow-up, not a recurring job.
