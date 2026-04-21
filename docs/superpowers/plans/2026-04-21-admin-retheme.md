# Sawwii Admin Re-theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-theme the admin SPA at `frontend/` to match the landing page's pastel retro-modern aesthetic (cream/butter/peach/mint/lavender/rose/plum palette, IBM Plex Sans + Serif). Same functionality, same markup structure, same JS behaviour — this is a CSS swap with a handful of surgical markup tweaks. No component refactors.

**Architecture:** The admin uses CSS custom properties (`:root` vars) extensively — 128 var references across `frontend/styles.css`. Task 1 rewrites `:root` values and the font imports; that alone repaints ~80% of the UI correctly. Tasks 2–6 are targeted fixes for components whose current CSS doesn't flatter pastels (glow effects, dark-glass panels, dark-mode input styles). Task 7 is responsive + regression.

**Tech Stack:** CSS only where possible. No framework. IBM Plex Sans + IBM Plex Serif + IBM Plex Mono via Google Fonts `@import`. Two inline `var(--...)` references in `frontend/app.js` picked up for free since var names are preserved.

---

## Prerequisites

Work from `/home/ahmed/signage` on a new branch `feature/admin-retheme` off `main`.

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage pull --ff-only
git -C /home/ahmed/signage checkout -b feature/admin-retheme
```

The Docker stack must be up. Regression suite must be green before starting:

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
# Expected: 7 passed
```

## Palette Mapping (authoritative — used across all tasks)

The existing admin var names stay the same — we only change their **values**. This keeps the 128 existing `var(--x)` references working without a find-replace pass.

| Existing var | Old (dark) | **New (pastel)** | Notes |
|---|---|---|---|
| `--bg-deep` | `#020617` | `#FFF8F0` (cream) | page background |
| `--bg-surface` | `#0f172a` | `#FDF3D6` (butter) | elevated surface |
| `--bg-panel` | `rgba(13,20,40,0.88)` | `#FFFFFF` | opaque instead of glass |
| `--bg-card` | `rgba(2,8,24,0.72)` | `#FFF8F0` (cream) | card fill |
| `--bg-input` | `rgba(2,6,23,0.55)` | `#FFFFFF` | input fill |
| `--text-primary` | `#e2e8f0` | `#3E2B4F` (plum) | body text |
| `--text-secondary` | `#94a3b8` | `#5D4A66` (cocoa) | secondary |
| `--text-muted` | `#475569` | `#A89382` (sand) | muted |
| `--cyan` | `#22d3ee` | `#E09478` (peach-deep) | primary accent |
| `--blue` | `#38bdf8` | `#F4B9A1` (peach) | secondary accent |
| `--indigo` | `#6366f1` | `#C9B8E0` (lavender) | tertiary accent |
| `--green` | `#34d399` | `#B5DABD` (mint) | success/active |
| `--orange` | `#f97316` | `#E09478` (peach-deep) | warning — peach reads warm |
| `--red` | `#ef4444` | `#C94F6D` (deep rose) | error — needs deeper rose than landing's `--rose` for legibility |
| `--border` | `rgba(148,163,184,0.14)` | `#E8DCC6` (cream-border) | |
| `--border-accent` | `rgba(34,211,238,0.28)` | `#E09478` (peach-deep) | |
| `--glow-cyan` | `0 0 18px rgba(34,211,238,0.35)` | `0 2px 0 #E8DCC6, 0 10px 30px rgba(62,43,79,0.12)` | drop glow → card stack shadow |
| `--glow-indigo` | `0 0 18px rgba(99,102,241,0.35)` | `0 2px 0 #C9B8E0, 0 10px 30px rgba(62,43,79,0.12)` | |
| `--shadow-lg` | `0 12px 40px rgba(0,0,0,0.55)` | `0 10px 30px rgba(62,43,79,0.12)` | |
| `--shadow-md` | `0 4px 16px rgba(0,0,0,0.35)` | `0 4px 16px rgba(62,43,79,0.08)` | |
| `--r-sm` | `5px` | `10px` | softer corners |
| `--r-md` | `10px` | `14px` | |
| `--r-lg` | `16px` | `20px` | |
| `--r-pill` | `999px` | `999px` | unchanged |
| `--font` | `'Inter', ...` | `'IBM Plex Sans', 'Inter', system-ui, sans-serif` | |
| `--mono` | `'JetBrains Mono', ...` | `'IBM Plex Mono', 'JetBrains Mono', 'Fira Code', monospace` | |

**Additions** (new vars the landing uses — we add them for components Task 2+ will touch):

| New var | Value | Purpose |
|---|---|---|
| `--font-display` | `'IBM Plex Serif', Georgia, serif` | headings, brand |
| `--plum` | `#3E2B4F` | explicit plum for new component CSS |
| `--peach` | `#F4B9A1` | explicit peach for new component CSS |
| `--peach-deep` | `#E09478` | ditto |
| `--cream` | `#FFF8F0` | ditto |
| `--butter` | `#FDF3D6` | ditto |
| `--mint` | `#B5DABD` | ditto |
| `--lavender` | `#C9B8E0` | ditto |
| `--rose` | `#E8B4C6` | ditto |
| `--cream-border` | `#E8DCC6` | ditto |
| `--plum-shadow` | `rgba(62,43,79,0.12)` | ditto |
| `--grain` | `rgba(62,43,79,0.035)` | paper texture |

## File Structure

```
frontend/
├── index.html         # add IBM Plex links; tiny markup tweak for nav logo
├── styles.css         # REWRITE :root; targeted fixes per task
└── app.js             # one inline-style call updated (line 424); one inline-style string updated (line 226)
```

No new files. No new directories. No new JS. No new behaviour.

---

## Task 1: Palette + typography foundation (root-level repaint)

**Files:**
- Modify: `frontend/styles.css` — rewrite `:root` block, swap `@import`
- Modify: `frontend/index.html` — no change in this task (fonts come through the @import)

- [ ] **Step 1: Replace the `@import` line (line 2) and the entire `:root` block**

In `frontend/styles.css`, the top of the file currently looks like this:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

:root {
  --bg-deep:    #020617;
  ...
}
```

Replace those two blocks (the `@import` plus everything from `:root {` through its closing `}`) with:

```css
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,500;0,700;1,500&family=IBM+Plex+Mono:wght@400;600&display=swap');

:root {
  /* ── Existing admin var names kept; VALUES remapped to pastel ─ */
  --bg-deep:        #FFF8F0;
  --bg-surface:     #FDF3D6;
  --bg-panel:       #FFFFFF;
  --bg-card:        #FFF8F0;
  --bg-input:       #FFFFFF;

  --text-primary:   #3E2B4F;
  --text-secondary: #5D4A66;
  --text-muted:     #A89382;

  --cyan:           #E09478;  /* now peach-deep (primary accent) */
  --blue:           #F4B9A1;  /* now peach */
  --indigo:         #C9B8E0;  /* now lavender */
  --green:          #B5DABD;  /* now mint */
  --orange:         #E09478;  /* warning → peach-deep */
  --red:            #C94F6D;  /* error → deep rose, legible on cream */

  --border:         #E8DCC6;
  --border-accent:  #E09478;

  --glow-cyan:      0 2px 0 #E8DCC6, 0 10px 30px rgba(62, 43, 79, 0.12);
  --glow-indigo:    0 2px 0 #C9B8E0, 0 10px 30px rgba(62, 43, 79, 0.12);

  --r-sm:   10px;
  --r-md:   14px;
  --r-lg:   20px;
  --r-pill: 999px;

  --shadow-lg: 0 10px 30px rgba(62, 43, 79, 0.12);
  --shadow-md: 0 4px 16px rgba(62, 43, 79, 0.08);

  --t-fast:   140ms ease;
  --t-normal: 260ms cubic-bezier(0.2, 0.7, 0.2, 1);

  --font:         'IBM Plex Sans', 'Inter', system-ui, -apple-system, sans-serif;
  --mono:         'IBM Plex Mono', 'JetBrains Mono', 'Fira Code', monospace;
  --font-display: 'IBM Plex Serif', Georgia, serif;

  /* ── Explicit pastel names (used by new component CSS in later tasks) ── */
  --cream:        #FFF8F0;
  --butter:       #FDF3D6;
  --peach:        #F4B9A1;
  --peach-deep:   #E09478;
  --mint:         #B5DABD;
  --lavender:     #C9B8E0;
  --rose:         #E8B4C6;
  --plum:         #3E2B4F;
  --cocoa:        #5D4A66;
  --sand:         #A89382;
  --cream-border: #E8DCC6;
  --plum-shadow:  rgba(62, 43, 79, 0.12);
  --grain:        rgba(62, 43, 79, 0.035);
}
```

- [ ] **Step 2: Rebuild & visually verify repaint**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build frontend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d frontend
curl -sSf http://localhost:3000/styles.css | grep -E 'IBM Plex|FFF8F0|3E2B4F' | head -5
```

Expected: `IBM+Plex+Sans` appears (fonts), `#FFF8F0` + `#3E2B4F` appear (pastel values live).

Open `http://192.168.18.192:3000/` in a browser. The admin should now render on a cream background with plum text, peach accents instead of cyan, and IBM Plex typography. Many components will look half-right — that's expected. We fix them in subsequent tasks. **Do not iterate visual fixes inside Task 1**; commit and move on.

- [ ] **Step 3: Run regression suite**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `7 passed`. (This task touches no backend files, but regression is cheap and catches surprises.)

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(admin): pastel palette + IBM Plex typography at :root"
```

---

## Task 2: Global layout — body, header, brand bar

**Files:**
- Modify: `frontend/styles.css` — replace body/header rules
- Modify: `frontend/index.html` — swap brand mark + nav layout to match landing

- [ ] **Step 1: Locate the current body + header CSS**

The admin uses `<body>` + `<header class="header">` + `<div class="header-top">` + `<button class="nav-toggle">`. Find those rules in `frontend/styles.css`. They currently set dark glassmorphism backgrounds and cyan/indigo gradient text.

- [ ] **Step 2: Replace the body rule**

Find the existing `body { ... }` block and replace with:

```css
body {
  font-family: var(--font);
  font-size: 15px;
  line-height: 1.6;
  color: var(--plum);
  background: var(--cream);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* Subtle paper-grain overlay (same as landing) */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  background-image:
    radial-gradient(var(--grain) 1px, transparent 1px),
    radial-gradient(var(--grain) 1px, transparent 1px);
  background-size: 3px 3px, 5px 5px;
  background-position: 0 0, 1px 2px;
  opacity: 0.6;
}
```

- [ ] **Step 3: Replace header styles**

Find the `.header`, `.header-top`, `.header-row`, `.auth-status`, and `.nav-toggle` rules. Replace them (keep the rule order, just swap bodies) with:

```css
.header {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(255, 248, 240, 0.92);
  backdrop-filter: saturate(140%) blur(12px);
  border-bottom: 1.5px solid var(--cream-border);
  padding: 14px 24px;
}

.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.header h1 {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--plum);
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  /* Remove any existing gradient background-clip text trick */
  background: none;
  -webkit-text-fill-color: var(--plum);
}

.header h1::before {
  content: '◠';
  display: inline-grid;
  place-items: center;
  width: 32px; height: 32px;
  background: var(--peach);
  border: 1.5px solid var(--plum);
  border-radius: 10px;
  font-size: 18px;
  line-height: 1;
}

.header-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.auth-status {
  display: inline-flex;
  gap: 10px;
  align-items: center;
}

.nav-toggle {
  display: none;
  background: transparent;
  border: 0;
  width: 36px; height: 36px;
  cursor: pointer;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
  padding: 0;
}

.nav-toggle span {
  display: block;
  width: 22px;
  height: 2px;
  background: var(--plum);
  border-radius: 2px;
}

@media (max-width: 880px) {
  .nav-toggle { display: flex; }
}
```

- [ ] **Step 4: Tweak the HTML brand mark**

Open `frontend/index.html`. Find the line with `<h1>Sawwii</h1>` (or whatever the current brand line is). Ensure it's just `<h1>Sawwii</h1>` with no inline wrapping span — the `::before` pseudo-element handles the peach square. If the existing markup wraps the mark in a span already, delete that span.

- [ ] **Step 5: Rebuild and verify**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build frontend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d frontend
```

Open `http://192.168.18.192:3000/`. Expect: sticky translucent-cream header with a `◠` peach tile + "Sawwii" wordmark in IBM Plex Serif. At <880px viewport, hamburger appears.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css frontend/index.html
git -C /home/ahmed/signage commit -m "feat(admin): sticky pastel header + brand mark"
```

---

## Task 3: Auth panel (Sign In / Create Account)

**Files:**
- Modify: `frontend/styles.css` — replace `.panel`, `.auth-tabs`, `.auth-tab`, `.auth-form`, `.helper-text` rules

- [ ] **Step 1: Replace `.panel` base**

Find the existing `.panel` rule. Replace with:

```css
.panel {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-lg);
  padding: 28px;
  box-shadow: var(--shadow-md);
  margin-bottom: 20px;
}

/* Auth panel is centred on the page for a focused sign-in */
#auth-panel {
  max-width: 440px;
  margin: 48px auto;
  padding: 36px 32px;
}
```

- [ ] **Step 2: Replace auth tabs**

Find `.auth-tabs` and `.auth-tab` rules. Replace with:

```css
.auth-tabs {
  display: flex;
  gap: 6px;
  background: var(--butter);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-pill);
  padding: 4px;
  margin-bottom: 24px;
}

.auth-tab {
  flex: 1;
  padding: 10px 16px;
  background: transparent;
  color: var(--cocoa);
  border: 0;
  border-radius: var(--r-pill);
  font-family: var(--font);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast);
}

.auth-tab.active {
  background: var(--peach);
  color: var(--plum);
  box-shadow: 0 2px 0 var(--peach-deep);
}

.auth-tab:not(.active):hover { color: var(--plum); }
```

- [ ] **Step 3: Replace auth form + helper text**

Find `.auth-form` and `.helper-text`. Replace with:

```css
.auth-form {
  display: grid;
  gap: 14px;
}

.auth-form input {
  width: 100%;
  padding: 12px 14px;
  background: var(--bg-input);
  color: var(--plum);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-md);
  font-family: var(--font);
  font-size: 15px;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.auth-form input:focus {
  outline: 0;
  border-color: var(--peach-deep);
  box-shadow: 0 0 0 3px rgba(224, 148, 120, 0.18);
}

.auth-form input::placeholder { color: var(--sand); }

.auth-form button[type="submit"] {
  width: 100%;
  padding: 13px 18px;
  background: var(--peach);
  color: var(--plum);
  border: 1.5px solid var(--peach-deep);
  border-radius: var(--r-md);
  font-family: var(--font);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), transform var(--t-fast);
  box-shadow: 0 2px 0 var(--peach-deep), 0 8px 20px var(--plum-shadow);
}

.auth-form button[type="submit"]:hover {
  background: var(--peach-deep);
  color: var(--cream);
  transform: translateY(-1px);
}

.helper-text {
  color: var(--sand);
  font-size: 13px;
  line-height: 1.5;
}

.helper-text a { color: var(--peach-deep); font-weight: 600; }
.helper-text a:hover { color: var(--plum); }
```

- [ ] **Step 4: Rebuild and verify**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build frontend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d frontend
```

Open the admin in a logged-out incognito window. Expect: a centred cream card with rounded corners, a butter pill-shaped tab strip (Sign In active is peach-filled), and pastel inputs + a peach submit button. Test both tabs — they should toggle cleanly.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(admin): pastel auth panel (tabs + forms)"
```

---

## Task 4: Buttons + form inputs (global)

**Files:**
- Modify: `frontend/styles.css` — replace `.save-btn`, `.secondary-btn`, generic `button`, `input`, `select`, `textarea`, checkbox label rules

- [ ] **Step 1: Replace `.save-btn` (primary CTA used across the app)**

Find `.save-btn` rule. Replace with:

```css
.save-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  background: var(--peach);
  color: var(--plum);
  border: 1.5px solid var(--peach-deep);
  border-radius: var(--r-md);
  font-family: var(--font);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), transform var(--t-fast), box-shadow var(--t-fast);
  box-shadow: 0 2px 0 var(--peach-deep);
}

.save-btn:hover {
  background: var(--peach-deep);
  color: var(--cream);
  transform: translateY(-1px);
  box-shadow: 0 3px 0 var(--peach-deep), 0 6px 16px var(--plum-shadow);
}

.save-btn:disabled {
  background: var(--cream-border);
  color: var(--sand);
  border-color: var(--cream-border);
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}
```

- [ ] **Step 2: Replace `.secondary-btn`**

```css
.secondary-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  background: transparent;
  color: var(--plum);
  border: 1.5px solid var(--plum);
  border-radius: var(--r-md);
  font-family: var(--font);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast);
}

.secondary-btn:hover {
  background: var(--plum);
  color: var(--cream);
}

.secondary-btn:disabled {
  color: var(--sand);
  border-color: var(--cream-border);
  cursor: not-allowed;
}

.secondary-btn:disabled:hover {
  background: transparent;
  color: var(--sand);
}
```

- [ ] **Step 3: Replace the global input/select/textarea style**

Find the generic `input, select, textarea { ... }` rule (or whatever form-control selector is used). Replace with:

```css
input[type="text"],
input[type="email"],
input[type="password"],
input[type="number"],
input[type="url"],
input[type="search"],
select,
textarea {
  width: 100%;
  padding: 10px 12px;
  background: var(--bg-input);
  color: var(--plum);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-md);
  font-family: var(--font);
  font-size: 14px;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

input[type="text"]:focus,
input[type="email"]:focus,
input[type="password"]:focus,
input[type="number"]:focus,
input[type="url"]:focus,
input[type="search"]:focus,
select:focus,
textarea:focus {
  outline: 0;
  border-color: var(--peach-deep);
  box-shadow: 0 0 0 3px rgba(224, 148, 120, 0.18);
}

input::placeholder, textarea::placeholder { color: var(--sand); }

label {
  font-size: 13px;
  font-weight: 500;
  color: var(--cocoa);
}

/* Checkbox label */
.checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--cocoa);
  cursor: pointer;
}

.checkbox input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--peach-deep);
}
```

- [ ] **Step 4: Update the inline `var(--cyan)` call in app.js**

Open `frontend/app.js`. Line 424 currently reads:

```javascript
heading.style.cssText = "font-size:14px;font-weight:700;color:var(--cyan);font-family:var(--mono);margin-bottom:10px";
```

The `var(--cyan)` now resolves to peach-deep (correct semantically as primary accent), but the value works — no change required. Leave it as-is. This step is a no-op; documented only so a future reader sees it was reviewed.

- [ ] **Step 5: Rebuild & verify**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build frontend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d frontend
```

Log in. Poke around — every save button should be peach, every secondary should be a plum-outlined ghost, inputs should have a soft peach focus ring. No more cyan glow rings.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(admin): pastel buttons + form controls"
```

---

## Task 5: Panels, cards, and lists (dashboard content)

**Files:**
- Modify: `frontend/styles.css` — `.plan-card*`, `.list`, list-item styles, section titles, tables

- [ ] **Step 1: Replace `.plan-card` + its sub-rules**

Find all rules starting with `.plan-card`. Replace the block with:

```css
.plan-card {
  background: var(--butter);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-lg);
  padding: 24px 28px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
}

.plan-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.plan-card h2,
.plan-card .plan-card-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 700;
  color: var(--plum);
  margin: 0;
}

.plan-card-sub {
  color: var(--cocoa);
  font-size: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.plan-card-dot { color: var(--sand); }

.plan-status {
  display: inline-block;
  padding: 5px 12px;
  border-radius: var(--r-pill);
  font-size: 12px;
  font-weight: 600;
  border: 1.5px solid var(--plum);
  background: var(--mint);
  color: var(--plum);
  white-space: nowrap;
}

.plan-status-trial   { background: var(--mint); }
.plan-status-active  { background: var(--mint); }
.plan-status-warn    { background: var(--peach); }
.plan-status-danger  { background: var(--rose); }
```

If the plan-card HTML uses different class names (e.g. `.plan-card-status` vs `.plan-status`), adjust the selectors to match what's in `frontend/index.html` + `frontend/app.js`. Grep first:

```bash
grep -nE 'plan-card|plan-status' /home/ahmed/signage/frontend/index.html /home/ahmed/signage/frontend/app.js | head -30
```

- [ ] **Step 2: Replace `.list` and list-item base**

```css
.list {
  display: grid;
  gap: 10px;
}

.list > * {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-md);
  padding: 14px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  transition: border-color var(--t-fast), transform var(--t-fast);
}

.list > *:hover {
  border-color: var(--peach-deep);
  transform: translateY(-1px);
}

.list .list-title {
  font-weight: 600;
  color: var(--plum);
  font-family: var(--font);
}

.list .list-meta {
  font-size: 12px;
  color: var(--sand);
  font-family: var(--mono);
}

.list .list-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
```

If the existing markup uses different sub-classes (`.list-item`, `.list-row`, etc.), adjust — but keep the `background + border + padding + rounded + hover` pattern.

- [ ] **Step 3: Section titles**

Find the current section heading rule (usually `.panel > h2` or similar). Replace with:

```css
.panel > h2,
section.panel > h2 {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 700;
  color: var(--plum);
  margin: 0 0 18px;
  display: flex;
  align-items: center;
  gap: 10px;
}
```

- [ ] **Step 4: Rebuild & verify**

Log into the admin. Visit the dashboard. Expect:
- Plan card with butter background, mint "Trial" pill
- Each section (Sites, Screens, Media, Playlists) in a cream panel with cream-border and a soft shadow
- List items are cream cards that lift on hover with a peach-deep border
- No cyan anywhere; no dark glass

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(admin): pastel panels, plan card, list items"
```

---

## Task 6: Overlays — dropzone, modals, toasts, zones editor, preview

**Files:**
- Modify: `frontend/styles.css` — `.dropzone*`, `.modal*`, `.toast*`, `.zones-*`, `.preview-panel*`

These components were designed for a dark theme (glassmorphic blur, cyan drop-target rings). They need the most attention.

- [ ] **Step 1: Dropzone**

Find `.dropzone`, `.dropzone-content`, `.dropzone-title`, `.dropzone-subtitle`, `.dropzone.is-drag-over` (or equivalent dragover state). Replace with:

```css
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 36px 24px;
  background: var(--butter);
  border: 2px dashed var(--peach-deep);
  border-radius: var(--r-lg);
  cursor: pointer;
  transition: background var(--t-fast), border-color var(--t-fast);
  text-align: center;
}

.dropzone:hover,
.dropzone.is-drag-over {
  background: var(--peach);
  border-color: var(--plum);
}

.dropzone-content {
  display: grid;
  gap: 4px;
}

.dropzone-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--plum);
}

.dropzone-subtitle {
  color: var(--cocoa);
  font-size: 13px;
}

.media-url-form {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
```

- [ ] **Step 2: Modals** (if they exist — grep to confirm)

```bash
grep -nE '\.modal|modal-overlay|modal-content' /home/ahmed/signage/frontend/styles.css | head -10
```

If `.modal*` rules exist, replace with:

```css
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(62, 43, 79, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 20px;
}

.modal-content {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-lg);
  padding: 28px;
  max-width: 520px;
  width: 100%;
  box-shadow: 0 20px 50px rgba(62, 43, 79, 0.25);
}

.modal-title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--plum);
  margin: 0 0 16px;
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 20px;
}
```

If no modals exist in the current CSS, skip this step.

- [ ] **Step 3: Toasts**

Find `.toast*` rules. Replace with:

```css
.toast-container {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 200;
  display: grid;
  gap: 10px;
  max-width: 360px;
}

.toast {
  background: var(--cream);
  border: 1.5px solid var(--plum);
  border-radius: var(--r-md);
  padding: 12px 16px;
  color: var(--plum);
  font-size: 14px;
  box-shadow: 0 10px 24px var(--plum-shadow);
  animation: toast-in 200ms cubic-bezier(0.2, 0.7, 0.2, 1);
}

.toast-success { border-left: 6px solid var(--mint); }
.toast-error   { border-left: 6px solid var(--red); background: var(--cream); }
.toast-warn    { border-left: 6px solid var(--peach-deep); }
.toast-info    { border-left: 6px solid var(--lavender); }

@keyframes toast-in {
  from { opacity: 0; transform: translateY(-8px); }
  to   { opacity: 1; transform: translateY(0); }
}
```

- [ ] **Step 4: Zones editor**

Find all `.zones-*` and `.zone-*` rules. Replace with:

```css
.zones-panel {
  padding: 24px;
}

.zones-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.zones-actions,
.zones-templates,
.zones-footer {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.zones-footer {
  margin-top: 16px;
  justify-content: flex-end;
}

.zones-canvas {
  position: relative;
  background: var(--butter);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-md);
  aspect-ratio: 16 / 9;
  overflow: hidden;
  margin: 12px 0;
}

.zone-region {
  position: absolute;
  background: var(--cream);
  border: 1.5px solid var(--peach-deep);
  border-radius: 8px;
  cursor: move;
  transition: border-color var(--t-fast);
}

.zone-region:hover,
.zone-region.selected {
  border-color: var(--plum);
  box-shadow: 0 0 0 3px var(--peach);
}

.zone-title {
  font-weight: 600;
  font-family: var(--mono);
  color: var(--peach-deep);
  font-size: 12px;
  padding: 4px 8px;
}
```

- [ ] **Step 5: Preview panel**

```css
.preview-panel {
  padding: 20px;
}

.preview-frame {
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-md);
  background: var(--butter);
  overflow: hidden;
}
```

Adjust if the existing markup uses different class names.

- [ ] **Step 6: Rebuild and verify**

Exercise each overlay in the admin:
- Media section: drop a file on the dropzone — it should turn peach with a plum border on hover/drag.
- Trigger a toast (e.g. save a change) — should be cream with a coloured left stripe.
- Open the zones editor — canvas has a butter background, zone regions have peach-deep borders.

- [ ] **Step 7: Commit**

```bash
git -C /home/ahmed/signage add frontend/styles.css
git -C /home/ahmed/signage commit -m "feat(admin): pastel overlays (dropzone, toasts, zones, preview)"
```

---

## Task 7: Responsive + accessibility + smoke test

**Files:** no edits unless a regression surfaces.

- [ ] **Step 1: Run the regression suite**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `7 passed`.

- [ ] **Step 2: Visual smoke — desktop**

At 1440px browser width, log in and walk through:
1. Sign in form renders on cream, peach submit works, error toast (wrong password) appears with rose stripe
2. Dashboard plan card shows trial days remaining in mint pill
3. Sites panel → add a site → list item appears with peach-deep hover border
4. Screens panel → add a screen → resolution dropdown renders with cream background, peach focus ring
5. Media panel → drag a test image onto the dropzone → peach background kicks in; upload succeeds; list item appears
6. Playlists panel → create a playlist, add items, save
7. Zones editor → open for a screen → canvas renders with butter background; drag a zone region; save

If any component still looks dark/cyan/glowing, open devtools, find the offending selector, and add a one-line fix to `frontend/styles.css`. Commit each fix individually with `fix(admin): <what>`.

- [ ] **Step 3: Visual smoke — mobile**

Shrink browser to 400px. Verify:
- Hamburger toggle shows (nothing actually collapses into a menu today — that's fine; Task 2 added the hamburger button for future bilingual/nav work, but its click handler is a no-op in admin)
- All panels are readable, no horizontal scroll
- Lists stack vertically, actions wrap cleanly
- Plan card pills don't overflow

- [ ] **Step 4: Cross-check the landing still works**

```bash
curl -sSf http://localhost:3003/ | head -5
curl -sSf http://localhost:3003/styles.css | head -3
```

Expected: landing untouched (this plan changed zero files in `landing/`).

- [ ] **Step 5: Merge to main (only after full smoke passes)**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/admin-retheme -m "Merge admin retheme: pastel retro-modern across frontend/"
git -C /home/ahmed/signage log --oneline -5
```

- [ ] **Step 6: Update memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md` to mark admin retheme as DONE and keep player retheme + pairing flow as the next item.

- [ ] **Step 7: Final report**

Print a one-paragraph summary: commits shipped, files touched, any remaining visual nits that should be picked up in the player retheme plan.

---

## Resume Notes (read this if you hit a context limit mid-plan)

Each task is self-contained and commits independently. To resume:

1. `git -C /home/ahmed/signage log --oneline feature/admin-retheme` shows which tasks are done (commit messages `feat(admin): ...`).
2. The plan file uses `- [ ]` checkbox syntax — check off steps as they complete so the next session can see exact progress.
3. The palette mapping table above is authoritative — do not re-derive values.
4. If a component still looks dark after its task is done, the fix is almost always "add a missing var override in the component's CSS rule" — not a structural rewrite.

## Out of Scope (explicit)

- Player retheme + QR pairing-flow redesign — separate plan, follow-up. Requires backend endpoints (`POST /screens/request_code`, `GET /screens/poll/{code}`, `POST /screens/claim`), admin deep-link `/pair?code=...`, and player full-bleed QR UI.
- Stripe billing UI hooks (Phase 2).
- Bilingual EN/AR (Phase 2) — language toggle in header will reuse the `.nav-toggle` button slot.
- Icon/illustration additions — admin currently has no SVG illustrations; the retheme keeps it that way to preserve density.
