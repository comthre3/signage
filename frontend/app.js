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

/* ── Localized confirm dialog ────────────────────────────────── */
// Localized replacement for window.confirm. Returns Promise<boolean>.
window.confirmDialog = function ({ title, message, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    if (document.querySelector(".confirm-dialog-modal")) {
      resolve(false);
      return;
    }
    const overlay = document.createElement("div");
    overlay.className = "modal confirm-dialog-modal";
    overlay.innerHTML = `
      <div class="modal-card confirm-dialog-card">
        <div class="confirm-dialog-header">
          <h3>${title || ""}</h3>
          <button class="confirm-dialog-close btn-ghost" aria-label="Close">✕</button>
        </div>
        <div class="confirm-dialog-body">
          <p>${(message || "").replace(/</g, "&lt;")}</p>
        </div>
        <div class="confirm-dialog-actions">
          <button class="btn btn-ghost confirm-dialog-cancel">${
            Khan.t("confirm.cancel", "Cancel")}</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"} confirm-dialog-confirm">${
            confirmLabel || Khan.t("confirm.delete_label", "Delete")}</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    let settled = false;
    function settle(value) {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeyDown);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(value);
    }
    function onKeyDown(e) {
      if (e.key === "Escape") settle(false);
      else if (e.key === "Enter") settle(true);
    }

    overlay.addEventListener("click", (e) => { if (e.target === overlay) settle(false); });
    overlay.querySelector(".confirm-dialog-close").addEventListener("click", () => settle(false));
    overlay.querySelector(".confirm-dialog-cancel").addEventListener("click", () => settle(false));
    overlay.querySelector(".confirm-dialog-confirm").addEventListener("click", () => settle(true));

    document.addEventListener("keydown", onKeyDown);

    setTimeout(() => overlay.querySelector(".confirm-dialog-cancel").focus(), 0);
  });
};

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
  if (id === "walls") Walls.onShow();
  if (id === "audit-log") AuditLog.show();
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
function localizeError(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && detail.code) {
    return Khan.t(`error.${detail.code}`, detail.message);
  }
  return fallback || "Something went wrong";
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, ...options });
  if (!res.ok) {
    if (res.status === 401) handleAuthFailure();
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    const msg = data && data.detail
      ? localizeError(data.detail, text)
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
  const auditBtn  = document.querySelector('button[data-section="audit-log"]');
  if (currentUser) {
    authUser.textContent = `${currentUser.username} · ${currentUser.role}`;
    logoutBtn.classList.remove("hidden");
    nav.classList.remove("hidden");
    if (usersBtn) usersBtn.classList.toggle("hidden", currentUser.role !== "admin");
    if (auditBtn) auditBtn.classList.toggle("hidden", currentUser.role !== "admin");
  } else {
    authUser.textContent = "";
    logoutBtn.classList.add("hidden");
    nav.classList.add("hidden");
    if (usersBtn) usersBtn.classList.add("hidden");
    if (auditBtn) auditBtn.classList.add("hidden");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_site_title", "Delete site"),
        message: Khan.t("confirm.delete_site_body", "Delete site \"{name}\"? Screens in this site become unassigned.").replace("{name}", site.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/sites/${site.id}`, { method: "DELETE" });
        toast(Khan.t("toast.site_deleted"), "success");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_playlist_title", "Delete playlist"),
        message: Khan.t("confirm.delete_playlist_body", "Delete playlist \"{name}\"?").replace("{name}", playlist.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/playlists/${playlist.id}`, { method: "DELETE" });
        toast(Khan.t("toast.playlist_deleted"), "success");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_media_title", "Delete media"),
        message: Khan.t("confirm.delete_media_body", "Delete \"{name}\"?").replace("{name}", item.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/media/${item.id}`, { method: "DELETE" });
        toast(Khan.t("toast.media_deleted"), "success");
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
    container.innerHTML = `<div class='card'>${Khan.t("users.admin_required", "Admin access required to manage users.")}</div>`;
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
        toast(Khan.t("toast.password_updated"), "success");
      });
    });
    card.querySelector(`[data-user-delete="${user.id}"]`).addEventListener("click", async (e) => {
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_user_title", "Delete user"),
        message: Khan.t("confirm.delete_user_body", "Delete user \"{name}\"?").replace("{name}", user.username),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/users/${user.id}`, { method: "DELETE" });
        toast(Khan.t("toast.user_deleted"), "success");
        await loadUsers();
      });
    });
    const roleSelect = card.querySelector(`[data-user-role="${user.id}"]`);
    roleSelect.value = user.role;
    card.querySelector(`[data-user-role-save="${user.id}"]`).addEventListener("click", async (e) => {
      await withLoading(e.currentTarget, async () => {
        await api(`/users/${user.id}`, { method: "PUT", body: JSON.stringify({ role: roleSelect.value }) });
        toast(Khan.t("toast.role_updated"), "success");
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
    heading.textContent = Khan.t("users.groups_heading", "Groups");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_group_title", "Delete group"),
        message: Khan.t("confirm.delete_group_body", "Delete group \"{name}\"?").replace("{name}", group.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/groups/${group.id}`, { method: "DELETE" });
        toast(Khan.t("toast.group_deleted"), "success");
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
        <span>Site: ${escHtml(screen.site_name || Khan.t("screens.site_unassigned_label", "Unassigned"))}</span>
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
        toast(Khan.t("toast.screen_saved"), "success");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_screen_title", "Delete screen"),
        message: Khan.t("confirm.delete_screen_body", "Delete screen \"{name}\"?").replace("{name}", screen.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(e.currentTarget, async () => {
        await api(`/screens/${screen.id}`, { method: "DELETE" });
        toast(Khan.t("toast.screen_deleted"), "success");
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
  meta.textContent = `${Khan.t("screens.preview_meta_label", "Previewing")}: ${screen.name} (${screen.site_name || Khan.t("screens.preview_meta_unassigned", "Unassigned")})` +
    (expiresAt ? ` · ${Khan.t("screens.preview_meta_expires", "expires")} ${expiresAt}` : "");
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
    preview.innerHTML = `<div class="zone-title">${Khan.t("screens.zone_default_name", "New Zone")}</div>`;
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
  ownerSelect.innerHTML = `<option value="">${Khan.t("screens.access_unassigned_option", "Unassigned")}</option>`;
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
      toast(Khan.t("toast.access_saved"), "success");
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
      toast(Khan.t("toast.template_saved"), "success");
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
      toast(Khan.t("toast.template_applied"), "success");
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
      toast(Khan.t("toast.zones_saved"), "success");
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
      if (!(await confirmDialog({
        title:   Khan.t("confirm.remove_item_title", "Remove item"),
        message: Khan.t("confirm.remove_item_body", "Remove this item from the playlist?"),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
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
    toast(Khan.t("toast.site_created"), "success");
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
      toast(Khan.t("toast.screen_created"), "success");
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
    toast(Khan.t("toast.media_uploaded"), "success");
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
    toast(Khan.t("toast.website_added"), "success");
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
  toast(Khan.t("toast.files_uploaded_n", "{n} file(s) uploaded.").replace("{n}", uploaded), "success");
  await loadMedia();
});

document.getElementById("playlist-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn  = e.target.querySelector("button[type=submit]");
  const name = document.getElementById("playlist-name").value.trim();
  await withLoading(btn, async () => {
    await api("/playlists", { method: "POST", body: JSON.stringify({ name }) });
    e.target.reset();
    toast(Khan.t("toast.playlist_created"), "success");
    await loadPlaylists();
  });
});

document.getElementById("playlist-select").addEventListener("change", async (e) => {
  await loadPlaylistItems(e.target.value);
});

document.getElementById("playlist-add-item").addEventListener("click", async (e) => {
  const playlistId = document.getElementById("playlist-select").value;
  if (!playlistId) return;
  let picks;
  try {
    picks = await MediaPicker.open({ allowedTypes: ["image", "video", "pdf", "url"] });
  } catch (err) {
    if (err && err.cancelled) return;
    throw err;
  }
  if (!picks.length) return;
  await withLoading(e.currentTarget, async () => {
    for (const p of picks) {
      const body = { media_id: p.media_id };
      if (p.duration_seconds != null) body.duration_seconds = p.duration_seconds;
      await api(`/playlists/${playlistId}/items`, {
        method: "POST",
        body: JSON.stringify(body),
      });
    }
    toast(Khan.t("toast.item_added"), "success");
    await loadPlaylistItems(playlistId);
  });
});

function handleLockoutCountdown(btn, seconds) {
  function fmt(remaining) {
    const minutes = Math.ceil(remaining / 60);
    return Khan.t(
      "auth.account_locked",
      "Too many failed attempts. Try again in {minutes} minutes."
    ).replace("{minutes}", String(minutes));
  }
  toast(fmt(seconds), "error");
  if (!btn) return;
  btn.disabled = true;
  let remaining = seconds;
  const tick = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(tick);
      btn.disabled = false;
    }
  }, 1000);
}

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
    if (err.status === 429 && err.data?.detail?.code === "account_locked") {
      const seconds = Math.max(0, parseInt(err.data.detail.retry_after_seconds || 0, 10));
      handleLockoutCountdown(btn, seconds);
      return;
    }
    toast(err.message || Khan.t("error.login_failed", "Login failed."), "error");
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
      toast(Khan.t("toast.code_sent"), "success");
    });
  } catch (err) {
    toast(err.message || Khan.t("error.code_send_failed", "Couldn't send code."), "error");
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
    toast(Khan.t("toast.new_code_sent"), "success");
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
    toast(Khan.t("toast.passwords_no_match"), "error");
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
      toast(Khan.t("toast.signup_welcome", "Welcome to Khanshoof, {name}! Your 5-day trial is active.").replace("{name}", signupState.business_name), "success", 6000);
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
    toast(Khan.t("toast.user_created"), "success");
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
    toast(Khan.t("toast.group_created"), "success");
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
  toast(Khan.t("toast.settings_saved"), "info");
});

connectionReset?.addEventListener("click", () => {
  localStorage.removeItem(CONNECTION_STORAGE_KEY);
  localStorage.removeItem(CONNECTION_API_KEY);
  localStorage.removeItem(CONNECTION_PLAYER_KEY);
  connectionMode.value = "local"; connectionApi.value = ""; connectionPlayer.value = "";
  toast(Khan.t("toast.settings_reset"), "info");
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

function bindLangToggle() {
  const btn = document.getElementById("lang-toggle");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", async () => {
    const next = Khan.currentLocale() === "en" ? "ar" : "en";
    if (authToken && currentUser?.is_admin) {
      try {
        await api("/organizations/me", { method: "PATCH", body: JSON.stringify({ locale: next }) });
      } catch (err) {
        console.error("PATCH /organizations/me failed", err);
      }
    }
    Khan.setLocale(next);
    location.reload();
  });
}

async function boot() {
  let orgLocale;
  if (authToken) {
    try {
      const me = await api("/auth/me");
      setAuth(authToken, me);
      orgLocale = me.organization?.locale;
    } catch (err) {
      // fall through to cookie/browser detection
    }
  }
  const locale = Khan.detectInitialLocale(orgLocale);
  await Khan.loadLocale(locale);
  Khan.applyTranslations(document);

  bindLangToggle();

  const isPairPath = location.pathname === "/pair";
  const isBillingPath = location.pathname === "/billing";
  const pairCodeParam = isPairPath
    ? new URLSearchParams(location.search).get("code") || ""
    : "";

  if (!authToken || !currentUser) {
    if (isPairPath) {
      sessionStorage.setItem("pair_resume", JSON.stringify({ path: "/pair", code: pairCodeParam }));
    }
    showAuthPanel();
    updateAuthUI();
    if (location.hash === '#signup') showAuthTab('signup');
    return;
  }

  if (isPairPath) {
    await showPairView(pairCodeParam);
  } else if (isBillingPath) {
    await showBilling();
  } else {
    showDashboard();
    await bootData();
    updateResolutionCustomVisibility();
    if (location.hash === '#signup') showAuthTab('signup');
  }
}

boot().catch((err) => {
  console.error(err);
  toast(Khan.t("toast.dashboard_load_failed"), "error", 6000);
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
    toast(Khan.t("toast.dashboard_load_failed"), "error", 6000);
  });
}

document.getElementById("pair-form")         .addEventListener("submit", onPairSubmit);
document.getElementById("pair-another-btn")  .addEventListener("click",  onPairAnother);
document.getElementById("pair-dashboard-btn").addEventListener("click",  onPairViewDashboard);

/* ── Billing view ───────────────────────────────────────────── */
const KWD_TO_USD = 3.267;
const PLAN_TIERS = [
  { tier: "starter",  label: "Starter",  kwd: 3,  screens: 3  },
  { tier: "growth",   label: "Growth",   kwd: 4,  screens: 5  },
  { tier: "business", label: "Business", kwd: 8,  screens: 10 },
  { tier: "pro",      label: "Pro",      kwd: 15, screens: 25 },
];
const BILLING_TERMS = [
  { months: 1,  multiplier: 1,  saveLabel: "" },
  { months: 6,  multiplier: 5,  saveLabel: "save 1 month" },
  { months: 12, multiplier: 10, saveLabel: "save 2 months" },
];

let billingCurrentTerm = 1;
let billingPollTimer = null;
let billingPollStartedAt = 0;

function billingAmountsFor(tier, months) {
  const plan = PLAN_TIERS.find((p) => p.tier === tier);
  const term = BILLING_TERMS.find((t) => t.months === months);
  if (!plan || !term) return null;
  const kwd = plan.kwd * term.multiplier;
  const usdApprox = (kwd * KWD_TO_USD).toFixed(2);
  return { kwd, usdApprox };
}

function showBillingPanel() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("pair-view").classList.add("hidden");
  document.getElementById("billing-view").classList.remove("hidden");
}

function renderBillingCurrent(org) {
  const body = document.getElementById("billing-current-body");
  if (!org) {
    body.textContent = "—";
    return;
  }
  const plan = PLAN_TIERS.find((p) => p.tier === org.plan) || { label: org.plan, screens: org.screen_limit };
  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const daysLeft = Math.max(0, Math.ceil((new Date(org.trial_ends_at) - new Date()) / 86400000));
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · Trial · ${daysLeft} day${daysLeft === 1 ? "" : "s"} left · up to ${plan.screens} screens`;
  } else if (org.paid_through_at) {
    const ends = new Date(org.paid_through_at).toLocaleDateString();
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · paid through ${escHtml(ends)} · up to ${plan.screens} screens`;
  } else {
    body.innerHTML = `<strong>${escHtml(plan.label)}</strong> · up to ${plan.screens} screens`;
  }
}

function renderBillingTiers() {
  const grid = document.getElementById("billing-tier-grid");
  const termInfo = BILLING_TERMS.find((t) => t.months === billingCurrentTerm);
  grid.innerHTML = "";
  for (const plan of PLAN_TIERS) {
    const amounts = billingAmountsFor(plan.tier, billingCurrentTerm);
    const saveMarkup = termInfo.saveLabel
      ? `<span class="billing-tier-save">${escHtml(termInfo.saveLabel)}</span>` : "";
    const card = document.createElement("div");
    card.className = "billing-tier";
    card.innerHTML = `
      <h3 class="billing-tier-name">${escHtml(plan.label)}</h3>
      <div class="billing-tier-limit">up to ${plan.screens} screens</div>
      <div class="billing-tier-kwd">${amounts.kwd} KWD${billingCurrentTerm === 1 ? " / month" : ""}</div>
      <div class="billing-tier-usd">≈ $${amounts.usdApprox}</div>
      ${saveMarkup}
      <button type="button" class="billing-tier-btn" data-tier="${escAttr(plan.tier)}">
        Pay ${amounts.kwd} KWD${termInfo.saveLabel ? " · " + escHtml(termInfo.saveLabel) : ""}
      </button>
    `;
    grid.appendChild(card);
  }
}

async function loadBillingHistory() {
  const body = document.getElementById("billing-history-body");
  try {
    const rows = await api("/billing/history");
    if (!rows.length) {
      body.innerHTML = '<p class="billing-empty">No payments yet.</p>';
      return;
    }
    body.innerHTML = rows.map((r) => {
      const when = new Date(r.updated_at || r.created_at).toLocaleDateString();
      return `<div class="billing-history-row ${r.status === 'failed' ? 'failed' : ''}">
        <span>${escHtml(r.tier)} · ${r.term_months} month${r.term_months === 1 ? '' : 's'} · ${escHtml(when)}</span>
        <span>${r.amount_kwd} KWD · ${escHtml(r.status)}</span>
      </div>`;
    }).join("");
  } catch (err) {
    body.innerHTML = '<p class="billing-empty">Couldn\'t load history.</p>';
  }
}

async function showBilling() {
  showBillingPanel();
  renderBillingTiers();
  try {
    const me = await api("/auth/me");
    renderBillingCurrent(me.organization);
  } catch (err) { renderBillingCurrent(null); }
  loadBillingHistory();
  maybeResumeBillingStatus();
}

function setBillingBanner(kind, message) {
  const el = document.getElementById("billing-banner");
  el.textContent = message;
  el.classList.remove("hidden", "success", "error");
  el.classList.add(kind);
}
function clearBillingBanner() {
  document.getElementById("billing-banner").classList.add("hidden");
}

async function onBillingPay(tier) {
  clearBillingBanner();
  const grid = document.getElementById("billing-tier-grid");
  const buttons = grid.querySelectorAll(".billing-tier-btn");
  buttons.forEach((b) => (b.disabled = true));
  try {
    const data = await api("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ tier, term_months: billingCurrentTerm }),
    });
    sessionStorage.setItem("billing_pending_trackid", data.trackid);
    window.location.href = data.payment_url;
  } catch (err) {
    buttons.forEach((b) => (b.disabled = false));
    setBillingBanner("error", err?.data?.detail || err.message || "Payment failed to start.");
  }
}

function stopBillingPoll() {
  if (billingPollTimer) { clearTimeout(billingPollTimer); billingPollTimer = null; }
}

function maybeResumeBillingStatus() {
  const params = new URLSearchParams(location.search);
  const status = params.get("status");
  const trackid = params.get("trackid") || sessionStorage.getItem("billing_pending_trackid");
  if (!trackid || !status) return;
  sessionStorage.removeItem("billing_pending_trackid");
  setBillingBanner("success", "Confirming payment…");
  billingPollStartedAt = Date.now();
  pollBillingStatus(trackid);
}

async function pollBillingStatus(trackid) {
  try {
    const data = await api(`/billing/status/${encodeURIComponent(trackid)}`);
    if (data.status === "captured") {
      setBillingBanner("success", `Plan upgraded · ${data.tier} · paid through ${new Date(data.paid_through_at).toLocaleDateString()}`);
      const me = await api("/auth/me");
      renderBillingCurrent(me.organization);
      loadBillingHistory();
      history.replaceState({}, "", "/billing");
      return;
    }
    if (data.status === "failed") {
      setBillingBanner("error", "Payment declined. You can try again.");
      history.replaceState({}, "", "/billing");
      return;
    }
    // still pending
    if (Date.now() - billingPollStartedAt > 15000) {
      setBillingBanner("success", "Payment is still processing — check back in a minute.");
      return;
    }
    billingPollTimer = setTimeout(() => pollBillingStatus(trackid), 2000);
  } catch (err) {
    setBillingBanner("error", "Couldn't confirm payment status.");
  }
}

document.querySelectorAll(".billing-term").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".billing-term").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    });
    billingCurrentTerm = Number(btn.dataset.term);
    renderBillingTiers();
  });
});

document.getElementById("billing-tier-grid").addEventListener("click", (e) => {
  const btn = e.target.closest(".billing-tier-btn");
  if (!btn) return;
  onBillingPay(btn.dataset.tier);
});

document.querySelector('nav button[data-section="billing"]')?.addEventListener("click", (e) => {
  e.preventDefault();
  history.pushState({}, "", "/billing");
  showBilling();
});

// ====== MediaPicker ======
const MediaPicker = (() => {
  // Single-instance state. While `state.overlay` is non-null, open() is a no-op.
  const state = {
    overlay:      null,
    mediaList:    [],   // raw /media response, filtered to allowedTypes
    selection:    [],   // ordered array of media_ids picked
    durations:    {},   // { media_id: number } — only for items the user touched in Advanced
    chip:         "all",
    search:       "",
    advancedOpen: false,
    resolve:      null,
    reject:       null,
    allowedTypes: [],
  };

  function attrEscape(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  function classifyMime(mime) {
    if (!mime) return "other";
    const m = mime.toLowerCase();
    if (m.startsWith("image/")) return "image";
    if (m.startsWith("video/")) return "video";
    if (m === "application/pdf") return "pdf";
    if (m === "text/url") return "url";
    return "other";
  }

  function open({ allowedTypes }) {
    if (!Array.isArray(allowedTypes) || allowedTypes.length === 0) {
      throw new Error("MediaPicker.open: allowedTypes must be a non-empty array");
    }
    if (state.overlay) {
      console.warn("MediaPicker already open; ignoring open() call");
      return Promise.resolve([]);
    }
    state.allowedTypes = allowedTypes.slice();
    state.selection = [];
    state.durations = {};
    state.chip = "all";
    state.search = "";
    state.advancedOpen = false;
    return new Promise(async (resolve, reject) => {
      state.resolve = resolve;
      state.reject  = reject;
      mountOverlay();
      await loadMedia();
    });
  }

  function close(picksOrCancel) {
    if (!state.resolve && !state.reject) return; // guard against double-fire
    const overlay = state.overlay;
    state.overlay = null;
    document.removeEventListener("keydown", onKeyDown);
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (picksOrCancel && picksOrCancel.cancelled) {
      state.reject({ cancelled: true });
    } else {
      state.resolve(picksOrCancel);
    }
    state.resolve = null;
    state.reject  = null;
  }

  function mountOverlay() {
    const o = document.createElement("div");
    o.className = "modal media-picker-modal";
    o.innerHTML = `
      <div class="modal-card media-picker-card">
        <div class="media-picker-header">
          <h3>${Khan.t("media_picker.title", "Pick media")}</h3>
          <input class="media-picker-search" type="search"
                 placeholder="${Khan.t("media_picker.search_placeholder", "Search by name…")}" />
          <button class="media-picker-close btn-ghost" aria-label="Close">✕</button>
        </div>
        <div class="media-picker-chips"></div>
        <div class="media-picker-grid" aria-live="polite"></div>
        <div class="media-picker-advanced">
          <button class="media-picker-advanced-toggle btn-ghost" type="button">
            ▸ ${Khan.t("media_picker.advanced_durations", "Advanced: set per-item durations")}
          </button>
          <div class="media-picker-advanced-list hidden"></div>
        </div>
        <div class="media-picker-footer">
          <span class="media-picker-count">${Khan.t("media_picker.selected_n", "{n} selected").replace("{n}", "0")}</span>
          <div class="media-picker-actions">
            <button class="btn btn-ghost media-picker-cancel">${Khan.t("media_picker.cancel", "Cancel")}</button>
            <button class="btn btn-primary media-picker-confirm" disabled>${
              Khan.t("media_picker.add_n", "Add {n} items").replace("{n}", "0")}</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(o);
    state.overlay = o;

    // Close on backdrop click (but not on card click).
    o.addEventListener("click", (e) => {
      if (e.target === o) close({ cancelled: true });
    });
    o.querySelector(".media-picker-close").addEventListener("click", () => close({ cancelled: true }));
    o.querySelector(".media-picker-cancel").addEventListener("click", () => close({ cancelled: true }));
    o.querySelector(".media-picker-search").addEventListener("input", (e) => {
      state.search = e.target.value.trim().toLowerCase();
      renderGrid();
    });
    o.querySelector(".media-picker-advanced-toggle").addEventListener("click", () => {
      state.advancedOpen = !state.advancedOpen;
      renderAdvanced();
    });
    o.querySelector(".media-picker-confirm").addEventListener("click", confirmPicks);

    document.addEventListener("keydown", onKeyDown);
    renderChips();
  }

  function onKeyDown(e) {
    if (e.key === "Escape" && state.overlay) close({ cancelled: true });
  }

  async function loadMedia() {
    if (!state.overlay) return; // picker was closed
    const grid = state.overlay.querySelector(".media-picker-grid");
    grid.textContent = "…";
    try {
      const all = await api("/media");
      if (!state.overlay) return; // closed during fetch
      state.mediaList = all.filter(m =>
        state.allowedTypes.includes(classifyMime(m.mime_type))
      );
      renderGrid();
    } catch (err) {
      if (!state.overlay) return; // closed during fetch
      grid.innerHTML = `
        <div class="media-picker-empty">
          <p>${Khan.t("media_picker.fetch_failed", "Couldn't load media.")}</p>
          <button class="btn media-picker-retry">Retry</button>
        </div>
      `;
      grid.querySelector(".media-picker-retry").addEventListener("click", loadMedia);
    }
  }

  function renderChips() {
    const root = state.overlay.querySelector(".media-picker-chips");
    const labels = {
      all:    "filter_all",
      image:  "filter_images",
      video:  "filter_videos",
      pdf:    "filter_pdfs",
      url:    "filter_urls",
    };
    const fallbacks = { all: "All", image: "Images", video: "Videos", pdf: "PDFs", url: "URLs" };
    const chips = ["all", ...state.allowedTypes];
    root.innerHTML = chips.map(c => `
      <button type="button"
              data-chip="${c}"
              class="media-picker-chip ${state.chip === c ? "active" : ""}">
        ${Khan.t("media_picker." + labels[c], fallbacks[c])}
      </button>
    `).join("");
    root.querySelectorAll(".media-picker-chip").forEach(el => {
      el.addEventListener("click", () => {
        const c = el.dataset.chip;
        state.chip = state.chip === c ? "all" : c;
        renderChips();
        renderGrid();
      });
    });
  }

  function visibleItems() {
    return state.mediaList.filter(m => {
      const cls = classifyMime(m.mime_type);
      if (state.chip !== "all" && cls !== state.chip) return false;
      if (state.search && !(m.name || "").toLowerCase().includes(state.search)) return false;
      return true;
    });
  }

  function renderGrid() {
    const grid = state.overlay.querySelector(".media-picker-grid");
    if (!state.mediaList.length) {
      grid.innerHTML = `
        <div class="media-picker-empty">
          <p>${Khan.t("media_picker.empty_library", "No media yet. Upload some in the Media tab.")}</p>
          <button class="btn media-picker-go-to-media">${Khan.t("nav.media", "Media")}</button>
        </div>
      `;
      grid.querySelector(".media-picker-go-to-media").addEventListener("click", () => {
        close({ cancelled: true });
        if (typeof showSection === "function") showSection("media");
      });
      return;
    }
    const items = visibleItems();
    if (!items.length) {
      grid.innerHTML = `
        <div class="media-picker-empty">
          <p>${Khan.t("media_picker.empty_filtered", "No matches.")}</p>
        </div>
      `;
      return;
    }
    grid.innerHTML = items.map(m => renderCard(m)).join("");
    grid.querySelectorAll(".media-picker-card").forEach(el => {
      el.addEventListener("click", () => toggleSelect(parseInt(el.dataset.mediaId, 10)));
    });
  }

  function renderCard(m) {
    const cls = classifyMime(m.mime_type);
    const idx = state.selection.indexOf(m.id);
    const checked = idx !== -1;
    const badge = checked ? `${idx + 1}` : "";
    const pill = cls === "url" ? "URL" : cls.toUpperCase();
    let thumb = "";
    if (cls === "image") {
      thumb = `<img src="/uploads/${attrEscape(m.filename)}" loading="lazy" alt="" />`;
    } else if (cls === "video") {
      thumb = `<video src="/uploads/${attrEscape(m.filename)}" preload="metadata" muted></video>`;
    } else if (cls === "pdf") {
      thumb = `<div class="picker-thumb-pdf">PDF</div>`;
    } else if (cls === "url") {
      let host = "";
      try { host = new URL(m.url || "").hostname; } catch (_) {}
      thumb = host
        ? `<div class="picker-thumb-url"><img src="https://www.google.com/s2/favicons?domain=${attrEscape(host)}&sz=64" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'🌐'}))" /></div>`
        : `<div class="picker-thumb-url">🌐</div>`;
    } else {
      thumb = `<div class="picker-thumb-other">?</div>`;
    }
    return `
      <div class="media-picker-card ${checked ? "checked" : ""}" data-media-id="${m.id}">
        <div class="media-picker-thumb">${thumb}</div>
        <div class="media-picker-badge">${badge}</div>
        <div class="media-picker-bottom">
          <span class="media-picker-name" title="${(m.name || "").replace(/"/g, "&quot;")}">${(m.name || "").replace(/</g, "&lt;")}</span>
          <span class="media-picker-pill">${pill}</span>
        </div>
      </div>
    `;
  }

  function toggleSelect(mediaId) {
    const i = state.selection.indexOf(mediaId);
    if (i === -1) state.selection.push(mediaId);
    else { state.selection.splice(i, 1); delete state.durations[mediaId]; }
    renderGrid();
    renderFooter();
    if (state.advancedOpen) renderAdvanced();
  }

  function renderFooter() {
    const n = state.selection.length;
    state.overlay.querySelector(".media-picker-count").textContent =
      Khan.t("media_picker.selected_n", "{n} selected").replace("{n}", String(n));
    const btn = state.overlay.querySelector(".media-picker-confirm");
    btn.textContent = Khan.t("media_picker.add_n", "Add {n} items").replace("{n}", String(n));
    btn.disabled = n === 0;
  }

  function renderAdvanced() {
    const wrap = state.overlay.querySelector(".media-picker-advanced-list");
    wrap.classList.toggle("hidden", !state.advancedOpen);
    if (!state.advancedOpen) return;
    if (!state.selection.length) {
      wrap.innerHTML = `<p class="media-picker-advanced-empty">—</p>`;
      return;
    }
    wrap.innerHTML = state.selection.map((mid, i) => {
      const m = state.mediaList.find(x => x.id === mid);
      const dur = state.durations[mid];
      const safeName = (m?.name || "").replace(/</g, "&lt;");
      return `
        <div class="media-picker-advanced-row" data-media-id="${mid}">
          <span class="media-picker-advanced-idx">${i + 1}</span>
          <span class="media-picker-advanced-name">${safeName}</span>
          <input type="number" min="1" max="3600" placeholder="default"
                 value="${dur ?? ""}" class="media-picker-advanced-duration" />
        </div>
      `;
    }).join("");
    wrap.querySelectorAll(".media-picker-advanced-duration").forEach(el => {
      el.addEventListener("input", () => {
        const row = el.closest(".media-picker-advanced-row");
        const mid = parseInt(row.dataset.mediaId, 10);
        const v = el.value.trim();
        if (v === "") delete state.durations[mid];
        else state.durations[mid] = Math.max(1, Math.min(3600, parseInt(v, 10)));
      });
    });
  }

  function confirmPicks() {
    const picks = state.selection.map(mid => {
      const out = { media_id: mid };
      if (state.durations[mid] != null) out.duration_seconds = state.durations[mid];
      return out;
    });
    close(picks);
  }

  return { open };
})();

// ====== Walls ======
const Walls = (() => {
  const state = { walls: [], editing: null, pairing: null };

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem(AUTH_STORAGE_KEY);
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${path}`,
      { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
    if (!res.ok && res.status !== 204) {
      if (res.status === 401) handleAuthFailure();
      let body = {};
      try { body = await res.json(); } catch (_) {}
      const code = body?.detail?.code || `http_${res.status}`;
      throw Object.assign(new Error(body?.detail?.message || res.statusText), { code });
    }
    return res.status === 204 ? null : res.json();
  }

  async function loadList() {
    state.walls = await api("/walls");
    renderList();
  }

  function renderList() {
    const root = document.getElementById("walls-list");
    if (!state.walls.length) {
      root.innerHTML = `<p class="empty" data-i18n="walls.empty">${
        Khan.t("walls.empty", "No walls yet. Click \"Create wall\" to start.")}</p>`;
      return;
    }
    root.innerHTML = state.walls.map(w => `
      <article class="walls-card" data-wall-id="${w.id}">
        <h4>${escHtml(w.name)}</h4>
        <div class="walls-meta">
          ${w.mode === "mirrored" ? Khan.t("walls.mode_mirrored", "Mirrored")
                                   : Khan.t("walls.mode_spanned", "Spanned")}
          · ${w.rows}×${w.cols}
        </div>
        <div class="walls-mosaic" style="grid-template-columns: repeat(${w.cols}, 1fr);">
          ${(w.cells || []).map(c => `
            <div class="walls-mosaic-cell ${c.screen_id ? "online" : "offline"}">
              ${c.screen_id ? "●" : ""}
            </div>`).join("")}
        </div>
        <div class="walls-actions">
          <button class="btn btn-ghost" data-action="edit">${Khan.t("walls.edit", "Edit")}</button>
          <button class="btn btn-danger" data-action="delete">${Khan.t("walls.delete", "Delete")}</button>
        </div>
      </article>
    `).join("");
    root.querySelectorAll("[data-wall-id]").forEach(card => {
      const id = parseInt(card.dataset.wallId, 10);
      card.querySelector('[data-action="edit"]').addEventListener("click", () => openEditor(id));
      card.querySelector('[data-action="delete"]').addEventListener("click", () => deleteWall(id));
    });
  }

  async function createWizard() {
    const playlists = await api("/playlists");
    const editor = document.getElementById("walls-editor");
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent =
      Khan.t("walls.wizard_title", "New wall");
    editor.classList.remove("hidden");
    body.innerHTML = `
      <form id="walls-wizard">
        <label>${Khan.t("walls.name", "Name")}
          <input name="name" required maxlength="120" /></label>
        <fieldset>
          <legend>${Khan.t("walls.mode", "Mode")}</legend>
          <label><input type="radio" name="mode" value="mirrored" checked />
            ${Khan.t("walls.mode_mirrored", "Mirrored")}</label>
          ${window.WALLS_PHASE2_ENABLED ? `
          <label><input type="radio" name="mode" value="spanned" />
            ${Khan.t("walls.mode_spanned", "Spanned")}</label>` : `
          <label><input type="radio" name="mode" value="spanned" disabled />
            ${Khan.t("walls.mode_spanned_phase2", "Spanned (coming soon)")}</label>`}
        </fieldset>
        <div class="walls-grid-picker">
          <label>${Khan.t("walls.rows", "Rows")}
            <input name="rows" type="number" min="1" max="8" value="1" required /></label>
          <label>${Khan.t("walls.cols", "Cols")}
            <input name="cols" type="number" min="1" max="8" value="2" required /></label>
        </div>
        <fieldset class="spanned-fields hidden">
          <legend>${Khan.t("walls.canvas_resolution", "Canvas resolution")}</legend>
          <select name="canvas_resolution">
            <option value="1920x1080">1080p (1920×1080)</option>
            <option value="3840x2160" selected>4K (3840×2160)</option>
            <option value="7680x4320">8K (7680×4320)</option>
          </select>
          <label>${Khan.t("walls.bezel_horizontal_pct", "Horizontal bezel %")}
            <input type="number" name="bezel_h_pct" min="0" max="10" step="0.1" value="0" /></label>
          <label>${Khan.t("walls.bezel_vertical_pct", "Vertical bezel %")}
            <input type="number" name="bezel_v_pct" min="0" max="10" step="0.1" value="0" /></label>
        </fieldset>
        <fieldset class="mirrored-fields">
          <legend>${Khan.t("walls.mirrored_submode", "Mirrored sub-mode")}</legend>
          <label><input type="radio" name="mirrored_mode" value="same_playlist" checked />
            ${Khan.t("walls.same_playlist", "Same playlist on all screens")}</label>
          <label><input type="radio" name="mirrored_mode" value="synced_rotation" />
            ${Khan.t("walls.synced_rotation", "Different playlist per cell, synchronized rotation")}</label>
          <label class="same-playlist-only">
            ${Khan.t("walls.playlist", "Playlist")}
            <select name="mirrored_playlist_id" required>
              ${playlists.map(p => `<option value="${p.id}">${escHtml(p.name)}</option>`).join("")}
            </select>
          </label>
        </fieldset>
        <div class="modal-actions">
          <button type="submit" class="btn btn-primary">${Khan.t("walls.save", "Create")}</button>
          <button type="button" class="btn btn-ghost" id="walls-wizard-cancel">${Khan.t("walls.cancel", "Cancel")}</button>
        </div>
      </form>
    `;
    body.querySelector("#walls-wizard-cancel").addEventListener("click", closeEditor);
    body.querySelector("#walls-wizard").addEventListener("submit", submitWizard);
    body.querySelectorAll('input[name="mirrored_mode"]').forEach(el => {
      el.addEventListener("change", () => {
        const same = body.querySelector('input[name="mirrored_mode"]:checked').value === "same_playlist";
        body.querySelector(".same-playlist-only").style.display = same ? "" : "none";
      });
    });
    body.querySelectorAll('input[name="mode"]').forEach(el => {
      el.addEventListener("change", () => {
        const mode = body.querySelector('input[name="mode"]:checked').value;
        body.querySelector(".spanned-fields").classList.toggle("hidden", mode !== "spanned");
        body.querySelector(".mirrored-fields").classList.toggle("hidden", mode !== "mirrored");
      });
    });
  }

  async function submitWizard(ev) {
    ev.preventDefault();
    const f = ev.target;
    const payload = {
      name: f.name.value.trim(),
      mode: f.mode.value,
      rows: parseInt(f.rows.value, 10),
      cols: parseInt(f.cols.value, 10),
    };
    if (f.mode.value === "spanned") {
      const [w, h] = f.canvas_resolution.value.split("x").map(Number);
      payload.canvas_width_px = w;
      payload.canvas_height_px = h;
      payload.bezel_h_pct = parseFloat(f.bezel_h_pct.value) || 0;
      payload.bezel_v_pct = parseFloat(f.bezel_v_pct.value) || 0;
    } else {
      const sub = f.mirrored_mode.value;
      payload.mirrored_mode = sub;
      if (sub === "same_playlist") payload.mirrored_playlist_id = parseInt(f.mirrored_playlist_id.value, 10);
    }
    try {
      const w = await api("/walls", { method: "POST", body: JSON.stringify(payload) });
      toast(Khan.t("walls.created", "Wall created"));
      await loadList();
      openEditor(w.id);
    } catch (err) {
      toast(err.message || Khan.t("walls.create_failed", "Couldn't create wall"), "error");
    }
  }

  async function deleteWall(id) {
    if (!(await confirmDialog({
      title:   Khan.t("walls.confirm_delete_title", "Delete wall"),
      message: Khan.t("walls.confirm_delete", "Delete this wall? Paired screens will revert to standalone."),
      confirmLabel: Khan.t("confirm.delete_label", "Delete"),
      danger:  true,
    }))) return;
    try {
      await api(`/walls/${id}`, { method: "DELETE" });
      toast(Khan.t("walls.deleted", "Wall deleted"));
      await loadList();
    } catch (err) {
      toast(err.message || Khan.t("walls.delete_failed", "Couldn't delete wall"), "error");
    }
  }

  function closeEditor() {
    if (mosaicTimer) { clearInterval(mosaicTimer); mosaicTimer = null; }
    if (pairTimer)   { clearInterval(pairTimer);   pairTimer   = null; }
    document.getElementById("walls-pair-modal")?.classList.add("hidden");
    state.pairing = null;
    document.getElementById("walls-editor").classList.add("hidden");
    state.editing = null;
  }

  // openEditor and pair-flow are filled in by Task 9.
  async function openEditor(id) {
    state.editing = id;
    document.getElementById("walls-editor").classList.remove("hidden");
    document.getElementById("walls-editor-body").innerHTML =
      `<p>${Khan.t("walls.editor_loading", "Loading…")}</p>`;
    // Implementation continues in Task 9.
    if (typeof Walls.renderEditor === "function") {
      await Walls.renderEditor(id);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("walls-create-btn");
    if (btn) btn.addEventListener("click", createWizard);
    const close = document.getElementById("walls-editor-close");
    if (close) close.addEventListener("click", closeEditor);
  });

  let pairTimer = null;
  let mosaicTimer = null;

  async function renderEditor(id) {
    const wall = await api(`/walls/${id}`);
    if (wall.mode === "spanned") {
      return renderCanvasEditor(wall);
    }
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent = wall.name;
    const playlists = wall.mirrored_mode === "synced_rotation" ? await api("/playlists") : [];
    body.innerHTML = `
      <div class="walls-editor-summary">
        <span class="walls-meta">
          ${wall.mode === "mirrored"
            ? Khan.t("walls.mode_mirrored", "Mirrored") + (wall.mirrored_mode === "synced_rotation"
                ? " · " + Khan.t("walls.synced_rotation_short", "Synced rotation")
                : " · " + Khan.t("walls.same_playlist_short", "Same playlist"))
            : Khan.t("walls.mode_spanned", "Spanned")}
          · ${wall.rows}×${wall.cols}
        </span>
      </div>
      <div class="walls-editor-grid"
           style="grid-template-columns: repeat(${wall.cols}, 1fr);">
        ${wall.cells.map(c => renderCellTile(c, wall, playlists)).join("")}
      </div>
    `;
    body.querySelectorAll(".walls-editor-cell").forEach(el => {
      const r = parseInt(el.dataset.row, 10);
      const c = parseInt(el.dataset.col, 10);
      el.querySelector('[data-action="pair"]')?.addEventListener("click",
        () => openPairModal(wall.id, r, c));
      el.querySelector('[data-action="unpair"]')?.addEventListener("click",
        () => unpairCell(wall.id, r, c));
      el.querySelector('select[data-action="cell-playlist"]')?.addEventListener("change", (ev) =>
        patchCell(wall.id, r, c, {
          playlist_id: ev.target.value === "" ? null : parseInt(ev.target.value, 10)
        }));
    });
    mountModeSwitchButton(wall);
    refreshMosaic(wall.id);
  }

  function mountModeSwitchButton(wall) {
    const header = document.querySelector(".walls-editor-header");
    if (!header) return;
    header.querySelector(".walls-mode-switch-btn")?.remove();
    const otherMode = wall.mode === "spanned" ? "mirrored" : "spanned";
    const modeBtn = document.createElement("button");
    modeBtn.className = "btn btn-ghost walls-mode-switch-btn";
    modeBtn.textContent = Khan.t(`walls.switch_to_${otherMode}`,
      `Switch to ${otherMode}`);
    modeBtn.addEventListener("click", () => openModeChangeModal(wall, otherMode));
    header.appendChild(modeBtn);
  }

  function renderCellTile(c, wall, playlists) {
    const paired = !!c.screen_id;
    const playlistPicker = wall.mirrored_mode === "synced_rotation"
      ? `<label class="cell-playlist">
           ${Khan.t("walls.playlist", "Playlist")}
           <select data-action="cell-playlist">
             <option value="">—</option>
             ${playlists.map(p =>
               `<option value="${p.id}" ${p.id === c.playlist_id ? "selected" : ""}>${escHtml(p.name)}</option>`
             ).join("")}
           </select>
         </label>` : "";
    return `
      <div class="walls-editor-cell ${paired ? "paired" : "empty"}"
           data-row="${c.row_index}" data-col="${c.col_index}">
        <strong>(${c.row_index},${c.col_index})</strong>
        ${paired
          ? `<span>${Khan.t("walls.cell_paired", "Paired")}</span>
             <button class="btn btn-ghost" data-action="unpair">${Khan.t("walls.cell_unpair", "Unpair")}</button>`
          : `<button class="btn" data-action="pair">${Khan.t("walls.cell_pair", "Pair this screen")}</button>`}
        ${playlistPicker}
      </div>
    `;
  }

  async function openPairModal(wallId, row, col) {
    const modal = document.getElementById("walls-pair-modal");
    modal.classList.remove("hidden");
    state.pairing = { wallId, row, col };
    await refreshPairCode();
    document.getElementById("walls-pair-refresh").onclick = refreshPairCode;
    document.getElementById("walls-pair-close").onclick = () => {
      modal.classList.add("hidden");
      state.pairing = null;
      if (pairTimer) { clearInterval(pairTimer); pairTimer = null; }
      renderEditor(wallId);
    };
  }

  async function refreshPairCode() {
    if (!state.pairing) return;
    if (pairTimer) { clearInterval(pairTimer); pairTimer = null; }
    const { wallId, row, col } = state.pairing;
    try {
      const r = await api(`/walls/${wallId}/cells/${row}/${col}/pair`, { method: "POST" });
      document.getElementById("walls-pair-code").textContent = r.code;
      let remaining = r.expires_in_seconds;
      const setLabel = () => {
        const m = Math.floor(remaining / 60);
        const s = String(remaining % 60).padStart(2, "0");
        document.getElementById("walls-pair-countdown").textContent =
          Khan.t("walls.pair_expires_in", "Expires in {time}").replace("{time}", `${m}:${s}`);
      };
      setLabel();
      pairTimer = setInterval(() => {
        remaining = Math.max(0, remaining - 1);
        setLabel();
        if (remaining === 0) clearInterval(pairTimer);
      }, 1000);
    } catch (err) {
      toast(err.message || Khan.t("walls.pair_code_failed", "Couldn't get pair code"), "error");
    }
  }

  async function unpairCell(wallId, row, col) {
    if (!(await confirmDialog({
      title:   Khan.t("walls.confirm_unpair_title", "Unpair cell"),
      message: Khan.t("walls.confirm_unpair", "Unpair this cell?"),
      confirmLabel: Khan.t("walls.unpair_label", "Unpair"),
      danger:  true,
    }))) return;
    try {
      await api(`/walls/${wallId}/cells/${row}/${col}/pairing`, { method: "DELETE" });
      toast(Khan.t("walls.unpaired", "Unpaired"));
      renderEditor(wallId);
    } catch (err) {
      toast(err.message || Khan.t("walls.unpair_failed", "Couldn't unpair"), "error");
    }
  }

  async function patchCell(wallId, row, col, fields) {
    try {
      await api(`/walls/${wallId}/cells`, {
        method: "PATCH",
        body: JSON.stringify({ row_index: row, col_index: col, ...fields }),
      });
      toast(Khan.t("walls.cell_updated", "Cell updated"));
    } catch (err) {
      toast(err.message || Khan.t("walls.cell_update_failed", "Couldn't update cell"), "error");
    }
  }

  async function refreshMosaic(wallId) {
    if (mosaicTimer) clearInterval(mosaicTimer);
    let timerId;
    const tick = async () => {
      if (state.editing !== wallId) { clearInterval(timerId); return; }
      try {
        const w = await api(`/walls/${wallId}`);
        document.querySelectorAll(".walls-editor-cell").forEach(el => {
          const r = parseInt(el.dataset.row, 10);
          const c = parseInt(el.dataset.col, 10);
          const cell = w.cells.find(x => x.row_index === r && x.col_index === c);
          if (!cell) return;
          el.classList.toggle("paired", !!cell.screen_id);
          el.classList.toggle("empty", !cell.screen_id);
        });
      } catch (_) { /* ignore */ }
    };
    timerId = setInterval(tick, 5000);
    mosaicTimer = timerId;
  }

  async function renderCanvasEditor(wall) {
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent = wall.name;
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
    state.editing = wall.id;
    body.innerHTML = `
      <div class="canvas-editor-summary">
        <span class="walls-meta">
          ${Khan.t("walls.mode_spanned", "Spanned")} ·
          ${wall.canvas_width_px}×${wall.canvas_height_px} ·
          ${wall.rows}×${wall.cols}
        </span>
      </div>
      <div class="canvas-editor-grid">
        <div class="canvas-editor-rail">
          <h4>${Khan.t("walls.canvas_items", "Items")}</h4>
          <ul id="canvas-items-list"></ul>
          <button id="canvas-add-item" class="btn">${Khan.t("walls.canvas_add_item", "Add item")}</button>
        </div>
        <div class="canvas-editor-preview" id="canvas-preview">
          <div class="canvas-bezel-grid"
               style="grid-template-columns: repeat(${wall.cols}, 1fr);
                      grid-template-rows: repeat(${wall.rows}, 1fr);
                      aspect-ratio: ${wall.canvas_width_px} / ${wall.canvas_height_px};
                      gap: ${wall.bezel_h_pct}% ${wall.bezel_v_pct}%;">
            ${Array.from({length: wall.rows * wall.cols}).map(() =>
              `<div class="canvas-bezel-cell"></div>`).join("")}
          </div>
          <div id="canvas-preview-media" class="canvas-preview-media"></div>
        </div>
      </div>
      <div id="canvas-item-detail" class="canvas-item-detail hidden">
        <h4>${Khan.t("walls.selected_item", "Selected item")}</h4>
        <label>${Khan.t("walls.duration_override_seconds", "Duration (seconds)")}
          <input id="canvas-item-duration" type="number" min="1" max="86400" /></label>
        <fieldset>
          <legend>${Khan.t("walls.fit_mode", "Fit mode")}</legend>
          <label><input type="radio" name="fit" value="fit" />${Khan.t("walls.fit_fit", "Fit")}</label>
          <label><input type="radio" name="fit" value="fill" />${Khan.t("walls.fit_fill", "Fill")}</label>
          <label><input type="radio" name="fit" value="stretch" />${Khan.t("walls.fit_stretch", "Stretch")}</label>
        </fieldset>
        <button id="canvas-item-save" class="btn btn-primary">${Khan.t("walls.save", "Save")}</button>
        <button id="canvas-item-delete" class="btn btn-danger">${Khan.t("walls.delete", "Delete")}</button>
      </div>
    `;
    renderCanvasItemList(wall, list.items);
    body.querySelector("#canvas-add-item").addEventListener("click",
      () => openCanvasMediaPicker(wall));
    mountModeSwitchButton(wall);
  }

  function renderCanvasItemList(wall, items) {
    const root = document.getElementById("canvas-items-list");
    if (!items.length) {
      root.innerHTML = `<li class="empty">${Khan.t("walls.canvas_empty", "No items yet.")}</li>`;
      return;
    }
    root.innerHTML = items.map(it => `
      <li data-item-id="${it.id}">
        <span>${escHtml(it.media_name)}</span>
        <small>${it.fit_mode} · ${it.duration_override_seconds || it.duration_seconds}s</small>
      </li>
    `).join("");
    root.querySelectorAll("[data-item-id]").forEach(li => {
      li.addEventListener("click", () => selectCanvasItem(wall, items.find(
        it => String(it.id) === li.dataset.itemId)));
    });
  }

  function selectCanvasItem(wall, item) {
    state.canvasSelectedItem = item;
    const detail = document.getElementById("canvas-item-detail");
    detail.classList.remove("hidden");
    detail.querySelector("#canvas-item-duration").value =
      item.duration_override_seconds || item.duration_seconds || "";
    detail.querySelectorAll('input[name="fit"]').forEach(el => {
      el.checked = el.value === item.fit_mode;
    });
    detail.querySelector("#canvas-item-save").onclick = () => saveCanvasItem(wall, item);
    detail.querySelector("#canvas-item-delete").onclick = () => deleteCanvasItem(wall, item);
    const preview = document.getElementById("canvas-preview-media");
    if (item.mime_type.startsWith("video/")) {
      preview.innerHTML = `<video src="${item.filename ? '/uploads/' + item.filename : ''}"
        muted autoplay loop playsinline></video>`;
    } else if (item.mime_type === "application/pdf") {
      preview.innerHTML = `<div class="pdf-thumb">PDF — ${escHtml(item.media_name)}</div>`;
    } else {
      preview.innerHTML = `<img src="/uploads/${item.filename || ''}" alt="" />`;
    }
  }

  async function saveCanvasItem(wall, item) {
    const detail = document.getElementById("canvas-item-detail");
    const dur = parseInt(detail.querySelector("#canvas-item-duration").value, 10);
    const fit = detail.querySelector('input[name="fit"]:checked')?.value || "fit";
    try {
      await api(`/walls/${wall.id}/canvas-playlist/items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({duration_override_seconds: isNaN(dur) ? null : dur, fit_mode: fit}),
      });
      toast(Khan.t("walls.cell_updated", "Updated"));
      await renderCanvasEditor(wall);
    } catch (err) {
      toast(err.message || Khan.t("walls.cell_update_failed", "Couldn't update"), "error");
    }
  }

  async function deleteCanvasItem(wall, item) {
    if (!(await confirmDialog({
      title:   Khan.t("walls.canvas_confirm_delete_title", "Delete item"),
      message: Khan.t("walls.canvas_confirm_delete", "Delete this item?"),
      confirmLabel: Khan.t("confirm.delete_label", "Delete"),
      danger:  true,
    }))) return;
    try {
      await api(`/walls/${wall.id}/canvas-playlist/items/${item.id}`, {method: "DELETE"});
      await renderCanvasEditor(wall);
    } catch (err) {
      toast(err.message || "delete failed", "error");
    }
  }

  async function openCanvasMediaPicker(wall) {
    let picks;
    try {
      picks = await MediaPicker.open({ allowedTypes: ["image", "video", "pdf"] });
    } catch (e) {
      if (e && e.cancelled) return;
      throw e;
    }
    if (!picks.length) return;
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
    let position = list.items.length;
    try {
      for (const p of picks) {
        const body = { media_id: p.media_id, position, fit_mode: "fit" };
        if (p.duration_seconds != null) body.duration_override_seconds = p.duration_seconds;
        await api(`/walls/${wall.id}/canvas-playlist/items`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        position++;
      }
      toast(Khan.t("walls.canvas_added", "Item added"));
      await renderCanvasEditor(wall);
    } catch (err) {
      toast(err.message || "add failed", "error");
    }
  }

  function openModeChangeModal(wall, newMode) {
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modal-card">
        <h3>${Khan.t("walls.mode_change_confirm_title", "Switch wall mode")}</h3>
        <p>${Khan.t("walls.mode_change_confirm_body",
          "Switching this wall to {mode} will permanently delete its current playlist. Cell pairings stay.")
          .replace("{mode}", Khan.t(`walls.mode_${newMode}`, newMode))}</p>
        <p>${Khan.t("walls.mode_change_type_name_to_confirm",
          "Type the wall name to confirm:")}</p>
        <input id="mode-change-typed" autocomplete="off" />
        <div class="modal-actions">
          <button class="btn" id="mode-change-cancel">${Khan.t("walls.cancel", "Cancel")}</button>
          <button class="btn btn-danger" id="mode-change-switch" disabled>${
            Khan.t("walls.mode_change_switch_btn", "Switch")}</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const typed = overlay.querySelector("#mode-change-typed");
    const switchBtn = overlay.querySelector("#mode-change-switch");
    typed.addEventListener("input", () => {
      switchBtn.disabled = typed.value !== wall.name;
    });
    overlay.querySelector("#mode-change-cancel").addEventListener("click",
      () => overlay.remove());
    switchBtn.addEventListener("click", async () => {
      const payload = {mode: newMode};
      if (newMode === "spanned") {
        payload.canvas_width_px = 3840;
        payload.canvas_height_px = 2160;
        payload.bezel_h_pct = 0;
        payload.bezel_v_pct = 0;
      } else {
        payload.mirrored_mode = "same_playlist";
      }
      try {
        await api(`/walls/${wall.id}`, {method: "PATCH", body: JSON.stringify(payload)});
        toast(Khan.t("walls.mode_changed", "Mode changed"));
        overlay.remove();
        await loadList();
        openEditor(wall.id);
      } catch (err) {
        toast(err.message || "mode change failed", "error");
      }
    });
  }

  return {
    onShow: loadList,
    state,
    api,
    loadList,
    openEditor,
    closeEditor,
    renderList,
    renderEditor,
  };
})();

// ── Audit Log (Phase 2.5c) ───────────────────────────────────────────
const AuditLog = (() => {
  const PAGE_SIZE = 50;
  let offset = 0;
  let total = 0;
  let actorFilterPopulated = false;

  async function show() {
    if (!actorFilterPopulated) await populateActorFilter();
    offset = 0;
    await fetchPage();
  }

  async function populateActorFilter() {
    const sel = document.getElementById("audit-filter-actor");
    if (!sel) return;
    try {
      const users = await api("/users");
      users.forEach(u => {
        const opt = document.createElement("option");
        opt.value = u.id;
        opt.textContent = u.username;
        sel.appendChild(opt);
      });
      actorFilterPopulated = true;
    } catch (_) { /* swallow — leave dropdown with just "All" */ }
  }

  async function fetchPage() {
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(offset));
    const action = document.getElementById("audit-filter-action").value;
    const actor  = document.getElementById("audit-filter-actor").value;
    const since  = document.getElementById("audit-filter-since").value;
    const until  = document.getElementById("audit-filter-until").value;
    if (action) params.set("action", action);
    if (actor)  params.set("actor_id", actor);
    if (since)  params.set("since", new Date(since).toISOString());
    if (until)  params.set("until", new Date(until).toISOString());

    try {
      const body = await api(`/audit-log?${params}`);
      total = body.total;
      renderRows(body.items);
      renderPagination();
    } catch (err) {
      toast(Khan.t("audit_log.error.fetch", "Failed to load audit log."), "error");
    }
  }

  function renderRows(items) {
    const tbody = document.getElementById("audit-log-tbody");
    tbody.innerHTML = "";
    if (!items.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.textContent = Khan.t("audit_log.empty", "No audit events match these filters.");
      td.className = "audit-log-empty";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    items.forEach(it => {
      const tr = document.createElement("tr");
      tr.appendChild(_td(formatWhen(it.created_at)));
      tr.appendChild(_td(it.actor ? it.actor.username : "—"));
      tr.appendChild(_td(it.action));
      tr.appendChild(_td(it.target ? `${it.target.type}#${it.target.id}` : "—"));
      tr.appendChild(_td(it.ip || "—"));
      tr.appendChild(_td(it.details ? JSON.stringify(it.details) : "—"));
      tbody.appendChild(tr);
    });
  }

  function _td(text) {
    const td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function formatWhen(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch (_) { return iso; }
  }

  function renderPagination() {
    const info = document.getElementById("audit-page-info");
    const start = total ? offset + 1 : 0;
    const end   = Math.min(offset + PAGE_SIZE, total);
    info.textContent = Khan.t(
      "audit_log.pagination.info",
      "{start}–{end} of {total}"
    ).replace("{start}", start).replace("{end}", end).replace("{total}", total);
    document.getElementById("audit-page-newer").disabled = offset === 0;
    document.getElementById("audit-page-older").disabled = end >= total;
  }

  function init() {
    document.getElementById("audit-filter-apply")?.addEventListener("click", () => {
      offset = 0;
      fetchPage();
    });
    document.getElementById("audit-page-newer")?.addEventListener("click", () => {
      offset = Math.max(0, offset - PAGE_SIZE);
      fetchPage();
    });
    document.getElementById("audit-page-older")?.addEventListener("click", () => {
      offset += PAGE_SIZE;
      fetchPage();
    });
  }

  return { show, init };
})();

AuditLog.init();

