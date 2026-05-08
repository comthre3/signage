# Media Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the canvas-playlist `prompt()` and the mirrored-playlist `<select>+<input>` add flows with one shared `MediaPicker` modal — thumbnail grid, multi-select, type-allowlist, name search, filter chips, optional per-item duration override, EN+AR i18n parity.

**Architecture:** New `MediaPicker` IIFE in `frontend/app.js` exposing `MediaPicker.open({allowedTypes}) → Promise<PickedItem[]>`. Two callers (spanned canvas + mirrored playlist) replace their old picker code with a single call. One backend tweak makes `POST /playlists/{id}/items` accept optional `duration_seconds` falling back to mime-typed defaults. No schema changes.

**Tech Stack:** Vanilla JS (IIFE, no framework), CSS Grid, FastAPI (Pydantic optional field), pytest. EN+AR i18n through existing `Khan.t()` helper and `scripts/check_i18n.py` parity gate.

**Branch:** `feature/media-picker` — already created, spec committed at `33c456f`. Stack: `media-picker → multi-screen-walls-phase2 → multi-screen-walls-phase1 → main`.

**Spec:** `docs/superpowers/specs/2026-05-07-media-picker-design.md`.

**Test budget:** 148 → 149 backend tests (frontend has no automated tests; manual smoke in Task 9).

---

## File map

- **New:** `backend/tests/test_playlists.py` — single pytest for the optional-duration default fallback. (Keep narrow; the file may grow later for picker-unrelated playlist tests.)
- **Modify:** `backend/main.py` — `PlaylistItemCreate` schema and `add_playlist_item` handler around line 489 / 2292–2325.
- **Modify:** `frontend/i18n/en.json` and `frontend/i18n/ar.json` — append 15 new keys before the closing `}`.
- **Modify:** `frontend/app.js`
  - **Add** the `MediaPicker` IIFE (place near the top of the file alongside other namespaces, OR just above the Walls IIFE — the existing convention is one big file with multiple namespaces).
  - **Replace** the body of `Walls.openCanvasMediaPicker(wall, mediaList)` (~line 2237 in current branch tip) with a call into `MediaPicker.open`.
  - **Replace** the `#playlist-add-item` click handler (~line 1070) with a call into `MediaPicker.open`. Remove the `mediaId`/`duration` reads.
- **Modify:** `frontend/index.html` — remove `<select id="playlist-media">` and `<input id="playlist-duration">` (lines 351–352); update the `#playlist-add-item` button label key from `playlists.add_item` to `playlist.add_media`.
- **Modify:** `frontend/styles.css` — append a `.media-picker-modal` block (one CSS-grid layout, card styles, chip styles, advanced-section styles).

---

## Task 1: Backend — `duration_seconds` becomes optional with mime-typed default

**Files:**
- Modify: `backend/main.py` (lines 489–491, 2292–2325)
- Create: `backend/tests/test_playlists.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_playlists.py` with this content:

```python
import io

import pytest
from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_playlist(client: TestClient, headers: dict) -> int:
    r = client.post("/playlists", json={"name": "P1"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload_image(client: TestClient, headers: dict, name: str = "img.png") -> int:
    # Minimal 1x1 PNG so the upload pipeline accepts it.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    r = client.post(
        "/media",
        files={"file": (name, io.BytesIO(png), "image/png")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_add_playlist_item_without_duration_uses_image_default(
    client: TestClient, signed_up_org: dict
) -> None:
    headers = _auth_headers(signed_up_org["token"])
    playlist_id = _create_playlist(client, headers)
    media_id = _upload_image(client, headers)

    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id},  # NO duration_seconds
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 10  # image default


def test_add_playlist_item_with_explicit_duration_is_respected(
    client: TestClient, signed_up_org: dict
) -> None:
    headers = _auth_headers(signed_up_org["token"])
    playlist_id = _create_playlist(client, headers)
    media_id = _upload_image(client, headers)

    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id, "duration_seconds": 42},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 42
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_playlists.py -v 2>&1 | tail -15
```

Expected: the first test fails (Pydantic validation error: `duration_seconds field required` or `value is not a valid integer`). The second test passes (current contract still works).

- [ ] **Step 3: Make `duration_seconds` optional in the schema**

In `backend/main.py` around line 489–491, replace:

```python
class PlaylistItemCreate(BaseModel):
    media_id: int
    duration_seconds: int = Field(10, ge=1, le=3600)
```

with:

```python
class PlaylistItemCreate(BaseModel):
    media_id: int
    duration_seconds: Optional[int] = Field(None, ge=1, le=3600)
```

(`Optional` is already imported at the top of `main.py` — Phase 1 used it. If grep says otherwise, add `from typing import Optional` to the imports.)

- [ ] **Step 4: Add a small helper for the mime-typed default**

In `backend/main.py`, just above the `add_playlist_item` handler (~line 2290), add:

```python
def _default_duration_seconds(media: dict) -> int:
    """Default playlist-item duration when caller omits duration_seconds."""
    mime = (media.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return 10
    if mime.startswith("video/"):
        stored = media.get("duration_seconds")
        if isinstance(stored, (int, float)) and stored > 0:
            return max(1, int(stored))
        return 10
    if mime == "application/pdf":
        return 30
    if mime == "text/url":
        return 10
    return 10
```

(Note: `media` rows in this codebase carry a `mime_type` column and may carry a stored `duration_seconds` for videos. If `duration_seconds` isn't on the media row, `media.get("duration_seconds")` simply returns `None` and we fall through to the 10s fallback — no error.)

- [ ] **Step 5: Use the helper in the handler**

In `backend/main.py`, replace the lines in `add_playlist_item` that compute and pass `payload.duration_seconds`. Around line 2314–2321, change:

```python
    item_id = execute(
        """
        INSERT INTO playlist_items
        (playlist_id, media_id, duration_seconds, position, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (playlist_id, payload.media_id, payload.duration_seconds, position, utc_now_iso()),
    )
```

to:

```python
    duration_seconds = (
        payload.duration_seconds
        if payload.duration_seconds is not None
        else _default_duration_seconds(media)
    )
    item_id = execute(
        """
        INSERT INTO playlist_items
        (playlist_id, media_id, duration_seconds, position, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (playlist_id, payload.media_id, duration_seconds, position, utc_now_iso()),
    )
```

- [ ] **Step 6: Run the new tests; both must pass**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_playlists.py -v 2>&1 | tail -10
```

Expected: `2 passed`.

- [ ] **Step 7: Run the full backend suite to confirm nothing regressed**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -3
```

Expected: `150 passed` (148 baseline + 2 new).

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_playlists.py
git commit -m "$(cat <<'EOF'
feat(media-picker): backend — optional duration_seconds with mime default

POST /playlists/{id}/items now accepts missing/null duration_seconds
and falls back to a per-mime default (10s images, video file
duration if known else 10s, 30s PDFs, 10s URLs/other). Two new
pytest cases pin the default and the explicit-override paths.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: i18n — 15 new keys (EN + AR)

**Files:**
- Modify: `frontend/i18n/en.json`
- Modify: `frontend/i18n/ar.json`

- [ ] **Step 1: Append the 15 keys to `frontend/i18n/en.json`**

Find the last key in the file (currently `walls.canvas_resolution_invalid`). Add a comma after its value, then append:

```json
  "media_picker.title": "Pick media",
  "media_picker.search_placeholder": "Search by name…",
  "media_picker.filter_all": "All",
  "media_picker.filter_images": "Images",
  "media_picker.filter_videos": "Videos",
  "media_picker.filter_pdfs": "PDFs",
  "media_picker.filter_urls": "URLs",
  "media_picker.advanced_durations": "Advanced: set per-item durations",
  "media_picker.cancel": "Cancel",
  "media_picker.add_n": "Add {n} items",
  "media_picker.selected_n": "{n} selected",
  "media_picker.empty_library": "No media yet. Upload some in the Media tab.",
  "media_picker.empty_filtered": "No matches.",
  "media_picker.fetch_failed": "Couldn't load media.",
  "playlist.add_media": "Add media"
```

(Total 15 keys — `playlist.add_media` replaces the old `playlists.add_item` button label which becomes unused.)

- [ ] **Step 2: Append the same 15 keys to `frontend/i18n/ar.json`**

```json
  "media_picker.title": "اختر الوسائط",
  "media_picker.search_placeholder": "ابحث بالاسم…",
  "media_picker.filter_all": "الكل",
  "media_picker.filter_images": "الصور",
  "media_picker.filter_videos": "الفيديوهات",
  "media_picker.filter_pdfs": "PDF",
  "media_picker.filter_urls": "روابط",
  "media_picker.advanced_durations": "متقدم: تعيين مدة لكل عنصر",
  "media_picker.cancel": "إلغاء",
  "media_picker.add_n": "إضافة {n} عناصر",
  "media_picker.selected_n": "{n} محدد",
  "media_picker.empty_library": "لا توجد وسائط بعد. ارفع بعضها من تبويب الوسائط.",
  "media_picker.empty_filtered": "لا توجد نتائج.",
  "media_picker.fetch_failed": "تعذر تحميل الوسائط.",
  "playlist.add_media": "إضافة وسائط"
```

- [ ] **Step 3: Validate JSON + parity**

```bash
python3 -c "import json; en=json.load(open('frontend/i18n/en.json')); ar=json.load(open('frontend/i18n/ar.json')); print('en:', len(en), 'ar:', len(ar), 'parity_ok:', set(en)==set(ar))"
python3 scripts/check_i18n.py
```

Expected:
- `en: 258 ar: 258 parity_ok: True` (243 baseline + 15 new)
- `i18n OK across frontend, landing, player`

- [ ] **Step 4: Commit**

```bash
git add frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(media-picker): i18n — 15 new keys EN + AR (MSA)

Picker title, search placeholder, 5 filter chips (All/Images/Videos/
PDFs/URLs), advanced-duration toggle, cancel + add{n}, selected{n}
counter, empty-library + empty-filtered states, fetch-failed toast,
and a re-purposed playlist.add_media button label.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: MediaPicker IIFE — skeleton, mount, fetch, grid, multi-select

**Files:**
- Modify: `frontend/app.js` (add a new IIFE; place it just above the `Walls` IIFE so callers later in the file can reference it)

- [ ] **Step 1: Locate the Walls IIFE start**

```bash
grep -n "^const Walls = (() => {" frontend/app.js
```

Expected: one match. Note the line number. The new `MediaPicker` IIFE goes immediately above this line.

- [ ] **Step 2: Add the `MediaPicker` IIFE skeleton**

Insert the following block just above `const Walls = (() => {`:

```javascript
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
  };

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
    const grid = state.overlay.querySelector(".media-picker-grid");
    grid.textContent = "…";
    try {
      const all = await api("/media");
      state.mediaList = all.filter(m =>
        state.allowedTypes.includes(classifyMime(m.mime_type))
      );
      renderGrid();
    } catch (err) {
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
        </div>
      `;
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
      thumb = `<img src="/uploads/${m.filename || ""}" loading="lazy" alt="" />`;
    } else if (cls === "video") {
      thumb = `<video src="/uploads/${m.filename || ""}" preload="metadata" muted></video>`;
    } else if (cls === "pdf") {
      thumb = `<div class="picker-thumb-pdf">PDF</div>`;
    } else if (cls === "url") {
      let host = "";
      try { host = new URL(m.url || "").hostname; } catch (_) {}
      thumb = host
        ? `<div class="picker-thumb-url"><img src="https://www.google.com/s2/favicons?domain=${host}&sz=64" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'🌐'}))" /></div>`
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
```

- [ ] **Step 3: Verify the JS parses**

```bash
node --check frontend/app.js && echo OK
```

Expected: `OK`. If not, paste the syntax error and stop.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "$(cat <<'EOF'
feat(media-picker): MediaPicker IIFE — modal, grid, multi-select, search, chips

New shared component MediaPicker.open({allowedTypes}) returns a
Promise<PickedItem[]>. Type-allowlist filtering, name search (client-
side), filter chips (All / Images / Videos / PDFs / URLs limited to
caller's allowedTypes), thumbnail cards with checkbox + pick-order
badges, Advanced section for per-item duration override, Esc /
backdrop / Cancel reject with {cancelled:true}.

No callers wired yet — done in Tasks 4 and 5.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire the picker into the spanned canvas-playlist add

**Files:**
- Modify: `frontend/app.js` — `Walls.openCanvasMediaPicker` (currently at the end of the canvas editor block; grep for `function openCanvasMediaPicker`)

- [ ] **Step 1: Locate the function**

```bash
grep -n "async function openCanvasMediaPicker" frontend/app.js
```

Expected: one hit (Phase 2 Task 8 added it).

- [ ] **Step 2: Replace the function body**

Find and replace the entire function:

```javascript
  async function openCanvasMediaPicker(wall, mediaList) {
    const allowed = mediaList.filter(m =>
      m.mime_type.startsWith("image/") ||
      m.mime_type.startsWith("video/") ||
      m.mime_type === "application/pdf");
    if (!allowed.length) {
      toast(Khan.t("walls.canvas_no_media", "Upload an image, video, or PDF first."), "error");
      return;
    }
    const id = parseInt(prompt(
      Khan.t("walls.canvas_pick_media", "Pick media id:") + "\n" +
      allowed.map(m => `${m.id}: ${m.name} (${m.mime_type})`).join("\n")), 10);
    if (!id || !allowed.find(m => m.id === id)) return;
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
    const position = list.items.length;
    try {
      await api(`/walls/${wall.id}/canvas-playlist/items`, {
        method: "POST",
        body: JSON.stringify({media_id: id, position, fit_mode: "fit"}),
      });
      toast(Khan.t("walls.canvas_added", "Item added"));
      await renderCanvasEditor(wall);
    } catch (err) {
      toast(err.message || "add failed", "error");
    }
  }
```

with:

```javascript
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
```

- [ ] **Step 3: Update the caller of `openCanvasMediaPicker`**

The function is invoked from `renderCanvasEditor` (search for it). The current call passes `(wall, mediaList)`. Find:

```javascript
    body.querySelector("#canvas-add-item").addEventListener("click",
      () => openCanvasMediaPicker(wall, mediaList));
```

Replace with:

```javascript
    body.querySelector("#canvas-add-item").addEventListener("click",
      () => openCanvasMediaPicker(wall));
```

The `mediaList` local variable in `renderCanvasEditor` is now unused. Remove it from the `Promise.all` and from the function body. Find:

```javascript
    const [list, mediaList] = await Promise.all([
      api(`/walls/${wall.id}/canvas-playlist`),
      api(`/media`),
    ]);
```

Replace with:

```javascript
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
```

- [ ] **Step 4: Verify JS parses**

```bash
node --check frontend/app.js && echo OK
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "$(cat <<'EOF'
feat(media-picker): wire spanned canvas-playlist add through MediaPicker

Replaces the prompt()-based picker (Phase 2 Task 8) with
MediaPicker.open. Multi-pick and per-item duration override are now
supported on canvas. The dropped /media fetch in renderCanvasEditor
is also gone — picker fetches its own list.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire the picker into the mirrored playlist add

**Files:**
- Modify: `frontend/index.html` (lines 350–353 area)
- Modify: `frontend/app.js` (the `#playlist-add-item` click handler around line 1070)

- [ ] **Step 1: Find the current HTML block**

```bash
grep -n "playlist-select\|playlist-media\|playlist-duration\|playlist-add-item" frontend/index.html
```

Expected: 4 hits in the same playlist-add `<div>`.

- [ ] **Step 2: Update `frontend/index.html`**

Find:

```html
            <select id="playlist-select"><option value="" data-i18n="playlists.select_playlist">Select playlist</option></select>
            <select id="playlist-media"><option value="" data-i18n="playlists.select_media">Select media</option></select>
            <input type="number" id="playlist-duration" placeholder="Duration (s)" data-i18n-placeholder="playlists.duration_placeholder" min="1" max="3600" value="10" />
            <button id="playlist-add-item" type="button" class="save-btn" data-i18n="playlists.add_item">Add Item</button>
```

Replace with:

```html
            <select id="playlist-select"><option value="" data-i18n="playlists.select_playlist">Select playlist</option></select>
            <button id="playlist-add-item" type="button" class="save-btn" data-i18n="playlist.add_media">Add media</button>
```

(Removes `#playlist-media` and `#playlist-duration`; updates the button's i18n key from `playlists.add_item` to `playlist.add_media`.)

- [ ] **Step 3: Update the click handler in `frontend/app.js`**

Find the `#playlist-add-item` click listener (around line 1070):

```javascript
document.getElementById("playlist-add-item").addEventListener("click", async (e) => {
  const playlistId = document.getElementById("playlist-select").value;
  const mediaId    = document.getElementById("playlist-media").value;
  const duration   = Number(document.getElementById("playlist-duration").value || 10);
  if (!playlistId || !mediaId) return;
  await withLoading(e.currentTarget, async () => {
    await api(`/playlists/${playlistId}/items`, { method: "POST", body: JSON.stringify({ media_id: Number(mediaId), duration_seconds: duration }) });
    toast(Khan.t("toast.item_added"), "success");
    await loadPlaylistItems(playlistId);
  });
});
```

Replace with:

```javascript
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
```

- [ ] **Step 4: Search for any remaining references to the removed elements**

```bash
grep -n "playlist-media\|playlist-duration" frontend/app.js frontend/index.html
```

Expected: no matches. If any remain, remove them — they reference DOM elements that no longer exist.

- [ ] **Step 5: Verify JS parses**

```bash
node --check frontend/app.js && echo OK
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "$(cat <<'EOF'
feat(media-picker): wire mirrored playlist add through MediaPicker

Removes <select id=playlist-media> and <input id=playlist-duration>
from index.html. Replaces the click handler with a MediaPicker.open
call that supports image/video/pdf/url. Multi-pick and per-item
duration override are now supported on the mirrored playlist add
flow too.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Picker CSS

**Files:**
- Modify: `frontend/styles.css` (append a new block at the end of the file, before any final `}`).

- [ ] **Step 1: Find the end of the file**

```bash
wc -l frontend/styles.css
```

Note the line number; the new block goes at the very end.

- [ ] **Step 2: Append the picker CSS**

Append to `frontend/styles.css`:

```css
/* ── Media Picker ───────────────────────────────────────────── */

.media-picker-modal .modal-card.media-picker-card {
  width:  min(80vw, 1100px);
  max-height: min(80vh, 720px);
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.media-picker-header {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border, #e9ddc6);
}

.media-picker-header h3 {
  margin: 0;
  font-size: 18px;
}

.media-picker-search {
  padding: 8px 12px;
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 6px;
  font-size: 14px;
}

.media-picker-close {
  font-size: 18px;
  background: transparent;
  border: 0;
  cursor: pointer;
  padding: 4px 8px;
}

.media-picker-chips {
  display: flex;
  gap: 8px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--border, #e9ddc6);
  flex-wrap: wrap;
}

.media-picker-chip {
  border: 1px solid var(--border, #e9ddc6);
  background: transparent;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  cursor: pointer;
}

.media-picker-chip.active {
  background: var(--olive, #6b7a3a);
  color: #fff;
  border-color: var(--olive, #6b7a3a);
}

.media-picker-grid {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  align-content: start;
}

.media-picker-empty {
  grid-column: 1 / -1;
  text-align: center;
  padding: 40px 20px;
  color: var(--muted, #6b6b6b);
}

.media-picker-card {
  position: relative;
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 6px;
  background: #fff;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.1s, box-shadow 0.1s;
}

.media-picker-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
}

.media-picker-card.checked {
  outline: 2px solid var(--olive, #6b7a3a);
  outline-offset: -2px;
}

.media-picker-thumb {
  height: 120px;
  background: #1a1a1a;
  display: grid;
  place-items: center;
  overflow: hidden;
}

.media-picker-thumb > img,
.media-picker-thumb > video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.picker-thumb-pdf,
.picker-thumb-url,
.picker-thumb-other {
  color: #fff;
  font-weight: 600;
  font-size: 14px;
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
}

.picker-thumb-url img {
  width: 32px;
  height: 32px;
}

.media-picker-badge {
  position: absolute;
  inset-inline-start: 6px;
  inset-block-start: 6px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.85);
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--olive, #6b7a3a);
}

.media-picker-card:not(.checked) .media-picker-badge::before {
  content: "";
  width: 14px;
  height: 14px;
  border: 1.5px solid var(--olive, #6b7a3a);
  border-radius: 3px;
}

.media-picker-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  padding: 8px;
  font-size: 12px;
}

.media-picker-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.media-picker-pill {
  background: var(--cream-2, #fff8ee);
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 10px;
  color: var(--muted, #6b6b6b);
}

.media-picker-advanced {
  border-top: 1px solid var(--border, #e9ddc6);
  padding: 8px 18px;
}

.media-picker-advanced-toggle {
  background: transparent;
  border: 0;
  padding: 4px 0;
  font-size: 13px;
  cursor: pointer;
  color: var(--olive, #6b7a3a);
}

.media-picker-advanced-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-block-start: 8px;
  max-height: 140px;
  overflow-y: auto;
}

.media-picker-advanced-row {
  display: grid;
  grid-template-columns: 24px 1fr 100px;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}

.media-picker-advanced-idx {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--cream-2, #fff8ee);
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 12px;
}

.media-picker-advanced-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.media-picker-advanced-duration {
  padding: 4px 8px;
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 4px;
  font-size: 13px;
}

.media-picker-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  border-top: 1px solid var(--border, #e9ddc6);
}

.media-picker-count {
  font-size: 13px;
  color: var(--muted, #6b6b6b);
}

.media-picker-actions {
  display: flex;
  gap: 8px;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/styles.css
git commit -m "$(cat <<'EOF'
feat(media-picker): CSS — modal, chips, thumbnail grid, advanced section

Picker modal sized for thumbnails (~80vw × 80vh), CSS-grid card
layout (auto-fill 160px), filter chips with active state, pick-order
badges using inset-inline so RTL stays in the visual top-start
corner, advanced-section row layout for per-item duration inputs,
hover lift on cards.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final regression sweep + smoke

**No new code.** Verification only.

- [ ] **Step 1: Backend regression**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -3
```

Expected: `150 passed`.

- [ ] **Step 2: i18n parity**

```bash
python3 scripts/check_i18n.py
```

Expected: `i18n OK across frontend, landing, player`.

- [ ] **Step 3: JS sanity-check**

```bash
node --check frontend/app.js && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Rebuild + redeploy frontend**

```bash
docker-compose build frontend && docker-compose up -d --force-recreate frontend
```

Expected: container healthy. Verify:

```bash
sleep 4 && docker-compose ps frontend | tail -3
```

- [ ] **Step 5: Manual smoke (record results in Step 6's commit message OR PR description)**

Open `https://app.khanshoof.com` (admin) in a browser. Tests:

1. **Canvas-add (spanned)**:
   - Walls → spanned wall → Edit → **Add item**. Picker opens. Image, Video, and PDF chips visible (no URL chip).
   - Search a partial name → only matches remain.
   - Pick 3 items in any order → footer reads "3 selected"; Add button reads "Add 3 items"; pick-order badges 1/2/3 visible on cards.
   - Click ▸ Advanced → 3 ordered rows with duration inputs (placeholder "default"). Type 5 in row 2.
   - Click **Add 3 items** → picker closes; canvas item list shows 3 new items in order; row 2's item shows 5s; others show backend defaults.
   - Open picker again, click Cancel → no items added, no toast.
   - Open picker again, click outside the modal-card on the dim backdrop → same as Cancel.
   - Open picker again, press Esc → same as Cancel.

2. **Mirrored-add**:
   - Playlists → pick a playlist → **Add media**. Picker opens with all 5 chips visible (Images, Videos, PDFs, URLs, plus All).
   - Pick 2 items → click Add → playlist receives both.

3. **AR sanity**:
   - Switch admin to Arabic. Repeat steps 1.1–1.4 (canvas-add). Confirm:
     - Title `اختر الوسائط`, search placeholder `ابحث بالاسم…`, chips/buttons translated.
     - Filter chips flow right-to-left.
     - Pick-order badges still in the visual top-start corner of each card (now visually right).
     - Cards still selectable; Advanced section opens; duration inputs accept numbers.

4. **Old-flow regression**:
   - Confirm `<select id=playlist-media>` and `<input id=playlist-duration>` are gone from the DOM:
     ```js
     // In browser console:
     document.getElementById("playlist-media")  // → null
     document.getElementById("playlist-duration")  // → null
     ```

- [ ] **Step 6: Commit a "smoke recorded" marker (optional) + push**

```bash
git push origin feature/media-picker
```

If you want a checkpoint commit recording the smoke results, you can amend the previous Task 6 commit's notes via a small `docs/CHANGELOG.md` entry or skip and put smoke results in the PR body. Recommended: skip — put results in PR body in Task 8.

---

## Task 8: Finish development branch

- [ ] **Step 1: Verification before completion**

Invoke skill: `superpowers:verification-before-completion`. Required evidence:
- `pytest` final count (must be `150 passed`).
- `python3 scripts/check_i18n.py` output (`i18n OK`).
- All 4 manual smoke checkpoints (Task 7 Step 5) passed by eye.

- [ ] **Step 2: Push the branch (if not already)**

```bash
git push origin feature/media-picker
```

- [ ] **Step 3: Open PR media-picker → multi-screen-walls-phase2**

```bash
~/.local/bin/gh pr create \
  --base feature/multi-screen-walls-phase2 \
  --head feature/media-picker \
  --title "feat(media-picker): shared picker modal for canvas + mirrored playlist add" \
  --body "[paste body — see template below]"
```

PR body template:

```markdown
## Summary
- New `MediaPicker` IIFE in `frontend/app.js` — thumbnail grid, multi-select with pick-order, type-allowlist filter, name search, filter chips, optional per-item duration override.
- Wired into the spanned canvas-playlist add flow (replaces Phase 2's `prompt()`).
- Wired into the mirrored playlist add flow (removes the `<select>+<input>` pair from `index.html`).
- One backend tweak: `POST /playlists/{id}/items` accepts missing/null `duration_seconds` and falls back to mime-typed defaults (10s images, video file duration if known else 10s, 30s PDFs, 10s URLs/other).
- 15 new i18n keys EN+AR (MSA), parity-checked.

**Tests:** 150 passing (148 baseline + 2 new playlist-item tests).

**Base:** PRs into `feature/multi-screen-walls-phase2`. Stack chain: media-picker → walls-phase2 → walls-phase1 → main.

## Test plan
- [x] `pytest` — 150 passed
- [x] `python3 scripts/check_i18n.py` — i18n OK
- [x] Manual canvas-add smoke (Task 7 Step 5.1)
- [x] Manual mirrored-add smoke (Task 7 Step 5.2)
- [x] AR sanity (Task 7 Step 5.3)
- [x] Old-flow DOM regression (Task 7 Step 5.4)

## Phase 2.5b deferred
Inline upload from picker, PDF first-page rasterized thumbnails, drag-to-reorder, pagination, bulk delete from picker.
```

- [ ] **Step 4: Update memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_walls_phase2_plan.md` (or create a new `project_media_picker.md`) to add:
- Branch tip SHA + PR URL
- Note that the picker is now the sole add path (no more `prompt()`, no more `<select>+<input>`)
- Phase 2.5b candidates to remember

---

## Self-review

**1. Spec coverage:**

| Spec section | Implementing task |
|---|---|
| Section 1 — Component interface (open/PickedItem/cancel sentinel/single-instance) | Task 3 |
| Section 2 — UI/UX (modal, chips, grid cards, badges, empty states, advanced, footer, RTL, keyboard) | Tasks 3 + 6 |
| Section 3 — Caller A (canvas-playlist add) | Task 4 |
| Section 3 — Caller B (mirrored playlist add) | Task 5 |
| Section 4 — Backend optional `duration_seconds` + mime default | Task 1 |
| Section 5 — 15 i18n keys EN + AR | Task 2 |
| Section 6 — Edge cases (empty/filtered/network/0-selected/Esc/double-open/empty-allowed/video-poster/favicon-404/50+) | Tasks 3 + 6 (network handled in loadMedia, all others in renderGrid/onKeyDown/toggleSelect) |
| Section 7 — Backend tests + manual smoke | Tasks 1 + 7 |
| Section 8 — Out of scope | (Documented; no task) |

No gaps.

**2. Placeholder scan:** No "TBD", "TODO", "implement later" or "fill in details" anywhere. The retry button in `loadMedia` is a real implementation; the `console.warn` for double-open is a real implementation. The browser-console regression check in Task 7 Step 5.4 is a concrete two-line snippet.

**3. Type consistency:**
- `MediaPicker.open({allowedTypes})` → `Promise<PickedItem[]>` consistent in spec Section 1 and Tasks 3 / 4 / 5.
- `PickedItem = { media_id, duration_seconds? }` consistent — Task 4 uses `duration_override_seconds` for canvas (because the canvas endpoint takes that field name), Task 5 uses `duration_seconds` for mirrored. The spec Section 4 explains this asymmetry.
- `classifyMime` returns one of `image|video|pdf|url|other` and is the single source of truth for chip/filter logic in Task 3.
- `state.selection` is a `number[]` (media_ids in pick-order); `state.durations` is `{[media_id: number]: number}` — both used consistently across `toggleSelect`, `confirmPicks`, `renderAdvanced`.

**4. Backwards-compatibility scan:**
- The `playlists.add_item` i18n key becomes orphaned (the button now uses `playlist.add_media`). This is acceptable; orphans don't break parity. (Optional cleanup: remove `playlists.add_item` from both en.json and ar.json — but only if `scripts/check_i18n.py` flags it; otherwise leave alone to keep the diff focused.)
- Existing playlist API consumers that send `duration_seconds: 10` continue to work — the field is now `Optional[int]` with the same `ge=1, le=3600` validators.

No issues found.

---

## Done

When this plan ships green: the `prompt()` and `<select>+<input>` are both gone, replaced by one consistent picker. Phase 2.5b (inline upload, PDF thumbs, drag-reorder) starts when this is merged.
