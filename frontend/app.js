const urlOverrides = new URLSearchParams(window.location.search);
const CONNECTION_STORAGE_KEY = "signage_connection_mode";
const CONNECTION_API_KEY     = "signage_connection_api";
const CONNECTION_PLAYER_KEY  = "signage_connection_player";

const savedMode       = localStorage.getItem(CONNECTION_STORAGE_KEY) || "local";
const savedApiBase    = localStorage.getItem(CONNECTION_API_KEY)    || "";
const savedPlayerBase = localStorage.getItem(CONNECTION_PLAYER_KEY) || "";

const API_BASE =
  urlOverrides.get("api_base") ||
  (savedMode === "external" ? savedApiBase : "") ||
  (window.API_BASE_URL || "").trim() ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

const PLAYER_BASE =
  urlOverrides.get("player_base") ||
  (savedMode === "external" ? savedPlayerBase : "") ||
  (window.PLAYER_BASE_URL || "").trim() ||
  `${window.location.protocol}//${window.location.hostname}:3001`;

const AUTH_STORAGE_KEY = "signage_auth_token";

let authToken   = localStorage.getItem(AUTH_STORAGE_KEY);
let currentUser = null;

/* ── Toast ───────────────────────────────────────────────────── */
function toast(message, type = "info", duration = 3500) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  const remove = () => {
    el.classList.add("toast-exit");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 300);
  };
  el.addEventListener("click", remove);
  setTimeout(remove, duration);
}

/* ── Loading state helper ────────────────────────────────────── */
function withLoading(btn, fn) {
  btn.classList.add("loading");
  btn.disabled = true;
  return Promise.resolve(fn()).finally(() => {
    btn.classList.remove("loading");
    btn.disabled = false;
  });
}

/* ── State ───────────────────────────────────────────────────── */
const state = {
  sites: [], screens: [], playlists: [], media: [], users: [], groups: [],
};

const zonesState = {
  screenId:    null,
  zones:       [],
  dragging:    null,
  drawing:     null,
  snapEnabled: true,
  gridStep:    0.05,
  screen:      null,
};

/* ── Hamburger toggle ─────────────────────────────────────────── */
document.getElementById("nav-toggle")?.addEventListener("click", () => {
  const row = document.getElementById("header-row");
  row?.classList.toggle("nav-open");
});

/* ── Navigation ──────────────────────────────────────────────── */
const navButtons = document.querySelectorAll("nav button[data-section]");
navButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    showSection(btn.dataset.section);
    document.getElementById("header-row")?.classList.remove("nav-open");
  });
});

function showSection(id) {
  document.querySelectorAll("#dashboard > section.panel, #dashboard > div").forEach((el) => {
    el.classList.toggle("hidden", el.id !== id);
  });
  navButtons.forEach((btn) => {
    btn.classList.toggle("nav-active", btn.dataset.section === id);
  });
}

function buildPlayerUrl(base, params) {
  const url = new URL(base, window.location.origin);
  Object.entries(params).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); });
  return url.toString();
}

function getPlayerBaseWithOverrides() {
  const base = PLAYER_BASE || `${window.location.protocol}//${window.location.hostname}:3001`;
  if (savedMode === "external" && savedApiBase && !savedPlayerBase) {
    return buildPlayerUrl(base, { api_base: savedApiBase });
  }
  return base;
}

function getScreenResolutionInput() {
  const select      = document.getElementById("screen-resolution");
  const customInput = document.getElementById("screen-resolution-custom");
  if (!select) return null;
  if (select.value === "custom") return customInput?.value.trim() || null;
  return select.value || null;
}

function updateResolutionCustomVisibility() {
  const select      = document.getElementById("screen-resolution");
  const customInput = document.getElementById("screen-resolution-custom");
  if (!select || !customInput) return;
  const showCustom = select.value === "custom";
  customInput.classList.toggle("hidden", !showCustom);
  if (!showCustom) customInput.value = "";
}

/* ── API ─────────────────────────────────────────────────────── */
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, ...options });
  if (!res.ok) {
    if (res.status === 401) handleAuthFailure();
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    const msg = (data && typeof data === "object" && typeof data.detail === "string")
      ? data.detail
      : (text || "Request failed");
    const err = new Error(msg);
    err.status = res.status;
    err.data   = data;
    throw err;
  }
  return res.json();
}

/* ── Auth ────────────────────────────────────────────────────── */
function setAuth(token, user) {
  authToken   = token;
  currentUser = user;
  token ? localStorage.setItem(AUTH_STORAGE_KEY, token)
        : localStorage.removeItem(AUTH_STORAGE_KEY);
  updateAuthUI();
}

function handleAuthFailure() {
  setAuth(null, null);
  showAuthPanel();
}

function updateAuthUI() {
  const authUser  = document.getElementById("auth-user");
  const logoutBtn = document.getElementById("logout-btn");
  const nav       = document.querySelector("header nav");
  const usersBtn  = document.querySelector('button[data-section="users"]');
  if (currentUser) {
    authUser.textContent = `${currentUser.username} · ${currentUser.role}`;
    logoutBtn.classList.remove("hidden");
    nav.classList.remove("hidden");
    if (usersBtn) usersBtn.classList.toggle("hidden", currentUser.role !== "admin");
  } else {
    authUser.textContent = "";
    logoutBtn.classList.add("hidden");
    nav.classList.add("hidden");
    if (usersBtn) usersBtn.classList.add("hidden");
  }
}

function showAuthPanel() {
  document.getElementById("auth-panel").classList.remove("hidden");
  document.getElementById("dashboard").classList.add("hidden");
}

function showDashboard() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
}

/* ── Data loaders ────────────────────────────────────────────── */
async function loadSites() {
  state.sites = await api("/sites");
  renderSites();
  renderSiteOptions();
}

async function loadScreens() {
  state.screens = await api("/screens");
  renderScreens();
}

async function loadPlaylists() {
  state.playlists = await api("/playlists");
  renderPlaylists();
  renderPlaylistOptions();
  renderPlaylistSelect();
}

async function loadMedia() {
  state.media = await api("/media");
  renderMedia();
  renderMediaOptions();
}

async function loadUsers() {
  if (currentUser?.role !== "admin") {
    state.users  = [];
    state.groups = [];
    renderUsers();
    renderGroups();
    return;
  }
  [state.users, state.groups] = await Promise.all([api("/users"), api("/groups")]);
  renderUsers();
  renderGroups();
}

/* ── Renderers ───────────────────────────────────────────────── */
function renderSites() {
  const container = document.getElementById("sites-list");
  container.innerHTML = "";
  state.sites.forEach((site) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escHtml(site.name)}</h3>
      <div>Slug: <span style="font-family:var(--mono)">${escHtml(site.slug)}</span></div>
      <div class="card-actions">
        <button class="delete-btn" data-id="${site.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async (e) => {
      if (!confirm(`Delete site "${site.name}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/sites/${site.id}`, { method: "DELETE" });
        toast("Site deleted.", "success");
        await loadSites();
        await loadScreens();
      });
    });
    container.appendChild(card);
  });
}

function renderSiteOptions() {
  const select = document.getElementById("screen-site");
  select.innerHTML = `<option value="">Site</option>`;
  state.sites.forEach((site) => {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.name;
    select.appendChild(option);
  });
}

function renderPlaylistOptions() {
  document.querySelectorAll(".playlist-option").forEach((el) => el.remove());
}

function renderPlaylists() {
  const container = document.getElementById("playlists-list");
  container.innerHTML = "";
  state.playlists.forEach((playlist) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escHtml(playlist.name)}</h3>
      <div class="card-actions">
        <button class="delete-btn" data-id="${playlist.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async (e) => {
      if (!confirm(`Delete playlist "${playlist.name}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/playlists/${playlist.id}`, { method: "DELETE" });
        toast("Playlist deleted.", "success");
        await loadPlaylists();
        await loadScreens();
      });
    });
    container.appendChild(card);
  });
}

function renderPlaylistSelect() {
  const select = document.getElementById("playlist-select");
  select.innerHTML = `<option value="">Select playlist</option>`;
  state.playlists.forEach((playlist) => {
    const option = document.createElement("option");
    option.value = playlist.id;
    option.textContent = playlist.name;
    select.appendChild(option);
  });
}

function renderMediaOptions() {
  const select = document.getElementById("playlist-media");
  select.innerHTML = `<option value="">Select media</option>`;
  state.media.forEach((media) => {
    const option = document.createElement("option");
    option.value = media.id;
    option.textContent = media.name;
    select.appendChild(option);
  });
}

function renderMedia() {
  const container = document.getElementById("media-list");
  container.innerHTML = "";
  state.media.forEach((item) => {
    const card = document.createElement("div");
    card.className = "card";
    const isUrl = item.mime_type === "text/url";
    const url   = isUrl ? item.url : `${API_BASE}${item.url}`;
    const typeLabel = isUrl ? "Website URL" : item.mime_type;
    card.innerHTML = `
      <h3>${escHtml(item.name)}</h3>
      <div class="card-meta">
        <span>${escHtml(typeLabel)}</span>
        ${!isUrl && item.size ? `<span>${formatBytes(item.size)}</span>` : ""}
      </div>
      <div><a href="${escAttr(url)}" target="_blank" rel="noreferrer">Open ↗</a></div>
      <div class="card-actions">
        <button class="delete-btn" data-id="${item.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async (e) => {
      if (!confirm(`Delete "${item.name}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/media/${item.id}`, { method: "DELETE" });
        toast("Media deleted.", "success");
        await loadMedia();
      });
    });
    container.appendChild(card);
  });
}

function renderUsers() {
  const container = document.getElementById("users-list");
  container.innerHTML = "";
  if (currentUser?.role !== "admin") {
    container.innerHTML = "<div class='card'>Admin access required to manage users.</div>";
    return;
  }
  state.users.forEach((user) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escHtml(user.username)}</h3>
      <div class="card-meta"><span>Role: ${escHtml(user.role)}</span></div>
      <div class="card-actions">
        <input type="password" placeholder="New password" data-user-pass="${user.id}" style="min-width:140px" />
        <button class="save-btn"   data-user-reset="${user.id}">Reset Password</button>
        <select data-user-role="${user.id}">
          <option value="viewer">Viewer</option>
          <option value="editor">Editor</option>
          <option value="admin">Admin</option>
        </select>
        <button class="save-btn"   data-user-role-save="${user.id}">Update Role</button>
        <button class="delete-btn" data-user-delete="${user.id}">Delete</button>
      </div>
      <div class="card-actions" data-user-groups="${user.id}"></div>
    `;
    card.querySelector(`[data-user-reset="${user.id}"]`).addEventListener("click", async (e) => {
      const input    = card.querySelector(`[data-user-pass="${user.id}"]`);
      const password = input.value.trim();
      if (!password) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/users/${user.id}`, { method: "PUT", body: JSON.stringify({ password }) });
        input.value = "";
        toast("Password updated.", "success");
      });
    });
    card.querySelector(`[data-user-delete="${user.id}"]`).addEventListener("click", async (e) => {
      if (!confirm(`Delete user "${user.username}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/users/${user.id}`, { method: "DELETE" });
        toast("User deleted.", "success");
        await loadUsers();
      });
    });
    const roleSelect = card.querySelector(`[data-user-role="${user.id}"]`);
    roleSelect.value = user.role;
    card.querySelector(`[data-user-role-save="${user.id}"]`).addEventListener("click", async (e) => {
      await withLoading(e.currentTarget, async () => {
        await api(`/users/${user.id}`, { method: "PUT", body: JSON.stringify({ role: roleSelect.value }) });
        toast("Role updated.", "success");
        await loadUsers();
      });
    });
    const groupsContainer = card.querySelector(`[data-user-groups="${user.id}"]`);
    state.groups.forEach((group) => {
      const label = document.createElement("label");
      label.className = "group-chip";
      label.innerHTML = `<input type="checkbox" data-user-group="${user.id}:${group.id}" /><span>${escHtml(group.name)}</span>`;
      groupsContainer.appendChild(label);
    });
    api(`/users/${user.id}/groups`).then((data) => {
      const groupIds = new Set((data.groups || []).map((g) => g.id));
      groupsContainer.querySelectorAll("[data-user-group]").forEach((input) => {
        const [, groupId] = input.dataset.userGroup.split(":").map(Number);
        input.checked = groupIds.has(groupId);
      });
    }).catch(() => {});
    card.querySelectorAll("[data-user-group]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const [userId] = checkbox.dataset.userGroup.split(":").map(Number);
        const selected = Array.from(card.querySelectorAll("[data-user-group]:checked"))
          .map((input) => Number(input.dataset.userGroup.split(":")[1]));
        await api(`/users/${userId}/groups`, { method: "PUT", body: JSON.stringify({ group_ids: selected }) });
      });
    });
    container.appendChild(card);
  });
}

function renderGroups() {
  const container = document.getElementById("groups-list");
  container.innerHTML = "";
  if (currentUser?.role !== "admin") return;
  if (state.groups.length) {
    const heading = document.createElement("h3");
    heading.textContent = "Groups";
    heading.style.cssText = "font-size:14px;font-weight:700;color:var(--cyan);font-family:var(--mono);margin-bottom:10px";
    container.appendChild(heading);
  }
  state.groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escHtml(group.name)}</h3>
      <div class="card-actions">
        <button class="delete-btn" data-group-delete="${group.id}">Delete</button>
      </div>
    `;
    card.querySelector(`[data-group-delete="${group.id}"]`).addEventListener("click", async (e) => {
      if (!confirm(`Delete group "${group.name}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/groups/${group.id}`, { method: "DELETE" });
        toast("Group deleted.", "success");
        await loadUsers();
      });
    });
    container.appendChild(card);
  });
}

function renderScreens() {
  const container = document.getElementById("screens-list");
  container.innerHTML = "";
  state.screens.forEach((screen) => {
    const card = document.createElement("div");
    card.className = "card";
    const base      = getPlayerBaseWithOverrides();
    const playerUrl = base.includes("?") ? `${base}&code=${screen.pair_code}` : `${base}/?code=${screen.pair_code}`;
    const statusHtml = screen.is_online
      ? `<span class="status-online">Online</span>`
      : `<span class="status-offline">Offline</span>`;
    const playlistOptions = [
      `<option value="">No playlist</option>`,
      ...state.playlists.map((p) => `<option value="${p.id}">${escHtml(p.name)}</option>`),
    ].join("");

    card.innerHTML = `
      <h3>${escHtml(screen.name)}</h3>
      <div class="card-meta">
        <span>Site: ${escHtml(screen.site_name || "Unassigned")}</span>
        ${screen.location ? `<span>${escHtml(screen.location)}</span>` : ""}
        ${screen.resolution ? `<span>${escHtml(screen.resolution)}</span>` : ""}
        ${screen.orientation ? `<span>${escHtml(screen.orientation)}</span>` : ""}
        <span>Status: ${statusHtml}</span>
      </div>
      <div>Pair code: <strong class="pair-code">${escHtml(screen.pair_code)}</strong></div>
      <div><a href="${escAttr(playerUrl)}" target="_blank" rel="noreferrer">Player URL ↗</a></div>
      <div class="card-actions">
        <select data-playlist-select="${screen.id}">${playlistOptions}</select>
        <button class="save-btn"    data-save-screen="${screen.id}">Save</button>
        <button class="save-btn"    data-zones-screen="${screen.id}">Zones</button>
        <button class="access-btn"  data-access-screen="${screen.id}">Access</button>
        <button class="preview-btn" data-preview-screen="${screen.id}">Preview</button>
        <button class="delete-btn"  data-delete-screen="${screen.id}">Delete</button>
      </div>
    `;

    const select = card.querySelector(`[data-playlist-select="${screen.id}"]`);
    select.value = screen.playlist_id || "";

    card.querySelector(`[data-save-screen="${screen.id}"]`).addEventListener("click", async (e) => {
      await withLoading(e.currentTarget, async () => {
        const playlistId = select.value ? Number(select.value) : null;
        await api(`/screens/${screen.id}`, { method: "PUT", body: JSON.stringify({ playlist_id: playlistId }) });
        toast("Screen saved.", "success");
        await loadScreens();
      });
    });

    card.querySelector(`[data-zones-screen="${screen.id}"]`).addEventListener("click", async () => {
      await openZonesEditor(screen.id);
    });

    card.querySelector(`[data-access-screen="${screen.id}"]`).addEventListener("click", async () => {
      await openScreenAccessEditor(screen.id);
    });

    card.querySelector(`[data-preview-screen="${screen.id}"]`).addEventListener("click", async (e) => {
      await withLoading(e.currentTarget, async () => {
        const preview = await api(`/screens/${screen.id}/preview-token`, { method: "POST" });
        const base    = getPlayerBaseWithOverrides();
        const url     = base.includes("?") ? `${base}&preview_token=${preview.token}` : `${base}/?preview_token=${preview.token}`;
        showPreview(screen, url, preview.expires_at);
      });
    });

    card.querySelector(`[data-delete-screen="${screen.id}"]`).addEventListener("click", async (e) => {
      if (!confirm(`Delete screen "${screen.name}"?`)) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/screens/${screen.id}`, { method: "DELETE" });
        toast("Screen deleted.", "success");
        await loadScreens();
      });
    });

    container.appendChild(card);
  });
}

function showPreview(screen, previewUrl, expiresAt) {
  const panel = document.getElementById("preview-panel");
  const frame = document.getElementById("preview-frame");
  const meta  = document.getElementById("preview-meta");
  meta.textContent = `Previewing: ${screen.name} (${screen.site_name || "Unassigned"})` +
    (expiresAt ? ` · expires ${expiresAt}` : "");
  frame.src = previewUrl;
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ── Zones ────────────────────────────────────────────────────── */
function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
function snapValue(value) {
  if (!zonesState.snapEnabled) return value;
  const step = zonesState.gridStep;
  return Math.round(value / step) * step;
}
function normalizeZone(zone) {
  return { ...zone, x: clamp(zone.x, 0, 1), y: clamp(zone.y, 0, 1), width: clamp(zone.width, 0.1, 1), height: clamp(zone.height, 0.1, 1) };
}
function setZones(zones) {
  zonesState.zones = zones.map((zone, index) => ({
    id: zone.id || `local-${index}`,
    name: zone.name || `Zone ${index + 1}`,
    x: zone.x, y: zone.y, width: zone.width, height: zone.height,
    sort_order: zone.sort_order ?? index,
    transition_ms: zone.transition_ms ?? 600,
    items: zone.items || [],
  }));
  renderZonesCanvas();
  renderZonesList();
}

function renderZonesCanvas() {
  const canvas = document.getElementById("zones-canvas");
  if (!canvas) return;
  canvas.classList.toggle("zones-canvas-grid", zonesState.snapEnabled);
  canvas.style.backgroundSize = `${zonesState.gridStep * 100}% ${zonesState.gridStep * 100}%`;
  canvas.innerHTML = "";
  const guideV = document.createElement("div"); guideV.className = "zone-guide vertical hidden";
  const guideH = document.createElement("div"); guideH.className = "zone-guide horizontal hidden";
  canvas.appendChild(guideV);
  canvas.appendChild(guideH);
  zonesState.zones.forEach((zone, index) => {
    const zoneEl = document.createElement("div");
    zoneEl.className = "zone-block";
    zoneEl.style.left   = `${zone.x * 100}%`;
    zoneEl.style.top    = `${zone.y * 100}%`;
    zoneEl.style.width  = `${zone.width * 100}%`;
    zoneEl.style.height = `${zone.height * 100}%`;
    zoneEl.dataset.zoneIndex = index;
    zoneEl.innerHTML = `
      <div class="zone-title">${escHtml(zone.name)}</div>
      <div class="zone-handle zone-handle-left"   data-handle="left"></div>
      <div class="zone-handle zone-handle-right"  data-handle="right"></div>
      <div class="zone-handle zone-handle-top"    data-handle="top"></div>
      <div class="zone-handle zone-handle-bottom" data-handle="bottom"></div>
    `;
    canvas.appendChild(zoneEl);
  });
  if (zonesState.drawing?.preview) {
    const preview = document.createElement("div");
    preview.className = "zone-block zone-preview";
    preview.style.left   = `${zonesState.drawing.preview.x * 100}%`;
    preview.style.top    = `${zonesState.drawing.preview.y * 100}%`;
    preview.style.width  = `${zonesState.drawing.preview.width * 100}%`;
    preview.style.height = `${zonesState.drawing.preview.height * 100}%`;
    preview.innerHTML = `<div class="zone-title">New Zone</div>`;
    canvas.appendChild(preview);
  }
}

function renderZonesList() {
  const list = document.getElementById("zones-list");
  if (!list) return;
  list.innerHTML = "";
  zonesState.zones.forEach((zone, zoneIndex) => {
    const card = document.createElement("div");
    card.className = "card";
    const itemsHtml = zone.items?.map((item, itemIndex) => `
      <div class="zone-item">
        <span>${escHtml(item.name || "Media")}</span>
        <input type="number" min="0" max="3600" value="${item.duration_seconds ?? 10}" data-zone-item-duration="${zoneIndex}:${itemIndex}" />
        <button class="delete-btn" data-zone-item-remove="${zoneIndex}:${itemIndex}">✕</button>
      </div>
    `).join("") || "<div class='helper-text'>No media yet.</div>";

    const mediaOptions = state.media.map((m) => `<option value="${m.id}">${escHtml(m.name)}</option>`).join("");

    card.innerHTML = `
      <div class="zone-meta">
        <strong>${escHtml(zone.name)}</strong>
        <span>${Math.round(zone.width * 100)}% × ${Math.round(zone.height * 100)}%</span>
      </div>
      <div class="zone-transition">
        <label>Fade (ms)</label>
        <input type="number" min="0" max="5000" value="${zone.transition_ms ?? 600}" data-zone-transition="${zoneIndex}" />
      </div>
      <div class="zone-actions">
        <button class="delete-btn" data-zone-remove="${zoneIndex}">Delete Zone</button>
      </div>
      <div class="zone-add">
        <select data-zone-media="${zoneIndex}"><option value="">Add media…</option>${mediaOptions}</select>
        <input type="number" min="0" max="3600" value="10" data-zone-duration="${zoneIndex}" style="width:70px" />
        <button class="save-btn" data-zone-add="${zoneIndex}">Add</button>
      </div>
      <div class="zone-items">${itemsHtml}</div>
    `;

    card.querySelector(`[data-zone-add="${zoneIndex}"]`).addEventListener("click", () => {
      const mediaSelect   = card.querySelector(`[data-zone-media="${zoneIndex}"]`);
      const durationInput = card.querySelector(`[data-zone-duration="${zoneIndex}"]`);
      const mediaId = Number(mediaSelect.value || 0);
      if (!mediaId) return;
      const media = state.media.find((item) => item.id === mediaId);
      zone.items.push({ media_id: mediaId, name: media?.name, duration_seconds: normalizeDuration(durationInput.value, 10) });
      renderZonesList();
    });
    card.querySelectorAll("[data-zone-item-remove]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [zIndex, iIndex] = btn.dataset.zoneItemRemove.split(":").map(Number);
        zonesState.zones[zIndex].items.splice(iIndex, 1);
        renderZonesList();
      });
    });
    card.querySelectorAll("[data-zone-item-duration]").forEach((input) => {
      input.addEventListener("change", () => {
        const [zIndex, iIndex] = input.dataset.zoneItemDuration.split(":").map(Number);
        zonesState.zones[zIndex].items[iIndex].duration_seconds = normalizeDuration(input.value, 10);
      });
    });
    card.querySelector(`[data-zone-remove="${zoneIndex}"]`).addEventListener("click", () => {
      zonesState.zones.splice(zoneIndex, 1);
      renderZonesCanvas();
      renderZonesList();
    });
    card.querySelector(`[data-zone-transition="${zoneIndex}"]`).addEventListener("change", (e) => {
      zonesState.zones[zoneIndex].transition_ms = normalizeDuration(e.target.value, 600);
    });
    list.appendChild(card);
  });
}

function normalizeDuration(value, fallback = 10) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function presetZones(type) {
  if (type === "single")     setZones([{ name: "Full",   x: 0,    y: 0, width: 1,    height: 1,   items: [] }]);
  if (type === "columns-2")  setZones([{ name: "Left",   x: 0,    y: 0, width: 0.5,  height: 1,   items: [] },
                                        { name: "Right",  x: 0.5,  y: 0, width: 0.5,  height: 1,   items: [] }]);
  if (type === "columns-3")  setZones([{ name: "Left",   x: 0,    y: 0, width: 0.33, height: 1,   items: [] },
                                        { name: "Center", x: 0.33, y: 0, width: 0.34, height: 1,   items: [] },
                                        { name: "Right",  x: 0.67, y: 0, width: 0.33, height: 1,   items: [] }]);
  if (type === "rows-2")     setZones([{ name: "Top",    x: 0,    y: 0, width: 1,    height: 0.5, items: [] },
                                        { name: "Bottom", x: 0,    y: 0.5, width: 1,  height: 0.5, items: [] }]);
  if (type === "hero-side")  setZones([{ name: "Hero",   x: 0,    y: 0, width: 0.7,  height: 1,   items: [] },
                                        { name: "Side",   x: 0.7,  y: 0, width: 0.3,  height: 1,   items: [] }]);
}

async function openZonesEditor(screenId) {
  zonesState.screenId = screenId;
  zonesState.screen   = state.screens.find((s) => s.id === screenId) || null;
  const zonesPanel    = document.getElementById("zones-editor");
  zonesPanel.classList.remove("hidden");
  const snapToggle = document.getElementById("zone-snap");
  if (snapToggle) snapToggle.checked = zonesState.snapEnabled;
  const gridSelect = document.getElementById("zone-grid");
  if (gridSelect) gridSelect.value = String(zonesState.gridStep);
  const data = await api(`/screens/${screenId}/zones`);
  if (!data.zones || data.zones.length === 0) presetZones("columns-2");
  else setZones(data.zones);
  await loadZoneTemplates();
  zonesPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeZonesEditor() {
  document.getElementById("zones-editor").classList.add("hidden");
}

async function openScreenAccessEditor(screenId) {
  const screen    = state.screens.find((item) => item.id === screenId);
  const panel     = document.getElementById("screen-access-panel");
  panel.classList.remove("hidden");
  const ownerSelect = document.getElementById("screen-owner-select");
  ownerSelect.innerHTML = `<option value="">Unassigned</option>`;
  state.users.forEach((user) => {
    const option = document.createElement("option");
    option.value = user.id;
    option.textContent = user.username;
    ownerSelect.appendChild(option);
  });
  ownerSelect.value = screen?.owner_user_id || "";

  const groupsList = document.getElementById("screen-groups-list");
  groupsList.innerHTML = "";
  state.groups.forEach((group) => {
    const label = document.createElement("label");
    label.className = "group-chip";
    label.innerHTML = `<input type="checkbox" data-screen-group="${screenId}:${group.id}" /><span>${escHtml(group.name)}</span>`;
    groupsList.appendChild(label);
  });

  const currentGroups = await api(`/screens/${screenId}/groups`);
  const currentIds    = new Set(currentGroups.groups.map((g) => g.id));
  groupsList.querySelectorAll("[data-screen-group]").forEach((input) => {
    const [, groupId] = input.dataset.screenGroup.split(":").map(Number);
    input.checked = currentIds.has(groupId);
  });

  document.getElementById("screen-access-save").onclick = async (e) => {
    await withLoading(e.currentTarget, async () => {
      const ownerId = ownerSelect.value ? Number(ownerSelect.value) : null;
      await api(`/screens/${screenId}`, { method: "PUT", body: JSON.stringify({ owner_user_id: ownerId }) });
      const selected = Array.from(groupsList.querySelectorAll("[data-screen-group]:checked"))
        .map((input) => Number(input.dataset.screenGroup.split(":")[1]));
      await api(`/screens/${screenId}/groups`, { method: "PUT", body: JSON.stringify({ group_ids: selected }) });
      toast("Access settings saved.", "success");
      panel.classList.add("hidden");
      await loadScreens();
    });
  };
  document.getElementById("screen-access-cancel").onclick = () => panel.classList.add("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function loadZoneTemplates() {
  const select = document.getElementById("zone-template-select");
  if (!select) return;
  select.innerHTML = `<option value="">Apply template…</option>`;
  const templates = await api("/zone-templates");
  templates.forEach((t) => {
    const option = document.createElement("option");
    option.value = t.id;
    option.textContent = t.name;
    select.appendChild(option);
  });
}

function bindZoneEditorEvents() {
  document.getElementById("zone-add")?.addEventListener("click", () => {
    const index = zonesState.zones.length + 1;
    zonesState.zones.push({ id: `local-${Date.now()}`, name: `Zone ${index}`, x: 0.1, y: 0.1, width: 0.3, height: 0.3, sort_order: index, transition_ms: 600, items: [] });
    renderZonesCanvas();
    renderZonesList();
  });
  document.getElementById("zone-snap")?.addEventListener("change", (e) => {
    zonesState.snapEnabled = e.target.checked;
    renderZonesCanvas();
  });
  document.getElementById("zone-grid")?.addEventListener("change", (e) => {
    zonesState.gridStep = Number(e.target.value || 0.05);
    renderZonesCanvas();
  });
  document.getElementById("zone-template-save")?.addEventListener("click", async (e) => {
    const name = document.getElementById("zone-template-name").value.trim();
    if (!name) return;
    await withLoading(e.currentTarget, async () => {
      await api("/zone-templates", {
        method: "POST",
        body: JSON.stringify({
          name, site_id: zonesState.screen?.site_id || null,
          zones: zonesState.zones.map((zone, index) => ({
            name: zone.name, x: zone.x, y: zone.y, width: zone.width, height: zone.height,
            sort_order: zone.sort_order ?? index,
            transition_ms: normalizeDuration(zone.transition_ms ?? 600, 600),
            items: zone.items?.map((item) => ({ media_id: item.media_id, duration_seconds: normalizeDuration(item.duration_seconds ?? 10, 10) })),
          })),
        }),
      });
      document.getElementById("zone-template-name").value = "";
      toast("Template saved.", "success");
      await loadZoneTemplates();
    });
  });
  document.getElementById("zone-template-apply")?.addEventListener("click", async (e) => {
    const templateId = Number(document.getElementById("zone-template-select").value || 0);
    if (!templateId || !zonesState.screenId) return;
    await withLoading(e.currentTarget, async () => {
      await api(`/screens/${zonesState.screenId}/zone-templates/apply`, { method: "POST", body: JSON.stringify({ template_id: templateId }) });
      const data = await api(`/screens/${zonesState.screenId}/zones`);
      setZones(data.zones || []);
      toast("Template applied.", "success");
    });
  });
  document.querySelectorAll("[data-zone-preset]").forEach((btn) => {
    btn.addEventListener("click", () => presetZones(btn.dataset.zonePreset));
  });
  document.getElementById("zones-save")?.addEventListener("click", async (e) => {
    if (!zonesState.screenId) return;
    await withLoading(e.currentTarget, async () => {
      await api(`/screens/${zonesState.screenId}/zones`, {
        method: "PUT",
        body: JSON.stringify({
          zones: zonesState.zones.map((zone, index) => ({
            name: zone.name, x: zone.x, y: zone.y, width: zone.width, height: zone.height,
            sort_order: zone.sort_order ?? index,
            transition_ms: normalizeDuration(zone.transition_ms ?? 600, 600),
            items: zone.items?.map((item) => ({ media_id: item.media_id, duration_seconds: normalizeDuration(item.duration_seconds ?? 10, 10) })),
          })),
        }),
      });
      toast("Zones saved.", "success");
    });
  });
  document.getElementById("zones-cancel")?.addEventListener("click", closeZonesEditor);

  const canvas = document.getElementById("zones-canvas");
  canvas?.addEventListener("mousedown", (event) => {
    const handle  = event.target.closest(".zone-handle");
    const zoneEl  = event.target.closest(".zone-block");
    if (!zoneEl) return;
    const zoneIndex = Number(zoneEl.dataset.zoneIndex);
    if (!Number.isFinite(zoneIndex)) return;
    const canvasRect = canvas.getBoundingClientRect();
    const original   = { ...zonesState.zones[zoneIndex] };
    zonesState.dragging = { zoneIndex, handle: handle ? handle.dataset.handle : "move", startX: event.clientX, startY: event.clientY, original, canvasRect };
  });
  canvas?.addEventListener("mousedown", (event) => {
    if (event.target.closest(".zone-block")) return;
    const canvasRect = canvas.getBoundingClientRect();
    const startX = clamp((event.clientX - canvasRect.left) / canvasRect.width, 0, 1);
    const startY = clamp((event.clientY - canvasRect.top)  / canvasRect.height, 0, 1);
    zonesState.drawing = { startX, startY, canvasRect };
  });
  window.addEventListener("mousemove", (event) => {
    if (!zonesState.dragging) return;
    const { zoneIndex, handle, startX, startY, original, canvasRect } = zonesState.dragging;
    const deltaX = (event.clientX - startX) / canvasRect.width;
    const deltaY = (event.clientY - startY) / canvasRect.height;
    const zone   = zonesState.zones[zoneIndex];
    const guidesV = canvas.querySelector(".zone-guide.vertical");
    const guidesH = canvas.querySelector(".zone-guide.horizontal");
    if (guidesV && guidesH) { guidesV.classList.add("hidden"); guidesH.classList.add("hidden"); }
    if (handle === "move") {
      zone.x = snapValue(clamp(original.x + deltaX, 0, 1 - original.width));
      zone.y = snapValue(clamp(original.y + deltaY, 0, 1 - original.height));
    }
    if (handle === "right")  zone.width  = snapValue(Math.max(0.1, Math.min(1 - original.x, original.width + deltaX)));
    if (handle === "left") {
      const nextX = snapValue(Math.max(0, Math.min(original.x + deltaX, original.x + original.width - 0.1)));
      zone.width = snapValue(original.width + (original.x - nextX));
      zone.x     = nextX;
    }
    if (handle === "bottom") zone.height = snapValue(Math.max(0.1, Math.min(1 - original.y, original.height + deltaY)));
    if (handle === "top") {
      const nextY = snapValue(Math.max(0, Math.min(original.y + deltaY, original.y + original.height - 0.1)));
      zone.height = snapValue(original.height + (original.y - nextY));
      zone.y      = nextY;
    }
    if (guidesV && guidesH) {
      const edgesX  = [0, 1, zone.x, zone.x + zone.width];
      const edgesY  = [0, 1, zone.y, zone.y + zone.height];
      const nearX   = edgesX.find((v) => Math.abs(v - Math.round(v)) < 0.01);
      const nearY   = edgesY.find((v) => Math.abs(v - Math.round(v)) < 0.01);
      if (nearX !== undefined) { guidesV.style.left = `${nearX * 100}%`; guidesV.classList.remove("hidden"); }
      if (nearY !== undefined) { guidesH.style.top  = `${nearY * 100}%`; guidesH.classList.remove("hidden"); }
    }
    renderZonesCanvas();
  });
  window.addEventListener("mousemove", (event) => {
    if (!zonesState.drawing) return;
    const { startX, startY, canvasRect } = zonesState.drawing;
    const currentX = clamp((event.clientX - canvasRect.left) / canvasRect.width, 0, 1);
    const currentY = clamp((event.clientY - canvasRect.top)  / canvasRect.height, 0, 1);
    zonesState.drawing.preview = {
      x: snapValue(Math.min(startX, currentX)), y: snapValue(Math.min(startY, currentY)),
      width:  snapValue(Math.max(0.1, Math.abs(currentX - startX))),
      height: snapValue(Math.max(0.1, Math.abs(currentY - startY))),
    };
    renderZonesCanvas();
  });
  window.addEventListener("mouseup", () => {
    if (zonesState.drawing?.preview) {
      const index = zonesState.zones.length + 1;
      zonesState.zones.push({ id: `local-${Date.now()}`, name: `Zone ${index}`, ...zonesState.drawing.preview, sort_order: index, transition_ms: 600, items: [] });
      renderZonesList();
    }
    zonesState.dragging = null;
    zonesState.drawing  = null;
  });
}

/* ── Playlist items ─────────────────────────────────────────── */
async function loadPlaylistItems(playlistId) {
  if (!playlistId) return;
  const data      = await api(`/playlists/${playlistId}`);
  const container = document.getElementById("playlists-list");
  container.innerHTML = "";
  const card = document.createElement("div");
  card.className = "card";
  const itemsHtml = data.items.map((item) => `
    <div class="card" style="margin-bottom:6px">
      <div>${escHtml(item.name)}</div>
      <div class="card-meta"><span>${item.duration_seconds}s</span></div>
      <div class="card-actions">
        <button class="delete-btn" data-item-id="${item.id}">Remove</button>
      </div>
    </div>
  `).join("");
  card.innerHTML = `<h3>${escHtml(data.playlist.name)} Items</h3>${itemsHtml || "<div class='helper-text'>No items yet.</div>"}`;
  container.appendChild(card);
  card.querySelectorAll("[data-item-id]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      if (!confirm("Remove this item?")) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/playlists/${playlistId}/items/${btn.dataset.itemId}`, { method: "DELETE" });
        await loadPlaylistItems(playlistId);
      });
    });
  });
}

/* ── Form handlers ───────────────────────────────────────────── */
document.getElementById("site-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn  = e.target.querySelector("button[type=submit]");
  const name = document.getElementById("site-name").value.trim();
  const slug = document.getElementById("site-slug").value.trim();
  await withLoading(btn, async () => {
    await api("/sites", { method: "POST", body: JSON.stringify({ name, slug: slug || null }) });
    e.target.reset();
    toast("Site created.", "success");
    await loadSites();
  });
});

document.getElementById("screen-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const payload = {
    name:        document.getElementById("screen-name").value.trim(),
    location:    document.getElementById("screen-location").value.trim() || null,
    resolution:  getScreenResolutionInput(),
    orientation: document.getElementById("screen-orientation").value || null,
    site_id:     document.getElementById("screen-site").value ? Number(document.getElementById("screen-site").value) : null,
  };
  await withLoading(btn, async () => {
    try {
      await api("/screens", { method: "POST", body: JSON.stringify(payload) });
      e.target.reset();
      updateResolutionCustomVisibility();
      toast("Screen created.", "success");
      await loadScreens();
    } catch (err) {
      toast(err.message || "Failed to add screen.", "error");
    }
  });
});

document.getElementById("media-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("media-file");
  const file  = input.files[0];
  if (!file) return;
  const btn = e.target.querySelector("button[type=submit]");
  await withLoading(btn, async () => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/media/upload`, {
      method:  "POST",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
      body:    formData,
    });
    if (!res.ok) { const text = await res.text(); throw new Error(text || "Upload failed"); }
    input.value = "";
    toast("Media uploaded.", "success");
    await loadMedia();
  });
});

document.getElementById("media-url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn  = e.target.querySelector("button[type=submit]");
  const name = document.getElementById("media-url-name").value.trim();
  const url  = document.getElementById("media-url").value.trim();
  if (!name || !url) return;
  await withLoading(btn, async () => {
    await api("/media/url", { method: "POST", body: JSON.stringify({ name, url }) });
    e.target.reset();
    toast("Website added.", "success");
    await loadMedia();
  });
});

const mediaForm = document.getElementById("media-form");
mediaForm?.addEventListener("dragover", (e) => { e.preventDefault(); mediaForm.classList.add("dropzone-active"); });
mediaForm?.addEventListener("dragleave", ()  => mediaForm.classList.remove("dropzone-active"));
mediaForm?.addEventListener("drop", async (e) => {
  e.preventDefault();
  mediaForm.classList.remove("dropzone-active");
  const files = Array.from(e.dataTransfer?.files || []);
  if (files.length === 0) return;
  let uploaded = 0;
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/media/upload`, {
      method: "POST",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
      body: formData,
    });
    if (res.ok) uploaded++;
  }
  toast(`${uploaded} file(s) uploaded.`, "success");
  await loadMedia();
});

document.getElementById("playlist-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn  = e.target.querySelector("button[type=submit]");
  const name = document.getElementById("playlist-name").value.trim();
  await withLoading(btn, async () => {
    await api("/playlists", { method: "POST", body: JSON.stringify({ name }) });
    e.target.reset();
    toast("Playlist created.", "success");
    await loadPlaylists();
  });
});

document.getElementById("playlist-select").addEventListener("change", async (e) => {
  await loadPlaylistItems(e.target.value);
});

document.getElementById("playlist-add-item").addEventListener("click", async (e) => {
  const playlistId = document.getElementById("playlist-select").value;
  const mediaId    = document.getElementById("playlist-media").value;
  const duration   = Number(document.getElementById("playlist-duration").value || 10);
  if (!playlistId || !mediaId) return;
  await withLoading(e.currentTarget, async () => {
    await api(`/playlists/${playlistId}/items`, { method: "POST", body: JSON.stringify({ media_id: Number(mediaId), duration_seconds: duration }) });
    toast("Item added.", "success");
    await loadPlaylistItems(playlistId);
  });
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn      = e.target.querySelector("button[type=submit]");
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      setAuth(data.token, data.user);

      const resumeRaw = sessionStorage.getItem("pair_resume");
      if (resumeRaw) {
        sessionStorage.removeItem("pair_resume");
        try {
          const resume = JSON.parse(resumeRaw);
          if (resume && resume.path === "/pair") {
            history.replaceState({}, "", `/pair${resume.code ? `?code=${encodeURIComponent(resume.code)}` : ""}`);
            await showPairView(resume.code || "");
            return;
          }
        } catch (_) { /* fall through */ }
      }

      showDashboard();
      await bootData();
    });
  } catch (err) {
    toast(err.message || "Login failed.", "error");
  }
});

/* ── Auth tabs (Sign In ⇄ Create Account) ───────────────────── */
function showAuthTab(which) {
  const loginTab   = document.getElementById("auth-tab-login");
  const signupTab  = document.getElementById("auth-tab-signup");
  const loginForm  = document.getElementById("login-form");
  const isSignup   = which === "signup";
  loginTab .classList.toggle("active", !isSignup);
  signupTab.classList.toggle("active",  isSignup);
  loginTab .setAttribute("aria-selected", String(!isSignup));
  signupTab.setAttribute("aria-selected", String( isSignup));
  loginForm .classList.toggle("hidden",  isSignup);
  // three-step wizard — always re-enter at the request step
  const signupRequestForm  = document.getElementById("signup-request-form");
  const signupVerifyForm   = document.getElementById("signup-verify-form");
  const signupPasswordForm = document.getElementById("signup-password-form");
  signupRequestForm .classList.toggle("hidden", !isSignup);
  signupVerifyForm  .classList.add("hidden");
  signupPasswordForm.classList.add("hidden");
  const firstInput = isSignup ? "signup-business" : "login-username";
  document.getElementById(firstInput)?.focus();
}

document.getElementById("auth-tab-login") .addEventListener("click", () => showAuthTab("login"));
document.getElementById("auth-tab-signup").addEventListener("click", () => showAuthTab("signup"));

/* ── Signup (3-step OTP wizard) ──────────────────────────────── */
const signupState = { email: "", business_name: "", verification_token: "" };

function signupShowStep(step) {
  const forms = {
    request:  document.getElementById("signup-request-form"),
    verify:   document.getElementById("signup-verify-form"),
    password: document.getElementById("signup-password-form"),
  };
  Object.entries(forms).forEach(([key, form]) => {
    form.classList.toggle("hidden", key !== step);
  });
  const focusMap = {
    request:  "signup-business",
    verify:   "signup-otp",
    password: "signup-new-password",
  };
  document.getElementById(focusMap[step])?.focus();
}

function signupResetDevOtpHint(otp) {
  const el = document.getElementById("signup-dev-otp");
  if (!el) return;
  if (otp) {
    el.textContent = `Dev mode: your code is ${otp}`;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

document.getElementById("signup-request-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const business_name = document.getElementById("signup-business").value.trim();
  const email         = document.getElementById("signup-email").value.trim();
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/request", {
        method: "POST",
        body: JSON.stringify({ business_name, email }),
      });
      signupState.email = email;
      signupState.business_name = business_name;
      document.getElementById("signup-verify-email").textContent = email;
      signupResetDevOtpHint(data.dev_otp || "");
      if (data.dev_otp) {
        document.getElementById("signup-otp").value = data.dev_otp;
      }
      signupShowStep("verify");
      toast("Code sent. Check the email (or dev log).", "success");
    });
  } catch (err) {
    toast(err.message || "Couldn't send code.", "error");
  }
});

document.getElementById("signup-verify-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const otp = document.getElementById("signup-otp").value.trim();
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/verify", {
        method: "POST",
        body: JSON.stringify({ email: signupState.email, otp }),
      });
      signupState.verification_token = data.verification_token;
      signupShowStep("password");
    });
  } catch (err) {
    toast(err.message || "Verification failed.", "error");
  }
});

document.getElementById("signup-resend").addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/auth/signup/request", {
      method: "POST",
      body: JSON.stringify({
        business_name: signupState.business_name,
        email: signupState.email,
      }),
    });
    signupResetDevOtpHint(data.dev_otp || "");
    if (data.dev_otp) {
      document.getElementById("signup-otp").value = data.dev_otp;
    }
    toast("New code sent.", "success");
  } catch (err) {
    toast(err.message || "Couldn't resend code.", "error");
  }
});

document.getElementById("signup-change-email").addEventListener("click", (e) => {
  e.preventDefault();
  signupState.email = "";
  signupState.verification_token = "";
  signupResetDevOtpHint("");
  document.getElementById("signup-otp").value = "";
  signupShowStep("request");
});

document.getElementById("signup-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const password        = document.getElementById("signup-new-password").value;
  const confirmPassword = document.getElementById("signup-confirm-password").value;
  if (password !== confirmPassword) {
    toast("Passwords do not match.", "error");
    return;
  }
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/complete", {
        method: "POST",
        body: JSON.stringify({
          verification_token: signupState.verification_token,
          password,
        }),
      });
      setAuth(data.token, data.user);
      showDashboard();
      await bootData();
      toast(`Welcome to Khanshoof, ${signupState.business_name}! Your 14-day trial is active.`, "success", 6000);
      signupState.email = "";
      signupState.business_name = "";
      signupState.verification_token = "";
    });
  } catch (err) {
    toast(err.message || "Sign-up failed.", "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  if (authToken) await api("/auth/logout", { method: "POST" }).catch(() => {});
  setAuth(null, null);
  showAuthPanel();
});

document.getElementById("user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentUser?.role !== "admin") return;
  const btn = e.target.querySelector("button[type=submit]");
  const payload = {
    username: document.getElementById("user-username").value.trim(),
    password: document.getElementById("user-password").value,
    role:     document.getElementById("user-role").value,
  };
  await withLoading(btn, async () => {
    await api("/users", { method: "POST", body: JSON.stringify(payload) });
    e.target.reset();
    toast("User created.", "success");
    await loadUsers();
  });
});

document.getElementById("group-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentUser?.role !== "admin") return;
  const btn  = e.target.querySelector("button[type=submit]");
  const name = document.getElementById("group-name").value.trim();
  if (!name) return;
  await withLoading(btn, async () => {
    await api("/groups", { method: "POST", body: JSON.stringify({ name }) });
    e.target.reset();
    toast("Group created.", "success");
    await loadUsers();
  });
});

/* ── Connection panel ────────────────────────────────────────── */
const connectionToggle = document.getElementById("connection-toggle");
const connectionPanel  = document.getElementById("connection-panel");
const connectionForm   = document.getElementById("connection-form");
const connectionMode   = document.getElementById("connection-mode");
const connectionApi    = document.getElementById("connection-api");
const connectionPlayer = document.getElementById("connection-player");
const connectionReset  = document.getElementById("connection-reset");

connectionToggle?.addEventListener("click", () => connectionPanel.classList.toggle("hidden"));

if (connectionMode && connectionApi && connectionPlayer) {
  connectionMode.value   = savedMode;
  connectionApi.value    = savedApiBase;
  connectionPlayer.value = savedPlayerBase;
}

connectionForm?.addEventListener("submit", (e) => {
  e.preventDefault();
  localStorage.setItem(CONNECTION_STORAGE_KEY, connectionMode.value);
  localStorage.setItem(CONNECTION_API_KEY,     connectionApi.value.trim());
  localStorage.setItem(CONNECTION_PLAYER_KEY,  connectionPlayer.value.trim());
  toast("Saved — reload to apply.", "info");
});

connectionReset?.addEventListener("click", () => {
  localStorage.removeItem(CONNECTION_STORAGE_KEY);
  localStorage.removeItem(CONNECTION_API_KEY);
  localStorage.removeItem(CONNECTION_PLAYER_KEY);
  connectionMode.value = "local"; connectionApi.value = ""; connectionPlayer.value = "";
  toast("Reset — reload to apply.", "info");
});

/* ── Boot ────────────────────────────────────────────────────── */
async function bootData() {
  await Promise.all([loadOrganization(), loadSites(), loadPlaylists(), loadMedia(), loadUsers()]);
  await loadScreens();
  showSection("sites");
}

async function loadOrganization() {
  try {
    const org = await api("/organization");
    renderPlanCard(org);
  } catch (err) {
    console.error("Failed to load organization", err);
  }
}

function renderPlanCard(org) {
  const card    = document.getElementById("plan-card");
  const biz     = document.getElementById("plan-card-business");
  const tier    = document.getElementById("plan-card-tier");
  const usage   = document.getElementById("plan-card-usage");
  const status  = document.getElementById("plan-card-status");
  const trial   = document.getElementById("plan-card-trial");

  const planLabels = {
    starter: "Starter", growth: "Growth", business: "Business",
    pro: "Pro", enterprise: "Enterprise",
  };
  const tierLabel  = planLabels[org.plan] || org.plan || "—";
  const used       = Number.isFinite(org.screens_used)  ? org.screens_used  : 0;
  const limit      = Number.isFinite(org.screen_limit) ? org.screen_limit : 0;

  biz.textContent   = org.name || "Your organization";
  tier.textContent  = `${tierLabel} plan`;
  usage.textContent = `${used} / ${limit} screens`;

  status.textContent = org.subscription_status || "—";
  status.className   = `plan-status plan-status-${(org.subscription_status || "unknown").toLowerCase()}`;

  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const endsAt  = new Date(org.trial_ends_at);
    const daysLeft = Math.max(0, Math.ceil((endsAt.getTime() - Date.now()) / 86400000));
    trial.textContent = daysLeft > 0
      ? `Trial ends in ${daysLeft} day${daysLeft === 1 ? "" : "s"} (${endsAt.toLocaleDateString()}).`
      : `Trial ended on ${endsAt.toLocaleDateString()}. Upgrade to keep your screens live.`;
    trial.classList.remove("hidden");
  } else {
    trial.classList.add("hidden");
  }

  card.classList.remove("hidden");
}

/* ── Pair view ──────────────────────────────────────────────── */
const PAIR_CODE_RE = /^[A-Z2-9]{5}$/;

function normalizePairCode(raw) {
  return String(raw || "").toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 5);
}

function showPairViewPanel() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("pair-view").classList.remove("hidden");
}

function setPairState(which) {
  const loading = document.getElementById("pair-loading");
  const form    = document.getElementById("pair-form");
  const success = document.getElementById("pair-success");
  loading.classList.toggle("hidden", which !== "loading");
  form   .classList.toggle("hidden", which !== "form");
  success.classList.toggle("hidden", which !== "success");
}

function updatePairSubmitEnabled() {
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
  let ok = PAIR_CODE_RE.test(code);
  if (target === "existing") {
    ok = ok && Boolean(document.getElementById("pair-existing-select").value);
  } else if (target === "new") {
    ok = ok && document.getElementById("pair-new-name").value.trim().length > 0;
  } else {
    ok = false;
  }
  document.getElementById("pair-submit").disabled = !ok;
}

function clearPairError() {
  const el = document.getElementById("pair-error");
  el.textContent = "";
  el.classList.add("hidden");
}

async function showPairView(initialCode) {
  showPairViewPanel();
  setPairState("loading");
  clearPairError();

  const codeInput    = document.getElementById("pair-code-input");
  const existingSel  = document.getElementById("pair-existing-select");
  const newNameInput = document.getElementById("pair-new-name");
  const radioExist   = document.getElementById("pair-target-existing");
  const radioNew     = document.getElementById("pair-target-new");

  codeInput.value    = normalizePairCode(initialCode);
  newNameInput.value = "";
  newNameInput.classList.add("hidden");
  existingSel.innerHTML = '<option value="">— Pick a screen —</option>';

  let screens = [];
  try {
    screens = await api("/screens");
  } catch (err) {
    if (err.status === 401) return;
    console.error(err);
    screens = [];
  }

  for (const s of screens) {
    const opt = document.createElement("option");
    opt.value = String(s.id);
    opt.textContent = s.name || `Screen #${s.id}`;
    existingSel.appendChild(opt);
  }

  if (screens.length === 0) {
    radioExist.disabled = true;
    existingSel.disabled = true;
    radioNew.checked = true;
    newNameInput.classList.remove("hidden");
  } else {
    radioExist.disabled = false;
    existingSel.disabled = false;
    radioExist.checked = true;
    newNameInput.classList.add("hidden");
  }

  setPairState("form");
  updatePairSubmitEnabled();
}

async function boot() {
  const isPairPath = location.pathname === "/pair";
  const pairCodeParam = isPairPath
    ? new URLSearchParams(location.search).get("code") || ""
    : "";

  if (!authToken) {
    if (isPairPath) {
      sessionStorage.setItem("pair_resume", JSON.stringify({ path: "/pair", code: pairCodeParam }));
    }
    showAuthPanel();
    updateAuthUI();
    if (location.hash === '#signup') showAuthTab('signup');
    return;
  }

  try {
    const me = await api("/auth/me");
    setAuth(authToken, me);
    if (isPairPath) {
      await showPairView(pairCodeParam);
    } else {
      showDashboard();
      await bootData();
      updateResolutionCustomVisibility();
      if (location.hash === '#signup') showAuthTab('signup');
    }
  } catch (err) {
    console.error(err);
    handleAuthFailure();
  }
}

boot().catch((err) => {
  console.error(err);
  toast("Failed to load dashboard. Check your connection.", "error", 6000);
});

/* ── Misc bindings ───────────────────────────────────────────── */
document.getElementById("screen-resolution")?.addEventListener("change", updateResolutionCustomVisibility);
updateResolutionCustomVisibility();
bindZoneEditorEvents();

/* ── Utilities ───────────────────────────────────────────────── */
function escHtml(str) {
  return String(str ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function escAttr(str) {
  return String(str ?? "").replace(/"/g,"&quot;").replace(/'/g,"&#039;");
}
function formatBytes(bytes) {
  if (bytes < 1024)       return `${bytes} B`;
  if (bytes < 1048576)    return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1048576).toFixed(1)} MB`;
}

/* ── Pair-view input wiring ─────────────────────────────────── */
document.getElementById("pair-code-input").addEventListener("input", (e) => {
  const cleaned = normalizePairCode(e.target.value);
  if (cleaned !== e.target.value) e.target.value = cleaned;
  updatePairSubmitEnabled();
});

document.getElementById("pair-existing-select").addEventListener("change", updatePairSubmitEnabled);
document.getElementById("pair-new-name")      .addEventListener("input",  updatePairSubmitEnabled);

document.querySelectorAll('input[name="pair-target"]').forEach((el) => {
  el.addEventListener("change", () => {
    const target = document.querySelector('input[name="pair-target"]:checked')?.value;
    const existingSel  = document.getElementById("pair-existing-select");
    const newNameInput = document.getElementById("pair-new-name");
    if (target === "new") {
      newNameInput.classList.remove("hidden");
      existingSel.classList.add("hidden");
    } else {
      newNameInput.classList.add("hidden");
      existingSel.classList.remove("hidden");
    }
    updatePairSubmitEnabled();
  });
});

/* ── Pair-view submit ───────────────────────────────────────── */
function showPairError(message) {
  const el = document.getElementById("pair-error");
  el.textContent = message;
  el.classList.remove("hidden");
}

function mapPairErrorMessage(err) {
  const detail = (err && err.data && typeof err.data.detail === "string")
    ? err.data.detail
    : (err?.message || "");
  const status = err?.status;
  if (status === 404 && /pairing code/i.test(detail)) {
    return "That code isn't recognised. Check the TV screen and try again.";
  }
  if (status === 400 && /expired/i.test(detail)) {
    return "Code expired. Refresh the TV to get a new one.";
  }
  if (status === 409) {
    return "That code's been used. Refresh the TV to get a new one.";
  }
  if (status === 400 && /bound to a different screen/i.test(detail)) {
    return "This code belongs to a different display. Refresh the TV to get a new one.";
  }
  if (status === 402) {
    return "You've hit your plan's screen limit. Upgrade to add more.";
  }
  if (status === 403) {
    return "Your account doesn't have permission to pair displays.";
  }
  if (!status) {
    return "Can't reach server. Please try again.";
  }
  return "Something went wrong — please try again.";
}

async function onPairSubmit(e) {
  e.preventDefault();
  clearPairError();
  const btn = document.getElementById("pair-submit");
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;

  await withLoading(btn, async () => {
    try {
      let screenId = null;
      let screenName = "";

      if (target === "new") {
        const name = document.getElementById("pair-new-name").value.trim();
        const screen = await api("/screens", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        screenId = screen.id;
        screenName = screen.name || name;
      } else {
        const sel = document.getElementById("pair-existing-select");
        screenId = Number(sel.value);
        screenName = sel.options[sel.selectedIndex]?.textContent || `Screen #${screenId}`;
      }

      await api("/screens/claim", {
        method: "POST",
        body: JSON.stringify({ code, screen_id: screenId }),
      });

      document.getElementById("pair-success-name").textContent = screenName;
      setPairState("success");
    } catch (err) {
      console.error(err);
      showPairError(mapPairErrorMessage(err));
    }
  });
}

async function onPairAnother() {
  history.replaceState({}, "", "/pair");
  await showPairView("");
}

function onPairViewDashboard() {
  history.pushState({}, "", "/");
  document.getElementById("pair-view").classList.add("hidden");
  showDashboard();
  bootData().catch((err) => {
    console.error(err);
    toast("Failed to load dashboard. Check your connection.", "error", 6000);
  });
}

document.getElementById("pair-form")         .addEventListener("submit", onPairSubmit);
document.getElementById("pair-another-btn")  .addEventListener("click",  onPairAnother);
document.getElementById("pair-dashboard-btn").addEventListener("click",  onPairViewDashboard);
