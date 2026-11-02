/* Runs on Chrome 66 and newer: the stock WebView of Android 9, which signage
   panels without Play never update. A syntax error blanks the whole screen,
   so no ?. or ??, no 1_000 separators and no { ...spread } in object
   literals; an API newer than 66 only behind a feature check. The Android
   app's MIN_WEBVIEW_MAJOR (android-tv/.../Policy.kt) holds the same number. */
const urlOverrides = new URLSearchParams(window.location.search);
const API_BASE = (
  urlOverrides.get("api_base") ||
  (window.API_BASE_URL || "").trim() ||
  `${window.location.protocol}//${window.location.hostname}:8000`
).replace(/\/+$/, "");

const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");
const zonesEl = document.getElementById("zones");
const wallHighlightEl = document.getElementById("wall-highlight");
const wallHighlightLabelEl = document.getElementById("wall-highlight-label");

const pairingEl = document.getElementById("pairing");
const pairingCodeEl = document.getElementById("pairing-code");
const pairingQrEl = document.getElementById("pairing-qr");
const pairingUrlEl = document.getElementById("pairing-url");
const pairingMetaEl = document.getElementById("pairing-meta");
const emptyStateEl = document.getElementById("empty-state");

const APP_URL = (window.APP_URL || "").trim() || "https://app.khanshoof.com";
const APP_HOST = APP_URL.replace(/^https?:\/\//, "").replace(/\/$/, "");
document.getElementById("empty-url").textContent = APP_HOST;
const PAIR_POLL_INTERVAL_MS = 3000;

// How often a healthy player re-reads its content/layout, and how it backs off
// while the API is unreachable (it keeps showing the last content it had).
const REFRESH_INTERVAL_MS = 15000;
const RETRY_BACKOFF_MS = [2000, 4000, 8000, 15000, 30000, 60000];
// A request that never answers must not stall the refresh loop forever.
const API_TIMEOUT_MS = 15000;
// Boot shows the cached content if the network has not answered by then.
const BOOT_CACHE_FALLBACK_MS = 4000;
// An item whose duration is 0/negative in a multi-item zone or playlist:
// images and pages dwell this long, videos play to their end.
const DEFAULT_DWELL_MS = 10000;
const VIDEO_METADATA_TIMEOUT_MS = 60000;
const VIDEO_END_GRACE_MS = 15000;
// While the wall socket is up it drives playback; the API is only re-checked
// this often, to notice the screen leaving the wall.
const WALL_CHECK_INTERVAL_MS = 60000;
// The server pings every 25s; a socket silent for longer than this is dead.
const WALL_SILENCE_LIMIT_MS = 75000;
const WALL_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
// The server's clock is estimated from the frames of the last 10 minutes, at
// most 32 of them (see noteServerTime).
const WALL_CLOCK_WINDOW_MS = 10 * 60 * 1000;
const WALL_CLOCK_MAX_SAMPLES = 32;
// A wall TV reports its screen size this long after the window stops resizing.
const WALL_SCREEN_REPORT_DEBOUNCE_MS = 1000;
// The server clamps a reported size to 100..10000 px. A window under 100 px
// has no layout yet and is no screen: reporting it would be clamped to
// 100x100 and square this TV's default box, so it is not reported.
const WALL_SCREEN_MIN_PX = 100;
// Deploys: how often to look for a new player version.
const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;
const RELOAD_GUARD_MS = 60000;
const CONTROLLER_RELOAD_DELAY_MS = 3000;
const MANIFEST_RESEND_MS = 10 * 60 * 1000;

let activePairCode = null;
let pairPollTimer = null;
let pairRetryTimer = null;

let screenToken = null;
let previewToken = null;
let currentSignature = "";
let currentItems = [];
let layoutSignature = "";
// What the screen is showing: "playlist" | "zones" | "wall" | "pairing" | null.
let mode = null;
const cachePrefix = "signage_cache_";

// ── Connection indicator (Phase 2.5d) ─────────────────────────────────
const ConnectionStatus = (() => {
  let lastFrameAt = 0;     // Date.now() of last successful WS frame OR API success
  let wsState = "none";    // "none" (no wall socket) | "open" | "connecting" | "closed"
  let timer = null;
  const dot = () => document.getElementById("connection-indicator");

  function markFrame() { lastFrameAt = Date.now(); }
  function setWs(s)    { wsState = s; }

  function compute() {
    const ageMs = Date.now() - lastFrameAt;
    // Playlist and zones screens have no socket: the age of the last API
    // answer is the whole signal. They poll every 15s, so green survives one
    // slow or missed poll. Without this a healthy screen showed amber forever.
    if (wsState === "none") {
      if (ageMs < 45000)          return "green";
      if (ageMs < 5 * 60 * 1000) return "amber";
      return "red";
    }
    if (wsState === "open" && ageMs < 30000)         return "green";
    if (wsState === "open" && ageMs < 5 * 60 * 1000) return "amber";
    if (wsState === "connecting")                    return "amber";
    if (ageMs > 30000)                               return "red";
    return "amber";
  }

  function render() {
    const el = dot(); if (!el) return;
    el.classList.remove("conn-green", "conn-amber", "conn-red");
    el.classList.add(`conn-${compute()}`);
  }

  function show() { const el = dot(); if (el) el.classList.remove("hidden"); render(); }
  function hide() { const el = dot(); if (el) el.classList.add("hidden"); }

  function start() {
    if (timer) return;
    timer = setInterval(render, 5000);
  }

  return { show, hide, markFrame, setWs, start };
})();

function setStatus(text) {
  statusEl.textContent = text;
}

function getParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

// The key names the screen explicitly: a response that arrives after the
// token changed (a "bye", a re-pair) must not be filed under the new one --
// or under "screen_null".
function getCacheKey(type, token = screenToken) {
  const key = previewToken ? `preview_${previewToken}` : `screen_${token}`;
  return `${cachePrefix}${type}_${key}`;
}

function readCache(type, token = screenToken) {
  try {
    const raw = localStorage.getItem(getCacheKey(type, token));
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function writeCache(type, data, token = screenToken) {
  if (!token && !previewToken) return;
  try { localStorage.setItem(getCacheKey(type, token), JSON.stringify(data)); } catch (_) {}
}

/* Media URLs from the API are relative ("/uploads/x.png") and live on the API
   host, not on the player's. Absolute URLs (website items, external images)
   are used as they are. */
function resolveMediaUrl(url) {
  if (typeof url !== "string" || !url) return url;
  if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith("//")) return url;
  return `${API_BASE}${url.startsWith("/") ? "" : "/"}${url}`;
}

/* fetch() plus the whole body, under one timeout. A response whose headers
   arrive and whose body then stalls must not hang the caller (the refresh
   loop stays "in flight" for good otherwise). Returns
   { ok, status, headers, text, json() } with the body already read. */
async function apiFetch(url, options = {}) {
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      if (ctrl) ctrl.abort();
      reject(new Error(`timeout after ${API_TIMEOUT_MS}ms: ${url}`));
    }, API_TIMEOUT_MS);
  });
  const request = (async () => {
    const res = await fetch(url, ctrl ? Object.assign({}, options, { signal: ctrl.signal }) : options);
    const text = await res.text();
    return {
      ok: res.ok,
      status: res.status,
      headers: res.headers,
      text,
      json() { return JSON.parse(text); },
    };
  })();
  try {
    return await Promise.race([request, timeout]);
  } finally {
    clearTimeout(timer);
    request.catch(() => {});   // the loser of the race must not go unhandled
  }
}

async function pairWithCode(code) {
  const res = await apiFetch(`${API_BASE}/screens/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pair_code: code }),
  });
  if (!res.ok) {
    throw new Error("Pairing failed");
  }
  const screen = await res.json();
  return screen.token;
}

function buildSignature(items) {
  return items.map((item) => `${item.id}:${item.duration_seconds}:${item.url}`).join("|");
}

/* ── Releasing media ───────────────────────────────────────────────────
   A removed <video> keeps its decoder until its source is dropped; on a TV
   that runs for weeks, cycling videos would exhaust the hardware decoders. */
function releaseMedia(root) {
  if (!root) return;
  const videos = root.tagName === "VIDEO"
    ? [root]
    : Array.from(root.querySelectorAll ? root.querySelectorAll("video") : []);
  videos.forEach((video) => {
    video._released = true;
    try {
      video.pause();
      video.removeAttribute("src");
      video.load();
    } catch (_) {}
  });
}

function removeNode(node) {
  releaseMedia(node);
  node.remove();
}

function clearContainer(el) {
  // Also invalidates any item still loading for this container (mountMedia).
  el._mountSeq = (el._mountSeq || 0) + 1;
  releaseMedia(el);
  el.innerHTML = "";
}

/* Autoplay is not a promise: kiosk browsers reject or silently skip play()
   (power saving, a decoder still busy, a policy change). Muted + playsinline
   is what every TV browser allows; retry a few times with backoff. */
function ensurePlaying(video, attempt = 0) {
  if (video._released) return;
  video.muted = true;
  let result;
  try { result = video.play(); } catch (_) { result = null; }
  if (result && typeof result.catch === "function") {
    result.catch(() => {
      if (video._released || attempt >= 5) return;
      setTimeout(() => {
        if (video.isConnected && video.paused && !video.ended) ensurePlaying(video, attempt + 1);
      }, 1000 * (attempt + 1));
    });
  }
}

function showPairingView() {
  ConnectionStatus.hide();
  contentEl.classList.add("hidden");
  zonesEl.classList.add("hidden");
  pairingEl.classList.remove("hidden");
  statusEl.style.display = "none";
}

function hidePairingView() {
  ConnectionStatus.show();
  ConnectionStatus.start();
  pairingEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  statusEl.style.display = "";
}

function renderPairingCode(code) {
  pairingCodeEl.textContent = code;
  const url = `${APP_URL}/pair?code=${encodeURIComponent(code)}`;
  pairingUrlEl.textContent = `${APP_HOST}/pair`;

  // QR — type 0 = auto-fit version, "M" error correction handles modest TV glare
  const qr = qrcode(0, "M");
  qr.addData(url);
  qr.make();
  pairingQrEl.innerHTML = qr.createImgTag(8, 16);
}

async function requestPairingCode() {
  const res = await apiFetch(`${API_BASE}/screens/request_code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_agent: navigator.userAgent.slice(0, 500) }),
  });
  if (!res.ok) {
    throw new Error(`request_code failed: ${res.status}`);
  }
  return res.json(); // { code, device_id, expires_at, expires_in_seconds }
}

async function pollPairingCode(code) {
  const res = await apiFetch(`${API_BASE}/screens/poll/${encodeURIComponent(code)}`);
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

/* Never throws and never waits on the network: the refresh loop loads the
   content and keeps retrying with backoff if the first attempt fails, so a
   single failed request right after pairing can no longer strand the TV on a
   hidden "Loading content..." forever. */
function onPaired(token) {
  stopPairPoll();
  clearTimeout(pairRetryTimer);
  activePairCode = null;
  try { localStorage.setItem("screen_token", token); } catch (_) {}
  screenToken = token;
  mode = null;
  hidePairingView();
  setStatus(Khan.t("status.loading_content", "Loading content..."));
  refreshFailures = 0;
  scheduleRefresh(0);
}

/* Last resort: mount the item even if its load event never arrives (a hung
   request, a server that never closes the connection, a frame that stalls).
   Generous enough not to pre-empt a normal load, short enough to stay well
   inside a typical slot duration — a screen must never go permanently blank. */
const MOUNT_FALLBACK_MS = 5000;

function mountMedia(container, node, enableFade, transitionMs = 600) {
  // A newer mount, or the container being cleared, supersedes this one: a
  // late load event must not drop a stale item on top of newer content.
  const seq = (container._mountSeq = (container._mountSeq || 0) + 1);
  if (enableFade) {
    node.classList.add("fade-media");
    node.style.transitionDuration = `${transitionMs}ms`;
  }
  let mounted = false;
  const showNode = () => {
    if (mounted) return;   // load and the fallback timer can both fire
    mounted = true;
    // Superseded, cleared, or its zone re-rendered away while it loaded: a
    // video played into a detached container would hold a decoder for good.
    if (container._mountSeq !== seq || !container.isConnected) {
      releaseMedia(node);
      return;
    }
    const previous = Array.from(container.children);
    container.appendChild(node);
    if (node.tagName === "VIDEO") ensurePlaying(node);
    if (enableFade) {
      /* Reveal on the next frame so the browser registers the opacity
         transition — but never depend on it alone: browsers throttle
         requestAnimationFrame in backgrounded tabs, which would leave the item
         mounted at opacity 0 and the screen black. classList.add is idempotent,
         so whichever path runs first wins and the other is a no-op. */
      const reveal = () => node.classList.add("visible");
      requestAnimationFrame(reveal);
      setTimeout(reveal, 50);
      if (previous.length) {
        setTimeout(() => previous.forEach(removeNode), transitionMs + 50);
      }
    } else {
      previous.forEach(removeNode);
    }
  };
  if (node.tagName === "IMG") {
    node.addEventListener("load", showNode, { once: true });
    node.addEventListener("error", showNode, { once: true });
  } else if (node.tagName === "VIDEO") {
    node.addEventListener("loadeddata", showNode, { once: true });
    node.addEventListener("error", showNode, { once: true });
  } else if (node.tagName === "IFRAME") {
    node.addEventListener("load", showNode, { once: true });
  } else {
    showNode();
  }
  if (!mounted) {
    setTimeout(showNode, MOUNT_FALLBACK_MS);
  }
}

/* A "website" item whose URL is really a picture (very common — people paste a
   link to a product photo) must be rendered as an <img>, not framed: the
   player's CSP allows https images but no external frames, so an iframe here
   renders a black screen. Query strings and fragments are ignored when
   sniffing the extension. */
const IMAGE_URL_RE = /\.(jpe?g|png|gif|webp|avif|bmp|svg)(?:[?#]|$)/i;

function isImageUrl(url) {
  return typeof url === "string" && IMAGE_URL_RE.test(url);
}

function createMediaNode(item, loop) {
  const mime = item.mime_type || "";
  const url = resolveMediaUrl(item.url);
  let node;
  if (mime.startsWith("video")) {
    node = createVideoNode(url, loop);
  } else if (mime.startsWith("image") || (mime === "text/url" && isImageUrl(item.url))) {
    node = document.createElement("img");
    node.src = url;
    node.decoding = "async";
    node.alt = "";
  } else if (mime === "application/pdf") {
    node = document.createElement("iframe");
    node.src = `${url}#toolbar=0&navpanes=0`;
  } else if (mime === "text/url") {
    node = document.createElement("iframe");
    node.src = url;
  } else {
    node = document.createElement("div");
    node.textContent = `Unsupported media: ${item.name}`;
  }
  return node;
}

function renderMediaItem(container, item, { loop, fade, transitionMs }) {
  const node = createMediaNode(item, loop);
  mountMedia(container, node, fade, transitionMs);
  return node;
}

function createVideoNode(url, loop) {
  const node = document.createElement("video");
  node.muted = true;
  node.setAttribute("muted", "");
  node.autoplay = true;
  node.playsInline = true;
  node.loop = loop;
  node.preload = "auto";
  node.disableRemotePlayback = true;
  node.controls = false;
  node.setAttribute("playsinline", "");
  node.setAttribute("webkit-playsinline", "");
  // Hint browser to prefer hardware decode + GPU compositing
  node.style.willChange = "transform";
  node.style.transform = "translateZ(0)";
  node.src = url;
  return node;
}

function itemDurationMs(item) {
  const raw = item ? item.duration_seconds : null;
  if (raw === null || raw === undefined || raw === "") return 10000;
  const seconds = Number(raw);
  return Number.isFinite(seconds) ? seconds * 1000 : 10000;
}

/* ── Carousels: the main playlist and each zone ────────────────────────
   One timer per carousel (replaced, never accumulated). Durations:
   - > 0: show for that long (videos loop), crossfading to the next item.
   - <= 0 on the only item: static, as documented — no fade, no advance; a
     lone video loops instead of freezing on its last frame.
   - <= 0 among several items: a video plays to its end, anything else dwells
     DEFAULT_DWELL_MS. It used to stop the carousel for good, freezing a video
     on its last frame. */
const carousels = new Map();

function stopCarousel(key) {
  const run = carousels.get(key);
  if (run) {
    clearTimeout(run.timer);
    carousels.delete(key);
  }
}

function clearPlayback() {
  stopCarousel("main");
}

function clearZonePlayback() {
  Array.from(carousels.keys())
    .filter((key) => key.startsWith("zone:"))
    .forEach(stopCarousel);
}

function startCarousel(key, container, items, transitionMs, onItem) {
  stopCarousel(key);
  if (!items || items.length === 0) return;
  const run = { timer: null };
  carousels.set(key, run);
  const alive = () => carousels.get(key) === run;
  const later = (fn, ms) => {
    clearTimeout(run.timer);
    run.timer = setTimeout(fn, ms);
  };
  const single = items.length === 1;
  let index = 0;
  const playNext = () => {
    if (!alive()) return;
    clearTimeout(run.timer);
    const item = items[index];
    index = (index + 1) % items.length;
    if (onItem) onItem(item);
    const durationMs = itemDurationMs(item);
    const isVideo = (item.mime_type || "").startsWith("video");
    if (durationMs > 0) {
      renderMediaItem(container, item, { loop: isVideo, fade: true, transitionMs });
      later(playNext, durationMs);
    } else if (single) {
      renderMediaItem(container, item, { loop: true, fade: false, transitionMs });
    } else if (isVideo) {
      const node = renderMediaItem(container, item, { loop: false, fade: true, transitionMs });
      let done = false;
      const advance = () => {
        if (done || !alive() || node._released) return;
        done = true;
        playNext();
      };
      node.addEventListener("ended", advance, { once: true });
      node.addEventListener("error", () => later(advance, DEFAULT_DWELL_MS), { once: true });
      node.addEventListener("loadedmetadata", () => {
        if (Number.isFinite(node.duration) && node.duration > 0) {
          later(advance, node.duration * 1000 + VIDEO_END_GRACE_MS);
        }
      }, { once: true });
      later(advance, VIDEO_METADATA_TIMEOUT_MS);
    } else {
      renderMediaItem(container, item, { loop: false, fade: true, transitionMs });
      later(playNext, DEFAULT_DWELL_MS);
    }
  };
  playNext();
}

function startPlayback(items) {
  currentItems = items;
  startCarousel("main", contentEl, items, 600, (item) => setStatus(`Playing ${item.name}`));
}

function renderZonesLayout(zones) {
  clearZonePlayback();
  clearContainer(zonesEl);
  zonesEl.classList.remove("hidden");
  ConnectionStatus.show();
  ConnectionStatus.start();
  contentEl.classList.add("hidden");
  zones.forEach((zone, index) => {
    const zoneEl = document.createElement("div");
    zoneEl.className = "zone-region";
    zoneEl.style.left = `${zone.x * 100}%`;
    zoneEl.style.top = `${zone.y * 100}%`;
    zoneEl.style.width = `${zone.width * 100}%`;
    zoneEl.style.height = `${zone.height * 100}%`;
    const content = document.createElement("div");
    content.className = "zone-content";
    zoneEl.appendChild(content);
    zonesEl.appendChild(zoneEl);
    startCarousel(`zone:${index}`, content, zone.items || [], Number(zone.transition_ms != null ? zone.transition_ms : 600));
  });
}

function getLayoutSignature(zones) {
  return JSON.stringify(
    (zones || []).map((zone) => ({
      id: zone.id,
      x: zone.x,
      y: zone.y,
      width: zone.width,
      height: zone.height,
      transition_ms: zone.transition_ms,
      items: (zone.items || []).map((item) => ({
        id: item.id,
        media_id: item.media_id,
        duration_seconds: item.duration_seconds,
        url: item.url,
      })),
    }))
  );
}

/* A screen with nothing assigned at all -- no playlist, no schedule -- is in
   practice one that was just paired. The status line is hidden on TVs, so
   without this the customer who just paired would be looking at black. A
   schedule with nothing on right now, or an emptied playlist, stays black:
   the operator chose that, and the cafe's customers can see this screen. */
function isUnassigned(data) {
  const screen = data && data.screen;
  return Boolean(screen && !screen.playlist_id && !screen.schedule_id);
}

function setEmptyState(visible) {
  emptyStateEl.classList.toggle("hidden", !visible);
}

function showSingleContainer() {
  zonesEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  ConnectionStatus.show();
  ConnectionStatus.start();
}

/* ── Modes ─────────────────────────────────────────────────────────────
   The server decides what this screen is — a wall cell, a zoned layout or a
   plain playlist — and can change its mind at any time. Every refresh goes
   through applyState(), which switches modes cleanly; nothing else may touch
   the containers of a mode it does not own. */
function leaveMode() {
  if (mode === "wall") {
    leaveWallMode();
  } else if (mode === "zones") {
    clearZonePlayback();
    clearContainer(zonesEl);
    layoutSignature = "";
  } else if (mode === "playlist") {
    clearPlayback();
    currentSignature = "";
    currentItems = [];
    clearContainer(contentEl);
  }
  zonesEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  setEmptyState(false);
  mode = null;
}

function showZones(zones) {
  const signature = getLayoutSignature(zones);
  if (mode === "zones" && signature === layoutSignature) return;
  if (mode !== "zones") leaveMode();
  mode = "zones";
  layoutSignature = signature;
  renderZonesLayout(zones);
}

function showPlaylist(content) {
  if (mode !== "playlist") {
    leaveMode();
    mode = "playlist";
    showSingleContainer();
  }
  renderContentData(content);
}

function renderContentData(data) {
  const items = (data && data.items) || [];
  if (items.length === 0) {
    if (currentSignature !== "" || currentItems.length) {
      clearPlayback();
      clearContainer(contentEl);
    }
    currentSignature = "";
    currentItems = [];
    setStatus(Khan.t("status.no_content", "No content assigned"));
    setEmptyState(isUnassigned(data));
    return;
  }
  setEmptyState(false);
  const signature = buildSignature(items);
  if (signature !== currentSignature) {
    currentSignature = signature;
    startPlayback(items);
  }
}

function applyState(content, layout) {
  if (!content && !layout) return;
  // Unpaired meanwhile (a wall "bye", a rejected token): nothing to show,
  // and entering a mode now would strand the pairing screen without a code.
  if ((!screenToken && !previewToken) || mode === "pairing") return;
  if (!previewToken && content && content.wall_id) {
    enterWallMode(content.wall_id);
    announceMedia(Array.from(wallSeen.values()));
    return;
  }
  const zones = layout && Array.isArray(layout.zones) ? layout.zones : [];
  if (zones.length > 0) {
    showZones(zones);
    announceMedia(zones.reduce((all, zone) => all.concat(zone.items || []), []));
    return;
  }
  if (!content) return;   // no zones, but the playlist is unknown: keep what is shown
  showPlaylist(content);
  announceMedia(content.items || []);
}

/* Tokens the API has rejected. A kiosk URL carrying ?token= would otherwise
   win over the token this TV re-paired with, on every reboot or update
   reload, and unpair it again. */
const REJECTED_TOKENS_KEY = "khan_rejected_tokens";

function rejectedTokens() {
  try {
    const list = JSON.parse(localStorage.getItem(REJECTED_TOKENS_KEY) || "[]");
    return Array.isArray(list) ? list : [];
  } catch (_) {
    return [];
  }
}

function rememberRejectedToken(token) {
  try {
    const list = rejectedTokens().filter((t) => t !== token);
    list.push(token);
    localStorage.setItem(REJECTED_TOKENS_KEY, JSON.stringify(list.slice(-5)));
  } catch (_) {}
}

function stripPairingParamsFromUrl() {
  try {
    const url = new URL(window.location.href);
    let changed = false;
    ["token", "code"].forEach((name) => {
      if (url.searchParams.has(name)) {
        url.searchParams.delete(name);
        changed = true;
      }
    });
    if (changed) history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch (_) {}
}

async function handleAuthFailure() {
  console.warn("Screen token rejected — returning to pairing view");
  if (screenToken) rememberRejectedToken(screenToken);
  try { localStorage.removeItem("screen_token"); } catch (_) {}
  stripPairingParamsFromUrl();
  screenToken = null;
  leaveMode();
  clearTimeout(refreshTimer);
  refreshTimer = null;
  await startPairingFlow();
}

/* Only our API saying exactly "this screen does not exist" unpairs a TV --
   the detail both /screens/{token}/content and /layout return. Not an HTML
   404 from a proxy, and not any JSON 404: another FastAPI on the same host
   answers {"detail": "Not Found"}, so a tunnel port typo would otherwise
   unpair every TV at once. */
const SCREEN_GONE_DETAIL = "Screen not found";

function isAuthFailure(res) {
  if (previewToken) return false;
  if (res.status !== 401 && res.status !== 404) return false;
  try {
    const body = res.json();
    return Boolean(body) && body.detail === SCREEN_GONE_DETAIL;
  } catch (_) {
    return false;
  }
}

/* Reads content or layout. Any failure — a rejected fetch (network down,
   tunnel outage without CORS headers), a timeout, an error status, a broken
   body — falls back to the last good copy in localStorage. */
async function loadEndpoint(kind, token) {
  const endpoint = previewToken
    ? `${API_BASE}/preview/${previewToken}/${kind}`
    : `${API_BASE}/screens/${token}/${kind}`;
  let res;
  try {
    res = await apiFetch(endpoint);
  } catch (_) {
    return { data: readCache(kind, token), live: false };
  }
  if (isAuthFailure(res)) return { authFailed: true };
  if (!res.ok) return { data: readCache(kind, token), live: false };
  let data;
  try {
    data = res.json();
  } catch (_) {
    return { data: readCache(kind, token), live: false };
  }
  if (screenToken !== token) return { stale: true };   // unpaired / re-paired meanwhile
  writeCache(kind, data, token);
  return { data, live: true };
}

/* ── Refresh loop ──────────────────────────────────────────────────────
   A chain of timeouts rather than an interval: a slow request can never
   overlap the next, and the delay adapts — every 15s while the API answers,
   backing off to once a minute while it does not. */
let refreshTimer = null;
let refreshFailures = 0;
let refreshInFlight = false;
let refreshQueued = false;
let lastWallCheckAt = 0;

function scheduleRefresh(delayMs) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(runRefresh, delayMs);
}

function refreshSoon() {
  if (mode === "pairing" || (!screenToken && !previewToken)) return;
  if (refreshInFlight) {
    refreshQueued = true;
    return;
  }
  scheduleRefresh(0);
}

async function runRefresh() {
  refreshTimer = null;
  if (refreshInFlight) {
    refreshQueued = true;
    return;
  }
  refreshInFlight = true;
  let ok = false;
  try {
    ok = await refresh();
  } catch (err) {
    console.error(err);
  } finally {
    refreshInFlight = false;
  }
  if (mode === "pairing" || (!screenToken && !previewToken)) return;
  if (refreshQueued) {
    refreshQueued = false;
    scheduleRefresh(0);
    return;
  }
  if (ok) {
    refreshFailures = 0;
    scheduleRefresh(REFRESH_INTERVAL_MS);
  } else {
    const delay = RETRY_BACKOFF_MS[Math.min(refreshFailures, RETRY_BACKOFF_MS.length - 1)];
    refreshFailures += 1;
    setStatus(Khan.t("status.connection_issue", "Connection issue"));
    scheduleRefresh(delay);
  }
}

// Returns true when the API answered (live), false while it is unreachable.
async function refresh() {
  const token = screenToken;
  if (!token && !previewToken) return true;
  if (mode === "wall" && wallSocket && wallSocket.readyState === WebSocket.OPEN) {
    if (Date.now() - wallLastMessageAt > WALL_SILENCE_LIMIT_MS) {
      // Half-open socket: the TV would wait forever for frames that never come.
      closeWallSocket();
      scheduleWallReconnect();
    } else if (Date.now() - lastWallCheckAt < WALL_CHECK_INTERVAL_MS) {
      return true;
    }
  }
  const [content, layout] = await Promise.all([
    loadEndpoint("content", token), loadEndpoint("layout", token),
  ]);
  // A wall "bye" or a re-pair landed while these were in flight: the answer
  // is about a screen this TV no longer is. Applying it would put a token-less
  // TV into wall mode and strand the pairing screen with no code and no poll.
  if (screenToken !== token || mode === "pairing") return true;
  if (content.authFailed || layout.authFailed) {
    await handleAuthFailure();
    return true;
  }
  const live = Boolean(content.live || layout.live);
  if (live) ConnectionStatus.markFrame();
  if (content.live) lastWallCheckAt = Date.now();
  applyState(content.data, layout.data);
  // Nothing on screen yet (e.g. layout answered but content did not): keep
  // retrying on the short backoff rather than the normal 15s cadence.
  return live && mode !== null;
}

function startRefreshLoop() {
  scheduleRefresh(0);
}

/* ── Media manifest for the service worker ─────────────────────────────
   The SW caches exactly what the current content references (plus a small
   LRU of recent items) and evicts the rest, so offline playback works and the
   cache cannot grow without bound over weeks of playlist changes. */
let lastManifestKey = "";
let lastManifestAt = 0;
let lastManifestUrls = [];

function announceMedia(items) {
  // A dashboard preview must not make the operator's browser download and
  // pin a screen's whole media set.
  if (previewToken) return;
  const urls = [];
  (items || []).forEach((item) => {
    if (!item || !item.url || item.mime_type === "text/url") return;
    const url = resolveMediaUrl(item.url);
    if (!urls.includes(url)) urls.push(url);
  });
  const key = urls.join("\n");
  if (key === lastManifestKey && Date.now() - lastManifestAt < MANIFEST_RESEND_MS) return;
  lastManifestKey = key;
  lastManifestAt = Date.now();
  lastManifestUrls = urls;
  postToServiceWorker({ type: "media-manifest", urls });
}

function postToServiceWorker(message) {
  if (!("serviceWorker" in navigator)) return;
  try {
    const controller = navigator.serviceWorker.controller;
    if (controller) {
      controller.postMessage(message);
      return;
    }
    navigator.serviceWorker.ready
      .then((reg) => { if (reg.active) reg.active.postMessage(message); })
      .catch(() => {});
  } catch (_) {}
}

let pairingFlowGen = 0;

async function startPairingFlow() {
  const gen = ++pairingFlowGen;
  clearTimeout(pairRetryTimer);
  pairRetryTimer = null;
  leaveMode();
  mode = "pairing";
  showPairingView();
  stopPairPoll();
  try {
    const data = await requestPairingCode();
    if (mode !== "pairing" || gen !== pairingFlowGen) return;
    activePairCode = data.code;
    renderPairingCode(data.code);
    pairingMetaEl.textContent = Khan.t("pairing.waiting", "Waiting for your phone…");
    schedulePairPoll();
  } catch (err) {
    console.error(err);
    if (mode !== "pairing" || gen !== pairingFlowGen) return;
    pairingCodeEl.textContent = "—";
    pairingMetaEl.textContent = Khan.t("pairing.no_server", "Can't reach server. Retrying…");
    pairRetryTimer = setTimeout(startPairingFlow, 5000);
  }
}

function schedulePairPoll() {
  stopPairPoll();
  pairPollTimer = setTimeout(runPairPoll, PAIR_POLL_INTERVAL_MS);
}

async function runPairPoll() {
  pairPollTimer = null;
  if (!activePairCode) return;
  try {
    const data = await pollPairingCode(activePairCode);
    if (data.status === "paired" && data.screen_token) {
      onPaired(data.screen_token);
      return;
    }
    if (data.status === "expired") {
      pairingMetaEl.textContent = Khan.t("pairing.expired", "Code expired — getting a new one…");
      await startPairingFlow();
      return;
    }
    schedulePairPoll();
  } catch (err) {
    console.error(err);
    pairingMetaEl.textContent = Khan.t("pairing.reconnecting", "Reconnecting…");
    schedulePairPoll();
  }
}

async function boot() {
  const tokenParam = getParam("token");
  const codeParam = getParam("code");
  const previewParam = getParam("preview_token");
  const isPreview = Boolean(previewParam);
  previewToken = previewParam;
  let storedToken = null;
  try { storedToken = isPreview ? null : localStorage.getItem("screen_token"); } catch (_) {}
  // A ?token= the API already rejected must not override the token this TV
  // has re-paired with since (the kiosk URL keeps carrying the dead one).
  const urlToken = tokenParam && !rejectedTokens().includes(tokenParam) ? tokenParam : null;
  if (tokenParam && !urlToken) stripPairingParamsFromUrl();
  screenToken = urlToken || storedToken || screenToken;

  if (!screenToken && codeParam) {
    setStatus(Khan.t("status.pairing", "Pairing..."));
    try {
      screenToken = await pairWithCode(codeParam);
      if (!isPreview) {
        try { localStorage.setItem("screen_token", screenToken); } catch (_) {}
      }
    } catch (err) {
      console.error(err);
      screenToken = null;
    }
  }

  if (!screenToken && !previewToken) {
    await startPairingFlow();
    return;
  }

  setStatus(Khan.t("status.loading_content", "Loading content..."));
  startRefreshLoop();
  // Network slow or down at boot: show what this screen last played rather
  // than black, while the refresh loop keeps retrying.
  setTimeout(() => {
    if (mode === null && (screenToken || previewToken)) {
      applyState(readCache("content"), readCache("layout"));
    }
  }, BOOT_CACHE_FALLBACK_MS);
}

// ====== Wall mode ======
let wallSocket = null;
let wallId = null;
let wallReconnectAttempts = 0;
let wallReconnectTimer = null;
let wallLastMessageAt = 0;
// The server's clock minus performance.now(). Until the first frame arrives
// it is this TV's own clock, as before.
let wallClockOffsetMs = Date.now() - performance.now();
// Recent clock samples: { at: performance.now() on arrival, offset }.
let wallClockSamples = [];
let wallLastFrame = null;
let wallNode = null;
let wallDriftTimer = null;
let wallSpannedGeometry = null;
let wallHighlightTimer = null;
let wallScreenReportTimer = null;
// Recently shown wall items, offered to the SW cache (bounded).
const wallSeen = new Map();
const WALL_SEEN_MAX = 30;

/* Every frame carries server_now_ms. Each sample (server_now_ms minus
   performance.now() on arrival) is the true offset minus that frame's network
   delay, so the largest sample of the last 10 minutes is the least delayed
   one. The offset used to be the latest frame's, delay and all. The samples
   use performance.now(), which never jumps: a TV whose own clock is stepped
   (NTP after boot) keeps a valid estimate, where Date.now()-based samples
   would let a stale one win for ten minutes. */
function noteServerTime(serverNowMs) {
  if (typeof serverNowMs !== "number" || !isFinite(serverNowMs)) return;
  const at = performance.now();
  wallClockSamples.push({ at: at, offset: serverNowMs - at });
  while (wallClockSamples.length > WALL_CLOCK_MAX_SAMPLES
         || at - wallClockSamples[0].at > WALL_CLOCK_WINDOW_MS) {
    wallClockSamples.shift();
  }
  let best = wallClockSamples[0].offset;
  for (let i = 1; i < wallClockSamples.length; i += 1) {
    if (wallClockSamples[i].offset > best) best = wallClockSamples[i].offset;
  }
  wallClockOffsetMs = best;
}

// The server's clock, in ms since the epoch.
function effectiveNowMs() { return performance.now() + wallClockOffsetMs; }

function enterWallMode(id) {
  if (mode === "wall" && wallId === id) {
    if (!wallSocket && !wallReconnectTimer) openWallSocket();
    return;
  }
  leaveMode();
  mode = "wall";
  showSingleContainer();
  wallId = id;
  wallReconnectAttempts = 0;
  lastWallCheckAt = Date.now();
  setStatus(Khan.t("wall.connecting", "Connecting to wall…"));
  openWallSocket();
}

function leaveWallMode() {
  wallId = null;
  clearTimeout(wallReconnectTimer);
  wallReconnectTimer = null;
  clearTimeout(wallScreenReportTimer);
  wallScreenReportTimer = null;
  closeWallSocket();
  ConnectionStatus.setWs("none");
  clearWallPicture();
  hideWallHighlight();
  wallSpannedGeometry = null;
}

function closeWallSocket() {
  const socket = wallSocket;
  wallSocket = null;   // its close handler must not reconnect
  if (socket) {
    try { socket.close(); } catch (_) {}
  }
}

function scheduleWallReconnect() {
  clearTimeout(wallReconnectTimer);
  const delay = WALL_RECONNECT_BACKOFF_MS[
    Math.min(wallReconnectAttempts, WALL_RECONNECT_BACKOFF_MS.length - 1)
  ];
  wallReconnectAttempts += 1;
  wallReconnectTimer = setTimeout(() => {
    wallReconnectTimer = null;
    openWallSocket();
  }, delay);
}

function openWallSocket() {
  clearTimeout(wallReconnectTimer);
  wallReconnectTimer = null;
  if (!wallId || !screenToken) return;
  const wsBase = API_BASE.replace(/^http/, "ws");
  const url = `${wsBase}/walls/${wallId}/ws?screen_token=${encodeURIComponent(screenToken)}`;
  closeWallSocket();
  let socket;
  try {
    socket = new WebSocket(url);
  } catch (err) {
    console.error(err);
    scheduleWallReconnect();
    return;
  }
  wallSocket = socket;
  ConnectionStatus.setWs("connecting");

  socket.addEventListener("open", () => {
    if (wallSocket !== socket) return;
    ConnectionStatus.setWs("open");
    ConnectionStatus.markFrame();
    wallReconnectAttempts = 0;
    wallLastMessageAt = Date.now();
    reportWallScreen();   // the phone shapes this TV's box from it
  });
  socket.addEventListener("message", (ev) => {
    if (wallSocket !== socket) return;
    wallLastMessageAt = Date.now();
    ConnectionStatus.markFrame();
    let frame;
    try { frame = JSON.parse(ev.data); } catch (_) { return; }
    onWallFrame(frame);
  });
  socket.addEventListener("close", (ev) => {
    if (wallSocket !== socket) return;   // replaced, or closed on purpose
    wallSocket = null;
    ConnectionStatus.setWs("closed");
    if (!wallId) return;
    setStatus(Khan.t("wall.reconnecting", "Reconnecting to wall…"));
    // 4401: the server no longer counts this screen in the wall — ask the API
    // what it is now instead of only knocking on the same door.
    if (ev.code === 4401) refreshSoon();
    scheduleWallReconnect();
  });
  socket.addEventListener("error", () => { /* close handler will retry */ });
}

function onWallFrame(frame) {
  const clientReceivedMs = Date.now();
  noteServerTime(frame.server_now_ms);
  if (frame.type === "hello") {
    // Spanned <-> mirrored rebuilds the picture: a cropped canvas and a plain
    // full-screen node are built differently. Anything else (a moved box, a
    // canvas of another size, another grid) only re-crops the picture that is
    // up, so a playing video keeps playing. renderWallPlay still remounts
    // when the item itself changed.
    const geometry = frame.mode === "spanned"
      ? { canvas: frame.canvas, cell_geometry: frame.cell_geometry }
      : null;
    const rebuild = (geometry === null) !== (wallSpannedGeometry === null);
    wallSpannedGeometry = geometry;
    applyWallTransform();
    if (frame.current_play) renderWallPlay(frame.current_play, rebuild);
    else clearWallPicture();
    showWallHighlight(frame.highlight_until_ms, frame.tv_number);
  } else if (frame.type === "geometry") {
    onWallGeometry(frame);
  } else if (frame.type === "play") {
    renderWallPlay(frame, false);
  } else if (frame.type === "ping") {
    try {
      if (wallSocket) wallSocket.send(JSON.stringify({
        type: "pong", client_received_ms: clientReceivedMs, client_now_ms: Date.now(),
      }));
    } catch (_) {}
  } else if (frame.type === "bye") {
    // This screen no longer exists as a wall cell: forget it, show a code.
    leaveMode();
    handleAuthFailure();
  } else if (frame.type === "wall_left") {
    // Still a screen, just not in this wall any more: play its own content.
    leaveMode();
    refreshSoon();
  } else if (frame.type === "playlist_change") {
    refreshSoon();
  }
}

/* Crops this TV's slice out of the wall canvas and scales it to fill the
   screen. With the origin at the canvas's top-left corner,
   scale(vw / cell.w, vh / cell.h) translate(-cell.x px, -cell.y px) maps the
   slice exactly onto the viewport. The canvas used to be translated only,
   which was right only when the slice happened to be the viewport's size. A
   free wall's box has the TV's own shape, so both scales are equal there; a
   grid cell of another shape is stretched to fill. Runs on hello, geometry,
   mount and window resize, and never touches the media element. */
function applyWallTransform() {
  const canvasEl = contentEl.querySelector(".wall-canvas");
  const g = wallSpannedGeometry;
  if (!canvasEl || !g || !g.canvas || !g.cell_geometry) return;
  const cw = Number(g.canvas.w);
  const ch = Number(g.canvas.h);
  const x = Number(g.cell_geometry.x) || 0;
  const y = Number(g.cell_geometry.y) || 0;
  const w = Number(g.cell_geometry.w);
  const h = Number(g.cell_geometry.h);
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const vh = document.documentElement.clientHeight || window.innerHeight;
  if (!(cw > 0 && ch > 0 && w > 0 && h > 0 && vw > 0 && vh > 0)) return;
  canvasEl.style.setProperty("--wall-w-px", String(cw));
  canvasEl.style.setProperty("--wall-h-px", String(ch));
  canvasEl.style.transform = "scale(" + (vw / w) + "," + (vh / h) + ") translate("
    + (-x) + "px," + (-y) + "px)";
}

/* A box moved on the phone, the TV reported another shape, or the wall got a
   video of another size: re-crop, and show or hide the outline. Only a hello
   switches between a cropped and a full-screen picture. The media element is
   left alone, so a playing video keeps playing. */
function onWallGeometry(frame) {
  if (wallSpannedGeometry && frame.canvas && frame.cell_geometry) {
    wallSpannedGeometry = { canvas: frame.canvas, cell_geometry: frame.cell_geometry };
    applyWallTransform();
  }
  showWallHighlight(frame.highlight_until_ms, frame.tv_number);
}

/* The outline and "TV 2" a phone asks for while it places this TV's box.
   untilMs is on the server's clock (highlight_until_ms). null, or a time
   already past, hides it at once. */
function showWallHighlight(untilMs, n) {
  const remainingMs = typeof untilMs === "number" ? untilMs - effectiveNowMs() : 0;
  if (!wallHighlightEl || !(remainingMs > 0)) {
    hideWallHighlight();
    return;
  }
  clearTimeout(wallHighlightTimer);
  wallHighlightTimer = setTimeout(hideWallHighlight, remainingMs);
  if (wallHighlightLabelEl) {
    wallHighlightLabelEl.textContent = typeof n === "number"
      ? Khan.t("wall.tv_label", "TV {n}").replace("{n}", String(n))
      : "";
  }
  wallHighlightEl.classList.remove("hidden");
}

function hideWallHighlight() {
  clearTimeout(wallHighlightTimer);
  wallHighlightTimer = null;
  if (wallHighlightEl) wallHighlightEl.classList.add("hidden");
}

/* This TV's screen size in CSS px, from which the phone gives the TV's box
   the TV's own shape. Sent when the socket opens and once the window has
   stopped resizing. */
function reportWallScreen() {
  const w = Math.round(document.documentElement.clientWidth || window.innerWidth || 0);
  const h = Math.round(document.documentElement.clientHeight || window.innerHeight || 0);
  if (!(w >= WALL_SCREEN_MIN_PX && h >= WALL_SCREEN_MIN_PX)) return;
  try {
    if (wallSocket && wallSocket.readyState === WebSocket.OPEN) {
      wallSocket.send(JSON.stringify({ type: "screen", w: w, h: h }));
    }
  } catch (_) {}
}

// A rotated tablet, a resized window, a WebView that settles late: re-crop at
// once, and report the new size when it has settled.
window.addEventListener("resize", () => {
  if (mode !== "wall") return;
  applyWallTransform();
  clearTimeout(wallScreenReportTimer);
  wallScreenReportTimer = setTimeout(() => {
    wallScreenReportTimer = null;
    reportWallScreen();
  }, WALL_SCREEN_REPORT_DEBOUNCE_MS);
});

function clearWallPicture() {
  stopWallDrift();
  clearContainer(contentEl);
  wallLastFrame = null;
  wallNode = null;
}

function stopWallDrift() {
  if (wallDriftTimer) {
    clearInterval(wallDriftTimer);
    wallDriftTimer = null;
  }
}

function rememberWallMedia(item) {
  if (!item || !item.url || item.mime_type === "text/url") return;
  wallSeen.delete(item.url);
  wallSeen.set(item.url, { url: item.url, mime_type: item.mime_type });
  while (wallSeen.size > WALL_SEEN_MAX) wallSeen.delete(wallSeen.keys().next().value);
  announceMedia(Array.from(wallSeen.values()));
}

function renderWallPlay(frame, force) {
  const item = frame && frame.item;
  if (!item) return;
  const sameItem = !force && wallLastFrame && wallNode && wallNode.isConnected
    && wallLastFrame.item.id === item.id && wallLastFrame.item.url === item.url
    && (wallLastFrame.fit_mode || "fit") === (frame.fit_mode || "fit");
  if (sameItem && wallLastFrame.started_at_ms === frame.started_at_ms) {
    return;   // the same frame again (hello after a reconnect, a duplicate)
  }
  wallLastFrame = frame;
  rememberWallMedia(item);
  if (sameItem) {
    // The same item for another slot (a one-item playlist cycling): keep the
    // picture up instead of flashing black, and restart a video in sync.
    if (wallNode.tagName === "VIDEO") syncWallVideo(wallNode);
    return;
  }
  stopWallDrift();
  clearContainer(contentEl);
  const node = createWallMediaNode(item, frame.fit_mode);
  if (wallSpannedGeometry) {
    const wrap = document.createElement("div");
    wrap.className = "cell-viewport";
    const canvasEl = document.createElement("div");
    canvasEl.className = "wall-canvas";
    wrap.appendChild(canvasEl);
    contentEl.appendChild(wrap);
    node.classList.add("wall-media");
    node.dataset.fit = frame.fit_mode || "fit";
    // Inline: "#content img { object-fit: contain }" outranks the
    // .wall-media[data-fit] rules, so fill/stretch never took effect.
    const fit = WALL_FIT[frame.fit_mode] || "contain";
    node.style.cssText = `position:absolute; top:0; left:0; width:100%; height:100%; object-fit:${fit};`;
    canvasEl.appendChild(node);
    applyWallTransform();   // canvas size and crop come from wallSpannedGeometry
  } else {
    contentEl.appendChild(node);
  }
  wallNode = node;
  if (node.tagName === "VIDEO") {
    node.addEventListener("loadeddata", () => syncWallVideo(node), { once: true });
    ensurePlaying(node);
  }
}

function syncWallVideo(node) {
  if (node !== wallNode || !wallLastFrame) return;
  const t = Math.max(0, (effectiveNowMs() - wallLastFrame.started_at_ms) / 1000);
  try { node.currentTime = t; } catch (_) {}
  ensurePlaying(node);
  stopWallDrift();
  wallDriftTimer = setInterval(() => correctVideoDrift(node), 2000);
}

const WALL_FIT = { fit: "contain", fill: "cover", stretch: "fill" };

function createWallMediaNode(item, fitMode) {
  const mime = item.mime_type || "";
  const url = resolveMediaUrl(item.url);
  const fit = WALL_FIT[fitMode] || "contain";
  const fill = `position:fixed;top:0;left:0;width:100%;height:100%;object-fit:${fit};background:#000;`;
  if (mime.startsWith("video/")) {
    const v = createVideoNode(url, false);
    v.style.cssText = `${fill}will-change:transform;transform:translateZ(0);`;
    return v;
  }
  if (mime.startsWith("image/") || (mime === "text/url" && isImageUrl(item.url))) {
    const i = document.createElement("img");
    i.src = url; i.alt = item.name || ""; i.decoding = "async";
    i.style.cssText = fill;
    return i;
  }
  const f = document.createElement("iframe");
  f.src = mime === "application/pdf" ? `${url}#toolbar=0&navpanes=0` : url;
  f.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;border:0;background:#000;";
  return f;
}

function correctVideoDrift(video) {
  if (!video.isConnected || video !== wallNode || !wallLastFrame) {
    stopWallDrift();
    return;
  }
  const expectedSec = (effectiveNowMs() - wallLastFrame.started_at_ms) / 1000;
  const delta = (video.currentTime - expectedSec) * 1000;
  if (Math.abs(delta) > 200) {
    try { video.currentTime = Math.max(0, expectedSec); } catch (_) {}
    video.playbackRate = 1.0;
  } else if (Math.abs(delta) > 50) {
    video.playbackRate = delta < 0 ? 1.02 : 0.98;
    setTimeout(() => { video.playbackRate = 1.0; }, 1000);
  }
  if (video.paused && !video.ended) ensurePlaying(video);
}

/* ── Updates ───────────────────────────────────────────────────────────
   TVs stay on for weeks without anyone touching them, so a deploy has to
   reach them by itself. The SW version is stamped from PLAYER_VERSION plus a
   hash of the shell files; a new one installs, takes over (skipWaiting +
   clients.claim) and this page reloads into it from the new SW's cache — so
   the reload works even offline. Guarded so it can never loop. */
function safeReload() {
  try {
    const last = Number(sessionStorage.getItem("khan_reload_at") || 0);
    if (Date.now() - last < RELOAD_GUARD_MS) {
      setTimeout(safeReload, RELOAD_GUARD_MS);
      return;
    }
    sessionStorage.setItem("khan_reload_at", String(Date.now()));
  } catch (_) {}
  window.location.reload();
}

// Without a service worker (insecure origin, blocked): compare the deployed
// sw.js version with the one seen at startup.
function startVersionPoll() {
  let seen = null;
  const check = async () => {
    try {
      const res = await apiFetch(`/sw.js?check=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) return;
      const match = /PLAYER_VERSION\s*=\s*"([^"]*)"/.exec(res.text);
      if (!match) return;
      if (seen === null) seen = match[1];
      else if (match[1] !== seen && navigator.onLine !== false) safeReload();
    } catch (_) {}
  };
  check();
  setInterval(check, UPDATE_CHECK_INTERVAL_MS);
}

function setupServiceWorker() {
  // The dashboard's preview iframe is not a TV: no offline cache, no pinned
  // media, no update reloads in the operator's browser.
  if (getParam("preview_token")) return;
  if (!("serviceWorker" in navigator)) {
    startVersionPoll();
    return;
  }
  let hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController) {
      // First install claiming this page: nothing to reload, but hand the
      // new worker what it should cache.
      hadController = true;
      if (lastManifestUrls.length) {
        postToServiceWorker({ type: "media-manifest", urls: lastManifestUrls });
      }
      return;
    }
    // The new worker navigates its pages itself (so pages of older players,
    // which have no handler like this one, update too). Reload only if that
    // has not happened within a few seconds.
    setTimeout(safeReload, CONTROLLER_RELOAD_DELAY_MS);
  });
  navigator.serviceWorker.register("/sw.js").then((reg) => {
    setInterval(() => { reg.update().catch(() => {}); }, UPDATE_CHECK_INTERVAL_MS);
  }).catch((err) => {
    console.warn("service worker unavailable", err);
    startVersionPoll();
  });
}

setupServiceWorker();
window.addEventListener("online", () => refreshSoon());

(async function bootI18nThenBoot() {
  try {
    const locale = Khan.detectInitialLocale();
    await Khan.loadLocale(locale);
    Khan.applyTranslations(document);
  } catch (err) {
    console.error("[i18n] boot failed", err);
  }
  try {
    await boot();
  } catch (err) {
    console.error(err);
    setStatus(Khan.t("status.failed_to_start", "Failed to start player"));
    // Never give up: try again as if freshly loaded.
    setTimeout(() => { boot().catch((e) => console.error(e)); }, 10000);
  }
})();

// ── "Have a code from admin?" affordance (Task 11) ───────────────
const adminCodeToggle = document.getElementById("admin-code-toggle");
const adminCodeForm = document.getElementById("admin-code-form");
const adminCodeInput = document.getElementById("admin-code-input");
const adminCodeError = document.getElementById("admin-code-error");

if (adminCodeToggle && adminCodeForm) {
  adminCodeToggle.addEventListener("click", () => {
    adminCodeForm.classList.toggle("hidden");
    if (!adminCodeForm.classList.contains("hidden")) {
      if (adminCodeInput) adminCodeInput.focus();
    }
  });
}
if (adminCodeForm) {
  adminCodeForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const submitBtn = adminCodeForm.querySelector('[type="submit"]');
    adminCodeError.textContent = "";
    const code = (adminCodeInput.value || "").trim().toUpperCase();
    if (code.length !== 6) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
      const res = await apiFetch(`${API_BASE}/walls/cells/redeem`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const body = await res.json();
      if (!res.ok) {
        const msgKey = body && body.detail && body.detail.code === "wall.pair_code_expired"
          ? "pairing.code_expired"
          : "pairing.code_invalid";
        adminCodeError.textContent = Khan.t(msgKey, "Code not recognized.");
        if (submitBtn) submitBtn.disabled = false;
        return;
      }
      try { localStorage.setItem("screen_token", body.screen_token); } catch (_) {}
      stopPairPoll();
      window.location.reload();
    } catch (err) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

// No language toggle on the player: a display screen shows content only, with
// no operator-facing chrome over it. Locale still comes from
// Khan.detectInitialLocale() -- the khanshoof_lang cookie, else the device's
// own browser language -- so an Arabic-configured TV still renders Arabic.
