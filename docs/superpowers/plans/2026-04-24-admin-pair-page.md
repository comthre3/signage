# Admin `/pair` Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing phone-first `/pair?code=…` page in the admin SPA so that scanning the QR shown by the TV player lands the user on a dedicated pairing view where they can pick or create a screen and claim the code in a single tap. Completes the end-to-end Pattern B pairing flow together with Plan 2's player QR UI (same branch, bundled merge).

**Architecture:** Extend the existing admin SPA — no new entrypoints, no backend changes. `boot()` in `frontend/app.js` gains a path branch that detects `location.pathname === "/pair"` and calls a new `showPairView(code)` controller. The controller renders a `#pair-view` panel that is a sibling of `#auth-panel` and `#dashboard` with three internal states (loading / form / success) toggled via `.hidden`. Unauthenticated pair visits stash a `pair_resume` key in `sessionStorage`, then the existing login success path replays the pair view after auth.

**Tech Stack:** Vanilla JS (no framework, no build step), nginx static serve, existing pastel Khanshoof CSS tokens (`--cream`, `--peach`, `--peach-deep`, `--plum`, IBM Plex Sans/Serif/Mono). Reuses `api()`, `setAuth()`, `showAuthPanel()`, `showDashboard()`, `withLoading()`, `toast()`.

**Backend endpoints used (all pre-existing):**
- `GET /screens` — list org's screens
- `POST /screens` — create a new screen (fills org from auth, default plan, etc.)
- `POST /screens/claim` — atomic claim of pair code by screen_id (admin/editor only)

**Branch:** Continue on `feature/player-qr-pairing`. HEAD is `d9e45d5` (spec doc commit). Plan 2 and Plan 3 commits merge together at the end.

---

## Prerequisites

```bash
cd /home/ahmed/signage
git status                                  # expect clean on feature/player-qr-pairing
docker-compose run --rm backend pytest      # expect 38 passed (baseline regression)
```

## File Structure

```
frontend/
├── index.html   # ADD #pair-view section with loading / form / success siblings
├── styles.css   # ADD .pair-view* rules (phone-first pastel, reuses tokens)
└── app.js       # api() error enrichment + routing branch + showPairView() controller
                 #  + submit handler + error mapping + login resume hook
```

No changes to backend, player, or landing.

---

## Task 1: Enrich `api()` errors with status + parsed body

The existing `api()` throws `new Error(text)` which drops the HTTP status code, making friendly error mapping on the pair page brittle. Attach `.status` and `.data` (parsed body, if JSON) to the thrown error. Backwards-compatible — existing `catch` blocks still read `err.message`.

**Files:** Modify `frontend/app.js:125-135`

- [ ] **Step 1: Replace the `api()` body**

Find (around line 125):

```javascript
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, ...options });
  if (!res.ok) {
    if (res.status === 401) handleAuthFailure();
    const text = await res.text();
    throw new Error(text || "Request failed");
  }
  return res.json();
}
```

Replace with:

```javascript
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, ...options });
  if (!res.ok) {
    if (res.status === 401) handleAuthFailure();
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    const msg = (data && typeof data === "object" && typeof data.detail === "string")
      ? data.detail
      : (text || "Request failed");
    const err = new Error(msg);
    err.status = res.status;
    err.data   = data;
    throw err;
  }
  return res.json();
}
```

- [ ] **Step 2: Rebuild + smoke existing login error path**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

Visit `https://app.khanshoof.com/`, attempt login with `nobody@example.com` / `x`. Expect the red toast to read `Invalid credentials` (clean message parsed from `data.detail`, not a raw JSON blob).

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): api() attaches status + parsed data to thrown errors"
```

---

## Task 2: Add `#pair-view` DOM scaffold to `index.html`

Inert markup only — no styling, no JS wiring. Panel is hidden by default. Three internal siblings (`#pair-loading`, `#pair-form`, `#pair-success`) are all hidden; the controller (Task 4) shows one at a time.

**Files:** Modify `frontend/index.html` — insert the new section between `#auth-panel`'s closing tag (line 87) and the `#dashboard` opening div (line 89).

- [ ] **Step 1: Insert the scaffold**

Find the exact two lines:

```html
      </section>

      <div id="dashboard" class="hidden">
```

Replace with:

```html
      </section>

      <section id="pair-view" class="panel pair-view hidden" aria-live="polite">
        <div id="pair-loading" class="pair-loading hidden">
          <p>Loading…</p>
        </div>

        <form id="pair-form" class="pair-form hidden" novalidate>
          <header class="pair-header">
            <h1 class="pair-brand">Khanshoof</h1>
            <p class="pair-title">Pair a display</p>
          </header>

          <label class="pair-field">
            <span class="pair-label">Pairing code</span>
            <input
              type="text"
              id="pair-code-input"
              class="pair-code-input"
              inputmode="text"
              autocapitalize="characters"
              autocorrect="off"
              spellcheck="false"
              maxlength="5"
              pattern="[A-Z2-9]{5}"
              placeholder="ABCDE"
              required
            />
          </label>

          <fieldset class="pair-field pair-choice">
            <legend class="pair-label">Which screen is this?</legend>

            <label class="pair-radio">
              <input type="radio" name="pair-target" id="pair-target-existing" value="existing" checked />
              <span>Existing screen</span>
            </label>
            <select id="pair-existing-select" class="pair-select">
              <option value="">— Pick a screen —</option>
            </select>

            <label class="pair-radio">
              <input type="radio" name="pair-target" id="pair-target-new" value="new" />
              <span>Create new screen</span>
            </label>
            <input
              type="text"
              id="pair-new-name"
              class="pair-new-name hidden"
              placeholder="Screen name (e.g. Lobby TV)"
              maxlength="80"
              autocomplete="off"
            />
          </fieldset>

          <button type="submit" id="pair-submit" class="pair-submit" disabled>Pair display</button>

          <p id="pair-error" class="pair-error hidden" role="alert"></p>
        </form>

        <div id="pair-success" class="pair-success hidden">
          <div class="pair-success-check" aria-hidden="true">✓</div>
          <h1 class="pair-success-title">Display paired</h1>
          <p class="pair-success-name" id="pair-success-name">—</p>
          <div class="pair-success-actions">
            <button type="button" id="pair-another-btn" class="pair-primary">Pair another display</button>
            <button type="button" id="pair-dashboard-btn" class="pair-secondary">View dashboard</button>
          </div>
        </div>
      </section>

      <div id="dashboard" class="hidden">
```

- [ ] **Step 2: Rebuild + verify the scaffold ships**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
curl -s https://app.khanshoof.com/ | grep -cE 'id="pair-view"|id="pair-form"|id="pair-success"|id="pair-code-input"|id="pair-existing-select"|id="pair-new-name"|id="pair-another-btn"|id="pair-dashboard-btn"'
```

Expected: `8` (all 8 anchors present in the served HTML).

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add frontend/index.html
git -C /home/ahmed/signage commit -m "feat(frontend): pair-view DOM scaffold"
```

---

## Task 3: Add `.pair-view*` CSS rules

Phone-first layout, single column, large touch targets, reuses existing pastel tokens. Append to the end of `frontend/styles.css` (the file already defines `--cream`, `--peach`, `--peach-deep`, `--plum`, etc. in `:root`).

**Files:** Modify `frontend/styles.css`

- [ ] **Step 1: Append the pair-view rules**

Open `frontend/styles.css`, scroll to the end of the file, and append:

```css

/* ── Pair view (phone-first) ───────────────────────────────── */
.pair-view {
  max-width: 560px;
  margin: clamp(16px, 4vh, 48px) auto;
  padding: clamp(20px, 3vh, 36px);
  background: var(--bg-panel);
  border-radius: var(--r-lg);
  border: 1px solid var(--cream-border);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  gap: clamp(16px, 2vh, 24px);
}

.pair-header {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pair-brand {
  font-family: var(--font-display);
  font-size: clamp(22px, 3vh, 32px);
  font-weight: 700;
  color: var(--plum);
  margin: 0;
}

.pair-title {
  font-size: clamp(15px, 2vh, 18px);
  color: var(--cocoa);
  margin: 0;
}

.pair-form {
  display: flex;
  flex-direction: column;
  gap: clamp(14px, 2vh, 20px);
}

.pair-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: none;
  padding: 0;
  margin: 0;
}

.pair-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--plum);
  padding: 0;
}

.pair-code-input {
  font-family: var(--mono);
  font-size: clamp(28px, 6vh, 40px);
  font-weight: 700;
  letter-spacing: clamp(4px, 1vh, 10px);
  text-align: center;
  text-transform: uppercase;
  padding: clamp(12px, 2vh, 18px) 12px;
  border: 2px solid var(--cream-border);
  border-radius: var(--r-md);
  background: var(--bg-input);
  color: var(--plum);
  width: 100%;
  min-height: 56px;
}

.pair-code-input:focus {
  outline: none;
  border-color: var(--peach-deep);
  box-shadow: 0 0 0 4px rgba(224, 148, 120, 0.18);
}

.pair-choice {
  gap: 10px;
}

.pair-radio {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  color: var(--plum);
  padding: 10px 0;
  min-height: 44px;
  cursor: pointer;
}

.pair-radio input[type="radio"] {
  width: 20px;
  height: 20px;
  accent-color: var(--peach-deep);
}

.pair-select,
.pair-new-name {
  width: 100%;
  font-size: 16px;
  padding: 12px 14px;
  border: 1px solid var(--cream-border);
  border-radius: var(--r-md);
  background: var(--bg-input);
  color: var(--plum);
  min-height: 48px;
}

.pair-select:focus,
.pair-new-name:focus {
  outline: none;
  border-color: var(--peach-deep);
  box-shadow: 0 0 0 3px rgba(224, 148, 120, 0.18);
}

.pair-select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.pair-submit {
  background: var(--peach-deep);
  color: #FFFFFF;
  font-size: 16px;
  font-weight: 600;
  border: none;
  border-radius: var(--r-md);
  padding: 14px;
  min-height: 48px;
  cursor: pointer;
  transition: transform var(--t-fast), background var(--t-fast);
}

.pair-submit:hover:not(:disabled) {
  background: #C97E62;
}

.pair-submit:active:not(:disabled) {
  transform: translateY(1px);
}

.pair-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pair-error {
  background: #FDECEF;
  color: var(--red);
  border: 1px solid #F2C6CF;
  border-radius: var(--r-md);
  padding: 10px 14px;
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
}

.pair-success {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 12px 0;
}

.pair-success-check {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--mint);
  color: var(--plum);
  font-size: 40px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pair-success-title {
  font-family: var(--font-display);
  font-size: clamp(22px, 3vh, 30px);
  color: var(--plum);
  margin: 0;
}

.pair-success-name {
  font-size: 16px;
  color: var(--cocoa);
  margin: 0;
}

.pair-success-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  margin-top: 8px;
}

.pair-primary,
.pair-secondary {
  width: 100%;
  font-size: 16px;
  font-weight: 600;
  border-radius: var(--r-md);
  padding: 14px;
  min-height: 48px;
  cursor: pointer;
  transition: transform var(--t-fast), background var(--t-fast);
}

.pair-primary {
  background: var(--peach-deep);
  color: #FFFFFF;
  border: none;
}

.pair-primary:hover { background: #C97E62; }
.pair-primary:active { transform: translateY(1px); }

.pair-secondary {
  background: transparent;
  color: var(--plum);
  border: 1px solid var(--cream-border);
}

.pair-secondary:hover { background: var(--bg-card); }
.pair-secondary:active { transform: translateY(1px); }

.pair-loading {
  text-align: center;
  color: var(--cocoa);
  padding: 40px 0;
}
```

- [ ] **Step 2: Rebuild + smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

Visit `https://app.khanshoof.com/pair` logged out (or in incognito). The auth panel should still display as before — `#pair-view` is hidden so the new CSS should have no visible impact yet. No console errors.

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(frontend): pastel phone-first styling for pair view"
```

---

## Task 4: `showPairView()` controller + `/pair` routing branch + form render

All state-rendering logic. No submit yet (Task 5). No resume hook yet (Task 7).

**Files:** Modify `frontend/app.js`

- [ ] **Step 1: Add pair controller above `boot()`**

Find the function `async function boot() {` at line 1366. Immediately above it, insert:

```javascript
/* ── Pair view ──────────────────────────────────────────────── */
const PAIR_CODE_RE = /^[A-Z2-9]{5}$/;

function normalizePairCode(raw) {
  return String(raw || "").toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 5);
}

function showPairViewPanel() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("pair-view").classList.remove("hidden");
}

function setPairState(which) {
  const loading = document.getElementById("pair-loading");
  const form    = document.getElementById("pair-form");
  const success = document.getElementById("pair-success");
  loading.classList.toggle("hidden", which !== "loading");
  form   .classList.toggle("hidden", which !== "form");
  success.classList.toggle("hidden", which !== "success");
}

function updatePairSubmitEnabled() {
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
  let ok = PAIR_CODE_RE.test(code);
  if (target === "existing") {
    ok = ok && Boolean(document.getElementById("pair-existing-select").value);
  } else if (target === "new") {
    ok = ok && document.getElementById("pair-new-name").value.trim().length > 0;
  } else {
    ok = false;
  }
  document.getElementById("pair-submit").disabled = !ok;
}

function clearPairError() {
  const el = document.getElementById("pair-error");
  el.textContent = "";
  el.classList.add("hidden");
}

async function showPairView(initialCode) {
  showPairViewPanel();
  setPairState("loading");
  clearPairError();

  // Reset form state
  const codeInput    = document.getElementById("pair-code-input");
  const existingSel  = document.getElementById("pair-existing-select");
  const newNameInput = document.getElementById("pair-new-name");
  const radioExist   = document.getElementById("pair-target-existing");
  const radioNew     = document.getElementById("pair-target-new");

  codeInput.value    = normalizePairCode(initialCode);
  newNameInput.value = "";
  newNameInput.classList.add("hidden");
  existingSel.innerHTML = '<option value="">— Pick a screen —</option>';

  let screens = [];
  try {
    screens = await api("/screens");
  } catch (err) {
    if (err.status === 401) return; // handleAuthFailure() already routed
    console.error(err);
    screens = [];
  }

  for (const s of screens) {
    const opt = document.createElement("option");
    opt.value = String(s.id);
    opt.textContent = s.name || `Screen #${s.id}`;
    existingSel.appendChild(opt);
  }

  if (screens.length === 0) {
    radioExist.disabled = true;
    existingSel.disabled = true;
    radioNew.checked = true;
    newNameInput.classList.remove("hidden");
  } else {
    radioExist.disabled = false;
    existingSel.disabled = false;
    radioExist.checked = true;
    newNameInput.classList.add("hidden");
  }

  setPairState("form");
  updatePairSubmitEnabled();
}
```

- [ ] **Step 2: Wire the form interactions**

At the bottom of `frontend/app.js` (after the existing `/* ── Misc bindings ──` block around line 1386), append:

```javascript

/* ── Pair-view input wiring ─────────────────────────────────── */
document.getElementById("pair-code-input").addEventListener("input", (e) => {
  const cleaned = normalizePairCode(e.target.value);
  if (cleaned !== e.target.value) e.target.value = cleaned;
  updatePairSubmitEnabled();
});

document.getElementById("pair-existing-select").addEventListener("change", updatePairSubmitEnabled);
document.getElementById("pair-new-name")      .addEventListener("input",  updatePairSubmitEnabled);

document.querySelectorAll('input[name="pair-target"]').forEach((el) => {
  el.addEventListener("change", () => {
    const target = document.querySelector('input[name="pair-target"]:checked')?.value;
    const existingSel  = document.getElementById("pair-existing-select");
    const newNameInput = document.getElementById("pair-new-name");
    if (target === "new") {
      newNameInput.classList.remove("hidden");
      existingSel.classList.add("hidden");
    } else {
      newNameInput.classList.add("hidden");
      existingSel.classList.remove("hidden");
    }
    updatePairSubmitEnabled();
  });
});
```

- [ ] **Step 3: Add the `/pair` branch to `boot()`**

Find the current `boot()` body (starts at line 1366):

```javascript
async function boot() {
  if (!authToken) { showAuthPanel(); updateAuthUI(); return; }
  try {
    const me = await api("/auth/me");
    setAuth(authToken, me);
    showDashboard();
    await bootData();
    updateResolutionCustomVisibility();
    if (location.hash === '#signup') showAuthTab('signup');
  } catch (err) {
    console.error(err);
    handleAuthFailure();
  }
}
```

Replace with:

```javascript
async function boot() {
  const isPairPath = location.pathname === "/pair";
  const pairCodeParam = isPairPath
    ? new URLSearchParams(location.search).get("code") || ""
    : "";

  if (!authToken) {
    showAuthPanel();
    updateAuthUI();
    if (location.hash === '#signup') showAuthTab('signup');
    return;
  }

  try {
    const me = await api("/auth/me");
    setAuth(authToken, me);
    if (isPairPath) {
      await showPairView(pairCodeParam);
    } else {
      showDashboard();
      await bootData();
      updateResolutionCustomVisibility();
      if (location.hash === '#signup') showAuthTab('signup');
    }
  } catch (err) {
    console.error(err);
    handleAuthFailure();
  }
}
```

- [ ] **Step 4: Rebuild + visual smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

1. Log in at `https://app.khanshoof.com/`.
2. Navigate to `https://app.khanshoof.com/pair?code=ABCDE` (paste into URL bar).
3. Expect the pair view to render: "Khanshoof / Pair a display" header, code field pre-filled with `ABCDE`, radio group showing "Existing screen" (with dropdown populated from your org's screens) and "Create new screen". Pair button stays disabled because no existing screen is picked yet.
4. Pick an existing screen from the dropdown — Pair button becomes enabled.
5. Switch to "Create new screen" — name input appears, Pair disabled again until a name is typed.
6. Type a name — Pair enabled.

Do NOT click Pair — submit wiring lands in Task 5.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): showPairView controller + /pair routing"
```

---

## Task 5: Pair-submit logic — claim flow + success state

Wires the Pair button and the two success-state buttons. Depends on Task 4's controller and Task 1's enriched `api()` errors.

**Files:** Modify `frontend/app.js`

- [ ] **Step 1: Add submit + success handlers at end of `app.js`**

Append to the end of `frontend/app.js`:

```javascript

/* ── Pair-view submit ───────────────────────────────────────── */
function showPairError(message) {
  const el = document.getElementById("pair-error");
  el.textContent = message;
  el.classList.remove("hidden");
}

function mapPairErrorMessage(err) {
  // err has .status and .data from the enriched api()
  const detail = (err && err.data && typeof err.data.detail === "string")
    ? err.data.detail
    : (err?.message || "");
  const status = err?.status;
  if (status === 404 && /pairing code/i.test(detail)) {
    return "That code isn't recognised. Check the TV screen and try again.";
  }
  if (status === 400 && /expired/i.test(detail)) {
    return "Code expired. Refresh the TV to get a new one.";
  }
  if (status === 409) {
    return "That code's been used. Refresh the TV to get a new one.";
  }
  if (status === 400 && /bound to a different screen/i.test(detail)) {
    return "This code belongs to a different display. Refresh the TV to get a new one.";
  }
  if (status === 402) {
    return "You've hit your plan's screen limit. Upgrade to add more.";
  }
  if (status === 403) {
    return "Your account doesn't have permission to pair displays.";
  }
  if (!status) {
    return "Can't reach server. Please try again.";
  }
  return "Something went wrong — please try again.";
}

async function onPairSubmit(e) {
  e.preventDefault();
  clearPairError();
  const btn = document.getElementById("pair-submit");
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;

  await withLoading(btn, async () => {
    try {
      let screenId = null;
      let screenName = "";

      if (target === "new") {
        const name = document.getElementById("pair-new-name").value.trim();
        const screen = await api("/screens", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        screenId = screen.id;
        screenName = screen.name || name;
      } else {
        const sel = document.getElementById("pair-existing-select");
        screenId = Number(sel.value);
        screenName = sel.options[sel.selectedIndex]?.textContent || `Screen #${screenId}`;
      }

      await api("/screens/claim", {
        method: "POST",
        body: JSON.stringify({ code, screen_id: screenId }),
      });

      document.getElementById("pair-success-name").textContent = screenName;
      setPairState("success");
    } catch (err) {
      console.error(err);
      showPairError(mapPairErrorMessage(err));
    }
  });
}

async function onPairAnother() {
  history.replaceState({}, "", "/pair");
  await showPairView("");
}

function onPairViewDashboard() {
  history.pushState({}, "", "/");
  document.getElementById("pair-view").classList.add("hidden");
  showDashboard();
  bootData().catch((err) => {
    console.error(err);
    toast("Failed to load dashboard. Check your connection.", "error", 6000);
  });
}

document.getElementById("pair-form")         .addEventListener("submit", onPairSubmit);
document.getElementById("pair-another-btn")  .addEventListener("click",  onPairAnother);
document.getElementById("pair-dashboard-btn").addEventListener("click",  onPairViewDashboard);
```

- [ ] **Step 2: Rebuild + happy-path browser smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

1. Open a player in incognito at `https://play.khanshoof.com/` — note the code it shows (e.g. `K7M2Q`).
2. In another tab (logged-in admin), go to `https://app.khanshoof.com/pair?code=K7M2Q`.
3. Pick "Create new screen", name it "Test Lobby", click Pair display.
4. Expect the success state: green check, "Display paired", "Test Lobby", and two buttons.
5. Within 3 s, the player tab should swap from the pairing view to content (or "No content assigned" if empty).
6. Back on the admin: click "Pair another display" → form re-renders, code empty, URL is now `/pair` (no `?code=`).
7. Click "View dashboard" (after another round or from a success state) → dashboard loads, URL is `/`.

- [ ] **Step 3: Commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): pair-view submit + success state"
```

---

## Task 6: Error surface — exercising the mapping

Task 5 already wrote the mapping function. This task is a smoke-only task to exercise each error branch and make sure no mapping regresses. No code changes expected; if a branch does produce a raw `detail` in the UI, add a regex and recommit.

**Files:** None expected (verification-only task). If the mapping needs a regex tweak, modify `frontend/app.js::mapPairErrorMessage`.

- [ ] **Step 1: Error matrix**

With the player rendering a fresh code and the admin `/pair` view open:

| Scenario | How to trigger | Expected inline error |
|---|---|---|
| Unknown code | Type `ZZZZZ` in the code field and submit | "That code isn't recognised. Check the TV screen and try again." |
| Expired code | Temporarily set `PAIR_CODE_TTL_SECONDS=10` in backend env, restart backend, let code time out, then submit | "Code expired. Refresh the TV to get a new one." |
| Already claimed | Submit the same code twice in rapid succession (or claim it from another admin tab first) | "That code's been used. Refresh the TV to get a new one." |
| Plan limit (create path) | On a Starter trial already at the 3-screen limit, pick "Create new" and submit | "You've hit your plan's screen limit. Upgrade to add more." |

Undo the TTL change after the expiry test (`unset PAIR_CODE_TTL_SECONDS` in the backend shell or set back to 600, restart).

- [ ] **Step 2: If any branch displayed a raw FastAPI `detail` instead of a friendly message**

Open `frontend/app.js`, find `mapPairErrorMessage`, add a matching clause for the observed `(status, detail)` pair. Rebuild, re-test, commit with message `fix(frontend): map <scenario> to friendly pair error`.

Skip this step if all four scenarios above produced clean messages.

- [ ] **Step 3: If any regex was added in Step 2, commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "fix(frontend): map additional pair errors to friendly messages"
```

---

## Task 7: Login resume hook — `pair_resume` session stash + replay

Unauthenticated `/pair?code=…` visits must drop through the auth panel and resume automatically after successful login.

**Files:** Modify `frontend/app.js`

- [ ] **Step 1: Stash on unauth /pair visit — update `boot()`**

Find the unauth branch in `boot()` (added in Task 4 Step 3):

```javascript
  if (!authToken) {
    showAuthPanel();
    updateAuthUI();
    if (location.hash === '#signup') showAuthTab('signup');
    return;
  }
```

Replace with:

```javascript
  if (!authToken) {
    if (isPairPath) {
      sessionStorage.setItem("pair_resume", JSON.stringify({ path: "/pair", code: pairCodeParam }));
    }
    showAuthPanel();
    updateAuthUI();
    if (location.hash === '#signup') showAuthTab('signup');
    return;
  }
```

- [ ] **Step 2: Replay on login success**

Find the login-form submit handler around line 1065:

```javascript
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn      = e.target.querySelector("button[type=submit]");
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      setAuth(data.token, data.user);
      showDashboard();
      await bootData();
    });
  } catch (err) {
    toast(err.message || "Login failed.", "error");
  }
});
```

Replace with:

```javascript
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn      = e.target.querySelector("button[type=submit]");
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      setAuth(data.token, data.user);

      const resumeRaw = sessionStorage.getItem("pair_resume");
      if (resumeRaw) {
        sessionStorage.removeItem("pair_resume");
        try {
          const resume = JSON.parse(resumeRaw);
          if (resume && resume.path === "/pair") {
            history.replaceState({}, "", `/pair${resume.code ? `?code=${encodeURIComponent(resume.code)}` : ""}`);
            await showPairView(resume.code || "");
            return;
          }
        } catch (_) { /* fall through to dashboard */ }
      }

      showDashboard();
      await bootData();
    });
  } catch (err) {
    toast(err.message || "Login failed.", "error");
  }
});
```

- [ ] **Step 3: Rebuild + resume smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build frontend
```

1. Open an incognito window. Go to `https://app.khanshoof.com/pair?code=WXYZ2` (the code doesn't need to be valid; we're testing the redirect, not a real claim).
2. Expect the login form to appear (not the dashboard, not the pair view).
3. Log in with real credentials.
4. Expect the URL bar to show `/pair?code=WXYZ2` and the pair view to render with `WXYZ2` pre-filled in the code field.
5. (Optional) close the tab without logging in, open a new incognito tab, go straight to `https://app.khanshoof.com/` — expect the normal auth panel and, after login, the normal dashboard (the stash from the previous tab died with the previous tab's `sessionStorage`).

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): resume pair view after login"
```

---

## Task 8: Full smoke matrix + merge + memory update

- [ ] **Step 1: Backend regression**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `38 passed`.

- [ ] **Step 2: Run the full smoke matrix**

Run each scenario exactly once in a real browser on a phone (or narrow-viewport desktop). Use the player at `https://play.khanshoof.com/` to generate codes and the admin at `https://app.khanshoof.com/` to claim them.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Fresh pair, not logged in: incognito → scan QR → login → `/pair` resumes | Pair succeeds; player swaps within 3 s |
| 2 | Existing-screen pair: logged in, `/pair?code=X`, pick existing | Claim succeeds; player swaps |
| 3 | New-screen pair: logged in, `/pair?code=X`, pick "Create new", name it | Screen row created + claimed; player swaps |
| 4 | Manual code entry: `/pair` (no query), type 5-char code from TV | Pair succeeds |
| 5 | Expired code: wait > 10 min, submit | Inline error "Code expired. Refresh the TV…" |
| 6 | Wrong code: type `ZZZZZ` | Inline error "That code isn't recognised…" |
| 7 | Plan limit: Starter org with 3 screens, try "Create new" | Inline error "You've hit your plan's screen limit." |
| 8 | Success → pair another: tap "Pair another display" | Form re-renders, code empty, URL `/pair` |
| 9 | Success → view dashboard: tap "View dashboard" | Navigates to `/`, dashboard loads |

- [ ] **Step 3: Merge the whole pairing feature (Plans 2 + 3) to `main`**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage pull --ff-only origin main 2>/dev/null || true
git -C /home/ahmed/signage merge --no-ff feature/player-qr-pairing -m "Merge end-to-end pairing flow (Plans 2 + 3): player QR UI + admin /pair page"
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build player frontend
```

- [ ] **Step 4: Update the roadmap memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`:

1. Replace the "Player QR UI (Plan 2 — NEXT)" bullet with a "Plan 2 + 3 — DONE 2026-04-24, merge commit `<SHA>`" block summarising the player QR UI and the admin `/pair?code=…` page.
2. Promote Plan 4 (retire legacy `POST /screens/pair` + `screens.pair_code` column) to the NEXT position.
3. If the entry in the MEMORY index (`MEMORY.md`) needs a date bump, update it too.

- [ ] **Step 5: Confirm production rendering**

Visit `https://app.khanshoof.com/pair?code=TEST1` logged in and `https://play.khanshoof.com/` in incognito. Both should render cleanly post-merge. No console errors.
