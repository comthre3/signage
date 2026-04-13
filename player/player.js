const urlOverrides = new URLSearchParams(window.location.search);
const API_BASE =
  urlOverrides.get("api_base") ||
  (window.API_BASE_URL || "").trim() ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");
const zonesEl = document.getElementById("zones");

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
    node = document.createElement("video");
    node.src = url;
    node.autoplay = true;
    node.muted = true;
    node.playsInline = true;
    node.loop = durationMs > 0;
  } else if (item.mime_type.startsWith("image")) {
    node = document.createElement("img");
    node.src = url;
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

function scheduleNext() {
  if (currentItems.length === 0) {
    setStatus("No content assigned");
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
    node = document.createElement("video");
    node.src = url;
    node.autoplay = true;
    node.muted = true;
    node.playsInline = true;
    node.loop = durationMs > 0;
  } else if (item.mime_type.startsWith("image")) {
    node = document.createElement("img");
    node.src = url;
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
async function fetchContent() {
  if (!screenToken && !previewToken) return;
  const endpoint = previewToken
    ? `${API_BASE}/preview/${previewToken}/content`
    : `${API_BASE}/screens/${screenToken}/content`;
  const res = await fetch(endpoint);
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
    setStatus("No content assigned");
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
  if (!res.ok) {
    const cached = localStorage.getItem(getCacheKey("layout"));
    return cached ? JSON.parse(cached) : null;
  }
  const data = await res.json();
  localStorage.setItem(getCacheKey("layout"), JSON.stringify(data));
  return data;
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
    setStatus("Pairing...");
    screenToken = await pairWithCode(codeParam);
    if (!isPreview) {
      localStorage.setItem("screen_token", screenToken);
    }
  }

  if (!screenToken && !previewToken) {
    setStatus("Missing pairing code or token");
    return;
  }

  setStatus("Loading content...");
  const layout = await fetchLayout();
  if (layout?.zones && layout.zones.length > 0) {
    layoutSignature = getLayoutSignature(layout.zones);
    renderZonesLayout(layout.zones);
  } else {
    renderSingleLayout();
    await fetchContent();
  }
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

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

boot().catch((err) => {
  console.error(err);
  setStatus("Failed to start player");
});
