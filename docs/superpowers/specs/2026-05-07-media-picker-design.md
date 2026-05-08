# Media Picker — Design

**Status:** approved 2026-05-07
**Phase:** 2.5a (first deferred item from Phase 2 to ship)
**Branch base:** `main` (or whatever Phase 2 lands on by then; pick at plan-time)

---

## Section 0 — Why

Two existing media-add flows in admin are crude:

1. **Spanned canvas-playlist add** (`Walls.openCanvasMediaPicker`, shipped in Phase 2 Task 8) uses `window.prompt()` with a numeric media-id list. Discoverable only to power users; no thumbnails; one-at-a-time; not localizable beyond the prompt label.
2. **Mirrored playlist add** (`#playlist-add-item` in `frontend/app.js` ~line 1070) uses a `<select>` of names + a duration `<input>`. No thumbnails, no preview, no bulk add.

Both flows want the same thing: **pick one or more media items from the org's library**. This spec defines a single shared `MediaPicker` component that replaces both.

Goals:
- **Visual recognition** — see thumbnails, not just names
- **Bulk add** — multi-select with pick-order preserved
- **Type-aware** — caller declares allowed kinds; picker enforces
- **i18n + RTL** — EN + MSA parity
- **Cheap** — no new backend endpoints; one optional-field tweak; one new pytest

Non-goals (explicit Phase 2.5b+):
- Inline upload from the picker
- Drag-to-reorder
- PDF first-page rasterized thumbnails
- Pagination / infinite scroll
- Bulk delete from picker

---

## Section 1 — Component interface

Single IIFE namespace `MediaPicker` exposed on `window`. One method:

```js
MediaPicker.open({ allowedTypes }) → Promise<Array<PickedItem>>

PickedItem = {
  media_id:          number,
  duration_seconds?: number   // present only if user opened "Advanced" and set a value
}

allowedTypes: Array<"image"|"video"|"pdf"|"url">  // required, non-empty
```

**Resolution:** array of picks in user's selection order. Empty Add (i.e., 0 selected) is impossible — the Add button is disabled until ≥1 item is checked.

**Rejection:** the promise rejects with a sentinel `{ cancelled: true }` when the user clicks Cancel, presses Esc, or clicks the backdrop. Callers MUST handle rejection without showing an error toast.

**Single-instance:** `open()` while a picker is already mounted resolves with `[]` immediately and logs a `console.warn`. Callers should not need to handle this; it's a defensive no-op.

**Lifecycle:**
1. Mount overlay → fetch `GET /media` → render
2. User interacts (search / filter / select / advanced)
3. User clicks Add → resolve, unmount
4. User clicks Cancel/Esc/backdrop → reject with `{ cancelled: true }`, unmount

`/media` response is cached per-open (re-fetched each `open()`). No long-lived cache — opens are infrequent and a stale list is worse than a 50ms refetch.

---

## Section 2 — UI / UX

**Modal:** centered card, `min(80vw, 1100px)` × `min(80vh, 720px)`, dim backdrop, scrollable body. Mounted as a sibling of `<body>` children, removed on close.

**Header:**
- Title: `media_picker.title`
- Search input (icon-prefixed, right-aligned in LTR / left-aligned in RTL): `media_picker.search_placeholder`
- Close button (✕)

**Filter chip row:**
- One chip per element of `allowedTypes`, plus an "All" chip.
- Chips not in `allowedTypes` do NOT render. (Picker never shows what the caller can't accept.)
- Active chip has filled background; inactive chips have outlined style.
- "All" is the default selection; clicking another chip switches; clicking the active chip switches back to "All".

**Body grid:**
- CSS grid, `repeat(auto-fill, minmax(160px, 1fr))`, gap 12px, 12px padding.
- One card per media item:
  - Card: 160×180px box, rounded 6px, border 1px var(--border).
  - Top: 160×120px thumbnail area, dark background.
    - **image**: `<img src="/uploads/{filename}" loading="lazy" alt="">` with `object-fit: cover`
    - **video**: `<video src="/uploads/{filename}" preload="metadata" muted>` — first frame shows as poster for free; no controls
    - **pdf**: `<div class="picker-thumb-pdf">PDF</div>` — paper-icon CSS background, document name shown below
    - **url**: `<div class="picker-thumb-url">` with favicon (`https://www.google.com/s2/favicons?domain={hostname}&sz=64`) — gracefully falls back to a globe icon on `error`
  - Top-left corner: 24×24px checkbox; when checked, shows pick-order number (1, 2, 3…) instead of a checkmark.
  - Bottom strip: media name (1 line, truncate w/ ellipsis) + mime-type pill (IMG / VID / PDF / URL) right-aligned.
- Click anywhere on the card toggles its checkbox.
- Hovering a card lifts it (subtle `transform: translateY(-2px)` + shadow).

**Empty states:**
- Library empty (`/media` returned `[]`): centered illustration + `media_picker.empty_library` + a button that closes the picker and switches to the Media tab.
- Filtered to nothing (search + chip yield 0): centered text `media_picker.empty_filtered` (no button).

**Advanced section** (collapsed by default):
- Toggle row: `▸ Advanced: set per-item durations` (label `media_picker.advanced_durations`).
- When expanded, renders a small ordered list of currently-checked items (in pick-order), one row each:
  ```
  [1]  Some product image.png    [  10  ] seconds
  [2]  Promo reel.mp4            [  __  ] seconds   (placeholder = "default")
  ```
- Empty input → falls back to backend default. Non-empty → resolves as `duration_seconds`.
- The list re-renders whenever the selection set changes (uncheck removes the row).

**Footer:**
- Left: `{n} selected` count, EN: `{n} selected`, AR: `{n} محدد`.
- Right: Cancel button (`media_picker.cancel`) + Add button (`media_picker.add_n`, with `{n}` interpolated). Add is disabled when n=0.

**RTL:** filter chips flow right-to-left; pick-order badge stays in the visual top-start corner (which is `right` in RTL); search icon moves to the right of its input. The grid layout is direction-agnostic so doesn't need RTL-specific CSS.

**Keyboard:**
- Esc: cancel
- Tab order: search → chips → cards → advanced toggle → cancel → add
- Enter on a card: toggle checkbox
- Enter on the Add button: confirm

---

## Section 3 — Caller wire-up

**Caller A: spanned canvas-playlist add**

Before (Phase 2 Task 8):
```js
async function openCanvasMediaPicker(wall, mediaList) {
  const allowed = mediaList.filter(m => /* image/video/pdf */);
  if (!allowed.length) { toast(…); return; }
  const id = parseInt(prompt(…), 10);
  if (!id || !allowed.find(m => m.id === id)) return;
  // POST single item
  await api(`/walls/${wall.id}/canvas-playlist/items`, {
    method: "POST",
    body: JSON.stringify({media_id: id, position, fit_mode: "fit"}),
  });
}
```

After:
```js
async function openCanvasMediaPicker(wall) {
  let picks;
  try {
    picks = await MediaPicker.open({ allowedTypes: ["image","video","pdf"] });
  } catch (e) {
    if (e?.cancelled) return;
    throw e;
  }
  const list = await api(`/walls/${wall.id}/canvas-playlist`);
  let position = list.items.length;
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
}
```

The `mediaList` parameter is no longer needed — picker fetches `/media` itself.

**Caller B: mirrored playlist add**

Before (`frontend/app.js` ~line 1070, plus the `<select>` + duration `<input>` in `index.html`):
- `#playlist-media` `<select>` populated from `/media`
- `#playlist-duration` `<input type="number">`
- `#playlist-add-item` button → POST one item

After:
- Remove `#playlist-media` and `#playlist-duration` from `frontend/index.html`.
- Replace `#playlist-add-item` button label with `playlist.add_media` ("Add media" / "إضافة وسائط").
- Click handler:
  ```js
  document.getElementById("playlist-add-item").addEventListener("click", async () => {
    const playlistId = document.getElementById("playlist-select").value;
    if (!playlistId) return;
    let picks;
    try {
      picks = await MediaPicker.open({
        allowedTypes: ["image","video","pdf","url"]
      });
    } catch (e) {
      if (e?.cancelled) return;
      throw e;
    }
    for (const p of picks) {
      const body = { media_id: p.media_id };
      if (p.duration_seconds != null) body.duration_seconds = p.duration_seconds;
      await api(`/playlists/${playlistId}/items`, {
        method: "POST",
        body: JSON.stringify(body),
      });
    }
    toast(Khan.t("toast.item_added"));
    await loadPlaylistItems(playlistId);
  });
  ```

---

## Section 4 — Backend changes

Single change: `POST /playlists/{id}/items` accepts missing/null `duration_seconds`.

**Current contract** (Phase 1):
- Request: `{ media_id: int, duration_seconds: int }` (both required)

**New contract:**
- Request: `{ media_id: int, duration_seconds?: int|null }`
- When `duration_seconds` is missing or null, the server picks a default by mime type:
  - `image/*`: 10
  - `video/*`: stored video duration (rounded up) if present in the media row, else 10
  - `application/pdf`: 30 (overridden later if Phase 2.5 ships per-page durations)
  - `text/url`: 10
  - anything else: 10
- All other fields and behavior unchanged.

The canvas-playlist endpoint (`POST /walls/{id}/canvas-playlist/items`) already supports this pattern via `duration_override_seconds` (optional); no change needed there.

**Why the asymmetry between `duration_seconds` (mirrored) and `duration_override_seconds` (canvas):** legacy. Mirrored playlist items store the duration directly on the item row; canvas items inherit a backend-computed default and only set the override when explicitly given. Not worth refactoring as part of this spec.

---

## Section 5 — i18n

10 new keys, all under the `media_picker.` prefix. EN + AR (MSA), checked by `scripts/check_i18n.py` to maintain parity across `frontend/i18n/{en,ar}.json`.

| Key | EN | AR |
|---|---|---|
| `media_picker.title` | Pick media | اختر الوسائط |
| `media_picker.search_placeholder` | Search by name… | ابحث بالاسم… |
| `media_picker.filter_all` | All | الكل |
| `media_picker.filter_images` | Images | الصور |
| `media_picker.filter_videos` | Videos | الفيديوهات |
| `media_picker.filter_pdfs` | PDFs | PDF |
| `media_picker.filter_urls` | URLs | روابط |
| `media_picker.advanced_durations` | Advanced: set per-item durations | متقدم: تعيين مدة لكل عنصر |
| `media_picker.cancel` | Cancel | إلغاء |
| `media_picker.add_n` | Add {n} items | إضافة {n} عناصر |
| `media_picker.selected_n` | {n} selected | {n} محدد |
| `media_picker.empty_library` | No media yet. Upload some in the Media tab. | لا توجد وسائط بعد. ارفع بعضها من تبويب الوسائط. |
| `media_picker.empty_filtered` | No matches. | لا توجد نتائج. |
| `media_picker.fetch_failed` | Couldn't load media. | تعذر تحميل الوسائط. |
| `playlist.add_media` | Add media | إضافة وسائط |

The `playlist.add_media` key replaces the old generic `Add Item` button label.

**Total: 15 new keys.**

---

## Section 6 — Edge cases

| Case | Handling |
|---|---|
| Library empty | Empty-state with `empty_library` + button "Go to Media" — closes picker, switches `view` state to media tab |
| Filtered to 0 | Inline `empty_filtered` text in the grid area; chips and search remain active |
| Network error on `/media` | Toast `media_picker.fetch_failed` + Retry button replaces empty state until success |
| 0 selected | Add button disabled, footer count reads "0 selected" |
| Esc / backdrop / Cancel | Reject with `{cancelled:true}`; callers swallow silently |
| Open while one is open | Resolve immediately with `[]`, log `console.warn`; no second modal mounted |
| Caller passes empty `allowedTypes` | Throw at `open()` call (programmer error, surfaces in dev) |
| Video poster unavailable (transcoding failed) | `<video>` shows its native broken-source state; card remains selectable |
| URL favicon 404 | `onerror` swap to `🌐` glyph |
| User selects 50+ items | No artificial cap; backend-side cap (per-playlist max items, set in Phase 1 schema) is enforced by the POST endpoints — surfaces as a toast on the offending POST |

(`media_picker.fetch_failed` is part of the 15-key set in Section 5.)

---

## Section 7 — Testing

**Backend:** 1 new pytest in `backend/tests/test_playlist_items.py` (file may need creation if it doesn't exist; if not, tack onto whatever currently covers `/playlists/.../items`).

```python
def test_add_item_without_duration_uses_mime_default(client, auth):
    # Create org, playlist, image media via existing fixtures
    media_id = ...  # image upload
    r = client.post(
        f"/playlists/{playlist_id}/items",
        json={"media_id": media_id},  # NO duration_seconds
        headers=auth,
    )
    assert r.status_code == 200
    item = r.json()
    assert item["duration_seconds"] == 10  # image default
```

Optional second test for video default-from-stored-duration; nice-to-have, skip if it complicates fixtures.

**Test count:** 148 → **149+** (149 if one test, 150 if two).

**Frontend:** no automated tests in this repo. Manual smoke checklist (run after deploy):

1. Open admin → Walls → spanned wall → Edit → **Add item**. Picker opens with image/video/PDF cards visible (no URL cards).
2. Search "log" → only matching cards remain. Clear search → all return.
3. Click "Videos" chip → only video cards. Click "All" or "Videos" again → reset.
4. Check 3 cards in any order → pick-order badges show 1/2/3. Footer says "3 selected". Add button enables and reads "Add 3 items".
5. Click ▸ Advanced → ordered list of 3 rows with duration inputs (placeholder "default"). Type 5 in row 2.
6. Click **Add 3 items**. Picker closes; canvas editor list shows 3 new items in order; row 2's item shows 5s.
7. Cancel from a fresh open → picker closes, no toast, no items added.
8. Esc from a fresh open → same as Cancel.
9. Click on backdrop → same as Cancel.
10. Open from mirrored playlist add → URL chip appears + URL items render with favicon.
11. Switch admin to Arabic → re-do steps 1–4. All labels translate; chips RTL-flowed; pick-order badges in top-start corner of each card.
12. Verify the old `<select>` + `<input>` in mirrored playlist add are gone from index.html.

---

## Section 8 — Out of scope (explicit Phase 2.5b+)

- **Inline upload** — drag-drop or Upload button hitting `/media` POST. Adds upload progress + error UI + transcoding-aware status; doubles test surface.
- **PDF first-page rasterized thumbnails** — would need a thumb-size on-demand rasterization endpoint; the canvas-resolution-specific renders are too large to use as thumbs.
- **Drag-to-reorder within picker** — pick-order is sufficient for v1.
- **Pagination / infinite scroll** — only matters at >500 items per org.
- **Bulk delete from picker** — keep destructive ops in the Media tab.
- **Per-page PDF duration array** — listed in Phase 2 spec Section 9; depends on Phase 2.5 PDF work.
- **Recently-used / favorite filter** — premature.

---

## Section 9 — Spec self-review

**Placeholder scan:** None. Every endpoint, key, and behavior has a concrete value.

**Internal consistency:**
- `allowedTypes` enum used in Section 1 matches the chip set in Section 2 and the caller examples in Section 3.
- `duration_seconds` (mirrored) vs `duration_override_seconds` (canvas) asymmetry is acknowledged in Section 4 and consistently applied in Section 3.
- The 15-key total in Section 5 (after Section 6 added `fetch_failed`) is the same set referenced in Sections 2 and 6.

**Scope check:** One picker, two callers, one tiny backend tweak, 15 i18n keys, 1 pytest. Single implementation plan.

**Ambiguity check:**
- Cancel reject vs empty-resolve — explicit: Cancel rejects with `{cancelled:true}`; double-open resolves with `[]`.
- "Pick-order" is the user's check sequence, not list position — explicit in Section 1 ("in user's selection order").
- Default duration for video without stored duration: 10s (Section 4).

No ambiguities found that would change behavior.

---

## Section 10 — Done definition

When this ships green:
- Both add-flows go through `MediaPicker`.
- The mirrored `<select>` + `<input>` are removed from `index.html`.
- 149+ backend tests passing.
- i18n parity OK.
- Manual smoke (Section 7) all pass in EN and AR.

Phase 2.5b candidates (in priority order, for a future spec):
1. Inline upload from picker
2. PDF first-page thumbnails
3. Per-page PDF duration array
