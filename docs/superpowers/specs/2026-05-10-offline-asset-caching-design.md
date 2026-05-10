# Phase 2.5d — Offline Asset Caching for Player TVs — Design

**Date:** 2026-05-10
**Branch:** `feature/offline-asset-caching` (fresh, branched from `main`)
**Predecessor:** Phase 2.5c (security hardening) is on PR #5, not yet merged. This work is independent — `feature/offline-asset-caching` branches from `main`, not from the security branch.

---

## 1. Goal

Player TVs continue showing the cached playlist when the internet drops, with a subtle on-screen signal so staff can see the player has gone offline.

## 2. Existing State (do not regress)

The player already has partial offline support:

- A Service Worker at `player/sw.js` (51 lines) with cache name `signage-player-v1` (hardcoded).
- Precaches 5 shell files: `/`, `/index.html`, `/player.js`, `/styles.css`, `/config.js`.
- Stale-while-revalidate caching for any `/uploads/*` request and any same-origin request. Cross-origin `/uploads/*` from `api.khanshoof.com` happens to work because the SW matches by `url.pathname.startsWith("/uploads/")`, regardless of origin; the fetch caches as an opaque response.
- The player code (`player/player.js`) keeps `localStorage` fallback copies of the playlist response (`fetchContent`) and the layout response (`fetchLayout`). On a fetch failure the cached JSON is used.

## 3. Gaps Addressed in This PR

1. **No prefetch on playlist change.** New media items only land in the SW cache when the player tries to render them. If the playlist changes while online and the network drops before render, those items are missing on next play.
2. **Stale shell cache version.** Cache name is hardcoded `v1`; deploys silently keep the old shell on TVs.
3. **Incomplete shell precache.** Missing i18n bundles (`i18n.js`, `i18n/en.json`, `i18n/ar.json`), the QR vendor lib (`vendor/qrcode.js`), and the six mascot images (`assets/faces/v1_*.png`). Player falling back to those at first offline boot fails.
4. **No offline indicator UX.** Staff can't tell at a glance whether the screen is online or running off cache.

## 4. Gaps Deferred (out of scope)

- Content-hash cache keying. The current URL-as-key model is fine because the backend uses unique filenames per upload (UUID-based). Replacing a media file always produces a new URL.
- LRU cache eviction with a storage cap. Player TVs typically have plenty of storage; eviction is YAGNI until a real-world report says otherwise.
- Admin-side "screen offline > N min" alert. The connection-dot UX targets staff who can see the screen.
- Forced SW unregister on uninstall paths.

## 5. Architecture

Three loosely-coupled changes, shipping as one PR:

1. **Service Worker rewrite** — version stamped at build time, full shell precache.
2. **Prefetch on playlist change** — `player.js` walks playlist items after each `fetchContent` and `fetchLayout` to populate the SW cache.
3. **Connection indicator** — a small fixed-position dot whose color reflects WebSocket state and time-since-last-frame.

No backend code changes. One backend smoke test pins the playlist response shape so the prefetch contract can't quietly break.

## 6. Component A — Service Worker Rewrite

### 6.1 Build-time version stamp

`player/sw.js` first line becomes:

```javascript
const PLAYER_VERSION = "__PLAYER_VERSION__";
const CACHE_NAME = `signage-player-${PLAYER_VERSION}`;
```

### 6.2 Dockerfile arg + entrypoint substitution

`player/Dockerfile` adds:

```dockerfile
ARG PLAYER_VERSION=dev
ENV PLAYER_VERSION=${PLAYER_VERSION}
```

`player/docker-entrypoint.sh` (prepend, before existing nginx start):

```sh
#!/bin/sh
set -e
sed -i "s/__PLAYER_VERSION__/${PLAYER_VERSION:-dev}/g" /usr/share/nginx/html/sw.js
exec "$@"
```

`docker-compose.yml`'s `player` service:

```yaml
  player:
    build:
      context: ./player
      args:
        PLAYER_VERSION: ${PLAYER_VERSION:-dev}
```

CI / deploy invokes `PLAYER_VERSION=$(git rev-parse --short HEAD) docker-compose build player`.

### 6.3 Precache list

```javascript
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

(15 entries. The mascot list is taken from a directory listing of `player/assets/faces/`.)

### 6.4 Activate handler — unchanged

The existing handler iterates `caches.keys()` and deletes any whose name doesn't equal the current `CACHE_NAME`. With the version stamp, deploys automatically evict old caches.

### 6.5 Fetch handler — unchanged behavior, tightened comments

Stale-while-revalidate logic stays. Two branches:

1. **`url.pathname.startsWith("/uploads/")`** — applies to cross-origin media files from `api.khanshoof.com`.
2. **Same-origin** — applies to shell + i18n + vendor + mascot.

If `cache.put` rejects (quota), the rejection is swallowed; the response still returns to the page. We rely on browser auto-eviction at the OS level for now.

## 7. Component B — Prefetch on Playlist Change

### 7.1 New helper in `player/player.js`

```javascript
function prefetchPlaylistMedia(items) {
  // Sequential, low-priority. Fire-and-forget — caller does NOT await.
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

`mode: "no-cors"` is required because `api.khanshoof.com` does not send CORS headers on `/uploads/*` static-files mount. The prefetch produces opaque responses, which the SW caches without complaint. Renders later fetch the same URL and get the cached opaque response.

### 7.2 Wiring

In `fetchContent()` (currently around line 346): immediately after the existing `localStorage.setItem(getCacheKey("content"), JSON.stringify(data));`, add:

```javascript
prefetchPlaylistMedia(data.items);   // fire and forget
```

In `fetchLayout()` (currently around line 386): after `localStorage.setItem(getCacheKey("layout"), JSON.stringify(data));`, flatten the zones' items and prefetch:

```javascript
const zoneItems = (data?.zones || []).flatMap(z => z.items || []);
prefetchPlaylistMedia(zoneItems);
```

The WS `playlist_change` handler (around line 600) already calls `fetchContent`. Since the prefetch is now part of that path, no separate WS-side wiring is needed.

### 7.3 Spanned-wall canvas frames

Walls Phase 2 produces a `wall_canvas_playlist` whose items also live under `/uploads/*` (rasterized PNG sequences). The same prefetch loop catches them — no special-case code.

## 8. Component C — Connection Indicator

### 8.1 Markup (`player/index.html`)

Add inside `<body>` near the existing `#status` element:

```html
<div id="connection-indicator" class="conn-dot conn-green hidden"
     aria-hidden="true" title="Connection status"></div>
```

`aria-hidden="true"` because it's a glanceable staff signal, not content for assistive tech.

### 8.2 Styles (`player/styles.css`)

```css
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

### 8.3 State machine (`player/player.js`)

```javascript
const ConnectionStatus = (() => {
  let lastFrameAt = 0;
  let wsState = "closed";   // "open" | "connecting" | "closed"
  let timer = null;
  const dot = () => document.getElementById("connection-indicator");

  function markFrame()  { lastFrameAt = Date.now(); }
  function setWs(s)     { wsState = s; }

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

### 8.4 Wiring in `player/player.js`

| Touch point (existing line ≈) | Call |
|---|---|
| WS `onopen` | `ConnectionStatus.setWs("open"); ConnectionStatus.markFrame();` |
| WS `onclose` | `ConnectionStatus.setWs("closed");` |
| WS `onmessage` (any frame) | `ConnectionStatus.markFrame();` |
| WS reconnect attempt fires | `ConnectionStatus.setWs("connecting");` |
| Successful `fetchContent`/`fetchLayout` resolution | `ConnectionStatus.markFrame();` |
| Player transitions to playback view (cell viewport / menu) | `ConnectionStatus.show(); ConnectionStatus.start();` |
| Player transitions to pairing screen | `ConnectionStatus.hide();` |

### 8.5 Why a 5-second tick

The dot's color depends on `Date.now() - lastFrameAt`. Without a periodic re-render the dot would stay green forever after the last frame. 5 seconds is small enough for prompt green→amber→red transitions and large enough to be free CPU-wise.

## 9. Testing

### 9.1 Automated — backend smoke test

`backend/tests/test_player_offline.py`:

```python
def test_player_playlist_response_items_have_uploads_url(client, signed_up_org):
    """Pin the contract: playlist items must expose `url` starting with /uploads/.
    The player's SW prefetch matches on this prefix."""
    # Build a media row + playlist + screen via existing test helpers, then
    # GET /screens/{token}/playlist (or whichever endpoint the player calls).
    # Assert each item has `item["url"].startswith("/uploads/")`.
```

Exact endpoint + helper choices are determined by reading the existing test files (`test_playlists.py`, `test_smoke.py`) during implementation. The point is to fail loudly if a future refactor moves media URLs off `/uploads/`.

### 9.2 Manual QA checklist (in PR body)

- Player loads, DevTools → Application → Cache Storage shows `signage-player-<sha>` with all 15 shell entries.
- Pair the player; load a playlist with two media items. Wait for `fetchContent` to resolve. The two `/uploads/*` URLs appear in the cache.
- DevTools → Network → "Offline." Reload. Player still shows the menu, plays through items.
- Connection dot: green online, amber within 30s of disconnect, red after 30s.
- Switch language while online. Switch again while offline. The i18n bundles are precached so locale switch works without network.
- Bump `PLAYER_VERSION` (e.g. `PLAYER_VERSION=test1 docker-compose build player && docker-compose up -d --force-recreate player`). Reload. Old `signage-player-<old>` cache is evicted on activate; new cache is populated.
- Pairing screen does not show the connection dot (`.hidden` class active).

## 10. File Layout

| File | Change |
|---|---|
| `player/sw.js` | Rewritten — version stamp + full shell precache, fetch handler tightened-comment-only |
| `player/Dockerfile` | Add `ARG PLAYER_VERSION=dev` and matching `ENV` |
| `player/docker-entrypoint.sh` | NEW — substitutes `__PLAYER_VERSION__` in `sw.js` at container start |
| `docker-compose.yml` | Pass `build.args.PLAYER_VERSION: ${PLAYER_VERSION:-dev}` to player service |
| `player/player.js` | Add `prefetchPlaylistMedia()` and `ConnectionStatus` IIFE; ~8 wiring touch points |
| `player/index.html` | Add `<div id="connection-indicator">` markup |
| `player/styles.css` | Add `.conn-dot`, `.conn-green`, `.conn-amber`, `.conn-red`, `.conn-dot.hidden` rules |
| `backend/tests/test_player_offline.py` | NEW — single contract test for `/uploads/*` URL shape |

## 11. Failure Modes

| Failure | Behavior |
|---|---|
| Cache quota exceeded | `cache.put` rejects; SW swallows; response still served. Future prefetches may keep failing until OS-level browser eviction kicks in. |
| `mode: "no-cors"` blocked at upstream (e.g., CORS tightening) | Prefetch silently fails for that URL; render-time fetch will still work online. No regression in online behavior. |
| SW fails to register (HTTPS unavailable, dev mode) | Player runs without offline shell; existing `localStorage` fallback for playlist/layout JSON still applies. |
| `PLAYER_VERSION` arg unset | Dockerfile defaults to `dev`. Cache name `signage-player-dev`. Acceptable in local dev; CI/deploy must set the real value. |
| Playlist response shape changes server-side | Backend smoke test in §9.1 fails CI. |

## 12. Migration / Rollout

1. Deploy with `PLAYER_VERSION=$(git rev-parse --short HEAD)`.
2. On first load, each player TV's old `signage-player-v1` cache is evicted on activate; new versioned cache populates.
3. No backend migration. No data migration.

## 13. Out of Scope (queued)

- Content-hash cache keying (gap 2 from earlier triage)
- LRU eviction with quota cap (gap 3)
- Admin-side "screen offline" alerts
- Push-style cache prewarm from backend (vs. pull-on-prefetch)

## 14. Next Initiative After This One

Per user's stated sequence: Arabic [DONE] → Security [PR #5] → **Offline caching [this PR]** → Payment gateway. After this lands, the queued payment-gateway work uses the existing Niupay/KNET billing spec at commits `1f2ead1`, `318e970`.
