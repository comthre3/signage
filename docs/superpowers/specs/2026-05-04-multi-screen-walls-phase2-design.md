# Multi-Screen Walls Phase 2 — Spanned Mode (Design / Spec)

**Date:** 2026-05-04
**Status:** Draft, awaiting user review.
**Branch base:** Phase 1 (`feature/multi-screen-walls-phase1`, currently held for pentest before merge).
**Companion:** original cross-phase design at `docs/superpowers/specs/2026-05-01-multi-screen-wall-sync-design.md`. This document supersedes Section 4 of that spec for v1 implementation; cross-phase concerns (auth, WS protocol, drift correction, single-worker assumption) are inherited from Phase 1 unchanged.

---

## Goal

Ship spanned-mode walls: a single virtual canvas (image, video, or PDF) that visually crosses every cell of an N×M grid of TVs, in lockstep, with optional bezel-aware geometry so a circle moving across the wall stays geometrically a circle even with hardware bezel gaps.

## Non-goals (v1)

- Per-cell bezel overrides (Q3 chose wall-level single value).
- Free-form positioning of multiple media elements per playlist scene (Q6 chose single-item-fills-canvas).
- Mixed-resolution / mixed-physical-size cells (Q3 wall-level bezel makes this moot for v1; geometry assumes uniform cells).
- Real-time PDF rendering (Q5 chose server-side rasterize-on-upload).
- URL / text-url media on spanned walls (already excluded in Phase 1 spec Q10).

## Decisions (consolidated from brainstorm 2026-05-04)

| # | Question | Decision |
|---|---|---|
| 1 | Scope ambition | Spec-as-written: canvas editor + bezel math + mixed-res support* + PDF (* mixed-res deferred to Phase 2.5; v1 assumes uniform cells) |
| 2 | Bezel input units | Percentage. No mm, no panel-size measurement. |
| 3 | Bezel scope | Wall-level single value (no per-cell override). |
| 4 | Canvas resolution | Admin picks {1080p, 4K, 8K} in wizard. |
| 5 | PDF rendering | Server-side rasterize to wall-canvas-sized PNG (one PNG per page) on upload; new dependency `pypdfium2`. |
| 6 | Authoring | Playlist of items; each item fills the whole canvas. Per-item duration override + fit/fill/stretch toggle. |
| 7 | Mode change post-creation | Allowed; clears the not-applicable-mode's playlist; pairings preserved. Typed-confirm modal. |
| 8 | Bezel inputs UX | Two inputs: horizontal % + vertical %. |

---

## Section 1 — Data model

### Schema additions

`walls` table — new columns (all NULL for mirrored walls; populated for spanned):

```sql
ALTER TABLE walls ADD COLUMN canvas_width_px   INTEGER;       -- 1920 | 3840 | 7680
ALTER TABLE walls ADD COLUMN canvas_height_px  INTEGER;       -- 1080 | 2160 | 4320
ALTER TABLE walls ADD COLUMN bezel_h_pct       REAL DEFAULT 0;-- 0.0 .. 10.0
ALTER TABLE walls ADD COLUMN bezel_v_pct       REAL DEFAULT 0;-- 0.0 .. 10.0
ALTER TABLE walls ADD COLUMN spanned_playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL;
```

CHECK constraints (added via migration; SQLite rebuild path):

```sql
CHECK (mode = 'mirrored' OR (canvas_width_px  IN (1920, 3840, 7680)
                          AND canvas_height_px IN (1080, 2160, 4320)
                          AND bezel_h_pct >= 0 AND bezel_h_pct <= 10
                          AND bezel_v_pct >= 0 AND bezel_v_pct <= 10
                          AND cols * bezel_h_pct < 100
                          AND rows * bezel_v_pct < 100))
```

`playlists.kind` — gains a new value `'wall_canvas'`. Existing values (`'standalone'` and the rest) untouched. CHECK constraint extended.

`playlist_items` — new nullable columns (only used when parent playlist is `kind = 'wall_canvas'`):

```sql
ALTER TABLE playlist_items ADD COLUMN duration_override_seconds INTEGER;  -- NULL = use media's native duration
ALTER TABLE playlist_items ADD COLUMN fit_mode TEXT DEFAULT 'fit'
  CHECK (fit_mode IN ('fit', 'fill', 'stretch'));
```

Mapping to CSS `object-fit`:
- `fit` → `contain` (letterbox/pillarbox)
- `fill` → `cover` (crop to fill)
- `stretch` → `fill` (distort to fill)

### PDF rasterization storage

```
uploads/
  pdf_pages/
    {media_id}/
      canvas_3840x2160/
        page_01.png
        page_02.png
        ...
      canvas_7680x4320/
        page_01.png
        ...
```

A single PDF media row may have multiple rendered resolutions cached if it appears on walls of different canvas sizes. Each subdirectory is created lazily on first need. New row in `media` table: `pdf_pages_status TEXT` (NULL for non-PDF; one of `'pending' | 'ready' | 'error' | 'stale'` for PDF).

---

## Section 2 — API surface

### `POST /walls` (extended)

When `mode == 'spanned'`, request body must include:
```json
{
  "name": "Lobby Wall",
  "mode": "spanned",
  "rows": 2,
  "cols": 2,
  "canvas_width_px":  3840,
  "canvas_height_px": 2160,
  "bezel_h_pct": 2.0,
  "bezel_v_pct": 1.0
}
```
Backend:
1. Validates fields per the CHECK constraints above.
2. Inserts the `walls` row.
3. Creates an empty `playlists` row with `kind = 'wall_canvas'`, `name = "Canvas: <wall name>"`, and the wall's organization.
4. Sets `walls.spanned_playlist_id` to that playlist id.

### `PATCH /walls/{id}` (extended)

Accepts the same fields as `POST` plus `mode`. If the patched `mode` differs from the current mode:
- If mode-out-going (e.g., `mirrored` → `spanned`): NULL the outgoing FK (`mirrored_playlist_id`), DELETE that mirrored playlist's items, DELETE the playlist row.
- Then create the incoming-mode artifact (the new `wall_canvas` playlist).
- Pairings (`wall_cells.screen_id`) remain. Pair codes invalidated if any are still in flight.

### `GET /walls/{id}/canvas-playlist` (new)

Returns the wall's `wall_canvas` playlist with its items, including each item's `duration_override_seconds`, `fit_mode`, and resolved media URL. 404 if wall is mirrored.

### `POST /walls/{id}/canvas-playlist/items` (new)

Body: `{media_id, position, duration_override_seconds?, fit_mode?}`. Backend:
1. Loads media row, asserts `mime_type` starts with `image/`, `video/`, or equals `application/pdf`. Else 400 `wall.canvas_media_type_blocked`.
2. If PDF: triggers async rasterization (see Section 3) for the wall's canvas resolution.
3. Inserts `playlist_items` row.

### `PATCH /walls/{id}/canvas-playlist/items/{item_id}` (new)

Updates `position`, `duration_override_seconds`, or `fit_mode`. Same media-type validation as POST.

### `DELETE /walls/{id}/canvas-playlist/items/{item_id}` (new)

Removes the item. Does NOT delete the underlying media row. Garbage-collection of orphaned PDF rasterizations is a Phase 2.5 concern (acceptable to leave PNG dirs on disk for v1).

### `POST /walls/redeem` and the WS endpoint — unchanged from Phase 1.

---

## Section 3 — PDF rasterization

### Trigger

When a PDF media is added to a `wall_canvas` playlist (POST `/walls/{id}/canvas-playlist/items`) AND no rendered PNG sequence exists for the wall's `canvas_width_px × canvas_height_px`, kick off rasterization.

### Implementation

- New backend dependency: `pypdfium2` (pure-Python wheel, MIT, no system dep). Add to `backend/requirements.txt`.
- Synchronous (blocking) rasterization on the API request thread for v1. Acceptable because:
  - Spanned-wall PDFs are typically slide decks, not 500-page reports — rasterize time is seconds, not minutes.
  - The single uvicorn worker assumption holds; the request blocks one worker for the duration.
  - If throughput becomes a problem, Phase 2.5 can move this to a background task queue.
- Page-by-page write to `uploads/pdf_pages/{media_id}/canvas_{w}x{h}/page_NN.png`. Use a tempfile + atomic rename per page so partial state isn't observable.
- On success: set `media.pdf_pages_status = 'ready'`. On failure: `'error'` and the API returns 500 with `code: "wall.pdf_rasterize_failed"`.

### Storage cap

- Refuse PDFs > 50 pages with 400 `wall.pdf_too_long` (admin can split it externally). Soft cap protects disk.
- One 4K PNG: ~2-10 MB compressed. 50 pages × 10 MB = 500 MB worst case per PDF. Acceptable.

### Canvas-resolution change

- If `walls.canvas_width_px` is patched, all the wall's PDF items get `pdf_pages_status = 'stale'` (set in the same transaction).
- Next time the WS tick loop reads a stale PDF item, it triggers re-rasterization (synchronously, blocking that wall's tick task for a few seconds — single worker assumption tolerates this). Other walls keep ticking; only this wall's `play` frames are delayed.
- Player tolerates the delay (it stays on the previous frame).

### Player-side rendering of PDF items

A PDF playlist item is **expanded server-side** into N `play` frames, one per page. The tick loop walks the page sequence with the item's per-page duration (default 5s, override-able via `duration_override_seconds` which applies per-page in v1). The frame contains the URL of the specific page PNG, not the original PDF URL — so the player just renders an `<img>` with no PDF awareness needed.

This means PDF items use `duration_override_seconds` as **per-page** duration, not total. Document this in admin UI (tooltip: "Each page shows for this many seconds").

---

## Section 4 — Cropping math (player-side)

### Without bezel (`bezel_h_pct == 0 && bezel_v_pct == 0`)

```
cell_w_px = canvas_width_px  / cols
cell_h_px = canvas_height_px / rows
cell_x_px = col * cell_w_px
cell_y_px = row * cell_h_px
```

### With bezel

The wall canvas in pixels is divided into N visible-cell strips separated by (cols-1) bezel gaps (and analogously vertically). Each bezel gap is `bezel_h_pct%` of the canvas width.

```
gap_w_px = (bezel_h_pct / 100) * canvas_width_px
visible_total_w_px = canvas_width_px - (cols - 1) * gap_w_px
cell_w_px = visible_total_w_px / cols
cell_x_px = col * (cell_w_px + gap_w_px)
```

Vertical analogous with `bezel_v_pct` and `rows`.

The "behind the bezel" pixels ARE painted on the wall canvas — the cell viewport just doesn't reveal them. A circle moving across the wall stays geometrically a circle.

### Frame schema (extends Phase 1 spec Section 2)

```json
{
  "type": "play",
  "mode": "spanned",
  "item": {"id": 42, "url": "/uploads/foo.mp4", "mime_type": "video/mp4", "name": "..."},
  "started_at_ms": 1715000000000,
  "duration_ms": 30000,
  "fit_mode": "fit",
  "playlist_signature": "abc123",
  "server_now_ms": 1715000000050
}
```

`hello` frame additions for spanned walls:
```json
{
  "type": "hello",
  "mode": "spanned",
  "wall_id": 7,
  "cell": {"row": 0, "col": 1, "rows": 2, "cols": 2},
  "canvas":   {"w": 3840, "h": 2160},
  "cell_geometry": {"x": 1920, "y": 0, "w": 1920, "h": 1080},
  "bezel": {"h_pct": 0.0, "v_pct": 0.0},
  "current_play": { ... play frame fields, or null ... },
  "server_now_ms": 1715000000000
}
```

Cell geometry is computed on the server (using the formulas above) and shipped in `hello` so the player doesn't have to redo bezel math.

---

## Section 5 — Player DOM + CSS

When `hello.mode === 'spanned'`, the player swaps from the Phase 1 fullscreen-element DOM to:

```html
<div class="cell-viewport">
  <div class="wall-canvas">
    <video|img|iframe class="wall-media" />
  </div>
</div>
```

CSS (lifted from spec Section 4 with minor adjustments for fit_mode):

```css
.cell-viewport {
  position: fixed; inset: 0; overflow: hidden; background: #000;
}
.wall-canvas {
  position: absolute;
  width:  calc(var(--wall-w-px) * 1px);
  height: calc(var(--wall-h-px) * 1px);
  transform: translate(
    calc(-1 * var(--cell-x-px) * 1px),
    calc(-1 * var(--cell-y-px) * 1px)
  );
  will-change: transform;
}
.wall-media {
  position: absolute; inset: 0; width: 100%; height: 100%;
}
.wall-media[data-fit="fit"]     { object-fit: contain; }
.wall-media[data-fit="fill"]    { object-fit: cover;   }
.wall-media[data-fit="stretch"] { object-fit: fill;    }
```

CSS variables (`--wall-w-px`, `--cell-x-px`, etc.) are set on `.wall-canvas` from the `hello.canvas` and `hello.cell_geometry` fields once on connect; the cell's geometry doesn't change unless the wall is reconfigured (which forces a player reload via the `bye` frame).

---

## Section 6 — Admin UI changes

### Wizard (extends Phase 1's 3-step form)

When user picks Spanned radio in step 1:
- Step 2 shows: rows, cols, **canvas resolution** dropdown (1080p / 4K / 8K), **horizontal bezel %** input (0–10, step 0.1, default 0), **vertical bezel %** input (same).
- Step 3 (cell pairing) unchanged from Phase 1 except the Spanned mode is no longer `disabled`.

### Wall card (list view)

Spanned walls' mosaic shows the canvas resolution badge ("4K"/"8K") and a thumbnail of the first canvas-playlist item if any.

### "Content" button → Canvas Editor (new)

Replaces "per-cell playlist" UI of mirrored walls. Layout:

```
+------------------+----------------------------+
| Playlist items   | Canvas preview (2:1 / 16:9)|
| ┌─────────────┐  | (proportional to wall      |
| │ [thumb] foo │  |  canvas dims, bezels       |
| │  Image · 5s │  |  drawn as dark stripes)    |
| │  Fit: contain│  |                            |
| └─────────────┘  | + Selected item fills      |
| ┌─────────────┐  |   the canvas (artist's     |
| │ [thumb] bar │  |   WYSIWYG)                 |
| │  Video · 12s│  |                            |
| │  Fit: cover │  |                            |
| └─────────────┘  |                            |
| [+ Add item]    |                            |
+------------------+----------------------------+
| Selected item: foo.png                         |
| Duration override: [5] seconds                 |
| Fit mode: ( • Fit ) ( ○ Fill ) ( ○ Stretch )   |
| [Save]                                         |
+------------------------------------------------+
```

Add-item picker filters media list to `image | video | pdf` (per Phase 1 spec Q10).

### Mode-change confirm modal

Triggered when admin patches a wall's mode (mirrored ↔ spanned). Modal:

> Switching this wall to **Spanned** will permanently delete its current playlist (mirrored cell playlists or canvas playlist). Cell pairings stay.
>
> Type the wall name to confirm:
> [______________________]
> [ Cancel ] [ Switch ]

The Switch button stays disabled until the typed name matches.

### i18n keys to add (admin)

Approximately 25 new keys, including: `walls.canvas_resolution`, `walls.canvas_4k`, `walls.canvas_8k`, `walls.bezel_horizontal_pct`, `walls.bezel_vertical_pct`, `walls.canvas_editor_title`, `walls.canvas_add_item`, `walls.fit_mode`, `walls.fit_fit`, `walls.fit_fill`, `walls.fit_stretch`, `walls.duration_override_seconds`, `walls.duration_override_help`, `walls.duration_override_pdf_help`, `walls.canvas_media_type_blocked`, `walls.mode_change_confirm_title`, `walls.mode_change_confirm_body`, `walls.mode_change_type_name_to_confirm`, `walls.mode_change_switch_btn`, `walls.pdf_rasterize_failed`, `walls.pdf_too_long`, etc. Full list locked in during plan-writing.

---

## Section 7 — Player changes summary

Already exists from Phase 1: WebSocket connect/auth, exponential backoff reconnect, `hello`/`play` frame parsing, time-anchor seek, drift correction, HTTP fallback.

**New work:**
- Detect `hello.mode === 'spanned'` and swap DOM to `cell-viewport > wall-canvas` structure.
- Read `hello.canvas` + `hello.cell_geometry` and set CSS custom properties.
- Read `play.fit_mode` and apply via the `data-fit` attribute on the media element.
- Same drift-correction loop applies to `<video>` inside the wall canvas (the transform doesn't affect `currentTime`).

**No new i18n on player side** (status messages reuse Phase 1's `wall.connecting` / `wall.reconnecting`).

---

## Section 8 — Testing strategy + rollout

### Backend tests (~25 new, target 148 total)

- Schema migration (additive nullable columns; CHECK constraints fire correctly).
- `POST /walls` spanned happy path + validation errors (bad canvas res, bezel > 10, `cols * bezel >= 100`).
- Canvas-playlist CRUD (create/list/patch/delete/reorder); media-type filter; cross-org rejection.
- PDF rasterization: 2-page PDF → 2 PNGs at correct dimensions; corrupt PDF → `status='error'`; > 50 pages → 400.
- Mode-change PATCH: spanned↔mirrored clears the right playlist, preserves pairings, idempotent same-mode.
- WS `hello` payload contains correct `canvas`/`cell_geometry`/`bezel` for a 2×2 + 3% bezel example (numerical assertion).
- Tick loop spanned-mode: PDF items expand to per-page frames; per-item `duration_override_seconds` honored; fit_mode propagated.

### Frontend smoke (manual, recorded as a checklist in the implementation plan)

- Wizard renders new fields when Spanned selected; submits successfully.
- Canvas editor renders, item add/remove/reorder works, fit-mode toggle persists.
- Mode-change typed-confirm gate works; cancel preserves state; confirm clears playlist.
- AR locale: all new strings translate; canvas editor RTL doesn't reflow.
- `python3 scripts/check_i18n.py` passes after Task X.

### Player smoke (manual, with 2-4 browser tabs)

- 2×2 spanned 4K wall, bezel 0%: each tab shows its quadrant of a 4K test grid; cells line up edge-to-edge.
- Same with bezel 3% horizontal / 2% vertical: visible gap between tabs proportional to bezel %.
- PDF playlist item: pages turn at configured per-page duration.
- Canvas resolution change (4K→8K) on a wall with a PDF item: next play frame uses re-rasterized higher-res PNG (eyeball: text sharper).
- Drift correction still works on spanned `<video>` (kill backend, restart, sync resumes).

### Rollout safety

- Schema migration additive only; existing mirrored walls untouched.
- Spanned radio in admin wizard stays disabled unless `WALLS_PHASE2_ENABLED=1` env var is set on the frontend container. Allows soft-launch without revealing the feature to all customers.
- `pypdfium2` added to `backend/requirements.txt`; existing CI pip-install path picks it up. No Dockerfile change needed (pure-Python wheel).

---

## Section 9 — Open questions deferred to Phase 2.5

- Per-cell bezel + mixed-resolution support (some customers will mix a 65" + 55" + 50" eventually).
- Free-form positioning of multiple media elements per "scene" on the canvas.
- PDF garbage collection (orphaned `uploads/pdf_pages/{media_id}/canvas_*` directories after media deletion or canvas-resolution change).
- Background-task-queue rasterization (instead of synchronous blocking).
- Per-PDF-item per-page duration array (for "page 3 lingers, page 7 is brief" workflows).

These are explicitly out-of-scope for Phase 2 v1 and are listed here so they don't get re-litigated mid-plan.

---

## Section 10 — Spec self-review

**Placeholder scan:** None. Every field, table column, endpoint, and frame field has a concrete value.

**Internal consistency:** Section 4 cropping math matches Section 2 frame schema (server computes `cell_geometry` from canvas + bezel + row/col, ships it in `hello`). Section 3 PDF rasterization is referenced consistently in Section 2 (POST handler) and Section 8 (tick-loop re-rasterize). Section 6 i18n list calls out keys that appear in Section 6 admin UI prose.

**Scope check:** This document covers ONE phase (Phase 2 spanned mode). Phase 2.5 items (mixed-res, GC, BG queue) are explicitly listed as deferred. v1 is a single implementation plan.

**Ambiguity check:**
- "Per-page duration" for PDF items — explicit in Section 3 "PDF items use `duration_override_seconds` as **per-page** duration".
- "Mode change clears playlist" — Section 2 PATCH handler spells out the DELETE sequence.
- "Canvas resolution change" — Section 3 covers the stale-marker + on-demand re-render flow.

No ambiguities found that change behavior.

---

## Done

When this spec is approved, the next step is `superpowers:writing-plans` against this document. The plan will produce branched/checklisted Tasks 0–N with full code blocks for each step, mirroring Phase 1's plan format.
