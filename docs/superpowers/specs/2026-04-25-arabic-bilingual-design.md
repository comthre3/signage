# Arabic / RTL Bilingual Design

**Date:** 2026-04-25
**Status:** Approved (pending user spec review)
**Scope:** EN ↔ AR across all three frontends (admin, landing, player) with full RTL flip, Kuwaiti-dialect microcopy on landing playful surfaces, MSA elsewhere. Bundles a pricing ladder switch to integer KWD (USD shown as conversion).

---

## 1. Goals

- Khanshoof becomes a real bilingual product — every visible string in EN and AR.
- Layout flips for Arabic (`dir="rtl"`), font swaps to IBM Plex Sans Arabic.
- Logged-in users get the org's saved locale automatically; anonymous visitors get an `Accept-Language`-detected locale with a manual override cookie.
- Pricing presents in clean integer KWD as the primary currency (Kuwaiti audience), with USD shown as the conversion.
- No new build steps, no new runtime dependencies. Project ethos: plain HTML/JS served by nginx.

## 2. Non-goals

- Per-user (vs per-org) language preference — Phase 2.
- ICU pluralization, gender agreement, complex Arabic morphology helpers — current copy doesn't need it.
- URL-prefixed locale routes (`/ar/`, `/en/`) — single SPA at `/`, locale lives in cookie + DB.
- Visual regression / Playwright suite — manual smoke for this round.
- Translating server-side log messages or backend stack traces — English only, frontend maps error codes to localized strings.
- Arabic-Indic numerals in pricing/IDs — Latin digits stay (audience reads `15 KWD` natively; Arabic-Indic would feel academic).
- KNET / Stripe billing rewrite — pricing constants and display change only; payment provider integration stays as-is in this round.

## 3. Architecture

### 3.1 Locale source of truth

| Surface | Source (in order) |
|---|---|
| Admin (logged-in) | `organizations.locale` → cookie → `Accept-Language` → `'en'` |
| Admin (signup, login) | cookie → `Accept-Language` → `'en'` |
| Landing | cookie → `Accept-Language` → `'en'` |
| Player (paired) | cookie → `'en'` (player chrome is minimal, falls back to org locale once paired in a future round) |
| Player (pairing screen) | cookie → `'en'` |

**Allowed locales:** `'en'`, `'ar'`. Anything else falls through to `'en'`.

### 3.2 Cookie

- Name: `khanshoof_lang`
- Domain: `.khanshoof.com` (shared across `app.`, `play.`, `yalla.`, `api.`)
- Path: `/`
- Max-Age: 1 year
- SameSite: Lax
- Not HttpOnly — JS sets it via toggle.

### 3.3 Translation files

Per app, two files:

```
frontend/i18n/en.json
frontend/i18n/ar.json
landing/i18n/en.json
landing/i18n/ar.json
player/i18n/en.json
player/i18n/ar.json
```

Flat keys with namespacing dots:
```json
{
  "auth.signin": "Sign in",
  "auth.email": "Email",
  "plan.starter.label": "Starter",
  "plan.starter.price_kwd": "3 KWD",
  "plan.starter.price_usd_approx": "≈ $9.80",
  "toast.plan_limit": "Plan limit reached",
  "error.email_taken": "This email is already registered."
}
```

No nested objects. Diffing keys across locales is a flat set comparison.

### 3.4 Tiny i18n helper (per app)

`frontend/i18n.js` (and identical-shape copies in `landing/`, `player/`):

```js
let _strings = {};
let _locale = 'en';

export async function loadLocale(locale) {
  const res = await fetch(`/i18n/${locale}.json?v=${window.__BUILD_HASH__ || ''}`);
  _strings = await res.json();
  _locale = locale;
  document.documentElement.lang = locale;
  document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr';
}

export function t(key, fallback) {
  const v = _strings[key];
  if (v == null) {
    if (typeof console !== 'undefined') console.warn('[i18n] missing key:', key);
    return fallback ?? key;
  }
  return v;
}

export function applyTranslations(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  root.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
    el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
  });
}

export function setLocale(locale) {
  if (locale !== 'en' && locale !== 'ar') locale = 'en';
  document.cookie = `khanshoof_lang=${locale}; domain=.khanshoof.com; path=/; max-age=31536000; samesite=lax`;
  return locale;
}

export function detectInitialLocale(orgLocale) {
  if (orgLocale === 'en' || orgLocale === 'ar') return orgLocale;
  const cookie = document.cookie.match(/(?:^|; )khanshoof_lang=(en|ar)\b/);
  if (cookie) return cookie[1];
  const accept = (navigator.language || 'en').slice(0, 2).toLowerCase();
  return accept === 'ar' ? 'ar' : 'en';
}
```

~50 LOC. Same shape across all three apps; player can be 30 LOC (no signed-in flow).

### 3.5 HTML markup convention

Static strings get `data-i18n` attributes. The helper walks the DOM after `loadLocale` resolves:

```html
<button data-i18n="auth.signin">Sign in</button>
<input data-i18n-placeholder="auth.email" placeholder="Email">
<a data-i18n="nav.pricing" href="#pricing">Pricing</a>
<title data-i18n="meta.title">Khanshoof</title>
```

Dynamic strings (toasts, error messages, modals built in JS) call `t('toast.plan_limit')` directly.

### 3.6 Boot flow

Each app's entry point becomes:

```js
import { loadLocale, applyTranslations, detectInitialLocale } from './i18n.js';

(async function boot() {
  // org locale arrives from /auth/me on admin, undefined elsewhere
  const initial = detectInitialLocale(window.__ORG_LOCALE__);
  await loadLocale(initial);
  applyTranslations(document);
  // ... existing boot continues
})();
```

For admin specifically: after a successful `/auth/login` or `/auth/me`, the response includes `organization.locale`. If that differs from current, call `loadLocale(orgLocale)` again and `applyTranslations` to re-render.

### 3.7 Switcher UI

Small pill in the header on each surface:

```
[ EN | عربي ]
```

- Anonymous (landing, signup, login, pairing): clicking writes the cookie and reloads.
- Logged-in admin: clicking calls `PATCH /organizations/me {locale: "ar"}`, then writes cookie, then reloads.
- Reload (vs live swap) is the contract — translations apply on next render. Avoids stale state in hand-rolled DOM, keeps i18n.js trivial.

## 4. Backend changes

### 4.1 New endpoint

`PATCH /organizations/me`

- **Auth:** session token, role `admin` only (not editor/viewer).
- **Body:** `{"locale": "en" | "ar"}`
- **Response:** sanitized organization row.
- **Errors:**
  - 400 `{"code": "invalid_locale", "message": "Locale must be 'en' or 'ar'"}`
  - 403 if role < admin

### 4.2 Augment `/auth/me` and `/auth/login`

Add `locale` to the `organization` block of the response (currently exists in DB, not yet returned). One-line change to the existing serializer.

### 4.3 Error code field

Refactor `HTTPException(detail="...")` sites in `main.py` so the response body is `{"code": "snake_case", "message": "English fallback"}` instead of a bare string. Frontend maps `code` → `t('error.<code>')`. If frontend doesn't recognize the code, falls back to the English `message`.

Sites to update (rough count from prior `grep`): ~15 in `main.py`. New helper:

```python
def http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})
```

Existing tests assert on `response.json()["detail"]` (a string). They'll need to assert on `response.json()["detail"]["code"]` instead. Backward-compatible escape hatch is not added — error response shape changes, all callers (one frontend) update in lockstep.

### 4.4 No `Accept-Language` parsing yet

No backend endpoint currently renders user-facing localized strings (errors are codes, not translated). `accept_language()` parser is deferred until we have a use case.

**Known gap — OTP email:** the signup OTP email sent via Resend stays English-only this round. A user who picks AR on the signup form will still get an English email. Acknowledged trade-off; localized email templates land in a follow-up plan. Rationale: email content is short ("Your code is 123456"), the OTP itself is digits, and the email arrives within seconds of the user picking AR — context-loss is small. Tracked in §10.

## 5. RTL CSS strategy

### 5.1 Logical properties sweep

Mechanical replacement across `frontend/styles.css`, `landing/styles.css`, `player/styles.css`:

| Physical | Logical |
|---|---|
| `margin-left` / `margin-right` | `margin-inline-start` / `margin-inline-end` |
| `padding-left` / `padding-right` | `padding-inline-start` / `padding-inline-end` |
| `left:` / `right:` | `inset-inline-start:` / `inset-inline-end:` |
| `border-left*` / `border-right*` | `border-inline-start*` / `border-inline-end*` |
| `border-top-left-radius` / `border-top-right-radius` | `border-start-start-radius` / `border-start-end-radius` |
| `text-align: left` / `right` | `text-align: start` / `end` |
| `float: left` / `right` | `float: inline-start` / `inline-end` |

Flexbox/grid mirror automatically when `dir="rtl"`. No changes to flex-direction or grid-template-columns.

### 5.2 Surgical exceptions kept physical

- **Direction-bearing icons** (chevrons, "next" arrows, play arrow): scoped flip
  ```css
  [dir="rtl"] .icon-arrow,
  [dir="rtl"] .icon-chevron { transform: scaleX(-1); }
  ```
- **Mascot pixel-art faces**: never flipped.
- **Logo wordmark "Khanshoof"**: never flipped (always LTR Latin).
- **Code/monospace inputs, URLs, IDs**: `dir="ltr"` forced on the input element.
- **Mixed content** (Khanshoof brand inside an Arabic sentence in JSON values): wrap in `<bdi>` or `<span dir="ltr">` inside the JSON value itself.

### 5.3 Font stack

```css
:root {
  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Serif", Georgia, serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
}
:lang(ar) {
  --font-sans: "IBM Plex Sans Arabic", "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Sans Arabic", "IBM Plex Serif", Georgia, serif;
}
```

IBM Plex Sans Arabic covers display and body for AR (no separate Arabic serif in the superfamily). Latin digits stay Latin.

### 5.4 Font loading

Per app, add three `@font-face` rules (weights 400/500/700) for IBM Plex Sans Arabic woff2, gated via `unicode-range` to the Arabic block. EN visitors don't download the file. Files self-hosted in each app's `assets/fonts/` (consistent with existing IBM Plex Sans deployment).

Estimated payload: ~80KB per app gz'd, only fetched when `:lang(ar)` matches.

## 6. Pricing ladder change

Bundled into this round so AR/EN strings are written once with the new numbers.

### 6.1 New ladder

| Tier | KWD (primary) | USD (~ at 0.306) | Screens |
|---|---|---|---|
| Starter | 3 KWD | ≈ $9.80 | up to 3 |
| Growth | 4 KWD | ≈ $13.07 | up to 5 |
| Business | 8 KWD | ≈ $26.14 | up to 10 |
| Pro | 15 KWD | ≈ $49.02 | up to 25 |
| Enterprise | Contact us | Contact us | 25+ |

### 6.2 Backend changes

- `backend/main.py` `PLANS` dict: replace `price_usd_monthly` with `price_kwd_monthly` (Decimal int) and a derived `price_usd_approx` (string for display).
- `backend/main.py` `PLAN_PRICING_USD` Decimal map: rename to `PLAN_PRICING_KWD`, values become integer Decimals (`Decimal("3")`, etc.). Existing `USD_TO_KWD` constant is repurposed as `KWD_TO_USD = Decimal("3.267")` (1/0.306) for display.
- `backend/billing.py` (KNET amounts in fils): downstream of `PLAN_PRICING_KWD` × 1000. No code change beyond reading the renamed constant.
- `tests/test_billing.py`: update assertion fixtures to the new amounts.

### 6.3 Frontend changes

- `landing/index.html` pricing section: rewritten to show KWD primary, USD approx secondary. Both EN and AR JSON files carry the localized labels.
- `frontend/index.html` plan card: same treatment.

## 7. File-by-file change list

```
backend/
  main.py                       # http_error helper, error-code refactor, PATCH /organizations/me,
                                # /auth/me + /auth/login include org.locale, PLANS rewrite
  billing.py                    # rename usage of pricing constant
  tests/test_organization_locale.py   # NEW: PATCH /organizations/me tests
  tests/test_billing.py         # update price assertions
  tests/test_signup_otp.py      # update error-shape assertions to {code, message}
  tests/test_pairing.py         # update error-shape assertions
  tests/test_plan_limits.py     # update error-shape assertions

frontend/
  i18n.js                       # NEW
  i18n/en.json                  # NEW
  i18n/ar.json                  # NEW
  index.html                    # add data-i18n attributes, language toggle in header
  app.js                        # boot order: detectInitialLocale → loadLocale → applyTranslations,
                                # toast/modal calls switch to t(),
                                # error-handler reads {code} and resolves t('error.<code>')
  styles.css                    # logical-properties sweep, :lang(ar) font swap, RTL exceptions
  assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2   # NEW

landing/
  i18n.js                       # NEW (same shape)
  i18n/en.json                  # NEW
  i18n/ar.json                  # NEW
  index.html                    # data-i18n attributes, language toggle in nav, pricing rewrite
  app.js                        # boot order updates
  styles.css                    # logical-properties sweep, font swap
  assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2   # NEW

player/
  i18n.js                       # NEW (smaller, no signed-in flow)
  i18n/en.json                  # NEW (~10 keys: pairing, "no content", "offline")
  i18n/ar.json                  # NEW
  index.html                    # data-i18n attributes
  player.js                     # boot order updates
  styles.css                    # logical-properties sweep
  assets/fonts/IBMPlexSansArabic-{400,500,700}.woff2   # NEW

scripts/
  check_i18n.py                 # NEW: diffs keys between en.json and ar.json per app, fails on mismatch
```

## 8. Testing

- **Backend:** pytest additions:
  - `PATCH /organizations/me` happy path (admin, `ar`).
  - `PATCH /organizations/me` rejects non-admin (403).
  - `PATCH /organizations/me` rejects invalid locale (400 with `code: "invalid_locale"`).
  - `/auth/me` response includes `organization.locale`.
  - `/auth/login` response includes `organization.locale`.
- **Backend regression:** all existing tests stay green after error-shape refactor.
- **Frontend manual smoke:**
  - Visit `https://yalla.khanshoof.com`, toggle to AR, confirm `dir="rtl"`, hero reads in Arabic, fonts swapped.
  - Visit `https://app.khanshoof.com`, sign up new org, confirm fresh signup defaults to current cookie locale.
  - Sign in, toggle to AR via header, refresh — confirm persists. Open new tab — still AR.
  - Sign in as a non-admin (editor) → toggle hidden / disabled (or returns 403 gracefully).
  - Visit `https://play.khanshoof.com`, toggle pairing screen between EN and AR.
- **i18n completeness:** `python scripts/check_i18n.py` exits non-zero if any AR key is missing or empty.

## 9. Open items resolved during brainstorm

1. Scope: all three apps (admin + landing + player). ✓
2. Locale storage: `organizations.locale` for logged-in, cookie for anon. ✓
3. Translation storage: per-app JSON, no library, no build step. ✓
4. RTL technique: `dir="rtl"` + logical CSS properties + `:lang(ar)` font swap. ✓
5. Translation source: I draft all strings; MSA for UI/system/errors, Kuwaiti dialect for landing playful microcopy. ✓
6. Pricing: bundled in this round, integer KWD primary, USD as conversion. ✓

## 10. Open items still TBD (to be picked up by the implementation plan)

- Exact toggle visual (pastel pill, segmented control, tiny "EN | عربي" link?) — designer discretion at implementation time, must work in both LTR and RTL.
- Whether admin player roster uses LTR-forced screen names (since users may name screens in English even with locale=ar). Default: keep input as `dir="auto"` so the browser decides per string.
- Whether language toggle in admin is admin-role-only (matches the PATCH endpoint) or any-role with a backend ignore for non-admins. Default: gate in UI to match endpoint, fail loud rather than silent.
- **Localized signup OTP email** (Resend template per locale). Follow-up plan; out of scope here.
- **Mid-form locale switch** wipes unsaved input on reload. Acceptable for this round (toggle is rare, expected pre-form). Revisit if support tickets mention it.
- **Local dev cookie domain:** `.khanshoof.com` won't bind on `localhost`. Helper falls back to setting cookie without `domain=` attribute when `location.hostname` doesn't end in `khanshoof.com`. Implementation detail for the plan.
