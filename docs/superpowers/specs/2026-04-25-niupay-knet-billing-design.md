# Niupay KNET Billing — Design Spec

**Date:** 2026-04-25
**Status:** Ready for implementation planning
**Related:** Roadmap's Phase 2 KNET integration, pulled forward. Stripe Checkout in the original roadmap is deprioritised in favour of Niupay-first for the Kuwait market.

## Purpose

Wire KNET payments (via Niupay) into the admin so a user can upgrade from the 14-day trial (now shortened to 5 days) to a paid plan, picking tier × term. This first slice proves the Niupay → webhook → DB plumbing end-to-end with the smallest possible surface — no trial paywall, no renewal emails, no cancel/downgrade yet.

## Scope

**In scope:**
- Shorten the free trial from 14 → 5 days (one constant + downstream strings + tests).
- New `/billing` admin page (dedicated nav entry): current plan card, upgrade picker (4 tiers × 3 terms), past-payments list.
- Four backend endpoints under `/billing/*`: `POST /billing/checkout`, `POST /billing/callback/{trackid}`, `GET /billing/status/{trackid}`, `GET /billing/history`.
- Niupay HTTP client (`backend/billing.py`) wrapping `POST https://niupay.me/api/requestKnet`.
- DB schema additions: `organizations.paid_through_at`, `organizations.plan_term_months`; new `payments` table.
- Pricing constants: 4 tiers × 3 terms, USD-primary / KWD-secondary dual display, KWD rounded to nearest whole unit (`round_to_nearest` — not ceiling, to avoid Starter/Growth collision at 4 KWD).
- Webhook security: shared-secret querystring on `responseUrl`, idempotent capture, amount pinned server-side.
- 10 new backend integration tests with mocked Niupay client. Existing 38 stay green.

**Explicitly out of scope (deferred to later slices):**
- Trial-expiry paywall (read-only mode when `trial_ends_at < now && paid_through_at IS NULL`).
- Renewal reminder emails (Resend integration is still a DEV stub).
- Cancel / downgrade / refund / proration flows.
- Live-mode cutover (flip `NIUPAY_MODE=1` → `2`). Gated on manual sign-off after soak.
- Auto-renew / card tokenization (Niupay docs do not expose this publicly).
- Invoices / VAT receipts (Kuwait has no VAT yet).
- Arabic copy on `/billing` — the Arabic sub-project retrofits translations across every surface.
- Stripe Checkout from the original roadmap — not built; deprioritised.

## Decisions (locked in during brainstorming)

| # | Decision | Chosen | Rejected |
|---|----------|--------|----------|
| 1 | Upgrade entrypoint | **"Upgrade" button on dashboard plan card + dedicated `/billing` page.** | Trial-expiry paywall combo; signup-time checkout; all three from day one. |
| 2 | Trial length | **5 days** (down from 14). | Stay at 14; skip trial; longer 30-day trial. |
| 3 | Subscription model | **Prepaid terms: 1 / 6 / 12 months.** 6-mo = 5× monthly (1 month free); 12-mo = 10× monthly (2 months free). | Monthly pay-as-you-go; card tokenization auto-renew. |
| 4 | Currency display | **Dual USD + KWD.** USD primary (typographically larger), KWD secondary. KWD is what's charged. | KWD-only reprice; live FX conversion at checkout; dual with live FX. |
| 5 | KWD rounding | **Round to nearest whole KWD** (no decimals displayed or charged). | Ceiling (causes Starter/Growth collision at 4 KWD); floor (amount undercharged vs. USD); round to fils. |
| 6 | UI surface | **Dedicated `/billing` admin page** (new nav entry). | Modal on plan card; inline plan-card expansion. |

## Pricing ladder

Computed via `amount_kwd = round(usd × TERM_MULTIPLIER × USD_TO_KWD)` where `USD_TO_KWD = Decimal("0.306")` (fixed rate, updated manually when KWD moves >2%). Term multipliers: `{1: 1, 6: 5, 12: 10}` — 6-mo buys 1 month free, 12-mo buys 2 months free.

| Tier | Screens | Monthly (USD / KWD) | 6-month (USD / KWD) | 12-month (USD / KWD) |
|------|---------|---------------------|---------------------|----------------------|
| Starter | up to 3 | $9.99 · 3 KWD | $49.95 · 15 KWD | $99.90 · 31 KWD |
| Growth | up to 5 | $12.99 · 4 KWD | $64.95 · 20 KWD | $129.90 · 40 KWD |
| Business | up to 10 | $24.99 · 8 KWD | $124.95 · 38 KWD | $249.90 · 77 KWD |
| Pro | up to 25 | $49.99 · 15 KWD | $249.95 · 77 KWD | $499.90 · 153 KWD |

KWD amounts are sent to Niupay as integers (Niupay accepts 3-decimal precision but we always use whole KWD). The USD line on the UI is typographically primary; the KWD line reads `≈ N KWD`.

## Architecture

### Backend

**New file `backend/billing.py`** — thin Niupay client.

```python
import os, secrets
import httpx
from decimal import Decimal

NIUPAY_URL = "https://niupay.me/api/requestKnet"
NIUPAY_API_KEY = os.environ["NIUPAY_API_KEY"]
NIUPAY_MODE = int(os.getenv("NIUPAY_MODE", "1"))           # 1=test, 2=live
NIUPAY_CALLBACK_SECRET = os.environ["NIUPAY_CALLBACK_SECRET"]

def create_knet_request(*, trackid: str, amount_kwd: int, response_url: str, success_url: str, error_url: str) -> dict:
    payload = {
        "apikey": NIUPAY_API_KEY,
        "type": NIUPAY_MODE,
        "trackid": trackid,
        "amount": f"{amount_kwd}.000",
        "language": 1,
        "responseUrl": response_url,
        "successUrl":  success_url,
        "errorUrl":    error_url,
    }
    r = httpx.post(NIUPAY_URL, json=payload, timeout=15.0)
    r.raise_for_status()
    return r.json()
```

**Endpoints added to `backend/main.py`:**

| Method + path | Auth | Behaviour |
|---|---|---|
| `POST /billing/checkout` | session (admin role) | Body `{tier, term_months}`. Computes amount from pricing table. Generates `trackid = "pay_" + secrets.token_hex(16)`. Rate-limits: if an existing row with `(organization_id, tier, term_months, status="pending")` was created < 60s ago, return its existing `payment_url` rather than creating a new one. Otherwise inserts `payments` row (status=`pending`, amounts stored). Calls `create_knet_request` with callback/success/error URLs constructed from env. Stores `niupay_payment_id`. Returns `{payment_url}`. |
| `POST /billing/callback/{trackid}` | shared secret in `?s=` | 404 if `?s` missing or mismatched. Parses body. If `body.trackid != path.trackid` → 400. Looks up payment row; unknown → 200 (no-leak). Already non-pending → 200 (idempotent). If `body.result == "CAPTURED"` → payment=`captured`, org.plan = payment.tier, org.plan_term_months = payment.term_months, org.paid_through_at = `now + term × 30 days`. Otherwise payment=`failed`. Store `niupay_tranid`, `niupay_result`, `niupay_ref`. Return 200 in all cases. |
| `GET /billing/status/{trackid}` | session (any role in org) | Returns `{status, tier, term_months, amount_kwd, amount_usd_display, paid_through_at}` for the caller's own org. 404 cross-org. Used for the success-page poll. |
| `GET /billing/history` | session (admin role) | Returns the org's `payments` rows (status IN captured/failed) ordered by `created_at desc`. |

**Amount pinning.** The captured amount we commit is the KWD we stored when we created the row — Niupay's callback body's amount is stored for audit but not trusted to mutate state.

### DB (new migrations in `backend/db.py init_db()`)

**Columns added to `organizations`:**
- `paid_through_at TIMESTAMPTZ NULL`
- `plan_term_months INTEGER NULL` (nullable since null = not yet purchased)

**New table `payments`:**
```sql
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
);
CREATE INDEX IF NOT EXISTS ix_payments_org_status ON payments(organization_id, status);
CREATE INDEX IF NOT EXISTS ix_payments_pending_key ON payments(organization_id, tier, term_months) WHERE status='pending';
```

### Frontend

**New page:** hidden `<section id="billing-view" class="panel billing-view hidden">` sibling of `#dashboard` and `#pair-view` in `frontend/index.html`. Switched in by a new `showBilling()` controller the same way `showPairView` works. Nav button `Billing` appended to the existing admin nav.

**Routing:** `/billing` in `boot()` path branch (same pattern as `/pair` — nginx `try_files` already serves `/index.html`). Also accessible via `showBilling()` called from the "Manage billing" link on the plan card.

**Layout (phone-first, reuses pastel tokens):**

```
┌─ Billing ────────────────────┐
│                              │
│  Current plan                │
│  ┌────────────────────────┐  │
│  │ Starter · Trial        │  │
│  │ 3 days left            │  │
│  │ up to 3 screens        │  │
│  └────────────────────────┘  │
│                              │
│  Upgrade                     │
│  [ Monthly | 6-mo | 12-mo ]  │ ← segmented control (defaults to Monthly)
│                              │
│  ┌────────────────────────┐  │
│  │ Starter                │  │
│  │ up to 3 screens        │  │
│  │ $9.99 / month          │  │
│  │ ≈ 3 KWD                │  │
│  │ [ Pay 3 KWD ]          │  │
│  └────────────────────────┘  │
│  … Growth, Business, Pro …   │
│                              │
│  Past payments               │
│  (empty, or list of rows)    │
└──────────────────────────────┘
```

Selecting a term on the segmented control re-prices every card in place. The Pay button says `Pay <N> KWD · save 1 month` (6-mo) or `· save 2 months` (12-mo).

**Success flow:**
- On `Pay` click: disable button, POST `/billing/checkout`, then `window.location.href = payment_url` (full redirect).
- On return to `/billing?status=success&trackid=…`: show "Confirming payment…" placeholder, poll `GET /billing/status/{trackid}` every 2 s. On `captured` → green banner "Plan upgraded · Starter · paid through 2026-10-25" + re-render plan card. On `failed` → red banner. Timeout after 15 s with "Payment is still processing — check back in a minute".

### Env vars (all in gitignored `.env`)

```
NIUPAY_API_KEY=ad274322bdff17670b37dd4ca7c5992a
NIUPAY_MODE=1
NIUPAY_CALLBACK_SECRET=<32-hex generated at install>
```

`NIUPAY_CALLBACK_SECRET` is generated once and never logged. It's embedded in the `responseUrl` querystring sent to Niupay.

## Security (webhook defense in depth)

1. **Path secret.** `responseUrl` is `https://api.khanshoof.com/billing/callback/<trackid>?s=<SECRET>`. Any POST missing or mismatching `?s` is answered with 404 (hides the endpoint). Primary control since Niupay docs expose no HMAC signatures.
2. **Body validation.** `body.trackid` must equal `<trackid>` in the path. Mismatch → 400.
3. **Idempotency.** A payment already in `captured` or `failed` short-circuits: 200 + no DB mutation. Avoids double-applying paid-through extensions on replay.
4. **Amount pinning.** Captured amount is the value we stored when creating the pending row — not read from the callback body.
5. **Info-hiding.** Unknown trackid → 200 (no leak). Wrong secret → 404.
6. **TLS-only.** All Niupay calls go through Cloudflare-terminated HTTPS on `api.khanshoof.com`. HTTP would be refused by the tunnel.
7. **API key boundary.** `NIUPAY_API_KEY` only exists in backend env — frontend never sees it. Browser POSTs to our backend, which adds the key before calling Niupay.

## Error paths

| Failure | Handling |
|---|---|
| Niupay HTTP 5xx / network timeout at checkout | Backend 502 to frontend; payment row stays `pending` (will be cleaned up by a future sweeper — not built now). User sees "Can't reach payment gateway. Try again." |
| Invalid tier / term in checkout body | 422 with Pydantic validation |
| Callback arrives but Niupay redirect doesn't (user closed tab) | Plan activates server-side; next `/billing` visit reflects it |
| Callback POST never arrives but user lands on success page | Poll retries up to 15 s; then "Payment is still processing" — manual operator check via `/billing/history` |
| Double-click on Pay within 60s | Rate limiter returns existing pending trackid; no duplicate Niupay request |
| Callback with `result=HOST_TIMEOUT` / `CANCELED` / anything ≠ CAPTURED | Payment→failed, org untouched, user sees error banner |
| Callback with wrong trackid in body | 400; no state change |
| Callback replay after success | 200 no-op (already captured) |

## Testing

**Backend integration (`backend/tests/test_billing.py`, 10 tests, mock `backend.billing.create_knet_request`):**

1. Happy-path checkout inserts pending row and returns Niupay URL.
2. Unknown tier → 422.
3. Unknown term_months → 422.
4. Unauthenticated → 401.
5. Rate-limit: second checkout for same (org, tier, term) within 60 s returns the first row's URL, no new Niupay call.
6. Callback wrong secret → 404.
7. Callback unknown trackid → 200, no DB mutation.
8. Callback `CAPTURED` → payment captured, org.plan + paid_through_at updated (term × 30 days).
9. Callback non-CAPTURED (e.g., `HOST_TIMEOUT`) → payment failed, org untouched.
10. Callback idempotency: second CAPTURED for same trackid does not double paid_through_at.

**Existing regression (`backend/tests/test_signup_otp.py`, `test_plan_limits.py`):** both assert `trial_ends_at` is ~14 days out. Update those expectations to 5 days as part of the trial-shortening task.

**Manual browser smoke (once implementation lands):**

- Test mode (`NIUPAY_MODE=1`). Card `888888 0000000001` = capture; card `888888 0000000002` = failure; expiry `09/30`.
- Plan card updates within 2-5 s of callback.
- `/billing?status=error` shows red banner; resubmit creates a new trackid.
- 5-day trial countdown renders in: admin plan card, signup password-step helper text, landing nav pill.

## Files touched

```
.env                                  # NEW vars: NIUPAY_API_KEY, NIUPAY_MODE, NIUPAY_CALLBACK_SECRET  (gitignored)
backend/billing.py                    # NEW: Niupay HTTP client
backend/db.py                         # columns + payments table
backend/main.py                       # pricing constants, 4 /billing endpoints, 5-day trial
backend/tests/test_billing.py         # NEW: 10 tests
backend/tests/test_signup_otp.py      # trial-length assertion: 14 → 5 days
backend/tests/test_plan_limits.py     # same
frontend/index.html                   # nav entry, #billing-view panel
frontend/app.js                       # /billing routing + showBilling controller + fetch/redirect/poll + plan-card "Manage billing" link + 5-day trial copy
frontend/styles.css                   # .billing-view* rules
landing/index.html                    # "5-day free trial" pill copy
```

No changes to `player/`.

## Open follow-ups (not this slice)

- Trial-expiry paywall: when `trial_ends_at < now && paid_through_at IS NULL`, block admin writes + show "Pay to continue" block. Separate design pass — downgrade/cancel UX should be considered alongside.
- Renewal reminder emails: requires Resend to be wired off the DEV stub.
- Live-mode flip: once soak-tested on type=1, set `NIUPAY_MODE=2` in `.env` and restart backend. Requires explicit user sign-off.
- Auto-renew via card tokenization: check with Niupay support for tokenisation availability.
- `/billing` page translated to Arabic: handled uniformly by the Arabic i18n sub-project.
