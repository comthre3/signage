const urlOverrides = new URLSearchParams(window.location.search);
const CONNECTION_STORAGE_KEY = "signage_connection_mode";
const CONNECTION_API_KEY = "signage_connection_api";
const CONNECTION_PLAYER_KEY = "signage_connection_player";

const savedMode = localStorage.getItem(CONNECTION_STORAGE_KEY) || "local";
const savedApiBase = localStorage.getItem(CONNECTION_API_KEY) || "";
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

let authToken = localStorage.getItem(AUTH_STORAGE_KEY);
let currentUser = null;

const state = {
  sites: [],
  screens: [],
  playlists: [],
  media: [],
  users: [],
  groups: [],
};
const zonesState = {
  screenId: null,
  zones: [],
  dragging: null,
  drawing: null,
  snapEnabled: true,
  gridStep: 0.05,
  screen: null,
};

const sections = document.querySelectorAll(".panel");
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => showSection(btn.dataset.section));
});

function showSection(id) {
  sections.forEach((section) => {
    section.classList.toggle("hidden", section.id !== id);
  });
}

function buildPlayerUrl(base, params) {
  const url = new URL(base, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      url.searchParams.set(key, value);
    }
  });
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
  const select = document.getElementById("screen-resolution");
  const customInput = document.getElementById("screen-resolution-custom");
  if (!select) return null;
  if (select.value === "custom") {
    return customInput?.value.trim() || null;
  }
  return select.value || null;
}

function updateResolutionCustomVisibility() {
  const select = document.getElementById("screen-resolution");
  const customInput = document.getElementById("screen-resolution-custom");
  if (!select || !customInput) return;
  const showCustom = select.value === "custom";
  customInput.classList.toggle("hidden", !showCustom);
  if (!showCustom) {
    customInput.value = "";
  }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401) {
      handleAuthFailure();
    }
    const text = await res.text();
    throw new Error(text || "Request failed");
  }
  return res.json();
}

function setAuth(token, user) {
  authToken = token;
  currentUser = user;
  if (token) {
    localStorage.setItem(AUTH_STORAGE_KEY, token);
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
  updateAuthUI();
}

function handleAuthFailure() {
  setAuth(null, null);
  showAuthPanel();
}

function updateAuthUI() {
  const authUser = document.getElementById("auth-user");
  const logoutBtn = document.getElementById("logout-btn");
  const nav = document.querySelector("header nav");
  const usersBtn = document.querySelector('button[data-section="users"]');
  if (currentUser) {
    authUser.textContent = `${currentUser.username} (${currentUser.role})`;
    logoutBtn.classList.remove("hidden");
    nav.classList.remove("hidden");
    if (usersBtn) {
      usersBtn.classList.toggle("hidden", currentUser.role !== "admin");
    }
  } else {
    authUser.textContent = "";
    logoutBtn.classList.add("hidden");
    nav.classList.add("hidden");
    if (usersBtn) {
      usersBtn.classList.add("hidden");
    }
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
    state.users = [];
    state.groups = [];
    renderUsers();
    renderGroups();
    return;
  }
  state.users = await api("/users");
  state.groups = await api("/groups");
  renderUsers();
  renderGroups();
}

function renderSites() {
  const container = document.getElementById("sites-list");
  container.innerHTML = "";
  state.sites.forEach((site) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${site.name}</h3>
      <div>Slug: ${site.slug}</div>
      <div class="card-actions">
        <button class="delete-btn" data-id="${site.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async () => {
      await api(`/sites/${site.id}`, { method: "DELETE" });
      await loadSites();
      await loadScreens();
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
      <h3>${playlist.name}</h3>
      <div class="card-actions">
        <button class="delete-btn" data-id="${playlist.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async () => {
      await api(`/playlists/${playlist.id}`, { method: "DELETE" });
      await loadPlaylists();
      await loadScreens();
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
    const url = isUrl ? item.url : `${API_BASE}${item.url}`;
    card.innerHTML = `
      <h3>${item.name}</h3>
      <div>Type: ${item.mime_type}</div>
      <div><a href="${url}" target="_blank" rel="noreferrer">Open</a></div>
      <div class="card-actions">
        <button class="delete-btn" data-id="${item.id}">Delete</button>
      </div>
    `;
    card.querySelector(".delete-btn").addEventListener("click", async () => {
      await api(`/media/${item.id}`, { method: "DELETE" });
      await loadMedia();
    });
    container.appendChild(card);
  });
}

function renderUsers() {
  const container = document.getElementById("users-list");
  container.innerHTML = "";
  if (currentUser?.role !== "admin") {
    container.innerHTML = "<div class='card'>Admin access required.</div>";
    return;
  }
  state.users.forEach((user) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${user.username}</h3>
      <div>Role: ${user.role}</div>
      <div class="card-actions">
        <input type="password" placeholder="New password" data-user-pass="${user.id}" />
        <button class="save-btn" data-user-reset="${user.id}">Reset Password</button>
        <select data-user-role="${user.id}">
          <option value="viewer">Viewer</option>
          <option value="editor">Editor</option>
          <option value="admin">Admin</option>
        </select>
        <button class="save-btn" data-user-role-save="${user.id}">Update Role</button>
        <button class="delete-btn" data-user-delete="${user.id}">Delete</button>
      </div>
      <div class="card-actions" data-user-groups="${user.id}"></div>
    `;
    card.querySelector(`[data-user-reset="${user.id}"]`).addEventListener(
      "click",
      async () => {
        const input = card.querySelector(`[data-user-pass="${user.id}"]`);
        const password = input.value.trim();
        if (!password) return;
        await api(`/users/${user.id}`, {
          method: "PUT",
          body: JSON.stringify({ password }),
        });
        input.value = "";
      }
    );
    card.querySelector(`[data-user-delete="${user.id}"]`).addEventListener(
      "click",
      async () => {
        await api(`/users/${user.id}`, { method: "DELETE" });
        await loadUsers();
      }
    );
    const roleSelect = card.querySelector(`[data-user-role="${user.id}"]`);
    roleSelect.value = user.role;
    card.querySelector(`[data-user-role-save="${user.id}"]`).addEventListener(
      "click",
      async () => {
        await api(`/users/${user.id}`, {
          method: "PUT",
          body: JSON.stringify({ role: roleSelect.value }),
        });
        await loadUsers();
      }
    );
    const groupsContainer = card.querySelector(`[data-user-groups="${user.id}"]`);
    state.groups.forEach((group) => {
      const label = document.createElement("label");
      label.className = "group-chip";
      label.innerHTML = `
        <input type="checkbox" data-user-group="${user.id}:${group.id}" />
        <span>${group.name}</span>
      `;
      groupsContainer.appendChild(label);
    });
    api(`/users/${user.id}/groups`)
      .then((data) => {
        const groupIds = new Set((data.groups || []).map((group) => group.id));
        groupsContainer.querySelectorAll("[data-user-group]").forEach((input) => {
          const [, groupId] = input.dataset.userGroup.split(":").map(Number);
          input.checked = groupIds.has(groupId);
        });
      })
      .catch(() => {});
    card.querySelectorAll("[data-user-group]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const [userId] = checkbox.dataset.userGroup.split(":").map(Number);
        const selected = Array.from(
          card.querySelectorAll("[data-user-group]:checked")
        ).map((input) => Number(input.dataset.userGroup.split(":")[1]));
        await api(`/users/${userId}/groups`, {
          method: "PUT",
          body: JSON.stringify({ group_ids: selected }),
        });
      });
    });
    container.appendChild(card);
  });
}

function renderGroups() {
  const container = document.getElementById("groups-list");
  container.innerHTML = "";
  if (currentUser?.role !== "admin") return;
  state.groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${group.name}</h3>
      <div class="card-actions">
        <button class="delete-btn" data-group-delete="${group.id}">Delete</button>
      </div>
    `;
    card.querySelector(`[data-group-delete="${group.id}"]`).addEventListener(
      "click",
      async () => {
        await api(`/groups/${group.id}`, { method: "DELETE" });
        await loadUsers();
      }
    );
    container.appendChild(card);
  });
}

function renderScreens() {
  const container = document.getElementById("screens-list");
  container.innerHTML = "";
  state.screens.forEach((screen) => {
    const card = document.createElement("div");
    card.className = "card";
    const base = getPlayerBaseWithOverrides();
    const playerUrl = base.includes("?")
      ? `${base}&code=${screen.pair_code}`
      : `${base}/?code=${screen.pair_code}`;
    const statusClass = screen.is_online ? "status-online" : "status-offline";
    const playlistOptions = [
      `<option value="">No playlist</option>`,
      ...state.playlists.map(
        (playlist) =>
          `<option value="${playlist.id}">${playlist.name}</option>`
      ),
    ].join("");
    card.innerHTML = `
      <h3>${screen.name}</h3>
      <div>Site: ${screen.site_name || "Unassigned"}</div>
      <div>Location: ${screen.location || "-"}</div>
      <div>Resolution: ${screen.resolution || "-"}</div>
      <div>Orientation: ${screen.orientation || "-"}</div>
      <div>Pairing code: <strong>${screen.pair_code}</strong></div>
      <div>Player URL: <a href="${playerUrl}" target="_blank" rel="noreferrer">${playerUrl}</a></div>
      <div>Status: <span class="${statusClass}">${screen.is_online ? "Online" : "Offline"}</span></div>
      <div class="card-actions">
        <select data-playlist-select="${screen.id}">
          ${playlistOptions}
        </select>
        <button class="save-btn" data-save-screen="${screen.id}">Save</button>
        <button class="save-btn" data-zones-screen="${screen.id}">Zones</button>
        <button class="preview-btn" data-preview-screen="${screen.id}">Preview</button>
        <button class="delete-btn" data-delete-screen="${screen.id}">Delete</button>
      </div>
    `;
    const select = card.querySelector(`[data-playlist-select="${screen.id}"]`);
    select.value = screen.playlist_id || "";
    card.querySelector(`[data-save-screen="${screen.id}"]`).addEventListener(
      "click",
      async () => {
        const playlistId = select.value ? Number(select.value) : null;
        await api(`/screens/${screen.id}`, {
          method: "PUT",
          body: JSON.stringify({ playlist_id: playlistId }),
        });
        await loadScreens();
      }
    );
    card.querySelector(`[data-zones-screen="${screen.id}"]`).addEventListener(
      "contextmenu",
      async (event) => {
        event.preventDefault();
        await openScreenAccessEditor(screen.id);
      }
    );
    card.querySelector(`[data-preview-screen="${screen.id}"]`).addEventListener(
      "click",
      async () => {
        const preview = await api(`/screens/${screen.id}/preview-token`, { method: "POST" });
        const base = getPlayerBaseWithOverrides();
        const url = base.includes("?")
          ? `${base}&preview_token=${preview.token}`
          : `${base}/?preview_token=${preview.token}`;
        showPreview(screen, url, preview.expires_at);
      }
    );
    card.querySelector(`[data-delete-screen="${screen.id}"]`).addEventListener(
      "click",
      async () => {
        await api(`/screens/${screen.id}`, { method: "DELETE" });
        await loadScreens();
      }
    );
    card.querySelector(`[data-zones-screen="${screen.id}"]`).addEventListener(
      "click",
      async () => {
        await openZonesEditor(screen.id);
      }
    );
    container.appendChild(card);
  });
}

function showPreview(screen, previewUrl, expiresAt) {
  const panel = document.getElementById("preview-panel");
  const frame = document.getElementById("preview-frame");
  const meta = document.getElementById("preview-meta");
  meta.textContent = `Previewing ${screen.name} (${screen.site_name || "Unassigned"})` +
    (expiresAt ? ` · expires ${expiresAt}` : "");
  frame.src = previewUrl;
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function snapValue(value) {
  if (!zonesState.snapEnabled) return value;
  const step = zonesState.gridStep;
  return Math.round(value / step) * step;
}

function normalizeZone(zone) {
  return {
    ...zone,
    x: clamp(zone.x, 0, 1),
    y: clamp(zone.y, 0, 1),
    width: clamp(zone.width, 0.1, 1),
    height: clamp(zone.height, 0.1, 1),
  };
}

function setZones(zones) {
  zonesState.zones = zones.map((zone, index) => ({
    id: zone.id || `local-${index}`,
    name: zone.name || `Zone ${index + 1}`,
    x: zone.x,
    y: zone.y,
    width: zone.width,
    height: zone.height,
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
  const guideV = document.createElement("div");
  guideV.className = "zone-guide vertical hidden";
  const guideH = document.createElement("div");
  guideH.className = "zone-guide horizontal hidden";
  canvas.appendChild(guideV);
  canvas.appendChild(guideH);
  zonesState.zones.forEach((zone, index) => {
    const zoneEl = document.createElement("div");
    zoneEl.className = "zone-block";
    zoneEl.style.left = `${zone.x * 100}%`;
    zoneEl.style.top = `${zone.y * 100}%`;
    zoneEl.style.width = `${zone.width * 100}%`;
    zoneEl.style.height = `${zone.height * 100}%`;
    zoneEl.dataset.zoneIndex = index;
    zoneEl.innerHTML = `
      <div class="zone-title">${zone.name}</div>
      <div class="zone-handle zone-handle-left" data-handle="left"></div>
      <div class="zone-handle zone-handle-right" data-handle="right"></div>
      <div class="zone-handle zone-handle-top" data-handle="top"></div>
      <div class="zone-handle zone-handle-bottom" data-handle="bottom"></div>
    `;
    canvas.appendChild(zoneEl);
  });
  if (zonesState.drawing?.preview) {
    const preview = document.createElement("div");
    preview.className = "zone-block zone-preview";
    preview.style.left = `${zonesState.drawing.preview.x * 100}%`;
    preview.style.top = `${zonesState.drawing.preview.y * 100}%`;
    preview.style.width = `${zonesState.drawing.preview.width * 100}%`;
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
    const itemsHtml =
      zone.items
        ?.map(
          (item, itemIndex) => `
          <div class="zone-item">
            <span>${item.name || "Media"}</span>
            <input type="number" min="0" max="3600" value="${item.duration_seconds ?? 10}" data-zone-item-duration="${zoneIndex}:${itemIndex}" />
            <button class="delete-btn" data-zone-item-remove="${zoneIndex}:${itemIndex}">Remove</button>
          </div>
        `
        )
        .join("") || "<div class='helper-text'>No media yet.</div>";

    const mediaOptions = state.media
      .map((media) => `<option value="${media.id}">${media.name}</option>`)
      .join("");

    card.innerHTML = `
      <div class="zone-meta">
        <strong>${zone.name}</strong>
        <span>${Math.round(zone.width * 100)}% × ${Math.round(zone.height * 100)}%</span>
      </div>
      <div class="zone-transition">
        <label>Fade ms</label>
        <input type="number" min="0" max="5000" value="${zone.transition_ms ?? 600}" data-zone-transition="${zoneIndex}" />
      </div>
      <div class="zone-actions">
        <button class="delete-btn" data-zone-remove="${zoneIndex}">Delete Zone</button>
      </div>
      <div class="zone-add">
        <select data-zone-media="${zoneIndex}">
          <option value="">Add media</option>
          ${mediaOptions}
        </select>
        <input type="number" min="0" max="3600" value="10" data-zone-duration="${zoneIndex}" />
        <button class="save-btn" data-zone-add="${zoneIndex}">Add</button>
      </div>
      <div class="zone-items">${itemsHtml}</div>
    `;
    card.querySelector(`[data-zone-add="${zoneIndex}"]`).addEventListener("click", () => {
      const mediaSelect = card.querySelector(`[data-zone-media="${zoneIndex}"]`);
      const durationInput = card.querySelector(`[data-zone-duration="${zoneIndex}"]`);
      const mediaId = Number(mediaSelect.value || 0);
      if (!mediaId) return;
      const media = state.media.find((item) => item.id === mediaId);
      zone.items.push({
        media_id: mediaId,
        name: media?.name,
        duration_seconds: normalizeDuration(durationInput.value, 10),
      });
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
    card
      .querySelector(`[data-zone-transition="${zoneIndex}"]`)
      .addEventListener("change", (event) => {
        zonesState.zones[zoneIndex].transition_ms = normalizeDuration(event.target.value, 600);
      });
    list.appendChild(card);
  });
}

function normalizeDuration(value, fallback = 10) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function presetZones(type) {
  if (type === "single") {
    setZones([{ name: "Full", x: 0, y: 0, width: 1, height: 1, items: [] }]);
  }
  if (type === "columns-2") {
    setZones([
      { name: "Left", x: 0, y: 0, width: 0.5, height: 1, items: [] },
      { name: "Right", x: 0.5, y: 0, width: 0.5, height: 1, items: [] },
    ]);
  }
  if (type === "columns-3") {
    setZones([
      { name: "Left", x: 0, y: 0, width: 0.33, height: 1, items: [] },
      { name: "Center", x: 0.33, y: 0, width: 0.34, height: 1, items: [] },
      { name: "Right", x: 0.67, y: 0, width: 0.33, height: 1, items: [] },
    ]);
  }
  if (type === "rows-2") {
    setZones([
      { name: "Top", x: 0, y: 0, width: 1, height: 0.5, items: [] },
      { name: "Bottom", x: 0, y: 0.5, width: 1, height: 0.5, items: [] },
    ]);
  }
  if (type === "hero-side") {
    setZones([
      { name: "Hero", x: 0, y: 0, width: 0.7, height: 1, items: [] },
      { name: "Side", x: 0.7, y: 0, width: 0.3, height: 1, items: [] },
    ]);
  }
}

async function openZonesEditor(screenId) {
  zonesState.screenId = screenId;
  zonesState.screen = state.screens.find((screen) => screen.id === screenId) || null;
  const zonesPanel = document.getElementById("zones-editor");
  zonesPanel.classList.remove("hidden");
  const snapToggle = document.getElementById("zone-snap");
  if (snapToggle) {
    snapToggle.checked = zonesState.snapEnabled;
  }
  const gridSelect = document.getElementById("zone-grid");
  if (gridSelect) {
    gridSelect.value = String(zonesState.gridStep);
  }
  const data = await api(`/screens/${screenId}/zones`);
  if (!data.zones || data.zones.length === 0) {
    presetZones("columns-2");
  } else {
    setZones(data.zones);
  }
  await loadZoneTemplates();
  zonesPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeZonesEditor() {
  const zonesPanel = document.getElementById("zones-editor");
  zonesPanel.classList.add("hidden");
}

async function openScreenAccessEditor(screenId) {
  const screen = state.screens.find((item) => item.id === screenId);
  const panel = document.getElementById("screen-access-panel");
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
    label.innerHTML = `
      <input type="checkbox" data-screen-group="${screenId}:${group.id}" />
      <span>${group.name}</span>
    `;
    groupsList.appendChild(label);
  });

  const currentGroups = await api(`/screens/${screenId}/groups`);
  const currentIds = new Set(currentGroups.groups.map((group) => group.id));
  groupsList.querySelectorAll("[data-screen-group]").forEach((input) => {
    const [, groupId] = input.dataset.screenGroup.split(":").map(Number);
    input.checked = currentIds.has(groupId);
  });

  document.getElementById("screen-access-save").onclick = async () => {
    const ownerId = ownerSelect.value ? Number(ownerSelect.value) : null;
    await api(`/screens/${screenId}`, {
      method: "PUT",
      body: JSON.stringify({ owner_user_id: ownerId }),
    });
    const selected = Array.from(
      groupsList.querySelectorAll("[data-screen-group]:checked")
    ).map((input) => Number(input.dataset.screenGroup.split(":")[1]));
    await api(`/screens/${screenId}/groups`, {
      method: "PUT",
      body: JSON.stringify({ group_ids: selected }),
    });
    panel.classList.add("hidden");
    await loadScreens();
  };
  document.getElementById("screen-access-cancel").onclick = () => {
    panel.classList.add("hidden");
  };
}
async function loadZoneTemplates() {
  const select = document.getElementById("zone-template-select");
  if (!select) return;
  select.innerHTML = `<option value="">Apply template</option>`;
  const templates = await api("/zone-templates");
  templates.forEach((template) => {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.name;
    select.appendChild(option);
  });
}

function bindZoneEditorEvents() {
  document.getElementById("zone-add")?.addEventListener("click", () => {
    const index = zonesState.zones.length + 1;
    zonesState.zones.push({
      id: `local-${Date.now()}`,
      name: `Zone ${index}`,
      x: 0.1,
      y: 0.1,
      width: 0.3,
      height: 0.3,
      sort_order: index,
      transition_ms: 600,
      items: [],
    });
    renderZonesCanvas();
    renderZonesList();
  });
  document.getElementById("zone-snap")?.addEventListener("change", (event) => {
    zonesState.snapEnabled = event.target.checked;
    renderZonesCanvas();
  });
  document.getElementById("zone-grid")?.addEventListener("change", (event) => {
    zonesState.gridStep = Number(event.target.value || 0.05);
    renderZonesCanvas();
  });
  document.getElementById("zone-template-save")?.addEventListener("click", async () => {
    const name = document.getElementById("zone-template-name").value.trim();
    if (!name) return;
    await api("/zone-templates", {
      method: "POST",
      body: JSON.stringify({
        name,
        site_id: zonesState.screen?.site_id || null,
        zones: zonesState.zones.map((zone, index) => ({
          name: zone.name,
          x: zone.x,
          y: zone.y,
          width: zone.width,
          height: zone.height,
          sort_order: zone.sort_order ?? index,
          transition_ms: normalizeDuration(zone.transition_ms ?? 600, 600),
          items: zone.items?.map((item) => ({
            media_id: item.media_id,
            duration_seconds: normalizeDuration(item.duration_seconds ?? 10, 10),
          })),
        })),
      }),
    });
    document.getElementById("zone-template-name").value = "";
    await loadZoneTemplates();
  });
  document.getElementById("zone-template-apply")?.addEventListener("click", async () => {
    const templateId = Number(document.getElementById("zone-template-select").value || 0);
    if (!templateId || !zonesState.screenId) return;
    await api(`/screens/${zonesState.screenId}/zone-templates/apply`, {
      method: "POST",
      body: JSON.stringify({ template_id: templateId }),
    });
    const data = await api(`/screens/${zonesState.screenId}/zones`);
    setZones(data.zones || []);
  });
  document.querySelectorAll("[data-zone-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      presetZones(btn.dataset.zonePreset);
    });
  });
  document.getElementById("zones-save")?.addEventListener("click", async () => {
    if (!zonesState.screenId) return;
    await api(`/screens/${zonesState.screenId}/zones`, {
      method: "PUT",
      body: JSON.stringify({
        zones: zonesState.zones.map((zone, index) => ({
          name: zone.name,
          x: zone.x,
          y: zone.y,
          width: zone.width,
          height: zone.height,
          sort_order: zone.sort_order ?? index,
          transition_ms: normalizeDuration(zone.transition_ms ?? 600, 600),
          items: zone.items?.map((item) => ({
            media_id: item.media_id,
            duration_seconds: normalizeDuration(item.duration_seconds ?? 10, 10),
          })),
        })),
      }),
    });
    alert("Zones saved.");
  });
  document.getElementById("zones-cancel")?.addEventListener("click", closeZonesEditor);

  const canvas = document.getElementById("zones-canvas");
  canvas?.addEventListener("mousedown", (event) => {
    const handle = event.target.closest(".zone-handle");
    const zoneEl = event.target.closest(".zone-block");
    if (!zoneEl) return;
    const zoneIndex = Number(zoneEl.dataset.zoneIndex);
    if (!Number.isFinite(zoneIndex)) return;
    const canvasRect = canvas.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const original = { ...zonesState.zones[zoneIndex] };
    if (!handle) {
      zonesState.dragging = {
        zoneIndex,
        handle: "move",
        startX,
        startY,
        original,
        canvasRect,
      };
      return;
    }
    zonesState.dragging = {
      zoneIndex,
      handle: handle.dataset.handle,
      startX,
      startY,
      original,
      canvasRect,
    };
  });
  canvas?.addEventListener("mousedown", (event) => {
    if (event.target.closest(".zone-block")) return;
    const canvasRect = canvas.getBoundingClientRect();
    const startX = clamp((event.clientX - canvasRect.left) / canvasRect.width, 0, 1);
    const startY = clamp((event.clientY - canvasRect.top) / canvasRect.height, 0, 1);
    zonesState.drawing = {
      startX,
      startY,
      canvasRect,
    };
  });
  window.addEventListener("mousemove", (event) => {
    if (!zonesState.dragging) return;
    const { zoneIndex, handle, startX, startY, original, canvasRect } = zonesState.dragging;
    const deltaX = (event.clientX - startX) / canvasRect.width;
    const deltaY = (event.clientY - startY) / canvasRect.height;
    const zone = zonesState.zones[zoneIndex];
    const guidesV = canvas.querySelector(".zone-guide.vertical");
    const guidesH = canvas.querySelector(".zone-guide.horizontal");
    if (guidesV && guidesH) {
      guidesV.classList.add("hidden");
      guidesH.classList.add("hidden");
    }
    if (handle === "move") {
      const nextX = clamp(original.x + deltaX, 0, 1 - original.width);
      const nextY = clamp(original.y + deltaY, 0, 1 - original.height);
      zone.x = snapValue(nextX);
      zone.y = snapValue(nextY);
    }
    if (handle === "right") {
      zone.width = snapValue(
        Math.max(0.1, Math.min(1 - original.x, original.width + deltaX))
      );
    }
    if (handle === "left") {
      const nextX = snapValue(
        Math.max(0, Math.min(original.x + deltaX, original.x + original.width - 0.1))
      );
      zone.width = snapValue(original.width + (original.x - nextX));
      zone.x = nextX;
    }
    if (handle === "bottom") {
      zone.height = snapValue(
        Math.max(0.1, Math.min(1 - original.y, original.height + deltaY))
      );
    }
    if (handle === "top") {
      const nextY = snapValue(
        Math.max(0, Math.min(original.y + deltaY, original.y + original.height - 0.1))
      );
      zone.height = snapValue(original.height + (original.y - nextY));
      zone.y = nextY;
    }
    if (guidesV && guidesH) {
      const edgesX = [0, 1, zone.x, zone.x + zone.width];
      const edgesY = [0, 1, zone.y, zone.y + zone.height];
      const nearestX = edgesX.find((value) => Math.abs(value - Math.round(value)) < 0.01);
      const nearestY = edgesY.find((value) => Math.abs(value - Math.round(value)) < 0.01);
      if (nearestX !== undefined) {
        guidesV.style.left = `${nearestX * 100}%`;
        guidesV.classList.remove("hidden");
      }
      if (nearestY !== undefined) {
        guidesH.style.top = `${nearestY * 100}%`;
        guidesH.classList.remove("hidden");
      }
    }
    renderZonesCanvas();
  });
  window.addEventListener("mousemove", (event) => {
    if (!zonesState.drawing) return;
    const { startX, startY, canvasRect } = zonesState.drawing;
    const currentX = clamp((event.clientX - canvasRect.left) / canvasRect.width, 0, 1);
    const currentY = clamp((event.clientY - canvasRect.top) / canvasRect.height, 0, 1);
    const x = Math.min(startX, currentX);
    const y = Math.min(startY, currentY);
    const width = Math.max(0.1, Math.abs(currentX - startX));
    const height = Math.max(0.1, Math.abs(currentY - startY));
    zonesState.drawing.preview = {
      x: snapValue(x),
      y: snapValue(y),
      width: snapValue(width),
      height: snapValue(height),
    };
    renderZonesCanvas();
  });
  window.addEventListener("mouseup", () => {
    if (zonesState.drawing?.preview) {
      const index = zonesState.zones.length + 1;
      zonesState.zones.push({
        id: `local-${Date.now()}`,
        name: `Zone ${index}`,
        x: zonesState.drawing.preview.x,
        y: zonesState.drawing.preview.y,
        width: zonesState.drawing.preview.width,
        height: zonesState.drawing.preview.height,
        sort_order: index,
        transition_ms: 600,
        items: [],
      });
      renderZonesList();
    }
    zonesState.dragging = null;
    zonesState.drawing = null;
  });
}

async function loadPlaylistItems(playlistId) {
  if (!playlistId) {
    return;
  }
  const data = await api(`/playlists/${playlistId}`);
  const container = document.getElementById("playlists-list");
  container.innerHTML = "";
  const card = document.createElement("div");
  card.className = "card";
  const itemsHtml = data.items
    .map(
      (item) => `
        <div class="card">
          <div>${item.name}</div>
          <div>Duration: ${item.duration_seconds}s</div>
          <div class="card-actions">
            <button class="delete-btn" data-item-id="${item.id}">Remove</button>
          </div>
        </div>
      `
    )
    .join("");
  card.innerHTML = `
    <h3>${data.playlist.name} Items</h3>
    ${itemsHtml || "<div>No items yet.</div>"}
  `;
  container.appendChild(card);
  card.querySelectorAll("[data-item-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const itemId = btn.dataset.itemId;
      await api(`/playlists/${playlistId}/items/${itemId}`, { method: "DELETE" });
      await loadPlaylistItems(playlistId);
    });
  });
}

document.getElementById("site-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("site-name").value.trim();
  const slug = document.getElementById("site-slug").value.trim();
  await api("/sites", {
    method: "POST",
    body: JSON.stringify({ name, slug: slug || null }),
  });
  e.target.reset();
  await loadSites();
});

document.getElementById("screen-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("screen-name").value.trim(),
    location: document.getElementById("screen-location").value.trim() || null,
    resolution: getScreenResolutionInput(),
    orientation: document.getElementById("screen-orientation").value || null,
    site_id: document.getElementById("screen-site").value
      ? Number(document.getElementById("screen-site").value)
      : null,
  };
  try {
    await api("/screens", { method: "POST", body: JSON.stringify(payload) });
    e.target.reset();
    updateResolutionCustomVisibility();
    await loadScreens();
  } catch (err) {
    console.error(err);
    alert("Failed to add screen. Check the console for details.");
  }
});

document.getElementById("media-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("media-file");
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/media/upload`, {
    method: "POST",
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Upload failed");
  }
  input.value = "";
  await loadMedia();
});

document.getElementById("media-url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("media-url-name").value.trim();
  const url = document.getElementById("media-url").value.trim();
  if (!name || !url) return;
  await api("/media/url", {
    method: "POST",
    body: JSON.stringify({ name, url }),
  });
  e.target.reset();
  await loadMedia();
});

const mediaForm = document.getElementById("media-form");
mediaForm?.addEventListener("dragover", (event) => {
  event.preventDefault();
  mediaForm.classList.add("dropzone-active");
});
mediaForm?.addEventListener("dragleave", () => {
  mediaForm.classList.remove("dropzone-active");
});
mediaForm?.addEventListener("drop", async (event) => {
  event.preventDefault();
  mediaForm.classList.remove("dropzone-active");
  const files = Array.from(event.dataTransfer?.files || []);
  if (files.length === 0) return;
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/media/upload`, {
      method: "POST",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || "Upload failed");
    }
  }
  await loadMedia();
});

document.getElementById("playlist-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("playlist-name").value.trim();
  await api("/playlists", { method: "POST", body: JSON.stringify({ name }) });
  e.target.reset();
  await loadPlaylists();
});

document
  .getElementById("playlist-select")
  .addEventListener("change", async (e) => {
    await loadPlaylistItems(e.target.value);
  });

document
  .getElementById("playlist-add-item")
  .addEventListener("click", async () => {
    const playlistId = document.getElementById("playlist-select").value;
    const mediaId = document.getElementById("playlist-media").value;
    const duration = Number(document.getElementById("playlist-duration").value || 10);
    if (!playlistId || !mediaId) return;
    await api(`/playlists/${playlistId}/items`, {
      method: "POST",
      body: JSON.stringify({ media_id: Number(mediaId), duration_seconds: duration }),
    });
    await loadPlaylistItems(playlistId);
  });

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const data = await api("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setAuth(data.token, data.user);
  showDashboard();
  await bootData();
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  if (authToken) {
    await api("/auth/logout", { method: "POST" });
  }
  setAuth(null, null);
  showAuthPanel();
});

document.getElementById("user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentUser?.role !== "admin") return;
  const payload = {
    username: document.getElementById("user-username").value.trim(),
    password: document.getElementById("user-password").value,
    role: document.getElementById("user-role").value,
  };
  await api("/users", { method: "POST", body: JSON.stringify(payload) });
  e.target.reset();
  await loadUsers();
});

document.getElementById("group-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentUser?.role !== "admin") return;
  const name = document.getElementById("group-name").value.trim();
  if (!name) return;
  await api("/groups", { method: "POST", body: JSON.stringify({ name }) });
  e.target.reset();
  await loadUsers();
});

const connectionToggle = document.getElementById("connection-toggle");
const connectionPanel = document.getElementById("connection-panel");
const connectionForm = document.getElementById("connection-form");
const connectionMode = document.getElementById("connection-mode");
const connectionApi = document.getElementById("connection-api");
const connectionPlayer = document.getElementById("connection-player");
const connectionReset = document.getElementById("connection-reset");

connectionToggle?.addEventListener("click", () => {
  connectionPanel.classList.toggle("hidden");
});

if (connectionMode && connectionApi && connectionPlayer) {
  connectionMode.value = savedMode;
  connectionApi.value = savedApiBase;
  connectionPlayer.value = savedPlayerBase;
}

connectionForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  localStorage.setItem(CONNECTION_STORAGE_KEY, connectionMode.value);
  localStorage.setItem(CONNECTION_API_KEY, connectionApi.value.trim());
  localStorage.setItem(CONNECTION_PLAYER_KEY, connectionPlayer.value.trim());
  alert("Saved. Refresh the page to apply.");
});

connectionReset?.addEventListener("click", () => {
  localStorage.removeItem(CONNECTION_STORAGE_KEY);
  localStorage.removeItem(CONNECTION_API_KEY);
  localStorage.removeItem(CONNECTION_PLAYER_KEY);
  connectionMode.value = "local";
  connectionApi.value = "";
  connectionPlayer.value = "";
  alert("Reset. Refresh the page to apply.");
});

async function bootData() {
  await Promise.all([loadSites(), loadPlaylists(), loadMedia(), loadUsers()]);
  await loadScreens();
  showSection("sites");
}

async function boot() {
  if (!authToken) {
    showAuthPanel();
    updateAuthUI();
    return;
  }
  try {
    const me = await api("/auth/me");
    setAuth(authToken, me);
    showDashboard();
    await bootData();
    updateResolutionCustomVisibility();
  } catch (err) {
    console.error(err);
    handleAuthFailure();
  }
}

boot().catch((err) => {
  console.error(err);
  alert("Failed to load dashboard data.");
});

document
  .getElementById("screen-resolution")
  ?.addEventListener("change", updateResolutionCustomVisibility);

updateResolutionCustomVisibility();
bindZoneEditorEvents();
