# Sawwii Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single-page marketing site for Sawwii with a pastel retro-modern aesthetic, SVG illustrations, and CTAs that route visitors into the existing admin signup flow.

**Architecture:** New `landing/` directory served by its own nginx container on port `3002`. Static HTML/CSS/JS; no framework. SVG illustrations are inline or external assets — no bitmap images, no external image requests. The hosting model mirrors the existing `frontend/` and `player/` services (alpine nginx image, same entrypoint pattern that bakes a runtime `config.js` with the admin URL). Re-theming the admin SPA is explicitly **out of scope** — that lands in a follow-up plan.

**Tech Stack:** HTML5, vanilla JS (ES2022 for mobile-nav toggle + smooth scroll only), plain CSS with CSS custom properties, IBM Plex Serif + IBM Plex Sans (Google Fonts), inline + external SVG illustrations, nginx:1.27-alpine container, Docker Compose.

---

## Prerequisites

Work from `/home/ahmed/signage` on a new branch `feature/landing-page` (branch off from the current `feature/phase1-lockdown` HEAD so this stays independent of Stripe work).

```bash
git -C /home/ahmed/signage checkout -b feature/landing-page feature/phase1-lockdown
```

Docker stack must be up for smoke tests.

## Brand & Palette Decisions (baked into this plan — do not redesign)

**Name, voice:** Sawwii — سوّي — Arabic imperative "make it." The voice is warm and confident. English-first for V1; HTML carries `lang="en"` and `dir="ltr"`, but copy avoids idioms so a translation pass is cheap later.

**Typography:** IBM Plex Serif (display, weights 500/700) for headings; IBM Plex Sans (body, weights 400/500/600) for everything else. Same superfamily as the future IBM Plex Sans Arabic add-on, so a bilingual flip later stays consistent.

**Palette (light pastel retro-modern):** the custom-property values below are authoritative. Every colour in the CSS refers to a var; no hardcoded colours outside of `:root`.

| Var | Hex | Use |
|---|---|---|
| `--cream`          | `#FFF8F0` | Page background |
| `--butter`         | `#FDF3D6` | Section alt background, card background |
| `--peach`          | `#F4B9A1` | Primary CTA, hero accents |
| `--peach-deep`     | `#E09478` | Primary CTA hover, stronger peach accent |
| `--mint`           | `#B5DABD` | Secondary accent, "active" badges |
| `--lavender`       | `#C9B8E0` | Tertiary accent, illustration accents |
| `--rose`           | `#E8B4C6` | Quaternary accent, highlight underlines |
| `--plum`           | `#3E2B4F` | Primary text, heading text |
| `--cocoa`          | `#5D4A66` | Secondary text |
| `--sand`           | `#A89382` | Tertiary/muted text |
| `--cream-border`   | `#E8DCC6` | Soft borders, dividers |
| `--plum-shadow`    | `rgba(62, 43, 79, 0.12)` | Soft shadows |
| `--grain`          | `rgba(62, 43, 79, 0.035)` | Paper-grain overlay opacity |

**Shape language:** generous rounded corners (`--r-card: 20px`, `--r-btn: 14px`, `--r-pill: 999px`), 1.5-2px borders instead of heavy shadows, a subtle paper-grain overlay on the whole page, and retro-modern geometric SVG illustrations (circles, rounded rectangles, soft waves — no line art, no gradients inside shapes).

**Layout grid:** max content width `1120px`, section vertical padding `96px` desktop / `56px` mobile, 12-col CSS grid only where necessary (most sections use flex).

## File Structure

```
landing/
├── Dockerfile                     # nginx:1.27-alpine, mirrors frontend/Dockerfile
├── docker-entrypoint.sh           # Writes config.js with APP_URL at boot
├── nginx.conf                     # gzip + asset caching, mirrors frontend/nginx.conf
├── index.html                     # Single-page marketing site
├── styles.css                     # Pastel palette + section styles
├── app.js                         # Mobile nav toggle + smooth scroll
├── config.js                      # GENERATED at container boot — don't hand-edit
└── assets/
    ├── favicon.svg
    └── illustrations/
        ├── hero.svg
        ├── feature-screens.svg
        ├── feature-playlists.svg
        ├── feature-ai.svg
        ├── feature-pay.svg
        └── how-it-works.svg
```

One file per concern. `index.html` is the only HTML file — the page is a single scroll. `app.js` is tiny (<60 lines) and handles two interactions: the mobile nav toggle and smooth-scrolling anchor clicks. Illustrations are external SVG files in `assets/illustrations/` so they can be swapped out without HTML edits.

Modifies one existing file: `docker-compose.yml` — adds a `landing` service.

---

## Task 1: Scaffold `landing/` directory + Docker service plumbing

**Files:**
- Create: `landing/Dockerfile`
- Create: `landing/docker-entrypoint.sh`
- Create: `landing/nginx.conf`
- Create: `landing/index.html` (minimal placeholder for this task)
- Create: `landing/styles.css` (empty stub for this task)
- Create: `landing/app.js` (empty stub for this task)
- Modify: `docker-compose.yml` — add `landing` service

- [ ] **Step 1: Create `landing/Dockerfile`**

```dockerfile
FROM nginx:1.27-alpine

COPY . /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
```

- [ ] **Step 2: Create `landing/docker-entrypoint.sh`**

```sh
#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.APP_URL = "${APP_URL:-http://192.168.18.192:3000}";
EOF

exec nginx -g 'daemon off;'
```

`APP_URL` points at the admin SPA. Defaults to the Montreal VPS IP for local development; in production (once `sawwii.com` DNS is live) the env var is set to `https://app.sawwii.com` via `.env`.

- [ ] **Step 3: Create `landing/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;

    root  /usr/share/nginx/html;
    index index.html;

    gzip            on;
    gzip_types      text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;
    gzip_vary       on;

    location ~* \.(html|js)$ {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
    }

    location ~* \.(css|png|jpg|jpeg|gif|svg|ico|webp|woff|woff2)$ {
        expires 7d;
        add_header Cache-Control "public, max-age=604800, stale-while-revalidate=86400";
    }

    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
    }
}
```

- [ ] **Step 4: Create placeholder `landing/index.html`**

```html
<!DOCTYPE html>
<html lang="en" dir="ltr">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sawwii — Digital signage that just works</title>
    <link rel="stylesheet" href="styles.css" />
    <script src="config.js"></script>
  </head>
  <body>
    <h1>Sawwii landing — scaffold placeholder</h1>
    <p>If you can read this, Task 1 plumbing is live.</p>
    <script src="app.js"></script>
  </body>
</html>
```

- [ ] **Step 5: Create empty stubs**

Create `landing/styles.css` containing just a comment:

```css
/* Placeholder — populated in Task 2. */
```

Create `landing/app.js` containing just a comment:

```javascript
/* Placeholder — populated in Task 8. */
```

- [ ] **Step 6: Add `landing` service to `docker-compose.yml`**

Append after the existing `player:` service (after line 66):

```yaml

  landing:
    build: ./landing
    ports:
      - "3002:80"
    env_file:
      - .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Optional (but recommended): also append this line to `.env` so the default is explicit rather than relying on the entrypoint fallback. Check the file exists first with `ls -la .env`:

```
APP_URL=http://192.168.18.192:3000
```

- [ ] **Step 7: Build and launch the new service**

```bash
docker-compose build landing && docker-compose up -d landing
```

- [ ] **Step 8: Verify the placeholder serves**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.18.192:3002/
curl -s http://192.168.18.192:3002/ | grep -E 'Sawwii landing|scaffold placeholder'
curl -s http://192.168.18.192:3002/config.js
```

Expected:
- HTTP 200
- The grep matches both strings
- `config.js` contains a line like `window.APP_URL = "http://192.168.18.192:3000";`

- [ ] **Step 9: Commit**

```bash
git add landing/ docker-compose.yml .env
git commit -m "feat(landing): scaffold marketing site container on :3002"
```

(If `.env` was not modified because `APP_URL` was already present or the file is gitignored, drop it from the `git add` and commit only the rest.)

---

## Task 2: Palette, typography, and global base styles

**Files:**
- Modify: `landing/styles.css` (replace the placeholder comment with the full foundation block below)
- Modify: `landing/index.html` — add a Google Fonts `<link>` in `<head>`

- [ ] **Step 1: Add Google Fonts to `landing/index.html`**

Inside `<head>`, immediately after the existing `<link rel="stylesheet" href="styles.css" />` line, insert:

```html
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:ital,wght@0,500;0,700;1,500&display=swap" />
```

Italic serif (`1,500`) is included because the hero headline uses an italic word for retro-modern flair (Task 3 will use it).

- [ ] **Step 2: Replace `landing/styles.css` with the foundation**

Overwrite the entire file with:

```css
/* ─────────────────────────────────────────────────────────────
   Sawwii landing — pastel retro-modern foundation
   ───────────────────────────────────────────────────────────── */

:root {
  /* ── Palette ─────────────────────────────────────────────── */
  --cream:         #FFF8F0;
  --butter:        #FDF3D6;
  --peach:         #F4B9A1;
  --peach-deep:    #E09478;
  --mint:          #B5DABD;
  --lavender:      #C9B8E0;
  --rose:          #E8B4C6;

  --plum:          #3E2B4F;
  --cocoa:         #5D4A66;
  --sand:          #A89382;

  --cream-border:  #E8DCC6;
  --plum-shadow:   rgba(62, 43, 79, 0.12);
  --plum-shadow-lg:rgba(62, 43, 79, 0.18);
  --grain:         rgba(62, 43, 79, 0.035);

  /* ── Shape language ──────────────────────────────────────── */
  --r-card: 20px;
  --r-btn:  14px;
  --r-pill: 999px;

  --shadow-card: 0 2px 0 var(--cream-border), 0 10px 30px var(--plum-shadow);
  --shadow-cta:  0 2px 0 var(--peach-deep),  0 8px 20px var(--plum-shadow);

  --t-fast:   140ms ease;
  --t-normal: 260ms cubic-bezier(0.2, 0.7, 0.2, 1);

  /* ── Typography ──────────────────────────────────────────── */
  --font-display: 'IBM Plex Serif', Georgia, serif;
  --font-body:    'IBM Plex Sans', system-ui, -apple-system, sans-serif;

  /* ── Layout ──────────────────────────────────────────────── */
  --maxw: 1120px;
  --gutter: 24px;
}

/* ── Reset ──────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { scroll-behavior: smooth; }

body {
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.6;
  color: var(--plum);
  background: var(--cream);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* Soft paper-grain overlay on the whole page */
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

/* ── Typography ──────────────────────────────────────────────── */
h1, h2, h3, h4 {
  font-family: var(--font-display);
  color: var(--plum);
  letter-spacing: -0.01em;
  line-height: 1.15;
}

h1 { font-size: clamp(36px, 6vw, 60px); font-weight: 700; }
h2 { font-size: clamp(28px, 4vw, 40px); font-weight: 700; }
h3 { font-size: clamp(20px, 2.4vw, 24px); font-weight: 500; }

p  { color: var(--cocoa); }
a  { color: var(--plum); text-decoration: none; transition: color var(--t-fast); }
a:hover { color: var(--peach-deep); }

/* ── Layout helpers ──────────────────────────────────────────── */
.container {
  max-width: var(--maxw);
  margin: 0 auto;
  padding: 0 var(--gutter);
}

.section {
  padding: 96px 0;
  position: relative;
}

.section--butter { background: var(--butter); }

@media (max-width: 720px) {
  .section { padding: 56px 0; }
}

/* ── Buttons ─────────────────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 14px 22px;
  border-radius: var(--r-btn);
  font-family: var(--font-body);
  font-size: 15px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  border: 1.5px solid transparent;
  transition: transform var(--t-fast), box-shadow var(--t-fast), background var(--t-fast), color var(--t-fast);
  white-space: nowrap;
}

.btn-primary {
  background: var(--peach);
  color: var(--plum);
  border-color: var(--peach-deep);
  box-shadow: var(--shadow-cta);
}

.btn-primary:hover {
  background: var(--peach-deep);
  color: var(--cream);
  transform: translateY(-1px);
  box-shadow: 0 3px 0 var(--peach-deep), 0 10px 24px var(--plum-shadow-lg);
}

.btn-ghost {
  background: transparent;
  color: var(--plum);
  border-color: var(--plum);
}

.btn-ghost:hover {
  background: var(--plum);
  color: var(--cream);
}

.btn-lg {
  padding: 18px 28px;
  font-size: 16px;
}

/* ── Pill / tag ──────────────────────────────────────────────── */
.pill {
  display: inline-block;
  padding: 6px 14px;
  border-radius: var(--r-pill);
  background: var(--mint);
  color: var(--plum);
  font-size: 13px;
  font-weight: 600;
  border: 1.5px solid var(--plum);
}

/* ── Card base ───────────────────────────────────────────────── */
.card {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-card);
  padding: 32px;
  box-shadow: var(--shadow-card);
}

/* ── Utility ─────────────────────────────────────────────────── */
.hidden { display: none !important; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
```

- [ ] **Step 3: Update the placeholder `<body>` to visually verify typography + palette**

Replace the `<body>` contents of `landing/index.html` with:

```html
  <body>
    <div class="container" style="padding-top: 80px; padding-bottom: 80px;">
      <span class="pill">Foundation ready</span>
      <h1 style="margin-top: 16px;">Sawwii — <em>make it</em> today</h1>
      <p style="max-width: 520px; margin-top: 16px;">Digital signage for cafés, clinics, and kiosks across the Gulf. Lovingly built, priced fairly, bilingual by design.</p>
      <div style="margin-top: 32px; display: flex; gap: 12px; flex-wrap: wrap;">
        <a class="btn btn-primary btn-lg" href="#">Start free trial</a>
        <a class="btn btn-ghost btn-lg" href="#">See it live</a>
      </div>
    </div>
    <script src="app.js"></script>
  </body>
```

This is a throwaway smoke — Task 3 replaces the body entirely with the real structure. It exists only to verify fonts load, colours render, the grain overlay shows up, and buttons style correctly.

- [ ] **Step 4: Rebuild and visually verify**

```bash
docker-compose build landing && docker-compose up -d landing
```

Open http://192.168.18.192:3002/ in a browser. Expected:
- Cream background (`#FFF8F0`)
- "Sawwii — *make it* today" headline in IBM Plex Serif, with "make it" in italic
- Body copy in IBM Plex Sans
- A peach primary button and an outlined plum ghost button
- A green "Foundation ready" pill above the headline
- A faint dotted texture across the whole page

If IBM Plex is not rendering (falls back to Georgia/system-ui), check the Google Fonts `<link>` URL is intact.

- [ ] **Step 5: Commit**

```bash
git add landing/index.html landing/styles.css
git commit -m "feat(landing): pastel palette + IBM Plex typography foundation"
```

---

## Task 3: Navigation bar + hero section

**Files:**
- Modify: `landing/index.html` — replace the `<body>` contents with the real nav + hero markup
- Modify: `landing/styles.css` — append nav + hero styles
- Create: `landing/assets/illustrations/hero.svg`

- [ ] **Step 1: Create the hero illustration**

Write `landing/assets/illustrations/hero.svg` with:

```svg
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 440" role="img" aria-label="Retro display with peach and mint shapes">
  <!-- Butter back-card (tilted) -->
  <rect x="60" y="60" width="400" height="300" rx="24" fill="#FDF3D6" stroke="#3E2B4F" stroke-width="3" transform="rotate(-3 260 210)"/>

  <!-- Main display -->
  <rect x="80" y="80" width="360" height="260" rx="20" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="3"/>

  <!-- Top status bar -->
  <rect x="96" y="96" width="328" height="20" rx="10" fill="#C9B8E0"/>

  <!-- Big hero tile -->
  <rect x="96" y="128" width="200" height="150" rx="14" fill="#F4B9A1"/>
  <circle cx="196" cy="180" r="26" fill="#FFF8F0"/>
  <rect x="120" y="220" width="110" height="12" rx="6" fill="#3E2B4F"/>
  <rect x="120" y="240" width="80"  height="10" rx="5" fill="#3E2B4F" opacity="0.6"/>

  <!-- Side tile 1 -->
  <rect x="308" y="128" width="116" height="70" rx="12" fill="#B5DABD"/>
  <circle cx="340" cy="163" r="14" fill="#3E2B4F"/>
  <rect x="362" y="156" width="50" height="8" rx="4" fill="#3E2B4F"/>
  <rect x="362" y="170" width="38" height="6" rx="3" fill="#3E2B4F" opacity="0.6"/>

  <!-- Side tile 2 -->
  <rect x="308" y="208" width="116" height="70" rx="12" fill="#E8B4C6"/>
  <path d="M328 243 L348 223 L368 243 L388 228" stroke="#3E2B4F" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>

  <!-- Bottom ticker -->
  <rect x="96" y="290" width="328" height="36" rx="10" fill="#FDF3D6" stroke="#3E2B4F" stroke-width="2"/>
  <circle cx="114" cy="308" r="6" fill="#F4B9A1"/>
  <rect x="130" y="302" width="200" height="12" rx="6" fill="#3E2B4F" opacity="0.8"/>

  <!-- Floating accents -->
  <circle cx="40" cy="110" r="18" fill="#B5DABD" stroke="#3E2B4F" stroke-width="2"/>
  <circle cx="480" cy="380" r="26" fill="#C9B8E0" stroke="#3E2B4F" stroke-width="2"/>
  <path d="M470 60 L490 40 L490 80 Z" fill="#E8B4C6" stroke="#3E2B4F" stroke-width="2" stroke-linejoin="round"/>
</svg>
```

- [ ] **Step 2: Replace the `<body>` in `landing/index.html`**

Overwrite the entire `<body>...</body>` block with:

```html
  <body>
    <!-- ── Nav ──────────────────────────────────────────────── -->
    <header class="nav" id="nav">
      <div class="container nav-inner">
        <a href="#" class="nav-logo">
          <span class="nav-mark">◠</span>
          <span>Sawwii</span>
        </a>

        <button class="nav-toggle" id="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
          <span></span><span></span><span></span>
        </button>

        <nav class="nav-links" id="nav-links">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#pricing">Pricing</a>
          <a href="#faq">FAQ</a>
          <a class="btn btn-ghost btn-sm" id="cta-signin"  href="#">Sign in</a>
          <a class="btn btn-primary btn-sm" id="cta-signup" href="#">Start free trial</a>
        </nav>
      </div>
    </header>

    <!-- ── Hero ─────────────────────────────────────────────── -->
    <section class="hero">
      <div class="container hero-grid">
        <div class="hero-copy">
          <span class="pill">14-day free trial · no card</span>
          <h1 class="hero-title">Digital signage that <em>just works</em>.</h1>
          <p class="hero-sub">
            Sawwii turns any screen in your shop, clinic, or kiosk into a living menu board — priced fairly, bilingual by design, and ready for the Gulf market from day one.
          </p>
          <div class="hero-ctas">
            <a class="btn btn-primary btn-lg" id="cta-signup-hero" href="#">Start free trial</a>
            <a class="btn btn-ghost btn-lg"   href="#how">How it works</a>
          </div>
          <div class="hero-proof">
            <span class="hero-proof-dot"></span>
            <span>Starter plan from <strong>$9.99/month</strong> — up to 3 screens.</span>
          </div>
        </div>

        <div class="hero-art">
          <img src="assets/illustrations/hero.svg" alt="" width="520" height="440" />
        </div>
      </div>
    </section>

    <script src="app.js"></script>
  </body>
```

The two `#cta-signup*` and `#cta-signin` anchor hrefs are left as `#` on purpose — Task 8 wires them to `APP_URL` via JS at boot.

- [ ] **Step 3: Append nav + hero CSS to `landing/styles.css`**

Append (do not replace) at the end of the file:

```css
/* ─────────────────────────────────────────────────────────────
   Navigation
   ───────────────────────────────────────────────────────────── */
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(255, 248, 240, 0.85);
  backdrop-filter: saturate(140%) blur(12px);
  border-bottom: 1.5px solid var(--cream-border);
}

.nav-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  padding-bottom: 16px;
  gap: 24px;
}

.nav-logo {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--plum);
}

.nav-mark {
  display: inline-grid;
  place-items: center;
  width: 32px; height: 32px;
  background: var(--peach);
  border: 1.5px solid var(--plum);
  border-radius: 10px;
  font-size: 18px;
  transform: translateY(-2px);
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 22px;
}

.nav-links a:not(.btn) {
  font-size: 14px;
  font-weight: 500;
  color: var(--cocoa);
}

.nav-links a:not(.btn):hover { color: var(--plum); }

.btn-sm { padding: 8px 14px; font-size: 13px; }

.nav-toggle {
  display: none;
  background: transparent;
  border: 0;
  width: 36px; height: 36px;
  cursor: pointer;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
}

.nav-toggle span {
  display: block;
  width: 22px;
  height: 2px;
  background: var(--plum);
  border-radius: 2px;
  transition: transform var(--t-fast), opacity var(--t-fast);
}

@media (max-width: 880px) {
  .nav-toggle { display: flex; }
  .nav-links {
    position: absolute;
    top: 100%; left: 0; right: 0;
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
    padding: 18px var(--gutter) 24px;
    background: var(--cream);
    border-bottom: 1.5px solid var(--cream-border);
    transform: translateY(-10px);
    opacity: 0;
    pointer-events: none;
    transition: transform var(--t-normal), opacity var(--t-normal);
  }
  .nav-links.is-open {
    transform: translateY(0);
    opacity: 1;
    pointer-events: auto;
  }
  .nav-links a:not(.btn) { padding: 8px 0; }
  .nav-links .btn { justify-content: center; }
}

/* ─────────────────────────────────────────────────────────────
   Hero
   ───────────────────────────────────────────────────────────── */
.hero {
  padding: 80px 0 96px;
  overflow: hidden;
  position: relative;
}

.hero-grid {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  align-items: center;
  gap: 48px;
}

.hero-title {
  margin-top: 18px;
}

.hero-title em {
  font-style: italic;
  color: var(--peach-deep);
  position: relative;
  white-space: nowrap;
}

.hero-title em::after {
  content: '';
  position: absolute;
  left: 0; right: 0; bottom: -6px;
  height: 8px;
  background: var(--rose);
  border-radius: 999px;
  opacity: 0.55;
  z-index: -1;
}

.hero-sub {
  margin-top: 20px;
  max-width: 520px;
  font-size: 18px;
  line-height: 1.65;
}

.hero-ctas {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 32px;
}

.hero-proof {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-top: 28px;
  color: var(--sand);
  font-size: 14px;
}

.hero-proof strong { color: var(--plum); font-weight: 600; }

.hero-proof-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--mint);
  border: 1.5px solid var(--plum);
  display: inline-block;
}

.hero-art img {
  width: 100%;
  max-width: 520px;
  height: auto;
  display: block;
  margin-left: auto;
  filter: drop-shadow(0 20px 30px var(--plum-shadow));
}

@media (max-width: 880px) {
  .hero { padding: 48px 0 64px; }
  .hero-grid { grid-template-columns: 1fr; gap: 32px; }
  .hero-art img { margin: 0 auto; }
  .hero-sub { font-size: 16px; }
}
```

- [ ] **Step 4: Rebuild and visually verify**

```bash
docker-compose build landing && docker-compose up -d landing
```

In a browser at http://192.168.18.192:3002/. Expected:
- A sticky translucent nav bar at the top with logo (`◠ Sawwii`), four anchor links, a ghost "Sign in" button, and a peach "Start free trial" button.
- Below, a two-column hero: left side has the "14-day free trial · no card" mint pill, a large serif headline with "just works" italic + a rose underline, a sub-paragraph, two CTA buttons, and a tiny price line. Right side shows the hero SVG illustration (cream display with a peach hero tile, mint + rose side tiles, purple status bar, accents floating around).
- At < 880px viewport, the nav collapses to a hamburger (non-functional yet — Task 8 wires it), and the hero stacks into a single column.

- [ ] **Step 5: Commit**

```bash
git add landing/index.html landing/styles.css landing/assets/illustrations/hero.svg
git commit -m "feat(landing): nav bar + hero section with SVG illustration"
```

---

## Task 4: Features section with four cards and inline SVG icons

**Files:**
- Modify: `landing/index.html` — append the features section after the `<section class="hero">`
- Modify: `landing/styles.css` — append features styles

- [ ] **Step 1: Append the features markup**

Insert this block in `landing/index.html` immediately after the closing `</section>` of the hero (and before `<script src="app.js">`):

```html
    <!-- ── Features ─────────────────────────────────────────── -->
    <section class="section section--butter" id="features">
      <div class="container">
        <div class="section-head">
          <span class="pill" style="background: var(--rose);">Why Sawwii</span>
          <h2 class="section-title">Built for shop owners, not stadiums.</h2>
          <p class="section-sub">A fair price, a fast setup, and the tiny touches that matter when you're running a café in Salmiya or a clinic in Hawalli.</p>
        </div>

        <div class="features-grid">
          <article class="feature-card">
            <div class="feature-icon feature-icon--peach">
              <svg viewBox="0 0 48 48" aria-hidden="true"><rect x="6" y="10" width="36" height="24" rx="4" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="2.5"/><rect x="18" y="36" width="12" height="4" rx="1" fill="#3E2B4F"/><circle cx="24" cy="22" r="5" fill="#F4B9A1" stroke="#3E2B4F" stroke-width="2"/></svg>
            </div>
            <h3>Any screen, in ten minutes</h3>
            <p>Plug an old TV or tablet in, scan the pairing code, done. No hardware to buy, no installer to wait for.</p>
          </article>

          <article class="feature-card">
            <div class="feature-icon feature-icon--mint">
              <svg viewBox="0 0 48 48" aria-hidden="true"><rect x="8"  y="8"  width="14" height="14" rx="3" fill="#B5DABD" stroke="#3E2B4F" stroke-width="2"/><rect x="26" y="8"  width="14" height="14" rx="3" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="2"/><rect x="8"  y="26" width="14" height="14" rx="3" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="2"/><rect x="26" y="26" width="14" height="14" rx="3" fill="#E8B4C6" stroke="#3E2B4F" stroke-width="2"/></svg>
            </div>
            <h3>Zones, playlists, schedules</h3>
            <p>Split a screen into tidy zones. Run a playlist in each. Schedule your weekend specials once and forget them.</p>
          </article>

          <article class="feature-card feature-card--highlight">
            <span class="pill" style="background: var(--lavender); position: absolute; top: 20px; right: 20px;">Coming soon</span>
            <div class="feature-icon feature-icon--lavender">
              <svg viewBox="0 0 48 48" aria-hidden="true"><path d="M14 34 L24 10 L34 34 Z" fill="#C9B8E0" stroke="#3E2B4F" stroke-width="2.5" stroke-linejoin="round"/><circle cx="36" cy="14" r="4" fill="#E8B4C6" stroke="#3E2B4F" stroke-width="2"/><circle cx="12" cy="18" r="3" fill="#F4B9A1" stroke="#3E2B4F" stroke-width="2"/></svg>
            </div>
            <h3>AI menu, from your website</h3>
            <p>Paste a URL. Sawwii pulls your menu, your colours, your logo — and drafts a polished playlist you can edit in minutes.</p>
          </article>

          <article class="feature-card">
            <div class="feature-icon feature-icon--rose">
              <svg viewBox="0 0 48 48" aria-hidden="true"><rect x="6" y="14" width="36" height="22" rx="4" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="2.5"/><rect x="6" y="18" width="36" height="6" fill="#E8B4C6" stroke="#3E2B4F" stroke-width="2.5"/><rect x="12" y="28" width="10" height="4" rx="1" fill="#3E2B4F"/></svg>
            </div>
            <h3>Pay your way</h3>
            <p>Card, Apple Pay, Google Pay on day one. KNET for Kuwait is on its way — because local trust is not optional.</p>
          </article>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Append features CSS**

```css
/* ─────────────────────────────────────────────────────────────
   Section head (shared)
   ───────────────────────────────────────────────────────────── */
.section-head {
  text-align: center;
  max-width: 640px;
  margin: 0 auto 56px;
}

.section-title { margin-top: 16px; }

.section-sub {
  margin-top: 18px;
  font-size: 17px;
}

/* ─────────────────────────────────────────────────────────────
   Features grid
   ───────────────────────────────────────────────────────────── */
.features-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}

.feature-card {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-card);
  padding: 28px;
  position: relative;
  transition: transform var(--t-normal), box-shadow var(--t-normal);
}

.feature-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-card);
}

.feature-card--highlight {
  background: var(--cream);
  border-color: var(--plum);
  box-shadow: var(--shadow-card);
}

.feature-icon {
  width: 56px; height: 56px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  margin-bottom: 18px;
  border: 1.5px solid var(--plum);
}

.feature-icon svg { width: 36px; height: 36px; }

.feature-icon--peach    { background: var(--peach); }
.feature-icon--mint     { background: var(--mint); }
.feature-icon--lavender { background: var(--lavender); }
.feature-icon--rose     { background: var(--rose); }

.feature-card h3 { margin-bottom: 10px; }

.feature-card p { font-size: 15px; }

@media (max-width: 980px) { .features-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .features-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 3: Rebuild and visually verify**

```bash
docker-compose build landing && docker-compose up -d landing
```

Expected at http://192.168.18.192:3002/:
- Below the hero, a butter-coloured section with a rose pill, a large "Built for shop owners, not stadiums." headline, and a sub-paragraph.
- Four feature cards in a row: peach/mint/lavender/rose icon tiles, each with a short title and a two-line blurb. The third card ("AI menu, from your website") has a `Coming soon` lavender pill in its top-right corner and a darker plum border.
- At ≤ 980px, cards reflow to 2×2; at ≤ 560px, to a single column.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html landing/styles.css
git commit -m "feat(landing): features section with four inline-SVG feature cards"
```

---

## Task 5: "How it works" — three-step walkthrough with illustration

**Files:**
- Modify: `landing/index.html` — append the "How it works" section after the features section
- Modify: `landing/styles.css` — append how-it-works styles
- Create: `landing/assets/illustrations/how-it-works.svg`

- [ ] **Step 1: Create the how-it-works illustration**

Write `landing/assets/illustrations/how-it-works.svg`:

```svg
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 360" role="img" aria-label="URL flowing into a TV screen">
  <!-- Laptop -->
  <rect x="40" y="80" width="180" height="120" rx="12" fill="#FFF8F0" stroke="#3E2B4F" stroke-width="3"/>
  <rect x="50" y="92" width="160" height="92" rx="6" fill="#C9B8E0"/>
  <rect x="64" y="106" width="120" height="10" rx="3" fill="#FFF8F0"/>
  <rect x="64" y="124" width="80"  height="8"  rx="3" fill="#FFF8F0" opacity="0.7"/>
  <rect x="64" y="140" width="100" height="8"  rx="3" fill="#FFF8F0" opacity="0.7"/>
  <rect x="16" y="200" width="228" height="8" rx="4" fill="#3E2B4F"/>

  <!-- Arrow -->
  <path d="M250 140 C 290 140, 310 200, 340 200" stroke="#E09478" stroke-width="4" fill="none" stroke-linecap="round" stroke-dasharray="2 8"/>
  <path d="M336 193 L346 200 L336 207" stroke="#E09478" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>

  <!-- TV -->
  <rect x="310" y="130" width="150" height="110" rx="10" fill="#FDF3D6" stroke="#3E2B4F" stroke-width="3"/>
  <rect x="324" y="144" width="122" height="82" rx="5" fill="#F4B9A1"/>
  <circle cx="385" cy="185" r="16" fill="#FFF8F0"/>
  <rect x="340" y="214" width="90" height="4" rx="2" fill="#3E2B4F"/>
  <rect x="360" y="240" width="50" height="8" rx="2" fill="#3E2B4F"/>
  <rect x="340" y="256" width="90" height="4" rx="2" fill="#3E2B4F"/>

  <!-- Accents -->
  <circle cx="100" cy="50"  r="16" fill="#B5DABD" stroke="#3E2B4F" stroke-width="2"/>
  <circle cx="430" cy="80"  r="12" fill="#E8B4C6" stroke="#3E2B4F" stroke-width="2"/>
  <path d="M40 320 L60 300 L80 320 L100 300" stroke="#3E2B4F" stroke-width="2" fill="none" stroke-linecap="round"/>
</svg>
```

- [ ] **Step 2: Append the how-it-works markup**

Add to `landing/index.html` after the features `</section>`:

```html
    <!-- ── How it works ─────────────────────────────────────── -->
    <section class="section" id="how">
      <div class="container how-grid">
        <div class="how-art">
          <img src="assets/illustrations/how-it-works.svg" alt="" width="480" height="360" />
        </div>

        <div class="how-copy">
          <span class="pill">How it works</span>
          <h2>Three steps to a screen that sells.</h2>

          <ol class="how-steps">
            <li>
              <span class="how-step-num">1</span>
              <div>
                <h3>Sign up in a minute</h3>
                <p>Business name, email, password. You land on a clean dashboard with a 14-day free trial already running.</p>
              </div>
            </li>
            <li>
              <span class="how-step-num">2</span>
              <div>
                <h3>Upload, arrange, schedule</h3>
                <p>Drop images and videos, group them into playlists, split screens into zones — a weekly menu or a happy-hour rotation is 15 minutes of work.</p>
              </div>
            </li>
            <li>
              <span class="how-step-num">3</span>
              <div>
                <h3>Pair any screen and you're live</h3>
                <p>Open the player URL on any TV or tablet, type the six-digit pairing code, and the right content appears instantly.</p>
              </div>
            </li>
          </ol>
        </div>
      </div>
    </section>
```

- [ ] **Step 3: Append how-it-works CSS**

```css
/* ─────────────────────────────────────────────────────────────
   How it works
   ───────────────────────────────────────────────────────────── */
.how-grid {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  align-items: center;
  gap: 56px;
}

.how-art img {
  width: 100%;
  max-width: 480px;
  height: auto;
  display: block;
  filter: drop-shadow(0 14px 24px var(--plum-shadow));
}

.how-copy h2 { margin-top: 16px; margin-bottom: 28px; }

.how-steps {
  list-style: none;
  display: grid;
  gap: 20px;
}

.how-steps li {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-card);
  padding: 20px;
}

.how-step-num {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: var(--r-pill);
  background: var(--peach);
  border: 1.5px solid var(--plum);
  color: var(--plum);
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  display: grid;
  place-items: center;
}

.how-steps h3 { margin-bottom: 4px; font-size: 18px; }

.how-steps p { font-size: 15px; }

@media (max-width: 880px) {
  .how-grid { grid-template-columns: 1fr; gap: 32px; }
  .how-art img { margin: 0 auto; }
}
```

- [ ] **Step 4: Rebuild and verify**

```bash
docker-compose build landing && docker-compose up -d landing
```

Expected: a new two-column section below Features. Left side is the laptop → TV illustration; right side is a pill, headline, and three numbered steps each in its own rounded card with a peach numbered circle.

- [ ] **Step 5: Commit**

```bash
git add landing/index.html landing/styles.css landing/assets/illustrations/how-it-works.svg
git commit -m "feat(landing): how-it-works section with three-step walkthrough"
```

---

## Task 6: Pricing section (five tiers, Business highlighted)

**Files:**
- Modify: `landing/index.html` — append the pricing section after how-it-works
- Modify: `landing/styles.css` — append pricing styles

The five tiers match `PLANS` in `backend/main.py:151-157`:

| Tier      | Screens | Price        |
|-----------|---------|--------------|
| Starter   | up to 3 | $9.99/mo     |
| Growth    | up to 5 | $12.99/mo    |
| Business  | up to 10| $24.99/mo    | ← "Most popular"
| Pro       | up to 25| $49.99/mo    |
| Enterprise| 25+     | Custom       |

- [ ] **Step 1: Append the pricing markup**

Insert into `landing/index.html` after the how-it-works `</section>`:

```html
    <!-- ── Pricing ──────────────────────────────────────────── -->
    <section class="section section--butter" id="pricing">
      <div class="container">
        <div class="section-head">
          <span class="pill">Honest pricing</span>
          <h2 class="section-title">Five tiers. No surprises.</h2>
          <p class="section-sub">Priced in USD. Billed monthly. Cancel anytime — we'd rather earn next month than trap you in a contract.</p>
        </div>

        <div class="pricing-grid">
          <article class="pricing-card">
            <h3>Starter</h3>
            <div class="pricing-price"><span class="pricing-amt">$9.99</span><span class="pricing-per">/ month</span></div>
            <div class="pricing-screens">Up to 3 screens</div>
            <ul class="pricing-list">
              <li>All core features</li>
              <li>Playlists + zones</li>
              <li>Email support</li>
            </ul>
            <a class="btn btn-ghost" href="#" data-cta="signup">Start free trial</a>
          </article>

          <article class="pricing-card">
            <h3>Growth</h3>
            <div class="pricing-price"><span class="pricing-amt">$12.99</span><span class="pricing-per">/ month</span></div>
            <div class="pricing-screens">Up to 5 screens</div>
            <ul class="pricing-list">
              <li>Everything in Starter</li>
              <li>Multi-location scheduling</li>
              <li>Priority email support</li>
            </ul>
            <a class="btn btn-ghost" href="#" data-cta="signup">Start free trial</a>
          </article>

          <article class="pricing-card pricing-card--popular">
            <span class="pill pricing-ribbon">Most popular</span>
            <h3>Business</h3>
            <div class="pricing-price"><span class="pricing-amt">$24.99</span><span class="pricing-per">/ month</span></div>
            <div class="pricing-screens">Up to 10 screens</div>
            <ul class="pricing-list">
              <li>Everything in Growth</li>
              <li>Team roles + audit log</li>
              <li>WhatsApp support</li>
            </ul>
            <a class="btn btn-primary" href="#" data-cta="signup">Start free trial</a>
          </article>

          <article class="pricing-card">
            <h3>Pro</h3>
            <div class="pricing-price"><span class="pricing-amt">$49.99</span><span class="pricing-per">/ month</span></div>
            <div class="pricing-screens">Up to 25 screens</div>
            <ul class="pricing-list">
              <li>Everything in Business</li>
              <li>Dedicated onboarding</li>
              <li>SLA on response times</li>
            </ul>
            <a class="btn btn-ghost" href="#" data-cta="signup">Start free trial</a>
          </article>

          <article class="pricing-card">
            <h3>Enterprise</h3>
            <div class="pricing-price"><span class="pricing-amt">Custom</span></div>
            <div class="pricing-screens">25+ screens</div>
            <ul class="pricing-list">
              <li>Volume pricing</li>
              <li>Self-hosted option</li>
              <li>Named account manager</li>
            </ul>
            <a class="btn btn-ghost" href="mailto:hello@sawwii.com">Contact us</a>
          </article>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Append pricing CSS**

```css
/* ─────────────────────────────────────────────────────────────
   Pricing
   ───────────────────────────────────────────────────────────── */
.pricing-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  align-items: stretch;
}

.pricing-card {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-card);
  padding: 28px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: relative;
  transition: transform var(--t-normal);
}

.pricing-card:hover { transform: translateY(-4px); }

.pricing-card--popular {
  background: var(--cream);
  border: 2px solid var(--plum);
  box-shadow: var(--shadow-card);
  transform: translateY(-8px);
}

.pricing-ribbon {
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--peach);
  white-space: nowrap;
}

.pricing-price {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.pricing-amt {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 32px;
  color: var(--plum);
}

.pricing-per {
  color: var(--sand);
  font-size: 13px;
}

.pricing-screens {
  color: var(--cocoa);
  font-weight: 500;
  font-size: 14px;
  padding-bottom: 14px;
  border-bottom: 1.5px dashed var(--cream-border);
}

.pricing-list {
  list-style: none;
  display: grid;
  gap: 10px;
  flex-grow: 1;
  font-size: 14px;
}

.pricing-list li {
  color: var(--cocoa);
  display: flex;
  gap: 8px;
}

.pricing-list li::before {
  content: '✓';
  color: var(--peach-deep);
  font-weight: 700;
}

.pricing-card .btn { width: 100%; justify-content: center; }

@media (max-width: 1080px) { .pricing-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px)  { .pricing-grid { grid-template-columns: 1fr; } }
@media (max-width: 1080px) { .pricing-card--popular { transform: none; } }
```

- [ ] **Step 3: Rebuild and verify**

Expected: a butter section with five pricing cards in a row (Starter / Growth / Business / Pro / Enterprise). The Business card is slightly taller, has a thicker plum border, a peach "Most popular" ribbon on top, and a peach primary CTA instead of ghost. All other cards have ghost CTAs. The Enterprise CTA says "Contact us" and has `mailto:hello@sawwii.com`.

At ≤1080px, the grid collapses to 2 columns (Enterprise wraps alone), at ≤560px to a single column.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html landing/styles.css
git commit -m "feat(landing): pricing section with five tiers, Business highlighted"
```

---

## Task 7: AI spotlight + FAQ + footer

**Files:**
- Modify: `landing/index.html` — append three sections: AI spotlight, FAQ, footer
- Modify: `landing/styles.css` — append styles for each

- [ ] **Step 1: Append the markup**

Insert into `landing/index.html` after the pricing `</section>`:

```html
    <!-- ── AI spotlight ─────────────────────────────────────── -->
    <section class="section" id="ai">
      <div class="container">
        <div class="ai-card">
          <div class="ai-copy">
            <span class="pill" style="background: var(--lavender);">Phase 3 preview</span>
            <h2>Your website, turned into a menu board — in under a minute.</h2>
            <p>
              Paste your website or Instagram URL. Sawwii reads your menu, extracts your brand palette and logo, and drafts a polished playlist — ready to tweak and publish. The feature that killed our competitors' ten-hour designer bills.
            </p>
            <a class="btn btn-primary btn-lg" href="mailto:hello@sawwii.com?subject=Sawwii%20AI%20early%20access">Get early access</a>
          </div>
          <div class="ai-visual">
            <div class="ai-chip">sawwii.com/menu</div>
            <div class="ai-arrow">→</div>
            <div class="ai-mini-tv">
              <div class="ai-mini-hero"></div>
              <div class="ai-mini-line"></div>
              <div class="ai-mini-line short"></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ── FAQ ──────────────────────────────────────────────── -->
    <section class="section section--butter" id="faq">
      <div class="container faq-wrap">
        <div class="section-head" style="margin-bottom: 40px;">
          <span class="pill">FAQ</span>
          <h2 class="section-title">Answers before you ask.</h2>
        </div>

        <div class="faq-list">
          <details class="faq-item" open>
            <summary>What devices can show the content?</summary>
            <p>Any device with a modern browser — smart TVs, Android boxes, Raspberry Pis, old iPads, or a laptop plugged into an HDMI cable. You pair by opening a URL and typing a six-digit code.</p>
          </details>
          <details class="faq-item">
            <summary>Do I need a designer?</summary>
            <p>No. Upload images or videos, drag them into a playlist, and go. Zones, schedules, and transitions are already handled. The upcoming AI menu generator drafts the first version for you.</p>
          </details>
          <details class="faq-item">
            <summary>Is Arabic supported?</summary>
            <p>Full bilingual English + Arabic with right-to-left layouts is coming in Phase 2. The product is built for Gulf business owners from day one — Arabic is not an afterthought.</p>
          </details>
          <details class="faq-item">
            <summary>How do I pay?</summary>
            <p>Credit card, Apple Pay, and Google Pay through Stripe — live at launch. KNET is on the roadmap for Kuwait customers who want to pay locally.</p>
          </details>
          <details class="faq-item">
            <summary>What happens after the 14-day trial?</summary>
            <p>You pick a plan, enter a card, and keep going. No auto-charge during the trial — we ask before we bill.</p>
          </details>
        </div>
      </div>
    </section>

    <!-- ── Final CTA ────────────────────────────────────────── -->
    <section class="section section-cta">
      <div class="container section-cta-inner">
        <h2>Ready to make it?</h2>
        <p>14 days free. No card. No lock-in.</p>
        <div class="hero-ctas" style="justify-content: center;">
          <a class="btn btn-primary btn-lg" href="#" data-cta="signup">Start free trial</a>
          <a class="btn btn-ghost btn-lg" href="mailto:hello@sawwii.com">Talk to us</a>
        </div>
      </div>
    </section>

    <!-- ── Footer ───────────────────────────────────────────── -->
    <footer class="footer">
      <div class="container footer-inner">
        <div class="footer-brand">
          <span class="nav-mark">◠</span>
          <span class="footer-wordmark">Sawwii</span>
          <span class="footer-tag">سوّي — make it</span>
        </div>
        <div class="footer-links">
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
          <a href="#faq">FAQ</a>
          <a href="mailto:hello@sawwii.com">Contact</a>
        </div>
        <div class="footer-copy">© 2026 Sawwii. Built in the Gulf.</div>
      </div>
    </footer>
```

- [ ] **Step 2: Append the CSS**

```css
/* ─────────────────────────────────────────────────────────────
   AI spotlight
   ───────────────────────────────────────────────────────────── */
.ai-card {
  background: linear-gradient(135deg, var(--lavender) 0%, var(--rose) 100%);
  border: 2px solid var(--plum);
  border-radius: var(--r-card);
  padding: 56px;
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 40px;
  align-items: center;
  box-shadow: var(--shadow-card);
}

.ai-copy h2 { margin-top: 18px; margin-bottom: 18px; }
.ai-copy p  { font-size: 17px; margin-bottom: 28px; }

.ai-visual {
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: center;
}

.ai-chip {
  background: var(--cream);
  border: 1.5px solid var(--plum);
  border-radius: var(--r-btn);
  padding: 10px 16px;
  font-family: var(--font-body);
  font-weight: 500;
  font-size: 14px;
  box-shadow: 2px 2px 0 var(--plum);
}

.ai-arrow {
  font-size: 32px;
  color: var(--plum);
}

.ai-mini-tv {
  background: var(--cream);
  border: 1.5px solid var(--plum);
  border-radius: 10px;
  padding: 12px;
  width: 140px;
  display: grid;
  gap: 8px;
  box-shadow: 2px 2px 0 var(--plum);
}

.ai-mini-hero { height: 60px; background: var(--peach); border-radius: 6px; }
.ai-mini-line { height: 8px;  background: var(--plum); border-radius: 4px; }
.ai-mini-line.short { width: 60%; }

@media (max-width: 880px) {
  .ai-card { grid-template-columns: 1fr; padding: 36px; }
}

/* ─────────────────────────────────────────────────────────────
   FAQ
   ───────────────────────────────────────────────────────────── */
.faq-wrap { max-width: 720px; margin: 0 auto; }

.faq-list { display: grid; gap: 12px; }

.faq-item {
  background: var(--cream);
  border: 1.5px solid var(--cream-border);
  border-radius: var(--r-card);
  padding: 18px 24px;
}

.faq-item summary {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 500;
  color: var(--plum);
  cursor: pointer;
  list-style: none;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.faq-item summary::-webkit-details-marker { display: none; }

.faq-item summary::after {
  content: '+';
  font-size: 24px;
  color: var(--peach-deep);
  transition: transform var(--t-fast);
}

.faq-item[open] summary::after { transform: rotate(45deg); }

.faq-item p {
  margin-top: 12px;
  font-size: 15px;
}

/* ─────────────────────────────────────────────────────────────
   Final CTA
   ───────────────────────────────────────────────────────────── */
.section-cta {
  text-align: center;
  padding: 80px 0;
}

.section-cta-inner h2 { margin-bottom: 12px; }

.section-cta-inner p {
  font-size: 17px;
  margin-bottom: 32px;
}

/* ─────────────────────────────────────────────────────────────
   Footer
   ───────────────────────────────────────────────────────────── */
.footer {
  background: var(--plum);
  color: var(--cream);
  padding: 48px 0;
}

.footer a { color: var(--cream); opacity: 0.8; }
.footer a:hover { color: var(--peach); opacity: 1; }

.footer-inner {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: center;
  justify-content: space-between;
}

.footer-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.footer-brand .nav-mark {
  background: var(--peach);
  color: var(--plum);
}

.footer-wordmark {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 22px;
  color: var(--cream);
}

.footer-tag {
  color: var(--rose);
  font-size: 14px;
}

.footer-links { display: flex; gap: 24px; font-size: 14px; }

.footer-copy {
  color: var(--cream);
  opacity: 0.55;
  font-size: 13px;
  width: 100%;
  text-align: center;
  border-top: 1px solid rgba(232, 220, 198, 0.15);
  padding-top: 24px;
  margin-top: 8px;
}
```

- [ ] **Step 3: Rebuild and verify**

Expected new sections, in order:
- A single centred rounded card with a lavender→rose gradient background and a plum border, containing the AI pitch on the left and a tiny laptop-URL → mini-TV illustration on the right.
- A butter-coloured FAQ section with five `<details>` cards — the first one open by default. Clicking a summary toggles its plus-sign into an × (via 45° rotation).
- A centred cream final CTA with an h2 and two buttons.
- A plum footer with the Sawwii wordmark, tagline in Arabic, a nav, and a © line on a second row.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html landing/styles.css
git commit -m "feat(landing): AI spotlight, FAQ, final CTA, and footer"
```

---

## Task 8: Mobile nav toggle + CTA wiring + favicon

**Files:**
- Modify: `landing/app.js` — replace placeholder with nav toggle + CTA href wiring + smooth scroll close
- Modify: `landing/index.html` — reference the favicon in `<head>`
- Create: `landing/assets/favicon.svg`

- [ ] **Step 1: Create the favicon**

Write `landing/assets/favicon.svg`:

```svg
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="6" y="6" width="52" height="52" rx="14" fill="#F4B9A1" stroke="#3E2B4F" stroke-width="4"/>
  <path d="M20 42 C 24 30, 40 30, 44 42" stroke="#3E2B4F" stroke-width="5" fill="none" stroke-linecap="round"/>
  <circle cx="32" cy="22" r="4" fill="#3E2B4F"/>
</svg>
```

- [ ] **Step 2: Add the favicon `<link>`**

In `landing/index.html`, inside `<head>`, immediately after the `<meta name="viewport">` line, insert:

```html
    <link rel="icon" type="image/svg+xml" href="assets/favicon.svg" />
```

- [ ] **Step 3: Replace `landing/app.js` with the real logic**

Overwrite the file contents with:

```javascript
/* ─────────────────────────────────────────────────────────────
   Sawwii landing — client behaviour
   ───────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  const APP_URL = (window.APP_URL || 'http://192.168.18.192:3000').replace(/\/+$/, '');

  /* ── CTA wiring ─────────────────────────────────────────── */
  const signupTargets = [
    document.getElementById('cta-signup'),
    document.getElementById('cta-signup-hero'),
    ...document.querySelectorAll('[data-cta="signup"]'),
  ].filter(Boolean);

  signupTargets.forEach((el) => { el.href = APP_URL + '/#signup'; });

  const signinEl = document.getElementById('cta-signin');
  if (signinEl) signinEl.href = APP_URL + '/';

  /* ── Mobile nav toggle ──────────────────────────────────── */
  const toggle = document.getElementById('nav-toggle');
  const links  = document.getElementById('nav-links');

  function closeMenu() {
    links.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  }

  if (toggle && links) {
    toggle.addEventListener('click', () => {
      const open = links.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', String(open));
    });

    links.querySelectorAll('a').forEach((a) => {
      a.addEventListener('click', () => {
        if (links.classList.contains('is-open')) closeMenu();
      });
    });
  }
})();
```

The admin SPA does not currently have a `#signup` deep-link — it always opens on the Sign In tab. Deep-linking is a one-line follow-up (in `frontend/app.js`'s `boot()`, check `location.hash === '#signup'` and call `showAuthTab('signup')`). Leave that follow-up for a later task; the `#signup` hash in the href is harmless today because the hash is just ignored by the SPA, and lands the user on Sign In.

- [ ] **Step 4: Rebuild and verify**

```bash
docker-compose build landing && docker-compose up -d landing
```

Verify:
- The browser tab shows the peach favicon with a plum smile.
- Shrink the browser to < 880px: the nav collapses to a hamburger. Click it — the four anchor links drop down with a fade/slide; click any link, the menu closes automatically.
- Click "Start free trial" in the nav or hero — the browser navigates to `http://192.168.18.192:3000/#signup` and lands on the admin SPA (which currently opens on Sign In — the `#signup` hash is cosmetic today and becomes functional once the deep-link follow-up ships).
- Click "Sign in" — navigates to `http://192.168.18.192:3000/`.

Optional deep-link follow-up (small, can go in the same commit if you want): edit `frontend/app.js`'s `boot()` to honour the hash:

```javascript
if (location.hash === '#signup') showAuthTab('signup');
```

If you do this, rebuild the admin container too (`docker-compose build frontend && docker-compose up -d frontend`) and verify that hitting `http://192.168.18.192:3000/#signup` directly opens on the Create Account tab.

- [ ] **Step 5: Commit**

```bash
git add landing/app.js landing/index.html landing/assets/favicon.svg
# If you also edited frontend/app.js for the deep-link follow-up:
# git add frontend/app.js
git commit -m "feat(landing): mobile nav, CTA wiring to admin, favicon"
```

---

## Task 9: Final polish + end-to-end smoke test

**Files:** no edits; this task only verifies.

- [ ] **Step 1: Lighthouse-style sanity check**

```bash
curl -s -o /dev/null -w "HTTP %{http_code} · %{size_download} bytes · %{time_total}s\n" http://192.168.18.192:3002/
curl -s -I http://192.168.18.192:3002/styles.css | grep -iE 'content-encoding|cache-control'
curl -s -I http://192.168.18.192:3002/assets/illustrations/hero.svg | grep -iE 'content-encoding|cache-control'
```

Expected:
- HTTP 200, < 20 KB for the HTML (SVG illustrations are external), < 1s total time.
- `content-encoding: gzip` on both CSS and SVG responses.
- `cache-control: public, max-age=604800, stale-while-revalidate=86400` on both.

- [ ] **Step 2: Confirm all four services are healthy**

```bash
docker-compose ps
```

Expected: `postgres`, `backend`, `frontend`, `player`, and `landing` all `Up (healthy)`.

- [ ] **Step 3: Cross-check the CTAs end-to-end**

1. Open http://192.168.18.192:3002/ in an incognito window.
2. Click **Start free trial** (nav). Browser → `http://192.168.18.192:3000/#signup`. Expected: admin loads. (If the deep-link follow-up from Task 8 was applied, it lands on Create Account; otherwise on Sign In — either is acceptable.)
3. Go back. Click any of the five pricing CTAs → same target.
4. Click the Enterprise "Contact us" → opens mailto.
5. Click the footer "Contact" → opens mailto.

- [ ] **Step 4: Responsive sanity**

Resize the browser to ~420px. Expected:
- Nav collapses to a hamburger, tap-to-open works, links auto-close on click.
- Hero art sits above the copy; CTA buttons wrap but remain full-size.
- Features grid stacks to a single column.
- How-it-works stacks.
- Pricing stacks.
- AI card stacks.
- FAQ width comfortable, no horizontal scroll anywhere.

- [ ] **Step 5: Confirm the admin stack is untouched**

```bash
curl -s http://192.168.18.192:3000/ | grep -E '<title>|<h1>'
docker-compose run --rm backend pytest
```

Expected: admin title + h1 still "Sawwii", pytest 7/7 green. This plan changed no backend code and no `frontend/` files (unless Task 8's optional deep-link follow-up was taken — in which case pytest is still the correct check for zero behaviour change).

- [ ] **Step 6: Final report**

Print:

```
Landing page complete.
- 8 new commits on feature/landing-page.
- New docker-compose service `landing` serving the marketing site at :3002.
- Sections: nav, hero, features, how-it-works, pricing, AI spotlight, FAQ, CTA, footer.
- Pastel retro-modern palette: cream/butter/peach/mint/lavender/rose/plum.
- IBM Plex Serif + IBM Plex Sans. 6 SVG illustrations inline or as assets.
- All CTAs route into the existing admin signup at :3000.
- Admin regression suite: 7/7 green (no backend changes).
- Ready to merge to main, or to start the admin re-theme follow-up plan.
```

---

## Notes for future plans

**Admin re-theme** will port `landing/styles.css`'s `:root` palette into `frontend/styles.css`, replacing the dark cyan/indigo variables. The existing gradient on the header `<h1>` (currently `linear-gradient(90deg, var(--cyan), var(--blue), var(--indigo))`) becomes a pastel ribbon; buttons shift from cyan-filled to peach-filled. The dashboard plan card's `.plan-status-*` pill colours map 1:1 onto the new palette. That's a single focused plan — two or three commits.

**Bilingual EN/AR** on the landing will: (1) add a tiny language toggle in the nav that writes `localStorage.lang`, (2) flip `<html lang dir>`, (3) load IBM Plex Sans Arabic + IBM Plex Serif's Arabic-compatible fallback, (4) move copy strings into `translations/{en,ar}.json`. The palette and illustrations work identically in RTL — the only layout changes are nav order and hero art side.

**Production DNS cutover** (when ready): point `sawwii.com` → Montreal VPS, add Caddy or Traefik in Docker, route `sawwii.com` → landing `:3002`, `app.sawwii.com` → frontend `:3000`, `play.sawwii.com` → player `:3001`, `api.sawwii.com` → backend `:8000`. Set `APP_URL=https://app.sawwii.com` in `.env` and redeploy landing.
