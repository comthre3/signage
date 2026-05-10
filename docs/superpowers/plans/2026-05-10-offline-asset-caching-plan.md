# Phase 2.5d — Offline Asset Caching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Player TVs keep playing the cached menu when the internet drops, with a subtle on-screen connection indicator for staff.

**Architecture:** Three loosely-coupled changes to the player container — a build-time-versioned Service Worker with full UI shell precache, a sequential prefetch loop in `player.js` that warms the cache after each playlist refresh, and a tiny green/amber/red status dot driven by WS state and time-since-last-frame. One backend smoke test pins the `/uploads/*` URL contract.

**Tech Stack:** Service Worker · Cache API · vanilla JS · nginx-served static frontend · FastAPI backend (no app changes; one test).

**Spec:** `docs/superpowers/specs/2026-05-10-offline-asset-caching-design.md`
**Branch:** `feature/offline-asset-caching` (already created from main)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(offline):` or `test(offline):`.
2. Player container source is **volume-mounted in the running container** for static assets but **baked into the image** at build time. After changes to `player/sw.js`, `player/Dockerfile`, or `player/docker-entrypoint.sh`, rebuild:
   ```bash
   docker-compose build player && docker-compose up -d --force-recreate player
   ```
3. Backend tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
   Without these env vars only ~110 of the suite pass. Baseline expected: **150 passing on main**.
4. `Khan.t(key, fallback)` is the i18n helper; `data-i18n` attributes auto-translate on locale switch. New translation keys must be added to BOTH `player/i18n/en.json` and `player/i18n/ar.json` and parity-checked via `scripts/check_i18n.py`.
5. JS parse check: `node -e "new Function(require('fs').readFileSync('player/player.js','utf8'))" && echo OK`.
6. Do NOT modify `.env` or rewrite prod URLs — local stack reaches prod domains via tunnel.
7. The player has no JS test runner. Verification for player changes is manual (DevTools → Application → Cache Storage; Network → Offline) plus the backend contract test in Task 1.

---

## Task 1: Backend smoke test — playlist `/uploads/*` contract

**Files:**
- Create: `backend/tests/test_player_offline.py`

**Why first:** Pins the contract that the prefetch loop relies on. If a future refactor ever moves media URLs off `/uploads/`, this test fails loudly in CI before player code silently breaks.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_player_offline.py`:

```python
"""Pin the contract that the player relies on for offline prefetch:
playlist items must expose `url` starting with /uploads/."""
from fastapi.testclient import TestClient


def _create_playlist_with_media(client: TestClient, signed_up_org: dict) -> dict:
    """Helper: create a playlist with one media item, attach to a screen,
    return {token, item_url}."""
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}

    # 1. Upload a media file
    files = {"file": ("test.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")}
    r = client.post("/media/upload", headers=bearer, files=files)
    assert r.status_code in (200, 201), r.text
    media = r.json()

    # 2. Create a playlist
    r = client.post(
        "/playlists",
        headers=bearer,
        json={"name": "Offline test playlist"},
    )
    assert r.status_code in (200, 201), r.text
    playlist = r.json()

    # 3. Add the media item to the playlist
    r = client.post(
        f"/playlists/{playlist['id']}/items",
        headers=bearer,
        json={"media_id": media["id"]},
    )
    assert r.status_code in (200, 201), r.text

    # 4. Create a site, then a screen attached to the playlist
    r = client.post("/sites", headers=bearer, json={"name": "Site A"})
    assert r.status_code in (200, 201), r.text
    site = r.json()
    r = client.post(
        "/screens",
        headers=bearer,
        json={
            "name": "Screen A",
            "site_id": site["id"],
            "playlist_id": playlist["id"],
        },
    )
    assert r.status_code in (200, 201), r.text
    screen = r.json()

    return {"token": screen["token"], "media_filename": media["filename"]}


def test_playlist_response_items_have_uploads_url(client, signed_up_org):
    info = _create_playlist_with_media(client, signed_up_org)
    r = client.get(f"/screens/{info['token']}/content")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("items") or []
    assert len(items) >= 1, "expected at least one playlist item"
    for item in items:
        assert "url" in item, f"item missing 'url' field: {item}"
        assert item["url"].startswith("/uploads/"), \
            f"expected /uploads/ prefix, got {item['url']}"
```

- [ ] **Step 2: Run the test to verify it passes** (the contract should already hold against current code)

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_player_offline.py
```

If it fails, the test is a discovery — read the failure carefully. The test EXPECTS the current code to satisfy the contract; failure means either the test setup (helpers, endpoint shapes, returned-field names) is out of sync with the actual API, OR the contract is genuinely violated. In the first case, adapt the helper. In the second case, escalate to the human — that's a real product issue.

- [ ] **Step 3: Run full backend suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `151 passed` (150 baseline + 1 new contract test).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_player_offline.py
git commit -m "$(cat <<'EOF'
test(offline): pin /uploads/ URL contract for player prefetch

The player's offline prefetch matches by url.pathname.startsWith("/uploads/").
This contract test fails CI loudly if the playlist response shape ever
moves media URLs elsewhere.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Service Worker version-stamp infrastructure

**Files:**
- Modify: `player/Dockerfile`
- Modify: `player/docker-entrypoint.sh`
- Modify: `docker-compose.yml`

**Goal:** every container build can stamp a unique version into `sw.js`. Local default is `dev`; CI/deploy supplies a real value (e.g., git short SHA). This task adds the plumbing without changing `sw.js` content yet.

- [ ] **Step 1: Update `player/Dockerfile`**

Current contents:

```dockerfile
FROM nginx:1.27-alpine

COPY . /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
```

Insert two new lines immediately after `FROM nginx:1.27-alpine`:

```dockerfile
ARG PLAYER_VERSION=dev
ENV PLAYER_VERSION=${PLAYER_VERSION}
```

Final file:

```dockerfile
FROM nginx:1.27-alpine

ARG PLAYER_VERSION=dev
ENV PLAYER_VERSION=${PLAYER_VERSION}

COPY . /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
```

- [ ] **Step 2: Update `player/docker-entrypoint.sh`**

Current contents:

```sh
#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

exec nginx -g 'daemon off;'
```

Insert one new line for the `sed` substitution between the `set -e` line and the existing `cat > ... config.js` block:

```sh
#!/bin/sh
set -e

sed -i "s/__PLAYER_VERSION__/${PLAYER_VERSION:-dev}/g" /usr/share/nginx/html/sw.js

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

exec nginx -g 'daemon off;'
```

(The `sed` runs even though `sw.js` doesn't yet contain `__PLAYER_VERSION__` — `sed` is a no-op on lines without the pattern, so this is safe to commit before Task 3 lands the placeholder.)

- [ ] **Step 3: Update `docker-compose.yml`**

Find the `player:` service block. Locate its `build:` directive. If it currently looks like:

```yaml
  player:
    build:
      context: ./player
```

Change to:

```yaml
  player:
    build:
      context: ./player
      args:
        PLAYER_VERSION: ${PLAYER_VERSION:-dev}
```

If the existing block is on a single line (`build: ./player`), expand to the multi-line form above first.

- [ ] **Step 4: Rebuild + recreate the player container**

```bash
docker-compose build player && docker-compose up -d --force-recreate player
sleep 4
docker-compose ps | grep player
```
Expected: `Up (healthy)`.

- [ ] **Step 5: Verify the substitution can run**

```bash
docker-compose exec -T player sh -c "cat /usr/share/nginx/html/sw.js | head -5"
```
Expected: shows the existing `sw.js` first 5 lines unchanged. (The `sed` is a no-op until Task 3 adds the placeholder.)

```bash
docker-compose exec -T player sh -c 'echo "PLAYER_VERSION=$PLAYER_VERSION"'
```
Expected: `PLAYER_VERSION=dev` (or whatever was passed).

- [ ] **Step 6: Commit**

```bash
git add player/Dockerfile player/docker-entrypoint.sh docker-compose.yml
git commit -m "$(cat <<'EOF'
feat(offline): build-time PLAYER_VERSION stamp infrastructure

Adds ARG PLAYER_VERSION (default 'dev') and an entrypoint sed pass
that substitutes __PLAYER_VERSION__ in sw.js at container start.
docker-compose passes the build arg through.

Service Worker content unchanged in this commit — just the plumbing.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Service Worker rewrite

**Files:**
- Modify: `player/sw.js`

**Goal:** Replace hardcoded `signage-player-v1` with the build-stamped name. Expand the precache list from 5 entries to 15 (full UI shell). Fetch handler logic stays the same.

- [ ] **Step 1: Replace `player/sw.js` content**

Current first 8 lines:

```javascript
const CACHE_NAME = "signage-player-v1";
const ASSETS = ["/", "/index.html", "/player.js", "/styles.css", "/config.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});
```

Replace lines 1–2 only:

```javascript
const PLAYER_VERSION = "__PLAYER_VERSION__";
const CACHE_NAME = `signage-player-${PLAYER_VERSION}`;
const ASSETS = [
  "/",
  "/index.html",
  "/player.js",
  "/styles.css",
  "/config.js",
  "/i18n.js",
  "/i18n/en.json",
  "/i18n/ar.json",
  "/vendor/qrcode.js",
  "/assets/faces/v1_smile.png",
  "/assets/faces/v1_wink.png",
  "/assets/faces/v1_kawaii.png",
  "/assets/faces/v1_heart.png",
  "/assets/faces/v1_star.png",
  "/assets/faces/v1_big.png",
];
```

(Everything else in `sw.js` — install/activate/fetch handlers — stays as-is. The fetch handler's existing logic for `/uploads/*` cross-origin and same-origin stale-while-revalidate is correct.)

- [ ] **Step 2: Rebuild + recreate player**

```bash
docker-compose build player && docker-compose up -d --force-recreate player
sleep 4
```

- [ ] **Step 3: Verify the version stamp landed**

```bash
docker-compose exec -T player sh -c "head -3 /usr/share/nginx/html/sw.js"
```
Expected: first line is `const PLAYER_VERSION = "dev";` (substituted from the placeholder). NOT the literal `__PLAYER_VERSION__`.

- [ ] **Step 4: Verify all 15 precache entries respond 200**

```bash
docker-compose exec -T player sh -c '
for path in / /index.html /player.js /styles.css /config.js \
            /i18n.js /i18n/en.json /i18n/ar.json /vendor/qrcode.js \
            /assets/faces/v1_smile.png /assets/faces/v1_wink.png \
            /assets/faces/v1_kawaii.png /assets/faces/v1_heart.png \
            /assets/faces/v1_star.png /assets/faces/v1_big.png; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost$path")
  echo "$code $path"
done'
```
Expected: every line shows `200`. Any 404 means the file path in `ASSETS` doesn't match what nginx serves — fix the path in `sw.js` before continuing.

- [ ] **Step 5: Verify SW registers in a real browser**

Open the player URL in a browser. DevTools → Application → Service Workers. The registered SW should show source `sw.js` and an active version. DevTools → Application → Cache Storage. Refresh the page. A cache named `signage-player-dev` should appear with all 15 entries listed.

(Note: the user can do this manually in Step 7 of Task 6's smoke checklist; here, it's a self-check the implementer should attempt before committing. If a real browser isn't available, document `DONE_WITH_CONCERNS — manual SW registration not verified, deferred to Task 6 smoke`.)

- [ ] **Step 6: Commit**

```bash
git add player/sw.js
git commit -m "$(cat <<'EOF'
feat(offline): version-stamped SW + full UI shell precache

CACHE_NAME now derives from build-time PLAYER_VERSION; old caches
auto-evict on activate. Precache expands from 5 to 15 entries:
adds i18n.js, both i18n bundles, qrcode vendor lib, and 6 mascot
images. First-offline-boot now renders complete UI without network.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Prefetch on playlist change

**Files:**
- Modify: `player/player.js` (around lines 346–402)

**Goal:** After every successful `fetchContent()` and `fetchLayout()`, walk the items list and `fetch()` each `/uploads/*` URL once to populate the SW cache. Sequential, fire-and-forget, errors swallowed.

- [ ] **Step 1: Add the prefetch helper to `player/player.js`**

Find the `fetchContent` function (around line 346). Immediately ABOVE it (i.e., before the `async function fetchContent()` line), insert:

```javascript
function prefetchPlaylistMedia(items) {
  // Sequential, low-priority. Fire-and-forget — caller does NOT await.
  // Each fetch is no-cors so the cross-origin /uploads/ response is opaque
  // (good enough for the SW to cache and good enough for <img>/<video> to render).
  return (async () => {
    for (const item of items || []) {
      const url = item && item.url;
      if (!url) continue;
      try {
        await fetch(url, { mode: "no-cors", cache: "no-store" });
      } catch (_) {
        /* offline or 404 — swallow */
      }
    }
  })();
}
```

- [ ] **Step 2: Wire prefetch into `fetchContent`**

In the existing `fetchContent` body, find this section (around line 364):

```javascript
  const data = await res.json();
  localStorage.setItem(getCacheKey("content"), JSON.stringify(data));
  return renderContentData(data);
```

Insert ONE new line between `localStorage.setItem(...)` and `return renderContentData(data);`:

```javascript
  const data = await res.json();
  localStorage.setItem(getCacheKey("content"), JSON.stringify(data));
  prefetchPlaylistMedia(data.items);   // fire and forget
  return renderContentData(data);
```

- [ ] **Step 3: Wire prefetch into `fetchLayout`**

In the existing `fetchLayout` body (around line 400), find:

```javascript
  const data = await res.json();
  localStorage.setItem(getCacheKey("layout"), JSON.stringify(data));
  return data;
```

Insert ONE new block:

```javascript
  const data = await res.json();
  localStorage.setItem(getCacheKey("layout"), JSON.stringify(data));
  const zoneItems = (data && data.zones || []).flatMap(z => z.items || []);
  prefetchPlaylistMedia(zoneItems);   // fire and forget
  return data;
```

- [ ] **Step 4: Parse JS to confirm syntax**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/player.js','utf8'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Reload the player container** (no rebuild needed; player.js is served directly)

```bash
docker-compose ps | grep player
```
Confirm `Up (healthy)`. The browser will pick up the new player.js on next page reload.

- [ ] **Step 6: Verify prefetch in a browser**

Open the player URL. DevTools → Network. Pair the screen with a playlist that has at least 2 media items. After the playlist response arrives, you should see additional `fetch` requests to `/uploads/<filename>` — these are the prefetches. They show as `(no-cors)` mode and may appear with `(opaque)` in the response column. DevTools → Application → Cache Storage → `signage-player-dev`. Each prefetched URL is listed.

(If a real browser isn't available, mark `DONE_WITH_CONCERNS — manual prefetch verification deferred to Task 6 smoke`.)

- [ ] **Step 7: Commit**

```bash
git add player/player.js
git commit -m "$(cat <<'EOF'
feat(offline): prefetch /uploads/ URLs after playlist change

Each successful fetchContent() and fetchLayout() walks the items
list and fires sequential no-cors fetches that populate the SW
cache. Errors are swallowed so a missing media file or transient
network blip doesn't break playback.

WS playlist_change handler benefits automatically because it calls
fetchContent().

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Connection indicator (markup, CSS, JS, wiring)

**Files:**
- Modify: `player/index.html`
- Modify: `player/styles.css`
- Modify: `player/player.js`

**Goal:** A small fixed-position colored dot. Green when WS is open and a frame arrived recently (<30s); amber when stale (30s–5min) or reconnecting; red when offline >30s. Hidden on the pairing screen, shown on the playback view.

- [ ] **Step 1: Add the markup in `player/index.html`**

Find a logical anchor — the existing `#status` element (a status-message overlay used during reconnect) is a good neighbor. Add the new dot immediately after the `<body>` open tag, OR adjacent to `#status`. Insert:

```html
<div id="connection-indicator" class="conn-dot conn-green hidden"
     aria-hidden="true" title="Connection status"></div>
```

(Position in the DOM doesn't matter visually because it's `position: fixed`. Pick a spot that's close to `#status` for code locality.)

- [ ] **Step 2: Add CSS in `player/styles.css`**

Append to the file:

```css
/* Phase 2.5d — connection indicator */
.conn-dot {
  position: fixed;
  inset-block-start: 12px;
  inset-inline-end: 12px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  z-index: 5;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.25);
  transition: background 200ms ease;
}
.conn-green { background: #5fc26b; }
.conn-amber { background: #d8a23a; }
.conn-red   { background: #c84a4a; }
.conn-dot.hidden { display: none; }
```

- [ ] **Step 3: Add the `ConnectionStatus` IIFE to `player/player.js`**

Append to the END of `player/player.js` (after all existing code):

```javascript
// ── Connection indicator (Phase 2.5d) ─────────────────────────────────
const ConnectionStatus = (() => {
  let lastFrameAt = 0;     // Date.now() of last successful WS frame OR API success
  let wsState = "closed";  // "open" | "connecting" | "closed"
  let timer = null;
  const dot = () => document.getElementById("connection-indicator");

  function markFrame() { lastFrameAt = Date.now(); }
  function setWs(s)    { wsState = s; }

  function compute() {
    const ageMs = Date.now() - lastFrameAt;
    if (wsState === "open" && ageMs < 30_000)     return "green";
    if (wsState === "open" && ageMs < 5 * 60_000) return "amber";
    if (wsState === "connecting")                 return "amber";
    if (ageMs > 30_000)                           return "red";
    return "amber";
  }

  function render() {
    const el = dot(); if (!el) return;
    el.classList.remove("conn-green", "conn-amber", "conn-red");
    el.classList.add(`conn-${compute()}`);
  }

  function show() { dot()?.classList.remove("hidden"); render(); }
  function hide() { dot()?.classList.add("hidden"); }

  function start() {
    if (timer) return;
    timer = setInterval(render, 5_000);
  }

  return { show, hide, markFrame, setWs, start };
})();
```

- [ ] **Step 4: Wire `ConnectionStatus` to the WebSocket handlers in `player/player.js`**

Find the existing WS event handlers (around lines 556–572). Modify each as follows.

**Open handler** (around line 556):

Current:
```javascript
  wallSocket.addEventListener("open", () => {
```
After this line, add at the start of the handler body:
```javascript
    ConnectionStatus.setWs("open");
    ConnectionStatus.markFrame();
```

**Message handler** (around line 559):

Current:
```javascript
  wallSocket.addEventListener("message", (ev) => {
```
After this line, add at the start of the handler body:
```javascript
    ConnectionStatus.markFrame();
```

**Close handler** (around line 564):

Current:
```javascript
  wallSocket.addEventListener("close", () => {
    setStatus(Khan.t("wall.reconnecting", "Reconnecting to wall…"));
```
After the `setStatus(...)` line, add:
```javascript
    ConnectionStatus.setWs("closed");
```

(The reconnect logic in this same handler will eventually re-fire the open handler, which sets `wsState` back to `"open"`. We don't need a separate `connecting` state transition unless reconnect-with-backoff is visible — for now, the close → open round trip is enough.)

- [ ] **Step 5: Wire `ConnectionStatus` to fetch successes**

In `fetchContent` (around line 364), immediately after `const data = await res.json();`:

```javascript
  ConnectionStatus.markFrame();
```

In `fetchLayout` (around line 400), immediately after `const data = await res.json();`:

```javascript
  ConnectionStatus.markFrame();
```

- [ ] **Step 6: Wire `ConnectionStatus` to view transitions**

Find `function showPairingView()` (around line 75). Add at the start of the body (after the `function showPairingView() {` line):

```javascript
  ConnectionStatus.hide();
```

Find `function hidePairingView()` (around line 82). Add at the start of the body:

```javascript
  ConnectionStatus.show();
  ConnectionStatus.start();
```

Also find any places where the view transitions to the cell viewport / wall canvas. Look for sites where `zonesEl.classList.remove("hidden")` is called (around line 293). After such transitions, add:

```javascript
  ConnectionStatus.show();
  ConnectionStatus.start();
```

(Doing it inside `hidePairingView` covers most paths; the explicit add inside the wall/zones transition path covers the case where the player switches view modes mid-session without going through `hidePairingView`.)

- [ ] **Step 7: Parse JS**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/player.js','utf8'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 8: Reload the player and verify in browser**

Open the player URL. After pairing, the dot appears in the top-right corner (LTR) / top-left (RTL). DevTools → Network → "Offline." Within 30 seconds the dot turns red. Restore network. Within 5 seconds (one tick of the 5s timer) it turns amber, then green on next WS frame.

- [ ] **Step 9: Commit**

```bash
git add player/index.html player/styles.css player/player.js
git commit -m "$(cat <<'EOF'
feat(offline): on-screen connection-status dot

Small fixed-position dot, green/amber/red per WS state and
time-since-last-frame. Hidden on pairing screen; shown during
playback. Re-renders every 5s so age transitions surface promptly.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Smoke checklist + push + PR

**Files:** none modified.

- [ ] **Step 1: Final backend test run**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `151 passed`.

- [ ] **Step 2: i18n parity check (no new keys, but verify no accidental drift)**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: OK.

- [ ] **Step 3: JS parse all four files**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo "frontend OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/player.js','utf8'))" && echo "player OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/landing/app.js','utf8'))" && echo "landing OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/i18n.js','utf8'))" && echo "i18n OK"
```
Each must print its `OK` line.

- [ ] **Step 4: Rebuild + recreate all four user-facing containers**

```bash
docker-compose build player frontend landing
docker-compose up -d --force-recreate player frontend landing
sleep 5
docker-compose ps
```
Expected: all five services `(healthy)`.

- [ ] **Step 5: Manual browser smoke checklist**

Open the player URL in a browser tab. Open DevTools.

| Check | Pass criteria |
|---|---|
| SW registered | Application → Service Workers shows `sw.js` active. |
| Cache populated | Application → Cache Storage shows `signage-player-dev` with all 15 shell entries. |
| Pair the player | Pair via admin UI. Player transitions to playback view. |
| Prefetch fires | Network tab shows `fetch` requests to each playlist `/uploads/*` URL after the content response, in `no-cors` mode. |
| Cache holds media | Cache Storage now lists each prefetched URL alongside the shell. |
| Offline playback | Network → "Offline." Reload page. Player still shows the menu, plays through items. |
| Indicator dot — online | Top-right corner shows the small green dot. |
| Indicator dot — offline | Within 30s of going offline, dot transitions to red. Within 5s of going back online, dot transitions back to green via amber. |
| Pairing screen no dot | Force unpair (e.g. clear `screen_token` from localStorage and reload). Dot is hidden during pairing. |
| Locale switch offline | While offline, toggle EN ↔ AR via the player's lang button. UI strings update because i18n bundles are precached. |
| Cache evicts on version bump | `PLAYER_VERSION=test1 docker-compose build player && docker-compose up -d --force-recreate player`. Reload. The old `signage-player-dev` cache is evicted on activate; new `signage-player-test1` cache populates. |

If any check fails, fix it (returning to the relevant task for an additional commit) before pushing.

- [ ] **Step 6: Push branch**

```bash
git push -u origin feature/offline-asset-caching
```

- [ ] **Step 7: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(offline): Phase 2.5d — player offline asset caching" \
  --body "$(cat <<'EOF'
## Summary
- Service Worker rewrite: build-time `PLAYER_VERSION` stamp replaces hardcoded `v1`. Old caches auto-evict on activate. Precache expands from 5 to 15 entries (full UI shell: code, i18n, vendor, mascots).
- Prefetch on playlist change: `fetchContent()` and `fetchLayout()` now walk the items list and fire sequential no-cors fetches that populate the SW cache. Fire-and-forget; errors swallowed; doesn't block playback.
- Connection-status dot: small fixed-position green/amber/red indicator on the playback view, driven by WS state and time-since-last-frame. Hidden on pairing screen.
- Backend contract test pinning the `/uploads/*` URL shape so the prefetch contract can't quietly break.

## Spec
`docs/superpowers/specs/2026-05-10-offline-asset-caching-design.md`

## Plan
`docs/superpowers/plans/2026-05-10-offline-asset-caching-plan.md`

## Test Plan
- [x] Backend: 151 passed (was 150 baseline; +1 contract test)
- [x] All four JS files parse
- [x] Containers rebuilt and healthy
- [ ] Browser smoke: SW registers, all 15 shell entries cached
- [ ] Browser smoke: prefetch fires after playlist load, media cached
- [ ] Browser smoke: offline reload still plays menu
- [ ] Browser smoke: indicator dot transitions (green ↔ amber ↔ red) match WS state
- [ ] Browser smoke: locale switch works while offline
- [ ] Browser smoke: PLAYER_VERSION bump evicts old cache cleanly

## Non-goals (queued)
- Content-hash cache keying
- LRU eviction with quota cap
- Admin-side "screen offline > N min" alert

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Save memory + update index**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_offline_caching.md`:

```markdown
---
name: Offline asset caching (Phase 2.5d) — branch
description: Versioned SW + prefetch on playlist_change + green/amber/red status dot. PR pending.
type: project
---

**Status (2026-05-10):** PR #<TBD> opened against main. Awaiting browser smoke + merge.

**What landed:**
- `player/sw.js` rewritten with build-time `PLAYER_VERSION` stamp; precache expands to 15 entries (full UI shell: code, i18n, vendor lib, mascot images).
- `player/Dockerfile` + `player/docker-entrypoint.sh` + `docker-compose.yml` plumb `PLAYER_VERSION` build arg through; entrypoint `sed`-substitutes the placeholder at container start.
- `prefetchPlaylistMedia()` helper in `player/player.js`; called fire-and-forget after every successful `fetchContent()` and `fetchLayout()`. Sequential `fetch(url, { mode: "no-cors", cache: "no-store" })` per item.
- `ConnectionStatus` IIFE in `player/player.js` + indicator markup + CSS. Dot is shown on playback view (`hidePairingView` + zones-transition); hidden on pairing screen. Re-renders every 5s so green→amber→red transitions surface promptly.
- `backend/tests/test_player_offline.py` pins the `/uploads/*` URL contract for the prefetch loop.

**Plan:** `docs/superpowers/plans/2026-05-10-offline-asset-caching-plan.md` — 6 tasks.
**Spec:** `docs/superpowers/specs/2026-05-10-offline-asset-caching-design.md`.

**Why fire-and-forget prefetch:** sequential bandwidth doesn't compete with playback decode; failure to prefetch a single file shouldn't break the playlist. SW will still serve the URL fresh from network on render.

**Why no-cors fetch mode:** `api.khanshoof.com` doesn't expose CORS headers on `/uploads/*` (it's a `StaticFiles` mount). Opaque responses are fine for SW caching and for `<img>`/`<video>` rendering.

**Why a 5-second indicator tick:** dot color depends on `Date.now() - lastFrameAt`; without periodic re-render the dot would stay green forever after the last frame.

**Three queued initiatives — Arabic [DONE], Security [PR #5], Offline [this PR], Payment gateway.** Next up after this lands: **payment gateway** (existing Niupay/KNET billing spec at commits `1f2ead1`, `318e970`).

**Out of scope (queued for later phase):**
- Content-hash cache keying (admin-replaced media currently relies on backend producing new filenames per upload)
- LRU eviction with storage cap
- Admin-side "screen offline > N min" alert
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` with a one-line index entry pointing at the new file. Remove the "Future feature flagged" placeholder line that referenced offline caching as not-yet-specced (it now has a real spec/plan/branch).

- [ ] **Step 9: Final verification**

```bash
git status -sb
```
Expected: working tree clean except for untracked `khanshoof_assets/` (which is unrelated).

```bash
~/.local/bin/gh pr view --json number,url,state | head
```
Expected: PR is open, returns its number + URL.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §3 Gap 1 (prefetch) | Task 4 |
| §3 Gap 2 (cache version) | Tasks 2 + 3 |
| §3 Gap 3 (precache list) | Task 3 |
| §3 Gap 4 (indicator) | Task 5 |
| §6 SW rewrite | Tasks 2 + 3 |
| §7 Prefetch hook | Task 4 |
| §8 Connection indicator | Task 5 |
| §9 Backend smoke test | Task 1 |
| §9 Manual QA checklist | Task 6 step 5 |
| §10 File layout | All file paths match |
| §11 Failure modes | Each documented in spec; behavior preserved by tasks |

No placeholders. Method names consistent across tasks (`prefetchPlaylistMedia`, `ConnectionStatus.markFrame`, `setWs`, `show`/`hide`/`start`/`render`/`compute`). Task ordering: 1 (smoke test) → 2 (infra) → 3 (SW rewrite, depends on 2) → 4 (prefetch, doesn't depend on SW changes but landing first means manual smoke can verify both at once) → 5 (indicator, independent) → 6 (regression + PR).
