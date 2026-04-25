# Arabic / RTL Bilingual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a real bilingual EN/AR Khanshoof — admin, landing, and player — with full RTL flip, IBM Plex Sans Arabic font swap, and a pricing rebase from `$9.99/$12.99/$24.99/$49.99` to integer KWD primary (`3/4/8/15 KWD`) with USD shown as conversion.

**Architecture:** Per-org `locale` column drives logged-in admin; `khanshoof_lang` cookie scoped to `.khanshoof.com` drives anon surfaces (landing, signup, pairing). Each app gets its own ~50 LOC `i18n.js` + `i18n/{en,ar}.json` pair — no library, no build step. CSS sweeps physical to logical properties (`margin-inline-start` etc.); browser flips automatically with `dir="rtl"`. Backend introduces a small `http_error()` helper so user-facing errors carry a `code` field the frontend can localize.

**Tech Stack:** FastAPI + Postgres backend; vanilla JS + nginx-served static frontends. Pytest for backend. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-25-arabic-bilingual-design.md`

---

## File Structure

**Backend (existing files modified):**
- `backend/main.py` — `http_error()` helper, `PATCH /organizations/me` endpoint, `/auth/me` + `/auth/login` augmented, `PLANS` rebase, user-facing error sites migrated to `http_error()`
- `backend/billing.py` — read renamed pricing constant
- `backend/tests/test_organization_locale.py` — NEW
- `backend/tests/test_billing.py` — KWD price assertions
- `backend/tests/test_signup_otp.py`, `test_pairing.py`, `test_plan_limits.py`, `test_smoke.py` — assert on `detail.code` for migrated endpoints

**Frontend (admin), all NEW unless noted:**
- `frontend/i18n.js`
- `frontend/i18n/en.json`, `frontend/i18n/ar.json`
- `frontend/index.html` — modify: `data-i18n` attributes, language toggle, `<link>` for Arabic font
- `frontend/app.js` — modify: boot order, `t()` integration, error-code mapper, dynamic strings
- `frontend/styles.css` — modify: logical properties sweep, `:lang(ar)` font swap, RTL exceptions
- `frontend/assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2` — NEW

**Landing (same shape as admin):**
- `landing/i18n.js`, `landing/i18n/en.json`, `landing/i18n/ar.json`
- `landing/index.html` — modify
- `landing/app.js` — modify
- `landing/styles.css` — modify
- `landing/assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2` — NEW

**Player (smaller surface):**
- `player/i18n.js`, `player/i18n/en.json`, `player/i18n/ar.json`
- `player/index.html` — modify
- `player/player.js` — modify
- `player/styles.css` — modify
- `player/assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2` — NEW

**Tooling:**
- `scripts/check_i18n.py` — NEW

**Tests run via:** `docker exec signage_backend_1 pytest`. The container does NOT auto-mount source, so updated files must be `docker cp`'d in before running tests, or the image rebuilt with `docker-compose build backend`. The pattern used previously in this branch:

```bash
docker cp backend/main.py signage_backend_1:/app/main.py && \
docker cp backend/tests/test_X.py signage_backend_1:/app/tests/test_X.py && \
docker exec signage_backend_1 pytest tests/test_X.py -v
```

After all backend work passes locally, rebuild + restart the image once at the end:

```bash
docker-compose build backend && docker-compose up -d backend
```

For frontend changes, the nginx images for admin/landing/player serve files from inside the image. Use `docker-compose build <service> && docker-compose up -d <service>` to deploy. Local dev iteration: edit files → rebuild → reload browser. There is no hot-reload setup.

---

## Part 1 — Backend foundation

### Task 1: `http_error()` helper

**Files:**
- Modify: `backend/main.py` (add helper near other utilities, around the `validate_password` block ~line 145)
- Create: `backend/tests/test_http_error.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_http_error.py`:
```python
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from main import http_error


def test_http_error_returns_httpexception():
    err = http_error(400, "email_taken", "Email is already registered")
    assert isinstance(err, HTTPException)
    assert err.status_code == 400
    assert err.detail == {"code": "email_taken", "message": "Email is already registered"}


def test_http_error_renders_through_fastapi():
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise http_error(403, "insufficient_role", "Insufficient role")

    client = TestClient(app)
    resp = client.get("/boom")
    assert resp.status_code == 403
    assert resp.json() == {"detail": {"code": "insufficient_role", "message": "Insufficient role"}}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker cp backend/tests/test_http_error.py signage_backend_1:/app/tests/test_http_error.py
docker exec signage_backend_1 pytest tests/test_http_error.py -v
```

Expected: FAIL `ImportError: cannot import name 'http_error' from 'main'`.

- [ ] **Step 3: Add the helper**

In `backend/main.py`, after the `validate_password()` function and before the `OTP_TTL_SECONDS` constants block (~line 165), add:

```python
def http_error(status: int, code: str, message: str) -> HTTPException:
    """Structured error response: detail = {code, message}.

    Frontend reads `code` to look up a localized string; falls back to
    `message` (English) if the code is unrecognized.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec signage_backend_1 pytest tests/test_http_error.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_http_error.py
git commit -m "feat(backend): http_error() helper for structured error responses"
```

---

### Task 2: Migrate user-facing error sites to `http_error()`

Only sites whose message is shown directly in the UI move. Auth/session errors (401 + missing auth, 403 generic permission) keep their string form — the SPA already handles them generically by redirecting to login or showing "Permission denied".

Sites to migrate (line numbers are approximate, verify before editing):

| File:line | Old | New |
|---|---|---|
| main.py:282 | `"Password must be at least 8 characters"` | `http_error(400, "password_too_short", "Password must be at least 8 characters")` |
| main.py:284 | `"Password must include a letter"` | `http_error(400, "password_no_letter", "Password must include a letter")` |
| main.py:286 | `"Password must include a number"` | `http_error(400, "password_no_number", "Password must include a number")` |
| main.py:574 | `"Invalid email address"` | `http_error(400, "invalid_email", "Invalid email address")` |
| main.py:576 | `"Email is already registered"` | `http_error(400, "email_taken", "Email is already registered")` |
| main.py:584 (cooldown 429) | f"Please wait {N} seconds…" | `http_error(429, "otp_cooldown", f"Please wait {OTP_RESEND_COOLDOWN_SECONDS} seconds before requesting another code.")` |
| main.py:634 | `"No pending signup for this email"` | `http_error(400, "no_pending_signup", "No pending signup for this email")` |
| main.py:642 | `"Code expired. Please request a new one."` | `http_error(400, "otp_expired", "Code expired. Please request a new one.")` |
| main.py:645 | `"Too many incorrect attempts. Request a new code."` | `http_error(400, "otp_attempts_exceeded", "Too many incorrect attempts. Request a new code.")` |
| main.py:652 | `"Incorrect code"` | `http_error(400, "otp_incorrect", "Incorrect code")` |
| main.py:684 | `"Invalid or expired verification token"` | `http_error(400, "invalid_verification_token", "Invalid or expired verification token")` |
| main.py:692 | `"Verification token expired. Please restart signup."` | `http_error(400, "verification_token_expired", "Verification token expired. Please restart signup.")` |
| main.py:699 | `"Email is already registered"` | `http_error(400, "email_taken", "Email is already registered")` |
| main.py:772 | `"Invalid credentials"` | `http_error(401, "invalid_credentials", "Invalid credentials")` |
| main.py:802 | `"Invalid current password"` | `http_error(400, "invalid_current_password", "Invalid current password")` |
| main.py:837 | `"Username already exists"` (user create) | `http_error(400, "username_taken", "Username already exists")` |
| main.py:839 | `"Invalid role"` | `http_error(400, "invalid_role", "Invalid role")` |
| main.py:877 | `"Invalid role"` (user update) | `http_error(400, "invalid_role", "Invalid role")` |
| Plan-limit 402 inside `/auth/signup/complete` and screen creation paths | "Plan limit reached" / similar | `http_error(402, "plan_limit", "Plan limit reached")` |

For sites NOT listed (auth 401s like "Missing authorization", 404 "Organization not found", "Site not found"), keep the bare string. They're generic SPA-handled cases.

**Files:**
- Modify: `backend/main.py` (multiple lines per table above)
- Modify: `backend/tests/test_signup_otp.py`, `backend/tests/test_smoke.py`, `backend/tests/test_pairing.py`, `backend/tests/test_plan_limits.py`

- [ ] **Step 1: Update tests first to assert new error shape**

Search backend test files for `response.json()["detail"]` and update assertions.

For each migrated site, the test changes from:
```python
assert resp.json()["detail"] == "Email is already registered"
```
to:
```python
assert resp.json()["detail"]["code"] == "email_taken"
```

Find every site:
```bash
grep -rn 'json()\["detail"\] ==' backend/tests/
grep -rn 'detail.*Email is already registered\|detail.*Incorrect code\|detail.*Code expired\|detail.*Invalid credentials' backend/tests/
```

Update each match. Keep tests asserting on a still-string `detail` (e.g. "Missing authorization") unchanged.

- [ ] **Step 2: Run tests — expect RED**

```bash
for f in backend/tests/test_*.py; do
  docker cp "$f" "signage_backend_1:/app/tests/$(basename $f)"
done
docker exec signage_backend_1 pytest -v 2>&1 | tail -30
```

Expected: failures across signup_otp / pairing / plan_limits tests with `detail` mismatches (because `main.py` still returns strings).

- [ ] **Step 3: Migrate sites in `main.py`**

Apply the table substitutions. For every row, replace:
```python
raise HTTPException(status_code=N, detail="...")
```
with:
```python
raise http_error(N, "code_string", "...")
```

After the sweep, run a sanity grep:
```bash
grep -nE 'HTTPException\(status_code=[0-9]+, detail="' backend/main.py
```
Expected: only auth/session/404 sites remain (the unmigrated set).

- [ ] **Step 4: Run all tests — expect GREEN**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec signage_backend_1 pytest -v 2>&1 | tail -10
```

Expected: all green (the same 76 + new http_error tests).

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/
git commit -m "refactor(backend): migrate user-facing errors to http_error() with codes"
```

---

### Task 3: `PATCH /organizations/me` endpoint

**Files:**
- Modify: `backend/main.py` (add new endpoint near `GET /organization` ~line 755)
- Create: `backend/tests/test_organization_locale.py`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_organization_locale.py`:
```python
def test_patch_organization_locale_admin_succeeds(client, signed_up_org):
    token = signed_up_org["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "ar"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locale"] == "ar"


def test_patch_organization_locale_rejects_invalid(client, signed_up_org):
    token = signed_up_org["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "fr"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_locale"


def test_patch_organization_locale_requires_auth(client):
    resp = client.patch("/organizations/me", json={"locale": "ar"})
    assert resp.status_code == 401


def test_patch_organization_locale_requires_admin(client, signed_up_org):
    """Editor cannot change org locale."""
    admin_token = signed_up_org["token"]
    # Create an editor user inside the same org
    r = client.post(
        "/users",
        json={"username": "editor@example.com", "password": "testpass1", "role": "editor"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/auth/login",
        json={"username": "editor@example.com", "password": "testpass1"},
    )
    editor_token = r.json()["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "ar"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests — expect RED**

```bash
docker cp backend/tests/test_organization_locale.py signage_backend_1:/app/tests/test_organization_locale.py
docker exec signage_backend_1 pytest tests/test_organization_locale.py -v
```

Expected: 4 failures, all 404 (endpoint doesn't exist).

- [ ] **Step 3: Add the endpoint**

In `backend/main.py`, after `get_organization()` (~line 765):

```python
class OrganizationLocaleUpdate(BaseModel):
    locale: str = Field(..., min_length=2, max_length=2)


@app.patch("/organizations/me")
def patch_organization_me(
    payload: OrganizationLocaleUpdate,
    user: dict = Depends(require_role("admin")),
) -> dict:
    if payload.locale not in ("en", "ar"):
        raise http_error(400, "invalid_locale", "Locale must be 'en' or 'ar'")
    execute(
        "UPDATE organizations SET locale = ? WHERE id = ?",
        (payload.locale, org_id(user)),
    )
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    return org
```

Use whatever role-gating helper already exists (`require_role`, `require_admin`, etc.). Check how `POST /users` is gated and copy the pattern. If the existing helper is named differently, adapt the dependency.

- [ ] **Step 4: Run tests — expect GREEN**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec signage_backend_1 pytest tests/test_organization_locale.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_organization_locale.py
git commit -m "feat(backend): PATCH /organizations/me for admin locale updates"
```

---

### Task 4: Include `locale` in `/auth/me` and `/auth/login` responses

**Files:**
- Modify: `backend/main.py` (response builders for login + signup_complete + GET /organization)
- Modify: existing test files that inspect login/signup-complete responses (`test_signup_otp.py`, `test_smoke.py`)

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_organization_locale.py`:
```python
def test_login_response_includes_locale(client, signed_up_org, unique_business):
    r = client.post(
        "/auth/login",
        json={"username": unique_business["email"], "password": unique_business["password"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["organization"]["locale"] == "en"


def test_signup_complete_response_includes_locale(signed_up_org):
    assert signed_up_org["org"]["locale"] == "en"


def test_get_organization_includes_locale(client, signed_up_org):
    token = signed_up_org["token"]
    r = client.get("/organization", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["locale"] == "en"
```

- [ ] **Step 2: Run — expect RED**

```bash
docker cp backend/tests/test_organization_locale.py signage_backend_1:/app/tests/test_organization_locale.py
docker exec signage_backend_1 pytest tests/test_organization_locale.py::test_login_response_includes_locale tests/test_organization_locale.py::test_signup_complete_response_includes_locale tests/test_organization_locale.py::test_get_organization_includes_locale -v
```

Expected: KeyError or assertion failure on `locale`.

- [ ] **Step 3: Augment response builders**

In `backend/main.py`:

a. `signup_complete` (~line 743): inside the returned `"organization": {...}` block, add `"locale": "en",` (or read from the inserted org row).

b. `login` (~line 779): the response includes a `"organization"` block. Add `"locale": user_org["locale"]` where `user_org` is fetched. If the existing login response doesn't include the org block, look up the org via `query_one("SELECT * FROM organizations WHERE id = ?", (user["organization_id"],))` and serialize `{id, name, slug, plan, screen_limit, subscription_status, trial_ends_at, locale}`.

c. `get_organization` already returns the full row via `query_one("SELECT * FROM organizations ...")`, so `locale` flows automatically — no change needed if the row already includes it.

- [ ] **Step 4: Run all tests — expect GREEN**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec signage_backend_1 pytest -v 2>&1 | tail -10
```

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_organization_locale.py
git commit -m "feat(backend): include organization.locale in auth + signup responses"
```

---

### Task 5: Pricing rebase to integer KWD

KWD becomes the primary currency (clean integers); USD displayed as conversion.

**New constants** (replacing the existing ones in `backend/main.py` ~line 246):

```python
PLANS = {
    "starter":    {"screen_limit": 3,    "price_kwd_monthly": 3,  "label": "Starter"},
    "growth":     {"screen_limit": 5,    "price_kwd_monthly": 4,  "label": "Growth"},
    "business":   {"screen_limit": 10,   "price_kwd_monthly": 8,  "label": "Business"},
    "pro":        {"screen_limit": 25,   "price_kwd_monthly": 15, "label": "Pro"},
    "enterprise": {"screen_limit": 9999, "price_kwd_monthly": 0,  "label": "Enterprise"},
}

# ── Billing pricing table ────────────────────────────────────────────
KWD_TO_USD = Decimal("3.267")  # 1/0.306 — display only, manual update if KWD moves >2%
PLAN_PRICING_KWD: dict[str, Decimal] = {
    "starter":  Decimal("3"),
    "growth":   Decimal("4"),
    "business": Decimal("8"),
    "pro":      Decimal("15"),
}
```

`USD_TO_KWD` and `PLAN_PRICING_USD` are removed.

**Files:**
- Modify: `backend/main.py` (PLANS, pricing constants, any reads of removed names)
- Modify: `backend/billing.py` (reads of pricing constant)
- Modify: `backend/tests/test_billing.py` (assertion fixtures)
- Modify: `frontend/app.js` (BILLING_PLANS array around line 1660 + USD_TO_KWD use)
- Modify: `frontend/index.html` (any hardcoded prices)
- Modify: `landing/index.html` (pricing section, hero "starter from..." line)

- [ ] **Step 1: Update billing tests first to expect KWD amounts**

In `backend/tests/test_billing.py`, find all KWD assertions and replace with the new ladder. Examples:
```python
# was: assert resp.json()["amount_kwd"] == 3  # 9.99 * 0.306 rounded
# now: assert resp.json()["amount_kwd"] == 3  # 3 KWD flat
```

The actual change: anywhere a test computes `9.99 * 0.306` or asserts `kwd_amount == 3` for the old conversion, the source of truth becomes `PLAN_PRICING_KWD["starter"] == 3` directly. Sweep:

```bash
grep -nE '9\.99|12\.99|24\.99|49\.99|USD_TO_KWD|PLAN_PRICING_USD' backend/tests/
```

Update assertions and test fixture references.

- [ ] **Step 2: Run — expect RED on backend**

```bash
for f in backend/tests/test_*.py; do
  docker cp "$f" "signage_backend_1:/app/tests/$(basename $f)"
done
docker exec signage_backend_1 pytest tests/test_billing.py -v
```

Expected: failures (constants still old in main.py).

- [ ] **Step 3: Apply backend constants**

Replace the `PLANS` dict and pricing constants in `backend/main.py` with the block above. Search for any remaining references to `USD_TO_KWD` or `PLAN_PRICING_USD` or `price_usd_monthly`:

```bash
grep -nE 'USD_TO_KWD|PLAN_PRICING_USD|price_usd_monthly' backend/main.py backend/billing.py
```

For each hit, switch to the new name. The KNET billing flow in `billing.py` already takes `amount_kwd: int` so the change is purely upstream — `PLAN_PRICING_KWD[plan_key]` flows in cleanly.

If the existing PLANS response is rendered to clients (e.g. `GET /plans`), the response body field changes from `price_usd_monthly` to `price_kwd_monthly`. The frontend reads this — handled in Task 11.

- [ ] **Step 4: Run backend tests — expect GREEN**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker cp backend/billing.py signage_backend_1:/app/billing.py
docker exec signage_backend_1 pytest -v 2>&1 | tail -10
```

Expected: full suite green.

- [ ] **Step 5: Update admin frontend `app.js` BILLING_PLANS array**

In `frontend/app.js` near line 1660, replace:
```js
const BILLING_PLANS = [
  { tier: "starter",  label: "Starter",  usd: 9.99,  screens: 3  },
  { tier: "growth",   label: "Growth",   usd: 12.99, screens: 5  },
  { tier: "business", label: "Business", usd: 24.99, screens: 10 },
  { tier: "pro",      label: "Pro",      usd: 49.99, screens: 25 },
];
```
with:
```js
const BILLING_PLANS = [
  { tier: "starter",  label: "Starter",  kwd: 3,  screens: 3  },
  { tier: "growth",   label: "Growth",   kwd: 4,  screens: 5  },
  { tier: "business", label: "Business", kwd: 8,  screens: 10 },
  { tier: "pro",      label: "Pro",      kwd: 15, screens: 25 },
];
```

Then update the price-render block (~line 1680):
```js
function planAmounts(plan, term) {
  const kwd = plan.kwd * term.multiplier;
  const usdApprox = (kwd * 3.267).toFixed(2);
  return { kwd, usdApprox };
}
```

And the renderer (~line 1720):
```js
<div class="billing-tier-kwd">${amounts.kwd} KWD${billingCurrentTerm === 1 ? " / month" : ""}</div>
<div class="billing-tier-usd">≈ $${amounts.usdApprox}</div>
```

(KWD is now primary, USD secondary — opposite of before.)

Find the existing `USD_TO_KWD` constant in `app.js` and remove it; replace any remaining uses with `* 3.267` (KWD → USD) inline.

- [ ] **Step 6: Update landing pricing**

In `landing/index.html` ~line 53, replace the hero blurb:
```html
<span>Starter plan from <strong>3 KWD/month</strong> — up to 3 screens.</span>
```

In the pricing section (~line 159 onwards), each `.pricing-amt` block becomes:
```html
<div class="pricing-price">
  <span class="pricing-amt">3 KWD</span>
  <span class="pricing-per">/ month</span>
</div>
<div class="pricing-usd-approx">≈ $9.80 USD</div>
```

Apply the ladder: 3/4/8/15 KWD → ≈ $9.80 / $13.07 / $26.14 / $49.02 USD.

In `landing/styles.css`, add a small rule for the new line:
```css
.pricing-usd-approx {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 4px;
}
```

(`--text-muted` should already be defined; if not, use a hex like `#6b6480`.)

- [ ] **Step 7: Smoke**

Rebuild + restart, hit `/plans` and the landing page:
```bash
docker-compose build backend frontend landing && docker-compose up -d backend frontend landing
sleep 5
curl -fsS https://api.khanshoof.com/plans | python3 -m json.tool | head -20
curl -fsS https://yalla.khanshoof.com | grep -A1 "pricing-amt" | head -10
```

Expected: `/plans` shows `price_kwd_monthly: 3` etc.; landing HTML shows `3 KWD`.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/billing.py backend/tests/test_billing.py \
        frontend/app.js landing/index.html landing/styles.css
git commit -m "feat(pricing): rebase to integer KWD primary (3/4/8/15), USD as conversion"
```

---

## Part 2 — i18n helper + translation file scaffolds

### Task 6: Admin `i18n.js` + skeleton translation files

**Files:**
- Create: `frontend/i18n.js`
- Create: `frontend/i18n/en.json`
- Create: `frontend/i18n/ar.json`

- [ ] **Step 1: Write `frontend/i18n.js`**

```js
// Tiny i18n runtime. Loads /i18n/{en|ar}.json, applies translations to
// [data-i18n*] attributes, exposes t() for dynamic strings.

const ALLOWED = ["en", "ar"];
let _strings = {};
let _locale = "en";

export async function loadLocale(locale) {
  if (!ALLOWED.includes(locale)) locale = "en";
  const res = await fetch(`/i18n/${locale}.json`);
  if (!res.ok) {
    console.error("[i18n] failed to load", locale, res.status);
    return;
  }
  _strings = await res.json();
  _locale = locale;
  document.documentElement.lang = locale;
  document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
}

export function t(key, fallback) {
  const v = _strings[key];
  if (v == null) {
    if (typeof console !== "undefined") console.warn("[i18n] missing key:", key);
    return fallback != null ? fallback : key;
  }
  return v;
}

export function applyTranslations(root) {
  const r = root || document;
  r.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  r.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  r.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  r.querySelectorAll("[data-i18n-aria-label]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  });
}

function setCookie(locale) {
  const host = location.hostname;
  const isProd = host.endsWith("khanshoof.com");
  const domainAttr = isProd ? "; domain=.khanshoof.com" : "";
  document.cookie = `khanshoof_lang=${locale}${domainAttr}; path=/; max-age=31536000; samesite=lax`;
}

export function setLocale(locale) {
  if (!ALLOWED.includes(locale)) locale = "en";
  setCookie(locale);
  return locale;
}

export function detectInitialLocale(orgLocale) {
  if (ALLOWED.includes(orgLocale)) return orgLocale;
  const m = document.cookie.match(/(?:^|; )khanshoof_lang=(en|ar)\b/);
  if (m) return m[1];
  const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
  return browser === "ar" ? "ar" : "en";
}

export function currentLocale() {
  return _locale;
}
```

- [ ] **Step 2: Write `frontend/i18n/en.json` skeleton**

Start with an empty object; the file will fill out across Tasks 10 and 11. Keys must be added as the markup is migrated. For now:

```json
{}
```

- [ ] **Step 3: Write `frontend/i18n/ar.json` skeleton**

```json
{}
```

- [ ] **Step 4: Wire into Docker — confirm nginx serves the new files**

`frontend/nginx.conf` should already serve any path under `/`. Verify by checking it has no allow-list of specific files. If it does, ensure `/i18n/*.json` is included.

```bash
grep -n "location" frontend/nginx.conf
```

If a `location ~ \.(html|js|css)$` block exists that excludes JSON, add `|json` to the alternation.

- [ ] **Step 5: Commit**

```bash
git add frontend/i18n.js frontend/i18n/
git commit -m "feat(admin): add i18n runtime + empty en/ar translation files"
```

---

### Task 7: Landing `i18n.js` + skeleton translation files

**Files:**
- Create: `landing/i18n.js`, `landing/i18n/en.json`, `landing/i18n/ar.json`

- [ ] **Step 1: Copy admin's `i18n.js` to `landing/i18n.js`**

Same code as Task 6 Step 1. The runtime is identical.

```bash
cp frontend/i18n.js landing/i18n.js
```

- [ ] **Step 2: Create empty translation files**

```bash
echo '{}' > landing/i18n/en.json
echo '{}' > landing/i18n/ar.json
mkdir -p landing/i18n
```

- [ ] **Step 3: Verify nginx serves /i18n/*.json**

```bash
grep -n "location" landing/nginx.conf
```

Same check as Task 6 Step 4. Add JSON to the served extensions if needed.

- [ ] **Step 4: Commit**

```bash
git add landing/i18n.js landing/i18n/
git commit -m "feat(landing): add i18n runtime + empty en/ar translation files"
```

---

### Task 8: Player `i18n.js` + skeleton translation files

**Files:**
- Create: `player/i18n.js`, `player/i18n/en.json`, `player/i18n/ar.json`

- [ ] **Step 1: Copy admin's `i18n.js`**

```bash
cp frontend/i18n.js player/i18n.js
```

- [ ] **Step 2: Empty JSON files**

```bash
mkdir -p player/i18n
echo '{}' > player/i18n/en.json
echo '{}' > player/i18n/ar.json
```

- [ ] **Step 3: Verify `player/nginx.conf` serves /i18n/*.json**

```bash
grep -n "location" player/nginx.conf
```

- [ ] **Step 4: Commit**

```bash
git add player/i18n.js player/i18n/
git commit -m "feat(player): add i18n runtime + empty en/ar translation files"
```

---

### Task 9: `scripts/check_i18n.py` completeness checker

Fails CI if any AR key is missing or empty for a key present in EN. Useful for catching strings added in EN that nobody translated.

**Files:**
- Create: `scripts/check_i18n.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Verify en.json and ar.json for each app have matching keys with non-empty values.

Exits 0 if all good, 1 otherwise. Run from repo root:
    python3 scripts/check_i18n.py
"""
import json
import sys
from pathlib import Path

APPS = ["frontend", "landing", "player"]
ROOT = Path(__file__).resolve().parent.parent


def check_app(app: str) -> list[str]:
    en_path = ROOT / app / "i18n" / "en.json"
    ar_path = ROOT / app / "i18n" / "ar.json"
    if not en_path.exists():
        return [f"{app}: missing en.json"]
    if not ar_path.exists():
        return [f"{app}: missing ar.json"]
    en = json.loads(en_path.read_text())
    ar = json.loads(ar_path.read_text())
    errors: list[str] = []
    for key in en:
        if key not in ar:
            errors.append(f"{app}: key missing in ar.json: {key}")
        elif not str(ar[key]).strip():
            errors.append(f"{app}: key empty in ar.json: {key}")
    for key in ar:
        if key not in en:
            errors.append(f"{app}: key in ar.json but not en.json: {key}")
    return errors


def main() -> int:
    all_errors: list[str] = []
    for app in APPS:
        all_errors.extend(check_app(app))
    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        return 1
    print(f"i18n OK across {', '.join(APPS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable + run on empty files**

```bash
chmod +x scripts/check_i18n.py
python3 scripts/check_i18n.py
```

Expected: `i18n OK across frontend, landing, player` (both files are `{}` so trivially equal).

- [ ] **Step 3: Verify it actually catches gaps**

Test with a temp mismatch:
```bash
echo '{"foo": "hello"}' > frontend/i18n/en.json
python3 scripts/check_i18n.py
```

Expected exit 1, message `frontend: key missing in ar.json: foo`.

Restore:
```bash
echo '{}' > frontend/i18n/en.json
```

- [ ] **Step 4: Commit**

```bash
git add scripts/check_i18n.py
git commit -m "feat(scripts): check_i18n.py — diff EN/AR translation keys per app"
```

---

## Part 3 — Markup migration + boot wiring

### Task 10: Admin `index.html` — `data-i18n` attributes + language toggle + Arabic font

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/i18n/en.json` (populate keys for everything tagged)

- [ ] **Step 1: Add Arabic font `@font-face` (top of styles, or in `<head>` via `<link>`)**

Append to the top of `frontend/styles.css`:

```css
@font-face {
  font-family: "IBM Plex Sans Arabic";
  src: url("/assets/fonts/IBMPlexSansArabic-400.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0600-06FF, U+0750-077F, U+0870-088E, U+0890-08FF, U+FB50-FDFF, U+FE70-FEFF;
}
@font-face {
  font-family: "IBM Plex Sans Arabic";
  src: url("/assets/fonts/IBMPlexSansArabic-500.woff2") format("woff2");
  font-weight: 500;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0600-06FF, U+0750-077F, U+0870-088E, U+0890-08FF, U+FB50-FDFF, U+FE70-FEFF;
}
@font-face {
  font-family: "IBM Plex Sans Arabic";
  src: url("/assets/fonts/IBMPlexSansArabic-700.woff2") format("woff2");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0600-06FF, U+0750-077F, U+0870-088E, U+0890-08FF, U+FB50-FDFF, U+FE70-FEFF;
}
```

Place the woff2 files in `frontend/assets/fonts/`. Source: download from https://github.com/IBM/plex/tree/master/IBM-Plex-Sans-Arabic/web/woff2 (regular/medium/bold). If the repo doesn't already have an `assets/fonts/` directory, create it.

- [ ] **Step 2: Add `data-i18n` attributes to static markup**

Walk `frontend/index.html` from top to bottom. For each user-visible English string, replace:
```html
<button>Sign in</button>
```
with:
```html
<button data-i18n="auth.signin">Sign in</button>
```

Group keys by section:
- `auth.*` — signin, signup, email, password, business name, OTP, "create account", "have an account", verification labels
- `nav.*` — header tabs, top nav
- `dashboard.*` — plan card, trial countdown, screens used
- `screens.*` — list, add screen, pair screen, screen details
- `playlists.*` — playlist editor labels
- `media.*` — upload, library
- `users.*` — invite, role labels, user list
- `billing.*` — plan tiers, term toggle, pay button, history
- `meta.*` — page title

For attributes, use `data-i18n-placeholder`, `data-i18n-title`, `data-i18n-aria-label`. The English text inside the element stays as a fallback for crawlers / no-JS.

- [ ] **Step 3: Add language toggle in header**

Find the existing header markup (search for `header` and the brand mark `◠`). Add a toggle pill near the right edge:

```html
<button class="lang-toggle" id="langToggle" aria-label="Switch language">
  <span data-i18n="lang.toggle_label">عربي</span>
</button>
```

For LTR (lang=en) the button reads "عربي" (switch to Arabic). For RTL (lang=ar) it reads "EN" — the `lang.toggle_label` key has different values per locale.

In `frontend/styles.css`, append a basic style:

```css
.lang-toggle {
  background: var(--cream);
  color: var(--plum);
  border: 1px solid var(--peach);
  border-radius: 999px;
  padding: 4px 12px;
  font-family: var(--font-sans);
  font-size: 13px;
  cursor: pointer;
  margin-inline-start: 12px;
}
.lang-toggle:hover { background: var(--butter); }
```

- [ ] **Step 4: Populate `frontend/i18n/en.json`**

Open the file and write every key tagged in Steps 2 and 3, with the existing English string as the value. Example:

```json
{
  "meta.title": "Khanshoof",
  "lang.toggle_label": "عربي",
  "nav.dashboard": "Dashboard",
  "nav.screens": "Screens",
  "nav.playlists": "Playlists",
  "nav.media": "Media",
  "nav.users": "Users",
  "nav.billing": "Billing",
  "auth.signin": "Sign in",
  "auth.signup": "Create account",
  "auth.email": "Email",
  "auth.email_placeholder": "you@example.com",
  "auth.password": "Password",
  "auth.business_name": "Business name",
  "auth.business_name_placeholder": "Acme Coffee",
  "auth.send_code": "Send code",
  "auth.verify_code": "Verify code",
  "auth.otp_placeholder": "123456",
  "auth.have_account": "Already have an account? Sign in",
  "auth.need_account": "New here? Create account",
  ...
}
```

This file becomes the source of truth for every visible string. Aim for 100-200 keys covering the admin UI.

- [ ] **Step 5: Validate completeness**

```bash
grep -oE 'data-i18n[^=]*="[^"]+"' frontend/index.html | sort -u | wc -l
python3 -c "import json; print(len(json.load(open('frontend/i18n/en.json'))))"
```

The two numbers should match (give or take aria/title duplicates).

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/i18n/en.json frontend/assets/fonts/
git commit -m "feat(admin): mark up index.html with data-i18n attrs + en.json source of truth"
```

---

### Task 11: Admin `app.js` — boot wiring + dynamic strings + error mapper + toggle handler

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html` (add `<script type="module">` wiring)

- [ ] **Step 1: Wire boot order**

At the top of `frontend/app.js` (or the entry point that calls `boot()`), import the i18n helpers and run them before render:

```js
import {
  loadLocale, t, applyTranslations, setLocale, detectInitialLocale, currentLocale
} from "./i18n.js";

// expose for non-module call sites already in app.js
window.t = t;
window.setLocaleAndReload = (loc) => { setLocale(loc); location.reload(); };
```

If `app.js` is loaded as a non-module today (`<script src="app.js">`), change the tag in `index.html` to `<script type="module" src="app.js">`. ES modules are supported in all browsers we target.

In the existing `boot()` function (find it via `function boot(`), modify to:
```js
async function boot() {
  // existing pre-render setup ...
  let orgLocale;
  try {
    const me = await fetch("/auth/me", { headers: authHeader() });
    if (me.ok) orgLocale = (await me.json()).organization?.locale;
  } catch (e) { /* anon, fine */ }
  const locale = detectInitialLocale(orgLocale);
  await loadLocale(locale);
  applyTranslations(document);
  // existing post-locale setup ...
}
```

- [ ] **Step 2: Wire the toggle button**

After `boot()` completes (or in a DOMContentLoaded hook):
```js
document.getElementById("langToggle")?.addEventListener("click", async () => {
  const next = currentLocale() === "en" ? "ar" : "en";
  // logged-in admin? persist on org
  if (window.__SESSION_TOKEN__) {
    try {
      await fetch("/organizations/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: JSON.stringify({ locale: next }),
      });
    } catch (e) { /* fall through to cookie */ }
  }
  setLocale(next);
  location.reload();
});
```

Use whatever the existing app uses for the auth token (`localStorage.getItem("token")` or similar — search for `Authorization` headers in `app.js` and copy the pattern).

- [ ] **Step 3: Replace dynamic strings**

Search for hardcoded toast / modal / error messages in `app.js`:
```bash
grep -nE 'toast\(|alert\(|innerHTML.*[A-Z][a-z]+ [a-z]+|console\.error.*"[A-Z]' frontend/app.js | head -40
```

Each user-visible literal becomes `t('toast.something')`. Add the key to `en.json` with the exact original English. Examples:

```js
// before
toast("Plan limit reached");
// after
toast(t("toast.plan_limit"));
```

```js
// before
toast("Screen added");
// after
toast(t("toast.screen_added"));
```

Repeat for every literal English string used at runtime. Add each new key to `en.json`.

- [ ] **Step 4: Add error-code mapper**

Add a helper near the top of `app.js`:

```js
// Maps backend error codes -> i18n keys. Falls back to server's `message`
// (English) if the code is unknown.
function localizeError(detail) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && detail.code) {
    return t(`error.${detail.code}`, detail.message);
  }
  return "Something went wrong";
}
```

In every `fetch().then(r => ...)` block that handles errors, swap:
```js
// before
toast(body.detail || "Error");
// after
toast(localizeError(body.detail));
```

Then add the error keys to `en.json` mirroring the code names from Task 2:

```json
{
  "error.email_taken": "This email is already registered.",
  "error.invalid_email": "Please enter a valid email.",
  "error.password_too_short": "Password must be at least 8 characters.",
  "error.password_no_letter": "Password must include a letter.",
  "error.password_no_number": "Password must include a number.",
  "error.otp_cooldown": "Please wait a moment before requesting another code.",
  "error.otp_expired": "Your code expired. Request a new one.",
  "error.otp_attempts_exceeded": "Too many attempts. Request a new code.",
  "error.otp_incorrect": "That code is incorrect.",
  "error.no_pending_signup": "No pending signup for this email.",
  "error.invalid_verification_token": "Verification expired. Please restart.",
  "error.verification_token_expired": "Verification expired. Please restart.",
  "error.invalid_credentials": "Wrong email or password.",
  "error.invalid_current_password": "Current password is incorrect.",
  "error.username_taken": "That username is taken.",
  "error.invalid_role": "Invalid role.",
  "error.plan_limit": "You've reached your plan's screen limit. Upgrade to add more.",
  "error.invalid_locale": "Locale must be 'en' or 'ar'."
}
```

- [ ] **Step 5: Smoke locally**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Open `https://app.khanshoof.com` in a browser. Confirm:
- Page loads without console errors related to i18n.
- Toggle button is visible.
- Clicking toggle switches `<html lang dir>`.

(Arabic will render in font fallback for now since `ar.json` is empty — strings show their key names. Cosmetics fixed in Tasks 16 + 19.)

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html frontend/i18n/en.json
git commit -m "feat(admin): wire i18n boot order, toggle, error-code mapper"
```

---

### Task 12: Landing `index.html` — `data-i18n` + toggle + font + KWD pricing rebase

**Files:**
- Modify: `landing/index.html`, `landing/styles.css`
- Modify: `landing/i18n/en.json`
- New: `landing/assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2`

- [ ] **Step 1: Copy Arabic font into `landing/assets/fonts/`**

Same files as admin. Add the same three `@font-face` blocks to the top of `landing/styles.css` (copy from Task 10 Step 1).

- [ ] **Step 2: Mark up landing markup with `data-i18n`**

`landing/index.html` sections to tag (full content):
- nav (logo alt, links: features, how, pricing, faq)
- hero h1, hero subhead, primary CTA, secondary CTA, hero blurb
- features (4 feature cards: heading + body each)
- how-it-works (3 steps: heading + body each)
- pricing (5 tiers: name, price, features bullet list, CTA button)
- AI spotlight section
- FAQ (each question + answer)
- final CTA section
- footer (links, copyright)

Estimate: ~80-120 keys for landing.

Tag everything with `data-i18n="landing.<section>.<element>"`. Example:
```html
<h1 data-i18n="landing.hero.headline">Signage that just works.</h1>
<p data-i18n="landing.hero.subhead">Pastel-friendly screens for your shop, café, clinic.</p>
<a data-i18n="landing.hero.cta_primary" class="btn btn-primary" href="https://app.khanshoof.com/#signup">Start free trial</a>
```

- [ ] **Step 3: Apply KWD pricing**

If Task 5 Step 6 already updated the pricing markup, verify the strings are tagged with `data-i18n` keys (`landing.pricing.starter.amount`, etc.). Each pricing tier gets:
- `landing.pricing.<tier>.label` — "Starter"
- `landing.pricing.<tier>.amount` — "3 KWD"
- `landing.pricing.<tier>.usd_approx` — "≈ $9.80"
- `landing.pricing.<tier>.per` — "/ month"
- `landing.pricing.<tier>.cta` — "Start free trial"

- [ ] **Step 4: Add language toggle to nav**

In the nav block:
```html
<button class="lang-toggle" id="langToggle" aria-label="Switch language">
  <span data-i18n="lang.toggle_label">عربي</span>
</button>
```

Style identical to admin (copy CSS rule from Task 10 Step 3).

- [ ] **Step 5: Wire boot in `landing/app.js`**

```js
import { loadLocale, t, applyTranslations, setLocale, detectInitialLocale, currentLocale } from "./i18n.js";

(async function boot() {
  const locale = detectInitialLocale();
  await loadLocale(locale);
  applyTranslations(document);
  document.getElementById("langToggle")?.addEventListener("click", () => {
    setLocale(currentLocale() === "en" ? "ar" : "en");
    location.reload();
  });
})();
```

Update the `<script>` tag in `landing/index.html` to `type="module"`.

- [ ] **Step 6: Populate `landing/i18n/en.json`**

Write every key tagged in Steps 2-4 with the original English value. ~100 keys.

- [ ] **Step 7: Verify count**

```bash
grep -oE 'data-i18n[^=]*="[^"]+"' landing/index.html | sort -u | wc -l
python3 -c "import json; print(len(json.load(open('landing/i18n/en.json'))))"
```

Numbers should align (within +/- 5 for aria/title duplicates).

- [ ] **Step 8: Smoke**

```bash
docker-compose build landing && docker-compose up -d landing
```

Open `https://yalla.khanshoof.com`. Confirm pricing reads `3 KWD` etc., toggle works, no console errors.

- [ ] **Step 9: Commit**

```bash
git add landing/index.html landing/app.js landing/styles.css landing/i18n/en.json landing/assets/fonts/
git commit -m "feat(landing): data-i18n markup + language toggle + en source of truth"
```

---

### Task 13: Player `index.html` + `player.js` — markup + boot

Player has minimal chrome — pairing code screen, "no content yet" empty state, "offline" indicator. Maybe 10-15 keys.

**Files:**
- Modify: `player/index.html`, `player/styles.css`, `player/player.js`
- Modify: `player/i18n/en.json`
- New: `player/assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2`

- [ ] **Step 1: Copy fonts + add @font-face**

Same as Task 10 Step 1, applied to `player/styles.css`.

- [ ] **Step 2: Tag pairing screen + empty states**

Find the pairing UI markup. Tag the visible strings:
- `player.pair.title` — "Pair this screen"
- `player.pair.code_label` — "Code"
- `player.pair.qr_caption` — "Scan with your phone, or visit app.khanshoof.com/pair"
- `player.pair.waiting` — "Waiting for connection..."
- `player.empty.title` — "Nothing to play yet"
- `player.empty.body` — "Add media in the admin dashboard."
- `player.offline` — "Offline — last sync at {time}"

Walk `player/index.html` to find every literal English string and tag it.

- [ ] **Step 3: Wire boot in `player/player.js`**

```js
import { loadLocale, applyTranslations, detectInitialLocale } from "./i18n.js";

(async function bootI18n() {
  const locale = detectInitialLocale();
  await loadLocale(locale);
  applyTranslations(document);
})();
```

Update `<script>` tag in `player/index.html` to `type="module"`. Player has no toggle button — language follows the cookie set on landing/admin (shared across `*.khanshoof.com`).

- [ ] **Step 4: Populate `player/i18n/en.json`**

```json
{
  "meta.title": "Khanshoof Player",
  "player.pair.title": "Pair this screen",
  "player.pair.code_label": "Code",
  "player.pair.qr_caption": "Scan with your phone, or visit app.khanshoof.com/pair",
  "player.pair.waiting": "Waiting for connection...",
  "player.empty.title": "Nothing to play yet",
  "player.empty.body": "Add media in the admin dashboard.",
  "player.offline": "Offline"
}
```

- [ ] **Step 5: Smoke**

```bash
docker-compose build player && docker-compose up -d player
```

Open `https://play.khanshoof.com`. Pairing screen renders, no console errors.

- [ ] **Step 6: Commit**

```bash
git add player/index.html player/player.js player/styles.css player/i18n/en.json player/assets/fonts/
git commit -m "feat(player): data-i18n markup + boot wiring"
```

---

## Part 4 — RTL CSS sweep

### Task 14: Admin `styles.css` — physical → logical properties

Mechanical sweep. Use sed for the bulk substitutions, then visually review the file for places sed misses (e.g. shorthand `margin: 4px 8px 4px 12px;` keeps mixed semantics; convert to `margin-block: 4px; margin-inline: 12px 8px;`).

**Files:**
- Modify: `frontend/styles.css`

- [ ] **Step 1: Bulk substitutions via sed**

```bash
cd frontend
sed -i \
  -e 's/margin-left:/margin-inline-start:/g' \
  -e 's/margin-right:/margin-inline-end:/g' \
  -e 's/padding-left:/padding-inline-start:/g' \
  -e 's/padding-right:/padding-inline-end:/g' \
  -e 's/border-left:/border-inline-start:/g' \
  -e 's/border-right:/border-inline-end:/g' \
  -e 's/border-left-width:/border-inline-start-width:/g' \
  -e 's/border-right-width:/border-inline-end-width:/g' \
  -e 's/border-left-color:/border-inline-start-color:/g' \
  -e 's/border-right-color:/border-inline-end-color:/g' \
  -e 's/border-left-style:/border-inline-start-style:/g' \
  -e 's/border-right-style:/border-inline-end-style:/g' \
  -e 's/border-top-left-radius:/border-start-start-radius:/g' \
  -e 's/border-top-right-radius:/border-start-end-radius:/g' \
  -e 's/border-bottom-left-radius:/border-end-start-radius:/g' \
  -e 's/border-bottom-right-radius:/border-end-end-radius:/g' \
  -e 's/text-align: left;/text-align: start;/g' \
  -e 's/text-align: right;/text-align: end;/g' \
  -e 's/float: left;/float: inline-start;/g' \
  -e 's/float: right;/float: inline-end;/g' \
  styles.css
cd ..
```

- [ ] **Step 2: Manual review for `left:` / `right:` (positioned elements)**

These need attention because `left:` is also a property name on positioned elements:
```bash
grep -nE '^\s*(left|right):' frontend/styles.css
```

For each hit, replace:
- `left: <val>` → `inset-inline-start: <val>`
- `right: <val>` → `inset-inline-end: <val>`

Skip cases where the rule is intentionally physical (e.g. a debug overlay pinned to the corner regardless of language).

- [ ] **Step 3: Add `:lang(ar)` font swap**

At the top of `styles.css` (or in `:root`), keep existing:
```css
:root {
  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Serif", Georgia, serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
}
```

Add immediately after:
```css
:lang(ar) {
  --font-sans: "IBM Plex Sans Arabic", "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Sans Arabic", "IBM Plex Serif", Georgia, serif;
}
```

- [ ] **Step 4: Add direction-aware overrides**

```css
/* Direction-bearing icons flip in RTL */
[dir="rtl"] .icon-chevron-right,
[dir="rtl"] .icon-chevron-left,
[dir="rtl"] .icon-arrow-right,
[dir="rtl"] .icon-arrow-left {
  transform: scaleX(-1);
}

/* Force LTR on URLs, IDs, code, monospace inputs */
input[type="url"],
input[type="email"],
input[name*="token"],
input[name*="id"],
code, kbd, samp, pre,
.mono, .monospace {
  direction: ltr;
  text-align: start;
}
```

(Adjust the selectors to whatever icon classes actually exist in the codebase — search for `class="icon-` to find them.)

- [ ] **Step 5: Smoke locally**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Open `https://app.khanshoof.com` and append `?lang=ar` won't work because the cookie hasn't been set; instead set the cookie via DevTools, then reload, OR click the toggle. Confirm:
- Layout flips (panels, headers, list items align right).
- No element is dramatically broken (overlapping, off-screen).
- Font subtly changes if Arabic content is present.

Visual issues are expected in this round — fix obvious overlaps inline; defer cosmetic polish.

- [ ] **Step 6: Commit**

```bash
git add frontend/styles.css
git commit -m "style(admin): logical CSS properties + :lang(ar) font swap + direction overrides"
```

---

### Task 15: Landing `styles.css` — same sweep

**Files:**
- Modify: `landing/styles.css`

- [ ] **Step 1: Run the same sed substitution**

Identical command as Task 14 Step 1, applied in `landing/`.

- [ ] **Step 2: Manual `left:` / `right:` review**

```bash
grep -nE '^\s*(left|right):' landing/styles.css
```

Same treatment.

- [ ] **Step 3: Add `:lang(ar)` font swap + direction-aware rules**

Copy the CSS blocks from Task 14 Steps 3 and 4 into `landing/styles.css`. Adjust icon class names to what landing actually uses.

- [ ] **Step 4: Smoke**

```bash
docker-compose build landing && docker-compose up -d landing
```

Open landing, set `khanshoof_lang=ar` cookie via DevTools (domain `.khanshoof.com`, path `/`), reload. Confirm RTL flip, hero/pricing align right, no breakage.

- [ ] **Step 5: Commit**

```bash
git add landing/styles.css
git commit -m "style(landing): logical CSS + :lang(ar) font swap"
```

---

### Task 16: Player `styles.css` — same sweep

**Files:**
- Modify: `player/styles.css`

- [ ] **Step 1: sed sweep**

Same command as Task 14 Step 1, in `player/`.

- [ ] **Step 2: Manual review**

```bash
grep -nE '^\s*(left|right):' player/styles.css
```

- [ ] **Step 3: Font + direction rules**

Copy from Task 14.

- [ ] **Step 4: Smoke**

```bash
docker-compose build player && docker-compose up -d player
```

Set cookie, open player. Pairing screen flips correctly.

- [ ] **Step 5: Commit**

```bash
git add player/styles.css
git commit -m "style(player): logical CSS + :lang(ar) font swap"
```

---

## Part 5 — Translation drafting + ship

### Task 17: Draft Arabic translations

This is the creative content task. Translate every key from `en.json` into MSA (Modern Standard Arabic) for UI/system/errors, with Kuwaiti dialect injected on the playful landing surfaces (hero, CTAs, FAQ tone, mascot captions).

**Files:**
- Modify: `frontend/i18n/ar.json`, `landing/i18n/ar.json`, `player/i18n/ar.json`

- [ ] **Step 1: Translate `frontend/i18n/ar.json` (admin — MSA throughout)**

Open `frontend/i18n/en.json`, copy keys, translate values. Tone: clear, professional, neutral MSA. Examples:

```json
{
  "meta.title": "خنشوف",
  "lang.toggle_label": "EN",
  "nav.dashboard": "اللوحة",
  "nav.screens": "الشاشات",
  "nav.playlists": "قوائم التشغيل",
  "nav.media": "الوسائط",
  "nav.users": "المستخدمون",
  "nav.billing": "الفواتير",
  "auth.signin": "تسجيل الدخول",
  "auth.signup": "إنشاء حساب",
  "auth.email": "البريد الإلكتروني",
  "auth.password": "كلمة المرور",
  "auth.business_name": "اسم المؤسسة",
  "error.email_taken": "هذا البريد مسجّل مسبقاً.",
  "error.invalid_credentials": "البريد أو كلمة المرور غير صحيحة.",
  "toast.plan_limit": "وصلت للحد الأقصى للشاشات في باقتك."
}
```

Write Arabic for every key. ~150-200 strings.

- [ ] **Step 2: Translate `landing/i18n/ar.json` (MSA + Kuwaiti microcopy)**

UI elements (nav, footer, form labels): MSA.

Playful surfaces (hero headline, CTAs, FAQ tone, mascot captions): Kuwaiti dialect — examples of permissible flavor:
- Hero: "خنشوف! شاشات شغّالة بدون وجع راس." (instead of formal MSA "شاشات تعمل بسهولة")
- CTA: "ابدأ تجربتك المجانية" (MSA — keep formal for action buttons)
- FAQ: tone leans casual ("بشلون يشتغل؟" Kuwaiti for "How does it work?")
- Pricing: MSA, since money talk should feel professional.

Annotate the file with `// COLLOQUIAL` comments where dialect is intentional, so the user can spot-check during review.

JSON doesn't support comments — instead, prefix Kuwaiti keys with `_kw_` in a sibling key for the user's review aid, OR create a separate notes section. Simpler: maintain a brief `landing/i18n/AR_TRANSLATION_NOTES.md` flagging which sections used dialect.

```bash
cat > landing/i18n/AR_TRANSLATION_NOTES.md <<'EOF'
# Arabic translation notes

- Hero (`landing.hero.*`) — Kuwaiti dialect for energy.
- FAQ questions (`landing.faq.q*`) — leans Kuwaiti casual.
- Mascot caption (`landing.mascot.caption`) — Kuwaiti.
- Everything else — MSA.

Dialect words used:
- بشلون (kw) → كيف (msa) — "how"
- شغّال (kw) → يعمل (msa) — "working / works"
- مالها (kw) → ليس لها (msa) — "doesn't have"
EOF
```

- [ ] **Step 3: Translate `player/i18n/ar.json`**

Small, all MSA:
```json
{
  "meta.title": "خنشوف",
  "player.pair.title": "اقران الشاشة",
  "player.pair.code_label": "الرمز",
  "player.pair.qr_caption": "امسح الرمز بهاتفك أو افتح app.khanshoof.com/pair",
  "player.pair.waiting": "جارٍ الاتصال...",
  "player.empty.title": "لا يوجد محتوى بعد",
  "player.empty.body": "أضف الوسائط من لوحة الإدارة.",
  "player.offline": "غير متصل"
}
```

- [ ] **Step 4: Run completeness checker**

```bash
python3 scripts/check_i18n.py
```

Expected: `i18n OK across frontend, landing, player`. If any key is missing or empty, add it.

- [ ] **Step 5: Commit**

```bash
git add frontend/i18n/ar.json landing/i18n/ar.json landing/i18n/AR_TRANSLATION_NOTES.md player/i18n/ar.json
git commit -m "feat(i18n): Arabic translations — MSA for UI, Kuwaiti dialect for landing playful surfaces"
```

---

### Task 18: Final smoke + ship

**Files:** none (deploy + manual QA)

- [ ] **Step 1: Rebuild all images**

```bash
docker-compose build backend frontend landing player
docker-compose up -d
sleep 10
```

- [ ] **Step 2: Verify backend live**

```bash
curl -fsS https://api.khanshoof.com/health
curl -fsS https://api.khanshoof.com/plans | python3 -m json.tool | head -20
```

Expected: `{"status":"ok"}` and plans list with `price_kwd_monthly`.

- [ ] **Step 3: Manual QA — landing**

Open `https://yalla.khanshoof.com` in a browser:
- [ ] EN renders correctly, hero readable, pricing shows "3 KWD" with "≈ $9.80" below.
- [ ] Click language toggle.
- [ ] AR renders, `<html lang="ar" dir="rtl">`, layout flipped, IBM Plex Sans Arabic loaded (DevTools → Computed → font-family).
- [ ] Hero microcopy reads in Kuwaiti dialect.
- [ ] FAQ tone is dialect.
- [ ] Pricing reads "٣ د.ك" or "3 KWD" (per spec — Latin digits OK; either is fine if consistent).
- [ ] CTA buttons go to `https://app.khanshoof.com/#signup`.

- [ ] **Step 4: Manual QA — admin**

Open `https://app.khanshoof.com`:
- [ ] EN signup form renders.
- [ ] Sign up a fresh org.
- [ ] Logged in: header shows toggle.
- [ ] Click toggle → AR renders, layout flipped.
- [ ] Reload — still AR (org locale persisted).
- [ ] Open new private window → `https://app.khanshoof.com` → cookie carries over from .khanshoof.com domain → toggles correctly.
- [ ] Trigger a deliberate error (signup with existing email) → toast shows Arabic message via error code mapping.

- [ ] **Step 5: Manual QA — player**

Open `https://play.khanshoof.com`:
- [ ] EN pairing screen renders (cookie cleared first via DevTools).
- [ ] Set cookie to `ar` → reload → Arabic pairing screen.
- [ ] Pair a screen end-to-end (request code, claim from admin) — confirm flow still works.

- [ ] **Step 6: Run translation completeness one more time**

```bash
python3 scripts/check_i18n.py
```

Expected: OK.

- [ ] **Step 7: Run full backend pytest**

```bash
docker exec signage_backend_1 pytest 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 8: Final commit (any cosmetic fixes)**

If QA turned up minor issues (overflowing buttons, mistranslated string), fix and commit:

```bash
git add -A
git commit -m "fix(i18n): RTL polish + translation tweaks from QA"
```

- [ ] **Step 9: Tag and notify**

```bash
git log --oneline main..HEAD | head -25
```

Verify the commit chain looks clean — one commit per task, no fixup-style noise.

---

## Self-Review Notes

- **Spec coverage:** Each section of the spec maps to tasks: §3.1-3.4 → T6/T7/T8, §3.5-3.6 → T10-13, §3.7 → T11/T12, §4 → T1/T3/T4, §5 → T14/T15/T16, §6 → T5, §7 file list → covered, §8 testing → distributed across T2/T3/T4 + T18 manual.
- **Placeholder scan:** No "TBD" / "implement later" present. The Arabic translation drafts are exemplary not exhaustive — the executor fills in the 200+ keys; this is acknowledged work, not a placeholder.
- **Type consistency:** `http_error()` signature is consistent across T1/T2/T3. `loadLocale`/`t`/`applyTranslations` signatures match across i18n.js usage.
- **Scope check:** Single feature (bilingual + RTL) with one piggybacked concern (pricing rebase, justified in §6.0 because translation strings would otherwise be written twice).

End of plan.
