# Phone-Driven Video Wall Setup — Design Spec

**Date:** 2026-11-01
**Status:** Approved in chat 2026-11-01; awaiting review of this written spec.
**Builds on:** `2026-05-01-multi-screen-wall-sync-design.md` (walls, sync protocol) and
`2026-05-04-multi-screen-walls-phase2-design.md` (spanned walls, canvas playlists).
Those stay valid for today's grid walls; this spec adds a second way to build a
spanned wall and fixes what that way depends on.

---

## Goal

Set up a video wall from a phone, standing in front of the TVs:

1. Every TV shows its usual pairing QR code.
2. Scan the first TV, choose **Part of a video wall**, start a new wall and pick
   (or upload) the video.
3. The phone shows the video with a box for this TV's portion. Drag the box
   (pinch or slider to resize) and the TV shows exactly what is in the box,
   following the finger live.
4. Tap **Done**, scan the next TV, place its box, and so on.
5. All TVs play the video in sync, as if one video ran across them.

## Non-goals (v1)

- Rotating content for TVs mounted on their side. A TV reports the screen size
  it has; if its software is landscape while the panel hangs portrait, that is a
  separate player feature (screen rotation).
- Adding a TV that is already paired as a standalone screen (it shows content,
  not a QR). Unpair it first; it then shows a QR.
- Cutting the video into one file per TV on the server. Live dragging needs every
  TV to hold the whole picture.
- A QR scanner inside the dashboard. The phone's own camera app opens the QR's
  `/pair?code=` link, as it does today.
- Changing the dashboard's grid-wall wizard. Grid walls keep working; the player
  fix below improves them as a side effect.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How a TV's portion is chosen | Free placement: a box over the video, dragged on the phone, mirrored live on the TV. Not a grid cell. |
| 2 | Video vs placement order | The video is chosen first; boxes are placed over it. |
| 3 | Box shape | Locked to the TV's own screen shape, which the TV reports when it connects. |
| 4 | Box size | Per TV. A new TV starts at the previous TV's size, flush to its right. |
| 5 | Snapping | Edges snap to other boxes' edges and to the video's edges when within 3% of the video's width (vertical edges) or height (horizontal edges); dragging further releases. |
| 6 | Box outside the video | Allowed while at least 5% of the box overlaps the video; the part outside shows black. Covers "fill" and "fit" without a mode switch. |
| 7 | What the phone shows | The video itself, muted and looping. |
| 8 | Rendering on TVs | Every TV plays the same file and crops it in the browser (today's model), now scaled to fill the screen. |
| 9 | Live transport | Phone → `PUT` → Postgres + Redis pub/sub → the worker holding the TV's socket → `geometry` frame. Tick loop re-checks as a fallback. |
| 10 | TVs per wall | Up to 8 (the existing `cols` limit). Each TV counts as a screen on the plan. |
| 11 | Pairing on the TV | No change: the TV's existing QR / poll flow hands it its token; it then finds it is a wall cell. |
| 12 | Video size | Every video transcode caps the long side at 3840 px, so TV hardware decoders can play wall panoramas. |

---

## Section 1 — Data model

All changes are additive (`ADD COLUMN IF NOT EXISTS` in `db.py`, as for Phase 2).

```sql
ALTER TABLE walls ADD COLUMN IF NOT EXISTS layout TEXT NOT NULL DEFAULT 'grid'
  CHECK (layout IN ('grid', 'free'));

ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_x REAL;   -- fractions of the canvas
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_y REAL;   -- width / height; the box's
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_w REAL;   -- top-left corner and size
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_h REAL;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS screen_w_px INTEGER;  -- what the TV reported
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS screen_h_px INTEGER;  -- (CSS px of its viewport)
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS highlight_until_ms BIGINT;  -- outline on the TV until then

ALTER TABLE media ADD COLUMN IF NOT EXISTS duration_seconds REAL;   -- videos, from ffprobe
ALTER TABLE media ADD COLUMN IF NOT EXISTS width_px  INTEGER;       -- images and videos
ALTER TABLE media ADD COLUMN IF NOT EXISTS height_px INTEGER;
```

A **free wall** is a `walls` row with `mode = 'spanned'` and `layout = 'free'`:

- Its canvas is its video: `canvas_width_px × canvas_height_px` are the video's
  probed size after transcoding. Boxes are stored as fractions of the canvas, so
  replacing the video with one of another size keeps every box in the same
  relative place.
- Its cells are added as TVs join: `row_index = 0`, `col_index` = the lowest
  unused index (0–7). `rows = 1`, and `cols` = the highest used index + 1 (at
  least 1), so code that walks `range(cols)` still finds every cell. Removing a TV
  deletes its cell. A TV's label is "TV {col_index + 1}", which stays the same
  while it is in the wall.
- `bezel_*` stay 0 and are ignored: a gap for bezels is left by placing the boxes.
- Its canvas playlist (`kind = 'wall_canvas'`, as today) holds the video as one
  item with `fit_mode = 'fit'` and `duration_seconds` = the probed length.

`media.duration_seconds` feeds the existing forward hook in
`_default_duration_seconds`, but only wall items use it in this change; standalone
playlist defaults are not changed.

## Section 2 — API

All new endpoints: session roles admin/editor, `require_active_subscription`,
organization-scoped like the existing wall endpoints. Errors use `http_error`
codes so the phone can show translated messages.

### `POST /walls/free`

```json
{ "name": "Lobby wall", "media_id": 812, "code": "K7MP2Q" }
```

One transaction:

1. Check the TV code exactly as `/screens/claim` does (unknown 404, expired 400,
   already paired 409) and the media (own org, `image/*` or `video/*`).
2. Create the wall (`layout = 'free'`, canvas from the media's size, falling back
   to 3840×2160 if the size is unknown) and its canvas playlist with the item.
3. Bind the TV into cell (0, 0) (see "Binding a TV" below). First box: the full
   canvas height at the left edge, its width from the TV's shape (16:9 until the
   TV reports). On a 3840×720 panorama that is exactly the left third.
4. Mark the pairing code `paired` with the new screen, so the TV's next poll
   hands it the token.

Plan limit reached → 402 `plan_limit`, and nothing is created.
Returns the wall with its cells (`serialize_wall`).

### `POST /walls/{wall_id}/tvs`

```json
{ "code": "R4WZ8N" }
```

Adds the TV into the next cell of a free wall. Its first box has the size of
the most recently added TV's box, flush to that box's right edge and top-aligned.
If that would leave less than 5% of it on the canvas, it is moved left until 5%
overlaps. 409 `wall.full` at 8 TVs; 409 `wall.not_free` on
a grid wall. Returns the new cell.

### `PUT /walls/{wall_id}/cells/{row}/{col}/placement`

```json
{ "x": 0.3333, "y": 0.0, "w": 0.3333, "h": 1.0, "highlight": true }
```

- `w`, `h` in (0, 4]; the box must overlap the canvas by at least 5% of its own
  area; otherwise 422 `wall.placement_invalid`.
- Free walls only (409 `wall.not_free`).
- Saves the box, then publishes a `geometry` frame for that cell (Section 3).
- `highlight` (default true) sets `highlight_until_ms` to now + 30 s, which
  shows the outline and label on the TV. `false` clears it; the phone sends
  `false` on **Done**.
- Rate limit `1800/minute`: the phone sends at most one request at a time.
- Returns 204.

### `PUT /walls/{wall_id}/video`

```json
{ "media_id": 913 }
```

Replaces a free wall's video: the canvas size follows the new media, the boxes
keep their fractions, and the canvas playlist item is swapped. The tick loop's
existing config check re-sends `hello` to the TVs.

### Changed endpoints

- `DELETE /walls/{id}/cells/{row}/{col}/pairing` on a free wall also deletes the
  cell row, which frees its index for the next TV, and sets `cols` to the highest
  remaining index + 1.
- `PATCH /walls/{id}` on a free wall: renaming is allowed; changing `mode`,
  `canvas_*` or `bezel_*` returns 409 `wall.free_layout`.
- `GET /walls`, `GET /walls/{id}`: also return `layout` and the new cell
  columns (they come from `SELECT *` already).
- `POST /media/upload`: probes the file before answering (Section 6).

### Binding a TV

`redeem_wall_cell` already creates or reuses a cell's screen inside one
transaction (code claim, `FOR UPDATE` on the organization and the cell, plan
limit, stale screens cleaned up). That body becomes a helper,
`_bind_screen_to_cell(conn, wall, cell, now)`, used by `redeem_wall_cell`,
`POST /walls/free` and `POST /walls/{id}/tvs`. The new endpoints claim the
standalone `pairing_codes` row with the same conditional
`UPDATE … WHERE status = 'pending'` as `/screens/claim`, so two phones cannot
claim one TV.

## Section 3 — Live placement channel

Production runs 4 uvicorn workers, and the TV's socket may live on a different
worker from the one answering the phone's `PUT`.

- **Publish.** After saving a box, the `PUT` handler publishes
  `{"wall_id", "row", "col"}` ("this cell changed") on Redis channel
  `khan:wall-geometry`, using
  the existing client in `ratelimit.get_redis()`. A publish failure is logged
  and does not fail the request.
- **Subscribe.** Each worker starts one subscriber task (`redis.asyncio`) the
  first time a wall socket connects to it. For each message about a socket it
  holds, it re-reads that cell and sends a `geometry` frame. On a Redis error it
  retries with backoff; the tick loop keeps working.
- **Fallback.** Each tick-loop pass (at most every 5 s) compares each connected
  cell's box and `highlight_until_ms` with what it last sent, and sends a
  `geometry` frame if either changed. Without Redis a move takes up to 5 s to arrive, but it always arrives,
  including at a TV that reconnects.
- **The phone** keeps one `PUT` in flight. When it returns, the phone sends the
  newest box if it changed meanwhile. Moves never queue up behind a slow network,
  and the last position always lands.

### Frames

Server → TV (new):

```json
{ "type": "geometry",
  "canvas": { "w": 3840, "h": 720 },
  "cell_geometry": { "x": 1280, "y": 0, "w": 1280, "h": 720 },
  "highlight_until_ms": 1762000030000, "label": "TV 2",
  "server_now_ms": 1762000000000 }
```

`hello` for a free wall carries `canvas` and `cell_geometry` from the stored box
(converted to canvas px), plus `highlight_until_ms` (null when off) and `label`.
For grid walls `hello` is unchanged.

TV → server (new):

```json
{ "type": "screen", "w": 960, "h": 540 }
```

Sent when the socket opens and when the viewport changes size (debounced 1 s).
Clamped to 100–10000 and stored in `wall_cells.screen_w_px/h_px` if it changed.
If the TV's box still has the 16:9 default shape and the TV is a different
shape, the server adjusts the box height to match (same width and centre) and
sends `geometry`.

The highlight lives in the database (`highlight_until_ms`), not in worker
memory, so every worker, the tick-loop fallback and a reconnecting TV agree on
it. It expires 30 s after the last placement, so a phone that is closed mid-edit
does not leave outlines on the TVs.

## Section 4 — Player

Everything stays Chrome 66-safe (no `?.`, `??`, numeric separators, object
spread, `flatMap`; CSS: no `inset`, flex `gap`, inline `min()`/`max()`).

1. **Scale the slice to the screen (fixes all spanned walls).** Today
   `.wall-canvas` is only translated, so the crop is right only when a cell's
   slice is exactly the viewport's size. New:
   `transform: scale(vw / cell.w, vh / cell.h) translate(-cell.x px, -cell.y px)`,
   with `transform-origin: 0 0`, set from JS on `hello`, `geometry` and window
   `resize`. For a free wall the box has the TV's shape, so the two scales are
   equal. A grid wall whose cell shape differs from its TV now fills the screen
   slightly stretched, where today it shows the wrong strip and black.
2. **`geometry` frames** update only the transform (and the highlight). The
   media element is not touched, so a playing video keeps playing.
3. **Highlight.** A fixed outline around the screen, with the label ("TV 2")
   centred in large type. It is shown until `highlight_until_ms`, measured on
   the server's clock through the player's clock offset; a null value hides it
   at once.
4. **Report the screen size** with a `screen` message on open and after resizes.
5. **Tighter clock offset.** Today every frame overwrites the offset with
   `server_now_ms − received`, which also includes the network delay. New: keep
   the samples from the last 10 minutes (at most 32) and use the largest. Every
   sample equals the true offset minus its own delay, so the largest is the
   least delayed. Drift correction thresholds are unchanged.
6. **Pairing.** No change. After its QR is claimed into a wall, the TV's poll
   returns the token, `/screens/{token}/content` returns `wall_id`, and the TV
   enters wall mode as today.

Strings (EN + AR in `player/i18n`): `wall.tv_label` = "TV {n}" / "شاشة {n}".

## Section 5 — Phone UI (dashboard)

This lives in the dashboard, which the redesign session
(`feature/friendly-dashboard`) is reworking. Most code goes in **new files**,
`frontend/wall-setup.js` and `frontend/wall-setup.css`. Hooks in the files that
session owns are kept small and agreed with it first:

- `frontend/index.html`: a `#wall-setup-view` full-page section (like
  `#pair-view`), a "What is this TV?" step in the pair form, and the script and
  stylesheet tags.
- `frontend/app.js`: route `/wall-setup?wall=<id>` in `boot()` (like `/pair`),
  call into `WallSetup` from the pair form, and an **Adjust TVs** button on
  free walls in the Walls page.
- `frontend/i18n/en.json`, `ar.json`: new `wallsetup.*` keys, kept at parity by
  `scripts/check_i18n.py`.

### Screens

1. **What is this TV?** Shown on `/pair` once the code is filled in. Two large
   choices: **Its own screen** (today's form, unchanged) and **Part of a video
   wall**.
2. **Which wall?** The organization's free walls, newest first. The wall this
   phone set up or joined in the last 30 minutes (remembered in
   `sessionStorage`) is preselected. There is also **New video wall**.
3. **New wall: name and video.** The name is prefilled ("Video wall", or
   "Video wall 2" if taken). The video comes from **Library** (the existing
   `MediaPicker`, filtered to video and image) or **Upload from this phone**
   (`<input type=file accept="video/*,image/*">` to `/media/upload`, with
   progress). Then `POST /walls/free`.
4. **Joining an existing wall:** `POST /walls/{id}/tvs`.
5. **Place this TV.** The video plays muted and on loop, across the phone's
   width. Every TV's box is drawn on it: the others are outlined and labelled,
   and this one is filled and draggable.
   - One finger drags; two fingers pinch to resize, keeping the TV's shape; a
     size slider does the same.
   - Edges snap (decision 5).
   - The editor area is always `dir="ltr"`: the video is physical, and left is
     left in Arabic too. Labels and buttons follow the UI language.
   - While the TV hasn't reported its size, the box is 16:9 and a note says
     "Waiting for TV {n} to connect…".
   - The editor re-reads the wall every 3 s, to pick up TV sizes and online dots.
6. **Done.** Sends `highlight: false` and shows "TV {n} placed. Point your
   camera at the next TV's QR code", plus **Finish**, which goes to the wall's
   page. Scanning the next QR opens `/pair` in the same browser, so the
   preselected wall makes it two taps.
7. **Adjust later.** `/wall-setup?wall=<id>` (reached from **Adjust TVs**)
   opens the same editor; tap a box to select that TV. Each box has **Remove
   TV** (the existing unpair, behind `confirmDialog`).

Error messages reuse the `/pair` mapping (unknown, expired or used code; plan
limit; no permission), plus `wall.full` and `wall.placement_invalid`.

## Section 6 — Media

- **Probe at upload.** `/media/upload` runs `ffprobe` (videos) or Pillow
  (images) before answering, with a 20 s timeout, and stores `width_px`,
  `height_px` and `duration_seconds`. After a transcode finishes, the size is
  re-probed, because the cap below can change it. A failed probe leaves the
  columns NULL; the wall then falls back to 3840×2160 and a 10 s item.
- **Cap at 3840.** `transcode_video` adds
  `-vf scale='if(gt(iw,ih),min(3840,iw),-2)':'if(gt(iw,ih),-2,min(3840,ih))'`.
  A 5760×1080 panorama becomes 3840×720, and each of three TVs shows a
  1280×720 slice scaled to its screen. That is softer than native, but cheap TV
  decoders often stop at 4096 px wide, and a file a TV cannot decode shows
  nothing at all. Smaller videos are unchanged.
- **Upload limit.** `MAX_UPLOAD_MB` (50 by default) also limits phone uploads.
  Bigger files get the existing 413, and the phone says the video is too large.
  Raising the limit is an operator setting, unchanged here.

## Section 7 — Error handling and edge cases

| Case | Behaviour |
|---|---|
| Code unknown / expired / already used | 404 / 400 / 409; the phone shows the `/pair` messages; nothing created. |
| Plan limit | 402 before anything is created (one transaction). |
| Two phones claim one TV | The conditional `UPDATE` lets only one win; the other gets 409. |
| Two phones move one box | Last write wins; both editors catch up on their next 3 s read. |
| TV offline while placing | The box is saved; the TV gets it in `hello` when it reconnects. |
| Redis down | Moves arrive through the tick loop within 5 s. |
| TV reloads or reboots | `hello` carries its geometry, so its first frame is already cropped right. |
| Video still transcoding | The TV plays the original until the transcode swaps the file (today's behaviour). The canvas keeps the probed aspect; boxes are fractions, so they stay valid. |
| Wall deleted / TV removed | Existing `bye` / `wall_left` handling; a removed TV shows its QR again. |
| 9th TV | 409 `wall.full`. |

## Section 8 — Testing

### Backend (pytest, in Docker; never alongside another session's run)

- `POST /walls/free`: success; each code rejection; foreign media; non-image or
  non-video media; plan limit (nothing left behind); the TV's poll returns the
  token afterwards; the TV's content returns `wall_id`.
- `POST /walls/{id}/tvs`: the next index; the first box flush right of the
  previous one; full at 8; grid wall refused; two concurrent claims of one code.
- Placement: validation bounds, org isolation, grid wall refused, publish
  called, `highlight_until_ms` set and cleared. Redis failure still returns 204.
- `hello` / `geometry` for free walls: px conversion; `highlight_until_ms`;
  `screen` message stored and clamped; box height adjusted once for a non-16:9
  TV.
- Tick-loop fallback sends `geometry` when the stored box changed.
- Subscriber forwards only to sockets this worker holds.
- Removing a free-wall TV deletes the cell and frees its index; `cols` follows
  the highest index. `PATCH` refuses mode, canvas and bezel changes.
- Upload probe: dimensions and duration stored; a probe failure leaves NULLs.
  Transcode cap: a 5760×1080 input comes out 3840×720.
- Every existing test still passes.

### End to end (`scripts/e2e-scan-to-pair/`, extended)

A new `e2e_wall.py` with `run.sh --wall`. The backend image is built from the
branch (`BACKEND_IMAGE`), with `WEB_CONCURRENCY=4`, so the phone's `PUT` and a
TV's socket usually land on different workers:

1. ffmpeg (in the backend image) makes a 20 s 5760×1080 test video: three
   coloured bands (red, green, blue) with large digits 1, 2, 3, and a white bar
   sweeping across all three.
2. Three TV pages (1920×1080; one at 960×540 to cover high-DPR TVs) show QR
   codes. Each page shifts `Date.now()` by a different amount (−3 s, 0, +2 s) to
   stand in for skewed TV clocks.
3. The phone signs in, opens TV 1's QR URL, chooses video wall → new wall,
   uploads the video, places TV 1 over the red band, Done. TV 2 joins the wall
   and is placed over green; TV 3 over blue.
4. **Live drag:** during TV 2's placement the phone drags the box, and TV 2's
   `.wall-canvas` transform and centre pixel colour change within 1 s (well
   under the 5 s fallback, which shows the Redis path worked).
5. **Crop:** each TV's screenshot is mostly its own band's colour, with its
   digit in the middle.
6. **Sync:** sampled together, the three TVs' `video.currentTime` values agree
   within 100 ms, despite the shifted clocks.
7. **Highlight:** the outline shows while placing and is gone after Done.
8. Arabic: the same flow in `ar` reaches the editor, and the editor stays LTR.

### Chromium 66

The player's wall mode, `geometry` frames, scaling and highlight are checked in
headless Chromium 66.0.3359.0, as PR #23 did. That covers a free-wall cell and a
grid-wall cell.

## Section 9 — Branches, PRs, rollout

- **PR 1 — backend, player, harness:** branch `feature/phone-wall-setup`, cut
  from `fix/player-cache-busting` (production's player is #22 + #23 + #24), with
  that branch as its base.
- **PR 2 — dashboard:** branch `feature/phone-wall-setup-dashboard`, cut from
  `feature/friendly-dashboard` (production's dashboard), once the redesign
  session agrees on the hooks.
- The end-to-end run uses a local, unpushed merge of both branches.
- Nothing is deployed or merged without the owner's OK. When approved, the order
  is backend (additive migration), then player, then dashboard, each with a
  rollback tag as in earlier deploys.
- Rollback safety: grid walls and standalone screens see no change apart from
  the scaling fix. A free wall rolled back to an older backend behaves like a
  1×N grid wall.

## Section 10 — Out of scope, noted for later

- Screen rotation for sideways-mounted TVs.
- Adding an already-paired standalone screen to a wall.
- Server-side per-TV cuts, for full sharpness on very wide walls.
- Using probed video length as the default in standalone playlists.
- More than 8 TVs per wall.
