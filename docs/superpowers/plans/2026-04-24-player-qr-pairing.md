# Player QR Pairing UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an unpaired TV opens `play.khanshoof.com`, show a full-screen pairing view with a 5-char code + QR pointing to `https://app.khanshoof.com/pair?code=…`. The player polls the backend and swaps to content the moment an admin claims the code. Expired codes auto-refresh. Already-paired devices keep using their stored `screen_token` unchanged.

**Architecture:** No new backend work — Plan 1's endpoints (`POST /screens/request_code`, `GET /screens/poll/{code}`) are already merged on `main`. All changes are in `player/`. A small vendored QR library (`qrcode-generator`, no deps) renders the QR offline. The existing `boot()` in `player.js` grows a third branch for the "no token anywhere" case; the legacy `?code=` short-path (calls `POST /screens/pair`) stays intact and is retired in Plan 4. Invalid/revoked tokens are detected by a 401/404 on content or layout fetches — player clears the bad token and restarts `boot()` into the pairing view.

**Tech Stack:** Vanilla JS + HTML + CSS (no build step), `qrcode-generator` by Kazuhiko Arase (MIT, ~8 KB), nginx static serve, Docker.

**Pairing view contract (decided):**
- Shown when: no `?token=` param, no `screen_token` in localStorage, no legacy `?code=` param.
- Poll interval: 3 seconds.
- On `status: "expired"`: immediately call `/screens/request_code` again and re-render.
- On `status: "paired"`: persist `screen_token`, tear down pairing view, call the existing `fetchLayout()`/`fetchContent()` path.
- QR payload: `${APP_URL}/pair?code=${code}` — Plan 3 will implement that admin page; until then the QR still resolves to the admin SPA domain (404 is acceptable during Plan 2 smoke since claim works via the API too).
- Pastel Khanshoof styling inherits `--cream`/`--peach`/`--plum` and IBM Plex Serif, same family as admin + landing.

---

## Prerequisites

Working tree is `/home/ahmed/signage`, branch `feature/player-qr-pairing` (already checked out, clean, at same commit as `main`).

Baseline regression must be 38 passed before starting:

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

## File Structure

```
player/
├── vendor/
│   └── qrcode.js        # NEW — vendored qrcode-generator library (MIT)
├── index.html            # ADD #pairing panel + vendor script tag
├── styles.css            # ADD pastel pairing-view rules
├── player.js             # ADD request_code + poll + render + invalid-token recovery
├── docker-entrypoint.sh  # ADD APP_URL env → window.APP_URL in config.js
└── config.js             # generated at container start (already)
```

No backend or admin frontend changes in this plan.

---

## Task 1: Vendor the QR library + expose APP_URL in config.js

**Files:**
- Create: `player/vendor/qrcode.js` (downloaded)
- Modify: `player/index.html` (add `<script src="vendor/qrcode.js">` before `player.js`)
- Modify: `player/docker-entrypoint.sh` (write `window.APP_URL` alongside `window.API_BASE_URL`)

- [ ] **Step 1: Download qrcode-generator into `player/vendor/`**

Use the pinned release from the library's GitHub (v1.4.4, MIT, ~8 KB minified):

```bash
mkdir -p /home/ahmed/signage/player/vendor
curl -L -o /home/ahmed/signage/player/vendor/qrcode.js \
  https://raw.githubusercontent.com/kazuhikoarase/qrcode-generator/1.4.4/js/qrcode.js
```

Verify:

```bash
head -5 /home/ahmed/signage/player/vendor/qrcode.js
wc -c  /home/ahmed/signage/player/vendor/qrcode.js
```

Expected: file begins with the Kazuhiko Arase header/licence block and is ≥ 30 KB (unminified).

- [ ] **Step 2: Wire vendor script in `player/index.html`**

Replace the existing `<head>` and `<body>` of `player/index.html` with:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Khanshoof Player</title>
    <link rel="stylesheet" href="styles.css" />
    <script src="config.js"></script>
    <script src="vendor/qrcode.js"></script>
  </head>
  <body>
    <div id="player">
      <div id="status">Connecting...</div>
      <div id="content" class="single-content"></div>
      <div id="zones" class="zones-layout hidden"></div>

      <section id="pairing" class="pairing hidden" aria-live="polite">
        <h1 class="pairing-brand">Khanshoof</h1>
        <p class="pairing-intro">Pair this display to your account</p>
        <div class="pairing-code" id="pairing-code" aria-label="Pairing code">—</div>
        <div class="pairing-qr" id="pairing-qr" aria-label="QR code for pairing URL"></div>
        <ol class="pairing-steps">
          <li>On your phone, open <strong id="pairing-url">app.khanshoof.com/pair</strong></li>
          <li>Enter the code above, or scan the QR to jump there</li>
          <li>Pick which screen this is — the display updates automatically</li>
        </ol>
        <p class="pairing-meta" id="pairing-meta">Code refreshes every 10 minutes</p>
      </section>
    </div>
    <script src="player.js"></script>
  </body>
</html>
```

- [ ] **Step 3: Expose `APP_URL` via container entrypoint**

Replace `player/docker-entrypoint.sh` with:

```sh
#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

exec nginx -g 'daemon off;'
```

- [ ] **Step 4: Rebuild + smoke the vendor load**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build player
curl -s https://play.khanshoof.com/vendor/qrcode.js | head -1
curl -s https://play.khanshoof.com/config.js
```

Expected: first command prints the qrcode.js header comment. Second prints both `window.API_BASE_URL = "https://api.khanshoof.com";` and `window.APP_URL = "https://app.khanshoof.com";`.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add player/vendor/qrcode.js player/index.html player/docker-entrypoint.sh
git -C /home/ahmed/signage commit -m "feat(player): vendor qrcode-generator + expose APP_URL"
```

---

## Task 2: Pairing-view CSS (Khanshoof pastel theme)

**Files:**
- Modify: `player/styles.css`

- [ ] **Step 1: Replace `player/styles.css` with the pastel-aware stylesheet**

Full file contents:

```css
* {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  width: 100%;
  height: 100%;
  background: #000000;
  color: #ffffff;
  font-family: 'IBM Plex Sans', system-ui, -apple-system, sans-serif;
}

#player {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

#content {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

#content img,
#content video,
#content iframe {
  max-width: 100%;
  max-height: 100%;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.fade-media {
  opacity: 0;
  transition: opacity 0.6s ease-in-out;
}

.fade-media.visible {
  opacity: 1;
}

.zones-layout {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.zone-region {
  position: absolute;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.zone-content {
  width: 100%;
  height: 100%;
}

.zone-content img,
.zone-content video,
.zone-content iframe {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

#status {
  position: absolute;
  top: 16px;
  left: 16px;
  background: rgba(0, 0, 0, 0.6);
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 14px;
  z-index: 2;
  display: none;
}

.hidden { display: none !important; }

/* ── Pairing view ────────────────────────────────────────────── */
.pairing {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 800px at 70% 30%, #FDF3D6 0%, transparent 60%),
    radial-gradient(900px 600px at 20% 80%, #F4B9A1 0%, transparent 55%),
    #FFF8F0;
  color: #3E2B4F;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: clamp(16px, 2vh, 32px);
  padding: clamp(24px, 4vh, 64px);
  z-index: 10;
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
}

.pairing-brand {
  font-family: 'IBM Plex Serif', Georgia, serif;
  font-size: clamp(28px, 3.5vh, 48px);
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #3E2B4F;
}

.pairing-intro {
  font-size: clamp(16px, 2.2vh, 24px);
  color: #5D4A66;
  margin: 0;
}

.pairing-code {
  font-family: 'IBM Plex Mono', monospace;
  font-size: clamp(72px, 14vh, 220px);
  font-weight: 700;
  letter-spacing: clamp(6px, 1vh, 16px);
  color: #3E2B4F;
  background: #FFFFFF;
  padding: clamp(12px, 2vh, 28px) clamp(24px, 4vw, 56px);
  border-radius: 20px;
  border: 3px solid #E09478;
  box-shadow: 0 10px 30px rgba(62, 43, 79, 0.12);
}

.pairing-qr {
  background: #FFFFFF;
  padding: clamp(12px, 1.6vh, 20px);
  border-radius: 16px;
  border: 1px solid #E8DCC6;
  box-shadow: 0 10px 30px rgba(62, 43, 79, 0.12);
}

.pairing-qr img,
.pairing-qr canvas {
  display: block;
  width: clamp(200px, 28vh, 420px);
  height: clamp(200px, 28vh, 420px);
  image-rendering: pixelated;
}

.pairing-steps {
  font-size: clamp(14px, 1.8vh, 22px);
  color: #5D4A66;
  max-width: 560px;
  padding-left: 24px;
  line-height: 1.6;
  margin: 0;
}

.pairing-steps strong {
  color: #3E2B4F;
  font-weight: 600;
}

.pairing-meta {
  font-size: clamp(12px, 1.4vh, 16px);
  color: #A89382;
  margin: 0;
}
```

- [ ] **Step 2: Commit**

```bash
git -C /home/ahmed/signage add player/styles.css
git -C /home/ahmed/signage commit -m "feat(player): pastel Khanshoof styling for pairing view"
```

---

## Task 3: player.js — request_code + QR render on boot

**Files:**
- Modify: `player/player.js`

This task wires the new code-request + QR rendering. The poll loop comes in Task 4.

- [ ] **Step 1: Add constants + DOM refs + helpers near the top of `player.js`**

Find the existing block starting with `const statusEl = document.getElementById("status");` (around line 7). Insert immediately after the existing DOM ref block:

```javascript
const pairingEl = document.getElementById("pairing");
const pairingCodeEl = document.getElementById("pairing-code");
const pairingQrEl = document.getElementById("pairing-qr");
const pairingUrlEl = document.getElementById("pairing-url");
const pairingMetaEl = document.getElementById("pairing-meta");

const APP_URL = (window.APP_URL || "").trim() || "https://app.khanshoof.com";
const PAIR_POLL_INTERVAL_MS = 3000;

let activePairCode = null;
let pairPollTimer = null;
```

- [ ] **Step 2: Add pairing-view show/hide + QR render helpers**

Add the following helpers below the existing `clearZonePlayback()` function (around line 61):

```javascript
function showPairingView() {
  contentEl.classList.add("hidden");
  zonesEl.classList.add("hidden");
  pairingEl.classList.remove("hidden");
  statusEl.style.display = "none";
}

function hidePairingView() {
  pairingEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  statusEl.style.display = "";
}

function renderPairingCode(code) {
  pairingCodeEl.textContent = code;
  const url = `${APP_URL}/pair?code=${encodeURIComponent(code)}`;
  const host = APP_URL.replace(/^https?:\/\//, "").replace(/\/$/, "");
  pairingUrlEl.textContent = `${host}/pair`;

  // QR — type 0 = auto-fit version, "M" error correction handles modest TV glare
  const qr = qrcode(0, "M");
  qr.addData(url);
  qr.make();
  pairingQrEl.innerHTML = qr.createImgTag(8, 16);
}

async function requestPairingCode() {
  const res = await fetch(`${API_BASE}/screens/request_code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_agent: navigator.userAgent.slice(0, 500) }),
  });
  if (!res.ok) {
    throw new Error(`request_code failed: ${res.status}`);
  }
  return res.json(); // { code, device_id, expires_at, expires_in_seconds }
}
```

- [ ] **Step 3: Replace the "Missing pairing code or token" dead end in `boot()`**

Find the block in `boot()`:

```javascript
  if (!screenToken && !previewToken) {
    setStatus("Missing pairing code or token");
    return;
  }
```

Replace it with:

```javascript
  if (!screenToken && !previewToken) {
    await startPairingFlow();
    return;
  }
```

- [ ] **Step 4: Add `startPairingFlow()` (code-request + render only — no poll yet)**

Add directly above `boot()`:

```javascript
async function startPairingFlow() {
  showPairingView();
  try {
    const data = await requestPairingCode();
    activePairCode = data.code;
    renderPairingCode(data.code);
    pairingMetaEl.textContent = "Waiting for your phone…";
  } catch (err) {
    console.error(err);
    pairingCodeEl.textContent = "—";
    pairingMetaEl.textContent = "Can't reach server. Retrying…";
    setTimeout(startPairingFlow, 5000);
  }
}
```

- [ ] **Step 5: Rebuild + manual smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build player
```

Open `https://play.khanshoof.com/` in a new incognito window (so no stored `screen_token`). Expected:
- Pastel pairing view fills the screen.
- A 5-char code renders in big type (e.g. `K7M2Q`).
- QR renders to the right/below.
- URL text reads `app.khanshoof.com/pair`.
- Meta line: "Waiting for your phone…".

Scan the QR with a phone — it should open `https://app.khanshoof.com/pair?code=K7M2Q` (will 404 until Plan 3; that's expected).

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add player/player.js
git -C /home/ahmed/signage commit -m "feat(player): render pairing code + QR on unpaired boot"
```

---

## Task 4: player.js — poll loop with paired/expired handling

**Files:**
- Modify: `player/player.js`

- [ ] **Step 1: Add the poll helper + persistence**

Add below `requestPairingCode()` (inserted in Task 3):

```javascript
async function pollPairingCode(code) {
  const res = await fetch(`${API_BASE}/screens/poll/${encodeURIComponent(code)}`);
  if (!res.ok) {
    throw new Error(`poll failed: ${res.status}`);
  }
  return res.json(); // { status: pending|expired|paired, screen_id?, screen_name?, screen_token? }
}

function stopPairPoll() {
  if (pairPollTimer) {
    clearTimeout(pairPollTimer);
    pairPollTimer = null;
  }
}

async function onPaired(screenToken) {
  stopPairPoll();
  activePairCode = null;
  localStorage.setItem("screen_token", screenToken);
  hidePairingView();
  setStatus("Loading content...");
  // Re-run the same post-auth path boot() uses
  await resumeAfterPair(screenToken);
}

async function resumeAfterPair(token) {
  screenToken = token;
  const layout = await fetchLayout();
  if (layout?.zones && layout.zones.length > 0) {
    layoutSignature = getLayoutSignature(layout.zones);
    renderZonesLayout(layout.zones);
  } else {
    renderSingleLayout();
    await fetchContent();
  }
  if (!refreshLoopStarted) {
    startRefreshLoop();
  }
}
```

- [ ] **Step 2: Extract the 15 s refresh loop from `boot()` into a reusable function**

Find the block at the bottom of `boot()`:

```javascript
  setInterval(() => {
    if (zonesEl && !zonesEl.classList.contains("hidden")) {
      fetchLayout()
        .then((nextLayout) => {
          if (nextLayout?.zones) {
            const nextSignature = getLayoutSignature(nextLayout.zones);
            if (nextSignature !== layoutSignature) {
              layoutSignature = nextSignature;
              renderZonesLayout(nextLayout.zones);
            }
          }
        })
        .catch((err) => {
          console.error(err);
          setStatus("Connection issue");
        });
    } else {
      fetchContent().catch((err) => {
        console.error(err);
        setStatus("Connection issue");
      });
    }
  }, 15000);
```

Replace with a single call:

```javascript
  startRefreshLoop();
```

Then, immediately above `boot()`, add:

```javascript
let refreshLoopStarted = false;

function startRefreshLoop() {
  if (refreshLoopStarted) return;
  refreshLoopStarted = true;
  setInterval(() => {
    if (zonesEl && !zonesEl.classList.contains("hidden")) {
      fetchLayout()
        .then((nextLayout) => {
          if (nextLayout?.zones) {
            const nextSignature = getLayoutSignature(nextLayout.zones);
            if (nextSignature !== layoutSignature) {
              layoutSignature = nextSignature;
              renderZonesLayout(nextLayout.zones);
            }
          }
        })
        .catch((err) => {
          console.error(err);
          setStatus("Connection issue");
        });
    } else {
      fetchContent().catch((err) => {
        console.error(err);
        setStatus("Connection issue");
      });
    }
  }, 15000);
}
```

- [ ] **Step 3: Drive the poll loop from `startPairingFlow()`**

Replace the `startPairingFlow()` body from Task 3 with:

```javascript
async function startPairingFlow() {
  showPairingView();
  stopPairPoll();
  try {
    const data = await requestPairingCode();
    activePairCode = data.code;
    renderPairingCode(data.code);
    pairingMetaEl.textContent = "Waiting for your phone…";
    schedulePairPoll();
  } catch (err) {
    console.error(err);
    pairingCodeEl.textContent = "—";
    pairingMetaEl.textContent = "Can't reach server. Retrying…";
    setTimeout(startPairingFlow, 5000);
  }
}

function schedulePairPoll() {
  pairPollTimer = setTimeout(runPairPoll, PAIR_POLL_INTERVAL_MS);
}

async function runPairPoll() {
  if (!activePairCode) return;
  try {
    const data = await pollPairingCode(activePairCode);
    if (data.status === "paired" && data.screen_token) {
      await onPaired(data.screen_token);
      return;
    }
    if (data.status === "expired") {
      pairingMetaEl.textContent = "Code expired — getting a new one…";
      await startPairingFlow();
      return;
    }
    schedulePairPoll();
  } catch (err) {
    console.error(err);
    pairingMetaEl.textContent = "Reconnecting…";
    schedulePairPoll();
  }
}
```

- [ ] **Step 4: Rebuild + manual smoke**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build player
```

Test the happy path:
1. Open `https://play.khanshoof.com/` incognito. Pairing view renders with code e.g. `K7M2Q`.
2. In a second browser logged in as admin, create a screen (or pick an existing one) and call:

```bash
# Grab session cookie from /auth/login first, then:
curl -X POST https://api.khanshoof.com/screens/claim \
  -H "Content-Type: application/json" \
  -b "session=<SESSION_COOKIE>" \
  -d '{"code":"K7M2Q","screen_id":<SCREEN_ID>}'
```

Expected: within ~3 s, the player swaps from the pairing view to the content view (or "No content assigned" if the screen is empty). `localStorage.screen_token` is set — reload the player tab, content loads directly without the pairing view.

Test the expiry path:
- Briefly set `PAIR_CODE_TTL_SECONDS=10` in `backend` env, restart backend, open player, wait 11 s without claiming.
- Expected: meta line shows "Code expired — getting a new one…", then a fresh code appears. Reset `PAIR_CODE_TTL_SECONDS` after the test.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add player/player.js
git -C /home/ahmed/signage commit -m "feat(player): poll loop with paired/expired handling"
```

---

## Task 5: Invalid-token recovery

**Files:**
- Modify: `player/player.js`

Today, if an admin deletes the screen or rotates its token, the player keeps hammering `/screens/{stale-token}/content` with 401s forever. Make 401/404 on the authenticated endpoints trigger a return to the pairing view.

- [ ] **Step 1: Add `handleAuthFailure()` helper**

Add above `fetchContent()`:

```javascript
async function handleAuthFailure() {
  console.warn("Screen token rejected — returning to pairing view");
  localStorage.removeItem("screen_token");
  screenToken = null;
  currentSignature = "";
  currentItems = [];
  clearPlayback();
  clearZonePlayback();
  contentEl.innerHTML = "";
  zonesEl.innerHTML = "";
  await startPairingFlow();
}
```

- [ ] **Step 2: Call it from `fetchContent()` on 401/404**

Replace the body of `fetchContent()` with:

```javascript
async function fetchContent() {
  if (!screenToken && !previewToken) return;
  const endpoint = previewToken
    ? `${API_BASE}/preview/${previewToken}/content`
    : `${API_BASE}/screens/${screenToken}/content`;
  const res = await fetch(endpoint);
  if ((res.status === 401 || res.status === 404) && !previewToken) {
    await handleAuthFailure();
    return;
  }
  if (!res.ok) {
    const cached = localStorage.getItem(getCacheKey("content"));
    if (!cached) {
      throw new Error("Failed to load content");
    }
    const data = JSON.parse(cached);
    return renderContentData(data);
  }
  const data = await res.json();
  localStorage.setItem(getCacheKey("content"), JSON.stringify(data));
  return renderContentData(data);
}
```

- [ ] **Step 3: Call it from `fetchLayout()` on 401/404**

Replace the body of `fetchLayout()` with:

```javascript
async function fetchLayout() {
  if (!screenToken && !previewToken) return null;
  const endpoint = previewToken
    ? `${API_BASE}/preview/${previewToken}/layout`
    : `${API_BASE}/screens/${screenToken}/layout`;
  const res = await fetch(endpoint);
  if ((res.status === 401 || res.status === 404) && !previewToken) {
    await handleAuthFailure();
    return null;
  }
  if (!res.ok) {
    const cached = localStorage.getItem(getCacheKey("layout"));
    return cached ? JSON.parse(cached) : null;
  }
  const data = await res.json();
  localStorage.setItem(getCacheKey("layout"), JSON.stringify(data));
  return data;
}
```

- [ ] **Step 4: Manual smoke**

1. Complete a full pair, verify content loads.
2. In the admin, delete the paired screen.
3. Wait up to 15 s (next refresh tick) — expected: the player falls back to the pairing view with a new code.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add player/player.js
git -C /home/ahmed/signage commit -m "feat(player): recover to pairing view on 401/404"
```

---

## Task 6: Full smoke test + merge

- [ ] **Step 1: Backend regression still green**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: `38 passed` (no change — no backend edits in this plan).

- [ ] **Step 2: End-to-end happy path on `play.khanshoof.com`**

Walk through the full user-facing flow in a real browser:

1. Clear `localStorage` on `play.khanshoof.com`. Open the player full-screen on a TV (or desktop browser at full res).
2. Confirm: pastel pairing view, legible code at 5 m viewing distance, QR scannable.
3. From a phone logged in as admin at `app.khanshoof.com`, create a screen and claim the code via an ad-hoc API call or curl (Plan 3 will add the phone-side UI).
4. Confirm: within 3 s the player swaps to content and `localStorage.screen_token` is set.
5. Reload the player tab → content loads directly, no pairing view.
6. Delete that screen in admin → within 15 s the player returns to the pairing view with a new code.

- [ ] **Step 3: Quick visual pass on rendered artifacts**

Spot-check in the browser's dev tools:
- `document.querySelector('#pairing-qr img')` exists and has a non-zero `naturalWidth`.
- No console errors on boot, poll, or pair transition.

- [ ] **Step 4: Merge to `main`**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/player-qr-pairing -m "Merge player QR pairing UI (Plan 2)"
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build player
```

- [ ] **Step 5: Update the roadmap memory**

Mark Plan 2 as done and flip "NEXT" to Plan 3 (admin `/pair?code=…` page). Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`: replace the Plan 2 bullet with "Player QR UI (Plan 2 — DONE 2026-04-24, commit `<merge-sha>`): …" and promote Plan 3 to NEXT.
