# Multi-Screen Wall Sync — Design Spec

**Date:** 2026-05-01
**Branch context:** to be developed on a fresh branch off `main` after `feature/security-hardening` lands. Must not break any existing screen / pairing / playback behavior.
**Phasing:** two phases, two implementation plans. Phase 1 ships first; Phase 2 is built and reviewed only after Phase 1 is in production.

---

## Goal

Let an admin link multiple physical screens at one venue so they behave as one logical surface. Two modes:

1. **Mirrored wall** — every screen plays content in time-locked synchronization. Either the same playlist on every screen ("same playlist") or different playlists per cell that advance items in lockstep ("synced rotation"). Use case: menu boards across a restaurant.
2. **Spanned wall** — content is authored on a single large canvas and each physical screen displays its slice. Motion crosses physical seams. Optional bezel compensation makes geometry look correct across the gaps. Use case: hero/feature videowalls.

Both modes use the same underlying time-sync infrastructure. Mirrored ships in Phase 1; spanned in Phase 2.

## Non-goals

- No NTP daemon on the player TVs.
- No WebRTC, no media-streaming server. TVs continue fetching media files via HTTP from existing nginx.
- No multi-process backend coordination. v1 assumes single uvicorn worker (current production state). Documented; future Redis pub/sub layer is the migration path when we scale workers.
- No tier-based plan gating in v1. Walls are unlocked for the first 300 customers as an early-adopter perk; an org-row feature flag (`walls_enabled`) lets us flip to gating later without code changes.
- No automatic reflow if a screen drops. Surviving screens keep playing; the dead cell's last frame stays frozen on its TV.

---

## Decisions captured during brainstorming

| # | Question | Decision |
|---|---|---|
| Q1 | Sync scope | C — both spatial split (spanned) and time-sync (mirrored) |
| Q2 | Topology | B — grid M×N (rows × cols), each cell at (row, col) |
| Q3 | Bezel compensation | C — default off, optional per wall |
| Q4 | Sync precision | C — WebSocket fanout, ~10–50ms drift on LAN |
| Q5 | Authoring | B (clarified) — spanned uses single-canvas editor; mirrored uses per-screen / per-playlist authoring |
| Q6 | Failure handling | C — surviving screens keep playing; dead slot shows last frame; admin sees offline indicator |
| Q7 | Pair into cell | B — admin clicks empty cell, code is generated bound to that cell, admin types it on the target TV |
| Q8 | Plan gating | A — no gating for first 300 customers; org feature flag for future |
| Q9 | Bezel units | A — bezel mm + screen size in inches per cell; system computes pixel offsets |
| Q10 | Spanned media types | D — images / videos / PDFs allowed; URL/text-url media blocked from spanned walls with a friendly UI message |
| Q11 | Mirrored sub-mode | C — admin picks: "same playlist on all screens" (default) or "different playlist per cell, synced rotation" |

---

## Naming

- **Wall** — customer-facing word. UI label, translation key namespace `wall.*`.
- A wall has a **mode**: `spanned` or `mirrored`.
- A wall has a **grid**: `rows` × `cols`. Each cell is `(row, col)`, zero-indexed.
- Mirrored sub-modes: `same_playlist` and `synced_rotation`.

---

## Section 1 — Data model

All schema changes are additive. Existing tables and rows are untouched.

### New tables

```sql
CREATE TABLE walls (
  id                   SERIAL PRIMARY KEY,
  organization_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name                 TEXT NOT NULL,
  mode                 TEXT NOT NULL CHECK (mode IN ('spanned','mirrored')),
  rows                 INTEGER NOT NULL CHECK (rows BETWEEN 1 AND 8),
  cols                 INTEGER NOT NULL CHECK (cols BETWEEN 1 AND 8),
  -- spanned-only fields (NULL for mirrored)
  canvas_width_px      INTEGER,
  canvas_height_px     INTEGER,
  bezel_enabled        BOOLEAN NOT NULL DEFAULT false,
  spanned_playlist_id  INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
  -- mirrored-only fields (NULL for spanned)
  mirrored_mode        TEXT CHECK (mirrored_mode IN ('same_playlist','synced_rotation')),
  mirrored_playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL, -- only used when mirrored_mode='same_playlist'
  -- shared
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);
CREATE INDEX idx_walls_org ON walls(organization_id);

CREATE TABLE wall_cells (
  id                 SERIAL PRIMARY KEY,
  wall_id            INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
  row_index          INTEGER NOT NULL,
  col_index          INTEGER NOT NULL,
  screen_id          INTEGER REFERENCES screens(id) ON DELETE SET NULL,
  -- physical dimensions for bezel math (Q9)
  screen_size_inches NUMERIC(4,1),
  bezel_top_mm       NUMERIC(5,2),
  bezel_right_mm     NUMERIC(5,2),
  bezel_bottom_mm    NUMERIC(5,2),
  bezel_left_mm      NUMERIC(5,2),
  -- per-cell playlist for synced_rotation mirrored walls
  playlist_id        INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
  created_at         TEXT NOT NULL,
  UNIQUE (wall_id, row_index, col_index)
);
CREATE INDEX idx_wall_cells_wall ON wall_cells(wall_id);
CREATE INDEX idx_wall_cells_screen ON wall_cells(screen_id);

CREATE TABLE wall_pairing_codes (
  id           SERIAL PRIMARY KEY,
  code         TEXT NOT NULL UNIQUE,
  device_id    TEXT,
  wall_id      INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
  row_index    INTEGER NOT NULL,
  col_index    INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','claimed','expired')),
  expires_at   TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  claimed_at   TEXT
);
CREATE INDEX idx_wall_pairing_codes_wall ON wall_pairing_codes(wall_id);
```

### Additions to existing tables

```sql
ALTER TABLE screens
  ADD COLUMN IF NOT EXISTS wall_cell_id INTEGER REFERENCES wall_cells(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_screens_wall_cell ON screens(wall_cell_id);

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS walls_enabled BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE playlists
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'standard'
    CHECK (kind IN ('standard','wall_canvas'));
```

A `screens` row with `wall_cell_id IS NULL` behaves exactly as today. Zero behavior change for non-wall screens.

The `playlists.kind = 'wall_canvas'` rows back the spanned-mode canvas editor. A `wall_canvas` playlist's `playlist_items` are filtered to `image | video | pdf` mime types only; the editor enforces this and the `POST /playlists/{id}/items` endpoint validates.

---

## Section 2 — Sync architecture

The backend is the conductor; TVs are the orchestra.

### WebSocket endpoint

```
WS  /walls/{wall_id}/ws?screen_token=<token>
```

- FastAPI native WebSocket. Auth: `screen_token` validated against `screens.token` and confirmed to match a `wall_cells.screen_id` whose `wall_id` matches the URL.
- Server keeps an in-process registry: `wall_id → { (row, col) → websocket }`.
- On connect, server sends `hello` with current playback state. On disconnect, server marks `screens.last_seen` and broadcasts a `cell_state` frame to siblings (consumed by the admin mosaic UI, not by sibling TVs).

### Authoritative tick loop (one asyncio task per active wall)

- Built lazily on first connect; torn down when last cell disconnects.
- Owns the canonical timeline.
- For mirrored/`same_playlist` and spanned: one shared timeline `[(item_id, started_at_ms, duration_ms), ...]` derived from the wall's playlist.
- For mirrored/`synced_rotation`: one timeline per cell, but item-advance ticks fire at the same `started_at_ms` across all cells (lockstep math below).
- Tick fires only at item boundaries (typically every 5–30s). Between ticks, screens compute their own playhead from the server-time anchor.

### Server-time-anchor protocol

Every WebSocket frame from server carries `server_now_ms`. Client maintains a rolling clock-offset estimate:

```
client_offset_ms = server_now_ms − client_received_ms − rtt_estimate / 2
```

RTT estimated from periodic `ping/pong` (every 30s). Client computes:

```
effective_now = Date.now() + client_offset_ms
expected_position_ms = effective_now − started_at_ms
```

For video items, catch-up rule:
- `Math.abs(video.currentTime * 1000 − expected_position_ms) > 200` → hard seek (`video.currentTime = expected_position_ms / 1000`).
- 50–200ms delta → `video.playbackRate = 1.02` for 1s then restore (smooths small drifts without visible jumps).
- <50ms → leave alone.

Images / PDFs: swap on `play` frame; no playhead.

### Frame schema (JSON)

Server → client:

```json
{ "type": "hello",
  "wall_id": 7,
  "mode": "spanned",
  "cell": {"row": 0, "col": 1, "rows": 2, "cols": 2},
  "canvas": {"w": 3840, "h": 2160},   // spanned only
  "cell_geometry": {"x": 1920, "y": 0, "w": 1920, "h": 1080}, // spanned only
  "bezel": {...} | null,                                       // spanned only
  "current_play": { ... same shape as a "play" frame ... },    // null if no item
  "server_now_ms": 1735000000000 }

{ "type": "play",
  "item": { "id": 42, "url": "/uploads/...", "mime_type": "video/mp4", "name": "..." },
  "started_at_ms": 1735000123456,
  "duration_ms": 8000,
  "playlist_signature": "sha256:...",
  "server_now_ms": 1735000123500 }

{ "type": "playlist_change", "playlist_signature": "sha256:..." }
   // tells client to re-fetch its playlist via existing HTTP endpoint, then keep listening for play frames

{ "type": "cell_state", "row": 0, "col": 1, "online": false }
   // for sibling-status awareness; player ignores it (admin UI consumes)

{ "type": "ping", "server_now_ms": 1735000200000 }

{ "type": "bye", "reason": "wall_disabled" | "cell_unpaired" | "wall_deleted" }
```

Client → server:

```json
{ "type": "pong", "client_received_ms": ..., "client_now_ms": ... }
{ "type": "ready" }   // sent after media loadeddata/load fires; server uses to track per-cell readiness for admin UI
```

### Lockstep math (synced_rotation)

To keep the math simple and the failure mode obvious for admins:

- **Synced-rotation walls require all cell playlists to have the same item count.** Save endpoint validates and returns 422 otherwise. UI surfaces this in the wall editor with a clear message ("All cells in a synced-rotation wall must have the same number of items. Cell (0,1) has 3, cell (0,2) has 5.").
- Each cell's per-item duration may differ. The tick loop walks index `i` for all cells together; the boundary for advancing to `i+1` is the **maximum** of the cells' `items[i].duration` (so the slowest cell in any given slot drives the wall's tempo). Cells that finish their item early stay frozen on the last frame until the slot ends.

This is the simplest semantics that matches admin intent. We can revisit (LCM-based slot scheduling, etc.) once we see what customers actually do.

### Reconnect & catch-up

- WebSocket reconnect uses exponential backoff: 1s, 2s, 4s, 8s, capped at 30s.
- On reconnect, server's `hello` frame includes `current_play`. Client renders + seeks immediately.
- HTTP polling kept as a fallback at 60s interval (longer than today's 15s for non-wall screens). If WS is down for >30s, the polling loop drives playback from `/screens/{token}/content` exactly like today's standalone behavior. WS-up state suppresses the polling driver.

### Single-process assumption

The asyncio tick-loop registry lives in process memory. Production runs one uvicorn worker (verified). If we ever scale workers, every wall's WS connections must land on the same worker, OR we add Redis pub/sub between workers. Out of scope for v1; documented in `walls.py` docstring + `PROJECT_SCOPE.md`.

---

## Section 3 — Admin UI: wall editor + pair-into-cell flow

### Walls tab

New top-level "Walls" entry in admin sidebar. Hidden if `organizations.walls_enabled = false`.

### Walls list page (`/walls`)

- Card grid. Each card: name, mode badge ("Spanned" / "Mirrored"), grid (e.g., "2×2"), online cells (e.g., "3/4 online"), small live mosaic thumbnail, Edit / Delete buttons.
- "Create Wall" button opens the wizard.

### Create-wall wizard (3 steps)

**Step 1 — Basics**
- Name (text).
- Mode (radio: Spanned / Mirrored).
- Grid: rows + cols pickers (1–8), with a live proportional preview.

**Step 2 — Mode-specific config**

*Spanned:*
- Bezel compensation toggle (default off). If on: per-cell inputs for screen size (inches) + 4 bezel measurements (mm). Helper: "All cells use the same TV?" → enter once, copies to all.
- Canvas resolution: derived (default `cols × 1920` × `rows × 1080`); admin can override per cell later.

*Mirrored:*
- Sub-mode radio: "Same playlist on all screens" (default) or "Different playlist per cell, synchronized rotation."
- If "same playlist": single playlist picker.
- If "synced rotation": empty here; per-cell playlist set in step 3.

**Step 3 — Cells**
- Visual proportional grid. Each cell:
  - Unpaired: dashed outline, "Empty" label, **"Pair this screen"** button.
  - Paired: solid outline, screen name, online/offline dot, "Replace" + "Unpair" menu.
- Mirrored / synced-rotation: per-cell "Playlist" dropdown.
- Spanned: a "Content" button per cell that opens the canvas editor (Section 4) — but the canvas is shared across cells, so this is really one editor per wall, surfaced from any cell.

### Pair-into-cell flow

1. Admin clicks **"Pair this screen"** on an empty cell.
2. Modal opens with copy: "On the TV you want in this position, open `play.khanshoof.com`. Tap 'Have a code from admin?' and enter the code below."
3. Modal calls `POST /walls/{id}/cells/{row}/{col}/pair` (admin auth, role admin or editor):
   - Creates `wall_pairing_codes` row with a 6-char code (charset matches existing `PAIR_CODE_CHARSET`, 10-min TTL).
   - Returns `{ code, expires_in_seconds }`.
4. Modal displays the 6-char code in big text + a "Code expires in N:NN" countdown. Refresh button generates a new code.
5. Admin walks to the target TV, taps "Have a code from admin?", enters the 6 characters.
6. Player calls `POST /walls/cells/redeem` `{ code }` (unauth):
   - Looks up `wall_pairing_codes` by code, verifies pending + not expired.
   - Creates a new `screens` row scoped to the wall's org (org inherited from `walls.organization_id`).
   - Inserts/updates `wall_cells.screen_id`.
   - Sets `screens.wall_cell_id`.
   - Marks `wall_pairing_codes.status = 'claimed'`, `claimed_at = now()`.
   - Returns `{ status: paired, screen_token, wall_id, cell: {row, col, rows, cols}, mode }`.
7. TV stores `screen_token` in localStorage, opens WS to `/walls/{wall_id}/ws?screen_token=...`.

### Unpair / replace

- `DELETE /walls/{id}/cells/{row}/{col}/pairing` clears `wall_cells.screen_id` and `screens.wall_cell_id`.
- Server pushes a `bye{reason: cell_unpaired}` WS frame; TV closes WS and reverts to standalone behavior. If the screen has a non-wall playlist assignment, it resumes that. If not, it shows the standard "No content assigned" status.

### Editing a wall

Same wizard, but step 1 fields are header summary; admin can edit name / bezel / cell content. Changing rows/cols allowed but warns: "Cells outside the new grid will be unpaired. Their screens revert to standalone."

### Live mosaic preview

Wall editor shows a live mosaic of what each cell is currently playing — small fetch of each cell's current item every 5s while the editor is open. Helps admin visually verify alignment.

---

## Section 4 — Spanned-mode canvas editor + cropping math (Phase 2 only)

### Canvas editor

Opens from the wall editor's "Content" button on a spanned wall.

- Renders one proportional rectangle representing the whole wall. Bezel gaps drawn as dark stripes if compensation is on, so admin sees what customers see.
- Grid overlaid as light dashed lines so admin sees per-cell boundaries.
- Left rail: the wall's playlist (a `playlists` row with `kind = 'wall_canvas'`). Add / remove / reorder items exactly like the existing playlist editor.
- Each playlist item, when selected, fills the whole canvas (artist's WYSIWYG).
- Item-add picker: lists media of types `image | video | pdf` only. URL / text-url media filtered out with tooltip: "URL embeds aren't supported on spanned walls. Switch to a Mirrored wall to use website embeds."

Save writes `playlists` row + `playlist_items` rows. Spanned wall's `walls.spanned_playlist_id` points to it.

### Per-cell cropping (player-side, Phase 2)

Each cell renders its slice via CSS transform on a wall-sized canvas inside a cell-sized viewport with `overflow: hidden`.

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
.wall-canvas > video,
.wall-canvas > img,
.wall-canvas > iframe {
  position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: fill;
}
```

The media element fills the virtual wall canvas (all pixels rendered); the cell's viewport reveals only this cell's slice.

Same source URL plays on every cell; each browser decodes its own copy. CSS transform handles cropping; the GPU compositor handles compositing. No server-side video splitting.

### Cropping math without bezel

```
cell_x_px = (col / cols) * wall_w_px
cell_y_px = (row / rows) * wall_h_px
cell_w_px = wall_w_px / cols
cell_h_px = wall_h_px / rows
```

`wall_w_px`, `wall_h_px` come from `walls.canvas_width_px / canvas_height_px`. Defaults: `cols * 1920` × `rows * 1080`.

### Cropping math with bezel (Q3=C, Q9=A)

For each cell, given `screen_size_inches` (diagonal) + `bezel_top_mm`, `bezel_right_mm`, `bezel_bottom_mm`, `bezel_left_mm`, and the cell's native pixel resolution (assume `1920×1080` unless overridden):

```
diagonal_mm   = inches * 25.4
aspect        = pixel_w / pixel_h
panel_w_mm    = diagonal_mm * aspect / sqrt(aspect² + 1)
panel_h_mm    = panel_w_mm  / aspect
mm_per_px     = panel_w_mm / pixel_w
```

Wall total physical dimensions:

```
wall_w_mm = sum over cells in row 0:  panel_w_mm + bezel_left_mm + bezel_right_mm
wall_h_mm = sum over cells in col 0:  panel_h_mm + bezel_top_mm + bezel_bottom_mm
```

Each cell's offset on the wall canvas (project mm into wall canvas pixels):

```
canvas_mm_per_px = wall_w_mm / wall_canvas_w_px
cell_x_px = sum over prior cells in this row of:
              their panel_w_mm + their bezel_right_mm + this cell's bezel_left_mm
            ... divided by canvas_mm_per_px
cell_y_px analogous
cell_w_px = panel_w_mm / canvas_mm_per_px
cell_h_px = panel_h_mm / canvas_mm_per_px
```

Result: media painted on the virtual canvas continues "behind" the bezels — pixels behind bezels are never visible — so a circle moving across the wall stays geometrically a circle.

### Bezel rendering on the admin canvas preview

Show gaps as dark stripes; toggle to hide. Helps admin verify alignment.

### Mixed-resolution / mixed-size cells

Math handles non-uniform cells because `panel_w_px` and `panel_w_mm` are computed per cell. Phase 2 v1 wall editor offers an "All cells identical" shortcut (default) and a "Custom per cell" advanced mode.

---

## Section 5 — Player changes (both phases)

### Boot-time decision tree

```
boot()
  ├─ pair-code in URL → existing pair flow
  ├─ no token →
  │    pairingFlow():  existing self-generated code + QR + new
  │                    "Have a code from admin? Enter it here" affordance
  └─ has token →
       fetchLayout() / fetchContent()
       ├─ response includes wall_id (new field) → enterWallMode(wall_id)
       └─ else → existing single/zone playback
```

### `enterWallMode(wall_id)` — Phase 1

- Connect WebSocket: `wss://api.khanshoof.com/walls/{wall_id}/ws?screen_token=...`
- Render mirrored mode: identical to today's single-element playback path, but driven by `play` frames from WS instead of `setTimeout`.
- HTTP fallback: keep `setInterval(60s)` against `/screens/{token}/content`. While WS is open, the loop is a no-op. If WS dies for >30s, the loop drives playback like today.
- Receives `cell_state` frames for siblings; player ignores (admin UI consumes for the mosaic).

### Wall-cell pairing screen additions

- Existing pairing screen gains a small link: "Have a code from admin? Enter it here."
- Tap reveals a 6-char input. Submit calls `POST /walls/cells/redeem`. On `paired`: store `screen_token`, navigate to wall mode boot path.
- The existing self-generated code + QR path stays the default. Existing standalone-pairing customers see no change in their flow.

### `enterWallMode(wall_id)` — Phase 2 additions

- On `hello` frame, if `mode === 'spanned'`, swap DOM to the `cell-viewport > wall-canvas` structure (Section 4).
- Sibling render functions: `renderWallCanvasItem(item, wallCanvasEl)` mounts media into the canvas element instead of `contentEl`. Reuses `createVideoNode`, image / pdf branches.
- Cropping CSS variables (`--wall-w-px`, `--wall-h-px`, `--cell-x-px`, `--cell-y-px`) computed once per `hello` frame. When admin changes wall geometry (bezel or grid), the server forces all cells to disconnect; clients reconnect, receive a fresh `hello`, and recompute. Avoids needing a separate geometry-change frame in v1.

### Service worker

No change in Phase 1.

Phase 2: cache wall canvas dimensions in `localStorage` so the first paint after a page refresh is correctly cropped (avoids 100ms flash of full canvas before WS `hello` lands).

### i18n keys to add (player)

- `pairing.code_from_admin`: "Have a code from admin? Enter it here."
- `pairing.code_input_label`: "Code"
- `pairing.code_invalid`: "Code not recognized."
- `pairing.code_expired`: "Code expired. Ask admin for a new one."
- `wall.connecting`: "Connecting to wall…"
- `wall.reconnecting`: "Reconnecting to wall…"

All present in EN + AR; `scripts/check_i18n.py` enforces parity.

---

## Section 6 — Testing strategy + rollout safety

### Backend tests (pytest, runs in `signage_backend_1`)

1. **Wall CRUD:** create / list / patch / delete walls, org isolation (org A cannot see org B's walls), grid bounds (1–8), required-field validation per mode.
2. **Cell pairing:** `POST /walls/{id}/cells/{r}/{c}/pair` issues a code; `POST /walls/cells/redeem` claims it; verifies a `screens` row created scoped to the wall's org, `wall_cells.screen_id` set, `screens.wall_cell_id` set; cross-org rejected; expired codes return 410; double-redeem returns 409.
3. **WebSocket auth:** valid `screen_token` succeeds; stale/wrong rejected; token belonging to a screen NOT in the wall rejected.
4. **Tick loop:** mock asyncio time; drive a 3-item playlist; assert `play` frames fire at correct boundaries; assert `cell_state` fires on disconnect; assert reconnect replays current `play` immediately.
5. **Mirrored same_playlist:** all cells receive identical `play` frames within 5ms tolerance.
6. **Synced rotation:** different playlists per cell with matching item counts; all receive `play` at the same `started_at_ms`; mismatched item counts return 422 on save.
7. **Spanned media-type gate (Phase 2):** `POST /playlists/{wall_canvas_id}/items` rejects URL/text-url with 422.
8. **Cropping math (Phase 2):** unit tests with sample (rows, cols, bezel) inputs; assert per-cell `(cell_x_px, cell_y_px, cell_w_px, cell_h_px)` matches expected within 1px.
9. **Regression:** all existing tests must pass unchanged. `pytest 2>&1 | tail -5`.

### Frontend smoke (manual)

1. Create mirrored wall (2×1, same playlist), pair 2 TVs into cells, observe both flip items together.
2. Disconnect one TV (pull power), confirm other keeps playing, confirm wall mosaic shows dead cell offline.
3. Reconnect, confirm catch-up.
4. Rapid playlist edit, confirm `playlist_change` frame fires and cells re-fetch within ~1s.
5. Synced-rotation wall: 2 cells, different playlists with matching item counts, confirm lockstep.
6. Standalone screen regression: pair, play, confirm zero behavior change vs today.
7. Bilingual: Arabic UI for wall editor (RTL grid layout — verify visual cell numbering).

### Player smoke (manual)

1. WS drop test: stop nginx WS upstream for 2 minutes, confirm fallback HTTP polling kicks in.
2. Long-running playback (8h overnight): clock-offset estimate stable, seek deltas <200ms.

### Phase 2 manual

- 2×2 wall on a single TV with 4 browser windows positioned to simulate cells; play a known horizontal-pan video; verify motion is continuous across windows. (No actual 4-TV setup required for cropping correctness — windows suffice.)

### Rollout safety

1. **All migrations are additive.** New tables + one new column on `screens` + one new column on `organizations` + one new column on `playlists`. Rollback = drop the new tables and columns; no data loss for existing screens.
2. **Feature is invisible to existing customers** until they create a wall. "Walls" tab hidden if `walls_enabled = false` or if no walls exist yet.
3. **Player change gated by wall membership.** A standalone screen never opens a WebSocket and never sees wall code paths. Today's polling behavior is the same code path as before.
4. **Backend WebSocket endpoint is new** — adding it cannot break existing HTTP routes. uvicorn WebSocket support is built in; no new dependency.
5. **Nginx tweak required** for WebSocket upgrade on `/walls/*/ws`:
   ```
   proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection "upgrade";
   proxy_read_timeout 3600s;
   ```
   Goes in the same PR.
6. **Legacy `pairing_codes` and `/screens/pair` paths untouched.** Wall pairing is a separate table and separate endpoints to avoid coupling.
7. **Per-wall asyncio task is lazy.** Created on first WS connect to a wall, torn down on last disconnect. Unused walls cost zero.
8. **Single-uvicorn-worker assumption** documented in `walls.py` docstring + `PROJECT_SCOPE.md`. Migration path when scaling workers: Redis pub/sub between workers (out of scope, easy when needed).
9. **Roll-back plan:** if Phase 1 misbehaves, set `walls_enabled = false` for all orgs (one SQL UPDATE) — UI hides the tab; existing wall-paired screens get a server-side `bye` frame and revert to standalone playback using their last-known playlist. No customer data lost.

---

## Phasing summary

**Phase 1 (target: ~1 week of work):**
- Data model: `walls`, `wall_cells`, `wall_pairing_codes`, `screens.wall_cell_id`, `organizations.walls_enabled`, `playlists.kind`.
- Backend: wall CRUD, pair-into-cell endpoints, WebSocket endpoint, asyncio tick loop, mirrored mode (`same_playlist` + `synced_rotation`).
- Admin UI: Walls tab, list page, create-wall wizard, pair-into-cell modal, mirrored cell config, live mosaic preview.
- Player: wall-cell pairing affordance, `enterWallMode` for mirrored, WS + time-anchor + HTTP fallback.
- Nginx WS upgrade config.
- i18n keys EN + AR.

**Phase 2 (target: ~2–3 weeks of work; written but not executed until Phase 1 is in production):**
- Spanned-mode canvas editor.
- Per-cell cropping in player.
- Bezel compensation math.
- URL/text-url media gate in editor.
- Service-worker canvas-dim caching.

Each phase gets its own implementation plan. Phase 1 plan is written and executed first.
