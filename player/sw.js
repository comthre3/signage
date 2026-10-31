/* Khanshoof player service worker.

   Shell: precached per version and served cache-first, so a TV boots with no
   network at all. The version is stamped at container start from
   PLAYER_VERSION plus a hash of the shell files and the nginx config
   (docker-entrypoint.sh), so every deploy that changes any of them installs a
   new worker; it takes over at once (skipWaiting + clients.claim) and reloads
   the open player pages into the new version.

   Media (/uploads/ on the API host): cache-first. Upload filenames are
   content-unique (uuid names, transcodes get a new name), so a cached copy
   never goes stale and is never re-downloaded. Only verified good responses
   are stored — a 200 read over CORS that is not an HTML error page — so a
   tunnel outage answering 530 (or anything opaque, whose status cannot be
   seen) can never replace good media or the app shell. The page tells the
   worker which media its current content uses ("media-manifest"); those are
   prefetched and pinned, plus a small LRU of recent ones, and everything
   else is evicted so the cache cannot grow for ever.

   Like player.js, this must run on Chrome 66 (Android 9's stock WebView). */
const PLAYER_VERSION = "__PLAYER_VERSION__";
const API_BASE_HINT = "__API_BASE_URL__";
const SHELL_CACHE = `signage-player-${PLAYER_VERSION}`;
const MEDIA_CACHE = "signage-media-v1";
const META_URL = new URL("/__khanshoof-media-meta.json", self.location.origin).href;
const STATE_URL = new URL("/__khanshoof-sw-state.json", self.location.origin).href;
// Recently used media kept beyond what the current content references.
const MAX_UNPINNED = 40;
const MAX_PINNED = 300;
// A URL that could not be stored is not downloaded again until this backoff
// (doubling per failure) has passed: a file that never fits the quota, or an
// upload answering an error, must not be re-fetched on every display.
const FAIL_BACKOFF_BASE_MS = 60 * 1000;
const FAIL_BACKOFF_MAX_MS = 60 * 60 * 1000;
const QUOTA_BACKOFF_MAX_MS = 12 * 60 * 60 * 1000;
const MAX_FAILURES_TRACKED = 500;
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

// Origins whose /uploads/ we cache: our own, the configured API, and any API
// origin a page has told us about (persisted in the media meta).
const uploadOrigins = new Set([self.location.origin]);
try {
  if (/^https?:\/\//.test(API_BASE_HINT)) uploadOrigins.add(new URL(API_BASE_HINT).origin);
} catch (_) {}

const inflight = new Map();     // url -> Promise, so one URL downloads once
const recentHits = new Map();   // url -> ms, merged into the LRU on next sync
const failures = new Map();     // url -> { count, until }, persisted in the meta
let manifestGen = 0;
let syncChain = Promise.resolve();
let metaChain = Promise.resolve();   // serializes read-modify-write of the meta
let persistTimer = null;

function isPreviewUrl(url) {
  return /[?&]preview_token=/.test(String(url || ""));
}

function versioned(url) {
  return `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(PLAYER_VERSION)}`;
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    try {
      // Fetched as ?v=<version> and stored under the plain URL: a URL unique
      // to this version cannot be answered from the browser's HTTP cache or
      // from Cloudflare's edge, which keeps CSS and images for 7 days and
      // would otherwise pin an old stylesheet to a new player.js.
      await Promise.all(ASSETS.map(async (url) => {
        const res = await fetch(versioned(url), { cache: "reload" });
        if (!res.ok) throw new Error(`precache ${url}: ${res.status}`);
        await cache.put(url, res);
      }));
    } catch (err) {
      // An install during an outage fails as a whole; the current worker
      // keeps serving, and no half-filled cache is left behind.
      await caches.delete(SHELL_CACHE);
      throw err;
    }
    await self.skipWaiting();
  })());
});

/* Pages of players older than this one have no controllerchange handler: they
   would keep running the old code until the TV rebooted. Navigate every open
   player page once, when this version replaces another. Guarded per version
   in the (unversioned) media cache, so it can never loop. */
async function reloadOpenPlayers() {
  const store = await caches.open(MEDIA_CACHE);
  try {
    const marker = await store.match(STATE_URL);
    if (marker && (await marker.json()).navigatedFor === PLAYER_VERSION) return;
  } catch (_) {}
  await store.put(STATE_URL, new Response(JSON.stringify({ navigatedFor: PLAYER_VERSION }), {
    headers: { "Content-Type": "application/json" },
  }));
  const pages = await self.clients.matchAll({ type: "window" });
  await Promise.all(pages
    .filter((client) => !isPreviewUrl(client.url))
    .map((client) => client.navigate(client.url).catch(() => {})));
}

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    const replacedAnother = keys.some((key) => key.startsWith("signage-player-") && key !== SHELL_CACHE);
    await Promise.all(
      keys.filter((key) => key !== SHELL_CACHE && key !== MEDIA_CACHE)
        .map((key) => caches.delete(key))
    );
    await self.clients.claim();
    if (replacedAnother) await reloadOpenPlayers();
  })());
});

async function readMeta(cache) {
  try {
    const res = await cache.match(META_URL);
    if (res) {
      const meta = await res.json();
      return {
        pinned: Array.isArray(meta.pinned) ? meta.pinned : [],
        used: meta.used && typeof meta.used === "object" ? meta.used : {},
        origins: Array.isArray(meta.origins) ? meta.origins : [],
        failed: meta.failed && typeof meta.failed === "object" ? meta.failed : {},
      };
    }
  } catch (_) {}
  return { pinned: [], used: {}, origins: [], failed: {} };
}

function serializeFailures() {
  const now = Date.now();
  const out = {};
  Array.from(failures.entries())
    .filter(([, f]) => f.until > now)
    .slice(-MAX_FAILURES_TRACKED)
    .forEach(([url, f]) => { out[url] = f; });
  return out;
}

async function writeMeta(cache, meta) {
  meta.failed = serializeFailures();
  await cache.put(META_URL, new Response(JSON.stringify(meta), {
    headers: { "Content-Type": "application/json" },
  }));
}

function withMeta(fn) {
  metaChain = metaChain.then(async () => {
    const cache = await caches.open(MEDIA_CACHE);
    const meta = await readMeta(cache);
    await fn(cache, meta);
    await writeMeta(cache, meta);
  }).catch(() => {});
  return metaChain;
}

caches.open(MEDIA_CACHE)
  .then(readMeta)
  .then((meta) => {
    meta.origins.forEach((origin) => uploadOrigins.add(origin));
    const now = Date.now();
    Object.entries(meta.failed).forEach(([url, f]) => {
      if (f && f.until > now && !failures.has(url)) failures.set(url, f);
    });
  })
  .catch(() => {});

function inBackoff(key) {
  const f = failures.get(key);
  return Boolean(f) && Date.now() < f.until;
}

function persistFailuresSoon() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => withMeta(() => {}), 1000);
}

function noteFailure(key, maxMs) {
  const prev = failures.get(key);
  const count = (prev ? prev.count : 0) + 1;
  failures.delete(key);   // re-insert: keeps the map in recency order
  failures.set(key, { count, until: Date.now() + Math.min(maxMs, FAIL_BACKOFF_BASE_MS * 2 ** (count - 1)) });
  while (failures.size > MAX_FAILURES_TRACKED) failures.delete(failures.keys().next().value);
  persistFailuresSoon();
}

function noteSuccess(key) {
  if (failures.delete(key)) persistFailuresSoon();
}

function mediaKey(url) {
  return `${url.origin}${url.pathname}${url.search}`;
}

function isInternal(url) {
  return url === META_URL || url === STATE_URL;
}

function isCacheable(res) {
  if (!res || !res.ok || res.status !== 200) return false;
  // Opaque responses hide their status: a 530 error page looks like success.
  if (res.type !== "cors" && res.type !== "basic") return false;
  const type = (res.headers.get("content-type") || "").toLowerCase();
  return !type.startsWith("text/html");
}

async function evict(cache, meta, keepUnpinned) {
  const pinned = new Set(meta.pinned);
  const keys = (await cache.keys()).map((req) => req.url);
  const loose = keys.filter((url) => !isInternal(url) && !pinned.has(url));
  loose.sort((a, b) => (meta.used[b] || 0) - (meta.used[a] || 0));
  for (const url of loose.slice(keepUnpinned)) {
    await cache.delete(url);
    delete meta.used[url];
  }
  const present = new Set(keys);
  Object.keys(meta.used).forEach((url) => {
    if (!present.has(url) && !pinned.has(url)) delete meta.used[url];
  });
}

function storeUpload(key) {
  if (inflight.has(key)) return inflight.get(key);
  if (inBackoff(key)) return Promise.resolve(false);
  const job = (async () => {
    const cache = await caches.open(MEDIA_CACHE);
    if (await cache.match(key, { ignoreVary: true })) return true;
    let res;
    try {
      // no-store: the screen's own <img>/<video> request carries no Origin,
      // so the API answers it without CORS headers and the browser's HTTP
      // cache keeps that copy. Served from there, this CORS request fails
      // its CORS check -- and nothing would ever be stored.
      res = await fetch(key, { mode: "cors", credentials: "omit", cache: "no-store" });
    } catch (_) {
      return false;   // offline or CORS refused: no bytes spent, no backoff
    }
    if (!isCacheable(res)) {
      try { if (res.body) res.body.cancel(); } catch (_) {}
      noteFailure(key, FAIL_BACKOFF_MAX_MS);
      return false;
    }
    try {
      await cache.put(key, res);
      recentHits.set(key, Date.now());
      noteSuccess(key);
      return true;
    } catch (err) {
      if (err && err.name === "QuotaExceededError") {
        noteFailure(key, QUOTA_BACKOFF_MAX_MS);
        await withMeta((c, meta) => evict(c, meta, 0));
      } else {
        noteFailure(key, FAIL_BACKOFF_MAX_MS);   // the body broke off mid-download
      }
      return false;
    }
  })().finally(() => inflight.delete(key));
  inflight.set(key, job);
  return job;
}

async function syncMedia(urls, gen) {
  const want = [];
  await withMeta(async (cache, meta) => {
    const now = Date.now();
    urls.forEach((raw) => {
      let url;
      try { url = new URL(raw); } catch (_) { return; }
      if (!url.pathname.startsWith("/uploads/")) return;
      uploadOrigins.add(url.origin);
      if (!meta.origins.includes(url.origin)) meta.origins.push(url.origin);
      const key = mediaKey(url);
      if (!want.includes(key) && want.length < MAX_PINNED) want.push(key);
    });
    recentHits.forEach((at, key) => { meta.used[key] = Math.max(meta.used[key] || 0, at); });
    recentHits.clear();
    want.forEach((key) => { meta.used[key] = now; });
    meta.pinned = want;
    meta.origins = meta.origins.slice(-10);
    await evict(cache, meta, MAX_UNPINNED);
  });
  for (const key of want) {
    if (gen !== manifestGen) return;   // newer content arrived; it decides now
    await storeUpload(key);
  }
}

self.addEventListener("message", (event) => {
  const msg = event.data;
  if (!msg || msg.type !== "media-manifest" || !Array.isArray(msg.urls)) return;
  // A dashboard preview never pins or prefetches a screen's media.
  if (event.source && isPreviewUrl(event.source.url)) return;
  const gen = ++manifestGen;
  syncChain = syncChain.then(() => syncMedia(msg.urls, gen)).catch(() => {});
  if (event.waitUntil) event.waitUntil(syncChain);
});

// Serve a byte range of a cached full response: <video> asks for ranges, and
// seeking (wall drift correction) must work offline too.
async function rangeResponse(cached, rangeHeader) {
  const blob = await cached.blob();
  const size = blob.size;
  const match = /^bytes=(\d*)-(\d*)$/.exec(String(rangeHeader).trim());
  if (!match || (match[1] === "" && match[2] === "")) {
    return new Response(blob, { status: 200, headers: cached.headers });
  }
  let start;
  let end;
  if (match[1] === "") {
    start = Math.max(0, size - Number(match[2]));
    end = size - 1;
  } else {
    start = Number(match[1]);
    end = match[2] === "" ? size - 1 : Math.min(Number(match[2]), size - 1);
  }
  if (start >= size || start > end) {
    return new Response("", { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    statusText: "Partial Content",
    headers: {
      "Content-Type": cached.headers.get("content-type") || blob.type || "application/octet-stream",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}

async function requestedByPreview(event) {
  try {
    const id = event.clientId || event.resultingClientId;
    if (!id) return false;
    const client = await self.clients.get(id);
    return Boolean(client) && isPreviewUrl(client.url);
  } catch (_) {
    return false;
  }
}

async function serveUpload(event, url) {
  const req = event.request;
  const key = mediaKey(url);
  let cached = null;
  try {
    cached = await (await caches.open(MEDIA_CACHE)).match(key, { ignoreVary: true });
  } catch (_) {}
  if (cached) {
    recentHits.set(key, Date.now());
    const range = req.headers.get("range");
    return range ? rangeResponse(cached, range) : cached;
  }
  // Not cached yet: the network answers this request untouched, and a
  // verified full copy is stored alongside for next time (not for previews).
  if (!(await requestedByPreview(event))) {
    try { event.waitUntil(storeUpload(key).catch(() => {})); } catch (_) {}
  }
  return fetch(req);
}

async function serveShell(req) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = req.mode === "navigate"
    ? await cache.match("/index.html")        // any ?token=… / ?api_base=… URL
    : await cache.match(req, { ignoreSearch: true });
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok && res.type === "basic" && req.mode !== "navigate") {
      cache.put(req, res.clone()).catch(() => {});
    }
    return res;
  } catch (err) {
    const fallback = await caches.match(req, { ignoreSearch: true });
    if (fallback) return fallback;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.pathname.startsWith("/uploads/") && uploadOrigins.has(url.origin)) {
    event.respondWith(serveUpload(event, url));
    return;
  }
  if (url.origin === self.location.origin) {
    event.respondWith(serveShell(req));
  }
});
