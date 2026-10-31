/* ── Where the API and the player live ───────────────────────────
   config.js, written at container start from the deployment's own
   API_BASE_URL / PLAYER_BASE_URL, is the source of truth; the CSP's
   connect-src is generated from the same value.

   There is deliberately no ?api_base= / ?player_base= override: any link
   could set one, and sign-in navigates top-level to the API base carrying
   the one-time sign-in nonce -- so https://app…/?api_base=https://evil
   handed the visitor and the nonce to that host, which bounced them back
   signed in to the attacker's organization.

   The "Connection" panel (saved in this browser) may still move API calls,
   but only within the configured API origin, which is all the CSP lets the
   page reach anyway. Only with nothing configured (local development) does
   it accept any http(s) URL. The navigations that carry secrets -- social
   sign-in, the player link and preview -- never use it at all. */
const CONNECTION_STORAGE_KEY = "signage_connection_mode";
const CONNECTION_API_KEY     = "signage_connection_api";
const CONNECTION_PLAYER_KEY  = "signage_connection_player";

const CONFIGURED_API_BASE    = String(window.API_BASE_URL || "").trim();
const CONFIGURED_PLAYER_BASE = String(window.PLAYER_BASE_URL || "").trim();

/** The API this deployment is configured for (local development falls back
 *  to port 8000 of this host). Never influenced by anything the visitor or
 *  a link can set. */
const DEPLOYED_API_BASE = CONFIGURED_API_BASE ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

function httpOrigin(raw) {
  try {
    const url = new URL(raw);
    return /^https?:$/.test(url.protocol) ? url.origin : null;
  } catch (_) { return null; }
}

/** A saved Connection override, if this deployment allows it: an http(s)
 *  URL on the configured origin, or any http(s) URL when nothing is
 *  configured. Anything else is ignored (""). */
function acceptedConnectionOverride(raw, configured) {
  const value = String(raw || "").trim();
  const origin = value ? httpOrigin(value) : null;
  if (!origin) return "";
  if (configured && origin !== httpOrigin(configured)) return "";
  return value.replace(/\/+$/, "");
}

function readSetting(key) {
  try { return localStorage.getItem(key) || ""; } catch (_) { return ""; }
}

const savedMode       = readSetting(CONNECTION_STORAGE_KEY) || "local";
const savedApiBase    = readSetting(CONNECTION_API_KEY);
const savedPlayerBase = readSetting(CONNECTION_PLAYER_KEY);

/** Base URL for API calls (fetch). */
const API_BASE =
  (savedMode === "external" && acceptedConnectionOverride(savedApiBase, CONFIGURED_API_BASE)) ||
  DEPLOYED_API_BASE;

/** Base URL of the player, for the "Player URL" link and the preview frame.
 *  The configured value always wins; a saved override only applies when no
 *  player is configured (local development). */
const PLAYER_BASE = CONFIGURED_PLAYER_BASE ||
  (savedMode === "external" && acceptedConnectionOverride(savedPlayerBase, "")) ||
  `${window.location.protocol}//${window.location.hostname}:3001`;

const AUTH_STORAGE_KEY = "signage_auth_token";

let authToken   = localStorage.getItem(AUTH_STORAGE_KEY);
let currentUser = null;

/* ── Timezone helpers ─────────────────────────────────────────── */
const TZ_OPTIONS = [
  "Asia/Kuwait", "Asia/Riyadh", "Asia/Dubai", "Asia/Qatar",
  "Asia/Bahrain", "Asia/Muscat", "Asia/Baghdad",
  "Africa/Cairo", "Asia/Amman", "Asia/Beirut",
  "UTC",
];

function populateTimezoneSelect(selectEl, currentValue) {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  TZ_OPTIONS.forEach(tz => {
    const opt = document.createElement("option");
    opt.value = tz;
    opt.textContent = tz;
    if (tz === currentValue) opt.selected = true;
    selectEl.appendChild(opt);
  });
}

/* ── Toast ───────────────────────────────────────────────────── */
function toast(message, type = "info", duration = 3500) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  // The same message already on screen (e.g. a call site and the global
  // handler both reporting one failure) would only be noise.
  for (const existing of container.children) {
    if (existing.textContent === message && existing.classList.contains(`toast-${type}`)
        && !existing.classList.contains("toast-exit")) return;
  }
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
          <button class="confirm-dialog-close btn-ghost" aria-label="${escAttr(Khan.t("common.close", "Close"))}">✕</button>
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
    // Escape cancels. Enter is deliberately NOT mapped to "confirm": it
    // activates whichever button has focus (native <button> behaviour), and
    // Cancel is focused on open -- so Enter on a delete prompt cancels
    // instead of deleting.
    function onKeyDown(e) {
      if (e.key === "Escape") { e.preventDefault(); settle(false); return; }
      if (e.key === "Enter") {
        const focused = document.activeElement;
        if (!(focused && overlay.contains(focused) && focused.tagName === "BUTTON")) e.preventDefault();
      }
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
  sites: [], screens: [], playlists: [], schedules: [], media: [], users: [], groups: [],
  org: null,
  capabilities: { menus: false, menu_import: false },
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
    document.getElementById("header-row")?.classList.remove("nav-open");
    // Billing is a full-page view outside #dashboard, not a section inside it.
    if (btn.dataset.section === "billing") { goToBilling(); return; }
    showSection(btn.dataset.section);
  });
});

/* Billing and pairing are full-page views that live OUTSIDE #dashboard and hide it,
   so they can't be reached through showSection — and leaving them again means
   restoring #dashboard. goToBilling/showSection are the only two entry points. */
function goToBilling() {
  if (location.pathname !== "/billing") history.pushState({}, "", "/billing");
  navButtons.forEach((btn) => {
    btn.classList.toggle("nav-active", btn.dataset.section === "billing");
  });
  showBilling();
}

function showSection(id) {
  // Dismiss the full-page views and put the dashboard back, or every nav click
  // after visiting Billing just toggles panels behind a hidden parent.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  document.getElementById("dashboard")?.classList.remove("hidden");
  if (location.pathname === "/billing") history.replaceState({}, "", "/");
  // The plan card sits above whichever section is showing; it is not a
  // section itself (hiding it here is why it never appeared).
  document.querySelectorAll("#dashboard > section.panel:not(#plan-card), #dashboard > div").forEach((el) => {
    el.classList.toggle("hidden", el.id !== id);
  });
  navButtons.forEach((btn) => {
    btn.classList.toggle("nav-active", btn.dataset.section === id);
  });
  if (id === "walls") Walls.onShow();
  if (id === "schedules") Schedules.show();
  if (id === "audit-log") AuditLog.show();
  if (id === "api-keys") ApiKeys.show();
  if (id === "menus") Menus.show();
}

function buildPlayerUrl(base, params) {
  const url = new URL(base, window.location.origin);
  Object.entries(params).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); });
  return url.toString();
}

function getPlayerBaseWithOverrides() {
  // Local development that points the dashboard at another API hands the
  // player the same one. A configured deployment never does: its player has
  // its own config.js, and a link must not carry a browser-local setting.
  if (!CONFIGURED_API_BASE && API_BASE !== DEPLOYED_API_BASE) {
    return buildPlayerUrl(PLAYER_BASE, { api_base: API_BASE });
  }
  return PLAYER_BASE;
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
// Statuses whose generic meaning is clearer to a user than whatever English
// text the server (or a proxy in front of it) attached.
const STATUS_ERROR_TEXT = {
  401: ["error.session_expired", "Your session has ended. Please sign in again."],
  403: ["error.forbidden", "Your account doesn't have permission to do that."],
  413: ["error.too_large", "That file is too large to upload."],
  415: ["error.unsupported_type", "Only images, videos and PDF files can be uploaded."],
  429: ["error.rate_limited", "Too many requests. Wait a moment and try again."],
};

function statusErrorText(status) {
  if (STATUS_ERROR_TEXT[status]) return Khan.t(...STATUS_ERROR_TEXT[status]);
  if (status >= 500) return Khan.t("error.server", "The server ran into a problem. Try again in a moment.");
  return null;
}

// FastAPI/Pydantic 422: [{loc: ["body", "rules", 0, "days_of_week"], msg}, ...]
function formatValidationErrors(list) {
  const parts = list.map((e) => {
    const loc = (Array.isArray(e && e.loc) ? e.loc : [])
      .filter((p) => !["body", "query", "path"].includes(p));
    const msg = (e && e.msg) || "";
    return loc.length ? `${loc.join(" › ")}: ${msg}` : msg;
  }).filter(Boolean);
  return [Khan.t("error.validation", "Some values aren't valid."), ...parts.slice(0, 3)].join(" ");
}

/** Localized, human-readable text for a failed response. Backend errors are
 *  {detail: {code, message}}, {detail: "string"} or a Pydantic 422 list;
 *  none of them is ever shown to the user as raw JSON. */
function describeApiError(status, data, text) {
  const detail = data ? data.detail : undefined;
  if (Array.isArray(detail)) return formatValidationErrors(detail);
  if (detail && typeof detail === "object") {
    if (detail.code && Khan.has(`error.${detail.code}`)) return Khan.t(`error.${detail.code}`);
    return statusErrorText(status) || detail.message || Khan.t("error.generic", "Something went wrong. Please try again.");
  }
  if (typeof detail === "string" && detail) return statusErrorText(status) || detail;
  const plain = typeof text === "string" && text.length < 200 && !/^\s*[<{[]/.test(text) ? text.trim() : "";
  return statusErrorText(status) || plain || Khan.t("error.generic", "Something went wrong. Please try again.");
}

function apiError(status, data, text) {
  const err = new Error(describeApiError(status, data, text));
  err.status = status;
  err.data = data;
  err.isApiError = true;
  return err;
}

// fetch() only rejects when the request never got an answer.
async function apiFetch(url, init) {
  try {
    return await fetch(url, init);
  } catch (cause) {
    const err = new Error(Khan.t("error.network", "Can't reach the server. Check your connection and try again."));
    err.status = 0;
    err.network = true;
    err.isApiError = true;
    err.cause = cause;
    throw err;
  }
}

async function failedResponse(res, text, headers, { anonymous = false } = {}) {
  // An anonymous request's 401 says nothing about the session this tab holds.
  if (res.status === 401 && !anonymous) handleAuthFailure();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
  const err = apiError(res.status, data, text);
  if (data?.detail?.code?.startsWith?.("subscription.")) {
    // Refresh org state so banner re-renders correctly
    try {
      const fresh = await fetch(`${API_BASE}/organization`, { headers });
      if (fresh.ok) {
        state.org = await fresh.json();
        SubscriptionBanner.update(state.org);
      }
    } catch (_) { /* swallow */ }
  }
  return err;
}

/** JSON API call. Resolves to the parsed body, or null for an empty one
 *  (204 No Content: DELETE /schedules/{id}, /api-keys/{id}, /menus/{id}...).
 *  Rejects with an Error whose message is ready to show to the user.
 *  `anonymous: true` sends no session token even when one is held. */
async function api(path, options = {}) {
  const { anonymous = false, ...init } = options;
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  const headers = { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(init.headers || {}) };
  if (authToken && !anonymous) headers.Authorization = `Bearer ${authToken}`;
  const res = await apiFetch(`${API_BASE}${path}`, { ...init, headers });
  const text = await res.text();
  if (!res.ok) throw await failedResponse(res, text, headers, { anonymous });
  if (!text) return null;
  try { return JSON.parse(text); } catch (_) { return text; }
}

/** Same auth, 401 handling and error shape as api(), but returns the raw body.
 *  For endpoints that answer with something other than JSON -- currently the
 *  menu preview, which returns a standalone HTML document. */
async function apiText(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await apiFetch(`${API_BASE}${path}`, { ...options, headers });
  const text = await res.text();
  if (!res.ok) throw await failedResponse(res, text, headers);
  return text;
}

/* ── Global error reporting ──────────────────────────────────── */
// Most handlers are `async (e) => { await withLoading(btn, ...) }` with no
// catch of their own. Their failures used to vanish into the console; now
// anything nobody handled ends up here and is shown once. Call sites that
// catch their own errors never reach this, so nothing is reported twice.
function reportError(err) {
  if (!err || err.cancelled) return;
  const text = err.isApiError && err.message
    ? err.message
    : Khan.t("error.generic", "Something went wrong. Please try again.");
  toast(text, "error", 6000);
}

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  event.preventDefault();
  if (reason && reason.cancelled) return;   // MediaPicker closed: not an error
  console.error(reason);
  reportError(reason);
});

/* ── Auth ────────────────────────────────────────────────────── */
function setAuth(token, user) {
  const hadSession = Boolean(authToken || currentUser);
  authToken   = token;
  currentUser = user;
  token ? localStorage.setItem(AUTH_STORAGE_KEY, token)
        : localStorage.removeItem(AUTH_STORAGE_KEY);
  if (!token && hadSession) resetOrgState();
  updateAuthUI();
}

function handleAuthFailure() {
  setAuth(null, null);
  showAuthPanel();
}

/* Everything below belongs to one organization. Signing out (or a dead
   session) must drop all of it, or the next account to sign in in this tab
   briefly works against the previous org's playlists, users and schedules. */
function resetOrgState() {
  Object.assign(state, {
    sites: [], screens: [], playlists: [], schedules: [], media: [], users: [], groups: [],
    org: null, capabilities: { menus: false, menu_import: false },
  });
  const safely = (fn) => { try { fn(); } catch (err) { console.warn("resetOrgState", err); } };
  safely(() => { renderSites(); renderSiteOptions(); });
  safely(() => { renderPlaylists(); renderPlaylistSelect(); });
  safely(() => { renderMedia(); renderScreens(); renderUsers(); renderGroups(); });
  safely(() => {
    ["zones-editor", "screen-access-panel", "preview-panel", "plan-card", "nav-menus-btn"]
      .forEach((id) => document.getElementById(id)?.classList.add("hidden"));
    const frame = document.getElementById("preview-frame");
    if (frame) frame.removeAttribute("src");
  });
  safely(() => Walls.reset());
  safely(() => Schedules.reset());
  safely(() => AuditLog.reset());
  safely(() => Menus.reset());
  safely(() => SubscriptionBanner.update(null));
  safely(() => {
    document.getElementById("billing-current-body").textContent = "—";
    document.getElementById("billing-history-body").innerHTML = "";
    clearBillingBanner();
  });
}

/* ── Roles ───────────────────────────────────────────────────── */
// Mirrors the backend's require_roles / session_roles: viewers read only;
// editors manage content (sites, screens, media, playlists, schedules,
// walls, menus, pairing); deleting sites, screens and schedules, plus
// users, groups, screen access, API keys, audit log and billing, is admin.
function currentRole() {
  if (!currentUser) return null;
  return currentUser.role || (currentUser.is_admin ? "admin" : "viewer");
}
function isAdmin() { return currentRole() === "admin"; }
function canEditContent() { return currentRole() === "admin" || currentRole() === "editor"; }

function roleLabel(role) {
  // Also called (from setAuth) before the locale has loaded; fall back
  // quietly then -- boot() renders the header again once strings arrive.
  const key = `users.role_${role}`;
  return Khan.has(key) ? Khan.t(key) : (role || "");
}

// Static controls that only make sense for someone who can write. Gated with
// an inline display so it never fights the .hidden class other code toggles.
const EDITOR_ONLY_IDS = [
  "site-form", "screen-form", "media-url-form", "media-form", "playlist-form",
  "playlist-add-item", "walls-create-btn", "schedule-new-btn", "menu-new-btn",
];
function applyRoleGating() {
  const edit = canEditContent();
  EDITOR_ONLY_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = edit ? "" : "none";
  });
}

function updateAuthUI() {
  const authUser  = document.getElementById("auth-user");
  const logoutBtn = document.getElementById("logout-btn");
  const nav       = document.querySelector("header nav");
  const usersBtn    = document.querySelector('button[data-section="users"]');
  const auditBtn    = document.querySelector('button[data-section="audit-log"]');
  const apiKeysBtn  = document.querySelector('button[data-section="api-keys"]');
  applyRoleGating();
  if (currentUser) {
    authUser.textContent = `${currentUser.username} · ${roleLabel(currentRole())}`;
    logoutBtn.classList.remove("hidden");
    nav.classList.remove("hidden");
    if (usersBtn) usersBtn.classList.toggle("hidden", !isAdmin());
    if (auditBtn) auditBtn.classList.toggle("hidden", !isAdmin());
    if (apiKeysBtn) apiKeysBtn.classList.toggle("hidden", !isAdmin());
  } else {
    authUser.textContent = "";
    logoutBtn.classList.add("hidden");
    nav.classList.add("hidden");
    if (usersBtn) usersBtn.classList.add("hidden");
    if (auditBtn) auditBtn.classList.add("hidden");
    if (apiKeysBtn) apiKeysBtn.classList.add("hidden");
  }
}

function showAuthPanel() {
  document.getElementById("auth-panel").classList.remove("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  // Billing and pairing are full-page views outside #dashboard; left visible
  // they sit underneath the sign-in form after a logout.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  document.getElementById("subscription-banner")?.classList.add("hidden");
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
  renderScreens();   // each screen card has a playlist picker built from state.playlists
}

async function loadSchedules() {
  try {
    const body = await api("/schedules");
    state.schedules = body.items || body || [];
  } catch (_) {
    state.schedules = [];
  }
}

/** After a schedule is created, renamed or deleted: the screen cards'
 *  schedule pickers are built from state.schedules. */
async function refreshScheduleOptions() {
  await loadSchedules();
  renderScreens();
}

async function loadMedia() {
  state.media = await api("/media");
  renderMedia();
  // An open zones editor lists the library in its "Add media…" pickers.
  if (!document.getElementById("zones-editor")?.classList.contains("hidden")) renderZonesList();
}

async function loadUsers() {
  if (!isAdmin()) {
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
    // Deleting a site is admin-only on the backend.
    card.innerHTML = `
      <h3>${escHtml(site.name)}</h3>
      <div>${escHtml(Khan.t("sites.slug_label", "Slug:"))} <span style="font-family:var(--mono)" dir="ltr">${escHtml(site.slug)}</span></div>
      ${isAdmin() ? `<div class="card-actions">
        <button class="delete-btn" data-id="${site.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>
      </div>` : ""}
    `;
    card.querySelector(".delete-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_site_title", "Delete site"),
        message: Khan.t("confirm.delete_site_body", "Delete site \"{name}\"? Screens in this site become unassigned.").replace("{name}", site.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
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
  select.innerHTML = `<option value="">${escHtml(Khan.t("screens.site_label", "Site"))}</option>`;
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
      ${canEditContent() ? `<div class="card-actions">
        <button class="delete-btn" data-id="${playlist.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>
      </div>` : ""}
    `;
    card.querySelector(".delete-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_playlist_title", "Delete playlist"),
        message: Khan.t("confirm.delete_playlist_body", "Delete playlist \"{name}\"?").replace("{name}", playlist.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
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
  select.innerHTML = `<option value="">${escHtml(Khan.t("playlists.select_playlist", "Select playlist"))}</option>`;
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
    const typeLabel = isUrl ? Khan.t("media.type_website", "Website URL") : item.mime_type;
    card.innerHTML = `
      <h3>${escHtml(item.name)}</h3>
      <div class="card-meta">
        <span>${escHtml(typeLabel)}</span>
        ${!isUrl && item.size ? `<span>${formatBytes(item.size)}</span>` : ""}
      </div>
      <div><a href="${escAttr(url)}" target="_blank" rel="noreferrer">${escHtml(Khan.t("media.open", "Open ↗"))}</a></div>
      ${canEditContent() ? `<div class="card-actions">
        <button class="delete-btn" data-id="${item.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>
      </div>` : ""}
    `;
    card.querySelector(".delete-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_media_title", "Delete media"),
        message: Khan.t("confirm.delete_media_body", "Delete \"{name}\"?").replace("{name}", item.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
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
  if (!isAdmin()) {
    container.innerHTML = `<div class='card'>${Khan.t("users.admin_required", "Admin access required to manage users.")}</div>`;
    return;
  }
  state.users.forEach((user) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escHtml(user.username)}</h3>
      <div class="card-meta"><span>${escHtml(Khan.t("users.role_label", "Role: {role}").replace("{role}", roleLabel(user.role)))}</span></div>
      <div class="card-actions">
        <input type="password" placeholder="${escAttr(Khan.t("users.new_password_placeholder", "New password"))}" data-user-pass="${user.id}" style="min-width:140px" autocomplete="new-password" />
        <button class="save-btn"   data-user-reset="${user.id}">${escHtml(Khan.t("users.reset_password", "Reset Password"))}</button>
        <select data-user-role="${user.id}">
          <option value="viewer">${escHtml(roleLabel("viewer"))}</option>
          <option value="editor">${escHtml(roleLabel("editor"))}</option>
          <option value="admin">${escHtml(roleLabel("admin"))}</option>
        </select>
        <button class="save-btn"   data-user-role-save="${user.id}">${escHtml(Khan.t("users.update_role", "Update Role"))}</button>
        <button class="delete-btn" data-user-delete="${user.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>
      </div>
      <div class="card-actions" data-user-groups="${user.id}"></div>
    `;
    card.querySelector(`[data-user-reset="${user.id}"]`).addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      const input    = card.querySelector(`[data-user-pass="${user.id}"]`);
      const password = input.value.trim();
      if (!password) return;
      await withLoading(btn, async () => {
        await api(`/users/${user.id}`, { method: "PUT", body: JSON.stringify({ password }) });
        input.value = "";
        toast(Khan.t("toast.password_updated"), "success");
      });
    });
    card.querySelector(`[data-user-delete="${user.id}"]`).addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_user_title", "Delete user"),
        message: Khan.t("confirm.delete_user_body", "Delete user \"{name}\"?").replace("{name}", user.username),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
        await api(`/users/${user.id}`, { method: "DELETE" });
        toast(Khan.t("toast.user_deleted"), "success");
        await loadUsers();
      });
    });
    const roleSelect = card.querySelector(`[data-user-role="${user.id}"]`);
    roleSelect.value = user.role;
    card.querySelector(`[data-user-role-save="${user.id}"]`).addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      await withLoading(btn, async () => {
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
  if (!isAdmin()) return;
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
        <button class="delete-btn" data-group-delete="${group.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>
      </div>
    `;
    card.querySelector(`[data-group-delete="${group.id}"]`).addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_group_title", "Delete group"),
        message: Khan.t("confirm.delete_group_body", "Delete group \"{name}\"?").replace("{name}", group.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
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
      ? `<span class="status-online">${escHtml(Khan.t("screens.status_online", "Online"))}</span>`
      : `<span class="status-offline">${escHtml(Khan.t("screens.status_offline", "Offline"))}</span>`;
    const playlistOptions = [
      `<option value="">${escHtml(Khan.t("screens.no_playlist", "No playlist"))}</option>`,
      ...state.playlists.map((p) => `<option value="${p.id}">${escHtml(p.name)}</option>`),
    ].join("");

    const scheduleOptions = [
      `<option value="">${escHtml(Khan.t("screen.schedule.none", "None — use default playlist"))}</option>`,
      ...(state.schedules || []).map((s) => `<option value="${s.id}">${escHtml(s.name)}</option>`),
    ].join("");

    // Saving, zones and preview need editor; access (GET/PUT
    // /screens/{id}/groups) and delete are admin-only on the backend.
    const edit  = canEditContent();
    const admin = isAdmin();
    const orientationLabel = screen.orientation
      ? Khan.t(`screens.orientation_${screen.orientation}`, screen.orientation) : "";
    card.innerHTML = `
      <h3>${escHtml(screen.name)}</h3>
      <div class="card-meta">
        <span>${escHtml(Khan.t("screens.site_prefix", "Site:"))} ${escHtml(screen.site_name || Khan.t("screens.site_unassigned_label", "Unassigned"))}</span>
        ${screen.location ? `<span>${escHtml(screen.location)}</span>` : ""}
        ${screen.resolution ? `<span dir="ltr">${escHtml(screen.resolution)}</span>` : ""}
        ${orientationLabel ? `<span>${escHtml(orientationLabel)}</span>` : ""}
        <span>${escHtml(Khan.t("screens.status_label", "Status:"))} ${statusHtml}</span>
      </div>
      <div>${escHtml(Khan.t("screens.pair_code_label", "Pair code:"))} <strong class="pair-code" dir="ltr">${escHtml(screen.pair_code)}</strong></div>
      <div><a href="${escAttr(playerUrl)}" target="_blank" rel="noreferrer">${escHtml(Khan.t("screens.player_url", "Player URL ↗"))}</a></div>
      <div class="card-actions">
        <select data-playlist-select="${screen.id}" ${edit ? "" : "disabled"} aria-label="${escAttr(Khan.t("schedules.editor.playlist", "Playlist"))}">${playlistOptions}</select>
        <select data-schedule-select="${screen.id}" ${edit ? "" : "disabled"} aria-label="${escAttr(Khan.t("screen.field.schedule", "Schedule"))}">${scheduleOptions}</select>
        ${edit ? `<button class="save-btn"    data-save-screen="${screen.id}">${escHtml(Khan.t("screens.save_btn", "Save"))}</button>
        <button class="save-btn"    data-zones-screen="${screen.id}">${escHtml(Khan.t("screens.zones_btn", "Zones"))}</button>` : ""}
        ${admin ? `<button class="access-btn"  data-access-screen="${screen.id}">${escHtml(Khan.t("screens.access_btn", "Access"))}</button>` : ""}
        ${edit ? `<button class="preview-btn" data-preview-screen="${screen.id}">${escHtml(Khan.t("screens.preview_btn", "Preview"))}</button>` : ""}
        ${admin ? `<button class="delete-btn"  data-delete-screen="${screen.id}">${escHtml(Khan.t("confirm.delete_label", "Delete"))}</button>` : ""}
      </div>
    `;

    const select = card.querySelector(`[data-playlist-select="${screen.id}"]`);
    select.value = screen.playlist_id || "";

    const scheduleSel = card.querySelector(`[data-schedule-select="${screen.id}"]`);
    if (scheduleSel) scheduleSel.value = screen.schedule_id || "";

    card.querySelector(`[data-save-screen="${screen.id}"]`)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      await withLoading(btn, async () => {
        const playlistId = select.value ? Number(select.value) : null;
        const scheduleId = scheduleSel?.value ? Number(scheduleSel.value) : null;
        await api(`/screens/${screen.id}`, {
          method: "PUT",
          body: JSON.stringify({ playlist_id: playlistId, schedule_id: scheduleId }),
        });
        toast(Khan.t("toast.screen_saved"), "success");
        await loadScreens();
      });
    });

    card.querySelector(`[data-zones-screen="${screen.id}"]`)?.addEventListener("click", async () => {
      await openZonesEditor(screen.id);
    });

    card.querySelector(`[data-access-screen="${screen.id}"]`)?.addEventListener("click", async () => {
      await openScreenAccessEditor(screen.id);
    });

    card.querySelector(`[data-preview-screen="${screen.id}"]`)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      await withLoading(btn, async () => {
        const preview = await api(`/screens/${screen.id}/preview-token`, { method: "POST" });
        const base    = getPlayerBaseWithOverrides();
        const url     = base.includes("?") ? `${base}&preview_token=${preview.token}` : `${base}/?preview_token=${preview.token}`;
        showPreview(screen, url, preview.expires_at);
      });
    });

    card.querySelector(`[data-delete-screen="${screen.id}"]`)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;   // null once the event finishes dispatching
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_screen_title", "Delete screen"),
        message: Khan.t("confirm.delete_screen_body", "Delete screen \"{name}\"?").replace("{name}", screen.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
        await api(`/screens/${screen.id}`, { method: "DELETE" });
        toast(Khan.t("toast.screen_deleted"), "success");
        await loadScreens();
        loadOrganization();   // plan card: "n / limit screens"
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
function setZones(zones) {
  zonesState.zones = zones.map((zone, index) => ({
    id: zone.id || `local-${index}`,
    name: zone.name || zoneDefaultName(index + 1),
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
        <span>${escHtml(item.name || Khan.t("zones.media_fallback", "Media"))}</span>
        <input type="number" min="0" max="3600" value="${item.duration_seconds ?? 10}" data-zone-item-duration="${zoneIndex}:${itemIndex}" />
        <button class="delete-btn" data-zone-item-remove="${zoneIndex}:${itemIndex}">✕</button>
      </div>
    `).join("") || `<div class='helper-text'>${escHtml(Khan.t("zones.no_media", "No media yet."))}</div>`;

    const mediaOptions = state.media.map((m) => `<option value="${m.id}">${escHtml(m.name)}</option>`).join("");

    card.innerHTML = `
      <div class="zone-meta">
        <strong>${escHtml(zone.name)}</strong>
        <span>${Math.round(zone.width * 100)}% × ${Math.round(zone.height * 100)}%</span>
      </div>
      <div class="zone-transition">
        <label>${escHtml(Khan.t("zones.fade_ms", "Fade (ms)"))}</label>
        <input type="number" min="0" max="5000" value="${zone.transition_ms ?? 600}" data-zone-transition="${zoneIndex}" />
      </div>
      <div class="zone-actions">
        <button class="delete-btn" data-zone-remove="${zoneIndex}">${escHtml(Khan.t("zones.delete_zone", "Delete Zone"))}</button>
      </div>
      <div class="zone-add">
        <select data-zone-media="${zoneIndex}"><option value="">${escHtml(Khan.t("zones.add_media_option", "Add media…"))}</option>${mediaOptions}</select>
        <input type="number" min="0" max="3600" value="10" data-zone-duration="${zoneIndex}" style="width:70px" />
        <button class="save-btn" data-zone-add="${zoneIndex}">${escHtml(Khan.t("zones.add_btn", "Add"))}</button>
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

function zoneDefaultName(n) {
  return Khan.t("zones.default_name_n", "Zone {n}").replace("{n}", n);
}

function presetZones(type) {
  // Zone names are saved with the layout, so they are created in the
  // language the person is working in.
  const z = (key, fallback) => Khan.t(`zones.preset.${key}`, fallback);
  if (type === "single")     setZones([{ name: z("full", "Full"),     x: 0,    y: 0, width: 1,    height: 1,   items: [] }]);
  if (type === "columns-2")  setZones([{ name: z("left", "Left"),     x: 0,    y: 0, width: 0.5,  height: 1,   items: [] },
                                        { name: z("right", "Right"),   x: 0.5,  y: 0, width: 0.5,  height: 1,   items: [] }]);
  if (type === "columns-3")  setZones([{ name: z("left", "Left"),     x: 0,    y: 0, width: 0.33, height: 1,   items: [] },
                                        { name: z("center", "Center"), x: 0.33, y: 0, width: 0.34, height: 1,   items: [] },
                                        { name: z("right", "Right"),   x: 0.67, y: 0, width: 0.33, height: 1,   items: [] }]);
  if (type === "rows-2")     setZones([{ name: z("top", "Top"),       x: 0,    y: 0, width: 1,    height: 0.5, items: [] },
                                        { name: z("bottom", "Bottom"), x: 0,    y: 0.5, width: 1,  height: 0.5, items: [] }]);
  if (type === "hero-side")  setZones([{ name: z("hero", "Hero"),     x: 0,    y: 0, width: 0.7,  height: 1,   items: [] },
                                        { name: z("side", "Side"),     x: 0.7,  y: 0, width: 0.3,  height: 1,   items: [] }]);
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
    const btn = e.currentTarget;   // null once the event finishes dispatching
    await withLoading(btn, async () => {
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
  select.innerHTML = `<option value="">${escHtml(Khan.t("screens.template_apply_label", "Apply template…"))}</option>`;
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
    zonesState.zones.push({ id: `local-${Date.now()}`, name: zoneDefaultName(index), x: 0.1, y: 0.1, width: 0.3, height: 0.3, sort_order: index, transition_ms: 600, items: [] });
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
    const btn = e.currentTarget;   // null once the event finishes dispatching
    const name = document.getElementById("zone-template-name").value.trim();
    if (!name) return;
    await withLoading(btn, async () => {
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
    const btn = e.currentTarget;   // null once the event finishes dispatching
    const templateId = Number(document.getElementById("zone-template-select").value || 0);
    if (!templateId || !zonesState.screenId) return;
    await withLoading(btn, async () => {
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
    const btn = e.currentTarget;   // null once the event finishes dispatching
    if (!zonesState.screenId) return;
    await withLoading(btn, async () => {
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

  // Pointer events rather than mouse events, so a finger can draw, move and
  // resize zones too (touch never produces a mousemove while dragging). The
  // canvas captures the pointer -- re-rendering replaces the zone elements
  // under the finger, and capture keeps the moves coming anyway -- and has
  // touch-action: none in the CSS so the drag does not scroll the page.
  const canvas = document.getElementById("zones-canvas");
  canvas?.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas?.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    const handle  = event.target.closest(".zone-handle");
    const zoneEl  = event.target.closest(".zone-block");
    if (!zoneEl) return;
    const zoneIndex = Number(zoneEl.dataset.zoneIndex);
    if (!Number.isFinite(zoneIndex)) return;
    const canvasRect = canvas.getBoundingClientRect();
    const original   = { ...zonesState.zones[zoneIndex] };
    zonesState.dragging = { zoneIndex, handle: handle ? handle.dataset.handle : "move", startX: event.clientX, startY: event.clientY, original, canvasRect };
  });
  canvas?.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    if (event.target.closest(".zone-block")) return;
    const canvasRect = canvas.getBoundingClientRect();
    const startX = clamp((event.clientX - canvasRect.left) / canvasRect.width, 0, 1);
    const startY = clamp((event.clientY - canvasRect.top)  / canvasRect.height, 0, 1);
    zonesState.drawing = { startX, startY, canvasRect };
  });
  window.addEventListener("pointermove", (event) => {
    if (!event.isPrimary || !zonesState.dragging) return;
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
  window.addEventListener("pointermove", (event) => {
    if (!event.isPrimary || !zonesState.drawing) return;
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
  const endZoneDrag = (event) => {
    if (!event.isPrimary) return;
    const drawn = event.type === "pointerup" && zonesState.drawing?.preview;
    if (drawn) {
      const index = zonesState.zones.length + 1;
      zonesState.zones.push({ id: `local-${Date.now()}`, name: zoneDefaultName(index), ...zonesState.drawing.preview, sort_order: index, transition_ms: 600, items: [] });
      renderZonesList();
    }
    const hadPreview = !!zonesState.drawing?.preview;
    zonesState.dragging = null;
    zonesState.drawing  = null;
    // Swap the dashed preview for the real zone (or drop it on cancel); left
    // in place it was a dead element that could not be moved or resized.
    if (hadPreview) renderZonesCanvas();
  };
  window.addEventListener("pointerup", endZoneDrag);
  window.addEventListener("pointercancel", endZoneDrag);
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
      <div class="card-meta"><span>${escHtml(Khan.t("playlists.item_seconds", "{n}s").replace("{n}", item.duration_seconds))}</span></div>
      ${canEditContent() ? `<div class="card-actions">
        <button class="delete-btn" data-item-id="${item.id}">${escHtml(Khan.t("playlists.remove_item", "Remove"))}</button>
      </div>` : ""}
    </div>
  `).join("");
  card.innerHTML = `<h3>${escHtml(Khan.t("playlists.items_heading", "{name} — items").replace("{name}", data.playlist.name))}</h3>${
    itemsHtml || `<div class='helper-text'>${escHtml(Khan.t("playlists.no_items", "No items yet."))}</div>`}`;
  container.appendChild(card);
  card.querySelectorAll("[data-item-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await confirmDialog({
        title:   Khan.t("confirm.remove_item_title", "Remove item"),
        message: Khan.t("confirm.remove_item_body", "Remove this item from the playlist?"),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
      await withLoading(btn, async () => {
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
  const tz   = document.getElementById("site-timezone-select")?.value || "Asia/Kuwait";
  await withLoading(btn, async () => {
    const created = await api("/sites", { method: "POST", body: JSON.stringify({ name, slug: slug || null }) });
    if (tz !== "Asia/Kuwait" && created && created.id) {
      try {
        await api(`/sites/${created.id}`, { method: "PUT", body: JSON.stringify({ timezone: tz }) });
      } catch (_) { /* swallow — site already exists with default tz */ }
    }
    e.target.reset();
    populateTimezoneSelect(document.getElementById("site-timezone-select"), "Asia/Kuwait");
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
      loadOrganization();   // plan card: "n / limit screens"
    } catch (err) {
      toast(err.message || Khan.t("screens.add_failed", "Failed to add screen."), "error");
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
    await uploadMediaFile(file);
    input.value = "";
    document.getElementById("media-file-name").textContent = "";
    toast(Khan.t("toast.media_uploaded"), "success");
    await loadMedia();
  });
});

// The backend only accepts these (anything else is a 415); the file input's
// accept= filters the picker, but drag-and-drop bypasses it.
const UPLOADABLE_TYPE_RE = /^(image\/|video\/|application\/pdf$)/;

async function uploadMediaFile(file) {
  if (file.type && !UPLOADABLE_TYPE_RE.test(file.type)) {
    const err = new Error(statusErrorText(415));
    err.isApiError = true;
    err.status = 415;
    throw err;
  }
  const formData = new FormData();
  formData.append("file", file);
  return api("/media/upload", { method: "POST", body: formData });
}

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
// The file input is invisible, so echo the picked file's name -- on a phone
// there is otherwise no sign the pick registered before tapping Upload.
document.getElementById("media-file")?.addEventListener("change", (e) => {
  document.getElementById("media-file-name").textContent = e.target.files[0]?.name || "";
});
mediaForm?.addEventListener("dragover", (e) => { e.preventDefault(); mediaForm.classList.add("dropzone-active"); });
mediaForm?.addEventListener("dragleave", ()  => mediaForm.classList.remove("dropzone-active"));
mediaForm?.addEventListener("drop", async (e) => {
  e.preventDefault();
  mediaForm.classList.remove("dropzone-active");
  const files = Array.from(e.dataTransfer?.files || []);
  if (files.length === 0) return;
  let uploaded = 0;
  const failures = [];
  for (const file of files) {
    try {
      await uploadMediaFile(file);
      uploaded++;
    } catch (err) {
      failures.push(`${file.name}: ${err.isApiError ? err.message : Khan.t("error.generic", "Something went wrong. Please try again.")}`);
    }
  }
  if (uploaded) {
    toast(Khan.t("toast.files_uploaded_n", "{n} file(s) uploaded.").replace("{n}", uploaded), "success");
  }
  if (failures.length) {
    toast(Khan.t("toast.files_failed_n", "{n} file(s) couldn't be uploaded. {reason}")
      .replace("{n}", failures.length).replace("{reason}", failures[0]), "error", 8000);
  }
  if (uploaded) await loadMedia();
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
  const btn = e.currentTarget;   // null once the event finishes dispatching
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
  await withLoading(btn, async () => {
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
      state.org = data.organization;
      SubscriptionBanner.update(state.org);

      const resumeRaw = sessionStorage.getItem("pair_resume");
      if (resumeRaw) {
        sessionStorage.removeItem("pair_resume");
        try {
          const resume = JSON.parse(resumeRaw);
          if (resume && resume.path === "/pair") {
            history.replaceState({}, "", `/pair${resume.code ? `?code=${encodeURIComponent(resume.code)}` : ""}`);
            await showPairView(resume.code || "");
            bootDataInBackground();
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

/* ── Phase 2.5j: Social auth (Google + Apple) ───────────────── */
// The backend hands the new session token back in the /auth-bounce URL. Any
// page can link to /auth-bounce?token=..., so a token there is only trusted
// when THIS tab started the sign-in: startSocialAuth stores a random nonce in
// sessionStorage and threads it through return_to (which the backend signs
// into the OAuth state and echoes back). A link built by someone else cannot
// carry it, so it can neither sign the visitor into the attacker's account
// nor redirect them off-site.
const SOCIAL_PENDING_KEY    = "khan_social_auth_pending";
const SOCIAL_PENDING_TTL_MS = 15 * 60 * 1000;
const SOCIAL_NONCE_PARAM    = "sa_nonce";
const SOCIAL_PROVIDERS      = ["google", "apple"];
const FLASH_KEY             = "khan_flash";

function randomNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function startSocialAuth(provider) {
  const intent = document.getElementById("auth-tab-signup")
                   .classList.contains("active") ? "signup" : "signin";
  const nonce = randomNonce();
  try {
    sessionStorage.setItem(SOCIAL_PENDING_KEY, JSON.stringify({ nonce, at: Date.now() }));
  } catch (_) { /* storage unavailable: the bounce will refuse the token */ }
  const here = new URL(window.location.href);
  here.searchParams.set(SOCIAL_NONCE_PARAM, nonce);
  const params = new URLSearchParams({ intent, return_to: here.pathname + here.search });
  // DEPLOYED_API_BASE, never API_BASE: this navigation carries the nonce,
  // so it may only ever go to the API the deployment is configured for.
  window.location = `${DEPLOYED_API_BASE}/auth/${provider}/start?${params}`;
}

/** The sign-in this tab started, if any (consumed: one use only). */
function takePendingSocialAuth() {
  let pending = null;
  try {
    pending = JSON.parse(sessionStorage.getItem(SOCIAL_PENDING_KEY) || "null");
    sessionStorage.removeItem(SOCIAL_PENDING_KEY);
  } catch (_) { pending = null; }
  if (!pending || typeof pending.nonce !== "string" || !pending.nonce) return null;
  if (!(Date.now() - Number(pending.at) < SOCIAL_PENDING_TTL_MS)) return null;
  return pending;
}

/** A same-origin URL for a relative path like "/billing?x=1", else null.
 *  The same rules as the backend's _safe_return_to: no absolute or
 *  protocol-relative URL ("https://…", "//…"), no backslash ("/\\host"),
 *  no control character (browsers drop tabs and newlines, so "/\t/host" is
 *  "//host"), and no dot-segment, raw or percent-encoded: "/.//host" and
 *  "/%2e//host" both resolve to the path "//host", which is
 *  protocol-relative the moment it is reused as a URL. */
function safeReturnUrl(raw) {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//")
      || raw.includes("\\") || /[\u0000-\u001f\u007f]/.test(raw)) return null;
  const path = raw.split("?", 1)[0].split("#", 1)[0];
  let decoded;
  try { decoded = decodeURIComponent(path); } catch (_) { return null; }
  if (decoded.split("/").some((segment) => segment === "." || segment === "..")) return null;
  let url;
  try { url = new URL(raw, window.location.origin); } catch (_) { return null; }
  if (url.origin !== window.location.origin || url.pathname.startsWith("//")
      || url.pathname === "/auth-bounce") return null;
  return url;
}

/** A toast to show after the next page load (this one is about to navigate). */
function setFlash(key, fallback, type = "error") {
  try { sessionStorage.setItem(FLASH_KEY, JSON.stringify({ key, fallback, type })); } catch (_) {}
}

function showFlash() {
  let flash = null;
  try {
    flash = JSON.parse(sessionStorage.getItem(FLASH_KEY) || "null");
    sessionStorage.removeItem(FLASH_KEY);
  } catch (_) { flash = null; }
  if (flash && flash.key) toast(Khan.t(flash.key, flash.fallback), flash.type || "info", 7000);
}
const googleBtn = document.getElementById("btn-social-google");
const appleBtn  = document.getElementById("btn-social-apple");
if (googleBtn) googleBtn.addEventListener("click", () => startSocialAuth("google"));
if (appleBtn)  appleBtn.addEventListener("click",  () => startSocialAuth("apple"));

// A deployment with no users has nothing to sign in to: show the first-run
// setup form instead of the auth panel. Failing open (leaving login visible)
// is the right default if this check cannot complete -- an operator who cannot
// reach the API has a bigger problem than a missing form.
fetch(`${API_BASE}/auth/setup-status`)
  .then((r) => (r.ok ? r.json() : null))
  .then((cfg) => {
    if (!cfg || !cfg.needs_setup) return;
    document.getElementById("auth-panel")?.classList.add("hidden");
    document.getElementById("setup-panel")?.classList.remove("hidden");
  })
  .catch(() => { /* leave the normal sign-in panel in place */ });

document.getElementById("setup-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("setup-error");
  err.classList.add("hidden");
  const body = {
    setup_token:   document.getElementById("setup-token").value.trim(),
    business_name: document.getElementById("setup-business").value.trim(),
    username:      document.getElementById("setup-username").value.trim(),
    password:      document.getElementById("setup-password").value,
  };
  try {
    const res = await fetch(`${API_BASE}/auth/setup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const code = data?.detail?.code || "";
      err.textContent = Khan.t(`error.${code}`, data?.detail?.message || Khan.t("setup.failed", "Setup failed."));
      err.classList.remove("hidden");
      return;
    }
    // Straight into the dashboard: the operator is now signed in.
    localStorage.setItem(AUTH_STORAGE_KEY, data.token);
    location.reload();
  } catch (_) {
    err.textContent = Khan.t("setup.network_error", "Couldn't reach the server.");
    err.classList.remove("hidden");
  }
});

// Hide buttons for providers that aren't configured on the backend.
// If /auth/providers itself 404s (e.g., older backend), keep both visible.
fetch(`${API_BASE}/auth/providers`)
  .then((r) => r.ok ? r.json() : null)
  .then((cfg) => {
    if (!cfg) return;
    if (!cfg.google && googleBtn) googleBtn.classList.add("hidden");
    if (!cfg.apple  && appleBtn)  appleBtn.classList.add("hidden");
    // Hide the divider too if no providers are configured at all
    const wrap = document.getElementById("social-auth-buttons");
    if (wrap && !cfg.google && !cfg.apple) wrap.classList.add("hidden");
  })
  .catch(() => { /* network noise; leave buttons visible */ });

function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    {"&": "&amp;", "<": "&lt;", ">": "&gt;",
     '"': "&quot;", "'": "&#39;"}[c]
  ));
}

function handleAuthBounce() {
  const params = new URLSearchParams(window.location.search);
  const token   = params.get("token");
  const stash   = params.get("complete_signup");
  const error   = params.get("error");
  const suggestedName = params.get("suggested_name") || "";
  const pending = takePendingSocialAuth();
  const refuse = () => {
    setFlash("auth.social.link_refused",
             "That sign-in link didn't start in this browser, so it was ignored. Please sign in again.");
    window.location.replace("/");
  };

  if (error) {
    setFlash("auth.social.failed", "Sign-in was cancelled or didn't complete. Please try again.");
    window.location.replace("/");
    return;
  }
  // Both legs -- the session token and the "finish creating your account"
  // stash -- must echo the nonce this tab put in return_to. A stash from
  // someone else's sign-in would otherwise tie a new org to their identity.
  const target = safeReturnUrl(params.get("return_to") || "/");
  const echoed = target ? target.searchParams.get(SOCIAL_NONCE_PARAM) : null;
  const startedHere = Boolean(pending && echoed && echoed === pending.nonce);
  if (token) {
    if (!startedHere) { refuse(); return; }
    target.searchParams.delete(SOCIAL_NONCE_PARAM);
    localStorage.setItem(AUTH_STORAGE_KEY, token);
    // replace(): the token-bearing URL must not stay in the history. The
    // absolute href, never a path rebuilt from its parts: a pathname such as
    // "//host/x" would be read as protocol-relative, i.e. off-site.
    window.location.replace(target.href);
    return;
  }
  if (stash) {
    if (!startedHere) { refuse(); return; }
    // The payload is read unverified (only the backend can check the
    // signature), so it may only pick between the two known providers --
    // never put arbitrary text into the request path.
    let provider = "google";
    try {
      const b64 = stash.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      const payload = JSON.parse(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)));
      if (payload && SOCIAL_PROVIDERS.includes(payload.provider)) provider = payload.provider;
    } catch (e) { /* fall through with default */ }
    history.replaceState({}, "", "/auth-bounce");   // keep the stash out of history

    const html = `
      <div class="modal-overlay" id="complete-signup-overlay">
        <div class="modal-card">
          <h2 data-i18n="auth.social.complete_signup.title">Welcome to Khanshoof!</h2>
          <form id="complete-signup-form">
            <label data-i18n="auth.social.complete_signup.business_name">Business name</label>
            <input type="text" name="business_name" required
                   value="${_escapeHtml(suggestedName)}" autofocus dir="auto" />
            <button type="submit" class="btn-primary"
                    data-i18n="auth.social.complete_signup.continue">Continue</button>
          </form>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML("beforeend", html);
    if (typeof Khan !== "undefined" && Khan.applyTranslations) {
      Khan.applyTranslations(document.getElementById("complete-signup-overlay"));
    }

    document.getElementById("complete-signup-form")
            .addEventListener("submit", async (e) => {
      e.preventDefault();
      const businessName = e.target.business_name.value.trim();
      if (!businessName) return;
      const submit = e.target.querySelector("button[type=submit]");
      try {
        await withLoading(submit, async () => {
          // anonymous: this creates a brand-new account, so whoever may be
          // signed in elsewhere in this browser has no business riding along.
          const body = await api(`/auth/${provider}/complete-signup`, {
            method: "POST",
            anonymous: true,
            body: JSON.stringify({ stash_token: stash, business_name: businessName }),
          });
          localStorage.setItem(AUTH_STORAGE_KEY, body.token);
          window.location.replace("/");
        });
      } catch (err) {
        toast(err.isApiError ? err.message
                             : Khan.t("auth.social.complete_signup.failed", "Couldn't finish creating your account."),
              "error", 6000);
      }
    });
  }
}

if (window.location.pathname === "/auth-bounce") {
  handleAuthBounce();
}

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
    el.textContent = Khan.t("auth.dev_otp", "Dev mode: your code is {otp}").replace("{otp}", otp);
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
    toast(err.message || Khan.t("error.verify_failed", "Verification failed."), "error");
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
    toast(err.message || Khan.t("error.code_resend_failed", "Couldn't resend code."), "error");
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
      state.org = data.organization;
      SubscriptionBanner.update(state.org);
      showDashboard();
      await bootData();
      toast(Khan.t("toast.signup_welcome", "Welcome to Khanshoof, {name}! Your 5-day trial is active.").replace("{name}", signupState.business_name), "success", 6000);
      signupState.email = "";
      signupState.business_name = "";
      signupState.verification_token = "";
    });
  } catch (err) {
    toast(err.message || Khan.t("error.signup_failed", "Sign-up failed."), "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  if (authToken) await api("/auth/logout", { method: "POST" }).catch(() => {});
  setAuth(null, null);
  showAuthPanel();
  // Don't leave /billing or /pair in the address bar under the sign-in form.
  if (location.pathname !== "/") history.replaceState({}, "", "/");
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

/** Why a Connection field can't be saved, or null. `overridable`: whether a
 *  configured deployment lets this browser move it within its own origin
 *  (the API) or not at all (the player). */
function connectionFieldProblem(raw, configured, overridable) {
  const value = String(raw || "").trim();
  if (!value) return null;
  if (configured && (!overridable || !acceptedConnectionOverride(value, configured))) {
    return Khan.t("toast.connection_locked",
      "This deployment only connects to {origin}; another address can't be used here.")
      .replace("{origin}", httpOrigin(configured) || configured);
  }
  if (!acceptedConnectionOverride(value, configured)) {
    return Khan.t("toast.connection_bad_url", "Enter a full address that starts with http:// or https://.");
  }
  return null;
}

if (connectionMode && connectionApi && connectionPlayer) {
  connectionMode.value   = savedMode;
  connectionApi.value    = savedApiBase;
  connectionPlayer.value = savedPlayerBase;
}

connectionForm?.addEventListener("submit", (e) => {
  e.preventDefault();
  // Say so now rather than silently ignoring the value on the next load.
  if (connectionMode.value === "external") {
    const problem = connectionFieldProblem(connectionApi.value, CONFIGURED_API_BASE, true)
                 || connectionFieldProblem(connectionPlayer.value, CONFIGURED_PLAYER_BASE, false);
    if (problem) { toast(problem, "error", 6000); return; }
  }
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
/** Load everything the dashboard sections need. `section` is the section to
 *  open afterwards, or null to load without navigating -- used when the page
 *  opens on a full-page view (/billing after KNET, /pair from a TV's QR code)
 *  so that leaving it lands on a populated dashboard, not empty lists. */
async function bootData({ section = "sites" } = {}) {
  populateTimezoneSelect(document.getElementById("site-timezone-select"), "Asia/Kuwait");
  await Promise.all([loadOrganization(), loadSites(), loadPlaylists(), loadSchedules(), loadMedia(), loadUsers()]);
  await loadScreens();
  try {
    const caps = await fetch(`${API_BASE}/ai/capabilities`).then((r) => r.json());
    state.capabilities = caps;
    document.getElementById("nav-menus-btn")?.classList.toggle("hidden", !caps.menus);
    document.getElementById("menu-import-btn")?.classList.toggle("hidden", !caps.menu_import);
  } catch (_) { state.capabilities = { menus: false, menu_import: false }; }
  if (section) showSection(section);
}

function bootDataInBackground() {
  bootData({ section: null }).catch((err) => {
    console.error(err);
    toast(Khan.t("toast.dashboard_load_failed"), "error", 6000);
  });
}

async function loadOrganization() {
  try {
    const org = await api("/organization");
    state.org = org;
    renderPlanCard(org);
    SubscriptionBanner.update(org);
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

  // Plan names are product names and stay as they are in both languages
  // (the landing page does the same).
  const planLabels = {
    starter: "Starter", growth: "Growth", business: "Business",
    pro: "Pro", enterprise: "Enterprise",
  };
  const tierLabel  = planLabels[org.plan] || org.plan || "—";
  const used       = Number.isFinite(org.screens_used)  ? org.screens_used  : 0;
  const limit      = Number.isFinite(org.screen_limit) ? org.screen_limit : 0;

  biz.textContent   = org.name || Khan.t("plan.org_fallback", "Your organization");
  tier.textContent  = Khan.t("plan.tier_label", "{plan} plan").replace("{plan}", tierLabel);
  usage.textContent = Khan.t("plan.usage", "{used} / {limit} screens")
    .replace("{used}", used).replace("{limit}", limit);

  const subStatus = org.subscription_status || "";
  status.textContent = subStatus ? Khan.t(`plan.status_${subStatus}`, subStatus) : "—";
  status.className   = `plan-status plan-status-${(subStatus || "unknown").toLowerCase()}`;

  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const endsAt  = new Date(org.trial_ends_at);
    const daysLeft = Math.max(0, Math.ceil((endsAt.getTime() - Date.now()) / 86400000));
    const date = formatDay(org.trial_ends_at);
    trial.textContent = daysLeft > 0
      ? Khan.t(daysLeft === 1 ? "plan.trial_ends_one" : "plan.trial_ends_other",
               daysLeft === 1 ? "Trial ends in 1 day ({date})." : "Trial ends in {n} days ({date}).")
          .replace("{n}", daysLeft).replace("{date}", date)
      : Khan.t("plan.trial_ended", "Trial ended on {date}. Upgrade to keep your screens live.").replace("{date}", date);
    trial.classList.remove("hidden");
  } else {
    trial.classList.add("hidden");
  }

  card.classList.remove("hidden");
}

/* ── Pair view ──────────────────────────────────────────────── */
// Must match PAIR_CODE_LENGTH in backend/main.py. The player renders the full
// code into its QR URL, so a shorter value here silently truncates the prefill
// and pairing fails with no visible error.
const PAIR_CODE_LENGTH = 6;
const PAIR_CODE_RE = new RegExp(`^[A-Z2-9]{${PAIR_CODE_LENGTH}}$`);

function normalizePairCode(raw) {
  return String(raw || "").toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, PAIR_CODE_LENGTH);
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

  // Stamp the length constraints from the JS constant so the markup can never
  // drift out of sync with it again.
  codeInput.maxLength = PAIR_CODE_LENGTH;
  codeInput.setAttribute("pattern", `[A-Z2-9]{${PAIR_CODE_LENGTH}}`);
  codeInput.value    = normalizePairCode(initialCode);
  newNameInput.value = "";
  newNameInput.classList.add("hidden");
  existingSel.innerHTML = `<option value="">${escHtml(Khan.t("pair.pick_screen", "— Pick a screen —"))}</option>`;

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
    opt.textContent = s.name || Khan.t("pair.screen_n", "Screen #{id}").replace("{id}", s.id);
    existingSel.appendChild(opt);
  }

  // Both branches set BOTH inputs' visibility: "Pair another display" comes
  // back here after the radio handler may have hidden the screen picker.
  if (screens.length === 0) {
    radioExist.disabled = true;
    existingSel.disabled = true;
    radioNew.checked = true;
    newNameInput.classList.remove("hidden");
    existingSel.classList.add("hidden");
  } else {
    radioExist.disabled = false;
    existingSel.disabled = false;
    radioExist.checked = true;
    newNameInput.classList.add("hidden");
    existingSel.classList.remove("hidden");
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
    // The choice is this person's (stored in the language cookie, which wins
    // over the org default on the next load). An admin's choice also becomes
    // the org default -- used for reminder emails and for users who never
    // picked one -- but that is best effort: a 402 on an expired subscription
    // or a 403 must not stop the switch itself.
    if (authToken && currentUser?.role === "admin") {
      try {
        await api("/organizations/me", { method: "PATCH", body: JSON.stringify({ locale: next }) });
      } catch (err) {
        console.warn("PATCH /organizations/me failed; switching language for this user only", err);
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
  updateAuthUI();   // the role label in the header was rendered before the strings loaded
  showFlash();

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
    bootDataInBackground();
  } else if (isBillingPath) {
    await showBilling();
    bootDataInBackground();
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
/** A calendar date in the UI's language (Arabic as used in Kuwait). */
function formatDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const locale = Khan.currentLocale() === "ar" ? "ar-KW" : "en-GB";
  return d.toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" });
}

/** Absolute URL of an uploaded file. The API serves /uploads; relative to the
 *  dashboard origin the same path is answered by index.html. */
function uploadUrl(filename) {
  return `${API_BASE}/uploads/${encodeURIComponent(filename || "")}`;
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

// POST /screens/claim answers with plain-string details, so the status code
// picks the message and the text only separates cases sharing a status
// (both 404s; the expired and already-bound 400s -- the backend's wording is
// "already bound to another display").
function mapPairErrorMessage(err) {
  const detail = (err && err.data && typeof err.data.detail === "string") ? err.data.detail : "";
  const status = err?.status;
  if (!status) {
    return Khan.t("pair.error.network", "Can't reach the server. Please try again.");
  }
  if (status === 404 && /pairing code/i.test(detail)) {
    return Khan.t("pair.error.unknown_code", "That code isn't recognised. Check the TV screen and try again.");
  }
  if (status === 404) {
    return Khan.t("pair.error.screen_missing", "That screen no longer exists. Pick another one.");
  }
  if (status === 400 && /expired/i.test(detail)) {
    return Khan.t("pair.error.expired", "Code expired. Refresh the TV to get a new one.");
  }
  if (status === 400 && /already bound|another display|different screen/i.test(detail)) {
    return Khan.t("pair.error.bound_elsewhere", "This code belongs to a different display. Refresh the TV to get a new one.");
  }
  if (status === 409) {
    return Khan.t("pair.error.used", "That code's been used. Refresh the TV to get a new one.");
  }
  if (status === 402) {
    // plan_limit or an expired subscription: both arrive already localized.
    return err.message;
  }
  if (status === 403) {
    return Khan.t("pair.error.forbidden", "Your account doesn't have permission to pair displays.");
  }
  if (status === 422) {
    return Khan.t("pair.error.bad_code", "Enter the full code shown on the TV.");
  }
  return (err.isApiError && err.message) || Khan.t("error.generic", "Something went wrong. Please try again.");
}

/** Ask the public endpoint the TV itself polls whether `code` can still be
 *  claimed, so an expired or unknown code fails before a new screen is
 *  created for it. Only a definite "no" counts; a rate limit or network
 *  hiccup falls through to the claim, which has the final word. */
async function pairCodeRejection(code) {
  let res;
  try { res = await fetch(`${API_BASE}/screens/poll/${encodeURIComponent(code)}`); }
  catch (_) { return null; }
  if (res.status === 404) return { status: 404, data: { detail: "Unknown pairing code" } };
  if (!res.ok) return null;
  const body = await res.json().catch(() => null);
  if (body?.status === "expired") return { status: 400, data: { detail: "Pairing code expired." } };
  if (body?.status === "paired") return { status: 400, data: { detail: "already bound to another display" } };
  return null;
}

async function onPairSubmit(e) {
  e.preventDefault();
  clearPairError();
  const btn = document.getElementById("pair-submit");
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;

  await withLoading(btn, async () => {
    let createdScreen = null;
    try {
      let screenId = null;
      let screenName = "";

      if (target === "new") {
        const rejection = await pairCodeRejection(code);
        if (rejection) throw Object.assign(new Error(""), rejection, { isApiError: true });
        const name = document.getElementById("pair-new-name").value.trim();
        const screen = await api("/screens", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        createdScreen = screen;
        screenId = screen.id;
        screenName = screen.name || name;
      } else {
        const sel = document.getElementById("pair-existing-select");
        screenId = Number(sel.value);
        screenName = sel.options[sel.selectedIndex]?.textContent
          || Khan.t("pair.screen_n", "Screen #{id}").replace("{id}", screenId);
      }

      await api("/screens/claim", {
        method: "POST",
        body: JSON.stringify({ code, screen_id: screenId }),
      });

      document.getElementById("pair-success-name").textContent = screenName;
      setPairState("success");
      if (createdScreen) loadOrganization();   // plan card usage
    } catch (err) {
      console.error(err);
      let message = mapPairErrorMessage(err);
      if (createdScreen) {
        // The claim failed after we created a screen for it. Left in place it
        // is an unpaired screen counting against the plan's limit.
        try {
          await api(`/screens/${createdScreen.id}`, { method: "DELETE" });
        } catch (cleanupErr) {
          // Deleting screens is admin-only; an editor can create one but not
          // remove it, so say what was left behind.
          console.warn("could not remove the screen created for a failed pairing", cleanupErr);
          message += " " + Khan.t("pair.error.orphan_left",
            "A screen named \"{name}\" was created but couldn't be removed; an admin can delete it under Screens.")
            .replace("{name}", createdScreen.name || "");
        }
      }
      showPairError(message);
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
  // Reload rather than reuse: the pairing just changed the screens list.
  bootData({ section: "screens" }).catch((err) => {
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
// saveKey: localized "save N months" badge for the longer terms.
const BILLING_TERMS = [
  { months: 1,  multiplier: 1,  saveKey: null,                  saveFallback: "" },
  { months: 6,  multiplier: 5,  saveKey: "billing.save_1_month", saveFallback: "save 1 month" },
  { months: 12, multiplier: 10, saveKey: "billing.save_2_months", saveFallback: "save 2 months" },
];
const BILLING_TERM_LABEL_KEYS = { 1: "billing.term_monthly", 6: "billing.term_6mo", 12: "billing.term_12mo" };

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

function billingSaveLabel(term) {
  return term && term.saveKey ? Khan.t(term.saveKey, term.saveFallback) : "";
}

function billingTermLabel(months) {
  const key = BILLING_TERM_LABEL_KEYS[months];
  return key ? Khan.t(key) : String(months);
}

function upToScreensLabel(n) {
  return Khan.t("billing.up_to_screens", "up to {n} screens").replace("{n}", n);
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
  const plan = PLAN_TIERS.find((p) => p.tier === org.plan) || { label: org.plan || "—", screens: org.screen_limit };
  const parts = [`<strong>${escHtml(plan.label)}</strong>`];
  if (org.subscription_status === "trialing" && org.trial_ends_at) {
    const daysLeft = Math.max(0, Math.ceil((new Date(org.trial_ends_at) - new Date()) / 86400000));
    parts.push(escHtml(Khan.t("billing.trial_label", "Trial")));
    parts.push(escHtml(Khan.t(daysLeft === 1 ? "billing.days_left_one" : "billing.days_left_other",
      daysLeft === 1 ? "1 day left" : "{n} days left").replace("{n}", daysLeft)));
  } else if (org.paid_through_at) {
    parts.push(escHtml(Khan.t("billing.paid_through", "paid through {date}").replace("{date}", formatDay(org.paid_through_at))));
  }
  if (plan.screens != null) parts.push(escHtml(upToScreensLabel(plan.screens)));
  body.innerHTML = parts.join(" · ");
}

function renderBillingTiers() {
  const grid = document.getElementById("billing-tier-grid");
  const termInfo = BILLING_TERMS.find((t) => t.months === billingCurrentTerm);
  const saveLabel = billingSaveLabel(termInfo);
  // POST /billing/checkout is admin-only: others see the prices, not a
  // button that can only fail.
  const canPay = isAdmin();
  grid.innerHTML = "";
  for (const plan of PLAN_TIERS) {
    const amounts = billingAmountsFor(plan.tier, billingCurrentTerm);
    const saveMarkup = saveLabel
      ? `<span class="billing-tier-save">${escHtml(saveLabel)}</span>` : "";
    const card = document.createElement("div");
    card.className = "billing-tier";
    card.innerHTML = `
      <h3 class="billing-tier-name">${escHtml(plan.label)}</h3>
      <div class="billing-tier-limit">${escHtml(upToScreensLabel(plan.screens))}</div>
      <div class="billing-tier-kwd">${amounts.kwd} KWD${billingCurrentTerm === 1 ? " " + escHtml(Khan.t("billing.per_month", "/ month")) : ""}</div>
      <div class="billing-tier-usd" dir="ltr">≈ $${amounts.usdApprox}</div>
      ${saveMarkup}
      ${canPay ? `<button type="button" class="billing-tier-btn" data-tier="${escAttr(plan.tier)}">
        ${escHtml(Khan.t("billing.pay_btn", "Pay {amount} KWD").replace("{amount}", amounts.kwd))}${saveLabel ? " · " + escHtml(saveLabel) : ""}
      </button>` : ""}
    `;
    grid.appendChild(card);
  }
  if (!canPay && currentUser) {
    const note = document.createElement("p");
    note.className = "helper-text";
    note.textContent = Khan.t("billing.admin_only", "Only an administrator can change the plan or see past payments.");
    grid.appendChild(note);
  }
}

function billingStatusLabel(status) {
  return Khan.t(`billing.status_${status}`, status || "");
}

async function loadBillingHistory() {
  const body = document.getElementById("billing-history-body");
  if (!isAdmin()) {            // GET /billing/history is admin-only
    body.innerHTML = "";
    return;
  }
  try {
    const rows = await api("/billing/history");
    if (!rows.length) {
      body.innerHTML = `<p class="billing-empty">${escHtml(Khan.t("billing.no_payments", "No payments yet."))}</p>`;
      return;
    }
    body.innerHTML = rows.map((r) => {
      const plan = PLAN_TIERS.find((p) => p.tier === r.tier);
      return `<div class="billing-history-row ${r.status === 'failed' ? 'failed' : ''}">
        <span>${escHtml(plan ? plan.label : r.tier)} · ${escHtml(billingTermLabel(r.term_months))} · ${escHtml(formatDay(r.updated_at || r.created_at))}</span>
        <span>${escHtml(r.amount_kwd)} KWD · ${escHtml(billingStatusLabel(r.status))}</span>
      </div>`;
    }).join("");
  } catch (err) {
    body.innerHTML = `<p class="billing-empty">${escHtml(Khan.t("billing.history_failed", "Couldn't load history."))}</p>`;
  }
}

/** The org as the billing view needs it. GET /organization carries
 *  paid_through_at today; /auth/me's organization is the fallback (and gains
 *  the field too). */
async function fetchBillingOrg() {
  try {
    const org = await api("/organization");
    state.org = org;
    return org;
  } catch (_) {
    const me = await api("/auth/me");
    return me.organization;
  }
}

async function showBilling() {
  showBillingPanel();
  renderBillingTiers();
  try {
    renderBillingCurrent(await fetchBillingOrg());
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
    setBillingBanner("error", (err.isApiError && err.message) || Khan.t("billing.pay_failed", "Payment failed to start."));
  }
}

function maybeResumeBillingStatus() {
  const params = new URLSearchParams(location.search);
  const status = params.get("status");
  const trackid = params.get("trackid") || sessionStorage.getItem("billing_pending_trackid");
  if (!trackid || !status) return;
  sessionStorage.removeItem("billing_pending_trackid");
  setBillingBanner("success", Khan.t("billing.confirming", "Confirming payment…"));
  billingPollStartedAt = Date.now();
  pollBillingStatus(trackid);
}

async function pollBillingStatus(trackid) {
  try {
    const data = await api(`/billing/status/${encodeURIComponent(trackid)}`);
    if (data.status === "captured") {
      const plan = PLAN_TIERS.find((p) => p.tier === data.tier);
      setBillingBanner("success", Khan.t("billing.upgraded", "Plan upgraded · {plan} · paid through {date}")
        .replace("{plan}", plan ? plan.label : data.tier)
        .replace("{date}", formatDay(data.paid_through_at)));
      renderBillingCurrent(await fetchBillingOrg());
      if (state.org) { renderPlanCard(state.org); SubscriptionBanner.update(state.org); }
      loadBillingHistory();
      history.replaceState({}, "", "/billing");
      return;
    }
    if (data.status === "failed") {
      setBillingBanner("error", Khan.t("billing.declined", "Payment declined. You can try again."));
      history.replaceState({}, "", "/billing");
      return;
    }
    // still pending
    if (Date.now() - billingPollStartedAt > 15000) {
      setBillingBanner("success", Khan.t("billing.still_processing", "Payment is still processing — check back in a minute."));
      return;
    }
    billingPollTimer = setTimeout(() => pollBillingStatus(trackid), 2000);
  } catch (err) {
    setBillingBanner("error", Khan.t("billing.status_unknown", "Couldn't confirm payment status."));
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

// (the Billing nav button is handled by the single nav handler above, via goToBilling)

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
          <button class="media-picker-close btn-ghost" aria-label="${escAttr(Khan.t("common.close", "Close"))}">✕</button>
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
          <button class="btn media-picker-retry">${escHtml(Khan.t("media_picker.retry", "Retry"))}</button>
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
    const pill = Khan.t(`media_picker.type_${cls}`, cls === "url" ? "URL" : cls.toUpperCase());
    let thumb = "";
    // Same URL renderMedia uses: uploads live on the API origin.
    const fileUrl = m.url ? `${API_BASE}${m.url}` : uploadUrl(m.filename);
    if (cls === "image") {
      thumb = `<img src="${attrEscape(fileUrl)}" loading="lazy" alt="" />`;
    } else if (cls === "video") {
      thumb = `<video src="${attrEscape(fileUrl)}" preload="metadata" muted></video>`;
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
          <input type="number" min="1" max="3600" placeholder="${escAttr(Khan.t("media_picker.duration_default", "default"))}"
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
  // Requests go through the shared api(): same auth, empty-body handling and
  // localized error messages as the rest of the dashboard.

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
        ${canEditContent() ? `<div class="walls-actions">
          <button class="btn btn-ghost" data-action="edit">${Khan.t("walls.edit", "Edit")}</button>
          <button class="btn btn-danger" data-action="delete">${Khan.t("walls.delete", "Delete")}</button>
        </div>` : ""}
      </article>
    `).join("");
    root.querySelectorAll("[data-wall-id]").forEach(card => {
      const id = parseInt(card.dataset.wallId, 10);
      card.querySelector('[data-action="edit"]')?.addEventListener("click", () => openEditor(id));
      card.querySelector('[data-action="delete"]')?.addEventListener("click", () => deleteWall(id));
    });
  }

  async function createWizard() {
    const playlists = await api("/playlists");
    // Leaving an open editor for the wizard: stop its timers, drop its button.
    if (mosaicTimer) { clearInterval(mosaicTimer); mosaicTimer = null; }
    removeModeSwitchButton();
    state.editing = null;
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

  // The "Switch to …" button lives in the shared editor header and is bound to
  // one wall. Left behind, it shows up in the next editor or the new-wall
  // wizard -- and clicking it there changes the previous wall.
  function removeModeSwitchButton() {
    document.querySelectorAll(".walls-editor-header .walls-mode-switch-btn").forEach((b) => b.remove());
  }

  function closeEditor() {
    if (mosaicTimer) { clearInterval(mosaicTimer); mosaicTimer = null; }
    if (pairTimer)   { clearInterval(pairTimer);   pairTimer   = null; }
    document.getElementById("walls-pair-modal")?.classList.add("hidden");
    state.pairing = null;
    document.getElementById("walls-editor").classList.add("hidden");
    removeModeSwitchButton();
    state.editing = null;
  }

  function reset() {
    closeEditor();
    state.walls = [];
    const root = document.getElementById("walls-list");
    if (root) root.innerHTML = "";
  }

  // openEditor and pair-flow are filled in by Task 9.
  async function openEditor(id) {
    state.editing = id;
    removeModeSwitchButton();
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
    const synced = wall.mirrored_mode === "synced_rotation";
    // Both sub-modes need the list: per-cell pickers for synced rotation, the
    // wall's one playlist for same-playlist (which could not be changed
    // after creation before).
    const playlists = await api("/playlists");
    const canEdit = canEditContent();
    const samePlaylistPicker = synced ? "" : `
      <label class="walls-mirrored-playlist">
        ${Khan.t("walls.playlist", "Playlist")}
        <select data-action="wall-playlist" ${canEdit ? "" : "disabled"}>
          ${wall.mirrored_playlist_id ? "" : `<option value="" selected>${escHtml(Khan.t("walls.choose_playlist", "— Choose a playlist —"))}</option>`}
          ${playlists.map(p =>
            `<option value="${p.id}" ${p.id === wall.mirrored_playlist_id ? "selected" : ""}>${escHtml(p.name)}</option>`
          ).join("")}
        </select>
      </label>
      ${wall.mirrored_playlist_id ? "" : `<p class="helper-text">${escHtml(Khan.t("walls.no_playlist_warning",
        "This wall has no playlist yet, so its screens have nothing to show. Choose one above."))}</p>`}`;
    body.innerHTML = `
      <div class="walls-editor-summary">
        <span class="walls-meta">
          ${wall.mode === "mirrored"
            ? Khan.t("walls.mode_mirrored", "Mirrored") + (synced
                ? " · " + Khan.t("walls.synced_rotation_short", "Synced rotation")
                : " · " + Khan.t("walls.same_playlist_short", "Same playlist"))
            : Khan.t("walls.mode_spanned", "Spanned")}
          · ${wall.rows}×${wall.cols}
        </span>
        ${samePlaylistPicker}
      </div>
      <div class="scroll-x">
        <div class="walls-editor-grid"
             style="grid-template-columns: repeat(${wall.cols}, 1fr);">
          ${wall.cells.map(c => renderCellTile(c, wall, playlists)).join("")}
        </div>
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
    body.querySelector('select[data-action="wall-playlist"]')?.addEventListener("change", async (ev) => {
      if (!ev.target.value) return;
      const fields = { mirrored_playlist_id: parseInt(ev.target.value, 10) };
      if (!wall.mirrored_mode) fields.mirrored_mode = "same_playlist";
      try {
        await api(`/walls/${wall.id}`, { method: "PATCH", body: JSON.stringify(fields) });
        toast(Khan.t("walls.playlist_updated", "Wall playlist updated"), "success");
        await renderEditor(wall.id);
      } catch (err) {
        toast(err.message || Khan.t("walls.cell_update_failed", "Couldn't update"), "error");
      }
    });
    if (canEdit) mountModeSwitchButton(wall);
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
    const addBtn = body.querySelector("#canvas-add-item");
    if (canEditContent()) {
      addBtn.addEventListener("click", () => openCanvasMediaPicker(wall));
      mountModeSwitchButton(wall);
    } else {
      addBtn.remove();
      body.querySelector("#canvas-item-save")?.remove();
      body.querySelector("#canvas-item-delete")?.remove();
    }
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
        <small>${escHtml(Khan.t(`walls.fit_${it.fit_mode}`, it.fit_mode))} · ${escHtml(Khan.t("playlists.item_seconds", "{n}s").replace("{n}", it.duration_override_seconds || it.duration_seconds))}</small>
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
    const saveBtn = detail.querySelector("#canvas-item-save");
    const deleteBtn = detail.querySelector("#canvas-item-delete");
    if (saveBtn) saveBtn.onclick = () => saveCanvasItem(wall, item);
    if (deleteBtn) deleteBtn.onclick = () => deleteCanvasItem(wall, item);
    const preview = document.getElementById("canvas-preview-media");
    // Uploads are served by the API, not by the dashboard's origin (where
    // /uploads/... falls through to index.html and renders as a broken image).
    const src = item.filename ? uploadUrl(item.filename) : "";
    if (item.mime_type.startsWith("video/")) {
      preview.innerHTML = `<video src="${escAttr(src)}"
        muted autoplay loop playsinline></video>`;
    } else if (item.mime_type === "application/pdf") {
      preview.innerHTML = `<div class="pdf-thumb">PDF — ${escHtml(item.media_name)}</div>`;
    } else {
      preview.innerHTML = `<img src="${escAttr(src)}" alt="" />`;
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
      toast(err.message || Khan.t("walls.canvas_delete_failed", "Couldn't delete the item"), "error");
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
      toast(err.message || Khan.t("walls.canvas_add_failed", "Couldn't add the item"), "error");
    }
  }

  async function openModeChangeModal(wall, newMode) {
    // A mirrored wall needs a sub-mode, and a same-playlist one needs its
    // playlist: switching with neither left a wall that plays nothing.
    const playlists = newMode === "mirrored" ? await api("/playlists") : [];
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modal-card">
        <h3>${Khan.t("walls.mode_change_confirm_title", "Switch wall mode")}</h3>
        <p>${Khan.t("walls.mode_change_confirm_body",
          "Switching this wall to {mode} will permanently delete its current playlist. Cell pairings stay.")
          .replace("{mode}", Khan.t(`walls.mode_${newMode}`, newMode))}</p>
        ${newMode === "mirrored" ? `
        <fieldset class="mirrored-fields">
          <legend>${Khan.t("walls.mirrored_submode", "Mirrored sub-mode")}</legend>
          <label><input type="radio" name="mode-change-submode" value="same_playlist" checked />
            ${Khan.t("walls.same_playlist", "Same playlist on all screens")}</label>
          <label><input type="radio" name="mode-change-submode" value="synced_rotation" />
            ${Khan.t("walls.synced_rotation", "Different playlist per cell, synchronized rotation")}</label>
          <label class="same-playlist-only">
            ${Khan.t("walls.playlist", "Playlist")}
            <select id="mode-change-playlist">
              <option value="">${escHtml(Khan.t("walls.choose_playlist", "— Choose a playlist —"))}</option>
              ${playlists.map(p => `<option value="${p.id}">${escHtml(p.name)}</option>`).join("")}
            </select>
          </label>
        </fieldset>` : ""}
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
    const playlistSel = overlay.querySelector("#mode-change-playlist");
    const subMode = () => overlay.querySelector('input[name="mode-change-submode"]:checked')?.value;
    const refreshSwitchEnabled = () => {
      const needsPlaylist = newMode === "mirrored" && subMode() === "same_playlist";
      switchBtn.disabled = typed.value !== wall.name || (needsPlaylist && !playlistSel.value);
    };
    typed.addEventListener("input", refreshSwitchEnabled);
    playlistSel?.addEventListener("change", refreshSwitchEnabled);
    overlay.querySelectorAll('input[name="mode-change-submode"]').forEach((el) => {
      el.addEventListener("change", () => {
        overlay.querySelector(".same-playlist-only").style.display = subMode() === "same_playlist" ? "" : "none";
        refreshSwitchEnabled();
      });
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
        payload.mirrored_mode = subMode() || "same_playlist";
        if (payload.mirrored_mode === "same_playlist") {
          if (!playlistSel.value) return;
          payload.mirrored_playlist_id = parseInt(playlistSel.value, 10);
        }
      }
      try {
        await api(`/walls/${wall.id}`, {method: "PATCH", body: JSON.stringify(payload)});
        toast(Khan.t("walls.mode_changed", "Mode changed"));
        overlay.remove();
        await loadList();
        openEditor(wall.id);
      } catch (err) {
        toast(err.message || Khan.t("walls.mode_change_failed", "Couldn't change the wall mode"), "error");
      }
    });
  }

  return {
    onShow: loadList,
    state,
    loadList,
    openEditor,
    closeEditor,
    reset,
    renderList,
    renderEditor,
  };
})();

// ── Schedules (Phase 2.5e) ───────────────────────────────────────────
const Schedules = (() => {
  let cachedPlaylists = [];
  let currentSchedule = null;     // {id, name, rules} or null when creating new

  const DOW_LABELS = ["dow.mon", "dow.tue", "dow.wed", "dow.thu", "dow.fri", "dow.sat", "dow.sun"];

  async function show() {
    document.getElementById("schedule-editor").classList.add("hidden");
    document.getElementById("schedules-list").classList.remove("hidden");
    await refreshList();
  }

  async function refreshList() {
    try {
      const body = await api("/schedules");
      renderList(body.items);
    } catch (err) {
      toast(Khan.t("schedules.error.fetch", "Failed to load schedules."), "error");
    }
  }

  function renderList(items) {
    const container = document.getElementById("schedules-list");
    container.innerHTML = "";
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = Khan.t("schedules.empty", "No schedules yet.");
      container.appendChild(p);
      return;
    }
    items.forEach(s => {
      const card = document.createElement("div");
      card.className = "schedule-card";
      const name = document.createElement("h3");
      name.textContent = s.name;
      card.appendChild(name);
      const actions = document.createElement("div");
      actions.className = "schedule-card-actions";
      if (canEditContent()) {
        const editBtn = document.createElement("button");
        editBtn.className = "btn";
        editBtn.textContent = Khan.t("schedules.editor.edit", "Edit");
        editBtn.addEventListener("click", () => openEditor(s.id));
        actions.appendChild(editBtn);
      }
      if (isAdmin()) {   // DELETE /schedules/{id} is admin-only
        const delBtn = document.createElement("button");
        delBtn.className = "btn btn-danger";
        delBtn.textContent = Khan.t("schedules.editor.delete", "Delete");
        delBtn.addEventListener("click", () => deleteSchedule(s.id, s.name));
        actions.appendChild(delBtn);
      }
      if (actions.children.length) card.appendChild(actions);
      container.appendChild(card);
    });
  }

  async function openEditor(id) {
    // Fetched on every open, never cached across opens: a cached list goes
    // stale as soon as a playlist is created, and survives into the next
    // org's session after a logout.
    try {
      const pls = await api("/playlists");
      cachedPlaylists = Array.isArray(pls) ? pls : (pls.items || []);
    } catch (_) { cachedPlaylists = []; }
    if (id) {
      try {
        currentSchedule = await api(`/schedules/${id}`);
      } catch (err) {
        toast(Khan.t("schedules.error.fetch", "Failed to load schedule."), "error");
        return;
      }
    } else {
      currentSchedule = { id: null, name: "", rules: [] };
    }
    renderEditor();
  }

  function renderEditor() {
    document.getElementById("schedules-list").classList.add("hidden");
    document.getElementById("schedule-editor").classList.remove("hidden");
    document.getElementById("schedule-editor-name").value = currentSchedule.name || "";
    renderRules();
  }

  function renderRules() {
    const tbody = document.getElementById("schedule-rules-tbody");
    tbody.innerHTML = "";
    currentSchedule.rules.forEach((r, idx) => tbody.appendChild(buildRuleRow(r, idx)));
  }

  function buildRuleRow(rule, idx) {
    const tr = document.createElement("tr");

    // Playlist dropdown
    const tdP = document.createElement("td");
    const sel = document.createElement("select");
    cachedPlaylists.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name;
      if (rule.playlist_id === p.id) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", () => { rule.playlist_id = parseInt(sel.value, 10); });
    tdP.appendChild(sel);
    tr.appendChild(tdP);

    // Start time
    const tdS = document.createElement("td");
    const startIn = document.createElement("input");
    startIn.type = "time";
    startIn.value = rule.start_time || "11:00";
    startIn.addEventListener("change", () => { rule.start_time = startIn.value; });
    tdS.appendChild(startIn);
    tr.appendChild(tdS);

    // End time
    const tdE = document.createElement("td");
    const endIn = document.createElement("input");
    endIn.type = "time";
    endIn.value = rule.end_time || "14:00";
    endIn.addEventListener("change", () => { rule.end_time = endIn.value; });
    tdE.appendChild(endIn);
    tr.appendChild(tdE);

    // Days of week — 7 checkboxes
    const tdD = document.createElement("td");
    DOW_LABELS.forEach((labelKey, bit) => {
      const lbl = document.createElement("label");
      lbl.className = "dow-checkbox";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!(rule.days_of_week & (1 << bit));
      cb.addEventListener("change", () => {
        if (cb.checked) rule.days_of_week |= (1 << bit);
        else            rule.days_of_week &= ~(1 << bit);
      });
      const span = document.createElement("span");
      span.setAttribute("data-i18n", `schedules.${labelKey}`);
      span.textContent = Khan.t(`schedules.${labelKey}`, labelKey.split(".")[1]);
      lbl.appendChild(cb);
      lbl.appendChild(span);
      tdD.appendChild(lbl);
    });
    tr.appendChild(tdD);

    // Delete button
    const tdX = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "btn btn-icon";
    btn.textContent = "×";
    btn.addEventListener("click", () => {
      currentSchedule.rules.splice(idx, 1);
      renderRules();
    });
    tdX.appendChild(btn);
    tr.appendChild(tdX);

    return tr;
  }

  function addRule() {
    if (!cachedPlaylists.length) {
      toast(Khan.t("schedules.no_playlists", "Create a playlist first."), "error");
      return;
    }
    currentSchedule.rules.push({
      playlist_id:  cachedPlaylists[0].id,
      start_time:   "11:00",
      end_time:     "14:00",
      days_of_week: 31,    // Mon-Fri
      position:     currentSchedule.rules.length,
    });
    renderRules();
  }

  async function save() {
    const nameEl = document.getElementById("schedule-editor-name");
    const name = nameEl.value.trim();
    if (!name) {
      toast(Khan.t("schedules.name_required", "Schedule name is required."), "error");
      return;
    }
    // The backend needs at least one day per rule (1..127); without this
    // check an all-unchecked row came back as a raw Pydantic error.
    if (currentSchedule.rules.some((r) => !r.days_of_week)) {
      toast(Khan.t("schedules.days_required", "Pick at least one day for every rule."), "error");
      return;
    }
    try {
      let sid = currentSchedule.id;
      if (sid == null) {
        const created = await api("/schedules", {
          method: "POST", body: JSON.stringify({ name }),
        });
        sid = created.id;
        // Remember it now: if saving the rules below fails, the next Save
        // must update this schedule rather than create a duplicate.
        currentSchedule.id = sid;
        refreshScheduleOptions();
      } else {
        await api(`/schedules/${sid}`, {
          method: "PUT", body: JSON.stringify({ name }),
        });
      }
      await api(`/schedules/${sid}/rules`, {
        method: "PUT",
        body: JSON.stringify({
          rules: currentSchedule.rules.map((r, i) => ({
            playlist_id:  r.playlist_id,
            start_time:   r.start_time,
            end_time:     r.end_time,
            days_of_week: r.days_of_week,
            position:     i,
          })),
        }),
      });
      toast(Khan.t("toast.schedule_saved", "Schedule saved."), "success");
      refreshScheduleOptions();
      await show();
    } catch (err) {
      const code = err.data?.detail?.code;
      const msg = code === "schedule.rule_overlap"
        ? Khan.t("schedules.overlap_error", "Two rules overlap on the same day.")
        : ((err.isApiError && err.message) || Khan.t("schedules.error.save", "Failed to save schedule."));
      toast(msg, "error");
    }
  }

  async function deleteSchedule(id, name) {
    const ok = await confirmDialog({
      title:        Khan.t("schedules.delete_confirm_title", "Delete schedule?"),
      message:      Khan.t("schedules.delete_confirm_message",
                           "Delete schedule \"{name}\"? Screens using it will fall back to their default playlist.")
                       .replace("{name}", name),
      confirmLabel: Khan.t("schedules.editor.delete", "Delete"),
      danger:       true,
    });
    if (!ok) return;
    try {
      await api(`/schedules/${id}`, { method: "DELETE" });
      toast(Khan.t("toast.schedule_deleted", "Schedule deleted."), "success");
      refreshScheduleOptions();
      await refreshList();
    } catch (err) {
      toast(err.message || Khan.t("schedules.error.delete", "Failed to delete."), "error");
    }
  }

  function init() {
    document.getElementById("schedule-new-btn")?.addEventListener("click", () => openEditor(null));
    document.getElementById("schedule-add-rule-btn")?.addEventListener("click", addRule);
    document.getElementById("schedule-save-btn")?.addEventListener("click", save);
    document.getElementById("schedule-cancel-btn")?.addEventListener("click", () => show());
  }

  function reset() {
    cachedPlaylists = [];
    currentSchedule = null;
    document.getElementById("schedule-editor")?.classList.add("hidden");
    document.getElementById("schedules-list")?.classList.remove("hidden");
    const list = document.getElementById("schedules-list");
    if (list) list.innerHTML = "";
  }

  return { show, init, reset };
})();

Schedules.init();

// ── Audit Log (Phase 2.5c) ───────────────────────────────────────────
const AuditLog = (() => {
  const PAGE_SIZE = 50;
  let offset = 0;
  let total = 0;

  async function show() {
    // Rebuilt on every visit: users come and go, and a list filled once
    // would still show the previous org's usernames after a logout.
    await populateActorFilter();
    offset = 0;
    await fetchPage();
  }

  function clearActorOptions(sel) {
    // Keep the leading "All" option, drop every username.
    while (sel.options.length > 1) sel.remove(1);
  }

  async function populateActorFilter() {
    const sel = document.getElementById("audit-filter-actor");
    if (!sel) return;
    const previous = sel.value;
    try {
      const users = await api("/users");
      clearActorOptions(sel);
      users.forEach(u => {
        const opt = document.createElement("option");
        opt.value = u.id;
        opt.textContent = u.username;
        sel.appendChild(opt);
      });
      sel.value = users.some((u) => String(u.id) === previous) ? previous : "";
    } catch (_) { clearActorOptions(sel); /* leave dropdown with just "All" */ }
  }

  function reset() {
    offset = 0;
    total = 0;
    const sel = document.getElementById("audit-filter-actor");
    if (sel) { clearActorOptions(sel); sel.value = ""; }
    const tbody = document.getElementById("audit-log-tbody");
    if (tbody) tbody.innerHTML = "";
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

  return { show, init, reset };
})();

AuditLog.init();

// ── Subscription banner (Phase 2.5f) ─────────────────────────────────
const SubscriptionBanner = (() => {
  const DISMISS_KEY = "khan_sub_banner_dismissed_state";
  let lastState = null;

  function update(orgData) {
    const banner = document.getElementById("subscription-banner");
    if (!banner) return;

    const subState = orgData?.state;
    const days     = orgData?.days_remaining;
    lastState      = subState;

    // Reset dismiss memory if state has changed since last dismiss
    const dismissedFor = sessionStorage.getItem(DISMISS_KEY);
    if (dismissedFor && dismissedFor !== subState) {
      sessionStorage.removeItem(DISMISS_KEY);
    }
    if (sessionStorage.getItem(DISMISS_KEY) === subState) {
      banner.classList.add("hidden");
      return;
    }

    const cfg = computeBannerConfig(subState, days);
    if (!cfg) { banner.classList.add("hidden"); return; }

    banner.classList.remove("hidden", "sub-banner-info", "sub-banner-warn", "sub-banner-error");
    banner.classList.add(`sub-banner-${cfg.tone}`);
    document.getElementById("subscription-banner-text").textContent = cfg.text;

    const cta = document.getElementById("subscription-banner-cta");
    cta.textContent = cfg.ctaText;
    cta.onclick = (e) => {
      e.preventDefault();
      // "billing" is not a dashboard section — showSection would blank the page.
      if (typeof goToBilling === "function") goToBilling();
    };
  }

  function computeBannerConfig(subState, days) {
    switch (subState) {
      case "trialing":
        if (days == null) return null;
        if (days > 3) return {
          tone:    "info",
          text:    Khan.t("sub_banner.trialing", "Trial — {n} days left.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
        return {
          tone:    "warn",
          text:    Khan.t("sub_banner.trialing_urgent", "Trial ends in {n} days.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
      case "trial_expired":
        return {
          tone:    "error",
          text:    Khan.t("sub_banner.trial_expired", "Trial ended — subscribe to make changes."),
          ctaText: Khan.t("sub_banner.cta_subscribe", "Subscribe"),
        };
      case "active":
        if (days != null && days <= 7) return {
          tone:    "warn",
          text:    Khan.t("sub_banner.renewal_soon", "Renewal in {n} days.").replace("{n}", days),
          ctaText: Khan.t("sub_banner.cta_manage", "Manage"),
        };
        return null;
      case "lapsed":
        return {
          tone:    "error",
          text:    Khan.t("sub_banner.lapsed", "Subscription expired — renew to make changes."),
          ctaText: Khan.t("sub_banner.cta_renew", "Renew"),
        };
      default:
        return null;
    }
  }

  function init() {
    document.getElementById("subscription-banner-dismiss")
      ?.addEventListener("click", () => {
        if (lastState) sessionStorage.setItem(DISMISS_KEY, lastState);
        document.getElementById("subscription-banner").classList.add("hidden");
      });
  }

  return { update, init };
})();

SubscriptionBanner.init();

// ── API Keys (Phase 2.5h) ───────────────────────────────────────────
const ApiKeys = (() => {
  async function show() {
    document.getElementById("api-key-create-modal").classList.add("hidden");
    document.getElementById("api-key-reveal-modal").classList.add("hidden");
    await refreshList();
  }

  async function refreshList() {
    const container = document.getElementById("api-keys-list");
    try {
      const body = await api("/api-keys");
      renderList(body.items || []);
    } catch (err) {
      toast(Khan.t("api_keys.error.fetch", "Failed to load API keys."), "error");
    }
  }

  function renderList(items) {
    const container = document.getElementById("api-keys-list");
    container.innerHTML = "";
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = Khan.t("api_keys.empty", "No API keys yet. Create one to get started.");
      container.appendChild(p);
      return;
    }
    items.forEach(k => container.appendChild(buildCard(k)));
  }

  function buildCard(k) {
    const card = document.createElement("div");
    card.className = "api-key-card" + (k.revoked_at ? " revoked" : "");

    const h = document.createElement("h3");
    h.textContent = k.name;
    card.appendChild(h);

    const meta = document.createElement("div");
    meta.className = "api-key-meta";
    meta.innerHTML = `
      <code>${escHtml(k.key_prefix)}…</code>
      <span class="badge">${escHtml(k.scope)}</span>
      <span class="muted">${escHtml(Khan.t("api_keys.col.created", "Created"))}: ${formatDate(k.created_at)}</span>
      <span class="muted">${escHtml(Khan.t("api_keys.col.last_used", "Last used"))}: ${k.last_used_at ? formatDate(k.last_used_at) : Khan.t("api_keys.never_used", "never")}</span>
      ${k.revoked_at ? `<span class="muted">${escHtml(Khan.t("api_keys.col.revoked", "Revoked"))}: ${formatDate(k.revoked_at)}</span>` : ""}
    `;
    card.appendChild(meta);

    if (!k.revoked_at) {
      const actions = document.createElement("div");
      actions.className = "api-key-actions";
      const revoke = document.createElement("button");
      revoke.className = "btn btn-danger";
      revoke.textContent = Khan.t("api_keys.col.revoke", "Revoke");
      revoke.addEventListener("click", () => revokeKey(k.id, k.name));
      actions.appendChild(revoke);
      card.appendChild(actions);
    }

    return card;
  }

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }
  function formatDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  function openCreateModal() {
    document.getElementById("api-key-name").value = "";
    const radios = document.querySelectorAll('input[name="api-key-scope"]');
    radios.forEach((r) => { r.checked = r.value === "api:rw"; });
    document.getElementById("api-key-create-modal").classList.remove("hidden");
  }

  async function submitCreate() {
    const name = document.getElementById("api-key-name").value.trim();
    if (!name) {
      toast(Khan.t("api_keys.name_required", "Name is required."), "error");
      return;
    }
    const scopeRadio = document.querySelector('input[name="api-key-scope"]:checked');
    const scope = scopeRadio ? scopeRadio.value : "api:rw";
    try {
      const created = await api("/api-keys", {
        method: "POST",
        body: JSON.stringify({ name, scope }),
      });
      document.getElementById("api-key-create-modal").classList.add("hidden");
      revealNewKey(created);
      await refreshList();
    } catch (err) {
      toast(err.message || Khan.t("api_keys.error.create", "Failed to create key."), "error");
    }
  }

  function revealNewKey(created) {
    document.getElementById("api-key-secret-block").textContent = created.key;
    document.getElementById("api-key-reveal-modal").classList.remove("hidden");
  }

  async function copySecret() {
    const text = document.getElementById("api-key-secret-block").textContent;
    try {
      await navigator.clipboard.writeText(text);
      toast(Khan.t("api_keys.created.copied", "Copied to clipboard."), "success");
    } catch (_) {
      toast(Khan.t("api_keys.created.copy_failed", "Copy failed — select the key and use Ctrl+C."), "error");
    }
  }

  function closeRevealModal() {
    // Clear the secret from the DOM so it doesn't linger if the user navigates back
    document.getElementById("api-key-secret-block").textContent = "";
    document.getElementById("api-key-reveal-modal").classList.add("hidden");
  }

  async function revokeKey(id, name) {
    const ok = await confirmDialog({
      title:        Khan.t("api_keys.revoke_confirm.title", "Revoke API key?"),
      message:      Khan.t("api_keys.revoke_confirm.message",
                           "Revoke API key \"{name}\"? Any agent using it will stop working immediately.")
                       .replace("{name}", name),
      confirmLabel: Khan.t("api_keys.col.revoke", "Revoke"),
      danger:       true,
    });
    if (!ok) return;
    try {
      await api(`/api-keys/${id}`, { method: "DELETE" });
      toast(Khan.t("api_keys.revoked_toast", "API key revoked."), "success");
      await refreshList();
    } catch (err) {
      toast(err.message || Khan.t("api_keys.error.revoke", "Failed to revoke."), "error");
    }
  }

  function init() {
    document.getElementById("api-key-new-btn")?.addEventListener("click", openCreateModal);
    document.getElementById("api-key-create-cancel")?.addEventListener("click", () =>
      document.getElementById("api-key-create-modal").classList.add("hidden"));
    document.getElementById("api-key-create-submit")?.addEventListener("click", submitCreate);
    document.getElementById("api-key-copy-btn")?.addEventListener("click", copySecret);
    document.getElementById("api-key-reveal-done")?.addEventListener("click", closeRevealModal);
  }

  return { show, init };
})();

ApiKeys.init();

