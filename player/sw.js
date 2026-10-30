/* Khanshoof player service worker.

   Shell: precached per version and served cache-first, so a TV boots with no
   network at all. The version is stamped at container start from
   PLAYER_VERSION plus a hash of the shell files (docker-entrypoint.sh), so
   every deploy that changes a shell file installs a new worker; it takes over
   at once (skipWaiting + clients.claim) and the page reloads into it.

   Media (/uploads/ on the API host): cache-first. Upload filenames are
   content-unique (uuid names, transcodes get a new name), so a cached copy
   never goes stale and is never re-downloaded. Only verified good responses
   are stored — a 200 read over CORS that is not an HTML error page — so a
   tunnel outage answering 530 (or anything opaque, whose status cannot be
   seen) can never replace good media or the app shell. The page tells the
   worker which media its current content uses ("media-manifest"); those are
   prefetched and pinned, plus a small LRU of recent ones, and everything
   else is evicted so the cache cannot grow for ever. */
const PLAYER_VERSION = "__PLAYER_VERSION__";
const API_BASE_HINT = "__API_BASE_URL__";
const SHELL_CACHE = `signage-player-${PLAYER_VERSION}`;
const MEDIA_CACHE = "signage-media-v1";
const META_URL = new URL("/__khanshoof-media-meta.json", self.location.origin).href;
// Recently used media kept beyond what the current content references.
const MAX_UNPINNED = 40;
const MAX_PINNED = 300;
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
let manifestGen = 0;
let syncChain = Promise.resolve();

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // cache: "reload" — never precache a stale copy from the HTTP cache
    // (styles.css and images carry a 7-day max-age). addAll rejects on any
    // non-ok response, so an install during an outage simply fails and the
    // current worker keeps serving.
    await cache.addAll(ASSETS.map((url) => new Request(url, { cache: "reload" })));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((key) => key !== SHELL_CACHE && key !== MEDIA_CACHE)
        .map((key) => caches.delete(key))
    );
    await self.clients.claim();
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
      };
    }
  } catch (_) {}
  return { pinned: [], used: {}, origins: [] };
}

async function writeMeta(cache, meta) {
  await cache.put(META_URL, new Response(JSON.stringify(meta), {
    headers: { "Content-Type": "application/json" },
  }));
}

caches.open(MEDIA_CACHE)
  .then(readMeta)
  .then((meta) => meta.origins.forEach((origin) => uploadOrigins.add(origin)))
  .catch(() => {});

function mediaKey(url) {
  return `${url.origin}${url.pathname}${url.search}`;
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
  const loose = keys.filter((url) => url !== META_URL && !pinned.has(url));
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
  const job = (async () => {
    const cache = await caches.open(MEDIA_CACHE);
    if (await cache.match(key, { ignoreVary: true })) return true;
    let res;
    try {
      res = await fetch(key, { mode: "cors", credentials: "omit" });
    } catch (_) {
      return false;   // offline, or the API does not allow this origin (CORS)
    }
    if (!isCacheable(res)) {
      try { if (res.body) res.body.cancel(); } catch (_) {}
      return false;
    }
    try {
      await cache.put(key, res);
      recentHits.set(key, Date.now());
      return true;
    } catch (err) {
      if (err && err.name === "QuotaExceededError") {
        const meta = await readMeta(cache);
        await evict(cache, meta, 0);
        await writeMeta(cache, meta);
      }
      return false;
    }
  })().finally(() => inflight.delete(key));
  inflight.set(key, job);
  return job;
}

async function syncMedia(urls, gen) {
  const cache = await caches.open(MEDIA_CACHE);
  const meta = await readMeta(cache);
  const now = Date.now();
  const want = [];
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
  await writeMeta(cache, meta);
  for (const key of want) {
    if (gen !== manifestGen) return;   // newer content arrived; it decides now
    await storeUpload(key);
  }
}

self.addEventListener("message", (event) => {
  const msg = event.data;
  if (!msg || msg.type !== "media-manifest" || !Array.isArray(msg.urls)) return;
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
  // verified full copy is stored alongside for next time.
  try { event.waitUntil(storeUpload(key).catch(() => {})); } catch (_) {}
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
