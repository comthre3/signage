const urlOverrides = new URLSearchParams(window.location.search);
const API_BASE =
  urlOverrides.get("api_base") ||
  (window.API_BASE_URL || "").trim() ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");
const zonesEl = document.getElementById("zones");

const pairingEl = document.getElementById("pairing");
const pairingCodeEl = document.getElementById("pairing-code");
const pairingQrEl = document.getElementById("pairing-qr");
const pairingUrlEl = document.getElementById("pairing-url");
const pairingMetaEl = document.getElementById("pairing-meta");

const APP_URL = (window.APP_URL || "").trim() || "https://app.khanshoof.com";
const PAIR_POLL_INTERVAL_MS = 3000;

let activePairCode = null;
let pairPollTimer = null;

let screenToken = null;
let playbackTimer = null;
let currentIndex = 0;
let currentSignature = "";
let currentItems = [];
let previewToken = null;
let zoneTimers = [];
let layoutSignature = "";
const cachePrefix = "signage_cache_";

function setStatus(text) {
  statusEl.textContent = text;
}

function getParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function getCacheKey(type) {
  const key = previewToken ? `preview_${previewToken}` : `screen_${screenToken}`;
  return `${cachePrefix}${type}_${key}`;
}

async function pairWithCode(code) {
  const res = await fetch(`${API_BASE}/screens/pair`, {
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
  return items.map((item) => `${item.id}:${item.duration_seconds}`).join("|");
}

function clearPlayback() {
  if (playbackTimer) {
    clearTimeout(playbackTimer);
    playbackTimer = null;
  }
}

function clearZonePlayback() {
  zoneTimers.forEach((timer) => clearTimeout(timer));
  zoneTimers = [];
}

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
  setStatus(Khan.t("status.loading_content", "Loading content..."));
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

function mountMedia(container, node, enableFade, transitionMs = 600) {
  const previous = container.firstElementChild;
  if (enableFade) {
    node.classList.add("fade-media");
    node.style.transitionDuration = `${transitionMs}ms`;
  }
  const showNode = () => {
    container.appendChild(node);
    if (enableFade) {
      requestAnimationFrame(() => node.classList.add("visible"));
      if (previous) {
        setTimeout(() => previous.remove(), transitionMs + 50);
      }
    } else if (previous) {
      previous.remove();
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
    setTimeout(showNode, 1500);
  } else {
    showNode();
  }
}

function renderItem(item, durationMs) {
  const url = `${API_BASE}${item.url}`;
  let node = null;
  if (item.mime_type.startsWith("video")) {
    node = createVideoNode(url, durationMs > 0);
  } else if (item.mime_type.startsWith("image")) {
    node = document.createElement("img");
    node.src = url;
    node.decoding = "async";
  } else if (item.mime_type === "application/pdf") {
    node = document.createElement("iframe");
    node.src = `${url}#toolbar=0&navpanes=0`;
  } else if (item.mime_type === "text/url") {
    node = document.createElement("iframe");
    node.src = item.url;
  } else {
    node = document.createElement("div");
    node.textContent = `Unsupported media: ${item.name}`;
  }
  mountMedia(contentEl, node, durationMs > 0, 600);
}

function createVideoNode(url, loop) {
  const node = document.createElement("video");
  node.src = url;
  node.autoplay = true;
  node.muted = true;
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
  return node;
}

function scheduleNext() {
  if (currentItems.length === 0) {
    setStatus(Khan.t("status.no_content", "No content assigned"));
    return;
  }
  const item = currentItems[currentIndex];
  const durationMs = Number(item.duration_seconds ?? 10) * 1000;
  renderItem(item, durationMs);
  setStatus(`Playing ${item.name}`);
  clearPlayback();
  if (durationMs <= 0) {
    return;
  }
  playbackTimer = setTimeout(() => {
    currentIndex = (currentIndex + 1) % currentItems.length;
    scheduleNext();
  }, durationMs);
}

function startPlayback(items) {
  currentItems = items;
  currentIndex = 0;
  scheduleNext();
}

function renderZoneItem(container, item, durationMs, transitionMs) {
  const url = `${API_BASE}${item.url}`;
  let node = null;
  if (item.mime_type.startsWith("video")) {
    node = createVideoNode(url, durationMs > 0);
  } else if (item.mime_type.startsWith("image")) {
    node = document.createElement("img");
    node.src = url;
    node.decoding = "async";
  } else if (item.mime_type === "application/pdf") {
    node = document.createElement("iframe");
    node.src = `${url}#toolbar=0&navpanes=0`;
  } else if (item.mime_type === "text/url") {
    node = document.createElement("iframe");
    node.src = item.url;
  } else {
    node = document.createElement("div");
    node.textContent = `Unsupported media: ${item.name}`;
  }
  mountMedia(container, node, durationMs > 0, transitionMs);
}

function startZonePlayback(zoneIndex, zone, container) {
  if (!zone.items || zone.items.length === 0) {
    return;
  }
  let index = 0;
  const playNext = () => {
    const item = zone.items[index];
    const durationMs = Number(item.duration_seconds ?? 10) * 1000;
    const transitionMs = Number(zone.transition_ms ?? 600);
    renderZoneItem(container, item, durationMs, transitionMs);
    index = (index + 1) % zone.items.length;
    if (durationMs <= 0) {
      return;
    }
    const timer = setTimeout(playNext, durationMs);
    zoneTimers.push(timer);
  };
  playNext();
}

function renderZonesLayout(zones) {
  zonesEl.innerHTML = "";
  zonesEl.classList.remove("hidden");
  contentEl.classList.add("hidden");
  clearZonePlayback();
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
    startZonePlayback(index, zone, content);
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
      items: (zone.items || []).map((item) => ({
        id: item.id,
        media_id: item.media_id,
        duration_seconds: item.duration_seconds,
      })),
    }))
  );
}

function renderSingleLayout() {
  zonesEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  clearZonePlayback();
}
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

function renderContentData(data) {
  const items = data.items || [];
  if (items.length === 0) {
    currentSignature = "";
    currentItems = [];
    clearPlayback();
    contentEl.innerHTML = "";
    setStatus(Khan.t("status.no_content", "No content assigned"));
    return;
  }
  const signature = buildSignature(items);
  if (signature !== currentSignature) {
    currentSignature = signature;
    startPlayback(items);
  }
}

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

async function startPairingFlow() {
  showPairingView();
  stopPairPoll();
  try {
    const data = await requestPairingCode();
    activePairCode = data.code;
    renderPairingCode(data.code);
    pairingMetaEl.textContent = Khan.t("pairing.waiting", "Waiting for your phone…");
    schedulePairPoll();
  } catch (err) {
    console.error(err);
    pairingCodeEl.textContent = "—";
    pairingMetaEl.textContent = Khan.t("pairing.no_server", "Can't reach server. Retrying…");
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
          setStatus(Khan.t("status.connection_issue", "Connection issue"));
        });
    } else {
      fetchContent().catch((err) => {
        console.error(err);
        setStatus(Khan.t("status.connection_issue", "Connection issue"));
      });
    }
  }, 15000);
}

async function boot() {
  const tokenParam = getParam("token");
  const codeParam = getParam("code");
  const previewParam = getParam("preview_token");
  const isPreview = Boolean(previewParam);
  previewToken = previewParam;
  screenToken =
    tokenParam ||
    (isPreview ? null : localStorage.getItem("screen_token")) ||
    screenToken;

  if (!screenToken && codeParam) {
    setStatus(Khan.t("status.pairing", "Pairing..."));
    screenToken = await pairWithCode(codeParam);
    if (!isPreview) {
      localStorage.setItem("screen_token", screenToken);
    }
  }

  if (!screenToken && !previewToken) {
    await startPairingFlow();
    return;
  }

  setStatus(Khan.t("status.loading_content", "Loading content..."));
  const layout = await fetchLayout();
  if (layout?.zones && layout.zones.length > 0) {
    layoutSignature = getLayoutSignature(layout.zones);
    renderZonesLayout(layout.zones);
  } else {
    renderSingleLayout();
    await fetchContent();
  }
  startRefreshLoop();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

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
    adminCodeInput?.focus();
  });
}
if (adminCodeForm) {
  adminCodeForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    adminCodeError.textContent = "";
    const code = (adminCodeInput.value || "").trim().toUpperCase();
    if (code.length !== 6) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/walls/cells/redeem`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const body = await res.json();
      if (!res.ok) {
        const msgKey = body?.detail?.code === "wall.pair_code_expired"
          ? "pairing.code_expired"
          : "pairing.code_invalid";
        adminCodeError.textContent = Khan.t(msgKey, "Code not recognized.");
        return;
      }
      localStorage.setItem("screen_token", body.screen_token);
      stopPairPoll();
      window.location.reload();
    } catch (err) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
    }
  });
}
