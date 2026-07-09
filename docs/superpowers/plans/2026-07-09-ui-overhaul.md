# UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pastel/kawaii UI with the approved sleek design system (sidebar dashboard, dark/light themes, coral accent, dosed mascot) across dashboard, auth, OAuth pages, and landing — no framework migration.

**Architecture:** Vanilla-JS SPA stays. CSS is rewritten around a token system copied verbatim from the approved mockups; `index.html` is restructured (top-nav → sidebar + topbar) while **every element id, `data-*` hook, and i18n key `app.js` relies on is preserved**. Backend-served OAuth templates get the same tokens via `frontend/oauth-consent.css`. Landing is rebuilt from its committed mockup with the SEO head block preserved verbatim.

**Tech Stack:** HTML/CSS/vanilla JS, Jinja2 templates (OAuth), nginx containers, pytest (backend), Inter + IBM Plex Sans Arabic + JetBrains Mono via Google Fonts.

**Source of truth for all visuals:** `docs/superpowers/specs/2026-07-09-ui-overhaul-mockups/dashboard.html` and `landing.html` (committed, user-approved). When a task says "port the `X` styles from the mockup", copy that selector block from the mockup file and adapt selectors as instructed — do not invent new visuals.

## Global Constraints

- **Frozen DOM contract:** never rename/remove an element id, `data-section`, `data-i18n`, `data-cta`, or any `data-*` hook that exists today. `scripts/check_ui_contract.sh` (Task 1) enforces ids + i18n keys and must pass before every commit.
- **Frozen JS-toggled class names:** `hidden`, `nav-active`, `nav-open`, `active`, `dropzone-active`, `paired`, `empty`, `loading`, `toast-exit`, `zones-canvas-grid`, `status-online`, `status-offline`, and all `confirm-dialog-*` / `media-picker-*` classes. New CSS must style these names, not replace them.
- **Theme attribute:** `document.documentElement.dataset.theme` ∈ {`dark`,`light`}; default follows `prefers-color-scheme`; user override persisted as `localStorage["khanshoof_theme"]`.
- **Accent:** `#E8794A` (hover `#D96835`, soft `rgba(232,121,74,.12)`). Status: green `#2FB344`, amber `#E8A33D`, red `#E5484D`.
- **RTL:** logical properties only (`inset-inline-*`, `margin-inline-*`, `border-inline-*`); never `left/right` for layout.
- **Mascot:** always `image-rendering: pixelated`; placements only per the spec table (brand, hero peek, CTA band, footer, empty states, success/error moments, info notes).
- **OAuth test strings:** templates must keep the literal strings `Sign in` (login page) and `Authorize` + client name (consent page) — `backend/tests/test_oauth.py:277,292` assert them.
- **Backend pytest suite green after every phase:** `cd backend && python -m pytest -q`.
- **i18n:** every new user-visible string gets a key in BOTH `frontend/i18n/en.json` and `ar.json` (landing: `landing/i18n/*.json`).
- Branch: `feature/ui-overhaul`. One PR per phase, phases land in order.

---

## Phase 1 — Dashboard shell (tokens, sidebar, topbar, themes)

### Task 1: UI contract check script

**Files:**
- Create: `scripts/check_ui_contract.sh`

**Interfaces:**
- Produces: `bash scripts/check_ui_contract.sh` → exit 0 on pass; prints violations and exits 1 otherwise. Every later task runs this before committing.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Guardrail for the UI overhaul: app.js's DOM contract must survive markup changes.
set -u
cd "$(dirname "$0")/.."
fail=0

# ── 1. Every id app.js looks up must exist in index.html ──
# (allowlist: nodes app.js creates dynamically at runtime)
DYNAMIC_IDS="canvas-item-detail canvas-items-list canvas-preview-media complete-signup-form complete-signup-overlay"
for id in $(grep -oE 'getElementById\("[a-zA-Z0-9_-]+"\)' frontend/app.js | sed 's/getElementById("//;s/")//' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING id in index.html: $id"; fail=1; }
done

# ── 2. Every #id used via querySelector must exist too ──
for id in $(grep -oE 'querySelector(All)?\("#[a-zA-Z0-9_-]+"' frontend/app.js | grep -oE '#[a-zA-Z0-9_-]+' | tr -d '#' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING querySelector id in index.html: $id"; fail=1; }
done

# ── 3. Every data-i18n key in index.html must exist in BOTH locale files ──
for key in $(grep -oE 'data-i18n(-placeholder|-aria-label)?="[^"]+"' frontend/index.html | sed 's/.*="//;s/"//' | sort -u); do
  grep -q "\"$key\"" frontend/i18n/en.json || { echo "MISSING en.json key: $key"; fail=1; }
  grep -q "\"$key\"" frontend/i18n/ar.json || { echo "MISSING ar.json key: $key"; fail=1; }
done

# ── 4. nav buttons still carry data-section inside a <nav> ──
for section in sites screens media playlists schedules users walls audit-log api-keys billing; do
  grep -q "data-section=\"$section\"" frontend/index.html || { echo "MISSING nav button: data-section=\"$section\""; fail=1; }
done

[ $fail -eq 0 ] && echo "UI contract OK"
exit $fail
```

- [ ] **Step 2: Make executable and run — must pass on the CURRENT untouched code**

Run: `chmod +x scripts/check_ui_contract.sh && bash scripts/check_ui_contract.sh`
Expected: `UI contract OK` (baseline green proves the checker itself is sound)

- [ ] **Step 3: Break it deliberately to verify it catches violations**

Run: `sed 's/id="main-nav"/id="main-nav-x"/' frontend/index.html > /tmp/t.html && cp frontend/index.html /tmp/orig.html && mv /tmp/t.html frontend/index.html && bash scripts/check_ui_contract.sh; mv /tmp/orig.html frontend/index.html`
Expected: `MISSING id in index.html: main-nav` + exit 1, then restored; re-run passes.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_ui_contract.sh
git commit -m "chore(ui): add DOM-contract guardrail script for the overhaul"
```

### Task 2: Design tokens + legacy variable bridge in styles.css

**Files:**
- Modify: `frontend/styles.css:1-97` (replace the `@import`→`body::before` region)

**Interfaces:**
- Produces: CSS custom properties `--bg, --bg-sidebar, --surface, --surface-2, --border, --border-2, --text, --text-2, --text-3, --accent, --accent-strong, --accent-soft, --green, --amber, --red, --r-sm/md/lg, --t, --font, --mono, --shadow-card, --shadow-pop` under `[data-theme="dark"]` / `[data-theme="light"]`. Legacy var names (`--bg-deep`, `--bg-panel`, `--text-primary`, `--cyan`, …) re-pointed at the new vars so not-yet-rewritten component CSS renders correctly on both themes.

- [ ] **Step 1: Replace lines 1–97 of `frontend/styles.css`**

Copy the token block **verbatim** from `docs/superpowers/specs/2026-07-09-ui-overhaul-mockups/dashboard.html` (`:root`, `[data-theme="dark"]`, `[data-theme="light"]` — the `<style>` head through the `::selection` rule), swap the font import to include Arabic:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
```

then append the bridge so every legacy variable name still resolves:

```css
/* ── Legacy variable bridge — remove when all component CSS is rewritten (Phase 2) ── */
:root, [data-theme="dark"], [data-theme="light"] {
  --bg-deep: var(--bg);           --bg-surface: var(--bg-sidebar);
  --bg-panel: var(--surface);     --bg-card: var(--surface-2);
  --bg-input: var(--surface);
  --text-primary: var(--text);    --text-secondary: var(--text-2);
  --text-muted: var(--text-3);
  --cyan: var(--accent);          --blue: var(--accent);
  --indigo: var(--accent);        --orange: var(--amber);
  --border-accent: var(--accent);
  --glow-cyan: var(--shadow-card); --glow-indigo: var(--shadow-card);
  --shadow-lg: var(--shadow-pop);  --shadow-md: var(--shadow-card);
  --r-pill: 999px;
  --t-fast: var(--t);              --t-normal: var(--t);
  --font-display: var(--font);
  --cream: var(--bg);              --butter: var(--bg-sidebar);
  --peach: var(--accent);          --peach-deep: var(--accent-strong);
  --mint: var(--green);            --lavender: var(--accent-soft);
  --rose: var(--accent-soft);      --plum: var(--text);
  --cocoa: var(--text-2);          --sand: var(--text-3);
  --cream-border: var(--border);   --plum-shadow: var(--shadow-card);
  --grain: transparent;            --cream-2: var(--bg);
  --muted: var(--text-3);          --font-mono: var(--mono);
}
:lang(ar) { --font: 'IBM Plex Sans Arabic', 'Inter', system-ui, sans-serif; }
```

Also delete the `body::before` paper-grain rule entirely and update the `body` rule to the mockup's (font/size/color/bg from tokens; **remove** `display:flex` — the app shell adds it in Task 3).

- [ ] **Step 2: Verify no stale variable references**

Run: `grep -oE 'var\(--[a-z0-9-]+\)' frontend/styles.css | sort -u | sed 's/var(//;s/)//' | while read v; do grep -q -- "$v:" frontend/styles.css || echo "UNDEFINED: $v"; done`
Expected: no output (every referenced var is defined).

- [ ] **Step 3: Run contract check + commit**

```bash
bash scripts/check_ui_contract.sh
git add frontend/styles.css
git commit -m "feat(ui): new dark/light design tokens with legacy variable bridge"
```

### Task 3: Shell restructure — sidebar + topbar markup and styles

**Files:**
- Modify: `frontend/index.html:21-50` (replace `<header>` block), wrap `<main>` in the new layout
- Modify: `frontend/styles.css` (replace old `header`/nav styles with mockup shell styles)

**Interfaces:**
- Consumes: tokens from Task 2.
- Produces: layout `body > aside.sidebar + div.main > (div.topbar + main)`. Sidebar contains `<nav id="main-nav">` with the SAME ten `data-section` buttons; topbar contains `#lang-toggle`, `#theme-toggle` (new), `#connection-toggle`, `#auth-user`, `#logout-btn`, `#nav-toggle`. Nav badge spans: `#nav-badge-screens`, `#nav-badge-walls`, `#nav-badge-sites` (Task 5 fills them).

- [ ] **Step 1: Replace the `<header>…</header>` block in `frontend/index.html` with:**

```html
<aside class="sidebar" id="sidebar">
  <div class="brand">
    <img class="brand-face" src="assets/faces/v1_kawaii.png" alt="" width="34" height="30" />
    <div class="brand-name">Khanshoof</div>
  </div>
  <nav id="main-nav" class="nav-groups">
    <div class="nav-group">
      <div class="nav-group-label" data-i18n="nav.group_displays">Displays</div>
      <button data-section="screens" data-i18n-html="nav.screens">Screens<span class="nav-badge hidden" id="nav-badge-screens"></span></button>
      <button data-section="walls" data-i18n-html="nav.walls">Walls<span class="nav-badge hidden" id="nav-badge-walls"></span></button>
      <button data-section="sites" data-i18n-html="nav.sites">Sites<span class="nav-badge hidden" id="nav-badge-sites"></span></button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label" data-i18n="nav.group_content">Content</div>
      <button data-section="media" data-i18n="nav.media">Media</button>
      <button data-section="playlists" data-i18n="nav.playlists">Playlists</button>
      <button data-section="schedules" data-i18n="nav.schedules">Schedules</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label" data-i18n="nav.group_org">Organization</div>
      <button data-section="users" data-i18n="nav.users">Users</button>
      <button data-section="api-keys" data-i18n="nav.api_keys">API Keys</button>
      <button data-section="audit-log" data-i18n="nav.audit_log">Audit log</button>
      <button data-section="billing" data-i18n="nav.billing">Billing</button>
    </div>
  </nav>
  <div class="sidebar-foot">
    <span id="auth-user"></span>
    <button id="connection-toggle" class="secondary-btn"><span data-i18n="auth.connection">⚙ Connection</span></button>
    <button id="logout-btn" class="secondary-btn hidden" data-i18n="auth.signout">Sign Out</button>
  </div>
</aside>
<div class="main">
  <div class="topbar">
    <button class="nav-toggle" id="nav-toggle" aria-label="Toggle navigation" data-i18n-aria-label="nav.toggle_aria">
      <span></span><span></span><span></span>
    </button>
    <div class="topbar-actions">
      <button id="lang-toggle" class="secondary-btn lang-toggle" data-i18n-aria-label="nav.lang_toggle_aria" aria-label="Switch language">
        <span data-i18n="lang.toggle_label">عربي</span>
      </button>
      <button id="theme-toggle" class="icon-btn" aria-label="Toggle theme" data-i18n-aria-label="nav.theme_toggle_aria">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
      </button>
    </div>
  </div>
  <!-- existing <main> moves here, unchanged inside -->
```

Close `</div>` after `</main>`. `#subscription-banner` and `#toast-container` stay direct children of `<body>` (they are fixed-position).

**Caution — two contract details:**
1. `app.js:150` selects `nav button[data-section]` — the buttons above are inside `<nav id="main-nav">`, so the selector still matches.
2. The old markup had nav item text as bare text nodes; the badge `<span>` inside the button means `data-i18n` (which sets `textContent`) would wipe the badge. So for the three badge buttons use `data-i18n-html` — **check `frontend/i18n.js` first**: if `applyTranslations` only handles `data-i18n` via `textContent`, add this handling to `i18n.js`:

```js
r.querySelectorAll("[data-i18n-html]").forEach(el => {
  const label = t(el.dataset.i18nHtml);
  const badge = el.querySelector(".nav-badge");
  el.textContent = label;
  if (badge) el.appendChild(badge);
});
```

- [ ] **Step 2: Port shell CSS from the mockup**

In `frontend/styles.css`, delete the old `header`, `.header-top`, `.header-row`, `nav button`, `.auth-status`, `.nav-toggle` rules. Port from the mockup: `.sidebar`, `.brand`, `.brand-face` (incl. hover tilt), `.nav-group`, `.nav-group-label`, `.nav-item` → **rename selector to `#main-nav button`**, `.nav-badge`, `.sidebar-foot`, `.main`, `.topbar`, `.topbar-actions`, `.icon-btn`, `.pill-btn`. Add the active-state mapping (app.js toggles `nav-active`, mockup used `.active`):

```css
#main-nav button.nav-active { background: var(--accent-soft); color: var(--accent); font-weight: 570; }
```

Add `body { display: flex; }` and the mobile drawer (reusing the existing `nav-open` class that `app.js` toggles on `#main-nav`):

```css
@media (max-width: 900px) {
  .sidebar { position: fixed; inset-inline-start: 0; top: 0; z-index: 60; transform: translateX(-100%); transition: transform var(--t); }
  [dir="rtl"] .sidebar { transform: translateX(100%); }
  .sidebar:has(#main-nav.nav-open) { transform: none; }
  .nav-toggle { display: inline-flex; }
}
@media (min-width: 901px) { .nav-toggle { display: none; } }
```

- [ ] **Step 3: Add the four new i18n keys to BOTH locale files**

`frontend/i18n/en.json`: `"nav.group_displays": "Displays"`, `"nav.group_content": "Content"`, `"nav.group_org": "Organization"`, `"nav.theme_toggle_aria": "Toggle dark / light theme"`
`frontend/i18n/ar.json`: `"nav.group_displays": "الشاشات"`, `"nav.group_content": "المحتوى"`, `"nav.group_org": "المؤسسة"`, `"nav.theme_toggle_aria": "تبديل الوضع الداكن / الفاتح"`

- [ ] **Step 4: Verify + rebuild + smoke**

```bash
bash scripts/check_ui_contract.sh          # expected: UI contract OK
docker compose build frontend && docker compose up -d frontend
curl -s http://localhost:3000/ | grep -c 'id="main-nav"'   # expected: 1
```
Browser: sidebar renders, all 10 sections switch, hamburger opens drawer at narrow width, AR flips sidebar to the right.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json frontend/i18n.js
git commit -m "feat(ui): sidebar + topbar shell replacing horizontal top-nav"
```

### Task 4: Theme system (follow-system default, persisted override)

**Files:**
- Modify: `frontend/index.html` (`<head>` bootstrap script)
- Modify: `frontend/app.js` (toggle handler, near the `#lang-toggle` handler)

**Interfaces:**
- Produces: `data-theme` on `<html>` before first paint; `#theme-toggle` cycles dark↔light and persists to `localStorage["khanshoof_theme"]`.

- [ ] **Step 1: Add no-FOUC bootstrap to `<head>` (before the stylesheet link)**

```html
<script>
  (function () {
    var saved = localStorage.getItem("khanshoof_theme");
    var theme = saved === "dark" || saved === "light"
      ? saved
      : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
  })();
</script>
```

- [ ] **Step 2: Add toggle handler in `app.js`**

```js
document.getElementById("theme-toggle")?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("khanshoof_theme", next);
});
```

- [ ] **Step 3: Verify**

Rebuild frontend container. Browser: toggle flips instantly with no flash on reload; `localStorage.khanshoof_theme` persists; clearing it and switching OS scheme changes the default. `bash scripts/check_ui_contract.sh` → OK.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat(ui): dark/light theme — follow-system default, persisted toggle"
```

### Task 5: Sidebar count badges

**Files:**
- Modify: `frontend/app.js` (in `loadScreens`, `loadSites`, and the walls loader — locate with `grep -n "function loadWalls\|function loadSites" frontend/app.js`)

**Interfaces:**
- Consumes: `#nav-badge-screens|walls|sites` spans from Task 3; `state.screens`, `state.sites`, `state.walls` arrays already populated by the loaders.
- Produces: `updateNavBadge(name, count)` helper.

- [ ] **Step 1: Add helper + calls**

```js
function updateNavBadge(name, count) {
  const el = document.getElementById(`nav-badge-${name}`);
  if (!el) return;
  el.textContent = String(count);
  el.classList.toggle("hidden", !count);
}
```

At the end of `loadScreens()`: `updateNavBadge("screens", state.screens.length);` — same pattern in the sites and walls loaders.

- [ ] **Step 2: Verify + commit**

Browser: badges show real counts after login; contract check OK.

```bash
git add frontend/app.js
git commit -m "feat(ui): live count badges on Screens/Walls/Sites nav items"
```

### Task 6: Phase 1 gate — full verification + PR

- [ ] **Step 1: Full checks**

```bash
bash scripts/check_ui_contract.sh                 # UI contract OK
cd backend && python -m pytest -q && cd ..       # all green (no backend changes — proves harness)
```

- [ ] **Step 2: Browser smoke matrix** — dark×EN, dark×AR, light×EN, light×AR, mobile width: login, switch all 10 sections, hamburger drawer, sign out.

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/ui-overhaul
gh pr create --title "feat(ui): Phase 1 — design tokens, sidebar shell, themes" --body "Dashboard shell per docs/superpowers/specs/2026-07-09-ui-overhaul-design.md. Sections still use legacy inner styles (bridged variables); Phase 2 restyles them."
```

---

## Phase 2 — Dashboard components (branch `feature/ui-overhaul-p2` off the merged main)

### Task 7: Core component styles — buttons, forms, panels, tables, toasts, modals

**Files:**
- Modify: `frontend/styles.css` (replace the legacy blocks for `.panel`, `button`/`.save-btn`/`.delete-btn`/`.secondary-btn`, `input/select/textarea`, `table` variants, `#toast-container`/`.toast*`, `.confirm-dialog-*`, `.media-picker-*`, `.sub-banner`)

**Interfaces:**
- Consumes: tokens (Task 2). Class names frozen per Global Constraints.
- Produces: `.btn-primary`-equivalent styling on existing button classes; `.panel` = mockup "card" surface (`--surface`, 1px `--border`, `--r-lg` radius, `--shadow-card`).

- [ ] **Step 1: Port from the mockup**, mapping mockup selectors → existing app classes (style the EXISTING names; do not touch app.js):

| Mockup | App selector(s) |
|---|---|
| `.btn.btn-primary` | `button[type="submit"], .save-btn` |
| `.btn.btn-ghost` | `.secondary-btn, .access-btn, .preview-btn` |
| red-tinted ghost (new: ghost + `color: var(--red)`) | `.delete-btn` |
| `.card` surface | `.panel` |
| `.search` input chrome | `input, select, textarea` |
| `.seg` | `.auth-tabs` (and reuse in section filter bars) |

Toasts: keep the `.toast`/`.toast-exit` animation hooks; surface = `--surface-2`, border-inline-start 3px in `--green`/`--red`/`--amber` by type. Confirm-dialog + media-picker: dark overlay `rgba(0,0,0,.55)`, modal = `--surface`, `--r-lg`, `--shadow-pop`. Subscription banner: `--amber` tint band, no longer full-saturation.

- [ ] **Step 2: Sweep for dead pastel rules**

Run: `grep -n "FFF8F0\|FDF3D6\|E8DCC6\|C9B8E0\|B5DABD\|E8B4C6\|IBM Plex Serif" frontend/styles.css`
Expected: no output — delete any survivors.

- [ ] **Step 3: Verify + commit**

Contract check OK; rebuild; browser: every section renders legibly in all 4 theme×lang combos (content unstyled-but-clean is OK for sections not yet reached).

```bash
git add frontend/styles.css
git commit -m "feat(ui): core components — buttons, forms, panels, tables, toasts, modals"
```

### Task 8: Screens section — stat row + redesigned screen cards

**Files:**
- Modify: `frontend/index.html` (inside `<section id="screens">`: add stats row above `#screens-list`)
- Modify: `frontend/app.js:581-…` (`renderScreens`)
- Modify: `frontend/styles.css` (port `.stats`, `.stat*`, `.grid`, `.card` internals, `.status-dot`, `.thumb` from mockup)

**Interfaces:**
- Consumes: `screen.is_online` (bool, from API), `screen.last_seen` (ISO string or null).
- Produces: `screenStatus(screen) -> "online"|"stale"|"offline"`, `renderScreenStats()`; card markup keeps ALL `data-playlist-select/data-schedule-select/data-save-screen/data-zones-screen/data-access-screen/data-preview-screen/data-delete-screen` hooks and the listener wiring **unchanged**.

- [ ] **Step 1: Add markup after the `<h2>` in `#screens`:**

```html
<div class="stats" id="screens-stats">
  <div class="stat"><div class="stat-label" data-i18n="screens.stat_online">Screens online</div><div class="stat-value" id="stat-screens-online">—</div></div>
  <div class="stat"><div class="stat-label" data-i18n="screens.stat_playing">Playing now</div><div class="stat-value" id="stat-screens-playing">—</div></div>
  <div class="stat"><div class="stat-label" data-i18n="screens.stat_media">Media items</div><div class="stat-value" id="stat-media-count">—</div></div>
  <div class="stat"><div class="stat-label" data-i18n="screens.stat_sites">Sites</div><div class="stat-value" id="stat-sites-count">—</div></div>
</div>
```

i18n keys (both files): en `"screens.stat_online":"Screens online"`, `"screens.stat_playing":"Playing now"`, `"screens.stat_media":"Media items"`, `"screens.stat_sites":"Sites"`; ar `"الشاشات المتصلة"`, `"قيد التشغيل الآن"`, `"عناصر الوسائط"`, `"المواقع"`.

- [ ] **Step 2: In `app.js`, add status + stats helpers and rework ONLY the template string of `renderScreens`:**

```js
function screenStatus(screen) {
  if (screen.is_online) return "online";
  if (!screen.last_seen) return "offline";
  const ageMin = (Date.now() - new Date(screen.last_seen).getTime()) / 60000;
  return ageMin < 10 ? "stale" : "offline";
}

function renderScreenStats() {
  const online = state.screens.filter((s) => s.is_online).length;
  const playing = state.screens.filter((s) => s.is_online && (s.playlist_id || s.schedule_id)).length;
  document.getElementById("stat-screens-online").textContent = `${online} / ${state.screens.length}`;
  document.getElementById("stat-screens-playing").textContent = String(playing);
  document.getElementById("stat-media-count").textContent = String(state.media?.length ?? "—");
  document.getElementById("stat-sites-count").textContent = String(state.sites?.length ?? "—");
}
```

Call `renderScreenStats()` at the top of `renderScreens()`. In the card template replace the old `<h3>` + `Status:` line with the mockup card header (dot + title + meta), **keeping every existing `data-*` attribute, the pair-code line, player URL link, selects and buttons exactly as they are**:

```js
const status = screenStatus(screen);
card.innerHTML = `
  <div class="card-title-row">
    <span class="status-dot ${status}"></span>
    <h3 class="card-title">${escHtml(screen.name)}</h3>
  </div>
  <div class="card-meta">
    <span>${escHtml(screen.site_name || Khan.t("screens.site_unassigned_label", "Unassigned"))}</span>
    ${screen.location ? `<span>${escHtml(screen.location)}</span>` : ""}
    ${screen.resolution ? `<span class="mono">${escHtml(screen.resolution)}</span>` : ""}
  </div>
  <div class="card-pair">${Khan.t("screens.pair_code_label", "Pair code")}: <strong class="pair-code">${escHtml(screen.pair_code)}</strong>
    <a href="${escAttr(playerUrl)}" target="_blank" rel="noreferrer" data-i18n="screens.player_url">Player URL ↗</a></div>
  <div class="card-actions">
    <select data-playlist-select="${screen.id}">${playlistOptions}</select>
    <select data-schedule-select="${screen.id}">${scheduleOptions}</select>
    <button class="save-btn"    data-save-screen="${screen.id}">${Khan.t("screens.save", "Save")}</button>
    <button class="save-btn"    data-zones-screen="${screen.id}">${Khan.t("screens.zones", "Zones")}</button>
    <button class="access-btn"  data-access-screen="${screen.id}">${Khan.t("screens.access", "Access")}</button>
    <button class="preview-btn" data-preview-screen="${screen.id}">${Khan.t("screens.preview", "Preview")}</button>
    <button class="delete-btn"  data-delete-screen="${screen.id}">${Khan.t("screens.delete", "Delete")}</button>
  </div>`;
```

(If any `Khan.t` key above is missing from the locale files, add it to both — check with `grep '"screens\.' frontend/i18n/en.json`.)

- [ ] **Step 3: Port CSS** — `.stats/.stat/.stat-label/.stat-value`, `.status-dot.online/.stale/.offline` (keep old `.status-online/.status-offline` rules too — other sections still emit them), `#screens-list` as `.grid` (auto-fill minmax 250px).

- [ ] **Step 4: Verify + commit**

Contract check; rebuild; browser: stats correct, dots correct (pair a screen, watch it go green; stop the player, amber within 90s + correct after 10 min), save/zones/access/preview/delete all still work, AR OK.

```bash
git add frontend/index.html frontend/app.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "feat(ui): screens section — stat row, status dots with stale tier, card redesign"
```

### Task 9: Remaining sections restyle (Media, Playlists, Schedules, Sites, Walls, Users, API Keys, Audit, Billing)

**Files:**
- Modify: `frontend/styles.css` only (section-specific blocks: `.media-*`, `.playlist-*`, `.schedule-*`, `.walls-*`, `.zones-*`, `.audit-log-table`, `.api-keys-*`, `.billing-*`, `.plan-card`, `.pricing-*`)

**Interfaces:** CSS-only task. No HTML/JS changes; if a visual can't be achieved without markup change, leave it and note it for Task 15 (polish) rather than touching hooks here.

- [ ] **Step 1..N (one commit per section, same recipe):** restyle the section's existing markup to tokens — surfaces `--surface`, borders `--border`, radii `--r-md/lg`, mono for codes (`pair-code`, API key prefixes), table style from mockup (header row `--text-3` uppercase 10.5px, row borders `--border`, hover `--surface-2`). Zones canvas + walls wizard: dark canvas `--bg-2`, grid lines `--border-2`, keep `zones-canvas-grid` class behavior. After each section: contract check + browser check of that section's full flow (upload in Media incl. drag-drop `dropzone-active` tint, reorder in Playlists, wizard in Walls, KNET redirect stub in Billing) in dark+light. Commit per section:

```bash
git add frontend/styles.css && git commit -m "feat(ui): restyle <section> to new tokens"
```

### Task 10: Phase 2 gate — verification + PR

- [ ] Contract check; `cd backend && python -m pytest -q`; full browser matrix (4 combos × all sections); `gh pr create --title "feat(ui): Phase 2 — all dashboard sections on the new design system"`.

---

## Phase 3 — Auth screens + OAuth pages (branch `feature/ui-overhaul-p3`)

### Task 11: SPA auth panel restyle

**Files:**
- Modify: `frontend/styles.css` (`.auth-tabs`, `.auth-form`, `.btn-social`, `.social-auth-divider`, `.helper-text`, `.password-hint`)

**Interfaces:** CSS-only. Ids `auth-panel`, `login-form`, `signup-*` untouched.

- [ ] **Step 1:** Center a 400px `--surface` card; tabs = `.seg` style; social buttons = ghost buttons with brand icons; OTP input in `--mono` with letter-spacing. Verify login + full signup (request→OTP→password) + Google button render; commit `feat(ui): auth screens restyle`.

### Task 12: OAuth templates (backend-served)

**Files:**
- Modify: `frontend/oauth-consent.css` (full rewrite to tokens — self-contained copy of the token block; these pages can't reach styles.css vars)
- Modify: `backend/templates/oauth_login.html`, `oauth_consent.html`, `oauth_error.html` (class tweaks + mascot on error page)
- Test: `backend/tests/test_oauth.py` (existing — must stay green)

**Interfaces:** Literal strings `Sign in` and `Authorize` + `{{ client_name }}` PRESERVED (asserted at test_oauth.py:277,292).

- [ ] **Step 1:** Run the OAuth tests FIRST for baseline: `cd backend && python -m pytest tests/test_oauth.py -q` → all pass.
- [ ] **Step 2:** Rewrite `oauth-consent.css`: embed the dark+light token sets with `@media (prefers-color-scheme)` (no JS on these pages), `.oauth-card` = 400px surface card, buttons per Task 7 mapping. Add to `oauth_error.html`: `<img src="https://app.khanshoof.com/assets/faces/v1_big.png" alt="" width="72" style="image-rendering:pixelated">` above the message.
- [ ] **Step 3:** `python -m pytest tests/test_oauth.py -q` → all pass. Manual: complete an OAuth flow against local backend (`/oauth/authorize` with a registered client) and eyeball all three pages.
- [ ] **Step 4:** Commit `feat(ui): OAuth login/consent/error pages on new tokens`; PR `feat(ui): Phase 3 — auth + OAuth surfaces`.

---

## Phase 4 — Landing restyle (branch `feature/ui-overhaul-p4`)

### Task 13: Rebuild landing from the approved mockup

**Files:**
- Modify: `landing/index.html`, `landing/styles.css`
- Reference: `docs/superpowers/specs/2026-07-09-ui-overhaul-mockups/landing.html`

**Interfaces / hard preservation list:**
1. The ENTIRE `<head>` SEO block (title/meta/OpenGraph/Twitter/JSON-LD) byte-for-byte
2. Every `data-i18n*` attribute and every key in `landing/i18n/*.json`
3. Every `data-cta="signup"` hook and href targets (`app.khanshoof.com` links)
4. `robots.txt`, `sitemap.xml`, `llms.txt`, nginx config untouched

- [ ] **Step 1: Extract the preservation baseline**

```bash
grep -oE 'data-i18n[a-z-]*="[^"]+"' landing/index.html | sort -u > /tmp/landing-i18n-before.txt
grep -c 'data-cta="signup"' landing/index.html   # note the count
sed -n '/<head>/,/<\/head>/p' landing/index.html > /tmp/landing-head-before.html
```

- [ ] **Step 2:** Rebuild body from the mockup, section by section (nav, hero + peek + CSS product frame, features, how, pricing, FAQ, CTA band, footer), re-attaching each original `data-i18n` attribute to its corresponding element and keeping original copy as fallback text. `landing/styles.css` = mockup styles + `:lang(ar)` font + logical-property audit. Theme: `@media (prefers-color-scheme)` + the same localStorage toggle snippet as the dashboard (shared key `khanshoof_theme`).

- [ ] **Step 3: Diff the contract**

```bash
grep -oE 'data-i18n[a-z-]*="[^"]+"' landing/index.html | sort -u | diff /tmp/landing-i18n-before.txt -   # empty
sed -n '/<head>/,/<\/head>/p' landing/index.html | diff /tmp/landing-head-before.html -                  # empty (or additions-only: theme bootstrap script)
grep -c 'data-cta="signup"' landing/index.html                                                            # same count
```

- [ ] **Step 4:** `docker compose build landing && docker compose up -d landing`; browser at :3003 — hero peek bob, RTL flip via عربي, pricing ribbon, FAQ accordions, dark+light; Lighthouse-level sanity (no layout shift from the peek image: it has explicit dimensions).
- [ ] **Step 5:** Commit `feat(ui): landing rebuilt on shared design system` + PR `feat(ui): Phase 4 — landing`.

---

## Phase 5 — Mascot moments + polish (branch `feature/ui-overhaul-p5`)

### Task 14: Empty states

**Files:**
- Modify: `frontend/app.js` (list renderers: screens/media/playlists/walls), `frontend/styles.css`

**Interfaces:**
- Produces: `emptyStateHtml(i18nKey, fallback)` used by all four renderers.

- [ ] **Step 1:**

```js
function emptyStateHtml(i18nKey, fallback) {
  return `<div class="empty-state">
    <img src="assets/faces/v1_star.png" alt="" class="empty-state-face" />
    <p>${escHtml(Khan.t(i18nKey, fallback))}</p>
  </div>`;
}
```

In each renderer: `if (!state.screens.length) { container.innerHTML = emptyStateHtml("screens.empty", "No screens yet — pair your first one!"); return; }` (keys ×4 sections, EN+AR). CSS: `.empty-state` centered, `.empty-state-face { width:64px; image-rendering:pixelated; filter:grayscale(1) opacity(.45); transition: filter var(--t); } .empty-state:hover .empty-state-face { filter:none; }`

- [ ] **Step 2:** Verify with the `haseeb` account's fresh org state or a new org; commit `feat(ui): mascot empty states`.

### Task 15: Success/error moments + leftover polish

**Files:**
- Modify: `frontend/app.js` (`toast()` — add optional face), `frontend/styles.css`

- [ ] **Step 1:** Extend `toast(msg, type)`: when `type === "success"` prepend `<img src="assets/faces/v1_heart.png" class="toast-face">`; on API-error toast use `v1_big`. CSS `.toast-face { width:22px; image-rendering:pixelated; }`. Sweep any visuals deferred from Task 9. Full 4-combo browser matrix one last time.
- [ ] **Step 2:** `bash scripts/check_ui_contract.sh && cd backend && python -m pytest -q` → green. Commit `feat(ui): mascot success/error moments + final polish`; PR `feat(ui): Phase 5 — mascot moments + polish`.

---

## Self-review (done at plan-writing time)

- **Spec coverage:** tokens/themes ✔ (T2,T4) · sidebar+badges ✔ (T3,T5) · components ✔ (T7-T9) · auth ✔ (T11) · OAuth ✔ (T12) · landing+SEO ✔ (T13) · mascot table ✔ (T3 brand, T12 error, T13 hero/CTA/footer, T14 empty, T15 success) · compatibility constraints ✔ (T1 enforced everywhere) · follow-system theme ✔ (T4) · stale-tier dots ✔ (T8).
- **Placeholder scan:** Task 9 is intentionally CSS-recipe-per-section against the committed mockup rather than inlined CSS for nine sections — the mockup file in-repo is the concrete source; no TBDs remain.
- **Type consistency:** `screenStatus`/`renderScreenStats`/`updateNavBadge`/`emptyStateHtml` signatures used consistently; `khanshoof_theme` key identical in T4/T12/T13.
