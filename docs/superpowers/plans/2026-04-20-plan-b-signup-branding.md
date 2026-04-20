# Plan B — Frontend Signup + Sawwii Branding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a self-serve signup flow and the Sawwii rebrand on the admin SPA so a new business owner can create an org + 14-day Starter trial from the browser, land on a rebranded dashboard, and see their plan status.

**Architecture:** Extend the existing vanilla-JS SPA (`frontend/index.html` + `app.js` + `styles.css`). The signup endpoint (`POST /auth/signup`) is already shipped in `backend/main.py:450` and returns the same `{token, user}` shape as `/auth/login` plus an `organization` block; the frontend only needs a new form, a Sign In ⇄ Create Account tab toggle, and a dashboard plan-status card that reads `GET /organization`. No backend changes. No build step (static assets).

**Tech Stack:** HTML5, vanilla JS (ES2022, no framework), plain CSS using existing CSS variables in `styles.css`, nginx serving static files from `frontend/`.

---

## Prerequisites

Work from `/home/ahmed/signage` on branch `feature/phase1-lockdown`. Phase 1 backend must already be committed (organizations table, `/auth/signup` endpoint, `/organization` endpoint). Verify with:

```bash
git -C /home/ahmed/signage rev-parse --abbrev-ref HEAD   # → feature/phase1-lockdown
grep -n '"/auth/signup"' backend/main.py                  # → line ~450
```

Docker stack must be up for smoke tests. Rebuild the admin frontend image after file changes — `frontend/` is baked into the image:

```bash
docker-compose build admin && docker-compose up -d admin
```

## File Structure

Three files in `frontend/` are touched. No new files are created; everything is additive or a text swap.

- **`frontend/index.html`** — Change page `<title>`, change header `<h1>`, extend `<section id="auth-panel">` with a Sign In ⇄ Create Account tab strip and a signup form, add a "Your plan" card inside `<div id="dashboard">` above the `#sites` section.
- **`frontend/app.js`** — Add handlers for the new signup form, the tab toggle, and a `loadOrganization()` + `renderPlanCard()` pair called from `bootData()`.
- **`frontend/styles.css`** — Add rules for `.auth-tabs`, `.auth-tab`, `#signup-form`, `.plan-card`, and the plan-status pill. Reuse existing vars (`--cyan`, `--r-md`, etc.) — no new CSS variables.

Each file has one clear responsibility: HTML is structure, JS is behaviour, CSS is presentation. No test files are created — frontend has no test harness in this repo, so verification is curl + browser smoke tests (codified at the end of each task).

---

## Task 1: Rebrand "Signage Admin" → "Sawwii"

**Files:**
- Modify: `frontend/index.html:6` (page `<title>`)
- Modify: `frontend/index.html:17` (header `<h1>`)

- [ ] **Step 1: Change the page title**

Replace the existing `<title>` line at `frontend/index.html:6`:

```html
    <title>Signage Admin</title>
```

with:

```html
    <title>Sawwii</title>
```

- [ ] **Step 2: Change the header heading**

Replace the existing `<h1>` line at `frontend/index.html:17`:

```html
        <h1>◈ Signage Admin</h1>
```

with:

```html
        <h1>Sawwii</h1>
```

The header gradient styling in `styles.css` already colour-maps any text inside `header h1` via `-webkit-background-clip: text`, so "Sawwii" will inherit the cyan→indigo gradient with no CSS change.

- [ ] **Step 3: Verify no other "Signage Admin" strings remain**

Run (from `/home/ahmed/signage`):

```bash
grep -rn "Signage Admin\|◈ Signage" frontend/
```

Expected output: **empty**. If any matches remain in `frontend/`, update them to "Sawwii" (leave `backend/` and `player/` strings alone — those are out of scope for this plan).

- [ ] **Step 4: Rebuild and sanity-check the admin container**

```bash
docker-compose build admin && docker-compose up -d admin
curl -s http://192.168.18.192:3000/ | grep -E '<title>|<h1>'
```

Expected: the grep shows `<title>Sawwii</title>` and a line containing `<h1>Sawwii</h1>`.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): rebrand header and page title to Sawwii"
```

---

## Task 2: Extend the auth panel with Sign In ⇄ Create Account tabs and a signup form

**Files:**
- Modify: `frontend/index.html:39-49` (the `<section id="auth-panel">` block)

- [ ] **Step 1: Replace the auth panel markup**

Replace the entire existing block at `frontend/index.html:39-49`:

```html
      <section id="auth-panel" class="panel">
        <h2>Sign In</h2>
        <form id="login-form">
          <input type="text" id="login-username" placeholder="Username" required autocomplete="username" />
          <input type="password" id="login-password" placeholder="Password" required autocomplete="current-password" />
          <button type="submit">Sign In</button>
        </form>
        <div class="helper-text">
          Default: <strong>admin</strong> / <strong>admin123</strong> — change after first login.
        </div>
      </section>
```

with:

```html
      <section id="auth-panel" class="panel">
        <div class="auth-tabs" role="tablist">
          <button type="button" class="auth-tab active" id="auth-tab-login"  role="tab" aria-selected="true">Sign In</button>
          <button type="button" class="auth-tab"        id="auth-tab-signup" role="tab" aria-selected="false">Create Account</button>
        </div>

        <form id="login-form" class="auth-form">
          <input type="text"     id="login-username" placeholder="Email or username" required autocomplete="username" />
          <input type="password" id="login-password" placeholder="Password"           required autocomplete="current-password" />
          <button type="submit">Sign In</button>
          <div class="helper-text">
            New here? Click <strong>Create Account</strong> to start a 14-day free trial — no card required.
          </div>
        </form>

        <form id="signup-form" class="auth-form hidden">
          <input type="text"     id="signup-business" placeholder="Business name"            required maxlength="100" autocomplete="organization" />
          <input type="email"    id="signup-email"    placeholder="Work email"               required autocomplete="email" />
          <input type="password" id="signup-password" placeholder="Password (min 8, letters + numbers)" required autocomplete="new-password" minlength="8" />
          <button type="submit">Start Free Trial</button>
          <div class="helper-text">
            You'll get the <strong>Starter</strong> plan (up to 3 screens) free for 14 days. Cancel anytime.
          </div>
        </form>
      </section>
```

Notes on what changed and why:
- The `<h2>` is gone — replaced by the tab strip, which signals switchability more clearly than a static heading.
- `login-username`'s placeholder is now "Email or username" because signup creates users keyed by email, and the login endpoint accepts either.
- Both forms carry the `auth-form` class so Task 3's CSS can target them uniformly.
- The signup form is `hidden` by default; Task 4's JS toggles `.hidden` on the two forms and `.active` on the two tabs.
- `minlength="8"` matches the backend's `validate_password` rule at `backend/main.py:160-166` (letters + numbers + ≥8 chars). The browser enforces the length rule client-side; the letter/number rule is enforced server-side and surfaced as a toast.

- [ ] **Step 2: Visually verify the markup parses**

```bash
docker-compose build admin && docker-compose up -d admin
curl -s http://192.168.18.192:3000/ | grep -E 'auth-tab|signup-form'
```

Expected: grep matches `auth-tab-login`, `auth-tab-signup`, and `signup-form`. No JS is wired up yet, so the signup form will render hidden and the tabs will be inert — that's correct for this step.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add signup form and auth tab strip to auth panel"
```

---

## Task 3: Style the auth tabs, the signup form, and keep the panel centred on large screens

**Files:**
- Modify: `frontend/styles.css` (append new block near the existing auth/form styles — place it immediately after the `.helper-text` rule at line 409-413)

- [ ] **Step 1: Add the auth tab + auth form CSS**

Insert this block into `frontend/styles.css` directly after the closing `}` of `.helper-text` (around line 413). Leave the existing form rules at `form button:not(...)` (line 258) and `.secondary-btn` (line 272) untouched — they'll still style the submit buttons.

```css
/* ── Auth panel (Sign In ⇄ Create Account) ───────────────────── */
#auth-panel {
  max-width: 440px;
  margin: 48px auto 0;
}

.auth-tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 18px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}

.auth-tab {
  flex: 1;
  padding: 8px 12px;
  background: transparent;
  color: var(--text-secondary);
  border: 0;
  border-radius: var(--r-sm);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast);
}

.auth-tab:hover { color: var(--text-primary); }

.auth-tab.active {
  background: linear-gradient(135deg, var(--cyan), var(--indigo));
  color: #020617;
  box-shadow: var(--glow-cyan);
}

.auth-form {
  display: grid;
  gap: 10px;
}
```

Rationale: `#auth-panel` is width-capped and centred because a full-bleed signup form on a 27" monitor looks absurd. The tab strip reuses the same gradient as `header h1` for visual consistency. Forms inside `#auth-panel` already pick up the existing `form` rules (layout via their default block flow), but giving them `.auth-form { display: grid; gap: 10px }` guarantees consistent spacing between the inputs and the submit button across both forms.

- [ ] **Step 2: Rebuild and visually verify**

```bash
docker-compose build admin && docker-compose up -d admin
```

Open http://192.168.18.192:3000/ in a browser. Expected:
- The auth panel is now visually centred, maxing at 440px wide.
- A pill-shaped tab strip shows "Sign In" (highlighted) and "Create Account" (muted) above the login form.
- The signup form is not visible yet (still `hidden`; JS comes in Task 4).

If the tab strip renders unstyled (i.e. looks like two plain buttons), the CSS block was inserted in a position where a later rule overrides it — move it to the very bottom of the file and re-check.

- [ ] **Step 3: Commit**

```bash
git add frontend/styles.css
git commit -m "style(frontend): auth tabs + centred auth panel + shared form grid"
```

---

## Task 4: Wire up signup form + tab toggle in `app.js`

**Files:**
- Modify: `frontend/app.js` — insert a new block immediately after the `login-form` handler that ends at line 1080 (look for `document.getElementById("login-form").addEventListener(...)` ending with `});`).

- [ ] **Step 1: Add the tab toggle helper + signup handler**

Insert this block into `frontend/app.js` directly after the `login-form` event listener (which ends near line 1080). Do not delete the login handler.

```javascript
/* ── Auth tabs (Sign In ⇄ Create Account) ───────────────────── */
function showAuthTab(which) {
  const loginTab   = document.getElementById("auth-tab-login");
  const signupTab  = document.getElementById("auth-tab-signup");
  const loginForm  = document.getElementById("login-form");
  const signupForm = document.getElementById("signup-form");
  const isSignup   = which === "signup";
  loginTab .classList.toggle("active", !isSignup);
  signupTab.classList.toggle("active",  isSignup);
  loginTab .setAttribute("aria-selected", String(!isSignup));
  signupTab.setAttribute("aria-selected", String( isSignup));
  loginForm .classList.toggle("hidden",  isSignup);
  signupForm.classList.toggle("hidden", !isSignup);
  const firstInput = isSignup ? "signup-business" : "login-username";
  document.getElementById(firstInput)?.focus();
}

document.getElementById("auth-tab-login") .addEventListener("click", () => showAuthTab("login"));
document.getElementById("auth-tab-signup").addEventListener("click", () => showAuthTab("signup"));

/* ── Signup ──────────────────────────────────────────────────── */
document.getElementById("signup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn          = e.target.querySelector("button[type=submit]");
  const business_name = document.getElementById("signup-business").value.trim();
  const email        = document.getElementById("signup-email").value.trim();
  const password     = document.getElementById("signup-password").value;
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ business_name, email, password }),
      });
      setAuth(data.token, data.user);
      showDashboard();
      await bootData();
      toast(`Welcome to Sawwii, ${business_name}! Your 14-day trial is active.`, "success", 6000);
    });
  } catch (err) {
    toast(err.message || "Sign-up failed.", "error");
  }
});
```

Why this shape:
- `showAuthTab("login"|"signup")` is the single source of truth for which form is visible; the handler keeps `.active`, `aria-selected`, and `.hidden` in sync.
- The signup handler mirrors the login handler's shape (`withLoading`, `api`, `setAuth`, `showDashboard`, `bootData`) so the happy path is identical once a token lands — only the success toast differs.
- The `api()` helper at `frontend/app.js:125-135` already throws `new Error(text)` when the server returns non-2xx, so backend validation errors (bad email, weak password, duplicate email, duplicate business name collisions resolved server-side via slug counter) surface as toasts without extra code.

- [ ] **Step 2: Rebuild and manually smoke-test signup end-to-end**

```bash
docker-compose build admin && docker-compose up -d admin
```

In a browser (incognito window to avoid a pre-existing session):

1. Go to http://192.168.18.192:3000/.
2. Click **Create Account** — the login form hides, the signup form appears, focus moves to the business name field.
3. Fill in: business name `SmokeTest Co`, email `smoke+$(date +%s)@example.com` (use an actually-unique email), password `Hunter2password`.
4. Click **Start Free Trial**.
5. Expected: the dashboard appears, a green toast reads `Welcome to Sawwii, SmokeTest Co! Your 14-day trial is active.`, and the header shows the signed-in email.

Negative paths to verify:
- Password `short` → toast `Password must be at least 8 characters`.
- Password `12345678` (digits only) → toast `Password must include a letter`.
- Same email twice → second attempt toasts `Email is already registered`.
- Empty business name → the browser's native `required` validation blocks submit (no toast needed).

If any of the above fails, pause and report before proceeding.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): signup form handler + Sign In/Create Account tabs"
```

---

## Task 5: "Your plan" card on the dashboard

**Files:**
- Modify: `frontend/index.html` — insert a new block at the very top of `<div id="dashboard">` (just above the existing `<section id="connection-panel">` at line 52).
- Modify: `frontend/app.js` — add `loadOrganization()` + `renderPlanCard()` and call from `bootData()`.
- Modify: `frontend/styles.css` — append plan-card styles at the end of the file.

- [ ] **Step 1: Add the plan-card markup**

Insert this block into `frontend/index.html` immediately after the opening `<div id="dashboard" class="hidden">` (line 51) and before the existing `<section id="connection-panel" ...>` (line 52):

```html
        <section id="plan-card" class="panel plan-card hidden">
          <div class="plan-card-head">
            <div>
              <h2 id="plan-card-business">—</h2>
              <div class="plan-card-sub">
                <span id="plan-card-tier">—</span>
                <span class="plan-card-dot">·</span>
                <span id="plan-card-usage">— / — screens</span>
              </div>
            </div>
            <span id="plan-card-status" class="plan-status">—</span>
          </div>
          <div id="plan-card-trial" class="helper-text hidden"></div>
        </section>
```

The card is inside `#dashboard` so it hides automatically when the user logs out. It starts `hidden` too — `renderPlanCard()` unhides it once data arrives so the loading moment is quiet.

- [ ] **Step 2: Add `loadOrganization()` + `renderPlanCard()` and wire into `bootData()`**

Find `bootData()` at `frontend/app.js:1153-1157`:

```javascript
async function bootData() {
  await Promise.all([loadSites(), loadPlaylists(), loadMedia(), loadUsers()]);
  await loadScreens();
  showSection("sites");
}
```

Replace it with:

```javascript
async function bootData() {
  await Promise.all([loadOrganization(), loadSites(), loadPlaylists(), loadMedia(), loadUsers()]);
  await loadScreens();
  showSection("sites");
}

async function loadOrganization() {
  try {
    const org = await api("/organization");
    renderPlanCard(org);
  } catch (err) {
    console.error("Failed to load organization", err);
  }
}

function renderPlanCard(org) {
  const card    = document.getElementById("plan-card");
  const biz     = document.getElementById("plan-card-business");
  const tier    = document.getElementById("plan-card-tier");
  const usage   = document.getElementById("plan-card-usage");
  const status  = document.getElementById("plan-card-status");
  const trial   = document.getElementById("plan-card-trial");

  const planLabels = {
    starter: "Starter", growth: "Growth", business: "Business",
    pro: "Pro", enterprise: "Enterprise",
  };
  const tierLabel  = planLabels[org.plan] || org.plan || "—";
  const used       = Number.isFinite(org.screens_used)  ? org.screens_used  : 0;
  const limit      = Number.isFinite(org.screen_limit) ? org.screen_limit : 0;

  biz.textContent   = org.name || "Your organization";
  tier.textContent  = `${tierLabel} plan`;
  usage.textContent = `${used} / ${limit} screens`;

  status.textContent = org.subscription_status || "—";
  status.className   = `plan-status plan-status-${(org.subscription_status || "unknown").toLowerCase()}`;

  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const endsAt  = new Date(org.trial_ends_at);
    const daysLeft = Math.max(0, Math.ceil((endsAt.getTime() - Date.now()) / 86400000));
    trial.textContent = daysLeft > 0
      ? `Trial ends in ${daysLeft} day${daysLeft === 1 ? "" : "s"} (${endsAt.toLocaleDateString()}).`
      : `Trial ended on ${endsAt.toLocaleDateString()}. Upgrade to keep your screens live.`;
    trial.classList.remove("hidden");
  } else {
    trial.classList.add("hidden");
  }

  card.classList.remove("hidden");
}
```

Why this shape:
- `loadOrganization()` failures are logged but not toasted — the card just stays hidden, which is a graceful degradation if the backend returns 500 or the endpoint ever disappears.
- `planLabels` is a tiny client-side map keyed to the backend `PLANS` dict at `backend/main.py:151-157`. A separate `/plans` fetch would work but is overkill for five labels that change only when pricing changes.
- `subscription_status` goes straight into a `plan-status-*` class so Task 5 Step 3's CSS can colour `trialing`/`active`/`past_due`/`canceled` differently without extra JS.

- [ ] **Step 3: Add the plan-card CSS**

Append to the end of `frontend/styles.css`:

```css
/* ── Plan card ───────────────────────────────────────────────── */
.plan-card { border: 1px solid var(--border-accent); }

.plan-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.plan-card-head h2 {
  font-size: 16px;
  margin-bottom: 4px;
}

.plan-card-sub {
  display: flex;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.plan-card-dot { color: var(--text-muted); }

.plan-status {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--r-pill);
  font-size: 12px;
  font-weight: 600;
  text-transform: capitalize;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-secondary);
}

.plan-status-trialing { color: var(--cyan);   border-color: var(--border-accent); }
.plan-status-active   { color: var(--green);  border-color: rgba(52, 211, 153, 0.35); }
.plan-status-past_due { color: var(--orange); border-color: rgba(249, 115, 22, 0.4);  }
.plan-status-canceled { color: var(--red);    border-color: rgba(239, 68, 68, 0.4);   }
```

- [ ] **Step 4: Rebuild and smoke-test the card**

```bash
docker-compose build admin && docker-compose up -d admin
```

In the browser:
1. Sign in as the trial user created in Task 4 (or create a new one via signup).
2. Expected: at the top of the dashboard, the plan card shows the business name, `Starter plan · 0 / 3 screens`, a cyan `trialing` pill on the right, and a helper line underneath reading `Trial ends in 14 days (<date>).`
3. Confirm the API contract directly (replace `<TOKEN>` with the `signage_auth_token` value from localStorage):

```bash
curl -s -H "Authorization: Bearer <TOKEN>" http://192.168.18.192:8000/organization | python3 -m json.tool
```

Expected JSON keys include: `name`, `plan: "starter"`, `screen_limit: 3`, `screens_used: 0`, `subscription_status: "trialing"`, `trial_ends_at`.

4. Sign out — the card hides with the rest of the dashboard.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css
git commit -m "feat(frontend): dashboard plan card with trial countdown"
```

---

## Task 6: Full end-to-end verification + wrap-up

**Files:** none modified — this task only runs checks.

- [ ] **Step 1: Confirm the backend regression suite is still green**

Phase 1's tests cover `/auth/signup` and multi-tenancy. They should still pass untouched since this plan made no backend changes, but re-run as a safety net:

```bash
docker-compose run --rm backend pytest
```

Expected: 7 passed.

- [ ] **Step 2: Two-tenant isolation smoke test from a shell**

Create two orgs via the signup endpoint, confirm each token only sees its own empty world:

```bash
API=http://192.168.18.192:8000
SUFFIX=$(date +%s)

TOK_A=$(curl -s -X POST "$API/auth/signup" -H 'Content-Type: application/json' \
  -d "{\"business_name\":\"Alpha $SUFFIX\",\"email\":\"alpha+$SUFFIX@example.com\",\"password\":\"Hunter2password\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

TOK_B=$(curl -s -X POST "$API/auth/signup" -H 'Content-Type: application/json' \
  -d "{\"business_name\":\"Bravo $SUFFIX\",\"email\":\"bravo+$SUFFIX@example.com\",\"password\":\"Hunter2password\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "A sees: $(curl -s -H "Authorization: Bearer $TOK_A" $API/sites)"
echo "B sees: $(curl -s -H "Authorization: Bearer $TOK_B" $API/sites)"
echo "A org:  $(curl -s -H "Authorization: Bearer $TOK_A" $API/organization | python3 -m json.tool)"
```

Expected:
- `A sees: []` and `B sees: []` — each new org starts empty.
- `A org:` shows `"name": "Alpha <suffix>"`, `"plan": "starter"`, `"screen_limit": 3`, `"subscription_status": "trialing"`, `"trial_ends_at"` ~14 days out.

If either tenant sees the other's data, stop and escalate — that's a multi-tenancy breach, not a frontend bug.

- [ ] **Step 3: Manual browser walk-through**

Open http://192.168.18.192:3000/ in an incognito window:

1. Tab strip shows "Sign In" selected and "Create Account" unselected.
2. Click **Create Account** → focus jumps to the business-name field.
3. Submit a new org (unique email). Dashboard loads, success toast appears, plan card at the top shows `Starter plan · 0 / 3 screens` with a `trialing` pill and a 14-day countdown line.
4. Sign out. The auth panel shows "Sign In" selected again (the tab state doesn't need to persist across sessions — on fresh page load the default is Sign In).
5. Sign back in with the same credentials. Plan card re-renders.
6. Add three screens via the Screens panel → plan card's `screens_used` updates to `3 / 3` on next refresh. Try to add a fourth → backend returns HTTP 402, toast surfaces the error. (This verifies the Phase 1 plan-limit wall is wired through to the UI error path. No extra code needed here — the existing `api()` helper already rethrows.)

- [ ] **Step 4: Confirm no "Signage Admin" branding leaked into the admin SPA**

```bash
grep -rn "Signage Admin\|◈ Signage" frontend/
```

Expected: **empty.**

- [ ] **Step 5: Final report**

Print:

```
Plan B complete — signup + Sawwii branding.
- 5 new commits on feature/phase1-lockdown.
- Frontend signup flow verified end-to-end: tab toggle, org creation, dashboard rendering, plan card with trial countdown.
- Backend regression suite: 7/7 green (no backend changes).
- Two-tenant isolation smoke test passed.
- Ready for Plan C (Stripe Checkout + webhooks) or bilingual EN/AR work.
```

---

## Notes for future plans

**Plan C (Stripe Checkout + webhooks)** will add an "Upgrade" button to the plan card built in Task 5. The button will hit a new `/billing/checkout-session` backend endpoint, redirect to Stripe-hosted Checkout, and a webhook at `/billing/webhook` will flip `organizations.subscription_status` on `customer.subscription.*` events. The plan-card CSS already handles `active` / `past_due` / `canceled` pill colours, so no frontend styling work will be needed then — just the upgrade button and its handler.

**Bilingual (EN/AR)** will need a `<html dir>` flip and a language toggle in the header, plus translation JSON for the auth-panel copy ("Sign In", "Create Account", "Start Free Trial", the success toast, and the plan-card labels). Consider extracting the hard-coded English strings in this plan's Task 4 and Task 5 to a translation module before that work begins.
