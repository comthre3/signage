# Phone-Driven Video Wall Setup — Design Spec

**Date:** 2026-11-01
**Status:** Approved in chat 2026-11-01. Amended the same day by the implementation
plan (see "Plan amendments" under the decisions); the sections below already read
as amended.
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

### Plan amendments

D rows were made while writing the implementation plan, after reading the code;
R rows are rulings made when the plan was reviewed.

| # | Amendment |
|---|---|
| D1 | Frames carry `tv_number` (`col_index + 1`), not an English `label`. The player shows it through `wall.tv_label` ("TV {n}" / "شاشة {n}"). |
| D2 | Standalone playlist defaults do not change: `_default_duration_seconds` stops reading `media.duration_seconds` (its old forward hook) and keeps 10 s for videos. |
| D3 | When a transcode finishes, every free wall playing that media gets the transcoded size as its canvas and the probed length as its item's duration. |
| D4 | The Redis channel is `khan:wall-geometry:db<N>`, N being the logical database in `REDIS_URL`, so test runs never publish into production's channel. |
| D5 | `GET /screens/poll/{code}` goes from 30 to 240 requests per minute and `POST /screens/request_code` from 10 to 30, for up to 8 TVs behind one venue's NAT (8 TVs polling every 3 s make 160 polls a minute, plus the phone's code checks). |
| D6 | `serialize_wall` adds `online` per cell and, for free walls, `video` (the canvas item's media). |
| D7 | Ghost cells (a TV deleted from the Screens page leaves its cell with no screen) are deleted when the next TV joins the wall. |
| D8 | A free wall's canvas playlist holds exactly one item. `PUT /walls/{id}/video` deletes and re-inserts it, so TVs switch at once. |
| D9 | The end-to-end run is in loopback mode: TVs load the player from `http://127.0.0.1`, pointed at the local API, because Playwright routing does not intercept WebSockets (a routed `https://play.khanshoof.com` TV would open its wall socket to production). |
| R1 | When the first box is wider than the video (w > 1: a portrait video on a 16:9 TV), it is centred, x = (1 − w) / 2. Otherwise x = 0. |
| R2 | The placement body takes plain numbers and `wall_layout.validate_box` decides, so every refused box is 422 with code `wall.placement_invalid` (w = 0, w = 5, NaN, a box off the video). |
| R3 | Refines decision 4: a TV added with `POST /walls/{id}/tvs` gets the previous (most recently added) box's height in the default 16:9 shape (width from that height), at x = previous x + previous w and the previous y, then nudged onto the video. For identical 16:9 TVs that is the previous box's size; after a portrait neighbour the new box stays 16:9, so the new TV's own screen report can refit it. |
| R4 | `POST /walls/free` and `POST /walls/{id}/tvs` are limited to 30 requests per minute. |
| R7 | Only a free wall's socket starts the geometry subscriber; grid walls never do. |
| R8 | The server enforces the TV's shape: when a cell has reported its screen size, the placement `PUT` stores the requested box reshaped to that screen (same width and centre). The phone reshapes too, so its box matches what the server stores. |
| R10 | The end-to-end sync bound is the median of 12 sampled spreads, each taken 6 s or more into the loop, under 100 ms (Section 8, item 6): a seek leaves a video up to ~110 ms off until the drift corrector pulls it back. |
| R11 | In the end-to-end run, the highlight is gone less than 1 s after the `highlight: false` placement: it rides the same Redis path as moves. |
| R13 | `/pair` preselects **Part of a video wall** when this phone remembers a wall from the last 30 minutes. Joining that wall is then **Continue**, **Add this TV**: two taps after the code. |
| R14 | On the Walls page, a free wall's canvas editor hides the item duration, Fit/Fill/Stretch, Delete item, Add item, the bezel grid and the mode switch, and shows the video's name and **Adjust TVs**. Its card reads "Set up from a phone · {n} TVs". |
| R15 | Before uploading or creating anything, the phone checks the TV code with the anonymous `GET /screens/poll/{code}`, as `/pair` does; a rejected code shows the `/pair` message and stops. |
| R16 | **Finish** goes to the Walls page with that wall's editor open. |
| R17 | Done text: "TV {n} placed. Point your camera at the next TV's QR code." when the TV was just added from `/pair`; only "TV {n} placed." in Adjust mode. |
| R18 | After the last TV is removed, the editor shows "No TVs in this wall yet. Scan a TV's QR code and choose Part of a video wall." with a link back to Video walls. The wall is not deleted. |
| R19 | History: choosing **Part of a video wall** on `/pair` pushes one history entry, still at `/pair`; the editor replaces that entry with `/wall-setup?wall=<id>&tv=<n>`, so Back from the editor returns to the `/pair` form. |

Also corrected: the transcode filter in Section 6 rounds the long side down to
even, because libx264 refuses odd sizes (a 1281×721 video failed to transcode).

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
  probed size after transcoding. A wall is usually created while its video is
  still transcoding, so it starts with the size probed at upload; when the
  transcode finishes, `_refresh_free_walls_for_media` sets every free wall
  playing that media to the transcoded size, and its item to the probed length.
  The aspect is unchanged, so no box moves. Boxes are stored as fractions of the
  canvas, so replacing the video with one of another size keeps every box in the
  same relative place.
- Its cells are added as TVs join: `row_index = 0`, `col_index` = the lowest
  unused index (0–7). `rows = 1`, and `cols` = the highest used index + 1 (at
  least 1), so code that walks `range(cols)` still finds every cell. Removing a TV
  deletes its cell. A TV's number is `col_index + 1`, which stays the same while
  it is in the wall. Frames carry the number (`tv_number`), not text, so each TV
  shows it in its own language ("TV 2", "شاشة 2"). Its screen row is named
  "{wall name} · TV {n}".
- `bezel_*` stay 0 and are ignored: a gap for bezels is left by placing the boxes.
- Its canvas playlist (`kind = 'wall_canvas'`, as today) holds exactly one item,
  the video, with `fit_mode = 'fit'` and `duration_seconds` =
  `max(1, ceil(probed length))` (the column is an integer), or 10 when the length
  is unknown or the media is an image.
- A TV deleted from the Screens page leaves its cell behind with `screen_id`
  NULL (the foreign key sets it). Such ghost cells are deleted when the next TV
  joins the wall, so they neither hold an index nor draw a box.

`_default_duration_seconds` had a forward hook that read `media.duration_seconds`
for videos. Now that the column exists the hook is removed, so standalone
playlist defaults stay at 10 s for videos (a probed two-hour video would
otherwise get a 7200 s item). Only free-wall items use the probed length.

## Section 2 — API

All new endpoints: session roles admin/editor, `require_active_subscription`,
organization-scoped like the existing wall endpoints. Errors use `http_error`
codes so the phone can show translated messages.

### `POST /walls/free`

```json
{ "name": "Lobby wall", "media_id": 812, "code": "K7MP2Q" }
```

One transaction:

1. Check the TV code (unknown 404 `pair.code_unknown`, already paired 409
   `pair.code_used`, expired 400 `pair.code_expired`) and the media (own org,
   else 404 `media.not_found`; `image/*` or `video/*`, else 400
   `wall.media_type_blocked`).
2. Create the wall (`layout = 'free'`, canvas from the media's size, falling back
   to 3840×2160 if the size is unknown) and its canvas playlist with the item.
3. Bind the TV into cell (0, 0) (see "Binding a TV" below). First box: the full
   canvas height at the left edge, its width from the TV's shape (16:9 until the
   TV reports). On a 3840×720 panorama that is exactly the left third. A box
   wider than the video (a portrait video on a 16:9 TV) is centred on it
   instead, x = (1 − w) / 2; one that would be more than 4 video widths wide
   (a very tall video) is first shrunk to 4, keeping its shape.
4. Mark the pairing code `paired` with the new screen, so the TV's next poll
   hands it the token.

Plan limit reached → 402 `plan_limit`, and nothing is created.
Rate limit `30/minute`. Returns 201 with the wall and its cells (`serialize_wall`).

### `POST /walls/{wall_id}/tvs`

```json
{ "code": "R4WZ8N" }
```

Adds the TV into the next cell of a free wall: the wall's ghost cells (no
screen) are deleted first, then the TV takes the lowest unused index. Its first
box has the most recently added TV's box's height and the default 16:9 shape
(width from that height; shrunk to 4 video widths, keeping that shape, if it
would be wider), flush to that box's right edge and top-aligned. If that would
leave less than 5% of it on the canvas, it is moved left until 5% overlaps.
Keeping 16:9, not the previous box's own shape (a refit portrait TV, say),
lets the new TV's screen report refit the box to that TV. 409 `wall.full` at
8 TVs; 409 `wall.not_free` on a grid wall. Rate limit `30/minute`. Returns 201
with the new cell (including `online`).

### `PUT /walls/{wall_id}/cells/{row}/{col}/placement`

```json
{ "x": 0.3333, "y": 0.0, "w": 0.3333, "h": 1.0, "highlight": true }
```

- `w`, `h` in (0, 4]; the box must overlap the canvas by at least 5% of its own
  area; otherwise 422 `wall.placement_invalid` (every refused box gets that
  code). When the TV has reported its screen size, the stored box is the
  requested box reshaped to that screen (same width and centre), so the TV's
  shape wins over a phone that has not seen it yet.
- Free walls only (409 `wall.not_free`).
- Saves the box, then publishes a `geometry` frame for that cell (Section 3).
- `highlight` (default true) sets `highlight_until_ms` to now + 30 s, which
  shows the outline and the TV's number on the TV. `false` clears it; the phone
  sends `false` on **Done**.
- Rate limit `1800/minute`: the phone sends at most one request at a time.
- Returns 204.

### `PUT /walls/{wall_id}/video`

```json
{ "media_id": 913 }
```

Replaces a free wall's video: the canvas size follows the new media, the boxes
keep their fractions, and the one canvas playlist item is deleted and inserted
again for the new media. The new item id starts a new play at once, even when
the new video has the same size and length; a size change also makes the tick
loop's config check re-send `hello`. Returns the wall (`serialize_wall`).

### Changed endpoints

- `DELETE /walls/{id}/cells/{row}/{col}/pairing` on a free wall also deletes the
  cell row, which frees its index for the next TV, and sets `cols` to the highest
  remaining index + 1.
- `PATCH /walls/{id}` on a free wall: renaming is allowed; changing `mode`,
  `canvas_*` or `bezel_*` returns 409 `wall.free_layout`.
- `GET /walls`, `GET /walls/{id}` (and every response built by
  `serialize_wall`): also return `layout` and the new cell columns (they come
  from `SELECT *` already), plus per cell `online` (its screen was seen in the
  last 90 s) and, for free walls, `video`: `{media_id, name, url, mime_type,
  width_px, height_px}` of the canvas item, or null.
- `POST /media/upload`: probes the file before answering (Section 6).
- `GET /screens/poll/{code}`: rate limit 30 → 240 per minute, and
  `POST /screens/request_code`: 10 → 30 per minute. The limits are per client
  IP, and up to 8 TVs behind one venue's NAT poll every 3 s while they wait to
  be scanned: 160 polls a minute, plus the phone's code checks.

### Binding a TV

`redeem_wall_cell` already creates or reuses a cell's screen inside one
transaction (code claim, `FOR UPDATE` on the organization and the cell, plan
limit, stale screens cleaned up). That body becomes a helper,
`_bind_screen_to_cell(wall, cell, org, now, name)`, used by `redeem_wall_cell`,
`POST /walls/free` and `POST /walls/{id}/tvs`. The new endpoints claim the
standalone `pairing_codes` row with the same conditional
`UPDATE … WHERE status = 'pending'` as `/screens/claim`, so two phones cannot
claim one TV.

## Section 3 — Live placement channel

Production runs 4 uvicorn workers, and the TV's socket may live on a different
worker from the one answering the phone's `PUT`.

- **Publish.** After saving a box, the `PUT` handler publishes
  `{"wall_id", "row", "col"}` ("this cell changed") on Redis channel
  `khan:wall-geometry:db<N>`, where N is the logical database number in
  `REDIS_URL` (0 in production). Pub/sub ignores logical databases, and the test
  suite uses production's Redis server with another database number, so the
  suffix keeps test publishes out of production's channel. It uses the existing
  client in `ratelimit.get_redis()`. A publish failure is logged and does not
  fail the request. When the worker answering the `PUT` holds the TV's socket
  itself, it also sends the frame straight away.
- **Subscribe.** Each worker starts one subscriber task (`redis.asyncio`) the
  first time a free wall's socket connects to it (grid walls never start it).
  For each message about a socket it
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
  "highlight_until_ms": 1762000030000, "tv_number": 2,
  "server_now_ms": 1762000000000 }
```

`hello` for a free wall carries `canvas` and `cell_geometry` from the stored box
(converted to canvas px), `bezel` of zeros, plus `highlight_until_ms` (null when
off) and `tv_number`. For grid walls `hello` is unchanged.

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
3. **Highlight.** A fixed outline around the screen, with the TV's number
   (`wall.tv_label` filled with the frame's `tv_number`: "TV 2", "شاشة 2")
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

Strings (EN + AR in `player/i18n`): `wall.tv_label` = "TV {n}" / "شاشة {n}",
where `{n}` is the frame's `tv_number`.

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
   wall**. When this phone remembers a wall from the last 30 minutes (step 2),
   **Part of a video wall** is preselected, so joining that wall takes two taps
   after the code: **Continue**, then **Add this TV**.
2. **Which wall?** The organization's free walls, newest first. The wall this
   phone set up or joined in the last 30 minutes (remembered in
   `sessionStorage` and `localStorage`, since the camera app usually opens the
   next QR's link in a new tab) is preselected. There is also **New video wall**.
3. **New wall: name and video.** The name is prefilled ("Video wall", or
   "Video wall 2" if taken). The video comes from **Library** (the existing
   `MediaPicker`, filtered to video and image) or **Upload from this phone**
   (`<input type=file accept="video/*,image/*">` to `/media/upload`, with
   progress). Before uploading or creating anything, the phone checks the TV
   code with the anonymous `GET /screens/poll/{code}`, as `/pair` does; a
   rejected code shows the `/pair` message and stops. Then `POST /walls/free`.
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
   - The editor re-reads the wall every 3 s (`GET /walls/{id}`), to pick up TV
     sizes (`screen_w_px`, `screen_h_px`), online dots (`online`) and the video
     (`video`).
6. **Done.** Sends `highlight: false` and shows "TV {n} placed. Point your
   camera at the next TV's QR code." when the TV was just added from `/pair`,
   or only "TV {n} placed." in Adjust mode (step 7), plus **Finish**, which
   goes to the Walls page with this wall's editor open. Scanning the next QR
   opens `/pair` in the same browser, so the preselected wall makes it two
   taps. The editor replaces `/pair`'s history entry with
   `/wall-setup?wall=<id>&tv=<n>`, so Back from the editor returns to the
   `/pair` form.
7. **Adjust later.** `/wall-setup?wall=<id>` (reached from **Adjust TVs**)
   opens the same editor; tap a box to select that TV. Each box has **Remove
   TV** (the existing unpair, behind `confirmDialog`). After the last TV is
   removed the editor shows "No TVs in this wall yet. Scan a TV's QR code and
   choose Part of a video wall." with a link back to Video walls; the wall is
   kept, not deleted.
8. **On the Walls page** a free wall's card reads "Set up from a phone ·
   {n} TVs". Its canvas editor hides what a free wall does not use (the item
   duration, Fit/Fill/Stretch, Delete item, Add item, the bezel grid and the
   mode switch) and shows the video's name and **Adjust TVs**.

Error messages reuse the `/pair` mapping (unknown, expired or used code; plan
limit; no permission), plus `wall.full` and `wall.placement_invalid`.

## Section 6 — Media

- **Probe at upload.** `/media/upload` runs `ffprobe` (videos) or Pillow
  (images, with `ffprobe` as the fallback for formats Pillow cannot read, such
  as AVIF) before answering, off the event loop and with a 20 s timeout, and
  stores `width_px`, `height_px` and `duration_seconds`. Sizes are as shown: a
  phone video stored sideways (rotation ±90) and a JPEG with EXIF orientation
  5–8 report width and height swapped. After a transcode finishes, the size and
  length are probed again, because the cap below can change the size, and every
  free wall playing that media is updated (Section 1). A failed probe leaves the
  columns NULL (a failed re-probe keeps the upload's values); the wall then
  falls back to 3840×2160 and a 10 s item.
- **Cap at 3840.** `transcode_video` adds
  `-vf scale='if(gt(iw,ih),min(3840,trunc(iw/2)*2),-2)':'if(gt(iw,ih),-2,min(3840,trunc(ih/2)*2))'`
  right after its input. The long side is also rounded down to even: libx264
  refuses odd sizes, so a 1281×721 video, which failed to transcode before,
  becomes 1280×720.
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
| Code unknown / expired / already used | 404 `pair.code_unknown` / 400 `pair.code_expired` / 409 `pair.code_used`; the phone shows the `/pair` messages; nothing created. |
| Plan limit | 402 before anything is created (one transaction). |
| Two phones claim one TV | The conditional `UPDATE` lets only one win; the other gets 409. |
| Two phones move one box | Last write wins; both editors catch up on their next 3 s read. |
| TV offline while placing | The box is saved; the TV gets it in `hello` when it reconnects. |
| Redis down | Moves arrive through the tick loop within 5 s. |
| TV reloads or reboots | `hello` carries its geometry, so its first frame is already cropped right. |
| Video still transcoding | The TV plays the original until the transcode swaps the file (today's behaviour). The canvas then switches to the transcoded size, with the same aspect; boxes are fractions, so they stay valid. |
| Wall deleted / TV removed | Existing `bye` / `wall_left` handling; a removed TV shows its QR again. |
| TV deleted from the Screens page | Its cell stays, with no screen (a ghost), until the next TV joins the wall, which deletes it. |
| Many TVs waiting behind one NAT | The poll limit (240 per minute per IP) covers 8 TVs polling every 3 s (160 a minute) and the phone's code checks. |
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
- Upload probe: dimensions and duration stored, sideways videos swapped; a
  probe failure leaves NULLs. Transcode cap: a 5760×1080 input comes out
  3840×720 and a 1281×721 one 1280×720; the finished transcode updates the
  canvas and item of free walls playing that media. A standalone video item
  still defaults to 10 s.
- The geometry channel name carries the Redis database number;
  `serialize_wall` returns `online` and `video`; `/tvs` deletes ghost cells
  first; the poll and request-code limits are 240 and 30 per minute.
- The first box is centred on a portrait video; a `/tvs` box takes the
  previous box's height in 16:9; a placement for a TV that has reported its
  size is stored in that TV's shape; every refused placement is 422
  `wall.placement_invalid`; `/walls/free` and `/tvs` allow 30 per minute.
- Every existing test still passes.

### End to end (`scripts/e2e-scan-to-pair/`, extended)

A new `e2e_wall.py` with `run.sh --wall`. The backend image is built from the
branch (`signage_backend:e2e`), with `WEB_CONCURRENCY=4`, so the phone's `PUT`
and a TV's socket usually land on different workers. The run is in loopback
mode: the TVs load the player from `http://127.0.0.1:13022`, a player container
whose `API_BASE_URL` is the local API (`http://127.0.0.1:18020`), and the phone
uses the dashboard at `http://127.0.0.1:13023`. Playwright routing does not
intercept WebSockets, so a TV routed as `https://play.khanshoof.com` would open
its wall socket to production:

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
   `.wall-canvas` transform changes while the finger is down and shows the
   final box within 1 s of the finger stopping (well under the 5 s fallback,
   which shows the Redis path worked). A placement onto the blue third turns
   TV 2's picture blue within 1 s.
5. **Crop:** each TV's screenshot is mostly its own band's colour, with its
   digit in the middle.
6. **Sync:** sampled together, the three TVs' `video.currentTime` values agree
   within 100 ms (the median spread of 12 rounds, each 6 s or more into the
   loop), despite the shifted clocks.
7. **Highlight:** the outline shows while placing and is gone less than 1 s
   after Done (the `highlight: false` placement rides the same Redis path as
   moves).
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
