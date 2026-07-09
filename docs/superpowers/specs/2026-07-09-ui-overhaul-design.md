# UI Overhaul — Design Spec

**Date:** 2026-07-09
**Status:** Approved via interactive mockups (user viewed both pages in browser, light/dark + EN/AR)
**Mockups:** `docs/superpowers/specs/2026-07-09-ui-overhaul-mockups/dashboard.html` and `landing.html` — these are the visual source of truth for the implementation.

## Goal

Replace the pastel/cream "kawaii" theme with a modern, sleek, professional design system across the product, while keeping the mascot as a deliberate, dosed brand element ("serious but light and fun" — user's words). No framework migration: the vanilla-JS SPA architecture stays.

## Scope

**In:**
1. **Dashboard SPA** (`frontend/`) — full restyle + navigation restructure (top-nav → grouped sidebar)
2. **Auth screens** (login/signup/OTP flows inside the SPA)
3. **Backend-served OAuth pages** (`backend/templates/oauth_login.html`, `oauth_consent.html`, `oauth_error.html`) — match the new system
4. **Landing site** (`landing/`) — full restyle, same tokens
5. **Mascot integration** — placement rules below

**Out:**
- Player (`player/`) — TV-facing, renders content fullscreen; only its pairing screen may inherit tokens later (separate project)
- `api-docs.html` — functional as-is; can be restyled opportunistically but not required
- Marketing copy changes — all existing EN/AR strings are kept verbatim unless a new UI element needs a new key
- Any backend logic. This is presentational only.

## Design System (tokens)

Defined once, shared by dashboard and landing (duplicated per app to keep the two containers independent, but byte-identical token block).

- **Fonts:** Inter (LTR), IBM Plex Sans Arabic (RTL), JetBrains Mono (code/pair-codes)
- **Accent:** coral `#E8794A` / hover `#D96835` / soft `rgba(232,121,74,.12)` — derived from the mascot's leaf coral, replacing the old peach
- **Status:** green `#2FB344`, amber `#E8A33D`, red `#E5484D`
- **Themes:** `[data-theme="dark"]` and `[data-theme="light"]` variable sets (see mockups for exact values).
  - **Default: follow system** (`prefers-color-scheme`), user toggle overrides, choice persisted in `localStorage` (`khanshoof_theme`)
- **Radii:** 6/10/14/20px. Subtle borders (`rgba(255,255,255,.07)` dark / `#E6E8EC` light) instead of heavy shadows.
- **RTL:** logical properties (`inset-inline-start`, `margin-inline-*`) everywhere; the existing `dir` switching in `i18n.js` is kept.

## Dashboard architecture

### Layout (was: sticky top header + horizontal nav; becomes: sidebar + topbar)

- **Sidebar** (232px, sticky, collapses on <900px to an off-canvas drawer behind the existing hamburger):
  - Brand: mascot face + "Khanshoof" + LIVE env pill
  - Grouped nav — **Displays:** Screens, Walls, Sites · **Content:** Media, Playlists, Schedules · **Organization:** Users, API Keys, Audit log, Billing
  - Count badges on Screens/Walls/Sites (populated from already-fetched list data; no new endpoints)
  - Footer: user avatar (initial), username, org + plan; connection-settings and sign-out live here
- **Topbar** (52px): section search/filter, lang toggle (عربي/English), theme toggle, notifications placeholder
- **Main:** max-width 1200px page area; each existing `<section class="panel">` keeps its **id** and `data-i18n` keys — `app.js` section switching (`showSection`, `nav button[data-section]`) continues to work with buttons relocated into the sidebar (selector stays valid as long as buttons live inside a `<nav>` with `data-section` attrs).

### Components (all shown in the dashboard mockup)

Stat cards · screen cards with 16:9 thumb, LIVE tag, status dot — green = `is_online` (API already exposes it, 90s threshold), amber = derived client-side from `last_seen` (offline < 10 min), red = offline longer · segmented filters · chips · redesigned tables (audit log, schedules, billing history) · modals, toasts, subscription banner, confirm dialog restyled to tokens · forms/inputs.

### Compatibility constraints (hard requirements)

- **Every existing element id referenced by `app.js` is preserved.** The restructure moves elements and changes classes; it must not rename ids or remove `data-section`/`data-i18n` attributes.
- All i18n keys in `frontend/i18n/en.json` + `ar.json` keep working; new UI (nav groups, theme toggle, stat labels) adds new keys to both files.
- Existing flows must keep working end-to-end: auth (password + Google), pairing, zones editor, walls wizard, media upload/drag-drop, playlist reorder, billing/KNET, API keys, audit log.

## Landing architecture

Same tokens; sections as in the mockup: sticky nav → hero (pill, gradient-em headline, CTAs, **CSS-drawn product frame with mascot peeking over the edge**) → features (3, third card leads with "Arabic-first, offline-proof") → how-it-works (3 steps) → pricing (5 real tiers, Business ribbon + scale) → FAQ accordions (native `<details>`) → CTA band (heart-face mascot) → footer (wink + "خنشوف — let's see").

- All SEO artifacts preserved: JSON-LD, meta/OpenGraph/Twitter tags, sitemap.xml, robots.txt, llms.txt, /AGENTS.md serving
- Existing i18n keys and `data-cta="signup"` hooks preserved

## Mascot placement rules

Dosed, never decorative wallpaper. `image-rendering: pixelated` at all sizes.

| Surface | Placement | Face |
|---|---|---|
| Both navs/brand | logo slot, tilts on hover | `v1_kawaii` |
| Landing hero | peeks over product frame, 4s bob animation, mirrors in RTL | `v1_smile` |
| Landing CTA band | above headline | `v1_heart` |
| Footers | inline with wordmark | `v1_wink` |
| Empty states (no screens/media/playlists/walls) | grayscale at rest, color on hover | `v1_star` |
| Success moments (first pairing, payment success) | toast/panel accent | `v1_heart` |
| Error/404/OAuth error | | `v1_big` |
| Info notes | replaces generic ⓘ | `v1_wink` |

## Implementation plan shape (detail in the plan doc)

Phased, one PR per phase, each independently shippable:

1. **Dashboard shell** — tokens, sidebar/topbar layout, theme system, nav regrouping; sections still render with legacy inner styles
2. **Dashboard components** — cards/tables/forms/modals/toasts/banner per section (Screens, Media, Playlists, Schedules, Sites, Walls, Users, API Keys, Audit, Billing)
3. **Auth + OAuth templates** — SPA auth screens + the three backend-served pages
4. **Landing restyle**
5. **Mascot moments + polish** — empty states, success/error faces, micro-interactions

## Error handling / risk

- **Biggest risk:** `app.js` (3.7k lines) has pervasive DOM coupling. Mitigation: ids/attrs frozen (constraint above); grep-verify every `getElementById`/`querySelector` target still exists after each phase.
- CSS is fully rewritten (`styles.css` 2.5k lines); old class names that `app.js` toggles (`hidden`, `nav-active`, `active`, `dropzone-active`, status-dot classes) are documented and kept.
- Theme FOUC: inline `<head>` script sets `data-theme` before first paint.
- RTL regressions: manual smoke in AR after each phase (layout mirror, sidebar side, peek flip).

## Testing

- **Backend:** full pytest suite must stay green (OAuth template changes touch `test_oauth.py` render assertions — update selectors there if needed, semantics unchanged)
- **Frontend:** no test infra exists; verification is browser smoke per phase (both themes × both languages × mobile width) using the verify/run flow against local containers
- **Player untouched** — no re-verification needed

## Rollout

Feature branch `feature/ui-overhaul`, one PR per phase into main, rebuild backend/frontend/landing containers per merge. The docker-cp'd `/preview*.html` files in the running frontend container are throwaway and vanish on first rebuild.
