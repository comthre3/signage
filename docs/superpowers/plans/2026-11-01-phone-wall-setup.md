# Phone-Driven Video Wall Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Track progress outside this file (for example in the SDD ledger), so the working tree stays clean for Task 10's checks.

**Goal:** Set up a Khanshoof video wall from a phone. Scan each TV's pairing QR code and choose "Part of a video wall". Drag that TV's box over the video while the TV follows live, then move to the next TV. All TVs play the video in sync.

**Architecture:** A new *free* wall layout (`walls.layout = 'free'`) stores one box per TV as fractions of the video's canvas. The phone claims each TV's existing standalone pairing code into a new cell, so TVs need no new pairing screen. Each box move is a `PUT`, saved in Postgres and relayed through Redis pub/sub to whichever of the 4 backend workers holds that TV's wall socket. That worker sends a `geometry` frame, and the player re-crops without touching the video. The tick loop re-checks every 5 s as a fallback. The player now scales each slice to fill the screen, which also fixes today's spanned walls. Its clock offset ignores delayed samples. Uploads are probed for size and length, and transcodes are capped at 3840 px.

**Tech Stack:** FastAPI + psycopg 3 (Postgres 16), redis-py 5.2.1 (`redis.asyncio` for the subscriber), ffmpeg/ffprobe 7.1.5 and Pillow in the backend image; a vanilla-JS TV player (Chrome 66 floor); a vanilla-JS dashboard (`feature/friendly-dashboard`); pytest through `scripts/test-backend-in-docker.sh`; Playwright 1.62 (`signage_renderer:latest`); headless Chromium 66.0.3359.0 for the player floor; `node --test` (node 18) for the dashboard's pure geometry.

**Spec:** `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` (Task 1 amends it with the rulings below). Read it before starting.

## Global Constraints

- **Execute tasks strictly in numeric order, 1 → 17.** Later tasks anchor on text earlier tasks leave behind.
- **PR 1 = Tasks 1–10** on branch `feature/phone-wall-setup`, in worktree `/home/ahmed/signage/.claude/worktrees/wall-setup`. PR base: `fix/player-cache-busting`, which is production's player (#22 + #23 + #24).
- **PR 2 = Tasks 11–15 and 17** on branch `feature/phone-wall-setup-dashboard`, in worktree `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. Task 11 cuts it from `feature/friendly-dashboard`. **Task 11 Step 0 is a gate:** the redesign session must agree the hooks first, through the user. Its own Tasks 8, 9 and 11 rewrite `#pair-view`, the Walls editor and `scripts/check_ui_contract.sh`. Never edit the redesign worktree (`.claude/worktrees/friendly-dashboard`) or the old dashboard in the wall-setup worktree's `frontend/`.
- **Task 16** adds the phone scenarios to PR 1's harness. It runs against a local, never-pushed merge of both branches.
- **Every commit block** starts with `cd <that PR's worktree>` and `git branch --show-current`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Nothing is pushed, merged or deployed without the user's explicit OK.** Tasks 10 and 17 end by asking.
- **Long runs get their own Bash call.** Every `scripts/ui/run.sh …` (flows.py, shoot.py, wall_setup_flows.py), `scripts/e2e-scan-to-pair/run.sh …`, Task 8's `run-wallsetup.sh` and Chromium 66 driver, and every pytest run of more than one file takes minutes. Run each as its own Bash call with the timeout set to 600000 ms, or with `run_in_background`, one at a time, and wait for it to finish. Where a step chains several in one block (for example Task 11 Step 2, Task 12 Step 10 and Task 14 Step 7), run them as separate calls in the order given.
- **Player code stays Chrome 66-safe.**
  - JS: no `?.`, `??`, numeric separators, object spread in literals, `flatMap`, `.flat()`, `globalThis`, `Object.fromEntries`, `replaceAll`, `.at()` or `Promise.allSettled`.
  - CSS: no `inset`, flex `gap`, inline `min()`/`max()`, `:is()`/`:where()`, `aspect-ratio`, or separate `scale:`/`translate:` properties. Never a rule that names only `:focus-visible`.
  - The player gets no new shell files.
- **Backend tests run only like this:**
  `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/<file>.py -v`
  The private database and private Redis DB matter: conftest truncates its database, and other sessions share the default one. Before a full run, check that no other pytest container is using this database.
- **Production safety:**
  - Never `docker kill`/`rm` by image or ancestor filter. `signage_backend:latest` is production's image, so never retag it.
  - Test images are `signage_backend:e2e`, `signage_player:e2e-wall` and `signage_frontend:e2e-wall`.
  - End-to-end TVs and the phone load loopback origins only (`http://127.0.0.1:13022` and `:13023`, API `:18020`). Playwright routing never sees WebSockets, so a routed `https://play.khanshoof.com` TV would open its wall socket to production.
  - Never remove a container the plan did not start.
- **walls.py never imports main.** main imports walls at module top. `generate_unique_token` stays a main.py global, because tests monkeypatch it.
- **Dashboard rules:**
  - CSP stays `script-src 'self'`, with no inline handlers. `media-src` has no `blob:`, so there is no preview of a local file before it is uploaded.
  - Each new JS file is one IIFE-returned const (`WallGeometry`, `WallSetup`).
  - `python3 scripts/check_i18n.py` and `bash scripts/check_ui_contract.sh` must pass, with real Arabic for every new string.

## Rulings that refine the spec

Task 1 writes D1–D9 and R1–R4, R7, R8, R10, R11 and R13–R19 into the spec (Step 6). The others (R5, R6, R9, R12 and R20–R23) are about how the code is written or this plan is carried out, and stay here. Tasks cite them as Dn or Rn.

- **D1.** Frames carry `tv_number` (col_index + 1). The player translates it with `wall.tv_label`, "TV {n}" / "شاشة {n}".
- **D2.** Standalone playlist video defaults stay 10 s, even though `media.duration_seconds` now exists.
- **D3.** `_refresh_free_walls_for_media(media_id)` runs after a transcode. It gives free walls the transcoded size and the probed length.
- **D4.** The Redis channel is `khan:wall-geometry:db<N>`, where N is the logical DB in `REDIS_URL`. Tests never reach production's channel.
- **D5.** The `/screens/poll/{code}` limit goes to 240/minute and `/screens/request_code` to 30/minute: up to 8 TVs behind one venue NAT poll every 3 s.
- **D6.** `serialize_wall` adds `online` to each cell, and `video` to free walls.
- **D7.** Before choosing an index, `/tvs` deletes ghost cells (`screen_id IS NULL`).
- **D8.** A free wall's canvas playlist holds exactly one item. Changing the video deletes it and inserts a new one.
- **D9.** The end-to-end test runs in loopback mode.
- **R1.** The first box is centred when it is wider than the video.
- **R2.** Placement fields are plain floats. Every refusal is 422 `wall.placement_invalid`.
- **R3.** A new TV's box has the previous box's height in the default 16:9 shape, flush to its right.
- **R4.** `POST /walls/free` and `POST /walls/{id}/tvs` are limited to 30/minute.
- **R5.** `publish_geometry` and `broadcast_bye` run only after the write has committed.
- **R6.** The screen-report handler keeps short synchronous statements and opens no transactions.
- **R7.** The geometry subscriber starts only for free-wall sockets.
- **R8.** The server stores the box in the TV's reported shape. It validates the phone's request first, then fits it.
- **R9.** The player ends the highlight with its own timer, measured on the server's clock.
- **R10.** The sync bound is a 100 ms median: the median spread of 12 rounds, each 6 s or more into the loop.
- **R11.** After Done, the outline is off in under 1 s.
- **R12.** This plan is committed to PR 1's branch.
- **R13.** `/pair` preselects "Part of a video wall" when the phone remembers a wall from the last 30 minutes. `WallSetup` exports `{ show, startFromPair, reset, recentWallId }`.
- **R14.** On the Walls page, a free wall's editor hides item duration, fit, add and delete, the bezel grid and the mode switch. The card reads "Set up from a phone · {n} TVs".
- **R15.** The phone checks the TV code before uploading or creating.
- **R16.** Finish opens the Walls page with that wall's editor.
- **R17.** Done reads "TV {n} placed. Point your camera at the next TV's QR code." after adding a TV, and only "TV {n} placed." in Adjust mode.
- **R18.** An empty wall shows how to add a TV. It is not deleted automatically.
- **R19.** `startFromPair` pushes one `/pair` history entry, and `openEditor` replaces it with `/wall-setup?wall=<id>&tv=<n>`.
- **R20.** Pushing `feature/friendly-dashboard` (not on origin) is the user's call.
- **R21.** Task 16's harness commit lands on PR 1's branch. Pushing it is covered by Task 17's question.
- **R22.** Grid walls see no frame changes, only the player's scaling fix.
- **R23.** Scratch container names are never removed unless the plan started them.

## File map

| Area | Files | Tasks |
|---|---|---|
| Schema | `backend/db.py` | 1 |
| Media probe and transcode cap | `backend/media_probe.py` (new), `backend/main.py` (`upload_media`, `transcode_video`, `_default_duration_seconds`) | 2 |
| Box geometry, pure | `backend/wall_layout.py` (new) | 3 |
| TV binding, free walls, placement, video, guards, limits | `backend/main.py` (walls section, pairing limits) | 4, 5, 7 |
| Live geometry: hello, frames, Redis relay, screen reports | `backend/walls.py` | 6 |
| Player | `player/player.js`, `player/styles.css`, `player/index.html`, `player/i18n/{en,ar}.json` | 8 |
| Player test harness (not committed) | `/home/ahmed/signage-harness/{mockharness,chrome66}` (copied from the planning scratchpad in Task 1 Step 0) | 1, 8, 10 |
| Hand-off files (not committed) | `/home/ahmed/signage-harness/handoff/` (test logs and the PR bodies, outside `/tmp`) | 9, 10, 16, 17 |
| End-to-end | `scripts/e2e-scan-to-pair/run.sh`, `scripts/e2e-scan-to-pair/e2e_wall.py` (new) | 9, 16 |
| Dashboard geometry, pure | `frontend/wall-geometry.js` (new), `frontend/tests/wall-geometry.test.js` (new), `frontend/.dockerignore` (new) | 11 |
| Dashboard wall setup | `frontend/wall-setup.js` (new), `frontend/wall-setup.css` (new), `frontend/index.html`, `frontend/app.js`, `frontend/i18n/{en,ar}.json`, `scripts/check_ui_contract.sh`, `scripts/ui/wall_setup_flows.py` (new) | 12–15 |
| Tests (backend) | `tests/test_walls_free_schema.py`, `tests/test_media_probe.py`, `tests/test_media_upload_probe.py`, `tests/test_wall_layout.py`, `tests/test_wall_tv_binding.py`, `tests/test_walls_free.py`, `tests/test_walls_live.py` (all new, under `backend/`) | 1–7 |

---

## Part A — PR 1: backend, player, end-to-end harness (Tasks 1–10)

### Task 1: Schema columns and spec amendments

All work happens in the PR 1 worktree `/home/ahmed/signage/.claude/worktrees/wall-setup` (branch
`feature/phone-wall-setup`). Paths below are relative to it.

**Files:**
- Modify: `backend/db.py:487` (insert 16 lines after it, inside `_init_db_locked`, right after the
  `playlist_items ... fit_mode` ALTER and before the `# First-run setup.` comment at line 488)
- Modify: `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` (lines 4, 54-56, 81-96,
  112-113, 116-118, 123, 131-135, 141-144, 147-149, 159-161, 170-172, 179, 190-196, 215, 219-221,
  255-256, 269, 290-291, 293-294, 299, 311-318, 325-332, 344, 351-353, 373-374, 379-381, 392-394,
  397-399)
- Test: `backend/tests/test_walls_free_schema.py` (new)
- Commit: `docs/superpowers/plans/2026-11-01-phone-wall-setup.md` (this plan, ruling R12; Step 8)
- Copy (not committed): the player test harness to `/home/ahmed/signage-harness` (Step 0, with Task 8
  Step 0's block)

**Interfaces:**
- Consumes: `db.init_db()`, `db.execute`, `db.query_one`, `db.utc_now_iso` (existing).
- Produces (later tasks rely on these columns existing after `init_db()`):
  - `walls.layout TEXT NOT NULL DEFAULT 'grid' CHECK (layout IN ('grid','free'))`
  - `wall_cells.place_x/place_y/place_w/place_h REAL` (NULL = no box yet)
  - `wall_cells.screen_w_px/screen_h_px INTEGER`, `wall_cells.highlight_until_ms BIGINT`
  - `media.duration_seconds REAL`, `media.width_px INTEGER`, `media.height_px INTEGER`
  - The amended spec text (D1-D9 and the review rulings R1-R4, R7, R8, R10, R11, R13-R19) that every
    later task's wording follows.
  - The plan itself committed on `feature/phone-wall-setup` (R12), which Task 10 lists among the
    changed files.

- [ ] **Step 0: Save the player harness before anything else**

Run Task 8 Step 0's bash block now, unchanged, as its own Bash call. It copies the mock harness and the
Chromium 66 tools (the `mockharness/…` and `chrome66/…` items it lists) from the planning scratchpad to
`/home/ahmed/signage-harness`, only what is missing there. The scratchpad is under `/tmp`, which an LXC
restart wipes, and Tasks 1-7 take hours. Expected: `harness ready in /home/ahmed/signage-harness` (about
280M). On a `MISSING` line, stop and ask the user.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_walls_free_schema.py`:

```python
"""Schema for phone-placed (free-layout) video walls and probed media sizes.

All additive ALTERs in db.init_db: free walls (walls.layout), each TV's box,
reported screen size and highlight (wall_cells.place_* / screen_*_px /
highlight_until_ms), and media size and length (media.width_px / height_px /
duration_seconds).
"""
import secrets

import psycopg
import pytest

from db import execute, init_db, query_one, utc_now_iso


def _column(table: str, column: str) -> dict | None:
    return query_one(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ? AND column_name = ?",
        (table, column),
    )


def _org() -> int:
    slug = "fs" + secrets.token_hex(3)
    return execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"FS {slug}", slug, "active", 10, utc_now_iso()),
    )


def _insert_wall(oid: int, **extra) -> int:
    cols = ["organization_id", "name", "mode", "rows", "cols", "created_at", "updated_at"]
    vals = [oid, "W", "spanned", 1, 1, utc_now_iso(), utc_now_iso()]
    for key, value in extra.items():
        cols.append(key)
        vals.append(value)
    placeholders = ", ".join("?" for _ in cols)
    return execute(
        f"INSERT INTO walls ({', '.join(cols)}) VALUES ({placeholders})", tuple(vals),
    )


@pytest.mark.parametrize("table,column,data_type", [
    ("wall_cells", "place_x", "real"),
    ("wall_cells", "place_y", "real"),
    ("wall_cells", "place_w", "real"),
    ("wall_cells", "place_h", "real"),
    ("wall_cells", "screen_w_px", "integer"),
    ("wall_cells", "screen_h_px", "integer"),
    ("wall_cells", "highlight_until_ms", "bigint"),
    ("media", "duration_seconds", "real"),
    ("media", "width_px", "integer"),
    ("media", "height_px", "integer"),
])
def test_new_columns_are_nullable_with_no_default(table, column, data_type):
    col = _column(table, column)
    assert col is not None, f"{table}.{column} is missing"
    assert col["data_type"] == data_type
    assert col["is_nullable"] == "YES"
    assert col["column_default"] is None


def test_walls_layout_is_text_not_null_defaulting_to_grid():
    col = _column("walls", "layout")
    assert col is not None, "walls.layout is missing"
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "NO"
    assert col["column_default"] == "'grid'::text"


def test_a_wall_inserted_without_layout_is_a_grid_wall():
    wid = _insert_wall(_org())
    assert query_one("SELECT layout FROM walls WHERE id = ?", (wid,))["layout"] == "grid"


def test_a_wall_can_be_free_with_any_canvas_size():
    wid = _insert_wall(_org(), layout="free", canvas_width_px=3840, canvas_height_px=720)
    row = query_one(
        "SELECT layout, canvas_width_px, canvas_height_px FROM walls WHERE id = ?", (wid,),
    )
    assert row == {"layout": "free", "canvas_width_px": 3840, "canvas_height_px": 720}


def test_layout_check_rejects_other_values():
    oid = _org()
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_wall(oid, layout="other")
    n = query_one("SELECT COUNT(*) AS n FROM walls WHERE organization_id = ?", (oid,))["n"]
    assert n == 0


def test_cell_box_screen_size_and_highlight_round_trip():
    wid = _insert_wall(_org(), layout="free")
    cid = execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
        "place_h, screen_w_px, screen_h_px, highlight_until_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        # 1762000030000 needs BIGINT: it is past 2**31.
        (wid, 0, 0, 0.25, -0.5, 0.5, 2.0, 960, 540, 1762000030000, utc_now_iso()),
    )
    cell = query_one(
        "SELECT place_x, place_y, place_w, place_h, screen_w_px, screen_h_px, "
        "highlight_until_ms FROM wall_cells WHERE id = ?", (cid,),
    )
    assert cell == {
        "place_x": 0.25, "place_y": -0.5, "place_w": 0.5, "place_h": 2.0,
        "screen_w_px": 960, "screen_h_px": 540, "highlight_until_ms": 1762000030000,
    }


def test_media_size_and_length_round_trip():
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "pano.mp4", "pano.mp4", "video/mp4", 100, utc_now_iso(), 5760, 1080, 20.5),
    )
    row = query_one(
        "SELECT width_px, height_px, duration_seconds FROM media WHERE id = ?", (mid,),
    )
    assert row == {"width_px": 5760, "height_px": 1080, "duration_seconds": 20.5}


def test_init_db_twice_keeps_one_layout_check():
    init_db()
    init_db()
    n = query_one(
        "SELECT COUNT(*) AS n FROM pg_constraint "
        "WHERE conrelid = 'walls'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) LIKE ?",
        ("%layout%",),
    )["n"]
    assert n == 1
    assert _column("walls", "layout")["column_default"] == "'grid'::text"
```

- [ ] **Step 2: Run it and watch it fail**

Before running anything, check no other session's pytest container uses this DB:
`docker ps --filter "ancestor=signage_backend:latest" --format '{{.Names}} | {{.Command}}'`
(only look; never kill by this filter, it lists production's `signage_backend_1`).

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free_schema.py -v`
Expected: FAIL, `17 failed`. The 10 parametrized column tests and
`test_walls_layout_is_text_not_null_defaulting_to_grid` fail with
`AssertionError: <table>.<column> is missing`; `test_a_wall_inserted_without_layout_is_a_grid_wall`
fails with `psycopg.errors.UndefinedColumn: column "layout" does not exist` on its `SELECT`;
`test_a_wall_can_be_free_with_any_canvas_size`, `test_layout_check_rejects_other_values` (it gets
`UndefinedColumn` instead of `CheckViolation`) and `test_cell_box_screen_size_and_highlight_round_trip`
fail with `UndefinedColumn: column "layout" of relation "walls" does not exist`;
`test_media_size_and_length_round_trip` fails with `UndefinedColumn: column "width_px" of relation
"media" does not exist`; `test_init_db_twice_keeps_one_layout_check` fails on `assert 0 == 1`.

- [ ] **Step 3: Implement**

In `backend/db.py`, replace this exact text (lines 487-488):

```python
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN IF NOT EXISTS fit_mode TEXT NOT NULL DEFAULT 'fit' CHECK (fit_mode IN ('fit','fill','stretch'))")
        # First-run setup. A deployment with no users publishes a one-time token
```

with:

```python
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN IF NOT EXISTS fit_mode TEXT NOT NULL DEFAULT 'fit' CHECK (fit_mode IN ('fit','fill','stretch'))")
        # Phone-placed video walls. A 'free' wall (layout) is a spanned wall whose
        # TVs each have a box over the video, stored as fractions of the canvas
        # (place_*); the TV reports its own viewport size (screen_*_px), and an
        # outline shows on it until highlight_until_ms (server clock, ms). Media
        # rows get the probed size and length, at upload and after transcoding.
        # IF NOT EXISTS skips the whole ADD COLUMN, CHECK included, once the
        # column exists, so a second init_db adds no second constraint.
        cursor.execute("ALTER TABLE walls          ADD COLUMN IF NOT EXISTS layout TEXT NOT NULL DEFAULT 'grid' CHECK (layout IN ('grid','free'))")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS place_x REAL")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS place_y REAL")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS place_w REAL")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS place_h REAL")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS screen_w_px INTEGER")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS screen_h_px INTEGER")
        cursor.execute("ALTER TABLE wall_cells     ADD COLUMN IF NOT EXISTS highlight_until_ms BIGINT")
        cursor.execute("ALTER TABLE media          ADD COLUMN IF NOT EXISTS duration_seconds REAL")
        cursor.execute("ALTER TABLE media          ADD COLUMN IF NOT EXISTS width_px INTEGER")
        cursor.execute("ALTER TABLE media          ADD COLUMN IF NOT EXISTS height_px INTEGER")
        # First-run setup. A deployment with no users publishes a one-time token
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free_schema.py tests/test_walls_crud.py tests/test_walls_phase2.py tests/test_fresh_install.py -v`
Expected: PASS (`test_walls_free_schema.py`: 17 passed; the existing schema/CRUD tests in
`test_walls_crud.py`, `test_walls_phase2.py` and `test_fresh_install.py` still pass).

- [ ] **Step 5: Commit the schema**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/db.py backend/tests/test_walls_free_schema.py
git commit -m "feat(walls): schema for phone-placed walls and probed media size

walls.layout ('grid' | 'free'), per-cell box (place_*), reported TV size
(screen_*_px) and highlight deadline, and media width/height/duration.
All additive ADD COLUMN IF NOT EXISTS; existing walls become 'grid'.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Amend the spec to the plan's decisions (D1-D9) and review rulings**

Edit `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md`. Each edit replaces one exact
block of the current text (every "Replace" block occurs exactly once in the file at the point it is
applied; apply them in this order, S1 to S34, with the Edit tool, `replace_all` off). S24-S34 change
text that S1-S23 leave alone.

**Edit S1** (status line, line 4). Replace:

```text
**Status:** Approved in chat 2026-11-01; awaiting review of this written spec.
```

With:

```text
**Status:** Approved in chat 2026-11-01. Amended the same day by the implementation
plan (see "Plan amendments" under the decisions); the sections below already read
as amended.
```

**Edit S2** (new "Plan amendments" table after decision 12, lines 54-56). Replace:

```text
| 12 | Video size | Every video transcode caps the long side at 3840 px, so TV hardware decoders can play wall panoramas. |

---
```

With:

```text
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
```

**Edit S3** (Section 1, canvas bullet, lines 81-84). Replace:

```text
- Its canvas is its video: `canvas_width_px × canvas_height_px` are the video's
  probed size after transcoding. Boxes are stored as fractions of the canvas, so
  replacing the video with one of another size keeps every box in the same
  relative place.
```

With:

```text
- Its canvas is its video: `canvas_width_px × canvas_height_px` are the video's
  probed size after transcoding. A wall is usually created while its video is
  still transcoding, so it starts with the size probed at upload; when the
  transcode finishes, `_refresh_free_walls_for_media` sets every free wall
  playing that media to the transcoded size, and its item to the probed length.
  The aspect is unchanged, so no box moves. Boxes are stored as fractions of the
  canvas, so replacing the video with one of another size keeps every box in the
  same relative place.
```

**Edit S4** (Section 1, TV number, lines 87-89). Replace:

```text
  least 1), so code that walks `range(cols)` still finds every cell. Removing a TV
  deletes its cell. A TV's label is "TV {col_index + 1}", which stays the same
  while it is in the wall.
```

With:

```text
  least 1), so code that walks `range(cols)` still finds every cell. Removing a TV
  deletes its cell. A TV's number is `col_index + 1`, which stays the same while
  it is in the wall. Frames carry the number (`tv_number`), not text, so each TV
  shows it in its own language ("TV 2", "شاشة 2"). Its screen row is named
  "{wall name} · TV {n}".
```

**Edit S5** (Section 1, canvas playlist and the forward hook, lines 91-96). Replace:

```text
- Its canvas playlist (`kind = 'wall_canvas'`, as today) holds the video as one
  item with `fit_mode = 'fit'` and `duration_seconds` = the probed length.

`media.duration_seconds` feeds the existing forward hook in
`_default_duration_seconds`, but only wall items use it in this change; standalone
playlist defaults are not changed.
```

With:

```text
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
```

**Edit S6** (`POST /walls/free` step 1, lines 112-113). Replace:

```text
1. Check the TV code exactly as `/screens/claim` does (unknown 404, expired 400,
   already paired 409) and the media (own org, `image/*` or `video/*`).
```

With:

```text
1. Check the TV code (unknown 404 `pair.code_unknown`, already paired 409
   `pair.code_used`, expired 400 `pair.code_expired`) and the media (own org,
   else 404 `media.not_found`; `image/*` or `video/*`, else 400
   `wall.media_type_blocked`).
```

**Edit S7** (`POST /walls/free` result, line 123). Replace:

```text
Returns the wall with its cells (`serialize_wall`).
```

With:

```text
Rate limit `30/minute`. Returns 201 with the wall and its cells (`serialize_wall`).
```

**Edit S8** (`POST /walls/{wall_id}/tvs`, lines 131-135). Replace:

```text
Adds the TV into the next cell of a free wall. Its first box has the size of
the most recently added TV's box, flush to that box's right edge and top-aligned.
If that would leave less than 5% of it on the canvas, it is moved left until 5%
overlaps. 409 `wall.full` at 8 TVs; 409 `wall.not_free` on
a grid wall. Returns the new cell.
```

With:

```text
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
```

**Edit S9** (placement highlight, lines 147-149). Replace:

```text
- `highlight` (default true) sets `highlight_until_ms` to now + 30 s, which
  shows the outline and label on the TV. `false` clears it; the phone sends
  `false` on **Done**.
```

With:

```text
- `highlight` (default true) sets `highlight_until_ms` to now + 30 s, which
  shows the outline and the TV's number on the TV. `false` clears it; the phone
  sends `false` on **Done**.
```

**Edit S10** (`PUT /walls/{wall_id}/video`, lines 159-161). Replace:

```text
Replaces a free wall's video: the canvas size follows the new media, the boxes
keep their fractions, and the canvas playlist item is swapped. The tick loop's
existing config check re-sends `hello` to the TVs.
```

With:

```text
Replaces a free wall's video: the canvas size follows the new media, the boxes
keep their fractions, and the one canvas playlist item is deleted and inserted
again for the new media. The new item id starts a new play at once, even when
the new video has the same size and length; a size change also makes the tick
loop's config check re-send `hello`. Returns the wall (`serialize_wall`).
```

**Edit S11** (changed endpoints, lines 170-172). Replace:

```text
- `GET /walls`, `GET /walls/{id}`: also return `layout` and the new cell
  columns (they come from `SELECT *` already).
- `POST /media/upload`: probes the file before answering (Section 6).
```

With:

```text
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
```

**Edit S12** (binding helper signature, line 179). Replace:

```text
`_bind_screen_to_cell(conn, wall, cell, now)`, used by `redeem_wall_cell`,
```

With:

```text
`_bind_screen_to_cell(wall, cell, org, now, name)`, used by `redeem_wall_cell`,
```

**Edit S13** (Section 3, publish, lines 190-194). Replace:

```text
- **Publish.** After saving a box, the `PUT` handler publishes
  `{"wall_id", "row", "col"}` ("this cell changed") on Redis channel
  `khan:wall-geometry`, using
  the existing client in `ratelimit.get_redis()`. A publish failure is logged
  and does not fail the request.
```

With:

```text
- **Publish.** After saving a box, the `PUT` handler publishes
  `{"wall_id", "row", "col"}` ("this cell changed") on Redis channel
  `khan:wall-geometry:db<N>`, where N is the logical database number in
  `REDIS_URL` (0 in production). Pub/sub ignores logical databases, and the test
  suite uses production's Redis server with another database number, so the
  suffix keeps test publishes out of production's channel. It uses the existing
  client in `ratelimit.get_redis()`. A publish failure is logged and does not
  fail the request. When the worker answering the `PUT` holds the TV's socket
  itself, it also sends the frame straight away.
```

**Edit S14** (geometry frame example, line 215). Replace:

```text
  "highlight_until_ms": 1762000030000, "label": "TV 2",
```

With:

```text
  "highlight_until_ms": 1762000030000, "tv_number": 2,
```

**Edit S15** (`hello` for free walls, lines 219-221). Replace:

```text
`hello` for a free wall carries `canvas` and `cell_geometry` from the stored box
(converted to canvas px), plus `highlight_until_ms` (null when off) and `label`.
For grid walls `hello` is unchanged.
```

With:

```text
`hello` for a free wall carries `canvas` and `cell_geometry` from the stored box
(converted to canvas px), `bezel` of zeros, plus `highlight_until_ms` (null when
off) and `tv_number`. For grid walls `hello` is unchanged.
```

**Edit S16** (Section 4, highlight, lines 255-256). Replace:

```text
3. **Highlight.** A fixed outline around the screen, with the label ("TV 2")
   centred in large type. It is shown until `highlight_until_ms`, measured on
```

With:

```text
3. **Highlight.** A fixed outline around the screen, with the TV's number
   (`wall.tv_label` filled with the frame's `tv_number`: "TV 2", "شاشة 2")
   centred in large type. It is shown until `highlight_until_ms`, measured on
```

**Edit S17** (Section 4, strings, line 269). Replace:

```text
Strings (EN + AR in `player/i18n`): `wall.tv_label` = "TV {n}" / "شاشة {n}".
```

With:

```text
Strings (EN + AR in `player/i18n`): `wall.tv_label` = "TV {n}" / "شاشة {n}",
where `{n}` is the frame's `tv_number`.
```

**Edit S18** (Section 5, editor refresh, line 311). Replace:

```text
   - The editor re-reads the wall every 3 s, to pick up TV sizes and online dots.
```

With:

```text
   - The editor re-reads the wall every 3 s (`GET /walls/{id}`), to pick up TV
     sizes (`screen_w_px`, `screen_h_px`), online dots (`online`) and the video
     (`video`).
```

**Edit S19** (Section 6, probe and cap, lines 325-332). Replace:

```text
- **Probe at upload.** `/media/upload` runs `ffprobe` (videos) or Pillow
  (images) before answering, with a 20 s timeout, and stores `width_px`,
  `height_px` and `duration_seconds`. After a transcode finishes, the size is
  re-probed, because the cap below can change it. A failed probe leaves the
  columns NULL; the wall then falls back to 3840×2160 and a 10 s item.
- **Cap at 3840.** `transcode_video` adds
  `-vf scale='if(gt(iw,ih),min(3840,iw),-2)':'if(gt(iw,ih),-2,min(3840,ih))'`.
```

With:

```text
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
```

**Edit S20** (Section 7, code errors, line 344). Replace:

```text
| Code unknown / expired / already used | 404 / 400 / 409; the phone shows the `/pair` messages; nothing created. |
```

With:

```text
| Code unknown / expired / already used | 404 `pair.code_unknown` / 400 `pair.code_expired` / 409 `pair.code_used`; the phone shows the `/pair` messages; nothing created. |
```

**Edit S21** (Section 7, transcoding, ghost cells, NAT, lines 351-353). Replace:

```text
| Video still transcoding | The TV plays the original until the transcode swaps the file (today's behaviour). The canvas keeps the probed aspect; boxes are fractions, so they stay valid. |
| Wall deleted / TV removed | Existing `bye` / `wall_left` handling; a removed TV shows its QR again. |
| 9th TV | 409 `wall.full`. |
```

With:

```text
| Video still transcoding | The TV plays the original until the transcode swaps the file (today's behaviour). The canvas then switches to the transcoded size, with the same aspect; boxes are fractions, so they stay valid. |
| Wall deleted / TV removed | Existing `bye` / `wall_left` handling; a removed TV shows its QR again. |
| TV deleted from the Screens page | Its cell stays, with no screen (a ghost), until the next TV joins the wall, which deletes it. |
| Many TVs waiting behind one NAT | The poll limit (240 per minute per IP) covers 8 TVs polling every 3 s (160 a minute) and the phone's code checks. |
| 9th TV | 409 `wall.full`. |
```

**Edit S22** (Section 8, backend tests, lines 373-374). Replace:

```text
- Upload probe: dimensions and duration stored; a probe failure leaves NULLs.
  Transcode cap: a 5760×1080 input comes out 3840×720.
```

With:

```text
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
```

**Edit S23** (Section 8, end to end, lines 379-381). Replace:

```text
A new `e2e_wall.py` with `run.sh --wall`. The backend image is built from the
branch (`BACKEND_IMAGE`), with `WEB_CONCURRENCY=4`, so the phone's `PUT` and a
TV's socket usually land on different workers:
```

With:

```text
A new `e2e_wall.py` with `run.sh --wall`. The backend image is built from the
branch (`signage_backend:e2e`), with `WEB_CONCURRENCY=4`, so the phone's `PUT`
and a TV's socket usually land on different workers. The run is in loopback
mode: the TVs load the player from `http://127.0.0.1:13022`, a player container
whose `API_BASE_URL` is the local API (`http://127.0.0.1:18020`), and the phone
uses the dashboard at `http://127.0.0.1:13023`. Playwright routing does not
intercept WebSockets, so a TV routed as `https://play.khanshoof.com` would open
its wall socket to production:
```

**Edit S24** (R1: `POST /walls/free` step 3, first box, line 118). Replace:

```text
   TV reports). On a 3840×720 panorama that is exactly the left third.
```

With:

```text
   TV reports). On a 3840×720 panorama that is exactly the left third. A box
   wider than the video (a portrait video on a 16:9 TV) is centred on it
   instead, x = (1 − w) / 2; one that would be more than 4 video widths wide
   (a very tall video) is first shrunk to 4, keeping its shape.
```

**Edit S25** (R2, R8: placement bounds and the TV's shape, line 144). Replace:

```text
  area; otherwise 422 `wall.placement_invalid`.
```

With:

```text
  area; otherwise 422 `wall.placement_invalid` (every refused box gets that
  code). When the TV has reported its screen size, the stored box is the
  requested box reshaped to that screen (same width and centre), so the TV's
  shape wins over a phone that has not seen it yet.
```

**Edit S26** (R7: subscriber only for free walls, line 196). Replace:

```text
  first time a wall socket connects to it. For each message about a socket it
```

With:

```text
  first time a free wall's socket connects to it (grid walls never start it).
  For each message about a socket it
```

**Edit S27** (R13: "What is this TV?", lines 290-291). Replace:

```text
   choices: **Its own screen** (today's form, unchanged) and **Part of a video
   wall**.
```

With:

```text
   choices: **Its own screen** (today's form, unchanged) and **Part of a video
   wall**. When this phone remembers a wall from the last 30 minutes (step 2),
   **Part of a video wall** is preselected, so joining that wall takes two taps
   after the code: **Continue**, then **Add this TV**.
```

**Edit S28** (R15: code check before uploading, line 299). Replace:

```text
   progress). Then `POST /walls/free`.
```

With:

```text
   progress). Before uploading or creating anything, the phone checks the TV
   code with the anonymous `GET /screens/poll/{code}`, as `/pair` does; a
   rejected code shows the `/pair` message and stops. Then `POST /walls/free`.
```

**Edit S29** (R16, R17, R19: Done and Finish, lines 312-315). Replace:

```text
6. **Done.** Sends `highlight: false` and shows "TV {n} placed. Point your
   camera at the next TV's QR code", plus **Finish**, which goes to the wall's
   page. Scanning the next QR opens `/pair` in the same browser, so the
   preselected wall makes it two taps.
```

With:

```text
6. **Done.** Sends `highlight: false` and shows "TV {n} placed. Point your
   camera at the next TV's QR code." when the TV was just added from `/pair`,
   or only "TV {n} placed." in Adjust mode (step 7), plus **Finish**, which
   goes to the Walls page with this wall's editor open. Scanning the next QR
   opens `/pair` in the same browser, so the preselected wall makes it two
   taps. The editor replaces `/pair`'s history entry with
   `/wall-setup?wall=<id>&tv=<n>`, so Back from the editor returns to the
   `/pair` form.
```

**Edit S30** (R18, R14: the empty wall and the Walls page, lines 317-318). Replace:

```text
   opens the same editor; tap a box to select that TV. Each box has **Remove
   TV** (the existing unpair, behind `confirmDialog`).
```

With:

```text
   opens the same editor; tap a box to select that TV. Each box has **Remove
   TV** (the existing unpair, behind `confirmDialog`). After the last TV is
   removed the editor shows "No TVs in this wall yet. Scan a TV's QR code and
   choose Part of a video wall." with a link back to Video walls; the wall is
   kept, not deleted.
8. **On the Walls page** a free wall's card reads "Set up from a phone ·
   {n} TVs". Its canvas editor hides what a free wall does not use (the item
   duration, Fit/Fill/Stretch, Delete item, Add item, the bezel grid and the
   mode switch) and shows the video's name and **Adjust TVs**.
```

**Edit S31** (R11: end-to-end highlight bound, line 399). Replace:

```text
7. **Highlight:** the outline shows while placing and is gone after Done.
```

With:

```text
7. **Highlight:** the outline shows while placing and is gone less than 1 s
   after Done (the `highlight: false` placement rides the same Redis path as
   moves).
```

**Edit S32** (R10: end-to-end sync bound, lines 397-398). Replace:

```text
6. **Sync:** sampled together, the three TVs' `video.currentTime` values agree
   within 100 ms, despite the shifted clocks.
```

With:

```text
6. **Sync:** sampled together, the three TVs' `video.currentTime` values agree
   within 100 ms (the median spread of 12 rounds, each 6 s or more into the
   loop), despite the shifted clocks.
```

**Edit S33** (R13: where the phone remembers the wall, lines 293-294). Replace:

```text
   phone set up or joined in the last 30 minutes (remembered in
   `sessionStorage`) is preselected. There is also **New video wall**.
```

With:

```text
   phone set up or joined in the last 30 minutes (remembered in
   `sessionStorage` and `localStorage`, since the camera app usually opens the
   next QR's link in a new tab) is preselected. There is also **New video wall**.
```

**Edit S34** (end-to-end live drag, lines 392-394). Replace:

```text
4. **Live drag:** during TV 2's placement the phone drags the box, and TV 2's
   `.wall-canvas` transform and centre pixel colour change within 1 s (well
   under the 5 s fallback, which shows the Redis path worked).
```

With:

```text
4. **Live drag:** during TV 2's placement the phone drags the box, and TV 2's
   `.wall-canvas` transform changes while the finger is down and shows the
   final box within 1 s of the finger stopping (well under the 5 s fallback,
   which shows the Redis path worked). A placement onto the blue third turns
   TV 2's picture blue within 1 s.
```

Check the result: `grep -n '"label"\|khan:wall-geometry`\|forward hook in\|min(3840,iw)\|has the size of\|120\|first time a wall socket' docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md`
Expected: no output (every old phrase is gone). `grep -c 'tv_number' docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` prints at least 6; `grep -c '^| R[0-9]' docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` prints 15 (the R rows of the amendments table); `grep -c '30/minute' docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` prints 2.

- [ ] **Step 7: Commit the spec amendments**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md
git commit -m "docs(walls): amend the wall-setup spec with the plan's decisions

tv_number instead of an English label, standalone video defaults kept,
free walls follow the transcoded size, per-DB geometry channel, higher
poll limits, online/video in serialize_wall, ghost cells, one-item canvas
playlist, loopback e2e, and an even-size transcode filter.

Review rulings: a first box wider than the video is centred, a new TV's
box keeps the previous height in 16:9, the TV's reported shape wins in
the placement PUT, every refused box is wall.placement_invalid, 30/minute
on /walls/free and /tvs, the subscriber runs only for free walls, and
the phone flow (preselected wall, code check, Done texts, Finish, empty
wall, Walls page card and editor, history), the remembered wall kept in
localStorage too, and the acceptance checks for sync (a median of 12
rounds) and the live drag.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Commit the plan (ruling R12)**

The plan you are executing (this document) belongs on PR 1's branch at
`docs/superpowers/plans/2026-11-01-phone-wall-setup.md`. If it is not saved there yet, copy the plan file you were handed there byte for byte, for example
`mkdir -p docs/superpowers/plans && cp /tmp/claude-1000/-home-ahmed-signage/39cbf124-cc26-4c98-9af8-19a9e47924c9/scratchpad/plan/plan-assembled.md docs/superpowers/plans/2026-11-01-phone-wall-setup.md`
when that is the file you were given. Never re-type or summarise it. Track progress in your task list rather than by
ticking boxes in this file, so the committed copy stays as written and Task 10 finds a clean tree.

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
test -s docs/superpowers/plans/2026-11-01-phone-wall-setup.md && grep -c '^### Task ' docs/superpowers/plans/2026-11-01-phone-wall-setup.md
```

Expected: `17` (Tasks 1 to 17). Any other output means the file is missing or incomplete; copy the
plan file there again, byte for byte, and run the check again. Then:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add docs/superpowers/plans/2026-11-01-phone-wall-setup.md
git commit -m "docs(walls): implementation plan for phone wall setup

Seventeen tasks: PR 1 (schema, media probe, layout helpers, free-wall
API, live placement, player, e2e) and PR 2 (the phone's dashboard flow).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git log --oneline -1 -- docs/superpowers/plans/2026-11-01-phone-wall-setup.md
```

Expected: the last line shows this commit.

---

---

### Task 2: Probe media at upload; cap transcodes at 3840

**Files:**
- Create: `backend/media_probe.py`
- Modify: `backend/main.py:35` (add `import media_probe` above `import menu_render`)
- Modify: `backend/main.py:47-98` (`transcode_video`: `-vf` after `-i`, re-probe, COALESCE update)
- Modify: `backend/main.py:4192-4208` (`_default_duration_seconds`: forward hook removed, D2)
- Modify: `backend/main.py:4604-4624` (`upload_media`: probe off the loop, 9-column INSERT,
  `_media_class(...) == "video"`)
- Test: `backend/tests/test_media_probe.py` (new; pure probe tests)
- Test: `backend/tests/test_media_upload_probe.py` (new; upload/transcode/default-duration tests)

(Line numbers are before this task's edits; the import adds one line, so later anchors move by +1.
Every edit below is anchored on exact current text, not on line numbers.)

**Interfaces:**
- Consumes: Task 1 columns `media.width_px INTEGER`, `media.height_px INTEGER`,
  `media.duration_seconds REAL`; existing `main._media_class(mime) -> 'pdf'|'image'|'video'|None`,
  `main.transcode_video(input_path, media_id)`, `main.UPLOAD_DIR`, `anyio` (main.py:28).
- Produces:
  - `media_probe.PROBE_TIMEOUT_SECONDS = 20`, `media_probe.VIDEO_LONG_SIDE_MAX = 3840`,
    `media_probe.TRANSCODE_SCALE_FILTER = "scale='if(gt(iw,ih),min(3840,trunc(iw/2)*2),-2)':'if(gt(iw,ih),-2,min(3840,trunc(ih/2)*2))'"`
  - `media_probe.probe_media(path: str, mime: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict`
    returning `{"width_px": int|None, "height_px": int|None, "duration_seconds": float|None}`, never raises.
  - `POST /media/upload` rows (and its response) carry `width_px`, `height_px`, `duration_seconds`.
  - `transcode_video` leaves the row with the transcoded file's size/length (COALESCE keeps
    upload-time values when the re-probe fails). Task 5 adds `_refresh_free_walls_for_media(media_id)`
    right after the final `logger.info("Transcoded media %s → %s (%d bytes)", ...)` line of
    `transcode_video` (its success path, after the input-file cleanup), so 7b keeps that line exactly
    as it is today, as the function's last line.
  - `_default_duration_seconds({"mime_type": "video/...", ...})` returns 10 regardless of
    `duration_seconds` (D2).

- [ ] **Step 1: Write the failing probe tests**

Create `backend/tests/test_media_probe.py`:

```python
"""media_probe: pixel size and length of uploaded images and videos.

ffmpeg/ffprobe are in the backend image; tests that need them generate their
input at test time and skip if either binary is missing.
"""
import shutil
import subprocess

import pytest

import media_probe

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)

NULLS = {"width_px": None, "height_px": None, "duration_seconds": None}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _make_video(path, size: str, *, seconds: float = 1, pix_fmt: str = "yuv420p") -> str:
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=10", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, str(path))
    return str(path)


def test_constants_match_the_contract():
    assert media_probe.PROBE_TIMEOUT_SECONDS == 20
    assert media_probe.VIDEO_LONG_SIDE_MAX == 3840
    assert media_probe.TRANSCODE_SCALE_FILTER == (
        "scale='if(gt(iw,ih),min(3840,trunc(iw/2)*2),-2)'"
        ":'if(gt(iw,ih),-2,min(3840,trunc(ih/2)*2))'"
    )


@needs_ffmpeg
def test_probe_reads_a_wide_video(tmp_path):
    path = _make_video(tmp_path / "pano.mp4", "5760x1080", seconds=2)
    got = media_probe.probe_media(path, "video/mp4")
    assert (got["width_px"], got["height_px"]) == (5760, 1080)
    assert got["duration_seconds"] == pytest.approx(2.0, abs=0.15)


@needs_ffmpeg
@pytest.mark.parametrize("rotation", ["90", "-90"])
def test_probe_swaps_the_size_of_a_video_stored_sideways(tmp_path, rotation):
    """Phones store portrait video as landscape frames plus a rotation."""
    land = _make_video(tmp_path / "land.mp4", "1920x1080")
    rotated = tmp_path / "rot.mov"
    _ffmpeg("-display_rotation", rotation, "-i", land, "-c", "copy", str(rotated))
    got = media_probe.probe_media(str(rotated), "video/quicktime")
    assert (got["width_px"], got["height_px"]) == (1080, 1920)
    assert got["duration_seconds"] == pytest.approx(1.0, abs=0.15)


@needs_ffmpeg
def test_probe_takes_a_mixed_case_mime(tmp_path):
    path = _make_video(tmp_path / "hd.mp4", "1280x720")
    got = media_probe.probe_media(path, "VIDEO/MP4")
    assert (got["width_px"], got["height_px"]) == (1280, 720)


def test_probe_reads_an_image_with_pillow(tmp_path):
    from PIL import Image
    path = tmp_path / "still.png"
    Image.new("RGB", (800, 600), (200, 0, 0)).save(path)
    assert media_probe.probe_media(str(path), "image/png") == {
        "width_px": 800, "height_px": 600, "duration_seconds": None,
    }


def test_probe_swaps_a_jpeg_with_exif_orientation_6(tmp_path):
    from PIL import Image
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6          # stored turned 90 degrees
    Image.new("RGB", (400, 300), (0, 0, 200)).save(path, exif=exif.tobytes())
    got = media_probe.probe_media(str(path), "image/jpeg")
    assert (got["width_px"], got["height_px"]) == (300, 400)
    assert got["duration_seconds"] is None


@pytest.mark.parametrize("name,body,mime", [
    ("garbage.3gp", b"\x00\x00\x00\x18ftyp", "video/3gpp"),
    ("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"),
    ("menu.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    ("note.txt", b"hello", "text/url"),
])
def test_probe_returns_nulls_for_what_it_cannot_read(tmp_path, name, body, mime):
    path = tmp_path / name
    path.write_bytes(body)
    assert media_probe.probe_media(str(path), mime) == NULLS


def test_probe_of_a_missing_file_returns_nulls(tmp_path):
    assert media_probe.probe_media(str(tmp_path / "gone.mp4"), "video/mp4") == NULLS
    assert media_probe.probe_media(str(tmp_path / "gone.png"), "image/png") == NULLS


def test_probe_passes_its_timeout_and_survives_a_hung_ffprobe(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 16)
    seen = []

    def hang(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(media_probe.subprocess, "run", hang)
    assert media_probe.probe_media(str(path), "video/mp4") == NULLS
    assert seen == [20]
    assert media_probe.probe_media(str(path), "video/mp4", timeout=3) == NULLS
    assert seen == [20, 3]


def test_probe_ignores_nonsense_ffprobe_values(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 16)
    out = (b'{"streams":[{"width":0,"height":"x","duration":"N/A"}],'
           b'"format":{"duration":"nan"}}')
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(media_probe.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, b""))
    assert media_probe.probe_media(str(path), "video/mp4") == NULLS
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_media_probe.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'media_probe'`.

- [ ] **Step 3: Implement `backend/media_probe.py`**

Create `backend/media_probe.py`:

```python
"""Pixel size and length of an uploaded image or video.

POST /media/upload probes every file before it answers, and transcode_video
probes its output again, because the 3840 px cap (TRANSCODE_SCALE_FILTER) can
change a video's size. A free video wall's canvas is the probed size of its
video, so this is what lets each TV's box crop the right part of the picture.

Pure file inspection, no database access. probe_media never raises: anything
it cannot read comes back as None, and the caller stores NULL.
"""
import json
import logging
import math
import shutil
import subprocess

logger = logging.getLogger("signage.media_probe")

PROBE_TIMEOUT_SECONDS = 20
# Cheap TV hardware decoders often stop at 4096 px wide, and a file a TV cannot
# decode shows nothing at all, so every transcode caps the long side here.
VIDEO_LONG_SIDE_MAX = 3840
# The long side becomes min(3840, itself rounded down to even) and the other
# side follows the aspect (-2: rounded to even). Never upscales. libx264
# refuses odd sizes, so rounding also lets a 1281x721 source transcode at all.
# In an argv list the single quotes are ffmpeg's own filtergraph quoting.
TRANSCODE_SCALE_FILTER = (
    f"scale='if(gt(iw,ih),min({VIDEO_LONG_SIDE_MAX},trunc(iw/2)*2),-2)'"
    f":'if(gt(iw,ih),-2,min({VIDEO_LONG_SIDE_MAX},trunc(ih/2)*2))'"
)

_NO_PROBE = {"width_px": None, "height_px": None, "duration_seconds": None}
_MAX_SIDE_PX = 100000
_EXIF_ORIENTATION = 0x0112
_EXIF_QUARTER_TURNS = (5, 6, 7, 8)   # stored turned by 90 or 270 degrees


def _pos_int(value):
    """A whole number of pixels in 1.._MAX_SIDE_PX, else None."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= _MAX_SIDE_PX else None


def _pos_float(value):
    """A finite number above 0, else None (ffprobe writes "N/A" when it does not know)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _ffprobe_json(path: str, timeout: float):
    """ffprobe's view of the first video stream and the container, or None."""
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,duration:stream_side_data=rotation:format=duration",
        "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _pillow_size(path: str):
    """(width, height) as the picture is shown. Raises if Pillow cannot read it."""
    from PIL import Image
    with Image.open(path) as im:          # header only; pixels are never decoded
        width, height = im.size
        if im.getexif().get(_EXIF_ORIENTATION) in _EXIF_QUARTER_TURNS:
            width, height = height, width
    return width, height


def probe_media(path: str, mime: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """{"width_px", "height_px", "duration_seconds"} of an image or video file.

    Images are read with Pillow, falling back to ffprobe for what Pillow has no
    plugin for (AVIF). Videos are read with ffprobe; one stored sideways
    (rotation side data of +-90, as phones record portrait video) reports its
    shown size. The container's duration is preferred, since webm and mkv carry
    none per stream. PDFs, URLs and anything unreadable give all None.
    """
    out = dict(_NO_PROBE)
    major = (mime or "").split(";")[0].strip().lower().split("/")[0]
    if major not in ("image", "video"):
        return out
    try:
        if major == "image":
            try:
                size = _pillow_size(path)
            except Exception:
                size = None   # AVIF, SVG, truncated or huge files: let ffprobe try
            if size is not None:
                out["width_px"], out["height_px"] = _pos_int(size[0]), _pos_int(size[1])
                return out
        data = _ffprobe_json(path, timeout)
        if not data:
            return out
        streams = data.get("streams") or []
        stream = streams[0] if streams and isinstance(streams[0], dict) else {}
        width, height = _pos_int(stream.get("width")), _pos_int(stream.get("height"))
        rotation = 0
        for side in stream.get("side_data_list") or []:
            if isinstance(side, dict) and "rotation" in side:
                try:
                    rotation = int(float(side["rotation"]))
                except (TypeError, ValueError):
                    pass
        if rotation % 180 != 0 and rotation % 90 == 0:
            width, height = height, width
        out["width_px"], out["height_px"] = width, height
        if major == "video":
            fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
            out["duration_seconds"] = (_pos_float(fmt.get("duration"))
                                       or _pos_float(stream.get("duration")))
    except Exception:
        logger.exception("media probe failed for %s", path)
        return dict(_NO_PROBE)
    return out
```

- [ ] **Step 4: Run the probe tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_media_probe.py -v`
Expected: PASS, `14 passed` (the ffmpeg-backed ones take about 4 s in all).

- [ ] **Step 5: Write the failing upload / transcode / default-duration tests**

Create `backend/tests/test_media_upload_probe.py`:

```python
"""POST /media/upload stores the probed size and length; transcode_video caps
the long side at 3840 px and stores the size of the file it made; standalone
video playlist items keep their 10 s default (plan decision D2).

TestClient runs a response's background tasks before client.post returns, so a
real video upload here also runs the real transcode.
"""
import asyncio
import io
import os
import secrets
import shutil
import subprocess
import uuid

import pytest

import main
import media_probe
from db import execute, query_one, utc_now_iso
from main import hash_password

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)

NULLS = {"width_px": None, "height_px": None, "duration_seconds": None}


def _org_headers(screen_limit: int = 10):
    slug = "mp" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"MP {slug}", slug, "active", screen_limit, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return oid, {"Authorization": f"Bearer {tok}"}


def _upload(client, headers, name, body, mime):
    return client.post("/media/upload", headers=headers,
                       files={"file": (name, io.BytesIO(body), mime)})


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _make_video(path, size: str, *, seconds: float = 1, pix_fmt: str = "yuv420p") -> str:
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=10", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, str(path))
    return str(path)


def _dims(path) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, check=True, timeout=20,
    ).stdout.decode().strip()
    width, height = out.split("x")
    return int(width), int(height)


def _media_row(media_id: int) -> dict:
    return query_one(
        "SELECT filename, mime_type, width_px, height_px, duration_seconds "
        "FROM media WHERE id = ?", (media_id,),
    )


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_upload_stores_an_images_size(client):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (0, 120, 0)).save(buf, format="PNG")
    _, headers = _org_headers()
    r = _upload(client, headers, "still.png", buf.getvalue(), "image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["width_px"], body["height_px"], body["duration_seconds"]) == (640, 360, None)
    row = _media_row(body["id"])
    assert (row["width_px"], row["height_px"], row["duration_seconds"]) == (640, 360, None)


def test_upload_reads_the_file_off_the_event_loop_and_stores_nulls_when_unreadable(
    client, monkeypatch,
):
    monkeypatch.setattr(main, "transcode_video", lambda *a, **k: None)
    real = media_probe.probe_media
    calls = []

    def spy(path, mime, *args, **kwargs):
        calls.append((mime, _on_event_loop()))
        return real(path, mime, *args, **kwargs)

    monkeypatch.setattr(media_probe, "probe_media", spy)
    _, headers = _org_headers()
    r = _upload(client, headers, "clip.3gp", b"\x00\x00\x00\x18ftyp", "video/3gpp")
    assert r.status_code == 200, r.text
    # Probed once, in a worker thread: a 20 s ffprobe must not stall the loop
    # that carries this worker's wall sockets.
    assert calls == [("video/3gpp", False)]
    row = _media_row(r.json()["id"])
    assert (row["width_px"], row["height_px"], row["duration_seconds"]) == (None, None, None)


def test_upload_queues_the_transcode_for_a_mixed_case_video_claim(client, monkeypatch):
    queued = []
    monkeypatch.setattr(main, "transcode_video", lambda path, media_id: queued.append(media_id))
    _, headers = _org_headers()
    r = _upload(client, headers, "clip.mp4", b"\x00\x00\x00\x18ftyp", "VIDEO/MP4")
    assert r.status_code == 200, r.text
    assert queued == [r.json()["id"]]


def test_upload_does_not_transcode_images(client, monkeypatch):
    queued = []
    monkeypatch.setattr(main, "transcode_video", lambda path, media_id: queued.append(media_id))
    _, headers = _org_headers()
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")
    r = _upload(client, headers, "a.png", buf.getvalue(), "image/png")
    assert r.status_code == 200, r.text
    assert queued == []


@needs_ffmpeg
def test_upload_then_transcode_caps_a_panorama_at_3840(client, tmp_path):
    src = _make_video(tmp_path / "pano.mp4", "5760x1080", seconds=1)
    _, headers = _org_headers()
    with open(src, "rb") as f:
        r = _upload(client, headers, "pano.mp4", f.read(), "video/mp4")
    assert r.status_code == 200, r.text
    body = r.json()
    # The answer describes the upload as probed, before the transcode ran.
    assert (body["width_px"], body["height_px"]) == (5760, 1080)
    assert body["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    # The background transcode has finished by now: the row is the capped file.
    row = _media_row(body["id"])
    assert row["filename"].endswith(".opt.mp4")
    assert row["mime_type"] == "video/mp4"
    assert (row["width_px"], row["height_px"]) == (3840, 720)
    assert row["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    assert _dims(os.path.join(main.UPLOAD_DIR, row["filename"])) == (3840, 720)


@needs_ffmpeg
def test_transcode_puts_the_scale_filter_right_after_the_input(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, b"", b"stub failure")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    src = str(tmp_path / "in.mov")
    main.transcode_video(src, 0)
    cmd = seen[0]
    assert cmd[0] == "ffmpeg"
    i = cmd.index("-i")
    assert cmd[i + 1] == src
    assert cmd[i + 2:i + 4] == ["-vf", media_probe.TRANSCODE_SCALE_FILTER]


@needs_ffmpeg
def test_transcode_rounds_an_odd_sized_video_to_even(tmp_path):
    # libx264 refuses odd sizes with yuv420p, so the source is yuv444p.
    src = _make_video(tmp_path / "odd.mp4", "1281x721", pix_fmt="yuv444p")
    oid, _ = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "odd.mp4", "odd.mp4", "video/mp4", os.path.getsize(src), utc_now_iso(), 1281, 721),
    )
    main.transcode_video(src, mid)
    row = _media_row(mid)
    assert row["filename"] == "odd.opt.mp4"
    assert (row["width_px"], row["height_px"]) == (1280, 720)
    assert row["duration_seconds"] == pytest.approx(1.0, abs=0.15)
    assert _dims(tmp_path / "odd.opt.mp4") == (1280, 720)


@needs_ffmpeg
def test_transcode_keeps_upload_time_values_the_output_read_lacks(tmp_path, monkeypatch):
    src = _make_video(tmp_path / "hd.mp4", "1280x720")
    oid, _ = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "hd.mp4", "hd.mp4", "video/mp4", os.path.getsize(src), utc_now_iso(), 1280, 720),
    )
    # The read of the output knows the length but not the size.
    monkeypatch.setattr(media_probe, "probe_media", lambda *a, **k: {
        "width_px": None, "height_px": None, "duration_seconds": 7.5,
    })
    main.transcode_video(src, mid)
    row = _media_row(mid)
    assert row["filename"] == "hd.opt.mp4"
    assert (row["width_px"], row["height_px"]) == (1280, 720)
    assert row["duration_seconds"] == pytest.approx(7.5)


def test_default_duration_ignores_the_stored_video_length():
    assert main._default_duration_seconds({"mime_type": "video/mp4", "duration_seconds": 42.5}) == 10
    assert main._default_duration_seconds({"mime_type": "video/mp4", "duration_seconds": None}) == 10
    assert main._default_duration_seconds({"mime_type": "image/png"}) == 10
    assert main._default_duration_seconds({"mime_type": "application/pdf"}) == 30


def test_standalone_video_item_still_defaults_to_ten_seconds(client):
    oid, headers = _org_headers()
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at, "
        "width_px, height_px, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "long.mp4", f"{uuid.uuid4().hex}.mp4", "video/mp4", 100, utc_now_iso(),
         1920, 1080, 42.5),
    )
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (oid, "p", utc_now_iso()))
    r = client.post(f"/playlists/{pid}/items", headers=headers, json={"media_id": mid})
    assert r.status_code == 200, r.text
    assert r.json()["duration_seconds"] == 10
```

- [ ] **Step 6: Run them and watch them fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_media_upload_probe.py -v`
Expected: FAIL, `9 failed, 1 passed`:
- `test_upload_stores_an_images_size`: `(None, None, None) == (640, 360, None)` fails (no probe yet).
- `test_upload_reads_the_file_off_the_event_loop...`: `[] == [("video/3gpp", False)]` (probe never called).
- `test_upload_queues_the_transcode_for_a_mixed_case_video_claim`: `[] == [<id>]`
  (`"VIDEO/MP4".startswith("video")` is False).
- `test_upload_then_transcode_caps_a_panorama_at_3840`: response `width_px` is None.
- `test_transcode_puts_the_scale_filter_right_after_the_input`: `['-c:v', 'libx264'] == ['-vf', ...]`.
- `test_transcode_rounds_an_odd_sized_video_to_even`: `filename` is still `odd.mp4` (ffmpeg rejects the
  odd width without the filter, so the row is left alone).
- `test_transcode_keeps_upload_time_values_the_output_read_lacks`: `duration_seconds` is None, not 7.5.
- `test_default_duration_ignores_the_stored_video_length` and
  `test_standalone_video_item_still_defaults_to_ten_seconds`: `43 == 10` (the forward hook now sees
  the column).
`test_upload_does_not_transcode_images` passes already (it guards the new `_media_class` check).

- [ ] **Step 7: Implement the main.py changes**

7a. Import. In `backend/main.py` replace (lines 35-36):

```python
import menu_render
import menus as menus_domain
```

with:

```python
import media_probe
import menu_render
import menus as menus_domain
```

7b. Replace the whole `transcode_video` function (lines 47-98, from `def transcode_video(` through
`    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)`) with:

```python
def transcode_video(input_path: str, media_id: int) -> None:
    """Re-encode uploaded video to streaming-optimized H.264.

    H.264 (libx264) hardware-decodes on every consumer device — minimal GPU/CPU
    load on signage clients. CRF 23 is visually lossless; +faststart puts the
    moov atom at the file head so the player starts immediately. Bitrate is
    capped at 12 Mbps to bound network use even on 4K source.

    The long side is capped at media_probe.VIDEO_LONG_SIDE_MAX (3840 px): cheap
    TV decoders often stop at 4096 px, and a video wall plays one file across
    every TV. The cap can change the size, so the output is probed again and
    the row gets the size and length of the file the TVs actually play.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not installed — skipping transcode for media %s", media_id)
        return
    base, _ext = os.path.splitext(input_path)
    output_path = f"{base}.opt.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", media_probe.TRANSCODE_SCALE_FILTER,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-maxrate", "12M",
        "-bufsize", "24M",
        "-movflags", "+faststart",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        logger.error("Transcode timed out for media %s", media_id)
        if os.path.exists(output_path):
            os.remove(output_path)
        return
    if result.returncode != 0:
        logger.error("ffmpeg failed for media %s: %s", media_id, result.stderr[-400:].decode(errors="ignore"))
        if os.path.exists(output_path):
            os.remove(output_path)
        return
    new_filename = os.path.basename(output_path)
    new_size = os.path.getsize(output_path)
    probed = media_probe.probe_media(output_path, "video/mp4")
    # COALESCE: whatever this second probe cannot read keeps the upload's value.
    execute(
        "UPDATE media SET filename = ?, mime_type = ?, size = ?, "
        "width_px = COALESCE(?::integer, width_px), "
        "height_px = COALESCE(?::integer, height_px), "
        "duration_seconds = COALESCE(?::real, duration_seconds) "
        "WHERE id = ?",
        (new_filename, "video/mp4", new_size,
         probed["width_px"], probed["height_px"], probed["duration_seconds"], media_id),
    )
    if os.path.exists(input_path) and input_path != output_path:
        try:
            os.remove(input_path)
        except OSError:
            pass
    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)
```

7c. Replace the whole `_default_duration_seconds` function (lines 4192-4208) with:

```python
def _default_duration_seconds(media: dict) -> int:
    """Default playlist-item duration when caller omits duration_seconds."""
    mime = (media.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return 10
    if mime.startswith("video/"):
        # Not media.duration_seconds, although uploads now store the probed
        # length: standalone playlists keep the 10 s default they always had
        # (a probed two-hour video would otherwise get a 7200 s item, past
        # PlaylistItemCreate's own 3600 s cap). Only a free video wall's one
        # canvas item plays for the probed length.
        return 10
    if mime == "application/pdf":
        return 30
    if mime == "text/url":
        return 10
    return 10
```

7d. In `upload_media`, replace this exact text (lines 4604-4624):

```python
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)

    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            principal.organization_id,
            file.filename or filename,
            filename,
            content_type,
            len(contents),
            utc_now_iso(),
        ),
    )
    if content_type.startswith("video"):
        background_tasks.add_task(transcode_video, file_path, media_id)
```

with:

```python
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)
    # Size and length before answering: a free video wall's canvas is its
    # video's size. In a worker thread, since ffprobe may take up to 20 s and
    # this worker's event loop also carries the wall sockets.
    probed = await anyio.to_thread.run_sync(media_probe.probe_media, file_path, content_type)

    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at,
                           width_px, height_px, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            principal.organization_id,
            file.filename or filename,
            filename,
            content_type,
            len(contents),
            utc_now_iso(),
            probed["width_px"],
            probed["height_px"],
            probed["duration_seconds"],
        ),
    )
    # _media_class, not startswith("video"): the stored claim keeps the
    # client's case, and a "VIDEO/MP4" upload must still be transcoded (and
    # capped at 3840 px).
    if _media_class(content_type) == "video":
        background_tasks.add_task(transcode_video, file_path, media_id)
```

- [ ] **Step 8: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_media_probe.py tests/test_media_upload_probe.py tests/test_upload_types.py tests/test_uploads_range.py tests/test_playlists.py tests/test_player_offline.py tests/test_walls_phase2.py tests/test_mcp.py -v`
Expected: PASS. Both new files pass, and the existing `test_upload_types.py` (PNG bytes as
`.mp4`/`.mov` probe as 1x1, garbage `.3gp`/`.wmv`/`.mpg`/`.ts` probe as NULL, all still 200),
`test_uploads_range.py`, `test_playlists.py`, `test_player_offline.py`, `test_walls_phase2.py` (its
broken-PNG upload probes as NULL) and `test_mcp.py` (`khanshoof_list_media` gains three keys) still
pass.

- [ ] **Step 9: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/media_probe.py backend/main.py backend/tests/test_media_probe.py backend/tests/test_media_upload_probe.py
git commit -m "feat(media): probe size and length at upload; cap transcodes at 3840 px

POST /media/upload reads width, height and duration (Pillow for images,
ffprobe for videos and as Pillow's fallback; sideways phone videos and
EXIF-rotated JPEGs report their shown size) in a worker thread and stores
them. transcode_video scales the long side to at most 3840 px, rounded to
even so odd-sized videos transcode at all, then re-probes its output.
A mixed-case video claim is now transcoded too. Standalone playlist items
keep their 10 s video default: the old forward hook is removed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

---

### Task 3: Pure layout helpers

**Files:**
- Create: `backend/wall_layout.py`
- Test: `backend/tests/test_wall_layout.py` (new)

**Interfaces:**
- Consumes: nothing (standard library `math` only; never imports `main`, `db` or `walls`).
- Produces (exact names; boxes are dicts `{"x", "y", "w", "h"}` of floats, fractions of the canvas,
  x/y the top-left corner):
  - `DEFAULT_TV_ASPECT = 16 / 9`, `MIN_OVERLAP = 0.05`, `MAX_TVS = 8`, `FALLBACK_CANVAS = (3840, 2160)`
  - extra constants: `MAX_BOX_SIDE = 4.0` (w and h limit: `validate_box` refuses larger, so the
    placement PUT does too; WallPlacement itself has plain floats, R2),
    `SHAPE_TOLERANCE = 0.01` (the 1% used by `has_default_shape`)
  - `canvas_size_for(width_px, height_px) -> tuple[int, int]`
  - `first_box(canvas_w: int, canvas_h: int, tv_aspect: float = DEFAULT_TV_ASPECT) -> dict`:
    h = 1 and w = tv_aspect * canvas_h / canvas_w (both shrunk, keeping the shape, if w > 4);
    y = 0, and x = 0, or (1 - w) / 2 when w > 1 so a box wider than the canvas is centred (R1)
  - `next_box(prev: dict, canvas_w: int, canvas_h: int) -> dict` (R3): the previous box's height
    in the default 16:9 pixel shape (w = DEFAULT_TV_ASPECT * prev.h * canvas_h / canvas_w, both
    shrunk if w > 4), at x = prev.x + prev.w, y = prev.y, then `nudge_into_canvas`. An unusable
    canvas size falls back to FALLBACK_CANVAS. Task 5's `POST /walls/{id}/tvs` calls it as
    `next_box(prev, *canvas_size_for(wall["canvas_width_px"], wall["canvas_height_px"]))`, or
    repeats exactly this arithmetic with `DEFAULT_TV_ASPECT`, `MAX_BOX_SIDE` and
    `nudge_into_canvas`.
  - `overlap_fraction(box: dict) -> float`
  - `nudge_into_canvas(box: dict) -> dict`
  - `validate_box(box: dict) -> bool`
  - `box_to_px(box: dict, canvas_w: int, canvas_h: int) -> dict`
  - `fit_box_to_screen(box: dict, screen_w: int, screen_h: int, canvas_w: int, canvas_h: int) -> dict`
  - `has_default_shape(box: dict, canvas_w: int, canvas_h: int) -> bool`
  - `lowest_free_index(used) -> int | None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_wall_layout.py`:

```python
"""wall_layout: box arithmetic for phone-placed (free-layout) video walls.

Pure functions; no database. Boxes are fractions of the canvas: x, y the
top-left corner, w, h the size.
"""
import pytest

import wall_layout as wl


def _box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def _pixel_aspect(box, canvas_w, canvas_h):
    return (box["w"] * canvas_w) / (box["h"] * canvas_h)


def test_constants():
    assert wl.DEFAULT_TV_ASPECT == pytest.approx(16 / 9)
    assert wl.MIN_OVERLAP == 0.05
    assert wl.MAX_TVS == 8
    assert wl.FALLBACK_CANVAS == (3840, 2160)
    assert wl.MAX_BOX_SIDE == 4.0


# ── canvas_size_for ────────────────────────────────────────────────────────

def test_canvas_size_is_the_media_size():
    assert wl.canvas_size_for(3840, 720) == (3840, 720)
    assert wl.canvas_size_for(1080, 1920) == (1080, 1920)


@pytest.mark.parametrize("w,h", [
    (None, None), (None, 720), (3840, None), (0, 720), (3840, -1), (True, 720), ("3840", "720"),
    (float("nan"), 720),
])
def test_canvas_size_falls_back_when_the_size_is_unknown(w, h):
    assert wl.canvas_size_for(w, h) == (3840, 2160)


# ── first_box ──────────────────────────────────────────────────────────────

def test_first_box_on_a_panorama_is_the_left_third():
    box = wl.first_box(3840, 720)
    assert box == pytest.approx(_box(0.0, 0.0, 1 / 3, 1.0))
    assert wl.box_to_px(box, 3840, 720) == pytest.approx(_box(0.0, 0.0, 1280.0, 720.0))


def test_first_box_on_a_16_9_canvas_is_the_whole_canvas():
    assert wl.first_box(3840, 2160) == pytest.approx(_box(0.0, 0.0, 1.0, 1.0))


def test_first_box_takes_the_tvs_own_shape():
    box = wl.first_box(3840, 720, tv_aspect=1080 / 1920)
    assert box["h"] == 1.0
    assert _pixel_aspect(box, 3840, 720) == pytest.approx(1080 / 1920)


def test_first_box_on_a_portrait_video_is_wider_than_the_canvas_and_centred():
    box = wl.first_box(1080, 1920)
    w = (16 / 9) * 1920 / 1080
    # Ruling R1: a box wider than the video is centred on it, so the picture
    # sits in the middle of the TV instead of at its left edge.
    assert box == pytest.approx(_box((1 - w) / 2, 0.0, w, 1.0))
    assert box["x"] + box["w"] / 2 == pytest.approx(0.5)
    assert wl.validate_box(box)


def test_first_box_exactly_as_wide_as_the_canvas_stays_at_the_left_edge():
    assert wl.first_box(3840, 2160)["x"] == 0.0
    assert wl.first_box(3840, 720)["x"] == 0.0


def test_first_box_on_a_very_tall_video_is_shrunk_to_the_size_limit():
    box = wl.first_box(1080, 5760)            # full height would be 9.48 canvases wide
    assert box["w"] == pytest.approx(4.0)
    assert box["x"] == pytest.approx(-1.5)    # centred: (1 - 4) / 2
    assert box["y"] == 0.0
    assert box["h"] < 1.0
    assert _pixel_aspect(box, 1080, 5760) == pytest.approx(16 / 9)
    assert wl.validate_box(box)


def test_first_box_with_an_unusable_canvas_uses_the_fallback():
    assert wl.first_box(0, 0) == pytest.approx(_box(0.0, 0.0, 1.0, 1.0))


# ── overlap_fraction ───────────────────────────────────────────────────────

@pytest.mark.parametrize("box,expected", [
    (_box(0.0, 0.0, 1.0, 1.0), 1.0),
    (_box(0.25, 0.25, 0.5, 0.5), 1.0),
    (_box(0.75, 0.0, 0.5, 1.0), 0.5),          # half hangs over the right edge
    (_box(0.5, 0.5, 1.0, 1.0), 0.25),          # only the top-left quarter is on it
    (_box(-1.0, -1.0, 3.0, 3.0), 1 / 9),       # bigger than the canvas
    (_box(1.0, 0.0, 0.5, 1.0), 0.0),           # touching the edge is not overlapping
    (_box(5.0, 5.0, 0.5, 0.5), 0.0),
    (_box(0.0, 0.0, 0.0, 1.0), 0.0),           # no area
])
def test_overlap_fraction(box, expected):
    assert wl.overlap_fraction(box) == pytest.approx(expected)


# ── nudge_into_canvas ──────────────────────────────────────────────────────

def test_nudge_leaves_a_box_with_enough_overlap_alone():
    box = _box(0.9, 0.1, 1.0, 0.5)             # 10% of it is on the canvas
    assert wl.nudge_into_canvas(box) == pytest.approx(box)


def test_nudge_pulls_a_box_back_from_the_right():
    box = wl.nudge_into_canvas(_box(5.0, 0.0, 0.5, 0.5))
    assert box == pytest.approx(_box(1.0 - 0.025, 0.0, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_pushes_a_box_in_from_the_left():
    box = wl.nudge_into_canvas(_box(-3.0, 0.0, 0.5, 0.5))
    assert box == pytest.approx(_box(0.025 - 0.5, 0.0, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_moves_y_only_when_the_box_misses_the_canvas_vertically():
    box = wl.nudge_into_canvas(_box(0.2, 3.0, 0.5, 0.5))     # below the canvas
    assert box == pytest.approx(_box(0.2, 1.0 - 0.025, 0.5, 0.5))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


def test_nudge_a_box_off_a_corner_moves_both_axes():
    box = wl.nudge_into_canvas(_box(5.0, 5.0, 0.5, 0.5))
    assert box["w"] == 0.5 and box["h"] == 0.5
    assert wl.overlap_fraction(box) == pytest.approx(0.05)
    assert wl.validate_box(box)


def test_nudge_a_four_canvas_wide_box():
    box = wl.nudge_into_canvas(_box(10.0, 0.0, 4.0, 1.0))
    assert box == pytest.approx(_box(0.8, 0.0, 4.0, 1.0))
    assert wl.overlap_fraction(box) == pytest.approx(0.05)


# ── next_box ───────────────────────────────────────────────────────────────

def test_next_box_is_flush_right_of_the_previous_one():
    first = wl.first_box(3840, 720)
    second = wl.next_box(first, 3840, 720)
    third = wl.next_box(second, 3840, 720)
    assert second == pytest.approx(_box(1 / 3, 0.0, 1 / 3, 1.0))
    assert third == pytest.approx(_box(2 / 3, 0.0, 1 / 3, 1.0))
    assert wl.box_to_px(second, 3840, 720) == pytest.approx(_box(1280.0, 0.0, 1280.0, 720.0))


def test_next_box_keeps_the_previous_height_and_top_in_the_default_shape():
    # Ruling R3: the previous box's height, the width from 16:9 (not the
    # previous width), flush right of it and top-aligned.
    box = wl.next_box(_box(0.1, 0.2, 0.25, 0.5), 5760, 1080)
    assert box == pytest.approx(_box(0.35, 0.2, (16 / 9) * 0.5 * 1080 / 5760, 0.5))
    assert wl.has_default_shape(box, 5760, 1080)
    assert wl.next_box(_box(0.2, -0.1, 0.3, 0.8), 3840, 2160) == pytest.approx(
        _box(0.5, -0.1, 0.8, 0.8))


def test_next_box_after_a_portrait_neighbour_is_16_9_again():
    # TV 1 reported a portrait screen, so its box was refit to that shape; TV 2
    # starts 16:9, so its own screen report can still refit it.
    portrait = wl.fit_box_to_screen(wl.first_box(3840, 720), 1080, 1920, 3840, 720)
    box = wl.next_box(portrait, 3840, 720)
    assert box["x"] == pytest.approx(portrait["x"] + portrait["w"])
    assert box["y"] == pytest.approx(portrait["y"])
    assert box["h"] == pytest.approx(portrait["h"])
    assert _pixel_aspect(box, 3840, 720) == pytest.approx(16 / 9)
    assert wl.has_default_shape(box, 3840, 720)
    assert wl.validate_box(box)


def test_next_box_on_a_very_tall_video_is_shrunk_to_the_size_limit():
    box = wl.next_box(_box(0.0, 0.0, 0.2, 1.0), 1080, 5760)   # 16:9 at h = 1 is 9.48 wide
    assert box["w"] == pytest.approx(4.0)
    assert box["h"] == pytest.approx(4.0 / ((16 / 9) * 5760 / 1080))
    assert (box["x"], box["y"]) == pytest.approx((0.2, 0.0))
    assert _pixel_aspect(box, 1080, 5760) == pytest.approx(16 / 9)
    assert wl.validate_box(box)


def test_next_box_with_an_unusable_canvas_uses_the_fallback():
    assert wl.next_box(_box(0.0, 0.0, 0.25, 0.5), 0, None) == pytest.approx(
        _box(0.25, 0.0, 0.5, 0.5))               # 16:9 at h = 0.5 on 3840x2160


def test_a_fourth_tv_on_a_three_tv_panorama_is_nudged_back_to_5_percent():
    third = _box(2 / 3, 0.0, 1 / 3, 1.0)
    fourth = wl.next_box(third, 3840, 720)     # would start at x = 1.0, off the canvas
    assert fourth == pytest.approx(_box(1.0 - 0.05 / 3, 0.0, 1 / 3, 1.0))
    assert wl.overlap_fraction(fourth) == pytest.approx(0.05)


def test_second_full_size_box_on_a_16_9_canvas_is_nudged_back_to_5_percent():
    first = wl.first_box(3840, 2160)
    second = wl.next_box(first, 3840, 2160)
    assert second == pytest.approx(_box(0.95, 0.0, 1.0, 1.0))
    assert wl.overlap_fraction(second) == pytest.approx(0.05)
    assert wl.validate_box(second)


# ── validate_box ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("box", [
    _box(0.0, 0.0, 1 / 3, 1.0),
    _box(0.95, 0.0, 1.0, 1.0),                 # exactly 5% on the canvas
    _box(-1.5, -1.5, 4.0, 4.0),                # the largest size
    _box(0, 0, 1, 1),                          # ints are numbers too
])
def test_validate_box_accepts(box):
    assert wl.validate_box(box) is True


@pytest.mark.parametrize("box", [
    _box(0.0, 0.0, 0.0, 1.0),                  # no width
    _box(0.0, 0.0, 1.0, -0.5),
    _box(0.0, 0.0, 4.01, 1.0),                 # past the size limit
    _box(0.0, 0.0, 1.0, 4.5),
    _box(0.951, 0.0, 1.0, 1.0),                # 4.9% on the canvas
    _box(2.0, 2.0, 0.5, 0.5),                  # off the canvas
    _box(float("nan"), 0.0, 0.5, 0.5),
    _box(0.0, float("inf"), 0.5, 0.5),
    _box(True, 0.0, 0.5, 0.5),                 # a bool is not a coordinate
    _box("0.1", 0.0, 0.5, 0.5),
    _box(None, 0.0, 0.5, 0.5),
    {"x": 0.0, "y": 0.0, "w": 0.5},            # missing h
    None,
])
def test_validate_box_rejects(box):
    assert wl.validate_box(box) is False


# ── box_to_px ──────────────────────────────────────────────────────────────

def test_box_to_px_gives_floats_in_canvas_pixels():
    px = wl.box_to_px(_box(0.25, -0.5, 0.5, 2.0), 3840, 720)
    assert px == pytest.approx(_box(960.0, -360.0, 1920.0, 1440.0))
    assert all(isinstance(v, float) for v in px.values())


# ── fit_box_to_screen ──────────────────────────────────────────────────────

def test_fit_to_a_16_9_screen_keeps_a_16_9_box():
    box = wl.first_box(3840, 720)
    assert wl.fit_box_to_screen(box, 960, 540, 3840, 720) == pytest.approx(box)


def test_fit_to_a_16_10_screen_keeps_width_and_centre():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 1280, 800, 3840, 720)
    assert fitted == pytest.approx(_box(0.0, 0.5 - 5 / 9, 1 / 3, 10 / 9))
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(1280 / 800)
    assert wl.box_to_px(fitted, 3840, 720)["w"] == pytest.approx(1280.0)
    assert wl.box_to_px(fitted, 3840, 720)["h"] == pytest.approx(800.0)


def test_fit_to_a_portrait_screen_makes_a_tall_box_around_the_same_centre():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 1080, 1920, 3840, 720)
    h = 1280 * 1920 / 1080 / 720
    assert fitted == pytest.approx(_box(0.0, 0.5 - h / 2, 1 / 3, h))
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(1080 / 1920)
    assert wl.validate_box(fitted)


def test_fit_keeps_the_centre_of_a_box_away_from_the_origin():
    fitted = wl.fit_box_to_screen(_box(0.5, 0.25, 0.25, 0.5), 1920, 1080, 3840, 2160)
    assert fitted == pytest.approx(_box(0.5, 0.375, 0.25, 0.25))


def test_fit_to_an_extreme_screen_stays_within_the_size_limit():
    box = wl.first_box(3840, 720)
    fitted = wl.fit_box_to_screen(box, 100, 10000, 3840, 720)
    assert fitted["h"] == pytest.approx(4.0)
    assert _pixel_aspect(fitted, 3840, 720) == pytest.approx(100 / 10000)
    assert fitted["x"] + fitted["w"] / 2 == pytest.approx(1 / 6)     # same centre
    assert fitted["y"] + fitted["h"] / 2 == pytest.approx(0.5)
    assert wl.validate_box(fitted)


def test_fit_with_an_unusable_screen_size_returns_the_box_unchanged():
    box = wl.first_box(3840, 720)
    assert wl.fit_box_to_screen(box, 0, 540, 3840, 720) == pytest.approx(box)


# ── has_default_shape ──────────────────────────────────────────────────────

def test_has_default_shape():
    box = wl.first_box(3840, 720)
    assert wl.has_default_shape(box, 3840, 720) is True
    near = _box(0.0, 0.0, box["w"] * 1.009, 1.0)          # 0.9% wider: still 16:9
    assert wl.has_default_shape(near, 3840, 720) is True
    off = _box(0.0, 0.0, box["w"] * 1.02, 1.0)            # 2% wider: not
    assert wl.has_default_shape(off, 3840, 720) is False
    fitted = wl.fit_box_to_screen(box, 1280, 800, 3840, 720)
    assert wl.has_default_shape(fitted, 3840, 720) is False
    assert wl.has_default_shape(_box(0.0, 0.0, 0.5, 0.0), 3840, 720) is False


# ── lowest_free_index ──────────────────────────────────────────────────────

@pytest.mark.parametrize("used,expected", [
    ([], 0),
    ([0, 1, 3], 2),
    ({1, 2}, 0),
    (range(7), 7),
    ([8, 9], 0),                               # out-of-range values do not block 0..7
    ((i for i in [0, 2]), 1),                  # any iterable
])
def test_lowest_free_index(used, expected):
    assert wl.lowest_free_index(used) == expected


def test_lowest_free_index_is_none_when_full():
    assert wl.lowest_free_index(range(8)) is None
    assert wl.lowest_free_index([7, 6, 5, 4, 3, 2, 1, 0]) is None


def test_module_is_pure():
    # main imports walls, and walls will import this module: importing main
    # (or db) here would be circular, and the helpers must stay testable alone.
    source = open(wl.__file__, encoding="utf-8").read()
    for name in ("import main", "from main", "import db", "from db", "import walls", "from walls"):
        assert name not in source
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_wall_layout.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'wall_layout'`.

- [ ] **Step 3: Implement**

Create `backend/wall_layout.py`:

```python
"""Box arithmetic for phone-placed ("free layout") video walls.

A free wall's canvas is its video. Each TV's box is stored as fractions of the
canvas -- x, y the top-left corner, w, h the size -- so 0..1 covers the canvas
and replacing the video keeps every box in the same relative place. A box may
hang over the canvas edges (that part shows black on the TV) as long as
MIN_OVERLAP of its own area stays on it.

Pure functions, no database and no I/O: used by the REST endpoints in main.py
and the wall socket in walls.py, and never imports either.
"""
import math

DEFAULT_TV_ASPECT = 16 / 9
MIN_OVERLAP = 0.05            # fraction of the box's own area that must lie on the canvas
MAX_TVS = 8
FALLBACK_CANVAS = (3840, 2160)
MAX_BOX_SIDE = 4.0            # w and h are at most 4 canvas widths / heights
SHAPE_TOLERANCE = 0.01        # pixel shapes within 1% of each other count as the same
_EPS = 1e-9                   # float slack when comparing an overlap with MIN_OVERLAP


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _xywh(box: dict) -> tuple:
    return float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])


def _axis_fraction(start: float, size: float) -> float:
    """Share of [start, start + size] that lies inside [0, 1]."""
    inside = min(start + size, 1.0) - max(start, 0.0)
    return max(0.0, inside) / size


def _move_axis(start: float, size: float, fraction: float) -> float:
    """New start for [start, start + size], moved the shortest way toward [0, 1]
    until `fraction` of its length lies inside."""
    length = fraction * size
    if start + size / 2 > 0.5:        # hangs past the far edge: pull it back
        return 1.0 - length
    return length - size              # hangs past the near edge: push it forward


def canvas_size_for(width_px, height_px) -> tuple:
    """The media's pixel size as a canvas, or FALLBACK_CANVAS when it is unknown."""
    if (_is_number(width_px) and _is_number(height_px)
            and width_px >= 1 and height_px >= 1):
        return int(width_px), int(height_px)
    return FALLBACK_CANVAS


def first_box(canvas_w: int, canvas_h: int, tv_aspect: float = DEFAULT_TV_ASPECT) -> dict:
    """The first TV's box: the full canvas height in the TV's shape, at the left
    edge (centred when wider than the canvas).

    On a 3840x720 panorama a 16:9 TV gets exactly the left third. A box that
    would be wider than MAX_BOX_SIDE canvases (a very tall video) is shrunk,
    keeping its shape.
    """
    if not (_is_number(canvas_w) and _is_number(canvas_h) and canvas_w > 0 and canvas_h > 0):
        canvas_w, canvas_h = FALLBACK_CANVAS
    if not (_is_number(tv_aspect) and tv_aspect > 0):
        tv_aspect = DEFAULT_TV_ASPECT
    w = tv_aspect * canvas_h / canvas_w
    h = 1.0
    if w > MAX_BOX_SIDE:
        h = MAX_BOX_SIDE / w
        w = MAX_BOX_SIDE
    # Ruling R1: a box wider than the video (a portrait video on a 16:9 TV) is
    # centred, so the picture sits in the middle of that TV.
    x = (1.0 - w) / 2 if w > 1.0 else 0.0
    return {"x": x, "y": 0.0, "w": w, "h": h}


def overlap_fraction(box: dict) -> float:
    """Share of the box's own area that lies on the canvas, 0..1."""
    x, y, w, h = _xywh(box)
    if not (w > 0 and h > 0):
        return 0.0
    return _axis_fraction(x, w) * _axis_fraction(y, h)


def nudge_into_canvas(box: dict) -> dict:
    """The box moved the shortest way onto the canvas when less than MIN_OVERLAP
    of it is on it; otherwise unchanged. Its size never changes.

    Only x moves, unless the box is so far above or below the canvas that x
    alone cannot reach MIN_OVERLAP; then y first moves just far enough.
    """
    x, y, w, h = _xywh(box)
    if not (w > 0 and h > 0) or overlap_fraction(box) >= MIN_OVERLAP - _EPS:
        return {"x": x, "y": y, "w": w, "h": h}
    fx_max = min(1.0, 1.0 / w)
    fy_max = min(1.0, 1.0 / h)
    fy = _axis_fraction(y, h)
    if fy * fx_max < MIN_OVERLAP - _EPS:
        y = _move_axis(y, h, min(fy_max, MIN_OVERLAP / fx_max))
        fy = _axis_fraction(y, h)
    if _axis_fraction(x, w) * fy < MIN_OVERLAP - _EPS:
        x = _move_axis(x, w, min(fx_max, MIN_OVERLAP / fy))
    return {"x": x, "y": y, "w": w, "h": h}


def next_box(prev: dict, canvas_w: int, canvas_h: int) -> dict:
    """A newly added TV's box: the previous box's height in the default 16:9
    shape, flush to its right edge and top-aligned, nudged back if that leaves
    too little of it on the canvas.

    Ruling R3: the default shape, not the previous box's own shape, so a TV
    added next to a refit portrait TV still has_default_shape and is refit to
    its own screen when it reports. For identical 16:9 TVs it is the previous
    box's size. A box that would be wider than MAX_BOX_SIDE is shrunk, keeping
    the shape.
    """
    x, y, w, h = _xywh(prev)
    if not (_is_number(canvas_w) and _is_number(canvas_h) and canvas_w > 0 and canvas_h > 0):
        canvas_w, canvas_h = FALLBACK_CANVAS
    new_h = h
    new_w = DEFAULT_TV_ASPECT * h * canvas_h / canvas_w
    if new_w > MAX_BOX_SIDE:
        new_h = h * MAX_BOX_SIDE / new_w
        new_w = MAX_BOX_SIDE
    return nudge_into_canvas({"x": x + w, "y": y, "w": new_w, "h": new_h})


def validate_box(box: dict) -> bool:
    """True for finite numbers with 0 < w, h <= MAX_BOX_SIDE and at least
    MIN_OVERLAP of the box on the canvas."""
    try:
        values = [box[key] for key in ("x", "y", "w", "h")]
    except (KeyError, TypeError):
        return False
    if not all(_is_number(v) for v in values):
        return False
    _x, _y, w, h = (float(v) for v in values)
    if not (0 < w <= MAX_BOX_SIDE and 0 < h <= MAX_BOX_SIDE):
        return False
    return overlap_fraction(box) >= MIN_OVERLAP - _EPS


def box_to_px(box: dict, canvas_w: int, canvas_h: int) -> dict:
    """The box in canvas pixels (floats): what the TV crops and scales."""
    x, y, w, h = _xywh(box)
    cw, ch = float(canvas_w), float(canvas_h)
    return {"x": x * cw, "y": y * ch, "w": w * cw, "h": h * ch}


def fit_box_to_screen(box: dict, screen_w: int, screen_h: int,
                      canvas_w: int, canvas_h: int) -> dict:
    """The box reshaped to the TV's screen: same width and centre, the height
    that gives it the screen's pixel shape.

    A very tall screen on a very wide canvas would need a box taller than
    MAX_BOX_SIDE; it is then shrunk around the same centre, keeping the
    screen's shape. The result is nudged back onto the canvas if needed.
    """
    x, y, w, h = _xywh(box)
    if not (screen_w > 0 and screen_h > 0 and canvas_w > 0 and canvas_h > 0
            and w > 0 and h > 0):
        return {"x": x, "y": y, "w": w, "h": h}
    cx, cy = x + w / 2, y + h / 2
    new_h = w * canvas_w * screen_h / (screen_w * canvas_h)
    if new_h > MAX_BOX_SIDE:
        w = w * MAX_BOX_SIDE / new_h
        new_h = MAX_BOX_SIDE
    return nudge_into_canvas({"x": cx - w / 2, "y": cy - new_h / 2, "w": w, "h": new_h})


def has_default_shape(box: dict, canvas_w: int, canvas_h: int) -> bool:
    """True while the box's pixel shape is within SHAPE_TOLERANCE of 16:9, the
    shape a TV's box has until the TV reports its real screen size."""
    _x, _y, w, h = _xywh(box)
    if not (w > 0 and h > 0 and canvas_w > 0 and canvas_h > 0):
        return False
    aspect = (w * canvas_w) / (h * canvas_h)
    return abs(aspect / DEFAULT_TV_ASPECT - 1.0) <= SHAPE_TOLERANCE


def lowest_free_index(used) -> int | None:
    """The lowest TV index 0..MAX_TVS-1 not in `used`, or None when the wall is full."""
    taken = set(used)
    for index in range(MAX_TVS):
        if index not in taken:
            return index
    return None
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_wall_layout.py -v`
Expected: PASS, `71 passed` (nothing else imports the new module yet).

- [ ] **Step 5: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/wall_layout.py backend/tests/test_wall_layout.py
git commit -m "feat(walls): pure box helpers for phone-placed video walls

first/next box placement (a first box wider than the video is centred;
a new TV's box keeps the previous height in the default 16:9 shape), 5%
overlap nudging and validation, canvas-pixel conversion, reshaping a box
to a TV's reported screen, the 16:9 default check and the lowest free TV
index. No database, no I/O.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: TV-binding helpers extracted from redeem_wall_cell

All paths are in the PR 1 worktree `/home/ahmed/signage/.claude/worktrees/wall-setup`. Line numbers are as they stand at HEAD ca74b28. Task 2's edits to `transcode_video` move everything below line 98 down by a few lines, so every edit here is anchored on text, not on line numbers.

**Files:**
- Modify: `backend/main.py:3753-3859`. New helpers go just above `@app.post("/walls/cells/redeem")` (line 3755). The transaction body of `redeem_wall_cell` (lines 3801-3851) is replaced.
- Create: `backend/tests/test_wall_tv_binding.py`
- Test (existing; must pass before and after): `backend/tests/test_walls_redeem.py`, `backend/tests/test_walls_pairing.py`

**Interfaces:**
- Consumes these existing main.py names: `query_one`, `execute`, `http_error(status, code, message)`, `generate_unique_pair_code()`, `generate_unique_token()`. The token generator stays a module global and is called by its global name, because tests monkeypatch `main.generate_unique_token`. Also consumes `datetime` and `timedelta`, which main.py already imports.
- Produces (contract, main.py):
  - `_tv_code_row_or_raise(code: str, now: datetime) -> dict`: unknown → 404 `pair.code_unknown`; status != 'pending' → 409 `pair.code_used`; `now > expires_at` → 400 `pair.code_expired`.
  - `_take_tv_code(row_id: int, now: datetime) -> None`: conditional claim. 0 rows → 409 `pair.code_used`.
  - `_lock_org(oid: int) -> dict | None`
  - `_bind_screen_to_cell(wall: dict, cell: dict, org: dict | None, now: datetime, name: str) -> tuple[int, str]`, returning `(screen_id, screen_token)`.
  - `_attach_tv_code(row_id: int, screen_id: int) -> None`
  - `redeem_wall_cell` behaves exactly as before.

- [ ] **Step 1: Record the baseline. The existing redeem and pairing tests pass before the refactor.**

First check that no other session's pytest container is running. It would not share this DB, but it competes for the same Postgres:
Run: `docker ps --format '{{.Names}}\t{{.Command}}' | grep -i pytest || echo "no other pytest run"`

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_redeem.py tests/test_walls_pairing.py -v`
Expected: PASS, `16 passed` (7 in test_walls_redeem.py, 9 in test_walls_pairing.py).

- [ ] **Step 2: Write the failing test file `backend/tests/test_wall_tv_binding.py`**

```python
"""Helpers that bind a TV to a wall cell.

redeem_wall_cell (a TV typing a wall code) shares them with the phone-driven
free-wall endpoints (a phone claiming the standalone code a TV shows in its
QR, POST /walls/free and POST /walls/{id}/tvs). The TV half is the ordinary
/screens/request_code row, claimed with the same conditional UPDATE as
/screens/claim so two phones cannot both take one TV.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import main
from db import execute, query_one, transaction, utc_now_iso
from main import hash_password


def _org(screen_limit=10):
    slug = "tb" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"TB {slug}", slug, "active", screen_limit, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return oid, {"Authorization": f"Bearer {tok}"}


def _tv_code(client):
    """The code a TV in pairing mode shows in its QR."""
    res = client.post("/screens/request_code", json={})
    assert res.status_code == 200, res.text
    return res.json()["code"]


def _past():
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def _refusal(fn, *args):
    with pytest.raises(HTTPException) as caught:
        fn(*args)
    return caught.value.status_code, caught.value.detail["code"]


def test_a_pending_code_row_is_returned(client):
    code = _tv_code(client)
    row = main._tv_code_row_or_raise(code, datetime.now(timezone.utc))
    assert (row["code"], row["status"]) == (code, "pending")
    assert isinstance(row["id"], int)


def test_an_unknown_code_is_404():
    # 0 is not in PAIR_CODE_CHARSET, so no TV can ever show this code.
    assert _refusal(main._tv_code_row_or_raise, "000000",
                    datetime.now(timezone.utc)) == (404, "pair.code_unknown")


def test_an_expired_code_is_400(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (_past(), code))
    assert _refusal(main._tv_code_row_or_raise, code,
                    datetime.now(timezone.utc)) == (400, "pair.code_expired")


def test_a_used_code_is_409_even_once_expired(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET status = 'paired', expires_at = ? WHERE code = ?",
            (_past(), code))
    assert _refusal(main._tv_code_row_or_raise, code,
                    datetime.now(timezone.utc)) == (409, "pair.code_used")


def test_taking_a_code_claims_it_once(client):
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with transaction():
        main._take_tv_code(row["id"], now)
    after = query_one("SELECT status, claimed_at FROM pairing_codes WHERE id = ?", (row["id"],))
    assert after["status"] == "paired"
    # Tz-aware: /screens/poll compares it with an aware now; a naive one is a 500.
    assert datetime.fromisoformat(after["claimed_at"]).tzinfo is not None
    with pytest.raises(HTTPException) as caught:
        with transaction():
            main._take_tv_code(row["id"], now)
    assert (caught.value.status_code, caught.value.detail["code"]) == (409, "pair.code_used")


def test_a_refusal_later_in_the_transaction_gives_the_code_back(client):
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with pytest.raises(RuntimeError):
        with transaction():
            main._take_tv_code(row["id"], now)
            raise RuntimeError("a plan limit, say")
    after = query_one("SELECT status, claimed_at FROM pairing_codes WHERE id = ?", (row["id"],))
    assert (after["status"], after["claimed_at"]) == ("pending", None)
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}


def test_attaching_the_screen_lets_the_tvs_poll_collect_its_token(client):
    oid, _headers = _org()
    token = "tok_" + secrets.token_hex(8)
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (oid, "Lobby · TV 1", secrets.token_hex(3), token, utc_now_iso()),
    )
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with transaction():
        main._take_tv_code(row["id"], now)
        main._attach_tv_code(row["id"], sid)
    assert client.get(f"/screens/poll/{code}").json() == {
        "status": "paired", "screen_id": sid,
        "screen_name": "Lobby · TV 1", "screen_token": token,
    }


def test_lock_org_returns_the_limit_row():
    oid, _headers = _org(screen_limit=7)
    with transaction():
        assert main._lock_org(oid) == {"id": oid, "screen_limit": 7}
        assert main._lock_org(-1) is None


def test_a_redeemed_cell_screen_is_named_after_its_wall_and_cell(client):
    """Characterisation: passes before and after the refactor."""
    oid, h = _org()
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (oid, "p", utc_now_iso()))
    wall = client.post("/walls", json={
        "name": "Foyer", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=h).json()
    code = client.post(f"/walls/{wall['id']}/cells/0/1/pair", headers=h).json()["code"]
    assert client.post("/walls/cells/redeem", json={"code": code}).status_code == 200
    cell = query_one(
        "SELECT screen_id FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 1",
        (wall["id"],),
    )
    assert query_one("SELECT name FROM screens WHERE id = ?",
                     (cell["screen_id"],))["name"] == "Wall Foyer (0,1)"
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_wall_tv_binding.py -v`
Expected: 8 tests FAIL with `AttributeError: module 'main' has no attribute '_tv_code_row_or_raise'` (or `_lock_org`, depending on the test). `test_a_redeemed_cell_screen_is_named_after_its_wall_and_cell` passes; it pins today's behaviour.

- [ ] **Step 4: Replace the transaction body of `redeem_wall_cell` (main.py, inside `with _db_connect().transaction():`)**

Old text. It is exact and unique, and runs from `org = query_one(` up to the final `UPDATE wall_cells`:
```python
        org = query_one(
            "SELECT id, screen_limit FROM organizations WHERE id = ? FOR UPDATE", (oid,)
        )
        cell = query_one(
            "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ? "
            "FOR UPDATE",
            (row["wall_id"], row["row_index"], row["col_index"]),
        )
        if not cell:
            raise http_error(404, "wall.cell_not_found", "Cell not found")
        reuse = None
        if cell["screen_id"]:
            reuse = query_one(
                "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
                (cell["screen_id"], oid),
            )
        # Screens an earlier re-pair left claiming this cell: their TVs lost
        # the cell long ago, and they still count toward the limit.
        execute(
            "DELETE FROM screens WHERE wall_cell_id = ? AND id <> ?",
            (cell["id"], reuse["id"] if reuse else 0),
        )
        pair_code = generate_unique_pair_code()
        screen_token = generate_unique_token()
        if reuse:
            screen_id = reuse["id"]
            execute(
                "UPDATE screens SET token = ?, pair_code = ?, wall_cell_id = ? WHERE id = ?",
                (screen_token, pair_code, cell["id"], screen_id),
            )
        else:
            if org:
                count_row = query_one(
                    "SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?", (oid,)
                )
                current = int(count_row["n"] if count_row else 0)
                limit = int(org["screen_limit"])
                if current >= limit:
                    raise http_error(
                        402,
                        "plan_limit",
                        f"Screen limit reached ({current}/{limit}). Upgrade your plan to add more.",
                    )
            name = f"Wall {wall['name']} ({row['row_index']},{row['col_index']})"
            screen_id = execute(
                """INSERT INTO screens (organization_id, name, pair_code, token, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (oid, name, pair_code, screen_token, now.isoformat()),
            )
            execute("UPDATE screens SET wall_cell_id = ? WHERE id = ?", (cell["id"], screen_id))
        execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (screen_id, cell["id"]))
```
New text:
```python
        org = _lock_org(oid)
        cell = query_one(
            "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ? "
            "FOR UPDATE",
            (row["wall_id"], row["row_index"], row["col_index"]),
        )
        if not cell:
            raise http_error(404, "wall.cell_not_found", "Cell not found")
        _screen_id, screen_token = _bind_screen_to_cell(
            wall, cell, org, now,
            f"Wall {wall['name']} ({row['row_index']},{row['col_index']})",
        )
```
Everything else in `redeem_wall_cell` stays as it is: the docstring, the pre-checks, the claim of `wall_pairing_codes`, and the returned dict, which still reads `screen_token`.

- [ ] **Step 5: Insert the helpers just above `@app.post("/walls/cells/redeem")` (main.py)**

Old text (unique):
```python
@app.post("/walls/cells/redeem")
@limiter.limit("30/minute")
def redeem_wall_cell(request: Request, payload: WallRedeemRequest) -> dict:
```
New text:
```python
# ---- Binding a TV to a wall cell ----
#
# Shared by /walls/cells/redeem (a TV typing a wall code) and the phone-driven
# free-wall endpoints (a phone claiming the standalone code a TV shows in its
# QR). Every caller holds one transaction and takes its locks in one order --
# TV code claim, organization, wall, cells -- so concurrent requests queue
# instead of deadlocking.


def _tv_code_row_or_raise(code: str, now: datetime) -> dict:
    """The pending standalone pairing code a TV is showing, or the refusal the
    phone translates: unknown 404, already paired 409, expired 400.

    Unlike /screens/claim, which re-binds a code to the screen it already
    names, these callers have no screen yet, so any status but 'pending'
    means another phone (or the Screens page) got there first.
    """
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (code,))
    if not row:
        raise http_error(404, "pair.code_unknown", "Pairing code not recognized.")
    if row["status"] != "pending":
        raise http_error(409, "pair.code_used", "This TV is already paired.")
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise http_error(400, "pair.code_expired",
                         "Pairing code expired. Ask the TV to show a new one.")
    return row


def _take_tv_code(row_id: int, now: datetime) -> None:
    """Claim the code inside the caller's transaction.

    The conditional UPDATE is /screens/claim's: of two phones claiming one TV
    only one matches status 'pending', and the other gets 409. claimed_at is
    tz-aware because /screens/poll compares it with an aware clock.
    """
    claimed = execute(
        "UPDATE pairing_codes SET status = 'paired', claimed_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now.isoformat(), row_id),
    )
    if not claimed:
        raise http_error(409, "pair.code_used", "This TV was just paired from another phone.")


def _lock_org(oid: int) -> dict | None:
    """Lock the organization row for the plan's screen-limit check.

    Take it before any INSERT in the transaction: an INSERT that references
    the organization takes a KEY SHARE lock on its row, and two requests that
    each hold one and then ask for FOR UPDATE deadlock.
    """
    return query_one(
        "SELECT id, screen_limit FROM organizations WHERE id = ? FOR UPDATE", (oid,)
    )


def _bind_screen_to_cell(wall: dict, cell: dict, org: dict | None, now: datetime,
                         name: str) -> tuple[int, str]:
    """Give the cell its screen and return (screen_id, screen_token).

    A cell's screen is a screens row like any other, so a new one is subject
    to the plan's screen_limit exactly as in create_screen. A cell that has a
    screen reuses it and rotates its token, which unplugs the TV that held it,
    instead of adding a second screen that would keep claiming the cell and
    counting toward the limit. ``name`` names a new screen only.

    Precondition: inside transaction(), with ``org`` locked by _lock_org.
    """
    oid = wall["organization_id"]
    reuse = None
    if cell["screen_id"]:
        reuse = query_one(
            "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
            (cell["screen_id"], oid),
        )
    # Screens an earlier re-pair left claiming this cell: their TVs lost
    # the cell long ago, and they still count toward the limit.
    execute(
        "DELETE FROM screens WHERE wall_cell_id = ? AND id <> ?",
        (cell["id"], reuse["id"] if reuse else 0),
    )
    pair_code = generate_unique_pair_code()
    screen_token = generate_unique_token()
    if reuse:
        screen_id = reuse["id"]
        execute(
            "UPDATE screens SET token = ?, pair_code = ?, wall_cell_id = ? WHERE id = ?",
            (screen_token, pair_code, cell["id"], screen_id),
        )
    else:
        if org:
            count_row = query_one(
                "SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?", (oid,)
            )
            current = int(count_row["n"] if count_row else 0)
            limit = int(org["screen_limit"])
            if current >= limit:
                raise http_error(
                    402,
                    "plan_limit",
                    f"Screen limit reached ({current}/{limit}). Upgrade your plan to add more.",
                )
        screen_id = execute(
            """INSERT INTO screens (organization_id, name, pair_code, token, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (oid, name, pair_code, screen_token, now.isoformat()),
        )
        execute("UPDATE screens SET wall_cell_id = ? WHERE id = ?", (cell["id"], screen_id))
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (screen_id, cell["id"]))
    return screen_id, screen_token


def _attach_tv_code(row_id: int, screen_id: int) -> None:
    """Point a claimed code at its new screen, so the TV's next poll collects
    the token. Do it in the same transaction as _take_tv_code: a poll that
    sees 'paired' with no screen answers 'expired', and the TV drops its code.
    """
    execute("UPDATE pairing_codes SET screen_id = ? WHERE id = ?", (screen_id, row_id))


@app.post("/walls/cells/redeem")
@limiter.limit("30/minute")
def redeem_wall_cell(request: Request, payload: WallRedeemRequest) -> dict:
```

- [ ] **Step 6: Run the new tests and the unchanged redeem and pairing tests, and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_wall_tv_binding.py tests/test_walls_redeem.py tests/test_walls_pairing.py -v`
Expected: PASS, `25 passed`. That is 9 new tests plus the same 16 as in Step 1, including both concurrency tests in test_walls_redeem.py. They exercise `main.generate_unique_token` through `_bind_screen_to_cell`.

- [ ] **Step 7: Commit**
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/main.py backend/tests/test_wall_tv_binding.py
git commit -m "refactor(walls): extract TV-binding helpers from redeem_wall_cell

_tv_code_row_or_raise, _take_tv_code, _lock_org, _bind_screen_to_cell and
_attach_tv_code, for the phone-driven free-wall endpoints to share.
redeem_wall_cell behaves exactly as before; its tests are unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

---

### Task 5: Create a free wall and add TVs from the phone

**Files:**
- Modify: `backend/main.py:41`. Add `import wall_layout` above `from walls import attach_walls`.
- Modify: `backend/main.py:941-948`. Changes to `serialize_wall`, plus new `_cell_online_map` and `_free_wall_video` right after it.
- Modify: `backend/main.py:98`. This is the last line of `transcode_video` today; after Task 2 it is still the final `logger.info("Transcoded media …")`. The refresh call goes after it.
- Modify: `backend/main.py:3895-3897`. The new free-wall section goes above `# ---- Spanned-mode canvas playlists ----`, after `unpair_wall_cell`; it sits just after Task 4's edit.
- Create: `backend/tests/test_walls_free.py`
- Test (existing; must still pass): `backend/tests/test_wall_tv_binding.py`, `test_walls_redeem.py`, `test_walls_pairing.py`, `test_walls_crud.py`, `test_walls_phase2.py`, `test_upload_types.py`

**Interfaces:**
- Consumes:
  - Task 1 columns: `walls.layout`; `wall_cells.place_x/place_y/place_w/place_h`; `media.width_px/height_px/duration_seconds`.
  - Task 2: `transcode_video` re-probes its output and stores the size and length with COALESCE.
  - Task 3: `wall_layout.canvas_size_for(width_px, height_px)`, `first_box(canvas_w, canvas_h)` (centred when wider than the canvas, R1), `next_box(prev, canvas_w, canvas_h)` (the previous box's height in the default 16:9 shape, R3), `lowest_free_index(used)`, `MAX_TVS`, `DEFAULT_TV_ASPECT`. The tests also use `overlap_fraction(box)`, `MIN_OVERLAP`, `has_default_shape(box, canvas_w, canvas_h)` and `fit_box_to_screen(...)`.
  - Task 4: `_tv_code_row_or_raise`, `_take_tv_code`, `_lock_org`, `_bind_screen_to_cell`, `_attach_tv_code`.
  - Existing main.py: `is_online`, `_media_class`, `audit`, `org_id`, `require_roles`, `require_active_subscription`, `limiter`, `transaction`, `utc_now_iso`, `PAIR_CODE_LENGTH`.
- Produces (contract):
  - `class FreeWallCreate(BaseModel)` with `name` (1..120), `media_id` and `code` (len 6). `class WallTvAdd(BaseModel)` with `code` (len 6).
  - `POST /walls/free` → `create_free_wall(request, payload, user, _sub)` → 201 `serialize_wall(wall)`. `@limiter.limit("30/minute")` (R4).
  - `POST /walls/{wall_id}/tvs` → `add_wall_tv(request, wall_id, payload, user, _sub)` → 201, the new `wall_cells` row plus `"online"`. `@limiter.limit("30/minute")` (R4). Its box is `wall_layout.next_box(last, canvas_w, canvas_h)` of the most recently added placed cell (R3), or `first_box` when no cell has a box.
  - `_free_wall_or_404(wall_id: int, oid: int) -> dict`: 404 `wall.not_found`, or 409 `wall.not_free` when the layout is not 'free'.
  - `_free_wall_media_or_raise(media_id: int, oid: int) -> dict`: 404 `media.not_found`, or 400 `wall.media_type_blocked` unless the media is an image or a video.
  - `_lock_free_wall_media(media_id: int, oid: int) -> dict`: the same row read again `FOR SHARE` inside the caller's transaction (404 `media.not_found` if it is gone). The canvas size and item length come from this read, so a transcode that finishes meanwhile waits for the commit and its refresh (D3) finds the wall. Task 7's `put_wall_video` uses it too.
  - `_free_wall_item_seconds(media: dict) -> int`
  - `_refresh_free_walls_for_media(media_id: int) -> None` (D3)
  - `_cell_online_map(wall_id: int) -> dict`
  - `serialize_wall` adds `cells[i]["online"]` (bool) on every wall, and `video` = `{media_id, name, url, mime_type, width_px, height_px}` or None on free walls (D6).
  - Extra private helper: `_free_wall_video(wall: dict) -> dict | None`.
  - Error codes: `pair.code_unknown` 404, `pair.code_expired` 400, `pair.code_used` 409, `wall.full` 409, `wall.not_free` 409, `wall.media_type_blocked` 400, `plan_limit` 402.
  - Screen name: `f"{wall['name']} · TV {col_index + 1}"`.
  - Test helpers in `backend/tests/test_walls_free.py` that Task 7 reuses: `_org`, `_tv_code`, `_media`, `_create`, `_free_wall`, `_add_tv`, `_grid_wall`, `_cells`, `_cols`, `_items`, `_code_status`, `_left_behind`, and `_limits(func_name) -> list[str]` (the limits registered on `main.<func_name>`, e.g. `["30 per 1 minute"]`).

- [ ] **Step 1: Write the failing test file `backend/tests/test_walls_free.py`**

```python
"""Video walls set up from a phone ("free" walls).

POST /walls/free starts a wall from the first TV's pairing QR and a video;
POST /walls/{id}/tvs adds the next TV. Each TV is the ordinary standalone
pairing code (/screens/request_code): once the phone claims it, the TV's own
poll hands it its token and /screens/{token}/content sends it into wall mode.
Boxes are fractions of the canvas, which is the video's size.
"""
import math
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import wall_layout
from db import execute, query_all, query_one, utc_now_iso
from main import app, hash_password


def _org(screen_limit=10):
    slug = "fw" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"FW {slug}", slug, "active", screen_limit, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return oid, {"Authorization": f"Bearer {tok}"}


def _tv_code(client):
    """The code a TV in pairing mode shows in its QR."""
    res = client.post("/screens/request_code", json={})
    assert res.status_code == 200, res.text
    return res.json()["code"]


def _media(oid, width=5760, height=1080, duration=20.0, mime="video/mp4"):
    """A media row as upload_media leaves it once probed (no file needed)."""
    ext = {"image/png": ".png", "application/pdf": ".pdf"}.get(mime, ".mp4")
    return execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, "
        "width_px, height_px, duration_seconds, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "clip", secrets.token_hex(8) + ext, mime, 100, width, height, duration,
         utc_now_iso()),
    )


def _create(client, headers, media_id, code, name="Lobby"):
    return client.post("/walls/free", json={"name": name, "media_id": media_id, "code": code},
                       headers=headers)


def _free_wall(client, headers, oid, name="Lobby", **media):
    res = _create(client, headers, _media(oid, **media), _tv_code(client), name)
    assert res.status_code == 201, res.text
    return res.json()


def _add_tv(client, headers, wall_id, code=None):
    return client.post(f"/walls/{wall_id}/tvs", json={"code": code or _tv_code(client)},
                       headers=headers)


def _grid_wall(client, headers):
    res = client.post("/walls", json={
        "name": "Grid", "mode": "spanned", "rows": 1, "cols": 2,
        "canvas_width_px": 1920, "canvas_height_px": 1080}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _cells(wall_id):
    return query_all("SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY col_index", (wall_id,))


def _cols(wall_id):
    return query_one("SELECT cols FROM walls WHERE id = ?", (wall_id,))["cols"]


def _items(client, headers, wall_id):
    res = client.get(f"/walls/{wall_id}/canvas-playlist", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["items"]


def _code_status(code):
    return query_one("SELECT status FROM pairing_codes WHERE code = ?", (code,))["status"]


def _left_behind(oid):
    """Rows a refused request must not have created."""
    return {t: query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE organization_id = ?",
                         (oid,))["n"]
            for t in ("walls", "playlists", "screens")}


def _unknown(client):
    return "000000"          # 0 is not in PAIR_CODE_CHARSET: no TV can show it


def _expired(client):
    code = _tv_code(client)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    return code


def _used(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET status = 'paired' WHERE code = ?", (code,))
    return code


BAD_CODES = [
    (_unknown, 404, "pair.code_unknown"),
    (_expired, 400, "pair.code_expired"),
    (_used, 409, "pair.code_used"),
]


def _limits(func_name):
    """The rate limits registered on main.<func_name>. Tests run with
    RATE_LIMITS_ENABLED=0, so no request ever hits one: assert the registration."""
    return [str(lim.limit) for lim in main.limiter._route_limits.get(f"main.{func_name}", [])]


# ---- POST /walls/free ----


def test_create_binds_the_first_tv_and_its_poll_returns_the_token(client):
    oid, h = _org()
    mid = _media(oid, width=5760, height=1080, duration=19.2)
    code = _tv_code(client)
    res = _create(client, h, mid, code, name="Lobby wall")
    assert res.status_code == 201, res.text
    wall = res.json()
    assert (wall["mode"], wall["layout"], wall["rows"], wall["cols"]) == ("spanned", "free", 1, 1)
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == (5760, 1080)
    assert (wall["bezel_h_pct"], wall["bezel_v_pct"]) == (0, 0)
    [cell] = wall["cells"]
    assert (cell["row_index"], cell["col_index"]) == (0, 0)
    assert cell["screen_id"] and cell["online"] is False
    filename = query_one("SELECT filename FROM media WHERE id = ?", (mid,))["filename"]
    assert wall["video"] == {"media_id": mid, "name": "clip", "url": f"/uploads/{filename}",
                             "mime_type": "video/mp4", "width_px": 5760, "height_px": 1080}
    assert [(i["media_id"], i["duration_seconds"], i["fit_mode"])
            for i in _items(client, h, wall["id"])] == [(mid, 20, "fit")]

    # The TV, still polling its QR's code, collects its token ...
    poll = client.get(f"/screens/poll/{code}").json()
    assert poll["status"] == "paired"
    assert poll["screen_id"] == cell["screen_id"]
    assert poll["screen_name"] == "Lobby wall · TV 1"
    # ... and its content sends it into wall mode.
    content = client.get(f"/screens/{poll['screen_token']}/content").json()
    assert content["wall_id"] == wall["id"]
    assert content["wall_cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 1}
    assert content["wall_mode"] == "spanned"
    # The poll was the TV checking in.
    assert client.get(f"/walls/{wall['id']}", headers=h).json()["cells"][0]["online"] is True


def test_first_box_is_the_left_third_of_a_5760x1080_video(client):
    oid, h = _org()
    [cell] = _free_wall(client, h, oid, width=5760, height=1080)["cells"]
    # Full height at the left edge, 16:9 until the TV reports: 1920 of 5760 px.
    assert (cell["place_x"], cell["place_y"]) == (0, 0)
    assert cell["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_h"] == pytest.approx(1.0)


@pytest.mark.parametrize("width, height, duration, mime, canvas, seconds", [
    (5760, 1080, 19.2, "video/mp4", (5760, 1080), 20),
    (1920, 1080, None, "image/png", (1920, 1080), 10),
    (None, None, None, "video/mp4", (3840, 2160), 10),     # probe failed: 4K, 10 s
    (1080, 1920, 6.0, "video/mp4", (1080, 1920), 6),       # portrait: the box is wider than it
])
def test_create_sizes_the_canvas_and_item_from_the_media(client, width, height, duration,
                                                         mime, canvas, seconds):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=width, height=height, duration=duration, mime=mime)
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == canvas
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [seconds]
    cell = wall["cells"][0]
    w = wall_layout.DEFAULT_TV_ASPECT * canvas[1] / canvas[0]
    assert cell["place_w"] == pytest.approx(w, abs=1e-6)
    # R1: a box wider than the video is centred on it; otherwise it starts at the left edge.
    assert cell["place_x"] == pytest.approx((1 - w) / 2 if w > 1 else 0.0, abs=1e-6)


@pytest.mark.parametrize("make_code, status, err", BAD_CODES)
def test_create_refuses_a_bad_code_and_leaves_nothing(client, make_code, status, err):
    oid, h = _org()
    res = _create(client, h, _media(oid), make_code(client))
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == err
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}


def test_create_refuses_another_orgs_media(client):
    oid, h = _org()
    other, _other_h = _org()
    code = _tv_code(client)
    res = _create(client, h, _media(other), code)
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "media.not_found"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}


def test_create_refuses_a_pdf(client):
    oid, h = _org()
    pdf = _media(oid, width=None, height=None, duration=None, mime="application/pdf")
    res = _create(client, h, pdf, _tv_code(client))
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "wall.media_type_blocked"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 0}


def test_create_at_the_plan_limit_is_402_and_leaves_nothing(client):
    oid, h = _org(screen_limit=1)
    execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (oid, "Front desk", secrets.token_hex(3), "tok_" + secrets.token_hex(8), utc_now_iso()),
    )
    mid = _media(oid)
    code = _tv_code(client)
    res = _create(client, h, mid, code)
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "plan_limit"
    assert _left_behind(oid) == {"walls": 0, "playlists": 0, "screens": 1}
    # Refused, not consumed: the TV keeps waiting, and the code works after an upgrade.
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}
    execute("UPDATE organizations SET screen_limit = 2 WHERE id = ?", (oid,))
    assert _create(client, h, mid, code).status_code == 201


# ---- POST /walls/{id}/tvs ----


def test_next_tv_takes_the_next_index_flush_right_of_the_last(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, name="Lobby")
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 201, res.text
    cell = res.json()
    assert (cell["row_index"], cell["col_index"]) == (0, 1)
    assert cell["place_x"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_y"] == 0
    assert cell["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert cell["place_h"] == pytest.approx(1.0)
    assert cell["online"] is False
    poll = client.get(f"/screens/poll/{code}").json()
    assert (poll["status"], poll["screen_id"], poll["screen_name"]) == (
        "paired", cell["screen_id"], "Lobby · TV 2")

    third = _add_tv(client, h, wall["id"])
    assert third.status_code == 201, third.text
    assert third.json()["col_index"] == 2
    assert third.json()["place_x"] == pytest.approx(2 / 3, abs=1e-6)
    after = client.get(f"/walls/{wall['id']}", headers=h).json()
    assert after["cols"] == 3
    assert [c["col_index"] for c in after["cells"]] == [0, 1, 2]


def test_next_tv_takes_the_last_boxs_height_and_top_in_16_9(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    execute("UPDATE wall_cells SET place_x = 0.1, place_y = 0.2, place_w = 0.25, place_h = 0.5 "
            "WHERE wall_id = ?", (wall["id"],))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    # R3: the last box's height (540 px) in the default 16:9 shape (960 px of
    # 5760), flush to its right edge (0.1 + 0.25) and top-aligned.
    assert box == pytest.approx({"x": 0.35, "y": 0.2, "w": 1 / 6, "h": 0.5}, abs=1e-6)
    assert wall_layout.has_default_shape(box, 5760, 1080)


def test_next_to_a_portrait_tv_the_new_box_is_16_9(client):
    """R3: the new TV's box keeps the default shape, not its neighbour's, so
    the new TV's own screen report can still refit it (Task 6 refits only a
    box that has_default_shape)."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    # TV 1 reported a 1080x1920 (portrait) screen and was refit to it.
    portrait = wall_layout.fit_box_to_screen({"x": 0.0, "y": 0.0, "w": 1 / 3, "h": 1.0},
                                             1080, 1920, 5760, 1080)
    execute("UPDATE wall_cells SET place_x = ?, place_y = ?, place_w = ?, place_h = ? "
            "WHERE wall_id = ?",
            (portrait["x"], portrait["y"], portrait["w"], portrait["h"], wall["id"]))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    assert box["h"] == pytest.approx(portrait["h"], rel=1e-5)
    assert box["y"] == pytest.approx(portrait["y"], rel=1e-5)
    assert box["x"] == pytest.approx(portrait["x"] + portrait["w"], rel=1e-5)
    assert wall_layout.has_default_shape(box, 5760, 1080)
    assert not wall_layout.has_default_shape(portrait, 5760, 1080)


def test_next_tv_is_nudged_back_onto_the_video(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    # Flush right of this box would be wholly off the video (x = 1.2).
    execute("UPDATE wall_cells SET place_x = 0.9, place_y = 0.0, place_w = 0.3, place_h = 1.0 "
            "WHERE wall_id = ?", (wall["id"],))
    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    box = {k: res.json()[f"place_{k}"] for k in ("x", "y", "w", "h")}
    assert 0.9 < box["x"] < 1.0
    # Full height in the 16:9 shape (R3), whatever the last box's width was.
    assert (box["w"], box["h"]) == pytest.approx((1 / 3, 1.0), abs=1e-6)
    assert wall_layout.overlap_fraction(box) == pytest.approx(wall_layout.MIN_OVERLAP, abs=1e-4)


def test_ninth_tv_is_refused_wall_full(client):
    oid, h = _org(screen_limit=20)          # the plan must not be what stops it
    wall = _free_wall(client, h, oid)
    for expected in range(1, 8):
        res = _add_tv(client, h, wall["id"])
        assert res.status_code == 201, res.text
        assert res.json()["col_index"] == expected
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "wall.full"
    assert len(_cells(wall["id"])) == 8
    assert _cols(wall["id"]) == 8
    assert _code_status(code) == "pending"


def test_adding_to_a_grid_wall_is_refused_not_free(client):
    oid, h = _org()
    grid = _grid_wall(client, h)
    code = _tv_code(client)
    res = _add_tv(client, h, grid["id"], code)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "wall.not_free"
    assert len(_cells(grid["id"])) == 2
    assert _code_status(code) == "pending"


def test_adding_to_another_orgs_wall_is_404(client):
    oid, h = _org()
    _other, other_h = _org()
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    res = _add_tv(client, other_h, wall["id"], code)
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "wall.not_found"
    assert len(_cells(wall["id"])) == 1
    assert _code_status(code) == "pending"


@pytest.mark.parametrize("make_code, status, err", BAD_CODES)
def test_adding_with_a_bad_code_is_refused_and_leaves_nothing(client, make_code, status, err):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    res = _add_tv(client, h, wall["id"], make_code(client))
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == err
    assert [c["col_index"] for c in _cells(wall["id"])] == [0]
    assert _left_behind(oid)["screens"] == 1


def test_adding_past_the_plan_limit_is_402_and_leaves_nothing(client):
    oid, h = _org(screen_limit=1)
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    res = _add_tv(client, h, wall["id"], code)
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "plan_limit"
    assert [c["col_index"] for c in _cells(wall["id"])] == [0]
    assert _cols(wall["id"]) == 1
    assert _left_behind(oid)["screens"] == 1
    assert _code_status(code) == "pending"


def _slow_token(monkeypatch):
    real = main.generate_unique_token

    def slow():
        time.sleep(0.4)        # widen the race window inside the transaction
        return real()

    monkeypatch.setattr(main, "generate_unique_token", slow)


def _add_tvs_concurrently(wall_id, headers, codes):
    results = []
    lock = threading.Lock()

    def go(code):
        res = TestClient(app).post(f"/walls/{wall_id}/tvs", json={"code": code},
                                   headers=headers)
        with lock:
            results.append(res)

    threads = [threading.Thread(target=go, args=(c,)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(results, key=lambda r: r.status_code)


def test_concurrent_tvs_with_two_codes_get_distinct_indexes(client, monkeypatch):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    codes = [_tv_code(client), _tv_code(client)]
    _slow_token(monkeypatch)
    results = _add_tvs_concurrently(wall["id"], h, codes)
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert sorted(r.json()["col_index"] for r in results) == [1, 2]
    assert [c["col_index"] for c in _cells(wall["id"])] == [0, 1, 2]
    assert _cols(wall["id"]) == 3


def test_concurrent_claims_of_one_code_pair_it_once(client, monkeypatch):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    code = _tv_code(client)
    _slow_token(monkeypatch)
    results = _add_tvs_concurrently(wall["id"], h, [code, code])
    assert [r.status_code for r in results] == [201, 409], [r.text for r in results]
    assert results[1].json()["detail"]["code"] == "pair.code_used"
    assert len(_cells(wall["id"])) == 2
    assert _left_behind(oid)["screens"] == 2


def test_a_cell_left_by_a_deleted_screen_is_reused(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    second = _add_tv(client, h, wall["id"]).json()
    assert _add_tv(client, h, wall["id"]).status_code == 201
    # Deleted from the Screens page: its cell stays behind with screen_id NULL.
    assert client.delete(f"/screens/{second['screen_id']}", headers=h).status_code == 200
    assert [c["screen_id"] is None for c in _cells(wall["id"])] == [False, True, False]

    res = _add_tv(client, h, wall["id"])
    assert res.status_code == 201, res.text
    assert res.json()["col_index"] == 1
    cells = _cells(wall["id"])
    assert [c["col_index"] for c in cells] == [0, 1, 2]
    assert all(c["screen_id"] for c in cells)
    assert _cols(wall["id"]) == 3


def test_create_and_add_tv_are_rate_limited():
    # Both claim whichever TV's pairing code they are given, so the limit is
    # what caps a script guessing codes (R4). One phone adds at most 8 TVs.
    assert _limits("create_free_wall") == ["30 per 1 minute"]
    assert _limits("add_wall_tv") == ["30 per 1 minute"]


# ---- after the transcode (D3) ----


def test_transcode_refresh_resizes_the_canvas_and_item(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=19.2)
    other = _free_wall(client, h, oid, width=1920, height=1080, duration=8.0)
    mid = wall["video"]["media_id"]
    grid = _grid_wall(client, h)
    res = client.post(f"/walls/{grid['id']}/canvas-playlist/items",
                      json={"media_id": mid, "position": 0}, headers=h)
    assert res.status_code == 201, res.text
    # What transcode_video's re-probe stores once the file is capped at 3840 px.
    execute("UPDATE media SET width_px = 3840, height_px = 720, duration_seconds = 20.4 "
            "WHERE id = ?", (mid,))

    main._refresh_free_walls_for_media(mid)

    after = client.get(f"/walls/{wall['id']}", headers=h).json()
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (3840, 720)
    # Boxes are fractions of the canvas: still the left third of the picture.
    assert after["cells"][0]["place_w"] == pytest.approx(1 / 3, abs=1e-6)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [21]
    # A free wall playing other media, and a grid wall playing this one, are left alone.
    untouched = client.get(f"/walls/{other['id']}", headers=h).json()
    assert (untouched["canvas_width_px"], untouched["canvas_height_px"]) == (1920, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, other["id"])] == [8]
    grid_after = client.get(f"/walls/{grid['id']}", headers=h).json()
    assert (grid_after["canvas_width_px"], grid_after["canvas_height_px"]) == (1920, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, grid["id"])] == [5]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg (in the backend image)")
def test_transcode_video_refreshes_free_walls_playing_it(client, tmp_path):
    oid, h = _org()
    src = tmp_path / "pano.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=5760x1080:rate=10", "-t", "1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True, timeout=120,
    )
    # As probed at upload. The 7 s stands in for a length the re-probe of the
    # 1 s output must replace.
    mid = execute(
        "INSERT INTO media (organization_id, name, filename, mime_type, size, "
        "width_px, height_px, duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, "pano", src.name, "video/mp4", src.stat().st_size, 5760, 1080, 7.0,
         utc_now_iso()),
    )
    res = _create(client, h, mid, _tv_code(client))
    assert res.status_code == 201, res.text
    wall = res.json()
    assert (wall["canvas_width_px"], wall["canvas_height_px"]) == (5760, 1080)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [7]

    main.transcode_video(str(src), mid)

    media = query_one("SELECT * FROM media WHERE id = ?", (mid,))
    assert (media["width_px"], media["height_px"]) == (3840, 720)
    after = query_one("SELECT canvas_width_px, canvas_height_px FROM walls WHERE id = ?",
                      (wall["id"],))
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (3840, 720)
    assert [i["duration_seconds"] for i in _items(client, h, wall["id"])] == [
        max(1, math.ceil(media["duration_seconds"]))]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free.py -v`
Expected: FAIL, 29 failed. POST /walls/free does not exist yet, but its path matches GET/PATCH/DELETE `/walls/{wall_id}`, so it answers 405 `{"detail":"Method Not Allowed"}`: the create tests, and every test that builds a wall with `_free_wall`, fail on their status assert (`assert 405 == 201`, `405 == 404`, ...). The grid-wall `/tvs` test gets 404 `{"detail":"Not Found"}` and fails on its status assert. The rate-limit test reads `[]` for `create_free_wall`.

- [ ] **Step 3: Import wall_layout in main.py**

Old text (unique, main.py:41):
```python
from walls import attach_walls
```
New text:
```python
import wall_layout
from walls import attach_walls
```

- [ ] **Step 4: Extend `serialize_wall` and add its two helpers (main.py:941-948)**

Old text:
```python
def serialize_wall(wall: dict, include_cells: bool = True) -> dict:
    out = dict(wall)
    if include_cells:
        out["cells"] = query_all(
            "SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY row_index, col_index",
            (wall["id"],),
        )
    return out
```
New text:
```python
def serialize_wall(wall: dict, include_cells: bool = True) -> dict:
    out = dict(wall)
    if include_cells:
        cells = query_all(
            "SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY row_index, col_index",
            (wall["id"],),
        )
        online = _cell_online_map(wall["id"])
        for cell in cells:
            cell["online"] = online.get(cell["id"], False)
        out["cells"] = cells
    if wall.get("layout") == "free":
        out["video"] = _free_wall_video(wall)
    return out


def _cell_online_map(wall_id: int) -> dict:
    """{cell id: whether its TV checked in within the last 90 s}. A wall TV
    re-reads its content every 60 s, which keeps screens.last_seen fresh."""
    rows = query_all(
        "SELECT wc.id, s.last_seen FROM wall_cells wc "
        "LEFT JOIN screens s ON s.id = wc.screen_id WHERE wc.wall_id = ?",
        (wall_id,),
    )
    return {r["id"]: is_online(r["last_seen"]) for r in rows}


def _free_wall_video(wall: dict) -> dict | None:
    """The one item a free wall plays, as the phone's editor draws it."""
    if not wall.get("spanned_playlist_id"):
        return None
    row = query_one(
        """SELECT m.id, m.name, m.filename, m.mime_type, m.width_px, m.height_px
           FROM playlist_items pi JOIN media m ON m.id = pi.media_id
           WHERE pi.playlist_id = ?
           ORDER BY pi.position ASC, pi.id ASC LIMIT 1""",
        (wall["spanned_playlist_id"],),
    )
    if not row:
        return None
    return {
        "media_id": row["id"], "name": row["name"],
        "url": f"/uploads/{row['filename']}", "mime_type": row["mime_type"],
        "width_px": row["width_px"], "height_px": row["height_px"],
    }
```

- [ ] **Step 5: Insert the free-wall section above `# ---- Spanned-mode canvas playlists ----` (main.py)**

Old text (unique, main.py:3897):
```python
# ---- Spanned-mode canvas playlists ----
```
New text:
```python
# ---- Free walls: video walls set up from a phone ----
#
# A free wall (layout 'free', always mode 'spanned') is built TV by TV from a
# phone standing in front of the screens. Its canvas is its one video, and
# each TV's portion is a box placed by hand, stored in wall_cells.place_* as
# fractions of the canvas. A cell exists only while a TV holds it: row 0,
# col_index the lowest free 0..7, and walls.cols = highest used index + 1, so
# code that walks range(cols) still finds every cell.


class FreeWallCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    media_id: int
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


class WallTvAdd(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


def _free_wall_or_404(wall_id: int, oid: int) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, oid))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    if wall.get("layout") != "free":
        raise http_error(409, "wall.not_free", "This wall was not set up from a phone.")
    return wall


def _free_wall_media_or_raise(media_id: int, oid: int) -> dict:
    """The organization's image or video. Stricter than the canvas playlist's
    _is_allowed_canvas_mime, which also takes PDFs: a free wall's canvas is
    the picture itself."""
    media = query_one("SELECT * FROM media WHERE id = ? AND organization_id = ?",
                      (media_id, oid))
    if not media:
        raise http_error(404, "media.not_found", "Media not found")
    if _media_class(media["mime_type"] or "") not in ("image", "video"):
        raise http_error(400, "wall.media_type_blocked",
                         "A video wall plays one video or image.")
    return media


def _lock_free_wall_media(media_id: int, oid: int) -> dict:
    """The media row read again FOR SHARE, inside the caller's transaction.

    The canvas size and the item's length come from this read. A transcode
    finishing meanwhile stores the new size with an UPDATE that waits for this
    commit, and only then runs _refresh_free_walls_for_media (D3), which then
    finds the wall. Read before the transaction, the size could be the
    upload's while the refresh has already run and missed the new wall.
    """
    media = query_one("SELECT * FROM media WHERE id = ? AND organization_id = ? FOR SHARE",
                      (media_id, oid))
    if not media:
        raise http_error(404, "media.not_found", "Media not found")
    return media


def _free_wall_item_seconds(media: dict) -> int:
    """How long a free wall's single item lasts: the video's probed length,
    so every loop restarts it on all TVs together. playlist_items stores whole
    seconds. Images, and videos whose length is unknown, hold for 10 s."""
    duration = media.get("duration_seconds")
    if (_media_class(media.get("mime_type") or "") == "video"
            and isinstance(duration, (int, float))
            and math.isfinite(duration) and duration > 0):
        return max(1, math.ceil(duration))
    return 10


@app.post("/walls/free", status_code=201)
@limiter.limit("30/minute")
def create_free_wall(request: Request, payload: FreeWallCreate,
                     user: dict = Depends(require_roles("admin", "editor")),
                     _sub: dict = Depends(require_active_subscription)) -> dict:
    """Start a video wall from a phone: the wall, its video and its first TV.

    ``code`` is the standalone pairing code the first TV shows in its QR. One
    transaction claims it, creates the wall (canvas = the media's size, or
    3840x2160 when unknown), its canvas playlist with the one item, and cell
    (0, 0) with the TV's screen, then points the code at that screen so the
    TV's next poll collects its token. A refused code, the wrong media or a
    full plan leaves nothing behind, and the code keeps working. The limit
    (R4) caps a script guessing codes: any pending TV code can be claimed.
    """
    oid = org_id(user)
    now = datetime.now(timezone.utc)
    code_row = _tv_code_row_or_raise(payload.code, now)
    _free_wall_media_or_raise(payload.media_id, oid)    # refuse early, before any lock
    stamp = now.isoformat()
    with transaction():
        _take_tv_code(code_row["id"], now)
        org = _lock_org(oid)
        media = _lock_free_wall_media(payload.media_id, oid)
        canvas_w, canvas_h = wall_layout.canvas_size_for(media.get("width_px"),
                                                         media.get("height_px"))
        box = wall_layout.first_box(canvas_w, canvas_h)
        playlist_id = execute(
            "INSERT INTO playlists (organization_id, name, kind, created_at) "
            "VALUES (?, ?, 'wall_canvas', ?)",
            (oid, f"Canvas: {payload.name}", stamp),
        )
        wall_id = execute(
            """INSERT INTO walls (organization_id, name, mode, layout, rows, cols,
                   canvas_width_px, canvas_height_px, bezel_enabled,
                   bezel_h_pct, bezel_v_pct, spanned_playlist_id,
                   created_at, updated_at)
               VALUES (?, ?, 'spanned', 'free', 1, 1, ?, ?, false, 0, 0, ?, ?, ?)""",
            (oid, payload.name, canvas_w, canvas_h, playlist_id, stamp, stamp),
        )
        execute(
            """INSERT INTO playlist_items
                   (playlist_id, media_id, position, duration_seconds,
                    duration_override_seconds, fit_mode, created_at)
               VALUES (?, ?, 0, ?, NULL, 'fit', ?)""",
            (playlist_id, media["id"], _free_wall_item_seconds(media), stamp),
        )
        cell_id = execute(
            """INSERT INTO wall_cells (wall_id, row_index, col_index,
                   place_x, place_y, place_w, place_h, created_at)
               VALUES (?, 0, 0, ?, ?, ?, ?, ?)""",
            (wall_id, box["x"], box["y"], box["w"], box["h"], stamp),
        )
        wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
        screen_id, _token = _bind_screen_to_cell(wall, cell, org, now,
                                                 f"{wall['name']} · TV 1")
        _attach_tv_code(code_row["id"], screen_id)
    audit(request, action="wall.create", actor=user,
          target_type="wall", target_id=wall_id,
          details={"name": payload.name, "mode": "spanned", "layout": "free",
                   "media_id": media["id"]})
    return serialize_wall(wall)


@app.post("/walls/{wall_id}/tvs", status_code=201)
@limiter.limit("30/minute")
def add_wall_tv(request: Request, wall_id: int, payload: WallTvAdd,
                user: dict = Depends(require_roles("admin", "editor")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
    """Add the TV showing ``code`` to a free wall, at its lowest free index.

    Its first box has the height of the most recently added TV's box in the
    default 16:9 shape (R3), flush to that box's right edge and top-aligned:
    for identical 16:9 TVs that is the previous box's size, and next to a TV
    refit to portrait it is still 16:9, so this TV's own screen report can
    refit it. If less than 5% of it would then be on the video, it moves back
    until 5% is. The wall row is locked before the index is chosen, after the
    organization (the order every wall transaction uses), so two phones
    adding TVs at once get different indexes rather than a unique-constraint
    500. Rate-limited like POST /walls/free (R4).
    """
    oid = org_id(user)
    now = datetime.now(timezone.utc)
    _free_wall_or_404(wall_id, oid)
    code_row = _tv_code_row_or_raise(payload.code, now)
    stamp = now.isoformat()
    with transaction():
        _take_tv_code(code_row["id"], now)
        org = _lock_org(oid)
        wall = query_one(
            "SELECT * FROM walls WHERE id = ? AND organization_id = ? FOR UPDATE",
            (wall_id, oid),
        )
        if not wall:
            raise http_error(404, "wall.not_found", "Wall not found")
        # A screen deleted from the Screens page leaves its cell behind with no
        # screen (ON DELETE SET NULL): a box with no TV, holding its index
        # forever. Clear such cells before choosing.
        execute("DELETE FROM wall_cells WHERE wall_id = ? AND screen_id IS NULL", (wall_id,))
        cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY id", (wall_id,))
        index = wall_layout.lowest_free_index({c["col_index"] for c in cells})
        if index is None:
            raise http_error(409, "wall.full",
                             f"A video wall holds up to {wall_layout.MAX_TVS} TVs.")
        placed = [c for c in cells
                  if None not in (c["place_x"], c["place_y"], c["place_w"], c["place_h"])]
        canvas_w, canvas_h = wall_layout.canvas_size_for(wall["canvas_width_px"],
                                                         wall["canvas_height_px"])
        if placed:
            last = placed[-1]          # cells are in id order: the most recently added
            box = wall_layout.next_box({"x": last["place_x"], "y": last["place_y"],
                                        "w": last["place_w"], "h": last["place_h"]},
                                       canvas_w, canvas_h)
        else:
            box = wall_layout.first_box(canvas_w, canvas_h)
        cell_id = execute(
            """INSERT INTO wall_cells (wall_id, row_index, col_index,
                   place_x, place_y, place_w, place_h, created_at)
               VALUES (?, 0, ?, ?, ?, ?, ?, ?)""",
            (wall_id, index, box["x"], box["y"], box["w"], box["h"], stamp),
        )
        cols = max([c["col_index"] for c in cells] + [index]) + 1
        execute("UPDATE walls SET cols = ?, updated_at = ? WHERE id = ?",
                (cols, stamp, wall_id))
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
        screen_id, _token = _bind_screen_to_cell(wall, cell, org, now,
                                                 f"{wall['name']} · TV {index + 1}")
        _attach_tv_code(code_row["id"], screen_id)
    audit(request, action="wall.tv_add", actor=user,
          target_type="wall", target_id=wall_id,
          details={"col_index": index, "screen_id": screen_id})
    out = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
    out["online"] = _cell_online_map(wall_id).get(cell_id, False)
    return out


def _refresh_free_walls_for_media(media_id: int) -> None:
    """Size the free walls that play this media to the file they now play.

    Called when transcode_video finishes (D3). POST /walls/free usually runs
    before that, so the canvas came from the upload's probe (5760x1080, say),
    while the transcoded file is capped at 3840 px (3840x720). The item's
    length likewise came from the original. Boxes are fractions of the canvas,
    so they keep their place. Grid walls are not touched. Best effort: a
    failure is logged and never undoes the finished transcode.
    """
    try:
        media = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
        if not media:
            return
        playlist_ids = [r["spanned_playlist_id"] for r in query_all(
            """SELECT DISTINCT w.spanned_playlist_id
               FROM walls w JOIN playlist_items pi ON pi.playlist_id = w.spanned_playlist_id
               WHERE w.layout = 'free' AND pi.media_id = ?""",
            (media_id,),
        )]
        if not playlist_ids:
            return
        if media.get("width_px") and media.get("height_px"):
            canvas_w, canvas_h = wall_layout.canvas_size_for(media["width_px"],
                                                             media["height_px"])
            execute(
                "UPDATE walls SET canvas_width_px = ?, canvas_height_px = ?, updated_at = ? "
                "WHERE layout = 'free' AND spanned_playlist_id = ANY(?)",
                (canvas_w, canvas_h, utc_now_iso(), playlist_ids),
            )
        execute(
            "UPDATE playlist_items SET duration_seconds = ? "
            "WHERE media_id = ? AND playlist_id = ANY(?)",
            (_free_wall_item_seconds(media), media_id, playlist_ids),
        )
    except Exception:
        logger.exception("free_wall_refresh_failed media_id=%s", media_id)


# ---- Spanned-mode canvas playlists ----
```

- [ ] **Step 6: Call the refresh at the end of `transcode_video` (main.py:98; after Task 2, the last line of its success path)**

Old text (unique):
```python
    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)
```
New text:
```python
    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)
    # A free wall made while this ran was sized from the upload (5760x1080,
    # say); the file it plays is now capped at 3840 px (3840x720).
    _refresh_free_walls_for_media(media_id)
```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free.py tests/test_wall_tv_binding.py tests/test_walls_redeem.py tests/test_walls_pairing.py tests/test_walls_crud.py tests/test_walls_phase2.py tests/test_upload_types.py -v`
Expected: PASS, `119 passed`. All 29 tests in test_walls_free.py pass. The ffmpeg test runs because ffmpeg is in `signage_backend:latest`; it takes about 1 s. Every test in the other six files still passes. `serialize_wall`'s new `online` and `video` keys are additive, and test_upload_types still stubs out `transcode_video`.

- [ ] **Step 8: Commit**
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/main.py backend/tests/test_walls_free.py
git commit -m "feat(walls): create a free wall and add TVs from the phone

POST /walls/free claims the first TV's standalone pairing code, creates the
wall (canvas = the video's size), its one-item canvas playlist and cell 0,
and hands the TV its token through its own poll. POST /walls/{id}/tvs adds
the next TV at the lowest free index, flush right of the last box, at its
height in the default 16:9 shape. One transaction each, with locks in the
order code, organization, wall, so a refusal leaves nothing behind and
concurrent phones never collide; both are limited to 30 a minute.
serialize_wall reports each cell's online state and a free wall's video,
and a finished transcode resizes the free walls that play it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

---

### Task 6: Live geometry: hello, geometry frames, Redis relay, screen reports

PR 1, worktree `/home/ahmed/signage/.claude/worktrees/wall-setup` (branch `feature/phone-wall-setup`); paths are
relative to it. Needs Task 1 (the new columns) and Task 3 (`backend/wall_layout.py`); needs nothing from Tasks 4-5,
because the tests build free walls with SQL. Four test-first cycles, one commit each.

Rules that bind this task: `walls.py` must never import `main` (main imports walls at module top). This task adds only
`import ratelimit` and `import wall_layout`; neither of those imports `main` or `walls`. No pytest-asyncio and no fakeredis: async code runs
under `asyncio.run()` with `FakeWS`. TestClient's `receive_json()` has no timeout, so the real-socket tests read
through `_recv_until` and never wait unbounded. The one real-Redis test publishes on a channel named
`khan:wall-geometry:test-<uuid>`, so no other subscriber (production's included) hears it. Never put the
`wall_cells` NUMERIC columns (Decimal) into a frame: `_send_safe` swallows the `json.dumps` TypeError and the
caller then drops the socket. The frames below use only the REAL, INTEGER and BIGINT columns.

Author rulings this task implements:
- **R5:** `publish_geometry` (and `broadcast_bye`) are called only after the transaction that wrote the box has
  committed, outside the `with transaction():` block, because the push re-reads the box. Task 7 follows this.
- **R6:** `_on_screen_report` keeps sync DB access on the event loop, like the rest of walls.py. Its statements
  stay short (reads, at most one UPDATE, a re-read) and it opens no transaction.
- **R7:** `wall_ws` starts the geometry subscriber only for sockets of free walls (`wall.get("layout") == "free"`).
- **R9:** frames carry `highlight_until_ms` exactly as stored. The TV hides the outline itself when that time
  passes (its own timer on the server clock, Task 8), so the server sends nothing at that moment.
- **R22:** grid walls are unchanged. They get no subscriber and no geometry frames, their tick passes skip the
  new cell read, and their hello is byte-identical to today's (the player-side scaling fix is Task 8).

**Files:**
- Modify: `backend/walls.py:1-19` (module docstring: one new bullet)
- Modify: `backend/walls.py:32` (imports)
- Modify: `backend/walls.py:46-47` (module state after `_server_loop`)
- Modify: `backend/walls.py:60-61` (constants after `TIMELINE_EPOCH_MS`)
- Modify: `backend/walls.py:94` (`_hello_frame`: a free-wall branch before the grid branch)
- Modify: `backend/walls.py:115-120` (new "free walls: geometry" section after `_hello_config`)
- Modify: `backend/walls.py:130-135` (`_unregister`)
- Modify: `backend/walls.py:196-213` (new "free walls: sending geometry" section between `broadcast_bye` and `# ---- playlist loading`)
- Modify: `backend/walls.py:492-545` (`_tick_once`; new `_geometry_fallback` right above it)
- Modify: `backend/walls.py:574-611` (`wall_ws`; new `_screen_px`, `_screen_fit` and `_on_screen_report` right above it)
- Create: `backend/tests/test_walls_live.py`
- Test: `backend/tests/test_walls_live.py`. These must still pass: `backend/tests/test_walls_phase2.py`, `test_walls_timeline.py`,
  `test_walls_websocket.py`, `test_walls_tick_loop.py`, `test_walls_pairing.py`, `test_walls_redeem.py`, `test_walls_crud.py`,
  `test_ratelimit.py`.

**Interfaces:**
- Consumes:
  - Task 1: `walls.layout`; `wall_cells.place_x/place_y/place_w/place_h` (REAL); `wall_cells.screen_w_px/screen_h_px` (INTEGER); `wall_cells.highlight_until_ms` (BIGINT).
  - Task 3 (`backend/wall_layout.py`):
    - `canvas_size_for(width_px, height_px) -> tuple[int, int]`
    - `box_to_px(box, canvas_w, canvas_h) -> dict`
    - `validate_box(box) -> bool`
    - `has_default_shape(box, canvas_w, canvas_h) -> bool`
    - `fit_box_to_screen(box, screen_w, screen_h, canvas_w, canvas_h) -> dict`
  - Existing code:
    - `ratelimit.REDIS_URL`, `ratelimit.get_redis()`, `ratelimit._mark_unhealthy(exc)`
    - in walls.py: `_run_on_server_loop`, `_send_safe`, `_register`, `_unregister`, `_connections`, `_conn_identity`, `_tick_tasks`, `now_ms`, `_hello_config`, `_drop_unbound`
- Produces, in `backend/walls.py`:
  - Constants: `HIGHLIGHT_SECONDS = 30`; `GEOMETRY_CHANNEL_BASE = "khan:wall-geometry"`.
  - `geometry_channel() -> str` returns `"khan:wall-geometry:db<N>"`. N is `ratelimit.REDIS_URL`'s logical DB, parsed by redis-py's own `parse_url`, so `?db=` wins over the path. The URL is read at call time.
  - Frame helpers: `tv_number(cell) -> int`, `free_cell_geometry(wall, cell) -> dict | None`, `_geometry_frame(wall, cell) -> dict`, `_geom_sig(wall, cell) -> tuple`, and the state `_geom_sent: Dict[object, tuple]`.
  - `publish_geometry(wall_id: int, row: int, col: int) -> None`. It is sync and never raises. **Task 7 calls `walls_mod.publish_geometry(wall_id, row, col)` only after the write that changed the box has committed, outside any `with transaction():` block (R5)**, because both the local push and the other workers re-read the box from the database. The same goes for `broadcast_bye` after a free-wall unpair.
  - Delivery: `async _push_geometry(wall_id, row, col)`, `_ensure_geometry_subscriber()` (called from `wall_ws` for free-wall sockets only, R7: a worker holding only grid or mirrored TVs opens no Redis subscription), `async _geometry_subscriber()`.
  - Screen report: `async _on_screen_report(wall_id, key, ws, cell_id, msg)`. Its two reads, at most one UPDATE and one re-read are sync statements on the event loop, with no transaction (R6).
  - Private to this task:
    - helpers `_cell_box`, `_free_canvas`, `_is_free_box`, `_highlight_ms`, `_load_free_cell`, `_send_geometry_if_changed`, `_geometry_fallback`, `_on_geometry_message`, `_screen_px`, `_screen_fit`
    - constants `GEOMETRY_POLL_S`, `GEOMETRY_RETRY_MIN_S`, `GEOMETRY_RETRY_MAX_S`, `SCREEN_MIN_PX`, `SCREEN_MAX_PX`, `SCREEN_SHAPE_TOLERANCE`
    - state `_geometry_task`
  - Wire format, for Task 8 (player):
    - **Free-wall `hello`**, only when the cell has a box. It has the usual keys plus:
      - `canvas {w, h}`
      - `bezel {"h_pct": 0.0, "v_pct": 0.0}`
      - `cell_geometry {x, y, w, h}`: canvas px, floats; it may be negative or reach past the canvas.
      - `highlight_until_ms` (int or null) and `tv_number` (int).
    - **Grid-wall `hello`:** byte-identical to today's (R22). It has no `tv_number` or `highlight_until_ms`, and a stored box on a grid cell is ignored.
    - **Server to TV:** `{"type": "geometry", "canvas": {w, h}, "cell_geometry": {x, y, w, h}, "highlight_until_ms": int|null, "tv_number": int, "server_now_ms": int}`. Only free-wall TVs get it.
    - **`highlight_until_ms`** is sent as stored, even after that time has passed (R9). No frame is sent when it passes: the player hides the outline on its own timer against the server clock.
    - **TV to server:** `{"type": "screen", "w": <int>, "h": <int>}`.
      - `w` and `h` must be JSON integers; floats, strings and bools are ignored. The player sends `document.documentElement.clientWidth/clientHeight`, which are integers.
      - Values are clamped to 100..10000.
      - Any non-object JSON message is ignored, where before it crashed the socket.
  - Test helpers in `backend/tests/test_walls_live.py`: `FakeWS` and `_recv_until(ws, type_, timeout=2.0)`.
    - Later test files import them with `from tests.test_walls_live import FakeWS, _recv_until`. I checked that this does not collect the module's tests twice.
    - The module's autouse `_clean_registry` fixture does not come along with the import, so an importing file needs its own cleanup.
    - `_recv_until` returns None on timeout and raises `WebSocketDisconnect` on a close frame.

#### Cycle 1: free-wall hello, geometry frame, channel name

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_walls_live.py` with exactly this content (later cycles append to it):

```python
"""Live geometry for phone-placed ("free") video walls.

A free wall's TV shows its own box of the wall's video. hello carries the
box; "geometry" frames move it live. The placement PUT calls
publish_geometry: the worker holding the TV's socket pushes the frame, the
other workers hear about it on Redis, and the tick loop is the fallback. A TV
also reports its screen size, and a box still at its 16:9 default takes the
TV's shape.

No pytest-asyncio: async code runs under asyncio.run() against FakeWS
sockets. TestClient's receive_json() has no timeout, so real sockets are read
through _recv_until().
"""
import asyncio
import json
import logging
import queue
import secrets
import time
import uuid

import anyio
import pytest
import redis
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import ratelimit
import walls as walls_mod
from db import execute, query_one, utc_now_iso
from main import app

PLACE = ("place_x", "place_y", "place_w", "place_h")


class FakeWS:
    """Stands in for a starlette WebSocket held in walls._connections."""

    def __init__(self):
        self.frames = []
        self.closed_with = None

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_with = code

    def of_type(self, kind):
        return [f for f in self.frames if f.get("type") == kind]


def _recv_until(ws, type_, timeout=2.0):
    """The next frame of type ``type_`` from a TestClient websocket, or None.

    Starlette 0.37.2's WebSocketTestSession.receive_json() blocks on
    ``_send_queue.get()`` with no timeout, so a frame that never comes would
    hang the suite. This reads the same queue.Queue with a deadline and skips
    frames of other types (hello, play, ping). The queue holds ASGI messages:
    {"type": "websocket.send", "text": ...}, {"type": "websocket.close", ...},
    or an exception the app raised.
    """
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        try:
            message = ws._send_queue.get(timeout=left)
        except queue.Empty:
            return None
        if isinstance(message, BaseException):
            raise message
        if message["type"] == "websocket.close":
            raise WebSocketDisconnect(code=message.get("code", 1000))
        if message["type"] != "websocket.send":
            continue
        frame = json.loads(message["text"])
        if frame.get("type") == type_:
            return frame


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    walls_mod._connections.clear()
    walls_mod._conn_identity.clear()
    walls_mod._tick_tasks.clear()
    walls_mod._geom_sent.clear()
    task, walls_mod._geometry_task = walls_mod._geometry_task, None
    if task is not None and not task.done():
        try:
            task.get_loop().call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass                        # its loop is closed: nothing left to cancel


def _org():
    slug = "lv" + secrets.token_hex(3)
    return execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"LV {slug}", slug, "active", 10, utc_now_iso()),
    )


def _free_wall(boxes, canvas=(3840, 720)):
    """A free wall with no playlist; cell i sits at (0, i) with box boxes[i] = (x, y, w, h)."""
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, layout, rows, cols, canvas_width_px, "
        "canvas_height_px, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "Live", "spanned", "free", 1, len(boxes), canvas[0], canvas[1],
         utc_now_iso(), utc_now_iso()),
    )
    cids = [
        execute(
            "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
            "place_h, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, 0, i, x, y, w, h, utc_now_iso()),
        )
        for i, (x, y, w, h) in enumerate(boxes)
    ]
    return wid, cids


def _grid_wall():
    """A 1x2 spanned grid wall; its cell (0, 0) carries a stray box that must be ignored."""
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, canvas_width_px, "
        "canvas_height_px, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_org(), "Grid", "spanned", 1, 2, 3840, 2160, utc_now_iso(), utc_now_iso()),
    )
    cid = execute(
        "INSERT INTO wall_cells (wall_id, row_index, col_index, place_x, place_y, place_w, "
        "place_h, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, 0, 0, 0.0, 0.0, 0.5, 1.0, utc_now_iso()),
    )
    return wid, cid


def _bind_tv(wid, cid):
    """A TV screen paired into cell ``cid``, linked both ways as redeem leaves it."""
    oid = query_one("SELECT organization_id FROM walls WHERE id = ?", (wid,))["organization_id"]
    token = uuid.uuid4().hex
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, wall_cell_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, "TV", "lv" + secrets.token_hex(6), token, cid, utc_now_iso()),
    )
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (sid, cid))
    return sid, token


def _cell(cid):
    return query_one("SELECT * FROM wall_cells WHERE id = ?", (cid,))


def _key(cid):
    cell = _cell(cid)
    return (cell["row_index"], cell["col_index"])


def _registered(wid, cid):
    """A FakeWS registered for cell ``cid`` as wall_ws registers a paired TV."""
    sid, token = _bind_tv(wid, cid)
    ws = FakeWS()
    walls_mod._register(wid, _key(cid), ws, sid, token)
    return ws


def _held(wid, cid):
    """A FakeWS this worker holds for cell ``cid``, already sent its box (as by hello)."""
    ws = FakeWS()
    walls_mod._connections[wid][_key(cid)] = ws
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wid,))
    walls_mod._geom_sent[ws] = walls_mod._geom_sig(wall, _cell(cid))
    return ws


# ---- hello and geometry frames ----------------------------------------------

FREE_WALL = {"id": 9, "mode": "spanned", "layout": "free", "rows": 1, "cols": 3,
             "canvas_width_px": 3840, "canvas_height_px": 720,
             "bezel_h_pct": 0.0, "bezel_v_pct": 0.0}


def test_free_wall_hello_carries_the_box_in_canvas_px():
    cell = {"row_index": 0, "col_index": 1, "place_x": 0.25, "place_y": -0.5,
            "place_w": 0.5, "place_h": 2.0, "highlight_until_ms": 1762000030000}
    frame = walls_mod._hello_frame(FREE_WALL, cell, current_play=None)
    assert frame["mode"] == "spanned"
    assert frame["cell"] == {"row": 0, "col": 1, "rows": 1, "cols": 3}
    assert frame["canvas"] == {"w": 3840, "h": 720}
    assert frame["bezel"] == {"h_pct": 0.0, "v_pct": 0.0}
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 960.0, "y": -360.0, "w": 1920.0, "h": 1440.0})
    assert frame["tv_number"] == 2
    assert frame["highlight_until_ms"] == 1762000030000
    json.dumps(frame)                   # _send_safe drops a frame json.dumps refuses


def test_free_wall_hello_without_a_highlight_says_null():
    cell = {"row_index": 0, "col_index": 0, "place_x": 0.0, "place_y": 0.0,
            "place_w": 1 / 3, "place_h": 1.0, "highlight_until_ms": None}
    frame = walls_mod._hello_frame(FREE_WALL, cell, current_play=None)
    assert frame["highlight_until_ms"] is None
    assert frame["tv_number"] == 1
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})


def test_grid_hello_ignores_a_stored_box():
    """Guards the grid branch (R22: today's hello, key for key); passes before and after this task."""
    wall = dict(FREE_WALL, layout="grid", cols=2, canvas_height_px=2160)
    cell = {"row_index": 0, "col_index": 1, "place_x": 0.1, "place_y": 0.1,
            "place_w": 0.2, "place_h": 0.2, "highlight_until_ms": 5}
    frame = walls_mod._hello_frame(wall, cell, current_play=None)
    assert frame["cell_geometry"] == {"x": 1920.0, "y": 0.0, "w": 1920.0, "h": 2160.0}
    assert "tv_number" not in frame and "highlight_until_ms" not in frame
    assert set(frame) == {"type", "wall_id", "mode", "cell", "current_play", "server_now_ms",
                          "canvas", "bezel", "cell_geometry"}


def test_free_wall_cell_without_a_box_gets_its_grid_slice():
    """Guards the fallback for a cell with no box; passes before and after this task."""
    frame = walls_mod._hello_frame(FREE_WALL, {"row_index": 0, "col_index": 2},
                                   current_play=None)
    assert frame["cell_geometry"] == pytest.approx(
        {"x": 2560.0, "y": 0.0, "w": 1280.0, "h": 720.0})
    assert "tv_number" not in frame


def test_geometry_frame_has_the_box_highlight_and_tv_number(monkeypatch):
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1762000000000)
    cell = {"row_index": 0, "col_index": 1, "place_x": 1 / 3, "place_y": 0.0,
            "place_w": 1 / 3, "place_h": 1.0, "highlight_until_ms": 1762000030000}
    frame = walls_mod._geometry_frame(FREE_WALL, cell)
    assert frame == {
        "type": "geometry",
        "canvas": {"w": 3840, "h": 720},
        "cell_geometry": pytest.approx({"x": 1280.0, "y": 0.0, "w": 1280.0, "h": 720.0}),
        "highlight_until_ms": 1762000030000,
        "tv_number": 2,
        "server_now_ms": 1762000000000,
    }
    json.dumps(frame)


def test_free_cell_geometry_needs_all_four_box_fields():
    cell = {"row_index": 0, "col_index": 0, "place_x": 0.0, "place_y": 0.0,
            "place_w": 0.5, "place_h": None}
    assert walls_mod.free_cell_geometry(FREE_WALL, cell) is None
    assert walls_mod.free_cell_geometry(FREE_WALL, dict(cell, place_h=1.0)) == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 720.0})


def test_geom_sig_follows_box_canvas_and_highlight_only():
    wall = {"canvas_width_px": 3840, "canvas_height_px": 720}
    cell = {"place_x": 0.0, "place_y": 0.0, "place_w": 0.5, "place_h": 1.0,
            "highlight_until_ms": None, "screen_w_px": 1920, "screen_h_px": 1080}
    sig = walls_mod._geom_sig(wall, cell)
    assert sig == (3840, 720, 0.0, 0.0, 0.5, 1.0, None)
    assert walls_mod._geom_sig(wall, dict(cell, place_x=0.1)) != sig
    assert walls_mod._geom_sig(wall, dict(cell, highlight_until_ms=5)) != sig
    assert walls_mod._geom_sig(dict(wall, canvas_width_px=5760), cell) != sig
    assert walls_mod._geom_sig(wall, dict(cell, screen_w_px=1080)) == sig   # size is not geometry


def test_tv_number_and_highlight_length():
    assert walls_mod.tv_number({"row_index": 0, "col_index": 4}) == 5
    assert walls_mod.HIGHLIGHT_SECONDS == 30


@pytest.mark.parametrize("url,channel", [
    ("redis://redis:6379/0", "khan:wall-geometry:db0"),
    ("redis://redis:6379", "khan:wall-geometry:db0"),
    ("redis://redis:6379/5", "khan:wall-geometry:db5"),
    ("redis://:secret@redis:6379/12", "khan:wall-geometry:db12"),
    ("redis://redis:6379/4?db=3", "khan:wall-geometry:db3"),
])
def test_geometry_channel_is_per_redis_database(monkeypatch, url, channel):
    monkeypatch.setattr(ratelimit, "REDIS_URL", url)
    assert walls_mod.geometry_channel() == channel


def test_test_runs_never_use_productions_channel():
    # scripts/test-backend-in-docker.sh refuses KHAN_TEST_REDIS_DB=0.
    assert walls_mod.geometry_channel() != f"{walls_mod.GEOMETRY_CHANNEL_BASE}:db0"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py -v`

Expected: FAIL, `12 failed, 2 passed, 14 errors`.
- The two free-wall hello tests fail on `cell_geometry`: they get the grid slice, not the box.
- The other ten fail with `AttributeError: module 'walls' has no attribute ...`: `_geometry_frame`, `free_cell_geometry`, `_geom_sig`, `tv_number`, `geometry_channel`.
- `test_grid_hello_ignores_a_stored_box` and `test_free_wall_cell_without_a_box_gets_its_grid_slice` pass. They guard the grid branch.
- Every test also reports a teardown ERROR from `_clean_registry`, because `walls._geom_sent` does not exist yet.

- [ ] **Step 3: Implement**

All edits are in `backend/walls.py`.

**3a. Imports** (line 32).

Replace this exact text:

```python
from db import query_one, query_all, execute, utc_now_iso
```

with:

```python
import ratelimit
import wall_layout
from db import query_one, query_all, execute, utc_now_iso
```

**3b. Module state** (lines 46-47). `_geometry_task` is used from cycle 3 on; it is added now so the test fixture is final from the start.

Replace this exact text:

```python
# The event loop serving this worker's sockets, for callers on other threads.
_server_loop: Optional[asyncio.AbstractEventLoop] = None
```

with:

```python
# The event loop serving this worker's sockets, for callers on other threads.
_server_loop: Optional[asyncio.AbstractEventLoop] = None
# WebSocket -> _geom_sig of the box it was last sent (in hello or geometry).
# One record per socket, shared by the connect hello, the tick-loop fallback,
# the Redis subscriber and the screen handler, so none repeats another.
_geom_sent: Dict[object, tuple] = {}
# This worker's Redis geometry subscriber (see _ensure_geometry_subscriber).
_geometry_task: Optional[asyncio.Task] = None
```

**3c. Constants** (lines 60-61).

Replace this exact text:

```python
# Every worker anchors timelines here, which is what makes them agree.
TIMELINE_EPOCH_MS = 0
```

with:

```python
# Every worker anchors timelines here, which is what makes them agree.
TIMELINE_EPOCH_MS = 0
# A placed TV shows its outline and number until this long after the phone's
# last move: main's placement PUT stores now + HIGHLIGHT_SECONDS in
# wall_cells.highlight_until_ms, so every worker and a reconnecting TV agree.
# Frames carry that deadline as stored. Nothing is sent when it passes: the
# TV hides the outline on its own timer, against the server clock.
HIGHLIGHT_SECONDS = 30
# Redis pub/sub channel for "the box of free-wall cell (row, col) changed";
# geometry_channel() appends REDIS_URL's logical database.
GEOMETRY_CHANNEL_BASE = "khan:wall-geometry"
```

**3d. `_hello_frame`** (line 94). The free-wall branch goes before the grid branch, which is unchanged and becomes an `elif`. `_is_free_box` uses `wall.get("layout")` and `cell.get("place_x")`: the existing unit tests pass partial dicts without those keys.

Replace this exact text:

```python
        "server_now_ms": now_ms(),
    }
    if wall["mode"] == "spanned":
        cw = wall["canvas_width_px"]
```

with:

```python
        "server_now_ms": now_ms(),
    }
    if _is_free_box(wall, cell):
        # A free wall: the TV shows its own box, placed from a phone, so its
        # geometry never depends on rows/cols. Bezel gaps are left by placing
        # the boxes, never computed.
        cw, ch = _free_canvas(wall)
        base["canvas"] = {"w": cw, "h": ch}
        base["bezel"] = {"h_pct": 0.0, "v_pct": 0.0}
        base["cell_geometry"] = free_cell_geometry(wall, cell)
        base["highlight_until_ms"] = _highlight_ms(cell)
        base["tv_number"] = tv_number(cell)
    elif wall["mode"] == "spanned":
        cw = wall["canvas_width_px"]
```

**3e. The free-wall geometry helpers**, after `_hello_config` (lines 115-120).

Replace this exact text:

```python
        "canvas_width_px", "canvas_height_px", "bezel_h_pct", "bezel_v_pct",
    ))
```

with:

```python
        "canvas_width_px", "canvas_height_px", "bezel_h_pct", "bezel_v_pct",
    ))


# ---- free walls: geometry ---------------------------------------------------
#
# A free wall (layout 'free', mode 'spanned') is one video with a box per TV,
# placed from a phone. Boxes are stored as fractions of the canvas
# (wall_cells.place_x/y/w/h); TVs are sent them in canvas px.

def geometry_channel() -> str:
    """Redis channel for box changes: GEOMETRY_CHANNEL_BASE + ":db<N>".

    N is the logical database in ratelimit.REDIS_URL, read at call time (0 when
    the URL names none). Pub/sub ignores logical databases, and test runs use
    production's Redis server on another database: without the suffix a
    test's publish would reach production's subscribers.
    """
    from redis.connection import parse_url   # lazy, like ratelimit's own redis import
    try:
        db = int(parse_url(ratelimit.REDIS_URL).get("db") or 0)
    except (TypeError, ValueError):
        db = 0
    return f"{GEOMETRY_CHANNEL_BASE}:db{db}"


def tv_number(cell: dict) -> int:
    """The number a free-wall TV is shown as ("TV 2"); fixed while it is in the wall."""
    return int(cell["col_index"]) + 1


def _cell_box(cell: dict) -> dict | None:
    """The cell's stored box as fractions of the canvas, or None if it has none."""
    values = [cell.get(k) for k in ("place_x", "place_y", "place_w", "place_h")]
    if any(v is None for v in values):
        return None
    x, y, w, h = (float(v) for v in values)
    return {"x": x, "y": y, "w": w, "h": h}


def _free_canvas(wall: dict) -> tuple[int, int]:
    return wall_layout.canvas_size_for(wall.get("canvas_width_px"),
                                       wall.get("canvas_height_px"))


def _is_free_box(wall: dict, cell: dict) -> bool:
    """True when ``cell`` is a placed TV of a free wall: its geometry is its box."""
    return (wall.get("mode") == "spanned" and wall.get("layout") == "free"
            and _cell_box(cell) is not None)


def _highlight_ms(cell: dict) -> int | None:
    value = cell.get("highlight_until_ms")
    return int(value) if value is not None else None


def free_cell_geometry(wall: dict, cell: dict) -> dict | None:
    """The cell's box in canvas px ({"x", "y", "w", "h"}), or None without a box."""
    box = _cell_box(cell)
    if box is None:
        return None
    cw, ch = _free_canvas(wall)
    return wall_layout.box_to_px(box, cw, ch)


def _geometry_frame(wall: dict, cell: dict) -> dict:
    """Moves a free-wall TV's crop without touching what it plays."""
    cw, ch = _free_canvas(wall)
    return {
        "type": "geometry",
        "canvas": {"w": cw, "h": ch},
        "cell_geometry": free_cell_geometry(wall, cell),
        "highlight_until_ms": _highlight_ms(cell),
        "tv_number": tv_number(cell),
        "server_now_ms": now_ms(),
    }


def _geom_sig(wall: dict, cell: dict) -> tuple:
    """Everything a geometry frame depends on: a TV is sent one when this changes."""
    return (wall.get("canvas_width_px"), wall.get("canvas_height_px"),
            cell.get("place_x"), cell.get("place_y"),
            cell.get("place_w"), cell.get("place_h"),
            cell.get("highlight_until_ms"))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py tests/test_walls_phase2.py -v`
Expected: PASS. `test_walls_live.py`: 14 passed. `test_walls_phase2.py` still passes; its two `_hello_frame` tests pass partial dicts with no `layout` or `place_*`.

- [ ] **Step 5: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/walls.py backend/tests/test_walls_live.py
git commit -m "feat(walls): free-wall hello and geometry frames carry each TV's box

A free wall's hello now carries the TV's own box (canvas px), its number
and its highlight deadline instead of a grid slice. Adds the geometry
frame, its de-dup signature and the per-Redis-database channel name.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

#### Cycle 2: tick-loop fallback

- [ ] **Step 6: Write the failing test**

Append to `backend/tests/test_walls_live.py`:

```python


# ---- tick-loop fallback -----------------------------------------------------

def _tick(wid, state, ws):
    """One tick-loop pass; returns the geometry frames it sent ``ws``."""
    ws.frames.clear()
    asyncio.run(walls_mod._tick_once(wid, state))
    return ws.of_type("geometry")


def test_tick_sends_geometry_once_per_change_of_box_or_highlight(monkeypatch):
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = _registered(wid, cid)
    state = {}
    first = _tick(wid, state, ws)          # no hello went through wall_ws: nothing recorded
    assert len(first) == 1
    assert first[0]["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})
    assert first[0]["tv_number"] == 1
    assert _tick(wid, state, ws) == []     # unchanged: nothing

    execute("UPDATE wall_cells SET place_x = ? WHERE id = ?", (0.5, cid))
    moved = _tick(wid, state, ws)
    assert len(moved) == 1
    assert moved[0]["cell_geometry"]["x"] == pytest.approx(1920.0)
    assert _tick(wid, state, ws) == []

    execute("UPDATE wall_cells SET highlight_until_ms = ? WHERE id = ?", (1762000030000, cid))
    assert [g["highlight_until_ms"] for g in _tick(wid, state, ws)] == [1762000030000]
    # R9: the deadline passing changes nothing stored. The TV's own timer hides
    # the outline, so the server sends nothing then.
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1762000030000 + 60_000)
    assert _tick(wid, state, ws) == []
    assert ws.closed_with is None          # still the cell's TV: _drop_unbound kept it


def test_tick_sends_the_stored_box_not_the_passs_older_copy(monkeypatch):
    """A box pushed through Redis during a pass is newer than the pass's cell rows."""
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = _registered(wid, cid)
    stale = _cell(cid)                     # what the pass's cell query will return
    execute("UPDATE wall_cells SET place_x = ? WHERE id = ?", (0.5, cid))   # moved meanwhile
    real_query_all = walls_mod.query_all

    def cells_from_before_the_move(sql, params=()):
        if sql.startswith("SELECT * FROM wall_cells WHERE wall_id"):
            return [stale]
        return real_query_all(sql, params)

    monkeypatch.setattr(walls_mod, "query_all", cells_from_before_the_move)
    state = {}
    assert [g["cell_geometry"]["x"] for g in _tick(wid, state, ws)] == [pytest.approx(1920.0)]
    # The TV has the newest box now; the pass's older copy must not undo it.
    assert _tick(wid, state, ws) == []


def test_re_hello_after_a_wall_change_carries_the_tvs_box():
    wid, (c0, c1) = _free_wall([(0.0, 0.0, 1 / 3, 1.0), (0.4, 0.1, 0.3, 0.9)])
    ws = _registered(wid, c1)
    state = {}
    _tick(wid, state, ws)                  # records the wall's config
    execute("UPDATE walls SET cols = 3 WHERE id = ?", (wid,))     # a third TV joined
    assert _tick(wid, state, ws) == []     # the hello carried the box: no geometry repeats it
    hellos = ws.of_type("hello")
    assert len(hellos) == 1
    assert hellos[0]["cell_geometry"] == pytest.approx(
        {"x": 1536.0, "y": 72.0, "w": 1152.0, "h": 648.0}, rel=1e-5)
    assert hellos[0]["tv_number"] == 2


def test_tick_sends_no_geometry_on_grid_walls(monkeypatch):
    """Guards the layout check (R22: a grid pass is today's); passes before and after this task."""
    wid, cid = _grid_wall()
    ws = _registered(wid, cid)
    queries = []
    real_query_all = walls_mod.query_all

    def recording_query_all(sql, params=()):
        queries.append(sql)
        return real_query_all(sql, params)

    monkeypatch.setattr(walls_mod, "query_all", recording_query_all)
    assert _tick(wid, {}, ws) == []
    assert "SELECT * FROM wall_cells WHERE wall_id = ?" not in queries   # no box read for grid walls


def test_unregister_forgets_what_the_socket_was_sent():
    ws = FakeWS()
    walls_mod._register(4343, (0, 0), ws, 1, "t" * 32)
    walls_mod._geom_sent[ws] = ("sig",)
    walls_mod._unregister(4343, (0, 0), ws)
    assert ws not in walls_mod._geom_sent
```

- [ ] **Step 7: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py -v`

Expected: FAIL, `4 failed, 15 passed`.

| Failing test | How it fails |
|---|---|
| `test_tick_sends_geometry_once_per_change_of_box_or_highlight` | `assert 0 == 1`: the tick sends no geometry |
| `test_tick_sends_the_stored_box_not_the_passs_older_copy` | `[] == [1920.0 ± ...]` |
| `test_re_hello_after_a_wall_change_carries_the_tvs_box` | The re-hello's `cell_geometry` is the grid slice (x 1280), not the box (x 1536) |
| `test_unregister_forgets_what_the_socket_was_sent` | The socket is still in `_geom_sent` |

`test_tick_sends_no_geometry_on_grid_walls` passes; it guards the layout check.

- [ ] **Step 8: Implement**

All edits are in `backend/walls.py`.

**8a. `_unregister`** (lines 130-135): forget what a socket was sent.

Replace this exact text:

```python
    if conns is not None and conns.get(key) is ws:
        conns.pop(key, None)
    _conn_identity.pop(ws, None)
```

with:

```python
    if conns is not None and conns.get(key) is ws:
        conns.pop(key, None)
    _conn_identity.pop(ws, None)
    _geom_sent.pop(ws, None)
```

**8b. Loading and sending a box**, between `broadcast_bye` and `# ---- playlist loading` (lines 210-213).

Replace this exact text:

```python
    _run_on_server_loop(_say_bye())


# ---- playlist loading -------------------------------------------------------
```

with:

```python
    _run_on_server_loop(_say_bye())


# ---- free walls: sending geometry -------------------------------------------

def _load_free_cell(wall_id: int, row: int, col: int):
    """(wall, cell) as stored now, if (row, col) is a cell of free wall ``wall_id``."""
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall or wall.get("layout") != "free":
        return None
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        return None
    return wall, cell


async def _send_geometry_if_changed(wall_id: int, key: Tuple[int, int], ws,
                                    wall: dict, cell: dict) -> None:
    """Send ``ws`` the cell's box unless it was already sent exactly this one."""
    if not _is_free_box(wall, cell):
        return
    sig = _geom_sig(wall, cell)
    if _geom_sent.get(ws) == sig:
        return
    # Recorded before the await: a newer box read while this frame is in
    # flight differs from it, so it is sent too, after this one.
    _geom_sent[ws] = sig
    if not await _send_safe(ws, _geometry_frame(wall, cell)):
        _unregister(wall_id, key, ws)


# ---- playlist loading -------------------------------------------------------
```

**8c. `_tick_once`.** Replace the whole `_tick_once` function (lines 492-545, from `async def _tick_once` through its
final `return max(TICK_MIN_SLEEP_S, min(TICK_MAX_SLEEP_S, until_next))`) with the following. It also adds
`_geometry_fallback` right above `_tick_once`. Here is what changes:
- For free walls, `_tick_once` loads `SELECT * FROM wall_cells WHERE wall_id = ?` once per pass. Grid walls skip
  that read (R22), so their pass and their re-hello (built from `{"row_index", "col_index"}`, as today) are unchanged.
- A free wall's re-hello uses the real cell row and records what it sent.
- For free walls, the pass ends with `_geometry_fallback`. When the pass's copy of a cell differs from what its TV was last sent, the fallback re-reads the cell before sending: a box pushed through Redis while this pass awaited is newer than that copy.

The play loop, ping and sleep are unchanged. `_hello_config` is NOT changed: putting `place_*` or the highlight in it would turn every drag into a full re-hello.

```python
async def _geometry_fallback(wall_id: int, wall: dict, conns: dict, cells: dict) -> None:
    """Send each local TV of a free wall its box if it changed since it was last sent.

    publish_geometry normally gets there first; this catches what it missed
    (Redis down, a lost publish) within TICK_MAX_SLEEP_S.
    """
    for key, ws in conns.items():
        if _connections.get(wall_id, {}).get(key) is not ws:
            continue                       # left or replaced during this pass
        cell = cells.get(key)
        if cell is None or _geom_sent.get(ws) == _geom_sig(wall, cell):
            continue
        # Re-read before sending: this pass has awaited since ``cells`` was
        # loaded, and a box pushed meanwhile (through Redis) is newer than it.
        fresh = _load_free_cell(wall_id, key[0], key[1])
        if fresh is not None:
            await _send_geometry_if_changed(wall_id, key, ws, *fresh)


async def _tick_once(wall_id: int, state: dict) -> float | None:
    """One pass of the tick loop. Returns seconds to sleep, or None to stop."""
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        # Its cells are gone with it; the TVs' screens survive as standalone.
        for key, ws in list(_connections.get(wall_id, {}).items()):
            await _send_safe(ws, {"type": "wall_left", "server_now_ms": now_ms()})
            _unregister(wall_id, key, ws)
            await _close_quietly(ws, 4000)
        return None
    await _drop_unbound(wall_id, dict(_connections.get(wall_id, {})))
    conns = dict(_connections.get(wall_id, {}))
    if not conns:
        return None
    # A free wall's hello and geometry come from each cell's own box, so its
    # stored cells are read once per pass. A grid wall's hello needs only
    # (row, col), so grid passes skip the read and stay as they were.
    cells = {}
    if wall.get("layout") == "free":
        cells = {(c["row_index"], c["col_index"]): c
                 for c in query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (wall_id,))}

    now = now_ms()
    plan = _wall_plan(wall, now)
    sent: dict = state.setdefault("sent", {})
    config = _hello_config(wall)
    if state.get("config") not in (None, config):
        # Mode / grid / canvas changed under connected TVs: re-introduce the
        # wall so they rebuild their geometry instead of mis-cropping.
        sent.clear()
        for (r, c), ws in conns.items():
            cell = cells.get((r, c)) or {"row_index": r, "col_index": c}
            hello = _hello_frame(wall, cell, _frame_for(plan, (r, c)))
            if _is_free_box(wall, cell):
                _geom_sent[ws] = _geom_sig(wall, cell)
            if not await _send_safe(ws, hello):
                _unregister(wall_id, (r, c), ws)
            elif hello["current_play"]:
                cp = hello["current_play"]
                sent[(r, c)] = (cp["item"]["id"], cp["started_at_ms"])
    state["config"] = config

    for key, ws in conns.items():
        frame = _frame_for(plan, key)
        if frame is None:
            continue
        mark = (frame["item"]["id"], frame["started_at_ms"])
        if sent.get(key) == mark:
            continue
        if await _send_safe(ws, frame):
            sent[key] = mark
        else:
            _unregister(wall_id, key, ws)

    if wall.get("layout") == "free":
        await _geometry_fallback(wall_id, wall, conns, cells)

    if now - state.get("last_ping_ms", 0) >= PING_INTERVAL_MS:
        state["last_ping_ms"] = now
        await broadcast(wall_id, {"type": "ping", "server_now_ms": now})

    if plan:
        until_next = (plan["ends_at_ms"] - now_ms()) / 1000.0
    else:
        until_next = TICK_MAX_SLEEP_S
    return max(TICK_MIN_SLEEP_S, min(TICK_MAX_SLEEP_S, until_next))
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py tests/test_walls_timeline.py tests/test_walls_tick_loop.py -v`
Expected: PASS. `test_walls_live.py`: 19 passed. `test_walls_timeline.py` and `test_walls_tick_loop.py` pass unchanged. Their FakeWS sockets sit on walls with the default `layout = 'grid'`, which never get geometry and skip the new cell read.

- [ ] **Step 10: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/walls.py backend/tests/test_walls_live.py
git commit -m "feat(walls): tick loop re-sends a free-wall TV's box when it changes

Each free-wall pass loads the wall's cell rows once, re-hellos with each
TV's real box, and sends a geometry frame when a cell's box or highlight
differs from what its socket was last sent (re-reading first, so an older
copy never overwrites a newer box). Grid-wall passes are unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

#### Cycle 3: the live path: local push, Redis publish, subscriber

- [ ] **Step 11: Write the failing test**

Append to `backend/tests/test_walls_live.py`:

```python


# ---- the live path: push, publish, subscribe --------------------------------

class _RecordingRedis:
    """Stands in for ratelimit.get_redis()'s client; records what was published."""

    def __init__(self):
        self.published = []

    def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return 0


def test_push_geometry_sends_each_box_once():
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))     # Redis echo of the local push
    assert len(ws.of_type("geometry")) == 1
    execute("UPDATE wall_cells SET place_x = ?, highlight_until_ms = ? WHERE id = ?",
            (0.25, 1762000030000, cid))
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    frames = ws.of_type("geometry")
    assert len(frames) == 2
    assert frames[1]["cell_geometry"]["x"] == pytest.approx(960.0)
    assert frames[1]["highlight_until_ms"] == 1762000030000


def test_push_geometry_reads_nothing_for_a_tv_held_elsewhere(monkeypatch):
    def no_database(*args, **kwargs):
        raise AssertionError("read the database for a socket this worker does not hold")

    monkeypatch.setattr(walls_mod, "query_one", no_database)
    asyncio.run(walls_mod._push_geometry(424242, 0, 0))


def test_push_geometry_sends_nothing_on_grid_walls():
    wid, cid = _grid_wall()
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    asyncio.run(walls_mod._push_geometry(wid, 0, 0))
    assert ws.frames == []


def test_publish_geometry_from_a_rest_thread_reaches_a_local_tv_and_redis(monkeypatch):
    fake = _RecordingRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    wid, (cid,) = _free_wall([(0.0, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()

    async def scenario():
        walls_mod._connections[wid][(0, 0)] = ws
        # REST handlers are sync endpoints, run in AnyIO worker threads.
        await anyio.to_thread.run_sync(walls_mod.publish_geometry, wid, 0, 0)
        for _ in range(100):
            if ws.frames:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert len(ws.of_type("geometry")) == 1
    assert fake.published == [
        (walls_mod.geometry_channel(), {"wall_id": wid, "row": 0, "col": 0})]


def test_publish_geometry_for_a_tv_held_elsewhere_only_publishes(monkeypatch):
    fake = _RecordingRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    scheduled = []

    def record(coro):
        scheduled.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(walls_mod, "_run_on_server_loop", record)
    walls_mod.publish_geometry(434343, 0, 1)
    assert scheduled == []
    assert fake.published == [
        (walls_mod.geometry_channel(), {"wall_id": 434343, "row": 0, "col": 1})]


def test_publish_geometry_never_raises_when_redis_is_down(monkeypatch):
    marked = []

    class _DownRedis:
        def publish(self, channel, message):
            raise redis.exceptions.ConnectionError("Error 111 connecting to redis:6379")

    monkeypatch.setattr(ratelimit, "get_redis", lambda: _DownRedis())
    monkeypatch.setattr(ratelimit, "_mark_unhealthy", marked.append)
    walls_mod.publish_geometry(434343, 0, 0)
    assert len(marked) == 1 and isinstance(marked[0], redis.exceptions.ConnectionError)

    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)   # in its cooldown
    walls_mod.publish_geometry(434343, 0, 0)
    assert len(marked) == 1


def test_geometry_subscriber_starts_once_per_event_loop(monkeypatch):
    loops = []

    async def idle_subscriber():
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(3600)

    monkeypatch.setattr(walls_mod, "_geometry_subscriber", idle_subscriber)

    async def two_sockets_connect():
        walls_mod._ensure_geometry_subscriber()
        first = walls_mod._geometry_task
        walls_mod._ensure_geometry_subscriber()
        assert walls_mod._geometry_task is first
        await asyncio.sleep(0)                   # let it start
        return first

    first = asyncio.run(two_sockets_connect())
    second = asyncio.run(two_sockets_connect())  # a new loop: the old task ended with its loop
    assert first is not second
    assert len(loops) == 2 and loops[0] is not loops[1]


def test_subscriber_forwards_only_to_tvs_this_worker_holds(monkeypatch):
    channel = f"khan:wall-geometry:test-{uuid.uuid4().hex}"   # nobody else listens here
    monkeypatch.setattr(walls_mod, "geometry_channel", lambda: channel)
    wid, _ = _free_wall([(0.0, 0.0, 1 / 3, 1.0), (1 / 3, 0.0, 1 / 3, 1.0)])
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws                 # only TV 1 is on this worker
    pushed = []
    real_push = walls_mod._push_geometry

    async def spy_push(wall_id, row, col):
        pushed.append((wall_id, row, col))
        await real_push(wall_id, row, col)

    monkeypatch.setattr(walls_mod, "_push_geometry", spy_push)
    publisher = redis.Redis.from_url(ratelimit.REDIS_URL, decode_responses=True,
                                     socket_connect_timeout=2, socket_timeout=2)

    async def scenario():
        task = asyncio.get_running_loop().create_task(walls_mod._geometry_subscriber())
        try:
            for _ in range(200):
                if dict(publisher.pubsub_numsub(channel)).get(channel, 0) >= 1:
                    break
                await asyncio.sleep(0.025)
            else:
                raise AssertionError("the subscriber never subscribed")
            publisher.publish(channel, "not json")
            publisher.publish(channel, json.dumps({"wall_id": str(wid), "row": 0, "col": 0}))
            publisher.publish(channel, json.dumps({"wall_id": wid, "row": 0, "col": 1}))
            publisher.publish(channel, json.dumps({"wall_id": wid, "row": 0, "col": 0}))
            for _ in range(200):
                if ws.frames:
                    break
                await asyncio.sleep(0.01)
            assert not task.done(), "a bad message ended the subscriber"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    finally:
        publisher.close()
    assert pushed == [(wid, 0, 0)]
    frames = ws.of_type("geometry")
    assert len(frames) == 1
    assert frames[0]["cell_geometry"] == pytest.approx(
        {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0})


def test_subscriber_keeps_retrying_while_redis_is_down(monkeypatch, caplog):
    monkeypatch.setattr(ratelimit, "REDIS_URL", "redis://127.0.0.1:1/0")   # nothing listens
    monkeypatch.setattr(walls_mod, "geometry_channel",
                        lambda: f"khan:wall-geometry:test-{uuid.uuid4().hex}")
    monkeypatch.setattr(walls_mod, "GEOMETRY_RETRY_MIN_S", 0.01)
    caplog.set_level(logging.WARNING, logger="walls")
    with pytest.raises(asyncio.TimeoutError):           # still running when cut off
        asyncio.run(asyncio.wait_for(walls_mod._geometry_subscriber(), timeout=0.5))
    failures = [r for r in caplog.records
                if "wall_geometry_subscriber_failed" in r.getMessage()]
    assert len(failures) >= 2
```

- [ ] **Step 12: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py -v`

Expected: FAIL, `9 failed, 19 passed`. All nine new tests fail with an AttributeError such as
`AttributeError: module 'walls' has no attribute '_push_geometry'`, or from monkeypatch
`<module 'walls' from '/app/walls.py'> has no attribute '_geometry_subscriber'`. The names involved are
`_push_geometry`, `publish_geometry`, `_ensure_geometry_subscriber`, `_geometry_subscriber` and
`GEOMETRY_RETRY_MIN_S`.

- [ ] **Step 13: Implement**

All edits are in `backend/walls.py`.

**13a. Module docstring** (lines 17-19): say where Redis comes in.

Replace this exact text:

```python
  matter which worker handled the REST call. ``broadcast_bye`` remains the
  fast path when the socket happens to live on the same worker.
"""
```

with:

```python
  matter which worker handled the REST call. ``broadcast_bye`` remains the
  fast path when the socket happens to live on the same worker.
* A free wall's box moves (a phone dragging one TV's crop) must arrive well
  inside the tick interval, so they also travel by Redis pub/sub:
  ``publish_geometry`` pushes to a local socket and publishes, and each
  worker holding a free-wall TV runs a ``_geometry_subscriber`` that pushes
  to the sockets it holds (grid walls never start one). Redis is
  only the fast path: the tick loop compares each cell's stored box with what
  its TV was last sent, so every move still arrives if Redis is down.
"""
```

**13b. Constants**, after the `GEOMETRY_CHANNEL_BASE` line added in step 3c.

Replace this exact text:

```python
GEOMETRY_CHANNEL_BASE = "khan:wall-geometry"
```

with:

```python
GEOMETRY_CHANNEL_BASE = "khan:wall-geometry"
# The geometry subscriber's wait for one message, and its reconnect backoff.
GEOMETRY_POLL_S = 5.0
GEOMETRY_RETRY_MIN_S = 1.0
GEOMETRY_RETRY_MAX_S = 30.0
```

**13c. Push, publish and subscribe**, after `_send_geometry_if_changed` (added in step 8b).

Replace this exact text:

```python
    if not await _send_safe(ws, _geometry_frame(wall, cell)):
        _unregister(wall_id, key, ws)


# ---- playlist loading -------------------------------------------------------
```

with:

```python
    if not await _send_safe(ws, _geometry_frame(wall, cell)):
        _unregister(wall_id, key, ws)


async def _push_geometry(wall_id: int, row: int, col: int) -> None:
    """Send cell (row, col) its stored box, if this worker holds its TV's socket."""
    key = (row, col)
    ws = _connections.get(wall_id, {}).get(key)
    if ws is None:
        return
    loaded = _load_free_cell(wall_id, row, col)
    if loaded is not None:
        await _send_geometry_if_changed(wall_id, key, ws, *loaded)


def publish_geometry(wall_id: int, row: int, col: int) -> None:
    """Tell cell (row, col)'s TV that its box changed. Sync and best-effort: never raises.

    For REST handlers. Call it only after the write that changed the box has
    committed, outside any ``with transaction():`` block: the local push and
    every other worker re-read the box, and inside the transaction they would
    read the old one. The same goes for broadcast_bye.
    The TV's socket may be on any worker: this one pushes at once if it holds
    it, and a Redis publish reaches the others' _geometry_subscriber. If both
    fail, the tick loop's fallback delivers the box within TICK_MAX_SLEEP_S.
    """
    try:
        if _connections.get(wall_id, {}).get((row, col)) is not None:
            _run_on_server_loop(_push_geometry(wall_id, row, col))
    except Exception:
        logger.exception("wall_geometry_push_failed wall_id=%s", wall_id)
    try:
        client = ratelimit.get_redis()
        if client is None:
            return                         # cooling down after a Redis failure
        client.publish(geometry_channel(),
                       json.dumps({"wall_id": wall_id, "row": row, "col": col}))
    except Exception as exc:
        ratelimit._mark_unhealthy(exc)
        logger.warning("wall_geometry_publish_failed wall_id=%s err=%s", wall_id, exc)


async def _on_geometry_message(data) -> None:
    """One published {"wall_id", "row", "col"}: push it if the TV is on this worker."""
    try:
        note = json.loads(data)
        wall_id, row, col = note["wall_id"], note["row"], note["col"]
    except (TypeError, ValueError, KeyError):
        return
    if not all(type(v) is int for v in (wall_id, row, col)):
        return
    if _connections.get(wall_id, {}).get((row, col)) is None:
        return                             # another worker holds that TV
    try:
        await _push_geometry(wall_id, row, col)
    except Exception:
        logger.exception("wall_geometry_push_failed wall_id=%s", wall_id)


def _ensure_geometry_subscriber() -> None:
    """Start this worker's geometry subscriber unless it already runs on this loop.

    wall_ws calls it for free-wall sockets only: only a free wall's boxes
    move live, so a worker holding only grid or mirrored TVs never opens the
    Redis subscription. A task belongs to the loop that made it. Production
    has one loop per worker; each TestClient websocket session has its own,
    closed (and its tasks cancelled) when the session ends. The task outlives
    the worker's last wall socket.
    """
    global _geometry_task
    loop = asyncio.get_running_loop()
    task = _geometry_task
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _geometry_task = loop.create_task(_geometry_subscriber())


async def _geometry_subscriber() -> None:
    """Forward box changes published by any worker to the TVs this worker holds.

    Its own redis.asyncio client, made on this loop: ratelimit's client is
    sync, and its 0.25 s socket timeout would end an idle subscription.
    get_message() returns None after GEOMETRY_POLL_S without a message. A
    Redis error is logged and retried with backoff (1 s doubling to 30 s)
    while the tick loops' fallback keeps TVs current. Ends only when cancelled.
    """
    import redis.asyncio as aredis
    delay = GEOMETRY_RETRY_MIN_S
    while True:
        client = pubsub = None
        try:
            client = aredis.Redis.from_url(ratelimit.REDIS_URL, decode_responses=True,
                                           socket_connect_timeout=2, health_check_interval=30)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(geometry_channel())
            delay = GEOMETRY_RETRY_MIN_S
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True,
                                               timeout=GEOMETRY_POLL_S)
                if msg is not None:
                    await _on_geometry_message(msg.get("data"))
        except Exception as exc:          # CancelledError is no Exception: it ends the task
            logger.warning("wall_geometry_subscriber_failed err=%s retry_in_s=%s", exc, delay)
        finally:
            for closable in (pubsub, client):
                if closable is not None:
                    try:
                        await closable.aclose()
                    except Exception:
                        pass
        await asyncio.sleep(delay)
        delay = min(GEOMETRY_RETRY_MAX_S, delay * 2)


# ---- playlist loading -------------------------------------------------------
```

- [ ] **Step 14: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py tests/test_ratelimit.py -v`
Expected: PASS. `test_walls_live.py`: 28 passed. `test_ratelimit.py` passes; this task only reads `ratelimit`, and its tests patch `get_redis` and `_mark_unhealthy` with monkeypatch.
`test_subscriber_forwards_only_to_tvs_this_worker_holds` uses the real test Redis (`REDIS_URL=redis://redis:6379/5` from the script) on a channel named `khan:wall-geometry:test-<uuid>`. If Redis is unreachable it fails within 15 s; it does not hang.

- [ ] **Step 15: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/walls.py backend/tests/test_walls_live.py
git commit -m "feat(walls): relay box moves across workers through Redis pub/sub

publish_geometry pushes to the TV at once when this worker holds its
socket and publishes {wall_id, row, col} on khan:wall-geometry:db<N>;
each worker's subscriber (its own redis.asyncio client, started per
event loop) pushes to the sockets it holds. Never raises; on Redis
errors the subscriber backs off 1..30 s and the tick loop still delivers.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

#### Cycle 4: the TV's socket: hello record, subscriber start, screen report

- [ ] **Step 16: Write the failing test**

Append to `backend/tests/test_walls_live.py`:

```python


# ---- the TV's socket and its screen report ----------------------------------

def _report(wid, cid, ws, msg):
    asyncio.run(walls_mod._on_screen_report(wid, _key(cid), ws, cid, msg))


def test_free_wall_tv_gets_its_box_in_hello_and_no_repeat(client):
    wid, (c0, c1) = _free_wall([(0.0, 0.0, 1 / 3, 1.0), (0.4, 0.1, 0.3, 0.9)])
    execute("UPDATE wall_cells SET highlight_until_ms = ? WHERE id = ?", (1762000030000, c1))
    _, token = _bind_tv(wid, c1)
    with client.websocket_connect(f"/walls/{wid}/ws?screen_token={token}") as ws:
        hello = _recv_until(ws, "hello")
        assert hello is not None, "no hello"
        assert hello["mode"] == "spanned"
        assert hello["canvas"] == {"w": 3840, "h": 720}
        assert hello["cell_geometry"] == pytest.approx(
            {"x": 1536.0, "y": 72.0, "w": 1152.0, "h": 648.0}, rel=1e-5)
        assert hello["tv_number"] == 2
        assert hello["highlight_until_ms"] == 1762000030000
        # The tick loop's first pass runs as soon as the handler waits for
        # input; the hello already told this TV its box, so nothing repeats it.
        assert _recv_until(ws, "geometry", timeout=0.5) is None
        task = walls_mod._geometry_task
        assert task is not None and not task.done(), "no geometry subscriber on this worker"


def test_grid_wall_socket_starts_no_geometry_subscriber(client, monkeypatch):
    """R7: only free walls get geometry, so a grid wall's TV opens no Redis subscription.

    Guards the layout check in wall_ws; passes before and after this task.
    """
    monkeypatch.setattr(walls_mod, "_geometry_task", None)   # whatever an earlier file left
    wid, cid = _grid_wall()
    _, token = _bind_tv(wid, cid)
    with client.websocket_connect(f"/walls/{wid}/ws?screen_token={token}") as ws:
        hello = _recv_until(ws, "hello")
        assert hello is not None, "no hello"
        assert "tv_number" not in hello and "highlight_until_ms" not in hello
        assert walls_mod._geometry_task is None


def test_screen_message_over_the_socket_fits_the_box(client):
    wid, (cid,) = _free_wall([(1 / 3, 0.0, 1 / 3, 1.0)])
    _, token = _bind_tv(wid, cid)
    with client.websocket_connect(f"/walls/{wid}/ws?screen_token={token}") as ws:
        assert _recv_until(ws, "hello") is not None
        ws.send_text("not json")
        ws.send_text("[1, 2]")
        ws.send_json({"type": "screen", "w": "1080", "h": 1920})
        ws.send_json({"type": "screen", "w": 1080, "h": 1920})
        geometry = _recv_until(ws, "geometry")
        assert geometry is not None, "no geometry after a portrait screen report"
        g = geometry["cell_geometry"]
        assert g["w"] / g["h"] == pytest.approx(1080 / 1920, rel=1e-4)
        assert g["x"] == pytest.approx(1280.0, rel=1e-5)
        assert g["y"] + g["h"] / 2 == pytest.approx(360.0, abs=0.01)
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (1080, 1920)


def test_portrait_tv_turns_its_default_box_portrait_once():
    wid, (cid,) = _free_wall([(1 / 3, 0.0, 1 / 3, 1.0)])     # 1280x720 px: the 16:9 default
    ws = _held(wid, cid)
    _report(wid, cid, ws, {"type": "screen", "w": 1080, "h": 1920})
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (1080, 1920)
    assert cell["place_x"] == pytest.approx(1 / 3, rel=1e-5)          # same width and centre
    assert cell["place_w"] == pytest.approx(1 / 3, rel=1e-5)
    assert cell["place_y"] + cell["place_h"] / 2 == pytest.approx(0.5, abs=1e-5)
    assert (cell["place_w"] * 3840) / (cell["place_h"] * 720) == pytest.approx(
        1080 / 1920, rel=1e-4)
    frames = ws.of_type("geometry")
    assert len(frames) == 1
    assert frames[0]["cell_geometry"] == pytest.approx(walls_mod.free_cell_geometry(
        {"canvas_width_px": 3840, "canvas_height_px": 720}, cell))

    # Reported again (a resize, a reconnect): no longer 16:9, so the box stays.
    _report(wid, cid, ws, {"type": "screen", "w": 1080, "h": 1920})
    assert [_cell(cid)[k] for k in PLACE] == [cell[k] for k in PLACE]
    assert len(ws.of_type("geometry")) == 1


def test_landscape_tv_keeps_its_default_box():
    wid, (cid,) = _free_wall([(1 / 3, 0.0, 1 / 3, 1.0)])
    ws = _held(wid, cid)
    before = [_cell(cid)[k] for k in PLACE]
    _report(wid, cid, ws, {"type": "screen", "w": 1920, "h": 1080})
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (1920, 1080)
    assert [cell[k] for k in PLACE] == before
    _report(wid, cid, ws, {"type": "screen", "w": 960, "h": 540})      # a high-DPR TV
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (960, 540)
    assert [cell[k] for k in PLACE] == before
    assert ws.frames == []


def test_screen_size_is_clamped():
    wid, (cid,) = _free_wall([(1 / 3, 0.0, 1 / 3, 1.0)])
    ws = _held(wid, cid)
    _report(wid, cid, ws, {"type": "screen", "w": 5, "h": 50000})
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (100, 10000)


@pytest.mark.parametrize("msg", [
    {"type": "screen"},
    {"type": "screen", "w": 1080},
    {"type": "screen", "w": True, "h": 1920},
    {"type": "screen", "w": "1080", "h": "1920"},
    {"type": "screen", "w": 1080.5, "h": 1920},
    {"type": "screen", "w": None, "h": None},
    {"type": "screen", "w": [1080], "h": 1920},
])
def test_bad_screen_reports_change_nothing(msg):
    wid, (cid,) = _free_wall([(1 / 3, 0.0, 1 / 3, 1.0)])
    ws = _held(wid, cid)
    before = _cell(cid)
    _report(wid, cid, ws, msg)
    assert _cell(cid) == before
    assert ws.frames == []


def test_grid_wall_screen_report_only_stores_the_size():
    wid, cid = _grid_wall()
    ws = FakeWS()
    walls_mod._connections[wid][(0, 0)] = ws
    _report(wid, cid, ws, {"type": "screen", "w": 1080, "h": 1920})
    cell = _cell(cid)
    assert (cell["screen_w_px"], cell["screen_h_px"]) == (1080, 1920)
    assert [cell[k] for k in PLACE] == [0.0, 0.0, 0.5, 1.0]
    assert ws.frames == []
```

- [ ] **Step 17: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py -v`

Expected: FAIL, `13 failed, 29 passed`, with no hang (every socket read has a deadline).

| Failing test | How it fails |
|---|---|
| `test_free_wall_tv_gets_its_box_in_hello_and_no_repeat` | Fails at `assert _recv_until(ws, "geometry", timeout=0.5) is None`: the tick's first pass repeats the hello's box, because connect did not record it |
| `test_screen_message_over_the_socket_fits_the_box` | `AttributeError: 'list' object has no attribute 'get'`, raised in `wall_ws`: the `[1, 2]` message kills the socket |
| The other 11 | `AttributeError: module 'walls' has no attribute '_on_screen_report'` |

`test_grid_wall_socket_starts_no_geometry_subscriber` passes. It guards R7: once 18c lands, it fails if a
grid-wall socket starts the subscriber.

- [ ] **Step 18: Implement**

All edits are in `backend/walls.py`.

**18a. Constants**, after the `GEOMETRY_RETRY_MAX_S` line added in step 13b.

Replace this exact text:

```python
GEOMETRY_RETRY_MAX_S = 30.0
```

with:

```python
GEOMETRY_RETRY_MAX_S = 30.0
# A TV's reported viewport ("screen" message) is clamped to this, in CSS px.
SCREEN_MIN_PX = 100
SCREEN_MAX_PX = 10000
# A TV whose shape is within this of its box's shape keeps the box as it is.
SCREEN_SHAPE_TOLERANCE = 0.01
```

**18b. The screen report**, right above `wall_ws` (line 574). Ruling R6: like the rest of walls.py, it runs its database statements synchronously on the event loop, so they stay short. It reads the wall and the cell, runs at most one UPDATE (the size and, when it changes, the fitted box together, as autocommit, with no transaction), and re-reads the cell after that write, because REAL columns round and the de-dup signature must match later reads. `_screen_fit` decides the box. Task 3's `fit_box_to_screen` already keeps a box within `MAX_BOX_SIDE` for an extreme clamped shape such as 100x10000; `validate_box` still has the last word on what is stored.

Replace this exact text:

```python
@router.websocket("/walls/{wall_id}/ws")
```

with:

```python
def _screen_px(value) -> int | None:
    """One side of a reported viewport, clamped; None unless it is an int."""
    if type(value) is not int:             # bool is an int subclass: refused too
        return None
    return max(SCREEN_MIN_PX, min(SCREEN_MAX_PX, value))


def _screen_fit(wall: dict, cell: dict, screen_w: int, screen_h: int) -> dict | None:
    """The box a free-wall TV's reported screen gives its cell, or None to keep the box.

    Only a box still at its 16:9 default takes the TV's shape (same width and
    centre, new height). A box the phone has reshaped stays as it is, and so
    does one already within SCREEN_SHAPE_TOLERANCE of the screen's shape.
    """
    if not _is_free_box(wall, cell):
        return None
    box = _cell_box(cell)
    cw, ch = _free_canvas(wall)
    if not (wall_layout.validate_box(box) and wall_layout.has_default_shape(box, cw, ch)):
        return None
    px = wall_layout.box_to_px(box, cw, ch)
    if abs((screen_w / screen_h) / (px["w"] / px["h"]) - 1) <= SCREEN_SHAPE_TOLERANCE:
        return None
    fitted = wall_layout.fit_box_to_screen(box, screen_w, screen_h, cw, ch)
    return fitted if wall_layout.validate_box(fitted) else None


async def _on_screen_report(wall_id: int, key: tuple, ws, cell_id: int, msg: dict) -> None:
    """{"type": "screen", "w", "h"}: the TV's viewport in CSS px, on open and after resizes.

    The size is stored on the cell (the phone locks the TV's box to that
    shape). A free-wall box still at its 16:9 default on a TV of another shape
    takes the TV's shape here: same width and centre, new height. The TV is
    then sent its box if that changed.

    Like the rest of walls.py this runs its statements synchronously on the
    event loop, so they stay short: two reads, at most one UPDATE (autocommit,
    no transaction) and a re-read after it.
    """
    w, h = _screen_px(msg.get("w")), _screen_px(msg.get("h"))
    if w is None or h is None:
        return
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    cell = query_one("SELECT * FROM wall_cells WHERE id = ? AND wall_id = ?", (cell_id, wall_id))
    if wall is None or cell is None:
        return
    size_changed = (cell.get("screen_w_px"), cell.get("screen_h_px")) != (w, h)
    fitted = _screen_fit(wall, cell, w, h)
    if fitted is not None:
        execute(
            "UPDATE wall_cells SET screen_w_px = ?, screen_h_px = ?, "
            "place_x = ?, place_y = ?, place_w = ?, place_h = ? WHERE id = ?",
            (w, h, fitted["x"], fitted["y"], fitted["w"], fitted["h"], cell_id),
        )
    elif size_changed:
        execute("UPDATE wall_cells SET screen_w_px = ?, screen_h_px = ? WHERE id = ?",
                (w, h, cell_id))
    if fitted is not None or size_changed:
        # Re-read: REAL columns round, and the sig must match later reads.
        cell = query_one("SELECT * FROM wall_cells WHERE id = ? AND wall_id = ?",
                         (cell_id, wall_id))
        if cell is None:
            return
    if (cell["row_index"], cell["col_index"]) != tuple(key):
        return                             # the socket's (row, col) is no longer this cell
    if _connections.get(wall_id, {}).get(key) is ws:
        await _send_geometry_if_changed(wall_id, key, ws, wall, cell)


@router.websocket("/walls/{wall_id}/ws")
```

**18c. `wall_ws`: start the subscriber for free walls and record the hello** (lines 596-597). Ruling R7: only
a free-wall socket starts the subscriber.

Replace this exact text:

```python
    await _ensure_tick_loop(wall_id)
    await _send_safe(websocket, _hello_frame(wall, cell, current_play_for(wall_id, cell)))
```

with:

```python
    await _ensure_tick_loop(wall_id)
    if wall.get("layout") == "free":
        # Only a free wall's boxes move live: a worker holding only grid or
        # mirrored TVs never opens the Redis subscription.
        _ensure_geometry_subscriber()
    hello = _hello_frame(wall, cell, current_play_for(wall_id, cell))
    if _is_free_box(wall, cell):
        # Recorded before the await, so a tick pass or a pushed box that runs
        # while the hello is in flight does not repeat it.
        _geom_sent[websocket] = _geom_sig(wall, cell)
    await _send_safe(websocket, hello)
```

**18d. `wall_ws`: dispatch `screen` messages** (lines 608-611 of the receive loop).

Replace this exact text:

```python
            if msg.get("type") == "pong":
                continue
            if msg.get("type") == "ready":
                continue
```

with:

```python
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "screen":
                try:
                    await _on_screen_report(wall_id, key, websocket, cell["id"], msg)
                except Exception:
                    # A database hiccup must not cost the TV its socket.
                    logger.exception("wall_screen_report_failed wall_id=%s", wall_id)
            # "pong", "ready" and unknown types need no answer.
```

- [ ] **Step 19: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py -v`  Expected: PASS, `42 passed`. The slowest tests are the 0.5 s no-repeat window and the 0.5 s retry test.

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_live.py tests/test_walls_phase2.py tests/test_walls_timeline.py tests/test_walls_websocket.py tests/test_walls_tick_loop.py tests/test_walls_pairing.py tests/test_walls_redeem.py tests/test_walls_crud.py tests/test_ratelimit.py -v`
Expected: PASS. Only free-wall sockets start the worker's subscriber (R7). The existing grid and mirrored socket
tests never start it; only this file's free-wall socket tests subscribe, to `khan:wall-geometry:db5` on the test
Redis. The set gives 124 passed (42 new plus 82 existing). Tasks 4-5 only add new test files, none of them in
this set, so the count is the same with them applied.

Before the full suite, make sure no other run is using this test database:
`docker ps -q | xargs -r docker inspect -f '{{.Config.Env}}' | grep -c sawwii_test_wallsetup`
Expected: `0`. If it prints anything else, wait for that run to finish.

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh`  Expected: PASS (the whole suite).

- [ ] **Step 20: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/walls.py backend/tests/test_walls_live.py
git commit -m "feat(walls): TVs report their screen size; default boxes take its shape

wall_ws records the box it sends in hello (no tick echo), starts the
worker's geometry subscriber for free-wall sockets only, and handles
screen messages {type, w, h}: integers only, clamped to 100..10000,
stored on the cell in one short UPDATE; a free-wall box still at its
16:9 default gets the TV's shape (same width and centre) and the TV is
sent it. Non-object messages are ignored instead of killing the socket.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Placement, video swap, free-wall guards and pairing limits

This task runs after Task 6, which adds `walls.publish_geometry`, `walls.HIGHLIGHT_SECONDS` and the Redis channel to walls.py. Execute the tasks in numeric order: this file holds Tasks 4, 5 and 7, and Task 6 comes between 5 and 7. Line numbers are as at HEAD ca74b28; Tasks 2, 4 and 5 have moved them since, so every edit is anchored on text.

**Files:**
- Modify: `backend/main.py:3327-3328` (the limit on `/screens/request_code`) and `backend/main.py:3349-3350` (the limit on `/screens/poll/{code}`).
- Modify: `backend/main.py:3595-3597`. Add a free-layout guard to `patch_wall`, right after `fields = payload.model_dump(...)`.
- Modify: `backend/main.py:3862-3894`. Rewrite `unpair_wall_cell` so it deletes the cell on free walls.
- Modify: `backend/main.py`. Add the placement and video endpoints at the end of Task 5's free-wall section, just above `# ---- Spanned-mode canvas playlists ----`.
- Modify: `backend/main.py:3909-4030`. Add `_refuse_free_wall_canvas_edit` after `_load_spanned_wall_or_404`, and call it in `add_canvas_item`, `patch_canvas_item` and `delete_canvas_item`.
- Modify: `backend/tests/test_walls_free.py`. Change the import block and append the new tests.
- Test (existing; must still pass): `backend/tests/test_walls_crud.py`, `test_walls_phase2.py` (the canvas-playlist endpoints on grid walls), `test_walls_redeem.py`, `test_walls_pairing.py`, `test_pairing.py`, `test_walls_websocket.py`, `test_walls_timeline.py`, `test_wall_tv_binding.py`, `test_walls_live.py`

**Interfaces:**
- Consumes:
  - Task 3: `wall_layout.validate_box(box) -> bool`, `wall_layout.canvas_size_for(...)` and `wall_layout.fit_box_to_screen(box, screen_w, screen_h, canvas_w, canvas_h) -> dict` (R8).
  - Task 4: `_lock_org(oid)`.
  - Task 5: `_free_wall_or_404`, `_free_wall_media_or_raise`, `_lock_free_wall_media`, `_free_wall_item_seconds`, `serialize_wall` (with `video`). Test helpers in `test_walls_free.py`: `_org`, `_tv_code`, `_media`, `_free_wall`, `_add_tv`, `_grid_wall`, `_cells`, `_cols`, `_items`, `_left_behind`, `_limits`.
  - Task 6: `walls.publish_geometry(wall_id: int, row: int, col: int) -> None`, which is sync and never raises; `walls.HIGHLIGHT_SECONDS` (30); `walls.now_ms()`. Existing: `walls.broadcast_bye(wall_id, row, col, reason)`.
  - main.py calls them as `import walls as walls_mod` (or the existing lazy `from walls import broadcast_bye`) inside the handler, so tests can monkeypatch them. walls.py still never imports main.
  - R5: `publish_geometry` and `broadcast_bye` run only after the write that changed the box or removed the cell has committed, outside any `with transaction():` block, because the push and the other workers re-read the database.
- Produces (contract):
  - `class WallPlacement(BaseModel)` with `x, y, w, h: float` (plain floats, no Field bounds: `wall_layout.validate_box` decides, R2) and `highlight: bool = True`. `class WallVideoUpdate(BaseModel)` with `media_id`.
  - `PUT /walls/{wall_id}/cells/{row}/{col}/placement` → `put_wall_placement` → 204, `@limiter.limit("1800/minute")`. Errors: 422 `wall.placement_invalid` (every invalid box, R2), 409 `wall.not_free`, 404 `wall.not_found` / `wall.cell_not_found`. When the cell has `screen_w_px`/`screen_h_px`, the stored box is `fit_box_to_screen(requested, ...)`: same width and centre, the TV's shape (R8). The phone's request is validated before that fit, so an invalid box is refused rather than nudged onto the video.
  - `PUT /walls/{wall_id}/video` → `put_wall_video` → 200 `serialize_wall(...)` (D8).
  - `PATCH /walls/{id}` on a free wall: changing mode, mirrored_mode, mirrored_playlist_id, bezel_enabled, bezel_h_pct, bezel_v_pct, canvas_width_px or canvas_height_px to a different value → 409 `wall.free_layout`.
  - `POST /walls/{id}/canvas-playlist/items`, `PATCH` and `DELETE .../items/{item_id}` on a free wall → 409 `wall.free_layout` (D8: one item, changed only by PUT /video). `GET /walls/{id}/canvas-playlist` stays allowed. Helper: `_refuse_free_wall_canvas_edit(wall: dict) -> None`.
  - `DELETE /walls/{id}/cells/{r}/{c}/pairing` on a free wall deletes the cell and recomputes `cols`. The wall itself stays, even with no TVs (R18).
  - D5 rate limits: `/screens/poll/{code}` 240/minute (8 TVs polling every 3 s make 160 a minute, plus the phone's code checks from the same NAT), `/screens/request_code` 30/minute.

- [ ] **Step 1: Add the imports the new tests need (`backend/tests/test_walls_free.py`)**

Old text:
```python
import main
import wall_layout
from db import execute, query_all, query_one, utc_now_iso
```
New text:
```python
import main
import psycopg
import ratelimit
import redis
import wall_layout
import walls as walls_mod
from db import execute, get_dsn, query_all, query_one, utc_now_iso
from psycopg.rows import dict_row
```

- [ ] **Step 2: Append the failing tests to the end of `backend/tests/test_walls_free.py`**

```python


# ---- PUT /walls/{id}/cells/{row}/{col}/placement ----


@pytest.fixture
def published(monkeypatch):
    """Calls to walls.publish_geometry, recorded instead of sent."""
    calls = []
    monkeypatch.setattr(walls_mod, "publish_geometry",
                        lambda wall_id, row, col: calls.append((wall_id, row, col)))
    return calls


def _place(client, headers, wall_id, box, row=0, col=0):
    return client.put(f"/walls/{wall_id}/cells/{row}/{col}/placement", json=box,
                      headers=headers)


def _box(cell):
    return {"x": cell["place_x"], "y": cell["place_y"],
            "w": cell["place_w"], "h": cell["place_h"]}


def _committed_cell(wall_id, row, col):
    """The cell as another connection sees it: only what has been committed.
    The handler's own connection would also see its open transaction (R5)."""
    with psycopg.connect(get_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM wall_cells WHERE wall_id = %s AND row_index = %s AND col_index = %s",
            (wall_id, row, col),
        ).fetchone()


def test_placement_is_saved_highlighted_and_published(client, monkeypatch, published):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    monkeypatch.setattr(walls_mod, "now_ms", lambda: 1_762_000_000_000)
    # Partly above and below the video is fine: that part shows black.
    res = _place(client, h, wall["id"], {"x": 0.25, "y": -0.1, "w": 0.4, "h": 1.2})
    assert res.status_code == 204, res.text
    assert res.content == b""
    cell = _cells(wall["id"])[0]
    assert _box(cell) == pytest.approx({"x": 0.25, "y": -0.1, "w": 0.4, "h": 1.2}, abs=1e-6)
    assert cell["highlight_until_ms"] == 1_762_000_000_000 + walls_mod.HIGHLIGHT_SECONDS * 1000
    assert published == [(wall["id"], 0, 0)]


def test_done_clears_the_highlight(client, published):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    box = {"x": 0.0, "y": 0.0, "w": 0.3, "h": 1.0}
    assert _place(client, h, wall["id"], box).status_code == 204
    assert _cells(wall["id"])[0]["highlight_until_ms"] is not None
    assert _place(client, h, wall["id"], {**box, "highlight": False}).status_code == 204
    assert _cells(wall["id"])[0]["highlight_until_ms"] is None
    assert published == [(wall["id"], 0, 0), (wall["id"], 0, 0)]


@pytest.mark.parametrize("box", [
    {"x": 1.0, "y": 0.0, "w": 0.5, "h": 1.0},      # right of the video
    {"x": 0.0, "y": -1.0, "w": 0.5, "h": 1.0},     # above it
    {"x": 0.99, "y": 0.0, "w": 1.0, "h": 1.0},     # 1% on it (< 5%)
    {"x": 0.0, "y": 0.0, "w": 0.0, "h": 1.0},      # w must be > 0
    {"x": 0.0, "y": 0.0, "w": -0.3, "h": 1.0},     # ... so not negative either
    {"x": 0.0, "y": 0.0, "w": 5.0, "h": 1.0},      # w must be <= 4
    {"x": 0.0, "y": 0.0, "w": 0.3, "h": 4.5},      # h must be <= 4
])
def test_invalid_boxes_are_422_and_change_nothing(client, published, box):
    """R2: WallPlacement has plain floats, so validate_box decides and every
    refusal carries the code the phone translates (no Pydantic list detail)."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    before = _cells(wall["id"])[0]
    res = _place(client, h, wall["id"], box)
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    after = _cells(wall["id"])[0]
    assert _box(after) == _box(before)
    assert after["highlight_until_ms"] == before["highlight_until_ms"]
    assert published == []


@pytest.mark.parametrize("body", [
    b'{"x": NaN, "y": 0, "w": 0.3, "h": 1}',
    b'{"x": 0, "y": 0, "w": Infinity, "h": 1}',
])
def test_a_non_finite_box_is_refused(client, published, body):
    # Python's json, and so FastAPI, accepts NaN and Infinity, and Pydantic
    # lets them into a float: validate_box refuses them, with the same code (R2).
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    res = client.put(f"/walls/{wall['id']}/cells/0/0/placement", content=body,
                     headers={**h, "Content-Type": "application/json"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    assert published == []


def test_placement_keeps_the_tvs_reported_shape(client, published):
    """R8: once the TV has reported its screen, a box from a phone that has not
    caught up (16:9 here, for a portrait TV) is stored in the TV's shape, with
    the same width and centre. An invalid request is still refused: fitting it
    first would nudge it back onto the video."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)                      # 5760x1080
    execute("UPDATE wall_cells SET screen_w_px = 1080, screen_h_px = 1920 WHERE wall_id = ?",
            (wall["id"],))
    res = _place(client, h, wall["id"], {"x": 0.25, "y": 0.0, "w": 1 / 3, "h": 1.0})
    assert res.status_code == 204, res.text
    stored = _box(_cells(wall["id"])[0])
    assert stored["w"] == pytest.approx(1 / 3, rel=1e-5)
    assert stored["x"] == pytest.approx(0.25, rel=1e-5)
    assert stored["y"] + stored["h"] / 2 == pytest.approx(0.5, abs=1e-5)
    assert (stored["w"] * 5760) / (stored["h"] * 1080) == pytest.approx(1080 / 1920, rel=1e-4)
    assert published == [(wall["id"], 0, 0)]
    # Wholly right of the video. Fitted first, it would be nudged back to 5% on it.
    res = _place(client, h, wall["id"], {"x": 1.0, "y": 0.0, "w": 0.5, "h": 1.0})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "wall.placement_invalid"
    assert _box(_cells(wall["id"])[0]) == stored
    assert published == [(wall["id"], 0, 0)]


def test_geometry_is_published_once_the_box_is_committed(client, monkeypatch):
    """R5: the local push and the other workers re-read the box from the
    database, so publish_geometry runs only after the write has committed."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    seen = []
    monkeypatch.setattr(walls_mod, "publish_geometry",
                        lambda wall_id, row, col: seen.append(_committed_cell(wall_id, row, col)))
    box = {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0}
    assert _place(client, h, wall["id"], box).status_code == 204
    assert len(seen) == 1
    assert _box(seen[0]) == pytest.approx(box, abs=1e-6)
    assert seen[0]["highlight_until_ms"] is not None


def test_placement_is_refused_on_grid_walls_other_orgs_and_missing_cells(client, published):
    oid, h = _org()
    _other, other_h = _org()
    wall = _free_wall(client, h, oid)
    grid = _grid_wall(client, h)
    before = _cells(wall["id"])[0]
    box = {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0}
    for url, headers, status, code in (
        (f"/walls/{grid['id']}/cells/0/0/placement", h, 409, "wall.not_free"),
        (f"/walls/{wall['id']}/cells/0/0/placement", other_h, 404, "wall.not_found"),
        (f"/walls/{wall['id']}/cells/0/5/placement", h, 404, "wall.cell_not_found"),
    ):
        res = client.put(url, json=box, headers=headers)
        assert (res.status_code, res.json()["detail"]["code"]) == (status, code), url
    # Nothing changed: not the free wall's box or highlight, not the grid's cells.
    after = _cells(wall["id"])[0]
    assert _box(after) == _box(before)
    assert after["highlight_until_ms"] == before["highlight_until_ms"]
    assert all(c["place_x"] is None and c["highlight_until_ms"] is None
               for c in _cells(grid["id"]))
    assert published == []


class _DeadRedis:
    """What ratelimit.get_redis() hands back while Redis is down: from_url
    never connects, so the first command is the one that fails."""

    def __init__(self):
        self.publishes = 0

    def publish(self, channel, message):
        self.publishes += 1
        raise redis.exceptions.ConnectionError("Error 111 connecting to redis:6379.")


@pytest.fixture
def fresh_redis_state():
    # publish_geometry may mark Redis unhealthy for 10 s; later tests must not inherit that.
    ratelimit.reset_for_tests()
    yield
    ratelimit.reset_for_tests()


def test_placement_still_204_when_redis_is_down(client, monkeypatch, fresh_redis_state):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    dead = _DeadRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: dead)
    res = _place(client, h, wall["id"], {"x": 0.5, "y": 0.0, "w": 0.3, "h": 1.0})
    assert res.status_code == 204, res.text
    assert dead.publishes == 1          # the Redis path was tried, and its failure swallowed
    assert _cells(wall["id"])[0]["place_x"] == pytest.approx(0.5, abs=1e-6)
    # In the cooldown after a failure, get_redis() returns None: also fine.
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    res = _place(client, h, wall["id"], {"x": 0.6, "y": 0.0, "w": 0.3, "h": 1.0})
    assert res.status_code == 204, res.text
    assert _cells(wall["id"])[0]["place_x"] == pytest.approx(0.6, abs=1e-6)


def test_placement_and_pairing_rate_limits():
    # The phone keeps one PUT in flight while dragging: several a second.
    assert _limits("put_wall_placement") == ["1800 per 1 minute"]
    # Up to 8 TVs behind one venue NAT, each polling every 3 s while it waits
    # (8 x 20 = 160 a minute), plus the phone's code checks from the same NAT.
    assert _limits("poll_pair_code") == ["240 per 1 minute"]
    assert _limits("request_pair_code") == ["30 per 1 minute"]


# ---- PUT /walls/{id}/video and the canvas playlist ----


def test_video_swap_replaces_the_item_and_follows_the_new_size(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid, width=5760, height=1080, duration=20.0)
    [old_item] = _items(client, h, wall["id"])
    new_mid = _media(oid, width=1920, height=1080, duration=12.5)
    res = client.put(f"/walls/{wall['id']}/video", json={"media_id": new_mid}, headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["canvas_width_px"], body["canvas_height_px"]) == (1920, 1080)
    assert body["video"]["media_id"] == new_mid
    [item] = _items(client, h, wall["id"])
    # A new row, not an update: its new id makes the TVs switch at once.
    assert item["id"] != old_item["id"]
    assert (item["media_id"], item["duration_seconds"], item["fit_mode"]) == (new_mid, 13, "fit")
    # Boxes are fractions of the canvas, so each TV keeps its part of the picture.
    assert _box(body["cells"][0]) == _box(wall["cells"][0])


def test_video_swap_refuses_foreign_media_pdfs_other_orgs_and_grid_walls(client):
    oid, h = _org()
    other, other_h = _org()
    wall = _free_wall(client, h, oid)
    url = f"/walls/{wall['id']}/video"
    pdf = _media(oid, width=None, height=None, duration=None, mime="application/pdf")
    for media_id, headers, status, code in (
        (_media(other), h, 404, "media.not_found"),
        (pdf, h, 400, "wall.media_type_blocked"),
        (_media(other), other_h, 404, "wall.not_found"),   # the other org, on this org's wall
    ):
        res = client.put(url, json={"media_id": media_id}, headers=headers)
        assert (res.status_code, res.json()["detail"]["code"]) == (status, code)
    grid = _grid_wall(client, h)
    res = client.put(f"/walls/{grid['id']}/video", json={"media_id": _media(oid)}, headers=h)
    assert (res.status_code, res.json()["detail"]["code"]) == (409, "wall.not_free")
    # Nothing changed: the same one item and the same canvas.
    assert [i["media_id"] for i in _items(client, h, wall["id"])] == [wall["video"]["media_id"]]
    after = query_one("SELECT canvas_width_px, canvas_height_px FROM walls WHERE id = ?",
                      (wall["id"],))
    assert (after["canvas_width_px"], after["canvas_height_px"]) == (5760, 1080)


def test_canvas_item_edits_are_refused_on_free_walls(client):
    """D8: a free wall plays exactly one item, which only PUT /walls/{id}/video
    changes. Reading the canvas playlist stays allowed."""
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    [item] = _items(client, h, wall["id"])
    base = f"/walls/{wall['id']}/canvas-playlist/items"
    for res in (
        client.post(base, json={"media_id": _media(oid), "position": 1}, headers=h),
        client.patch(f"{base}/{item['id']}", json={"fit_mode": "fill"}, headers=h),
        client.delete(f"{base}/{item['id']}", headers=h),
    ):
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "wall.free_layout"
    assert _items(client, h, wall["id"]) == [item]


# ---- PATCH /walls/{id} and removing a TV ----


def test_patch_renames_a_free_wall_but_refuses_layout_changes(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    url = f"/walls/{wall['id']}"
    # A form that sends the unchanged values along still renames.
    res = client.patch(url, json={"name": "Foyer", "mode": "spanned", "canvas_width_px": 5760,
                                  "canvas_height_px": 1080, "bezel_h_pct": 0}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Foyer"
    for change in ({"mode": "mirrored", "mirrored_mode": "synced_rotation"},
                   {"canvas_width_px": 3840, "canvas_height_px": 2160},
                   {"bezel_h_pct": 2.0},
                   {"bezel_enabled": True}):
        res = client.patch(url, json=change, headers=h)
        assert res.status_code == 409, (change, res.text)
        assert res.json()["detail"]["code"] == "wall.free_layout"
    after = query_one("SELECT * FROM walls WHERE id = ?", (wall["id"],))
    assert (after["name"], after["mode"], after["layout"], after["canvas_width_px"],
            after["bezel_h_pct"], after["spanned_playlist_id"]) == (
        "Foyer", "spanned", "free", 5760, 0, wall["spanned_playlist_id"])
    assert query_one("SELECT id FROM playlists WHERE id = ?", (wall["spanned_playlist_id"],))


def test_removing_tvs_deletes_cells_and_screens_and_frees_indexes(client):
    oid, h = _org()
    wid = _free_wall(client, h, oid, name="Lobby")["id"]
    second = _add_tv(client, h, wid).json()
    assert _add_tv(client, h, wid).status_code == 201
    token = query_one("SELECT token FROM screens WHERE id = ?", (second["screen_id"],))["token"]

    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 204
    assert [c["col_index"] for c in _cells(wid)] == [0, 2]
    assert _cols(wid) == 3                      # highest index left + 1
    assert query_one("SELECT id FROM screens WHERE id = ?", (second["screen_id"],)) is None
    assert client.get(f"/screens/{token}/content").status_code == 404

    code = _tv_code(client)
    res = _add_tv(client, h, wid, code)
    assert res.status_code == 201, res.text
    assert res.json()["col_index"] == 1         # the freed index, and so "TV 2" again
    assert client.get(f"/screens/poll/{code}").json()["screen_name"] == "Lobby · TV 2"

    for col in (2, 1, 0):
        assert client.delete(f"/walls/{wid}/cells/0/{col}/pairing", headers=h).status_code == 204
    assert _cells(wid) == []
    assert _cols(wid) == 1                      # walls.cols is CHECKed 1..8
    assert _left_behind(oid)["screens"] == 0
    # R18: the wall stays, empty, for the next TV a phone adds to it.
    assert query_one("SELECT id FROM walls WHERE id = ?", (wid,)) is not None


def test_bye_goes_out_once_the_cell_is_gone(client, monkeypatch):
    """R5: broadcast_bye runs after the removal has committed, outside its
    transaction, as publish_geometry does after a move."""
    oid, h = _org()
    wid = _free_wall(client, h, oid)["id"]
    assert _add_tv(client, h, wid).status_code == 201
    seen = []
    monkeypatch.setattr(walls_mod, "broadcast_bye",
                        lambda wall_id, row, col, reason: seen.append(
                            (reason, _committed_cell(wall_id, row, col))))
    assert client.delete(f"/walls/{wid}/cells/0/1/pairing", headers=h).status_code == 204
    assert seen == [("cell_unpaired", None)]


# ---- Roles and subscription on the four new endpoints (spec Section 2) ----


def _session(oid, role):
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"{role}_{secrets.token_hex(3)}@x.com", hash_password("pw"), 0, role, utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)", (uid, tok, utc_now_iso()))
    return {"Authorization": f"Bearer {tok}"}


def test_new_endpoints_need_an_editor_and_a_live_subscription(client):
    oid, h = _org()
    wall = _free_wall(client, h, oid)
    mid = _media(oid)
    calls = [
        ("post", "/walls/free", {"name": "X", "media_id": mid, "code": _tv_code(client)}),
        ("post", f"/walls/{wall['id']}/tvs", {"code": _tv_code(client)}),
        ("put", f"/walls/{wall['id']}/cells/0/0/placement", {"x": 0.0, "y": 0.0, "w": 0.3, "h": 1.0}),
        ("put", f"/walls/{wall['id']}/video", {"media_id": mid}),
    ]
    viewer = _session(oid, "viewer")
    for method, path, body in calls:
        res = getattr(client, method)(path, json=body, headers=viewer)
        assert res.status_code == 403, (path, res.text)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    execute("UPDATE organizations SET subscription_status = 'trialing', trial_ends_at = ? WHERE id = ?",
            (past, oid))
    for method, path, body in calls:
        res = getattr(client, method)(path, json=body, headers=h)
        assert res.status_code == 402, (path, res.text)
        assert res.json()["detail"]["code"] == "subscription.trial_expired"
    assert len(_cells(wall["id"])) == 1
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free.py -v`
Expected: FAIL, 23 failed and 29 passed (Task 5's tests).
- `test_new_endpoints_need_an_editor_and_a_live_subscription` gets 404 instead of 403 for the placement PUT. The first two calls, Task 5's endpoints, already give 403.
- The placement and video tests get 404 `{"detail":"Not Found"}` because neither route exists yet. Most fail on the status assert; the two refusal tables (`test_placement_is_refused_on_grid_walls_other_orgs_and_missing_cells`, `test_video_swap_refuses_foreign_media_pdfs_other_orgs_and_grid_walls`) hit `TypeError: string indices must be integers, not 'str'` on the detail.
- The canvas-item test gets 201 for the POST instead of 409.
- The PATCH test gets 200 instead of 409.
- The removal test still finds cells `[0, 1, 2]`, and the bye test sees the cell row still there when `broadcast_bye` runs.
- The limits test reads `[]` for `put_wall_placement`.

- [ ] **Step 4: D5, raise the pairing limits for a venue of TVs (main.py:3327-3328 and 3349-3350)**

Old text:
```python
@app.post("/screens/request_code")
@limiter.limit("10/minute")
```
New text:
```python
# Up to 8 TVs of one wall can sit behind one venue NAT (a single client IP),
# each asking for a fresh code when its last one expires.
@app.post("/screens/request_code")
@limiter.limit("30/minute")
```
Old text:
```python
@app.get("/screens/poll/{code}")
@limiter.limit("30/minute")
```
New text:
```python
# The same 8 TVs each poll every 3 s while waiting to be scanned: 8 x 20 = 160
# a minute, plus the phone's code checks (GET /screens/poll) from the same NAT.
@app.get("/screens/poll/{code}")
@limiter.limit("240/minute")
```

- [ ] **Step 5: Guard `patch_wall` on free walls (main.py:3595-3597)**

Old text (unique):
```python
    fields = payload.model_dump(exclude_unset=True)
    require_org_rows("playlists", [fields.get("mirrored_playlist_id")], org_id(user))
```
New text:
```python
    fields = payload.model_dump(exclude_unset=True)
    # A free wall's canvas is its video and its boxes are placed from the
    # phone: mode, canvas and bezel settings mean nothing there, and a mode
    # change would delete its canvas playlist. Values are compared, so a form
    # that re-sends the unchanged ones can still rename the wall.
    if wall.get("layout") == "free":
        locked = ("mode", "mirrored_mode", "mirrored_playlist_id", "bezel_enabled",
                  "bezel_h_pct", "bezel_v_pct", "canvas_width_px", "canvas_height_px")
        if any(k in fields and fields[k] != wall.get(k) for k in locked):
            raise http_error(409, "wall.free_layout",
                             "A video wall set up from a phone keeps its layout; "
                             "only its name can change here.")
    require_org_rows("playlists", [fields.get("mirrored_playlist_id")], org_id(user))
```

- [ ] **Step 6: Replace `unpair_wall_cell` with this whole function**

Use the Edit tool with this exact old text. It runs from the decorator through the first `    return None` after the `wall_bye_failed` log line, just above Task 5's `# ---- Free walls: video walls set up from a phone ----`. Tasks 2, 4 and 5 did not change it; at HEAD it was main.py:3862-3894.

Old text:
```python
@app.delete("/walls/{wall_id}/cells/{row}/{col}/pairing", status_code=204)
def unpair_wall_cell(wall_id: int, row: int, col: int,
                     user: dict = Depends(require_roles("admin", "editor")),
                     _sub: dict = Depends(require_active_subscription)) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    if cell["screen_id"]:
        # The screen was created by redeem for this cell alone, and its TV is
        # about to be told "bye" (it forgets the token and shows a pairing
        # code). Kept, the row would have no device yet still count toward the
        # plan's screen limit, so each unpair/re-pair leaked a screen.
        execute(
            "DELETE FROM screens WHERE id = ? AND organization_id = ? AND wall_cell_id = ?",
            (cell["screen_id"], wall["organization_id"], cell["id"]),
        )
        execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (cell["screen_id"],))
    execute("UPDATE wall_cells SET screen_id = NULL WHERE id = ?", (cell["id"],))
    # Reaches the TV directly when its socket is on this worker; otherwise the
    # wall's tick loop on the worker holding it notices within seconds.
    try:
        from walls import broadcast_bye  # type: ignore
        broadcast_bye(wall_id, row, col, "cell_unpaired")
    except Exception:
        logger.exception("wall_bye_failed wall_id=%s cell=%s,%s", wall_id, row, col)
    return None
```
New text:
```python
@app.delete("/walls/{wall_id}/cells/{row}/{col}/pairing", status_code=204)
def unpair_wall_cell(wall_id: int, row: int, col: int,
                     user: dict = Depends(require_roles("admin", "editor")),
                     _sub: dict = Depends(require_active_subscription)) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    if wall.get("layout") == "free":
        # A free wall's cell exists only for its TV (POST /walls/{id}/tvs made
        # both), so removing the TV removes the cell. Its index, and with it the
        # "TV n" label, is free for the next TV, and cols follows the highest
        # index left (at least 1: walls.cols is CHECKed 1..8). The wall row is
        # locked, as /tvs locks it, so a TV joining meanwhile sees a
        # consistent set of cells. The wall itself stays, even with no TV
        # left (R18).
        with transaction():
            query_one("SELECT id FROM walls WHERE id = ? FOR UPDATE", (wall_id,))
            cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell["id"],))
            if cell:
                if cell["screen_id"]:
                    execute(
                        "DELETE FROM screens WHERE id = ? AND organization_id = ? "
                        "AND wall_cell_id = ?",
                        (cell["screen_id"], wall["organization_id"], cell["id"]),
                    )
                execute("DELETE FROM wall_cells WHERE id = ?", (cell["id"],))
                execute(
                    """UPDATE walls
                          SET cols = GREATEST(1, COALESCE(
                                  (SELECT MAX(col_index) + 1 FROM wall_cells
                                    WHERE wall_id = ?), 1)),
                              updated_at = ?
                        WHERE id = ?""",
                    (wall_id, utc_now_iso(), wall_id),
                )
    else:
        if cell["screen_id"]:
            # The screen was created by redeem for this cell alone, and its TV is
            # about to be told "bye" (it forgets the token and shows a pairing
            # code). Kept, the row would have no device yet still count toward the
            # plan's screen limit, so each unpair/re-pair leaked a screen.
            execute(
                "DELETE FROM screens WHERE id = ? AND organization_id = ? AND wall_cell_id = ?",
                (cell["screen_id"], wall["organization_id"], cell["id"]),
            )
            execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (cell["screen_id"],))
        execute("UPDATE wall_cells SET screen_id = NULL WHERE id = ?", (cell["id"],))
    # Reaches the TV directly when its socket is on this worker; otherwise the
    # wall's tick loop on the worker holding it notices within seconds. Only
    # after the removal has committed (R5): the tick loop re-reads the cells.
    try:
        from walls import broadcast_bye  # type: ignore
        broadcast_bye(wall_id, row, col, "cell_unpaired")
    except Exception:
        logger.exception("wall_bye_failed wall_id=%s cell=%s,%s", wall_id, row, col)
    return None
```

- [ ] **Step 7: Add the placement and video endpoints just above `# ---- Spanned-mode canvas playlists ----` (main.py)**

The new code lands just below Task 5's `_refresh_free_walls_for_media`.

Old text (unique):
```python
# ---- Spanned-mode canvas playlists ----
```
New text:
```python
class WallPlacement(BaseModel):
    # Plain floats (ruling R2): wall_layout.validate_box decides -- finite,
    # 0 < w <= 4, 0 < h <= 4 and at least 5% on the video -- so every refusal
    # is 422 wall.placement_invalid, the code the phone translates.
    x: float
    y: float
    w: float
    h: float
    highlight: bool = True


class WallVideoUpdate(BaseModel):
    media_id: int


@app.put("/walls/{wall_id}/cells/{row}/{col}/placement", status_code=204)
@limiter.limit("1800/minute")
def put_wall_placement(request: Request, wall_id: int, row: int, col: int,
                       payload: WallPlacement,
                       user: dict = Depends(require_roles("admin", "editor")),
                       _sub: dict = Depends(require_active_subscription)) -> None:
    """Move or resize one TV's box while the phone drags it.

    The phone keeps one request in flight and sends the newest box when it
    returns, so a drag runs this several times a second: two short reads and
    one UPDATE, with no transaction and no audit. Once the TV has reported its
    screen size, the stored box is the requested one in the TV's shape (same
    width and centre, R8), so a phone that has not seen the report yet cannot
    stretch the picture. The TV hears of it through publish_geometry (Redis,
    to whichever worker holds its socket), or from that worker's tick loop
    within 5 s when Redis is down. ``highlight`` outlines the TV and shows its
    number for HIGHLIGHT_SECONDS after the last move; the phone sends false on
    Done, which clears it at once.
    """
    import walls as walls_mod

    wall = _free_wall_or_404(wall_id, org_id(user))
    cell = query_one(
        "SELECT id, screen_w_px, screen_h_px FROM wall_cells "
        "WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    box = {"x": payload.x, "y": payload.y, "w": payload.w, "h": payload.h}
    # Validate what the phone sent first: fit_box_to_screen nudges a box back
    # onto the video, which would turn an invalid request into a valid one.
    if not wall_layout.validate_box(box):
        raise http_error(422, "wall.placement_invalid",
                         "At least 5% of the TV's box must be on the video.")
    if cell["screen_w_px"] and cell["screen_h_px"]:
        canvas_w, canvas_h = wall_layout.canvas_size_for(wall["canvas_width_px"],
                                                         wall["canvas_height_px"])
        box = wall_layout.fit_box_to_screen(box, cell["screen_w_px"], cell["screen_h_px"],
                                            canvas_w, canvas_h)
        if not wall_layout.validate_box(box):
            raise http_error(422, "wall.placement_invalid",
                             "At least 5% of the TV's box must be on the video.")
    highlight_until_ms = (
        (walls_mod.now_ms() + walls_mod.HIGHLIGHT_SECONDS * 1000) if payload.highlight else None
    )
    updated = execute(
        "UPDATE wall_cells SET place_x = ?, place_y = ?, place_w = ?, place_h = ?, "
        "highlight_until_ms = ? WHERE id = ?",
        (box["x"], box["y"], box["w"], box["h"], highlight_until_ms, cell["id"]),
    )
    if not updated:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    # The UPDATE ran in autocommit, so it has committed: the local push and
    # the other workers re-read the box from the database (R5).
    walls_mod.publish_geometry(wall_id, row, col)
    return None


@app.put("/walls/{wall_id}/video")
def put_wall_video(request: Request, wall_id: int, payload: WallVideoUpdate,
                   user: dict = Depends(require_roles("admin", "editor")),
                   _sub: dict = Depends(require_active_subscription)) -> dict:
    """Swap a free wall's video.

    The canvas takes the new media's size, and the boxes keep their fractions,
    so each TV shows the same part of the new picture. The tick loop's config
    check re-sends hello when the size changed. The item is deleted and
    re-inserted rather than updated: its new id makes the TVs switch at their
    next tick even when the new video has the old one's size and length (D8).
    """
    oid = org_id(user)
    _free_wall_or_404(wall_id, oid)
    _free_wall_media_or_raise(payload.media_id, oid)    # refuse early, before any lock
    stamp = utc_now_iso()
    with transaction():
        # Same lock order as /tvs (organization, then wall). Recreating a
        # missing canvas playlist below INSERTs a row that references the
        # organization.
        _lock_org(oid)
        wall = query_one(
            "SELECT * FROM walls WHERE id = ? AND organization_id = ? FOR UPDATE",
            (wall_id, oid),
        )
        if not wall:
            raise http_error(404, "wall.not_found", "Wall not found")
        # Size and length from a FOR SHARE read: a transcode finishing now
        # waits for this commit, and its refresh (D3) then finds this wall.
        media = _lock_free_wall_media(payload.media_id, oid)
        canvas_w, canvas_h = wall_layout.canvas_size_for(media.get("width_px"),
                                                         media.get("height_px"))
        playlist_id = wall.get("spanned_playlist_id")
        if not playlist_id:
            # Its canvas playlist was deleted (walls.spanned_playlist_id is
            # ON DELETE SET NULL): give the wall a new one.
            playlist_id = execute(
                "INSERT INTO playlists (organization_id, name, kind, created_at) "
                "VALUES (?, ?, 'wall_canvas', ?)",
                (oid, f"Canvas: {wall['name']}", stamp),
            )
        execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        execute(
            """INSERT INTO playlist_items
                   (playlist_id, media_id, position, duration_seconds,
                    duration_override_seconds, fit_mode, created_at)
               VALUES (?, ?, 0, ?, NULL, 'fit', ?)""",
            (playlist_id, media["id"], _free_wall_item_seconds(media), stamp),
        )
        execute(
            "UPDATE walls SET canvas_width_px = ?, canvas_height_px = ?, "
            "spanned_playlist_id = ?, updated_at = ? WHERE id = ?",
            (canvas_w, canvas_h, playlist_id, stamp, wall_id),
        )
    audit(request, action="wall.video", actor=user,
          target_type="wall", target_id=wall_id,
          details={"media_id": media["id"]})
    return serialize_wall(query_one("SELECT * FROM walls WHERE id = ?", (wall_id,)))


# ---- Spanned-mode canvas playlists ----
```

- [ ] **Step 8: Refuse canvas-item edits on free walls (D8; main.py:3909-4030 at HEAD)**

A free wall's canvas playlist holds exactly its one video. The old dashboard's Walls editor and the MCP tool `khanshoof_add_canvas_playlist_item` still reach these endpoints, and a second item, a deleted item or a fill/stretch fit would break the TVs' boxes. Four exact replacements. Each old text occurs once.

Old text:
```python
    if wall["mode"] != "spanned" or not wall.get("spanned_playlist_id"):
        raise http_error(404, "wall.not_spanned", "Wall is not spanned.")
    return wall
```
New text:
```python
    if wall["mode"] != "spanned" or not wall.get("spanned_playlist_id"):
        raise http_error(404, "wall.not_spanned", "Wall is not spanned.")
    return wall


def _refuse_free_wall_canvas_edit(wall: dict) -> None:
    """A free wall's canvas playlist is its one video (D8): only
    PUT /walls/{id}/video changes it. Reading it stays allowed."""
    if wall.get("layout") == "free":
        raise http_error(409, "wall.free_layout",
                         "A video wall set up from a phone plays one video; "
                         "change it with PUT /walls/{id}/video.")
```
Old text (in `add_canvas_item`):
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    media = query_one(
```
New text:
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    _refuse_free_wall_canvas_edit(wall)
    media = query_one(
```
Old text (in `patch_canvas_item`):
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    item = query_one(
```
New text:
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    _refuse_free_wall_canvas_edit(wall)
    item = query_one(
```
Old text (in `delete_canvas_item`):
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    execute(
        "DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?",
```
New text:
```python
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    _refuse_free_wall_canvas_edit(wall)
    execute(
        "DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?",
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh tests/test_walls_free.py tests/test_wall_tv_binding.py tests/test_walls_crud.py tests/test_walls_phase2.py tests/test_walls_redeem.py tests/test_walls_pairing.py tests/test_pairing.py tests/test_walls_websocket.py tests/test_walls_timeline.py tests/test_walls_live.py -v`
Expected: PASS, with 52 tests in test_walls_free.py (29 from Task 5 and 23 new). Every test in the other nine files still passes:
- Grid-wall PATCH, unpair and canvas-item edits take the unchanged branch.
- test_pairing.py asserts only `pair_screen`'s limit, which this task does not touch.
- Only the Redis-down test calls the real `publish_geometry`, and it is given a fake client.

- [ ] **Step 10: Commit**
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add backend/main.py backend/tests/test_walls_free.py
git commit -m "feat(walls): live box placement, video swap and free-wall guards

PUT /walls/{id}/cells/{r}/{c}/placement stores a TV's box (validated against
the 5% overlap rule, every refusal 422 wall.placement_invalid), keeps it in
the TV's reported shape, sets or clears its highlight and, once committed,
publishes the change to whichever worker holds the TV's socket; a Redis
failure still answers 204. PUT /walls/{id}/video swaps the one canvas item
and resizes the canvas. PATCH and the canvas-item endpoints refuse layout
changes on free walls, and removing a free-wall TV deletes its cell so the
index is reused. Pairing poll and request_code limits now fit up to 8 TVs
behind one venue NAT. A test holds all four new endpoints to an editor or
admin with a live subscription.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Player: scale slices, live geometry, highlight, screen report, tighter clock

All work happens in the PR 1 worktree `/home/ahmed/signage/.claude/worktrees/wall-setup` (branch
`feature/phone-wall-setup`). Paths in **Files** are relative to it, except the test harness, which is
**not committed**. The harness lives in `/home/ahmed/signage-harness`, outside every repo. Its first copy
is in the planning session's scratchpad under `/tmp`, which an LXC restart wipes, and Task 10 re-runs
these checks later, so Task 1 Step 0 copies it to the persistent place before any other work, and
Step 0 below checks it again. Below, `$H` and `$C` stand for
these two absolute paths (each command sets them itself):

- `H=/home/ahmed/signage-harness/mockharness`
- `C=/home/ahmed/signage-harness/chrome66`

There is no JS test setup in the repo. The player is tested with the mock harness in `$H`. Its
`mock.py` is a FastAPI mock API on localhost:3150 plus a static server on :3151 that serves the raw
`player/`. `POST /__mock/state` sets the `hello` frame and `POST /__mock/wall_send` pushes any frame
to every open wall socket. The Playwright tests run in `signage_renderer:latest` (Playwright 1.62,
Chromium 151). Chrome 66 is the Chromium 66.0.3359.0 snapshot in `$C/chromium/r540276`, driven over
raw CDP.

Chrome 66 rules (from the contract) that apply to every line below:

- **JS:** no `?.`, `??`, `1_000`, `{ ...spread }`, `flatMap`, `.flat()`, `globalThis`,
  `Object.fromEntries`, `replaceAll`, `.at()` or `Promise.allSettled`, and no optional catch binding.
- **CSS:** no `inset`, flex `gap`, inline `min()`/`max()`, `:is()`/`:where()`, `aspect-ratio`, or
  standalone `scale:`/`translate:`. Use only the `transform` property.
- **Files:** no new shell files.

Ports used: 3150/3151 for the mock and 9335 for Chrome 66's CDP. Every command that binds them checks
first that they are free. Containers (exact names, all `--rm`): `e2e-wall-geo-py-<pid>` (Playwright
test), `e2e-wall-geo-ffmpeg`, `e2e-wall-geo-mock` and `e2e-wall-geo-cr66`. A command that starts a
fixed-name container first refuses to go on when that name already exists, because another run owns
it. Only a container this run started is ever stopped or removed, and only by its name (R23).

**Files:**
- Modify: `player/player.js:13-15` (element lookups), `player/player.js:47` (wall constants),
  `player/player.js:1000-1009` (wall state, clock), `player/player.js:1026-1034` (`leaveWallMode`),
  `player/player.js:1074-1080` (socket `open` handler), `player/player.js:1103-1119` (`onWallFrame` head),
  `player/player.js:1139` (new functions inserted before `clearWallPicture`),
  `player/player.js:1181-1199` (`renderWallPlay` spanned mount)
- Modify: `player/index.html:21` (add `#wall-highlight` after `#zones`)
- Modify: `player/styles.css:20-21` (`:lang(ar)` font list), `player/styles.css:145-161` (the four `--u`
  blocks), `player/styles.css:460-475` (`.wall-canvas`, new `.wall-highlight`)
- Modify: `player/i18n/en.json:28`, `player/i18n/ar.json:28` (`wall.tv_label`)
- Test (harness in `/home/ahmed/signage-harness`, not committed): copy the harness there from the
  scratchpad (Task 1 Step 0; Step 0 checks it again); modify `$H/mock.py:257-261` (record what the player sends); create
  `$H/run-wallsetup.sh`, `$H/t_wall_geometry.py`, `$H/cr66_wall_geometry.js`; generate
  `$H/media/bands.webm` (and the test writes `$H/media/bands.png`)

**Interfaces:**
- Consumes (from Task 6, see the contract):
  - **`hello` for a free wall:** `mode: "spanned"`, `canvas {w,h}`, `cell_geometry {x,y,w,h}` in
    canvas px, `highlight_until_ms` (int or null), `tv_number` (int, col_index + 1) and
    `server_now_ms`. A grid wall's `hello` is unchanged and has no `highlight_until_ms` or `tv_number`.
  - **The `geometry` frame:** `{"type": "geometry", "canvas": {"w","h"}, "cell_geometry":
    {"x","y","w","h"}, "highlight_until_ms": int|None, "tv_number": int, "server_now_ms": int}`.
  - **The server's handling of `screen`:** `_on_screen_report` accepts the TV's `{"type": "screen",
    "w": int, "h": int}`. Today's backend ignores unknown client messages, so the player is safe to
    deploy before or after the backend.
- Produces (Tasks 9 and 16 E2E read these by name through `page.evaluate`):
  - **Functions:** `noteServerTime(serverNowMs)`, `effectiveNowMs()` (same name and signature,
    now `performance.now() + wallClockOffsetMs`), `applyWallTransform()`, `onWallGeometry(frame)`,
    `showWallHighlight(untilMs, n)`, `hideWallHighlight()` and `reportWallScreen()`.
  - **Globals:** `wallClockSamples` (array of `{at, offset}`) and `wallClockOffsetMs`.
  - **DOM:** `#wall-highlight.wall-highlight` (class `hidden` when off) containing
    `#wall-highlight-label`. The outline goes away on the player's own timer, set from
    `highlight_until_ms - effectiveNowMs()` (the server's clock), because frames keep carrying the stored
    `highlight_until_ms` after it has passed and no frame comes when it does (R9). A null or past value
    hides it at once.
  - **The crop:** `.wall-canvas` gets an inline `transform: scale(sx,sy) translate(-x px,-y px)`, so
    its computed transform is `matrix(sx, 0, 0, sy, -x*sx, -y*sy)`.
  - **TV → server:** exactly `{"type":"screen","w":<int>,"h":<int>}` when the socket opens and 1 s after
    the last window resize. `w` and `h` are JSON integers (`Math.round` of `clientWidth`/`clientHeight`),
    because Task 6 ignores a float, a string or a bool. The tests check the raw text for it.
  - **i18n:** key `wall.tv_label` = "TV {n}" / "شاشة {n}".

- [ ] **Step 0: Copy the harness to a place that survives a restart**

The mock harness and the Chromium 66 tools exist only in the planning session's scratchpad, and nothing in
this plan can rebuild them (the Chromium 66.0.3359.0 snapshot r540276, `acorn`, `ws`, `mock.py` and
`common.py` were fetched or written by earlier sessions). Copy what Task 8 and Task 10 use to
`/home/ahmed/signage-harness`, outside every repo (Task 1 Step 0 normally did this already; run the block again, then it only checks). Each item is copied only when it is not there yet, so
re-running this step is safe; if an item is missing in both places, stop and ask the user.

```bash
SRC=/tmp/claude-1000/-home-ahmed-signage/39cbf124-cc26-4c98-9af8-19a9e47924c9/scratchpad
P=/home/ahmed/signage-harness
copy_once() {   # $1 = path below the harness root, copied from $SRC only when missing in $P
  if [ ! -e "$P/$1" ]; then
    test -e "$SRC/$1" || { echo "MISSING $SRC/$1 (and $P/$1): stop and ask the user"; return 1; }
    mkdir -p "$(dirname "$P/$1")"
    rm -rf "${P:?}/${1:?}.part"   # the :? forms stop on an empty P or 1; the Bash safety check refuses a target that could be empty
    cp -a "$SRC/$1" "$P/$1.part" && mv "$P/$1.part" "$P/$1"
  fi
}
for item in mockharness/mock.py mockharness/common.py mockharness/media \
            chrome66/chromium/r540276/chrome-linux chrome66/tools/syntax-floor.js \
            chrome66/dl-acorn/package/dist chrome66/dl-ws/package; do
  copy_once "$item" || exit 1
done
for f in mockharness/mock.py mockharness/common.py mockharness/media/green.png mockharness/media/red.png \
         chrome66/chromium/r540276/chrome-linux/chrome chrome66/tools/syntax-floor.js \
         chrome66/dl-acorn/package/dist/acorn.js chrome66/dl-ws/package/index.js; do
  test -e "$P/$f" || { echo "MISSING $P/$f: stop and ask the user"; exit 1; }
done
test -x "$P/chrome66/chromium/r540276/chrome-linux/chrome" || { echo "chrome is not executable: stop"; exit 1; }
du -sh "$P"
echo "harness ready in $P"
```

Expected: `harness ready in /home/ahmed/signage-harness`, about 280M (almost all of it the Chromium 66
snapshot). Every later step and Task 10 use `H=/home/ahmed/signage-harness/mockharness` and
`C=/home/ahmed/signage-harness/chrome66`, never the scratchpad copy.

- [ ] **Step 1: Prepare the mock harness (outside the repo)**

The mock throws away what the player sends over the wall socket. Record it, so the test can check the
`screen` message.

**T.1 — `$H/mock.py` lines 257-261.** Skip T.1 when `grep -q WS-RECV /home/ahmed/signage-harness/mockharness/mock.py`
succeeds: an earlier run has already made this change, and the text below no longer matches. Otherwise replace:

```python
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
```

with:

```python
    try:
        while True:
            text = await websocket.receive_text()
            # What the player sends ({"type": "screen"}, pongs), for the tests.
            log({"method": "WS-RECV", "path": f"/walls/{wall_id}/ws", "data": text})
    except WebSocketDisconnect:
        pass
```

The scratchpad's old `run.sh` (not copied) mounts directories of another session. Use a new runner that
mounts this worktree's `player/` and runs as the calling user, so nothing in the harness ends up owned by
root.

Create `/home/ahmed/signage-harness/mockharness/run-wallsetup.sh`:

```bash
#!/usr/bin/env bash
# Usage: run-wallsetup.sh <test.py> [player dir inside the container, default /player]
# Runs a mock-harness test in signage_renderer:latest (host network, ports
# 3150/3151) against the wall-setup worktree's player/, as the calling user.
# The container is e2e-wall-geo-py-<this pid>, --rm: nothing else is removed.
set -euo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
WT="${WT:-/home/ahmed/signage/.claude/worktrees/wall-setup}"
# The test starts its own mock on 3150/3151. If another mock is up there, the
# test would drive that one instead (grep without -q: no SIGPIPE under pipefail).
if ss -ltn | grep -E ':(3150|3151)\b' > /dev/null; then
  echo "PORT BUSY (3150/3151): another mock is up; find out whose before going on" >&2
  exit 1
fi
mkdir -p "$H/shots"
exec docker run --rm --name "e2e-wall-geo-py-$$" --network host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$H:/w" -v "$WT/player:/player:ro" -v "$H/shots:/shots" -w /w \
  --entrypoint python signage_renderer:latest "$@"
```

Then make the 30 s VP8 test video. It has the same red | green | blue bands as the e2e video; VP8 is
used because both test browsers decode it.

```bash
H=/home/ahmed/signage-harness/mockharness
if docker ps -a --format '{{.Names}}' | grep -qx e2e-wall-geo-ffmpeg; then
  echo "e2e-wall-geo-ffmpeg exists: another run owns it; stop"; exit 1
fi
docker run --rm --name e2e-wall-geo-ffmpeg --network none --user "$(id -u):$(id -g)" \
  -v "$H/media:/m" --entrypoint ffmpeg signage_backend:latest -y -v error \
  -f lavfi -i color=c=0xff0000:s=640x360:r=25:d=30 \
  -f lavfi -i color=c=0x00ff00:s=640x360:r=25:d=30 \
  -f lavfi -i color=c=0x0000ff:s=640x360:r=25:d=30 \
  -filter_complex "[0][1][2]hstack=inputs=3,format=yuv420p" \
  -c:v libvpx -b:v 300k -deadline realtime -cpu-used 8 -an /m/bands.webm
ls -l "$H/media/bands.webm"
(ss -ltn | grep -E ':(3150|3151|9335)\b') && echo "PORT BUSY: find out who uses it before going on" || echo "ports free"
```

Expected: `bands.webm` exists (about 190 KB), then `ports free`.

- [ ] **Step 2: Write the failing test**

Create `/home/ahmed/signage-harness/mockharness/t_wall_geometry.py`:

```python
"""Task 8 (phone wall setup): the player's free-wall geometry, in Playwright's
Chromium against the mock API.

Checks:
- A slice is scaled to fill the screen, at 1920x1080 and at 960x540.
- Geometry frames and a reconnect hello re-crop without replacing the media
  element, so a video keeps playing.
- The canvas can change size.
- A grid-wall cell is scaled too.
- The highlight shows "TV 2" until highlight_until_ms, null hides it, and
  leaving wall mode hides it.
- With Arabic labels the page is RTL and crops the same way.
- The screen size is reported when the socket opens and once after a resize,
  as exactly {"type":"screen","w":<int>,"h":<int>}.
- The clock offset is the largest recent sample (10 min window, 32 samples).
- The highlight ends on the server's clock (the player's own timer), not the
  TV's.

Run from this directory:  bash run-wallsetup.sh t_wall_geometry.py
Prints WALL-GEOMETRY OK. Any failed check raises AssertionError (exit 1).
It lives in /home/ahmed/signage-harness, not in the repo.
"""
import json
import os
import re
import struct
import sys
import time
import zlib

from playwright.sync_api import sync_playwright

from common import API, PLAYER, center_pixel, mock, mock_get, start_mock, state

MEDIA = os.environ.get("MEDIA_DIR", "/w/media")
PLAYER_DIR = sys.argv[1] if len(sys.argv) > 1 else "/player"


def now():
    return int(time.time() * 1000)


def write_bands_png(path, w=3840, h=720):
    """Red | green | blue, one third each: the layout of the e2e wall video."""
    third = w // 3
    row = (b"\x00" + b"\xff\x00\x00" * third + b"\x00\xff\x00" * third
           + b"\x00\x00\xff" * (w - 2 * third))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(row * h, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def play(item_id, url, mime, fit="fit"):
    return {"type": "play", "item": {"id": item_id, "url": url, "mime_type": mime, "name": "n"},
            "started_at_ms": now(), "duration_ms": 600000, "fit_mode": fit}


def free_hello(x, current_play, until=None, tv=2, canvas=(3840, 720), cell=(1280, 720)):
    return {"type": "hello", "wall_id": 7, "mode": "spanned",
            "cell": {"row": 0, "col": tv - 1, "rows": 1, "cols": 3},
            "canvas": {"w": canvas[0], "h": canvas[1]}, "bezel": {"h_pct": 0.0, "v_pct": 0.0},
            "cell_geometry": {"x": x, "y": 0, "w": cell[0], "h": cell[1]},
            "highlight_until_ms": until, "tv_number": tv, "current_play": current_play}


def geometry(x, until=None, tv=2, canvas=(3840, 720), cell=(1280, 720)):
    return {"type": "geometry", "canvas": {"w": canvas[0], "h": canvas[1]},
            "cell_geometry": {"x": x, "y": 0, "w": cell[0], "h": cell[1]},
            "highlight_until_ms": until, "tv_number": tv}


CANVAS = """() => {
  const c = document.querySelector('.wall-canvas');
  if (!c) return null;
  const b = c.getBoundingClientRect(), cs = getComputedStyle(c);
  const m = c.querySelector('.wall-media');
  return {
    transform: cs.transform, origin: cs.transformOrigin,
    rect: [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)],
    tag: m ? m.tagName : null, marker: m ? (m._marker || null) : null,
    ready: Boolean(m) && (m.tagName === 'IMG' ? (m.complete && m.naturalWidth > 0) : m.readyState >= 2),
    paused: m && m.tagName === 'VIDEO' ? m.paused : null,
    t: m && m.tagName === 'VIDEO' ? m.currentTime : null,
  };
}"""

HIGHLIGHT = """() => {
  const h = document.getElementById('wall-highlight');
  if (!h) return null;
  const cs = getComputedStyle(h);
  const label = document.getElementById('wall-highlight-label');
  return { shown: !h.classList.contains('hidden') && cs.display !== 'none',
           label: label ? label.textContent : null,
           z: cs.zIndex, pointer: cs.pointerEvents, position: cs.position };
}"""

SKEW = "() => effectiveNowMs() - Date.now()"

CLOCK_UNIT = """() => {
  const saved = { samples: wallClockSamples, offset: wallClockOffsetMs };
  let t = 1e9;
  performance.now = () => t;
  const out = {};
  try {
    wallClockSamples = [];
    noteServerTime(t + 1000);                     out.a = wallClockOffsetMs;
    t += 60 * 1000;  noteServerTime(t + 500);     out.b = wallClockOffsetMs;
    t += 570 * 1000; noteServerTime(t + 400);     out.c = wallClockOffsetMs;
    t += 60 * 1000;  noteServerTime(t + 300);     out.d = wallClockOffsetMs;
    out.now = effectiveNowMs() - t;
    wallClockSamples = [];
    for (let i = 0; i < 40; i += 1) noteServerTime(t + 2000 - i);
    out.cap = wallClockOffsetMs;
    out.kept = wallClockSamples.length;
    noteServerTime(NaN);
    noteServerTime(undefined);
    out.ignored = wallClockSamples.length;
  } finally {
    delete performance.now;
    wallClockSamples = saved.samples;
    wallClockOffsetMs = saved.offset;
  }
  return out;
}"""
# a: first sample. b: a smaller one does not lower it. c: at 10.5 min the
# first sample is out of the window, the 1-min one (500) is the max. d: at
# 11.5 min that one is out too. cap: 40 samples, only the last 32 (2000-8..)
# count. NaN/undefined are not samples.
CLOCK_EXPECTED = {"a": 1000, "b": 1000, "c": 500, "d": 400, "now": 400,
                  "cap": 1992, "kept": 32, "ignored": 32}


def wait_eval(page, js, pred, timeout_s, what):
    """Poll page.evaluate(js) until pred(value) is true; at most timeout_s."""
    end = time.time() + timeout_s
    while True:
        last = page.evaluate(js)
        if pred(last):
            return last
        if time.time() > end:
            raise AssertionError(f"timed out after {timeout_s}s: {what}; last value {last!r}")
        page.wait_for_timeout(100)


def wait_until(page, fn, timeout_s, what):
    end = time.time() + timeout_s
    while True:
        value = fn()
        if value:
            return value
        if time.time() > end:
            raise AssertionError(f"timed out after {timeout_s}s: {what}")
        page.wait_for_timeout(100)


SCREEN_TEXT = re.compile(r'^\{"type":"screen","w":(\d+),"h":(\d+)\}$')


def screen_reports():
    """(w, h) of every {"type": "screen"} the player sent, oldest first.

    Each must be exactly {"type":"screen","w":<int>,"h":<int>} on the wire:
    the server (Task 6) ignores a float such as 1080.5, a string or a bool.
    Python's json would read 1080.0 as a float that still == 1080, so the
    raw text is checked."""
    out = []
    for entry in mock_get("/__mock/log"):
        if entry.get("method") != "WS-RECV":
            continue
        text = entry.get("data") or ""
        try:
            msg = json.loads(text)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("type") == "screen":
            m = SCREEN_TEXT.match(text)
            assert m, f"screen report is not exactly type + integer w and h: {text!r}"
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def is_rgb(px, want):
    """want 'r', 'g' or 'b': that channel high, the other two low."""
    idx = "rgb".index(want)
    return all((px[i] > 200) if i == idx else (px[i] < 70) for i in range(3))


def open_tv(browser, token, size, lang=None):
    """One TV = one browser context (own localStorage, own socket)."""
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]},
                              service_workers="block")
    if lang:
        ctx.add_cookies([{"name": "khanshoof_lang", "value": lang, "url": PLAYER}])
    page = ctx.new_page()
    problems = []

    def on_console(msg):
        if "missing key" in msg.text:
            problems.append(f"console: {msg.text}")

    page.on("pageerror", lambda err: problems.append(f"pageerror: {err}"))
    page.on("console", on_console)
    page.add_init_script(f"localStorage.setItem('screen_token', '{token}')")
    page.goto(f"{PLAYER}/?api_base={API}")
    return ctx, page, problems


proc = start_mock(PLAYER_DIR)
try:
    write_bands_png(os.path.join(MEDIA, "bands.png"))
    assert os.path.exists(os.path.join(MEDIA, "bands.webm")), "make media/bands.webm first (Step 1)"
    bands = play(41, "/uploads/bands.png", "image/png")
    state(content={"items": [], "wall_id": 7}, layout={"zones": []}, hello=free_hello(1280, bands))
    mock("/__mock/clear_log")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, problems = open_tv(browser, "tok_wall_geo", (1920, 1080))

        # 1. The 1280x720 slice at x=1280 of a 3840x720 canvas fills 1920x1080.
        c = wait_eval(page, CANVAS, lambda v: bool(v and v["ready"]), 15, "free-wall image on screen")
        assert c["transform"] == "matrix(1.5, 0, 0, 1.5, -1920, 0)", f"transform at 1920x1080: {c}"
        assert c["origin"] == "0px 0px", c
        assert c["rect"] == [-1920, 0, 5760, 1080], c
        px = center_pixel(page)
        assert is_rgb(px, "g"), f"centre of TV 2 should be the green band: {px}"
        h = page.evaluate(HIGHLIGHT)
        assert h and not h["shown"], f"no highlight without highlight_until_ms: {h}"
        assert (h["z"], h["pointer"], h["position"]) == ("4", "none", "fixed"), h
        print("ok 1: scale 1.5 crop at 1920x1080")

        # 2. The socket's first message is the screen size, in JSON integers
        #    (screen_reports() fails on any other shape).
        wait_until(page, lambda: (1920, 1080) in screen_reports(), 5, "screen report on open")
        print("ok 2: screen report on open, integer w and h")

        # 3. 960x540: re-cropped at once; reported once, after the 1 s debounce.
        mock("/__mock/clear_log")
        page.set_viewport_size({"width": 960, "height": 540})
        c = wait_eval(page, CANVAS, lambda v: v["transform"] == "matrix(0.75, 0, 0, 0.75, -960, 0)",
                      3, "scale 0.75 after resize")
        assert c["rect"] == [-960, 0, 2880, 540], c
        page.wait_for_timeout(300)
        assert screen_reports() == [], f"reported before the debounce: {screen_reports()}"
        wait_until(page, lambda: screen_reports() == [(960, 540)], 4, "one report 1 s after the resize")
        page.wait_for_timeout(1500)
        assert screen_reports() == [(960, 540)], f"more than one report: {screen_reports()}"
        print("ok 3: scale 0.75 at 960x540, one debounced report")

        # 4. A geometry frame moves the box: new crop, same media element.
        page.evaluate("() => { document.querySelector('.wall-canvas .wall-media')._marker = 'm1'; }")
        mock("/__mock/wall_send", geometry(2560))
        c = wait_eval(page, CANVAS, lambda v: v["transform"] == "matrix(0.75, 0, 0, 0.75, -1920, 0)",
                      3, "crop after a geometry frame")
        assert c["marker"] == "m1", f"geometry replaced the media element: {c}"
        px = center_pixel(page)
        assert is_rgb(px, "b"), f"centre should be the blue band now: {px}"
        print("ok 4: geometry re-crops without a remount")

        # 5. Highlight until highlight_until_ms (server clock), then gone by itself.
        until = now() + 2500
        mock("/__mock/wall_send", geometry(2560, until=until))
        h = wait_eval(page, HIGHLIGHT, lambda v: v["shown"], 2, "highlight shown")
        assert h["label"] == "TV 2", h
        wait_eval(page, HIGHLIGHT, lambda v: not v["shown"], 5, "highlight hidden after highlight_until_ms")
        assert now() >= until - 200, f"hid {until - now()} ms early"
        mock("/__mock/wall_send", geometry(2560, until=now() + 60000))
        wait_eval(page, HIGHLIGHT, lambda v: v["shown"], 2, "highlight shown again")
        mock("/__mock/wall_send", geometry(2560, until=None))
        wait_eval(page, HIGHLIGHT, lambda v: not v["shown"], 1, "null hides at once")
        print("ok 5: highlight shows TV 2, expires, null hides")

        # 6. A reconnect hello with the box moved: re-cropped, not remounted
        #    (same item, same started_at_ms).
        state(hello=free_hello(0, bands))
        mock("/__mock/close_ws")
        c = wait_eval(page, CANVAS,
                      lambda v: bool(v) and v["transform"] == "matrix(0.75, 0, 0, 0.75, 0, 0)",
                      8, "crop after a reconnect hello")
        assert c["marker"] == "m1", f"a geometry-only hello remounted the media: {c}"
        px = center_pixel(page)
        assert is_rgb(px, "r"), f"centre should be the red band: {px}"
        print("ok 6: reconnect hello re-crops without a remount")

        # 7. The canvas changes size (the wall got another video): width and
        #    height follow, no remount.
        mock("/__mock/wall_send", geometry(0, canvas=(1920, 1080), cell=(1920, 1080)))
        c = wait_eval(page, CANVAS, lambda v: v["rect"] == [0, 0, 960, 540], 3, "canvas resized to 1920x1080")
        assert c["transform"] == "matrix(0.5, 0, 0, 0.5, 0, 0)" and c["marker"] == "m1", c
        print("ok 7: canvas size change without a remount")

        # 8. A grid-wall cell (1x2 grid, canvas 1920x1080, cell 0/1) at 1920x1080
        #    is stretched to fill, instead of half its slice and black.
        page.set_viewport_size({"width": 1920, "height": 1080})
        state(hello={"type": "hello", "wall_id": 7, "mode": "spanned",
                     "cell": {"row": 0, "col": 1, "rows": 1, "cols": 2},
                     "canvas": {"w": 1920, "h": 1080}, "bezel": {"h_pct": 0, "v_pct": 0},
                     "cell_geometry": {"x": 960, "y": 0, "w": 960, "h": 1080},
                     "current_play": play(42, "/uploads/green.png", "image/png", fit="fill")})
        mock("/__mock/close_ws")
        c = wait_eval(page, CANVAS,
                      lambda v: bool(v) and v["ready"] and v["transform"] == "matrix(2, 0, 0, 1, -1920, 0)",
                      8, "grid cell scaled 2x1")
        assert c["rect"] == [-1920, 0, 3840, 1080], c
        assert not page.evaluate(HIGHLIGHT)["shown"], "a grid hello must not show the highlight"
        print("ok 8: grid-wall cell scaled to fill")

        # 9. A free-wall video keeps playing while its box moves.
        state(hello=free_hello(1280, play(43, "/uploads/bands.webm", "video/webm")))
        mock("/__mock/close_ws")
        c = wait_eval(page, CANVAS,
                      lambda v: bool(v) and v["tag"] == "VIDEO" and v["ready"] and not v["paused"],
                      10, "free-wall video playing")
        page.evaluate("() => { document.querySelector('.wall-canvas video')._marker = 'v1'; }")
        t0 = c["t"]
        for x in (1000, 1100, 1200, 1300):
            mock("/__mock/wall_send", geometry(x))
            page.wait_for_timeout(200)
        c = wait_eval(page, CANVAS, lambda v: v["transform"] == "matrix(1.5, 0, 0, 1.5, -1950, 0)",
                      3, "video re-cropped")
        assert c["marker"] == "v1" and not c["paused"] and c["t"] > t0, \
            f"the video was replaced or stopped: {c}, t0={t0}"
        print("ok 9: a playing video keeps playing through geometry frames")

        # 10. Leaving wall mode hides the highlight (a "bye": the TV was unpaired).
        mock("/__mock/wall_send", geometry(1300, until=now() + 60000))
        wait_eval(page, HIGHLIGHT, lambda v: v["shown"], 2, "highlight before bye")
        mock("/__mock/wall_send", {"type": "bye"})
        wait_eval(page, HIGHLIGHT, lambda v: not v["shown"], 2, "leaveWallMode hides the highlight")
        assert not problems, problems
        ctx.close()
        print("ok 10: leaving wall mode hides the highlight")

        # 11. Arabic: translated label; the RTL page crops exactly like LTR.
        state(hello=free_hello(1280, bands, until=now() + 60000))
        ctx, page, problems = open_tv(browser, "tok_wall_geo_ar", (1920, 1080), lang="ar")
        c = wait_eval(page, CANVAS, lambda v: bool(v and v["ready"]), 15, "Arabic TV picture")
        assert page.evaluate("() => document.documentElement.dir") == "rtl"
        assert c["rect"] == [-1920, 0, 5760, 1080], f"RTL crop differs from LTR: {c}"
        h = wait_eval(page, HIGHLIGHT, lambda v: v["shown"], 2, "Arabic highlight")
        assert h["label"] == "شاشة 2", h
        assert not problems, problems
        ctx.close()
        print("ok 11: Arabic label, RTL crop")

        # 12. Clock: the largest recent sample wins; a late one does not pull it down.
        state(hello=free_hello(1280, bands))
        ctx, page, problems = open_tv(browser, "tok_wall_clock", (1280, 720))
        wait_eval(page, CANVAS, lambda v: bool(v and v["ready"]), 15, "clock TV picture")
        assert abs(page.evaluate(SKEW)) < 150, page.evaluate(SKEW)
        mock("/__mock/wall_send", {"type": "ping", "server_now_ms": now() + 5000})
        wait_eval(page, SKEW, lambda d: abs(d - 5000) < 150, 2, "offset follows a server 5 s ahead")
        mock("/__mock/wall_send", {"type": "ping", "server_now_ms": now() + 4200})   # 800 ms late
        page.wait_for_timeout(400)
        d = page.evaluate(SKEW)
        assert abs(d - 5000) < 150, f"an 800 ms-late sample pulled the offset down: {d}"
        mock("/__mock/wall_send", {"type": "ping", "server_now_ms": now() + 5300})
        wait_eval(page, SKEW, lambda d: abs(d - 5300) < 150, 2, "a less delayed sample raises the offset")
        r = page.evaluate(CLOCK_UNIT)
        assert r == CLOCK_EXPECTED, f"clock samples: {r}"
        print("ok 12: clock offset = max of recent samples (10 min, 32 samples)")

        # 13. The highlight ends on the server's clock, by the player's own
        #     timer: no frame comes when it ends (R9). The server is 5.3 s
        #     ahead of this TV now, so "until server now + 2 s" lasts about
        #     2 s. On the TV's own clock it would last 7.3 s.
        t0 = now()
        mock("/__mock/wall_send", dict(geometry(1280, until=t0 + 7300), server_now_ms=t0 + 5300))
        wait_eval(page, HIGHLIGHT, lambda v: v["shown"], 2, "highlight shown (server 5.3 s ahead)")
        wait_eval(page, HIGHLIGHT, lambda v: not v["shown"], 4, "highlight hidden on the server clock")
        took = now() - t0
        assert 1700 <= took <= 3000, f"highlight lasted {took} ms: want about 2000 (server clock), not 7300"
        assert not problems, problems
        ctx.close()
        print("ok 13: the highlight ends on the server clock")
        browser.close()
    print("WALL-GEOMETRY OK")
finally:
    proc.terminate()
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/ahmed/signage-harness/mockharness && bash run-wallsetup.sh t_wall_geometry.py`

Expected: FAIL. The traceback ends in
`AssertionError: transform at 1920x1080: {'transform': 'matrix(1, 0, 0, 1, -1280, 0)', 'origin': '1920px 360px', 'rect': [-1280, 0, 3840, 720], ...}`.
Today's player only translates the canvas, so TV 2 shows canvas x 1280-3200 at 1:1: its own green
slice, then 640 px of TV 3's blue, and black below y = 720.

- [ ] **Step 4: Implement the player.js changes**

**8.1 — `player/player.js` lines 13-15.** Replace:

```js
const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");
const zonesEl = document.getElementById("zones");
```

with:

```js
const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");
const zonesEl = document.getElementById("zones");
const wallHighlightEl = document.getElementById("wall-highlight");
const wallHighlightLabelEl = document.getElementById("wall-highlight-label");
```

**8.2 — `player/player.js` line 47.** Replace:

```js
const WALL_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
```

with:

```js
const WALL_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
// The server's clock is estimated from the frames of the last 10 minutes, at
// most 32 of them (see noteServerTime).
const WALL_CLOCK_WINDOW_MS = 10 * 60 * 1000;
const WALL_CLOCK_MAX_SAMPLES = 32;
// A wall TV reports its screen size this long after the window stops resizing.
const WALL_SCREEN_REPORT_DEBOUNCE_MS = 1000;
```

**8.3 — `player/player.js` lines 1000-1009.** Replace:

```js
let wallClockOffsetMs = 0;
let wallLastFrame = null;
let wallNode = null;
let wallDriftTimer = null;
let wallSpannedGeometry = null;
// Recently shown wall items, offered to the SW cache (bounded).
const wallSeen = new Map();
const WALL_SEEN_MAX = 30;

function effectiveNowMs() { return Date.now() + wallClockOffsetMs; }
```

with:

```js
// The server's clock minus performance.now(). Until the first frame arrives
// it is this TV's own clock, as before.
let wallClockOffsetMs = Date.now() - performance.now();
// Recent clock samples: { at: performance.now() on arrival, offset }.
let wallClockSamples = [];
let wallLastFrame = null;
let wallNode = null;
let wallDriftTimer = null;
let wallSpannedGeometry = null;
let wallHighlightTimer = null;
let wallScreenReportTimer = null;
// Recently shown wall items, offered to the SW cache (bounded).
const wallSeen = new Map();
const WALL_SEEN_MAX = 30;

/* Every frame carries server_now_ms. Each sample (server_now_ms minus
   performance.now() on arrival) is the true offset minus that frame's network
   delay, so the largest sample of the last 10 minutes is the least delayed
   one. The offset used to be the latest frame's, delay and all. The samples
   use performance.now(), which never jumps: a TV whose own clock is stepped
   (NTP after boot) keeps a valid estimate, where Date.now()-based samples
   would let a stale one win for ten minutes. */
function noteServerTime(serverNowMs) {
  if (typeof serverNowMs !== "number" || !isFinite(serverNowMs)) return;
  const at = performance.now();
  wallClockSamples.push({ at: at, offset: serverNowMs - at });
  while (wallClockSamples.length > WALL_CLOCK_MAX_SAMPLES
         || at - wallClockSamples[0].at > WALL_CLOCK_WINDOW_MS) {
    wallClockSamples.shift();
  }
  let best = wallClockSamples[0].offset;
  for (let i = 1; i < wallClockSamples.length; i += 1) {
    if (wallClockSamples[i].offset > best) best = wallClockSamples[i].offset;
  }
  wallClockOffsetMs = best;
}

// The server's clock, in ms since the epoch.
function effectiveNowMs() { return performance.now() + wallClockOffsetMs; }
```

(The newest sample is always 0 ms old, so the `while` loop never empties the array, and
`wallClockSamples[0]` exists.)

**8.4 — `player/player.js` lines 1026-1034.** Replace:

```js
function leaveWallMode() {
  wallId = null;
  clearTimeout(wallReconnectTimer);
  wallReconnectTimer = null;
  closeWallSocket();
  ConnectionStatus.setWs("none");
  clearWallPicture();
  wallSpannedGeometry = null;
}
```

with:

```js
function leaveWallMode() {
  wallId = null;
  clearTimeout(wallReconnectTimer);
  wallReconnectTimer = null;
  clearTimeout(wallScreenReportTimer);
  wallScreenReportTimer = null;
  closeWallSocket();
  ConnectionStatus.setWs("none");
  clearWallPicture();
  hideWallHighlight();
  wallSpannedGeometry = null;
}
```

**8.5 — `player/player.js` lines 1074-1080.** Replace:

```js
  socket.addEventListener("open", () => {
    if (wallSocket !== socket) return;
    ConnectionStatus.setWs("open");
    ConnectionStatus.markFrame();
    wallReconnectAttempts = 0;
    wallLastMessageAt = Date.now();
  });
```

with:

```js
  socket.addEventListener("open", () => {
    if (wallSocket !== socket) return;
    ConnectionStatus.setWs("open");
    ConnectionStatus.markFrame();
    wallReconnectAttempts = 0;
    wallLastMessageAt = Date.now();
    reportWallScreen();   // the phone shapes this TV's box from it
  });
```

**8.6 — `player/player.js` lines 1103-1119.** Replace:

```js
function onWallFrame(frame) {
  const clientReceivedMs = Date.now();
  if (typeof frame.server_now_ms === "number") {
    wallClockOffsetMs = frame.server_now_ms - clientReceivedMs;
  }
  if (frame.type === "hello") {
    // Mode may have changed since the last hello (spanned <-> mirrored, a
    // new grid or canvas): rebuild rather than crop with stale geometry.
    const geometry = frame.mode === "spanned"
      ? { canvas: frame.canvas, cell_geometry: frame.cell_geometry, bezel: frame.bezel }
      : null;
    const changed = JSON.stringify(geometry) !== JSON.stringify(wallSpannedGeometry);
    wallSpannedGeometry = geometry;
    if (frame.current_play) renderWallPlay(frame.current_play, changed);
    else clearWallPicture();
  } else if (frame.type === "play") {
    renderWallPlay(frame, false);
```

with:

```js
function onWallFrame(frame) {
  const clientReceivedMs = Date.now();
  noteServerTime(frame.server_now_ms);
  if (frame.type === "hello") {
    // Spanned <-> mirrored rebuilds the picture: a cropped canvas and a plain
    // full-screen node are built differently. Anything else (a moved box, a
    // canvas of another size, another grid) only re-crops the picture that is
    // up, so a playing video keeps playing. renderWallPlay still remounts
    // when the item itself changed.
    const geometry = frame.mode === "spanned"
      ? { canvas: frame.canvas, cell_geometry: frame.cell_geometry }
      : null;
    const rebuild = (geometry === null) !== (wallSpannedGeometry === null);
    wallSpannedGeometry = geometry;
    applyWallTransform();
    if (frame.current_play) renderWallPlay(frame.current_play, rebuild);
    else clearWallPicture();
    showWallHighlight(frame.highlight_until_ms, frame.tv_number);
  } else if (frame.type === "geometry") {
    onWallGeometry(frame);
  } else if (frame.type === "play") {
    renderWallPlay(frame, false);
```

**8.7 — `player/player.js` line 1139 (insert before `clearWallPicture`).** Replace:

```js
function clearWallPicture() {
  stopWallDrift();
```

with:

```js
/* Crops this TV's slice out of the wall canvas and scales it to fill the
   screen. With the origin at the canvas's top-left corner,
   scale(vw / cell.w, vh / cell.h) translate(-cell.x px, -cell.y px) maps the
   slice exactly onto the viewport. The canvas used to be translated only,
   which was right only when the slice happened to be the viewport's size. A
   free wall's box has the TV's own shape, so both scales are equal there; a
   grid cell of another shape is stretched to fill. Runs on hello, geometry,
   mount and window resize, and never touches the media element. */
function applyWallTransform() {
  const canvasEl = contentEl.querySelector(".wall-canvas");
  const g = wallSpannedGeometry;
  if (!canvasEl || !g || !g.canvas || !g.cell_geometry) return;
  const cw = Number(g.canvas.w);
  const ch = Number(g.canvas.h);
  const x = Number(g.cell_geometry.x) || 0;
  const y = Number(g.cell_geometry.y) || 0;
  const w = Number(g.cell_geometry.w);
  const h = Number(g.cell_geometry.h);
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const vh = document.documentElement.clientHeight || window.innerHeight;
  if (!(cw > 0 && ch > 0 && w > 0 && h > 0 && vw > 0 && vh > 0)) return;
  canvasEl.style.setProperty("--wall-w-px", String(cw));
  canvasEl.style.setProperty("--wall-h-px", String(ch));
  canvasEl.style.transform = "scale(" + (vw / w) + "," + (vh / h) + ") translate("
    + (-x) + "px," + (-y) + "px)";
}

/* A box moved on the phone, the TV reported another shape, or the wall got a
   video of another size: re-crop, and show or hide the outline. Only a hello
   switches between a cropped and a full-screen picture. The media element is
   left alone, so a playing video keeps playing. */
function onWallGeometry(frame) {
  if (wallSpannedGeometry && frame.canvas && frame.cell_geometry) {
    wallSpannedGeometry = { canvas: frame.canvas, cell_geometry: frame.cell_geometry };
    applyWallTransform();
  }
  showWallHighlight(frame.highlight_until_ms, frame.tv_number);
}

/* The outline and "TV 2" a phone asks for while it places this TV's box.
   untilMs is on the server's clock (highlight_until_ms). null, or a time
   already past, hides it at once. */
function showWallHighlight(untilMs, n) {
  const remainingMs = typeof untilMs === "number" ? untilMs - effectiveNowMs() : 0;
  if (!wallHighlightEl || !(remainingMs > 0)) {
    hideWallHighlight();
    return;
  }
  clearTimeout(wallHighlightTimer);
  wallHighlightTimer = setTimeout(hideWallHighlight, remainingMs);
  if (wallHighlightLabelEl) {
    wallHighlightLabelEl.textContent = typeof n === "number"
      ? Khan.t("wall.tv_label", "TV {n}").replace("{n}", String(n))
      : "";
  }
  wallHighlightEl.classList.remove("hidden");
}

function hideWallHighlight() {
  clearTimeout(wallHighlightTimer);
  wallHighlightTimer = null;
  if (wallHighlightEl) wallHighlightEl.classList.add("hidden");
}

/* This TV's screen size in CSS px, from which the phone gives the TV's box
   the TV's own shape. Sent when the socket opens and once the window has
   stopped resizing. */
function reportWallScreen() {
  const w = Math.round(document.documentElement.clientWidth || window.innerWidth || 0);
  const h = Math.round(document.documentElement.clientHeight || window.innerHeight || 0);
  if (!(w > 0 && h > 0)) return;
  try {
    if (wallSocket && wallSocket.readyState === WebSocket.OPEN) {
      wallSocket.send(JSON.stringify({ type: "screen", w: w, h: h }));
    }
  } catch (_) {}
}

// A rotated tablet, a resized window, a WebView that settles late: re-crop at
// once, and report the new size when it has settled.
window.addEventListener("resize", () => {
  if (mode !== "wall") return;
  applyWallTransform();
  clearTimeout(wallScreenReportTimer);
  wallScreenReportTimer = setTimeout(() => {
    wallScreenReportTimer = null;
    reportWallScreen();
  }, WALL_SCREEN_REPORT_DEBOUNCE_MS);
});

function clearWallPicture() {
  stopWallDrift();
```

**8.8 — `player/player.js` lines 1181-1199 (in `renderWallPlay`).** Replace:

```js
  if (wallSpannedGeometry) {
    const { canvas, cell_geometry } = wallSpannedGeometry;
    const wrap = document.createElement("div");
    wrap.className = "cell-viewport";
    wrap.innerHTML = `
      <div class="wall-canvas"
           style="--wall-w-px:${Number(canvas.w)}; --wall-h-px:${Number(canvas.h)};
                  --cell-x-px:${Number(cell_geometry.x)}; --cell-y-px:${Number(cell_geometry.y)};">
      </div>
    `;
    contentEl.appendChild(wrap);
    node.classList.add("wall-media");
    node.dataset.fit = frame.fit_mode || "fit";
    // Inline: "#content img { object-fit: contain }" outranks the
    // .wall-media[data-fit] rules, so fill/stretch never took effect.
    const fit = WALL_FIT[frame.fit_mode] || "contain";
    node.style.cssText = `position:absolute; top:0; left:0; width:100%; height:100%; object-fit:${fit};`;
    wrap.querySelector(".wall-canvas").appendChild(node);
  } else {
```

with:

```js
  if (wallSpannedGeometry) {
    const wrap = document.createElement("div");
    wrap.className = "cell-viewport";
    const canvasEl = document.createElement("div");
    canvasEl.className = "wall-canvas";
    wrap.appendChild(canvasEl);
    contentEl.appendChild(wrap);
    node.classList.add("wall-media");
    node.dataset.fit = frame.fit_mode || "fit";
    // Inline: "#content img { object-fit: contain }" outranks the
    // .wall-media[data-fit] rules, so fill/stretch never took effect.
    const fit = WALL_FIT[frame.fit_mode] || "contain";
    node.style.cssText = `position:absolute; top:0; left:0; width:100%; height:100%; object-fit:${fit};`;
    canvasEl.appendChild(node);
    applyWallTransform();   // canvas size and crop come from wallSpannedGeometry
  } else {
```

Every other reader of `wallSpannedGeometry` is shown above: the declaration, the reset in
`leaveWallMode`, the hello, `onWallGeometry`, `applyWallTransform` and this mount. A `play` frame with a
new item remounts through this branch and gets the current crop from `applyWallTransform()`.

- [ ] **Step 5: Implement the markup and styles**

**8.9 — `player/index.html` line 21.** Replace:

```html
      <div id="zones" class="zones-layout hidden"></div>
```

with:

```html
      <div id="zones" class="zones-layout hidden"></div>
      <div id="wall-highlight" class="wall-highlight hidden" aria-hidden="true"><span id="wall-highlight-label" class="wall-highlight-label"></span></div>
```

The overlay is a static sibling of `#content`, like the connection dot, because `clearContainer(contentEl)`
empties `#content` on every remount.

**8.10 — `player/styles.css` lines 20-21.** Replace:

```css
:lang(ar) .empty-state,
:lang(ar) #status {
```

with:

```css
:lang(ar) .empty-state,
:lang(ar) .wall-highlight,
:lang(ar) #status {
```

**8.11 — `player/styles.css` lines 145-161.** Replace:

```css
.pairing,
.empty-state { --u: 0.5625vw; }

@media (min-aspect-ratio: 16/9) {
  .pairing,
  .empty-state { --u: 1vh; }
}

@media (orientation: portrait) {
  .pairing,
  .empty-state { --u: 1.2vw; }
}

@media (orientation: portrait) and (min-aspect-ratio: 5/8) {
  .pairing,
  .empty-state { --u: 0.75vh; }
}
```

with:

```css
.pairing,
.empty-state,
.wall-highlight { --u: 0.5625vw; }

@media (min-aspect-ratio: 16/9) {
  .pairing,
  .empty-state,
  .wall-highlight { --u: 1vh; }
}

@media (orientation: portrait) {
  .pairing,
  .empty-state,
  .wall-highlight { --u: 1.2vw; }
}

@media (orientation: portrait) and (min-aspect-ratio: 5/8) {
  .pairing,
  .empty-state,
  .wall-highlight { --u: 0.75vh; }
}
```

**8.12 — `player/styles.css` lines 460-475.** Replace:

```css
.cell-viewport {
  position: fixed; top: 0; right: 0; bottom: 0; left: 0; overflow: hidden; background: #000;
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
.wall-media[data-fit="fit"]     { object-fit: contain; }
.wall-media[data-fit="fill"]    { object-fit: cover;   }
.wall-media[data-fit="stretch"] { object-fit: fill;    }
```

with:

```css
.cell-viewport {
  position: fixed; top: 0; right: 0; bottom: 0; left: 0; overflow: hidden; background: #000;
}
/* The whole wall picture at canvas size. player.js (applyWallTransform) sets
   transform: scale(...) translate(...) inline: that crops this TV's slice and
   scales it to fill the screen, which needs the origin at the canvas's
   top-left corner and top/left set. Without them an RTL (Arabic) page puts
   an absolute box's static position on the right. No will-change: a layer
   promoted at one scale keeps that raster, so an image would stay soft after
   its box was resized on the phone. A video is composited either way. */
.wall-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width:  calc(var(--wall-w-px) * 1px);
  height: calc(var(--wall-h-px) * 1px);
  transform-origin: 0 0;
}
.wall-media[data-fit="fit"]     { object-fit: contain; }
.wall-media[data-fit="fill"]    { object-fit: cover;   }
.wall-media[data-fit="stretch"] { object-fit: fill;    }

/* While a phone places this TV's box: an outline around the screen and the
   TV's number in the middle. It sits over the picture and under the
   connection dot (5), the welcome view (5) and pairing (10). Sized in --u,
   which is set with .pairing above, and kept to Chrome 66 CSS the same way. */
.wall-highlight {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 4;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border: calc(var(--u) * 1.2) solid #E09478;
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
}
.wall-highlight-label {
  font-size: calc(var(--u) * 18);
  font-weight: 700;
  line-height: 1;
  color: #FFFFFF;
  background: rgba(0, 0, 0, 0.6);
  padding: calc(var(--u) * 2) calc(var(--u) * 5);
  border-radius: calc(var(--u) * 3);
  white-space: nowrap;
}
.wall-highlight-label:empty { display: none; }
```

`.hidden { display: none !important; }` (line 102) hides the overlay despite its `display: flex`.
`:lang(ar) .wall-highlight` has higher specificity than `.wall-highlight`, so Arabic gets IBM Plex Sans
Arabic.

- [ ] **Step 6: Add the string**

**8.13 — `player/i18n/en.json` line 28.** Replace:

```json
  "wall.reconnecting": "Reconnecting to wall…",
```

with:

```json
  "wall.reconnecting": "Reconnecting to wall…",
  "wall.tv_label": "TV {n}",
```

**8.14 — `player/i18n/ar.json` line 28.** Replace:

```json
  "wall.reconnecting": "جاري إعادة الاتصال بالجدار…",
```

with:

```json
  "wall.reconnecting": "جاري إعادة الاتصال بالجدار…",
  "wall.tv_label": "شاشة {n}",
```

(`Khan.t` has no interpolation. `showWallHighlight` does `.replace("{n}", String(n))`.)

- [ ] **Step 7: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage-harness/mockharness && bash run-wallsetup.sh t_wall_geometry.py`

Expected: PASS. It prints `ok 1` through `ok 13`, then `WALL-GEOMETRY OK`, and exits 0 in under a
minute. Only the harness in `/home/ahmed/signage-harness` tests the player, so no existing repo test needs a re-run for
this task. The backend suite is not touched.

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup && python3 scripts/check_i18n.py`
Expected: `i18n OK across frontend, landing, player`

- [ ] **Step 8: Check the Chrome 66 syntax floor and banned constructs**

Run:

```bash
C=/home/ahmed/signage-harness/chrome66
cd /home/ahmed/signage/.claude/worktrees/wall-setup
node "$C/tools/syntax-floor.js" "$C/dl-acorn/package/dist/acorn.js" player/player.js player/sw.js player/i18n.js
echo "--- js"; grep -nE '\?\.|\?\?|\b[0-9]+_[0-9]+\b|flatMap|\.flat\(|globalThis|fromEntries|replaceAll|\.at\(|allSettled' player/player.js
echo "--- css"; grep -nE '\binset\b|(^|[;{ ])gap\s*:|:is\(|:where\(|(^|[;{ ])aspect-ratio\s*:|(^|[;{ ])(scale|translate|rotate)\s*:|focus-visible' player/styles.css
```

Expected:
- `player/player.js: syntax floor Chrome 55`, `player/sw.js: syntax floor Chrome 55` and
  `player/i18n.js: syntax floor Chrome 55`. Every file must be at 66 or lower; a new `Chrome 66`
  line would mean a `catch {` without a binding.
- After `--- js`, only line 3 (the header comment `no ?. or ??, no 1_000 separators`).
- After `--- css`, only the existing lines: the comment `flexbox gap (84) or inset (87)` (now line
  115), the comment `no :focus-visible (and` (now line 395), and the two
  `:focus:not(:focus-visible)` selectors (now lines 405-406).

- [ ] **Step 9: Write the Chrome 66 driver**

Create `/home/ahmed/signage-harness/mockharness/cr66_wall_geometry.js`:

```js
// Chrome 66 run of the Task 8 wall checks against the mock harness: a
// free-wall cell (scale, geometry frame without remount, highlight on the
// server clock, resize, integer screen report) and a grid-wall cell (scale
// 2x1), plus the clock samples.
// Images only: Chromium 66 r540276 cannot play H.264, and these checks are
// about the crop, not the decoder.
//
// Needs the mock up on localhost:3150/3151 (plan Step 10) and media/bands.png
// (written by t_wall_geometry.py). Usage: node cr66_wall_geometry.js
// Env: C66 (default ../chrome66 beside this file), CR66_NAME (container,
// default e2e-wall-geo-cr66), CR66_PORT (CDP port, default 9335).
// It refuses to start when a container of that name exists or the CDP port
// answers, and it removes only the container it started itself.
// Prints CR66 WALL-GEOMETRY OK and exits 0, or the failure and exits 1.
"use strict";
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const C66 = process.env.C66 || path.join(__dirname, "..", "chrome66");
const WebSocket = require(path.join(C66, "dl-ws", "package"));
const CHROME_DIR = path.join(C66, "chromium", "r540276", "chrome-linux");
const NAME = process.env.CR66_NAME || "e2e-wall-geo-cr66";
const PORT = Number(process.env.CR66_PORT || 9335);
// The page uses localhost (the mock's CORS allows http://localhost:3151 only).
// Node 18's fetch resolves localhost to ::1 first and the mock listens on
// 127.0.0.1, so this script talks to it by address.
const API = "http://localhost:3150";
const PLAYER = "http://localhost:3151";
const MOCK = "http://127.0.0.1:3150";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now();
const steps = [];

async function mock(p, body) {
  const res = await fetch(MOCK + p, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body === undefined ? {} : body),
  });
  if (!res.ok) throw new Error(`${p}: ${res.status}`);
  return res.json();
}

// [w, h] of every screen report, oldest first. Each must be exactly
// {"type":"screen","w":<int>,"h":<int>} on the wire: the server ignores a
// float, a string or a bool.
async function screenReports() {
  const log = await (await fetch(`${MOCK}/__mock/log`)).json();
  return log.filter((e) => e.method === "WS-RECV")
    .map((e) => { try { return { text: e.data, msg: JSON.parse(e.data) }; } catch (_) { return null; } })
    .filter((r) => r && r.msg && r.msg.type === "screen")
    .map((r) => {
      const m = /^\{"type":"screen","w":(\d+),"h":(\d+)\}$/.exec(r.text);
      if (!m) throw new Error(`screen report is not exactly type + integer w and h: ${r.text}`);
      return [Number(m[1]), Number(m[2])];
    });
}

function play(id, url, mime, fit) {
  return { type: "play", item: { id, url, mime_type: mime, name: "n" },
           started_at_ms: now(), duration_ms: 600000, fit_mode: fit || "fit" };
}
function freeHello(x, currentPlay) {
  return { type: "hello", wall_id: 7, mode: "spanned", cell: { row: 0, col: 1, rows: 1, cols: 3 },
           canvas: { w: 3840, h: 720 }, bezel: { h_pct: 0, v_pct: 0 },
           cell_geometry: { x, y: 0, w: 1280, h: 720 },
           highlight_until_ms: null, tv_number: 2, current_play: currentPlay };
}
function geometry(x, until) {
  return { type: "geometry", canvas: { w: 3840, h: 720 }, cell_geometry: { x, y: 0, w: 1280, h: 720 },
           highlight_until_ms: until === undefined ? null : until, tv_number: 2 };
}

// Starts Chrome 66 as container NAME. It never removes a container it did
// not start (R23): when NAME exists, another run owns it, and this throws
// before anything is started.
function launch(w, h) {
  const names = execSync("docker ps -a --format '{{.Names}}'").toString().split("\n");
  if (names.indexOf(NAME) !== -1) {
    throw new Error(`${NAME} exists: another run owns it; wait for it, or remove it if it is yours`);
  }
  execSync([
    `docker run -d --rm --name ${NAME} --network host`,
    `--user ${process.getuid()}:${process.getgid()} -e HOME=/tmp`,
    `-v ${CHROME_DIR}:/c:ro --entrypoint /c/chrome signage_renderer:latest`,
    "--headless --no-sandbox --disable-gpu --hide-scrollbars",
    "--autoplay-policy=no-user-gesture-required",
    `--remote-debugging-port=${PORT} --remote-debugging-address=127.0.0.1`,
    `--window-size=${w},${h} --user-data-dir=/tmp/profile about:blank`,
  ].join(" "));
}

async function cdpAnswers() {
  try { await fetch(`http://127.0.0.1:${PORT}/json/version`); return true; } catch (_) { return false; }
}

async function connect() {
  for (let i = 0; i < 100; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch (_) {}
    await sleep(200);
  }
  throw new Error("chrome 66 did not come up");
}

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = {};
    ws.on("message", (raw) => {
      const msg = JSON.parse(raw);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result);
      } else if (msg.method && this.handlers[msg.method]) {
        this.handlers[msg.method].forEach((fn) => fn(msg.params));
      }
    });
  }
  send(method, params) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(method, fn) { (this.handlers[method] = this.handlers[method] || []).push(fn); }
}

async function openPage() {
  const ws = new WebSocket(await connect(), { perMessageDeflate: false });
  await new Promise((resolve, reject) => { ws.on("open", resolve); ws.on("error", reject); });
  const cdp = new Cdp(ws);
  const exceptions = [];
  cdp.on("Runtime.exceptionThrown", (p) => {
    const d = p.exceptionDetails;
    exceptions.push(`${(d.exception && d.exception.description) || d.text} @ ${d.url || ""}:${d.lineNumber + 1}`);
  });
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  const ev = async (expression) => {
    const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`evaluate failed: ${JSON.stringify(r.exceptionDetails)}`);
    return r.result.value;
  };
  return { cdp, ev, exceptions, close: () => ws.close() };
}

async function waitEv(page, expr, pred, ms, what) {
  const end = Date.now() + ms;
  let last;
  for (;;) {
    try { last = await page.ev(expr); if (pred(last)) return last; } catch (e) { last = String(e); }
    if (Date.now() > end) throw new Error(`timed out (${ms} ms): ${what}; last ${JSON.stringify(last)}`);
    await sleep(100);
  }
}
async function waitFor(fn, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    if (await fn()) return;
    if (Date.now() > end) throw new Error(`timed out (${ms} ms): ${what}`);
    await sleep(100);
  }
}
function check(ok, what, detail) {
  if (!ok) throw new Error(`${what}: ${JSON.stringify(detail)}`);
  steps.push(what);
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const CANVAS = `(() => {
  const c = document.querySelector('.wall-canvas');
  if (!c) return null;
  const b = c.getBoundingClientRect(), cs = getComputedStyle(c);
  const m = c.querySelector('.wall-media');
  return { transform: cs.transform, origin: cs.transformOrigin,
    rect: [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)],
    marker: m ? (m._marker || null) : null,
    ready: Boolean(m && m.tagName === 'IMG' && m.complete && m.naturalWidth > 0) };
})()`;
const HIGHLIGHT = `(() => {
  const h = document.getElementById('wall-highlight');
  if (!h) return null;
  return { shown: !h.classList.contains('hidden') && getComputedStyle(h).display !== 'none',
           label: document.getElementById('wall-highlight-label').textContent };
})()`;
const CLOCK_UNIT = `(() => {
  const saved = { samples: wallClockSamples, offset: wallClockOffsetMs };
  let t = 1e9;
  performance.now = function () { return t; };
  const out = {};
  try {
    wallClockSamples = [];
    noteServerTime(t + 1000); out.a = wallClockOffsetMs;
    t += 60 * 1000; noteServerTime(t + 500); out.b = wallClockOffsetMs;
    t += 570 * 1000; noteServerTime(t + 400); out.c = wallClockOffsetMs;
    t += 60 * 1000; noteServerTime(t + 300); out.d = wallClockOffsetMs;
    out.now = effectiveNowMs() - t;
    wallClockSamples = [];
    for (let i = 0; i < 40; i += 1) noteServerTime(t + 2000 - i);
    out.cap = wallClockOffsetMs;
    out.kept = wallClockSamples.length;
    noteServerTime(NaN);
    noteServerTime(undefined);
    out.ignored = wallClockSamples.length;
  } finally {
    delete performance.now;
    wallClockSamples = saved.samples;
    wallClockOffsetMs = saved.offset;
  }
  return out;
})()`;
const CLOCK_EXPECTED = { a: 1000, b: 1000, c: 500, d: 400, now: 400, cap: 1992, kept: 32, ignored: 32 };

async function main() {
  if (!fs.existsSync(path.join(__dirname, "media", "bands.png"))) {
    throw new Error("media/bands.png missing: run t_wall_geometry.py first");
  }
  const bands = play(41, "/uploads/bands.png", "image/png");
  await mock("/__mock/state", { content: { items: [], wall_id: 7 }, layout: { zones: [] },
                                hello: freeHello(1280, bands) });
  await mock("/__mock/clear_log");
  if (await cdpAnswers()) throw new Error(`port ${PORT} answers: another Chrome is up; find out whose`);
  launch(1920, 1080);   // throws before starting anything when NAME is taken
  let page = null;
  try {
    page = await openPage();
    await page.cdp.send("Page.addScriptToEvaluateOnNewDocument",
                        { source: "localStorage.setItem('screen_token', 'tok_cr66_wall');" });
    await page.cdp.send("Page.navigate", { url: `${PLAYER}/?api_base=${API}` });
    const ua = await page.ev("navigator.userAgent");
    check(/Chrome\/66\./.test(ua), `running ${ua.match(/Chrome\/[\d.]+/)[0]}`, ua);

    let c = await waitEv(page, CANVAS, (v) => v && v.ready, 20000, "free-wall image on screen");
    check(c.transform === "matrix(1.5, 0, 0, 1.5, -1920, 0)", "free wall 1920x1080: scale 1.5", c);
    check(same(c.rect, [-1920, 0, 5760, 1080]) && c.origin === "0px 0px", "canvas rect [-1920,0,5760,1080]", c);
    await waitFor(async () => (await screenReports()).some((r) => same(r, [1920, 1080])), 5000, "screen report on open");
    steps.push("screen report on open: 1920x1080");

    await page.ev("document.querySelector('.wall-canvas .wall-media')._marker = 'm1'");
    await mock("/__mock/wall_send", geometry(2560));
    c = await waitEv(page, CANVAS, (v) => v && v.transform === "matrix(1.5, 0, 0, 1.5, -3840, 0)", 3000, "crop after geometry");
    check(c.marker === "m1", "geometry frame re-crops, same media element", c);

    const until = now() + 2500;
    await mock("/__mock/wall_send", geometry(2560, until));
    const h = await waitEv(page, HIGHLIGHT, (v) => v && v.shown, 2000, "highlight shown");
    check(h.label === "TV 2", "highlight label TV 2", h);
    await waitEv(page, HIGHLIGHT, (v) => v && !v.shown, 5000, "highlight hidden after highlight_until_ms");
    check(now() >= until - 200, "highlight lasted until highlight_until_ms", { early: until - now() });
    await mock("/__mock/wall_send", geometry(2560, now() + 60000));
    await waitEv(page, HIGHLIGHT, (v) => v && v.shown, 2000, "highlight shown again");
    await mock("/__mock/wall_send", geometry(2560, null));
    await waitEv(page, HIGHLIGHT, (v) => v && !v.shown, 1000, "null hides at once");
    steps.push("null highlight hides at once");

    // The highlight ends on the server's clock, by the player's own timer (no
    // frame comes when it ends). With the server 5 s ahead of this TV,
    // "until server now + 2 s" lasts about 2 s, not 7 s.
    const t0 = now();
    await mock("/__mock/wall_send", Object.assign(geometry(2560, t0 + 7000), { server_now_ms: t0 + 5000 }));
    await waitEv(page, HIGHLIGHT, (v) => v && v.shown, 2000, "highlight shown (server 5 s ahead)");
    await waitEv(page, HIGHLIGHT, (v) => v && !v.shown, 4000, "highlight hidden on the server clock");
    const took = now() - t0;
    check(took >= 1700 && took <= 3000, "highlight ends on the server clock (2 s, not 7 s)", { took });

    await mock("/__mock/clear_log");
    await page.cdp.send("Emulation.setDeviceMetricsOverride",
                        { width: 960, height: 540, deviceScaleFactor: 1, mobile: false });
    await waitEv(page, CANVAS, (v) => v && v.transform === "matrix(0.75, 0, 0, 0.75, -1920, 0)", 3000, "scale 0.75 after resize");
    steps.push("960x540: scale 0.75");
    await waitFor(async () => same(await screenReports(), [[960, 540]]), 4000, "one screen report after the resize");
    steps.push("one debounced screen report: 960x540");
    await page.cdp.send("Emulation.clearDeviceMetricsOverride");
    await waitEv(page, CANVAS, (v) => v && v.transform === "matrix(1.5, 0, 0, 1.5, -3840, 0)", 3000, "scale 1.5 again");

    await mock("/__mock/state", { hello: {
      type: "hello", wall_id: 7, mode: "spanned", cell: { row: 0, col: 1, rows: 1, cols: 2 },
      canvas: { w: 1920, h: 1080 }, bezel: { h_pct: 0, v_pct: 0 },
      cell_geometry: { x: 960, y: 0, w: 960, h: 1080 },
      current_play: play(42, "/uploads/green.png", "image/png", "fill") } });
    await mock("/__mock/close_ws");
    c = await waitEv(page, CANVAS, (v) => v && v.ready && v.transform === "matrix(2, 0, 0, 1, -1920, 0)", 8000, "grid cell scaled 2x1");
    check(same(c.rect, [-1920, 0, 3840, 1080]), "grid cell 0/1: canvas [-1920,0,3840,1080] (was [-960,0,1920,1080])", c);
    const hg = await page.ev(HIGHLIGHT);
    check(hg && !hg.shown, "grid hello: no highlight", hg);

    const r = await page.ev(CLOCK_UNIT);
    check(same(r, CLOCK_EXPECTED), "clock: max of samples, 10 min window, 32 samples", r);
    check(page.exceptions.length === 0, "no exceptions", page.exceptions);
  } finally {
    if (page) page.close();
    // Reached only after launch() started NAME, so this is this run's own
    // container (also when Chrome never came up).
    execSync(`docker rm -f ${NAME} > /dev/null 2>&1 || true`);
  }
}

main().then(() => {
  steps.forEach((s) => console.log(`ok: ${s}`));
  console.log("CR66 WALL-GEOMETRY OK");
}).catch((err) => {
  steps.forEach((s) => console.log(`ok: ${s}`));
  console.log(`CR66 WALL-GEOMETRY FAIL: ${err.message}`);
  process.exitCode = 1;
});
```

- [ ] **Step 10: Run the checks in Chromium 66**

Start the mock with this worktree's player, run the driver, and stop the mock again. The block stops
before starting anything when a container name or a port is taken, so it never stops another run's
mock:

```bash
H=/home/ahmed/signage-harness/mockharness
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
for n in e2e-wall-geo-mock e2e-wall-geo-cr66; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$n"; then echo "$n exists: another run owns it; stop"; exit 1; fi
done
if ss -ltn | grep -E ':(3150|3151|9335)\b'; then echo "PORT BUSY: find out who uses it before going on"; exit 1; fi
mkdir -p "$H/shots"
docker run -d --rm --name e2e-wall-geo-mock --network host --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$H:/w" -v "$WT/player:/player:ro" -w /w --entrypoint python signage_renderer:latest /w/mock.py \
  > /dev/null || exit 1
curl -fs --retry 30 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:3150/__mock/log \
  && curl -fs --retry 30 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:3151/index.html \
  || echo "the mock did not answer on 3150/3151"
node "$H/cr66_wall_geometry.js" 2>&1 | tee "$H/shots/cr66-wall.log"; rc=${PIPESTATUS[0]}
docker stop e2e-wall-geo-mock > /dev/null
echo "cr66 exit $rc"
```

Expected: a list of `ok:` lines, also kept in `$H/shots/cr66-wall.log`. The first is
`ok: running Chrome/66.0.3359.0`. Among them are `ok: screen report on open: 1920x1080`,
`ok: highlight ends on the server clock (2 s, not 7 s)` and
`ok: grid cell 0/1: canvas [-1920,0,3840,1080] (was [-960,0,1920,1080])`. Then
`CR66 WALL-GEOMETRY OK` and `cr66 exit 0`, and `docker ps -a --format '{{.Names}}' | grep e2e-wall-geo-`
prints nothing. Run against the player before Step 4, the same commands fail
at `free wall 1920x1080: scale 1.5` with `"transform":"matrix(1, 0, 0, 1, -1280, 0)"`. Node 18 may print an
`ExperimentalWarning` about `fetch` (18.20.4 does not); either way is fine.

- [ ] **Step 11: Commit**

The harness in `/home/ahmed/signage-harness` (`mock.py`, `run-wallsetup.sh`, `t_wall_geometry.py`,
`cr66_wall_geometry.js` and the `media/bands.*` files) stays out of the repo; Task 10 re-runs it from
there. The commit contains only player files:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop otherwise
git add player/player.js player/styles.css player/index.html player/i18n/en.json player/i18n/ar.json
git status --short   # exactly those five files staged, nothing else changed
git commit -m "feat(player): scale wall slices to the screen, live geometry, highlight, tighter clock

A spanned cell's slice is now scaled to fill the screen. It used to be
translated only, so a grid cell of another size than the TV showed part
of its slice and black. The crop is set from JS on hello, geometry, mount
and resize. A geometry-only change never remounts the media, so a
playing video keeps playing.

New geometry frames move a free wall's box live. A highlight shows
'TV n' until highlight_until_ms on the server's clock. The TV reports
its screen size when the socket opens and after resizes. The clock
offset is the largest sample of the last 10 minutes (at most 32), timed
with performance.now(), instead of the latest frame's delayed one.

Chrome 66 safe: syntax floor 55, transform property only, no inset/gap.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: E2E: three TVs on a free wall, driven through the API

All work happens in the PR 1 worktree `/home/ahmed/signage/.claude/worktrees/wall-setup` (branch
`feature/phone-wall-setup`). Paths are relative to it. In commands, `WT` is that worktree and `S` is
`/home/ahmed/signage-harness/handoff`, outside `/tmp`, so the logs survive a restart until Task 10 quotes
them (each command block sets both and creates `$S`).

The harness runs in **loopback mode** (D9). The TVs load the player from `http://127.0.0.1:13022`, a
player container whose `API_BASE_URL` is the local API `http://127.0.0.1:18020`. Playwright's
`route()` never intercepts WebSockets, so a TV loaded as `https://play.khanshoof.com` would open its
wall socket to **production**. In loopback mode the CSP's `connect-src`/`media-src` already allow the
local API, and nothing has to be proxied. The phone is not used yet: Task 16 adds the phone scenarios
and the `e2e-wall-app` dashboard container.

Facts this task relies on (verified while planning):
- The `signage_renderer:latest` image (Playwright 1.62, Chromium 151) has no ffmpeg, PIL or zxing. PIL
  and zxing come from `.pylib`, and the video is made with the backend image's ffmpeg.
- The ffmpeg command in run.sh below produces a file byte-identical to the one verified in
  recon/e2e.md section 4 (5760x1080, H.264 High, 20.000 s, 218,846 bytes). The contract's
  `TRANSCODE_SCALE_FILTER` turns it into 3840x720, 20.000 s.
- The helpers were run in `signage_renderer:latest`. On ffmpeg crops of the transcoded video scaled to
  1920x1080, `dominant_color` gives red 92–97%, green 92–97% and blue 96%. `dark_share` gives 0.24–0.27
  in the centre box and 0.00 in the edge strips. `parse_matrix`/`expected_matrix` match Chromium's
  computed `matrix(...)`. `SKEW_READ_JS` reads −3001/−1/+2000 under the skews. One page sampled three
  times gives a sync spread of 0.07–0.13 ms, so the method has no systematic error.
- `timed_colour_move` was run in `signage_renderer:latest` on a 960x540 DPR 2 page playing the
  transcoded test video, with Task 8's `.wall-canvas` crop and `.wall-highlight` CSS, and a stand-in
  for the PUT that moved the crop 80 ms later. The blue third measured 92% blue with the outline and
  "TV 2" up (96% without), and the picture was blue 150–190 ms after the PUT. A 1.5 s delivery was
  measured as 1.6 s, and a moved crop over a blank picture failed.
- A seek leaves a video a constant ~50–110 ms off (measured) until the player's drift corrector
  (every 2 s, ±50 ms dead band) pulls it back. The one-item wall playlist seeks at every slot
  restart, so a sync round counts only when every video is ≥ 6 s into its loop.
- `run.sh` runs `python -I`, so the script's own directory is not on `sys.path`. Also, `e2e_pair.py`
  binds `API` to `:18000` when imported. `e2e_wall.py` therefore **copies** `api`, `multipart`,
  `decode_qr` and `wait_for_code` rather than importing them.

**Files:**
- Create: `scripts/e2e-scan-to-pair/e2e_wall.py`
- Modify: `scripts/e2e-scan-to-pair/run.sh:8-12` (header comment: `--wall` usage, `e2e-wall-*` names)
  and insert the `--wall` block between line 15 (`repo="$(cd "$here/../.." && pwd)"`) and line 16
  (`backend_image="${BACKEND_IMAGE:-signage_backend:latest}"`). Lines 16–59, the pair run, stay
  byte-identical.
- Test: `scripts/e2e-scan-to-pair/run.sh --wall` (runs `e2e_wall.py`), plus the unchanged default
  `scripts/e2e-scan-to-pair/run.sh` (runs `e2e_pair.py`), which must still pass.

**Interfaces:**
- Consumes:
  - **Login:** `POST /auth/login` → `{"token"}`, with the bootstrap admin `demo` /
    `Khanshoof-Demo-2026` that run.sh seeds through `ADMIN_USERNAME`/`ADMIN_PASSWORD`. That admin is in org
    "Default" (plan pro, `screen_limit` 25, active).
  - **Media:** `POST /media/upload` (multipart field `file`) and `GET /media`. Rows carry `filename`
    (it becomes `*.opt.mp4` after transcoding) and the Task 1 columns `width_px`, `height_px` and
    `duration_seconds`. Task 2's `transcode_video` applies `-vf media_probe.TRANSCODE_SCALE_FILTER`
    and stores the re-probed size in the **same** UPDATE that swaps the filename.
  - **Task 5:**
    - `POST /walls/free` `{"name", "media_id", "code"}` → 201 `serialize_wall(...)`, which has `id`,
      `layout`, `mode`, `canvas_width_px`, `canvas_height_px` and `cells[]`.
    - `POST /walls/{wall_id}/tvs` `{"code"}` → 201, the new `wall_cells` row (`row_index`,
      `col_index`, …).
  - **Task 7:**
    - `PUT /walls/{wall_id}/cells/{row}/{col}/placement` `{"x","y","w","h","highlight"}` → 204.
      It sets `highlight_until_ms` to now + `HIGHLIGHT_SECONDS` × 1000, or NULL, then calls
      `walls_mod.publish_geometry`.
    - `DELETE /walls/{id}/cells/{r}/{c}/pairing` on a free wall deletes the cell and sets `cols` to the
      highest remaining index + 1.
  - **Task 6:**
    - `GET /walls/{wall_id}` cells carry `screen_w_px`/`screen_h_px`, stored from the TV's
      `{"type": "screen", "w", "h"}`.
    - `geometry` frames reach the worker holding the socket through `walls.geometry_channel()`.
  - **Task 8 (player):**
    - `.wall-canvas` has an inline `transform: scale(sx,sy) translate(-x px,-y px)` with
      `transform-origin: 0 0`.
    - `#wall-highlight` (class `hidden` when off) contains `#wall-highlight-label`, which reads
      "TV {n}" (`wall.tv_label`).
    - The `screen` message is sent when the socket opens.
    - The top-level `let mode` global is `"wall"` in wall mode.
- Produces (Task 10 runs these; Task 16 extends them):
  - **`run.sh --wall`:**
    - Builds `signage_backend:e2e` from `$repo/backend` and `signage_player:e2e-wall` from
      `PLAYER_DIR` (default `$repo/player`).
    - Containers `e2e-wall-api`, `e2e-wall-db` (`-c max_connections=200`), `e2e-wall-redis`,
      `e2e-wall-player` and `e2e-wall-ffmpeg`, on network `e2e-wall-net`.
    - Ports: API 127.0.0.1:18020, player 127.0.0.1:13022.
    - The API runs with `WEB_CONCURRENCY=4` and `ALLOWED_ORIGINS` including `http://127.0.0.1:13022` and
      `http://127.0.0.1:13023`.
    - The test video is `shots/wall-test.mp4`.
    - Shell names: the array `wall_containers` and the functions `cleanup_wall` and `run_wall`. Task 16
      appends `e2e-wall-app` and the dashboard build.
    - Env: `PLAYER_DIR`, and `E2E_WALL_REPLACE=1`, which removes leftover `e2e-wall-*` containers.
      Without it, a run refuses to start while any of `e2e-wall-api`, `-db`, `-redis`, `-player`, `-app`
      or `-ffmpeg` exists, because another session's run may own them (R23). Use it only for your own
      killed run's leftovers; Step 1 says how to tell. Task 8's `e2e-wall-geo-*` names do not block it.
  - **`e2e_wall.py`:**
    - `lan_context(browser, locale="en-US", size=(1920, 1080), scale=1, touch=False, skew_ms=0, device=None, block_fonts=True)`.
      It refuses, and records in `BLOCKED`, every request to a khanshoof.com host (`KHAN`) or to a port
      production publishes on this host's loopback, 8000/3000/3001/3003 (`PROD_LOCAL`).
    - `wait_eval(page, js, pred, timeout_s, what="the condition", interval_ms=100)`
    - `dominant_color(path) -> (band | None, share)`
    - `dark_share(path, box) -> float`
    - `sample_sync_spread_ms(pages, samples=12, gap_ms=250, max_rounds=100, settle_s=6.0, end_s=1.0) -> (median_ms, spreads)`
    - `wait_for_code(page, previous=None, timeout_s=20)`
    - `api(...)`, `multipart(...)`, `decode_qr(element, name)`, `parse_matrix(value)`,
      `expected_matrix(box, canvas, viewport)`, `matrix_close(got, want)`
    - `timed_move(page, auth, wall_id, tv, x, canvas, timeout_s=7.0) -> seconds` and
      `timed_colour_move(page, auth, wall_id, tv, x, band, canvas, path, timeout_s=7.0) -> (transform_s, picture_s, share)`
    - `api_wall(browser, auth) -> timings` and `main()`
    - The module list `BLOCKED`, and the constants `API`, `LAN_PLAY`, `OUT`, `KHAN`, `PROD_LOCAL`,
      `WS_PREFIX` and `TVS`.
    - `touch_drag` is added by Task 16.
  - **Output:** the script prints `E2E WALL OK` and a `timings:` JSON line with `live_move_median_s`,
    `live_moves_s`, `live_colour_move_s`, `sync_median_ms`, `highlight_off_s`, `unpair_to_new_code_s`
    and the join times. It fails unless the five live moves, the move onto blue (`live_colour_move_s`)
    and `highlight_off_s` are each under 1 s (R11) and `sync_median_ms` is under 100 (R10).

- [ ] **Step 1: Prepare (no repo change): free names and ports, and a local `.pylib`**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
docker ps -a --format '{{.Names}}  created {{.CreatedAt}}  {{.Status}}' \
  | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg) ' && echo "BUSY: e2e-wall containers exist" || echo "no e2e-wall containers"
docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -F e2e_wall.py \
  && echo "BUSY: an e2e_wall.py runner is alive" || echo "no e2e_wall.py runner"
(ss -ltn | grep -E ':(18020|13022)\b') && echo "PORT BUSY: find out who holds it" || echo "ports 18020/13022 free"
# .pylib (zxing-cpp 2.3.0 + pillow 11.3.0, gitignored). Copying it skips the PyPI install, which this
# host's flaky DNS can break.
[ -d "$WT/scripts/e2e-scan-to-pair/.pylib" ] || cp -a \
  /home/ahmed/signage/.claude/worktrees/player-cache/scripts/e2e-scan-to-pair/.pylib \
  "$WT/scripts/e2e-scan-to-pair/.pylib"
git -C "$WT" status --short    # expect nothing: .pylib/ and shots/ are gitignored
```

Expected: `no e2e-wall containers`, `no e2e_wall.py runner`, `ports 18020/13022 free`, and an empty
`git status`. If the player-cache copy is gone, skip the `cp`: run.sh installs `.pylib` from PyPI itself.

On a `BUSY` line, wait (R23: never remove a container the plan did not start). `E2E_WALL_REPLACE=1`
(which removes them) is only for your own run's leftovers: `no e2e_wall.py runner` is printed, and
the `created` time matches the start of a run of yours that was interrupted, for example one the Bash
tool's timeout killed. Otherwise another session owns them: leave them and ask the user. Task 8's
`e2e-wall-geo-*` containers use other names and ports, and neither this check nor run.sh counts them.

- [ ] **Step 2: Write the failing test: create `scripts/e2e-scan-to-pair/e2e_wall.py`**

```python
"""End-to-end video wall test, driven through the API (the phone UI comes
later). Run it with run.sh --wall, which starts the stack: the backend built
from this tree with 4 workers (as in production), its own Postgres and Redis,
and a player container whose API_BASE_URL is that backend.

Loopback mode. The TVs load the player from http://127.0.0.1:13022 and talk
to the API at http://127.0.0.1:18020 directly, WebSockets and video Range
requests included. Playwright's routing never sees WebSockets: a TV loaded
as https://play.khanshoof.com would open its wall socket to production.
Every request to a khanshoof.com host, or to a port production publishes on
this host's loopback (8000, 3000, 3001, 3003: the player and the dashboard
fall back to <page host>:8000 when API_BASE_URL is empty), is refused and
recorded instead, and the test fails if there was one.

Three TVs, each in its own browser context, with Date.now() shifted the way
a TV with a wrong clock would be:
  TV 1  1920x1080           -3000 ms
  TV 2   960x540 at DPR 2       0 ms   (a high-DPR TV reports CSS px)
  TV 3  1920x1080           +2000 ms
The 5760x1080 test video (shots/wall-test.mp4, made by run.sh) has red,
green and blue thirds with large black digits 1, 2, 3 and a white bar
sweeping across. The backend transcodes it to 3840x720.

Checks, in the order they run:
  1. TV 1's QR decodes to https://app.khanshoof.com/pair?code=<its code>
  2. the upload is transcoded to 3840x720 (the 3840 px cap), 20 s long
  3. POST /walls/free with TV 1's code, then POST /walls/{id}/tvs for TV 2
     and TV 3: each TV leaves its pairing screen and plays the wall video
  b. crop: with the boxes on the red, green and blue thirds, each TV's
     .wall-canvas transform is scale(screen / box) translate(-box), and its
     screenshot is mostly its own band's colour, with its digit in the
     middle and no black at the edges; each TV reported its screen size
  c. live move: a placement PUT reaches TV 2's transform in under 1 s, five
     times out of five. The tick loop's fallback takes up to 5 s, and with 4
     workers the PUT and TV 2's socket are usually on different ones, so
     this is the Redis path. Then one move onto the blue third: within 1 s
     the transform follows and TV 2's picture turns from green to blue
  d. highlight: "TV 2" shows while highlight=true and goes within 1 s of a
     highlight=false PUT (the same Redis path); TV 1 and TV 3 show none
  e. sync: the TVs' video.currentTime agree within 100 ms (median of 12
     samples, each 6 s or more into the loop), despite the shifted clocks
  f. DELETE TV 3's pairing: TV 3 shows a new pairing code; the wall keeps
     two cells, and TV 1 and TV 2 stay in wall mode
  a. every TV's WebSockets went to ws://127.0.0.1:18020/walls/<id>/ws, and
     no request went to a khanshoof.com host or to production's loopback ports
Screenshots: shots/wall-*.png (wall-fail-tv<n>.png when a check fails).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.environ.get("E2E_PYLIB", "/w/.pylib"))
import zxingcpp  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

API = os.environ.get("E2E_API", "http://127.0.0.1:18020").rstrip("/")
LAN_PLAY = os.environ.get("E2E_PLAY_LAN", "http://127.0.0.1:13022").rstrip("/")
OUT = os.environ.get("E2E_OUT", "/w/shots")
USER = os.environ.get("E2E_USER", "demo")
PASSWORD = os.environ.get("E2E_PASSWORD", "Khanshoof-Demo-2026")
VIDEO = os.environ.get("E2E_WALL_VIDEO", os.path.join(OUT, "wall-test.mp4"))
# What the TV's QR points at: the player container's APP_URL (run.sh). The
# test never loads it.
APP_URL = "https://app.khanshoof.com"
# Where every wall socket must go: the local API, http -> ws.
WS_PREFIX = "ws" + API[len("http"):] + "/walls/"

KHAN = re.compile(r"^(https?|wss?)://([^/]*\.)?khanshoof\.com(:\d+)?(/|$)")
# Production publishes its API, dashboard, player and landing page on this
# host's loopback. The player and the dashboard fall back to <page host>:8000
# when API_BASE_URL is empty, so a misconfigured container would reach
# production's API at 127.0.0.1:8000. Refused and recorded like KHAN.
PROD_LOCAL = re.compile(r"^(https?|wss?)://(127\.0\.0\.1|localhost|\[::1\]):(8000|3000|3001|3003)(/|$)")
FONTS = re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/")
SKEW_JS = ("(() => { const off = %d; const real = Date.now; "
           "Date.now = function () { return real.call(Date) + off; }; })();")
BLOCKED = []   # khanshoof.com and production-loopback URLs some page asked for (all refused)

THIRD = 1.0 / 3.0
# Which channel is high and which two are low, per band.
BANDS = {"red": (0, 1, 2), "green": (1, 0, 2), "blue": (2, 0, 1)}
TVS = [
    {"n": 1, "size": (1920, 1080), "scale": 1, "skew_ms": -3000, "band": "red", "x": 0.0},
    {"n": 2, "size": (960, 540), "scale": 2, "skew_ms": 0, "band": "green", "x": THIRD},
    {"n": 3, "size": (1920, 1080), "scale": 1, "skew_ms": 2000, "band": "blue", "x": 2 * THIRD},
]

# page.evaluate, not wait_for_function: the latter evaluates a string, which
# the player's CSP (script-src 'self') refuses, and the CSP stays on.
TRANSFORM_JS = """() => {
  const c = document.querySelector('#content .wall-canvas');
  return c ? getComputedStyle(c).transform : null;
}"""
PLAYING_JS = """() => {
  const v = document.querySelector('#content .wall-canvas video');
  return v ? {src: v.currentSrc, ready: v.readyState, paused: v.paused} : null;
}"""
HIGHLIGHT_JS = """() => {
  const h = document.getElementById('wall-highlight');
  if (!h) return null;
  const label = document.getElementById('wall-highlight-label');
  return {shown: !h.classList.contains('hidden') && getComputedStyle(h).display !== 'none',
          label: label ? label.textContent.trim() : ''};
}"""
SAMPLE_JS = """() => {
  const v = document.querySelector('#content .wall-canvas video');
  if (!v) return null;
  return {t: v.currentTime, at: performance.timeOrigin + performance.now(),
          ok: !v.paused && !v.seeking && !v.ended && v.readyState >= 3,
          rate: v.playbackRate, dur: v.duration};
}"""
# This page's Date.now() against the real clock: the shift SKEW_JS applied.
SKEW_READ_JS = "() => Date.now() - (performance.timeOrigin + performance.now())"
MODE_JS = "() => (typeof mode === 'undefined' ? null : mode)"


def api(method, path, token=None, body=None, raw=None, content_type=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data, headers["Content-Type"] = raw, content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path} -> {e.code}: {e.read()[:500]!r}") from None


def multipart(field, filename, payload, mime):
    boundary = "----e2e-scan-to-pair"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    return head + payload + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def decode_qr(element, name):
    path = f"{OUT}/{name}"
    element.screenshot(path=path)
    found = zxingcpp.read_barcodes(Image.open(path))
    assert found, "no QR code found in the TV's pixels"
    return found[0].text


def wait_for_code(page, previous=None, timeout_s=20):
    """The pairing code the TV shows, once its pairing screen is up. With
    previous, waits for a different one: a TV sent back to pairing keeps
    its old code in the DOM until the new one arrives."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if page.is_visible("#pairing"):
            code = page.inner_text("#pairing-code").strip()
            if re.fullmatch(r"[A-Z2-9]{6}", code) and code != previous:
                page.wait_for_timeout(1200)   # fonts, QR image decode
                return code
        page.wait_for_timeout(250)
    raise AssertionError(f"TV never showed a {'new ' if previous else ''}pairing code")


def _refuse_khanshoof(route):
    BLOCKED.append(route.request.url)
    return route.abort()


def lan_context(browser, locale="en-US", size=(1920, 1080), scale=1, touch=False,
                skew_ms=0, device=None, block_fonts=True):
    """A context for loopback mode. Only requests to khanshoof.com and to
    production's loopback ports (PROD_LOCAL) are routed: refused and recorded
    in BLOCKED. The test's own loopback traffic goes straight to the
    containers. skew_ms shifts Date.now() from before the page's first
    script runs (add_init_script is not subject to the CSP) and survives
    the player's own reloads. block_fonts refuses Google Fonts: this host's
    DNS is unreliable, and a hung font stylesheet would hold up the page's
    load event for nothing the checks look at."""
    opts = dict(device) if device else {
        "viewport": {"width": size[0], "height": size[1]},
        "device_scale_factor": scale, "has_touch": touch}
    ctx = browser.new_context(locale=locale, service_workers="block", **opts)
    ctx.route(KHAN, _refuse_khanshoof)
    ctx.route(PROD_LOCAL, _refuse_khanshoof)
    if block_fonts:
        ctx.route(FONTS, lambda route: route.abort())
    if skew_ms:
        ctx.add_init_script(script=SKEW_JS % skew_ms)
    return ctx


def wait_eval(page, js, pred, timeout_s, what="the condition", interval_ms=100):
    """Polls page.evaluate(js) until pred(value) holds; returns the value."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = page.evaluate(js)
        if pred(value):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s} s waiting for {what}; "
                                 f"last value: {value!r}")
        page.wait_for_timeout(interval_ms)


def _classify(px):
    for name, (hi, lo1, lo2) in BANDS.items():
        if px[hi] >= 150 and px[lo1] <= 100 and px[lo2] <= 100:
            return name
    if max(px[:3]) <= 60:
        return "dark"
    return "other"


def _grid_counts(path, box=(0.0, 0.0, 1.0, 1.0), cols=64, rows=36):
    """Classifies a cols x rows grid of points spread over box (fractions
    of the picture): red, green, blue, dark or other."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = box
    counts = {}
    for j in range(rows):
        for i in range(cols):
            x = min(w - 1, int((x0 + (x1 - x0) * (i + 0.5) / cols) * w))
            y = min(h - 1, int((y0 + (y1 - y0) * (j + 0.5) / rows) * h))
            name = _classify(img.getpixel((x, y)))
            counts[name] = counts.get(name, 0) + 1
    return counts, cols * rows


def dominant_color(path):
    """The band colour most of a screenshot shows, and its share of the
    sample points. Thresholds, not exact values: pure green comes back as
    about (0, 216, 2) after yuv420. The black digit, the white bar and the
    highlight's outline count as no band."""
    counts, total = _grid_counts(path)
    bands = {k: v for k, v in counts.items() if k in BANDS}
    if not bands:
        return None, 0.0
    best = max(bands, key=bands.get)
    return best, bands[best] / total


def dark_share(path, box):
    """Share of near-black sample points in box (fractions of the picture)."""
    counts, total = _grid_counts(path, box, cols=32, rows=24)
    return counts.get("dark", 0) / total


def save_montage(paths, out):
    tiles = [Image.open(p).convert("RGB").resize((640, 360)) for p in paths]
    sheet = Image.new("RGB", (640 * len(tiles), 360))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (640 * i, 0))
    sheet.save(out)


def parse_matrix(value):
    """getComputedStyle(el).transform -> [a, b, c, d, e, f], or None."""
    m = re.fullmatch(r"matrix\(([^)]*)\)", (value or "").strip())
    return [float(v) for v in m.group(1).split(",")] if m else None


def expected_matrix(box, canvas, viewport):
    """The crop the player must set for box (fractions of the canvas):
    scale(vw / box_w_px, vh / box_h_px) translate(-box_x_px, -box_y_px)
    with transform-origin 0 0, which computes to
    matrix(sx, 0, 0, sy, -x_px * sx, -y_px * sy)."""
    cw, ch = canvas
    vw, vh = viewport
    sx = vw / (box["w"] * cw)
    sy = vh / (box["h"] * ch)
    return [sx, 0.0, 0.0, sy, -box["x"] * cw * sx + 0.0, -box["y"] * ch * sy + 0.0]   # + 0.0: no -0.0


def matrix_close(got, want):
    return got is not None and len(got) == 6 and all(
        abs(g - w) <= (0.002 if i < 4 else 1.0) for i, (g, w) in enumerate(zip(got, want)))


def sample_sync_spread_ms(pages, samples=12, gap_ms=250, max_rounds=100, settle_s=6.0, end_s=1.0):
    """Median spread, in ms, of the pages' video.currentTime at one instant;
    returns (median, the spreads sorted).

    Each round reads every page once, a few ms apart, and moves each reading
    back to the first page's instant along its own playback rate. The
    instants come from performance.timeOrigin + performance.now(), the real
    clock, which the Date.now() shift leaves alone. A round counts only while
    every video plays (not paused, seeking, ended or buffering) and is at
    least settle_s into its loop and end_s before its end. The one-item wall
    playlist restarts the video every slot with a seek, which leaves each TV
    up to a few hundred ms off until the drift corrector (every 2 s) has
    pulled it back within 50 ms; and a round straddling the restart would
    compare 19.9 s with 0.1 s.
    """
    spreads = []
    for _ in range(max_rounds):
        if len(spreads) >= samples:
            break
        s = [pg.evaluate(SAMPLE_JS) for pg in pages]
        if all(x and x["ok"] for x in s):
            ref = s[0]["at"]
            adj = [x["t"] - (x["at"] - ref) / 1000.0 * x["rate"] for x in s]
            dur = s[0]["dur"] or 20.0
            if all(settle_s <= t <= dur - end_s for t in adj):
                spreads.append((max(adj) - min(adj)) * 1000.0)
        pages[0].wait_for_timeout(gap_ms)
    assert len(spreads) >= samples, f"only {len(spreads)} usable sync samples in {max_rounds} rounds"
    spreads.sort()
    return spreads[len(spreads) // 2], spreads


def placement_path(wall_id, cell):
    return f"/walls/{wall_id}/cells/{cell['row_index']}/{cell['col_index']}/placement"


def box_body(x, highlight):
    return {"x": x, "y": 0.0, "w": THIRD, "h": 1.0, "highlight": highlight}


def wait_transcoded(auth, media_id, timeout_s=180):
    """The media row once transcode_video has swapped in the .opt.mp4. The
    same UPDATE stores the transcoded file's probed size and length."""
    deadline = time.monotonic() + timeout_s
    row = None
    while time.monotonic() < deadline:
        row = next((m for m in api("GET", "/media", auth) if m["id"] == media_id), None)
        if row and row["filename"].endswith(".opt.mp4"):
            return row
        time.sleep(1)
    raise AssertionError(f"media {media_id} was never transcoded: {row!r}")


def wait_playing(page, n, timeout_s=40):
    page.wait_for_selector("#pairing", state="hidden", timeout=timeout_s * 1000)
    return wait_eval(page, PLAYING_JS, lambda v: bool(v) and v["ready"] >= 3 and not v["paused"],
                     timeout_s, f"TV {n} to play the wall video")


def timed_move(page, auth, wall_id, tv, x, canvas, timeout_s=7.0):
    """PUTs the TV's box to x, highlight on (as the phone does while
    dragging), and returns the seconds until the TV's transform shows it.
    Waits past the tick loop's 5 s fallback, so a slow move is measured
    rather than lost."""
    want = expected_matrix({"x": x, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(x, True))
    while True:
        got = page.evaluate(TRANSFORM_JS)
        dt = time.monotonic() - t0
        if matrix_close(parse_matrix(got), want):
            return dt
        if dt > timeout_s:
            raise AssertionError(f"TV {tv['n']} never moved to x={x:.4f}: "
                                 f"transform {got}, expected {want}")
        page.wait_for_timeout(50)


def timed_colour_move(page, auth, wall_id, tv, x, band, canvas, path, timeout_s=7.0):
    """Like timed_move, onto a box over another band: PUTs the TV's box to x
    (highlight on) and returns (seconds until the transform shows it, seconds
    until a screenshot of the TV is mostly band, that share). So the picture
    itself is checked, not only the matrix. The screenshots are at CSS scale
    (960x540 for TV 2), which is quicker; each one replaces path."""
    want = expected_matrix({"x": x, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
    t_matrix, got, share = None, None, 0.0
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(x, True))
    while True:
        if t_matrix is None and matrix_close(parse_matrix(page.evaluate(TRANSFORM_JS)), want):
            t_matrix = time.monotonic() - t0
        if t_matrix is not None:
            page.screenshot(path=path, scale="css")
            got, share = dominant_color(path)
            # b.'s 75%. The outline and "TV n" are up while placing; with them
            # a blue third still measured 92% (96% without).
            if got == band and share >= 0.75:
                return t_matrix, time.monotonic() - t0, share
        if time.monotonic() - t0 > timeout_s:
            moved = "never" if t_matrix is None else f"after {t_matrix:.2f} s"
            raise AssertionError(f"TV {tv['n']} never showed {band} at x={x:.4f} within {timeout_s} s: "
                                 f"transform {moved}, picture {got} ({share:.0%})")
        page.wait_for_timeout(50)


def api_wall_checks(auth, media, tvs, timings, t_upload):
    tv1, tv2, tv3 = tvs

    # 1. TV 1's QR
    code1 = wait_for_code(tv1["page"])
    tv1["page"].screenshot(path=f"{OUT}/wall-tv1-pairing.png")
    qr = decode_qr(tv1["page"].locator("#pairing-qr"), "wall-tv1-qr.png")
    assert qr == f"{APP_URL}/pair?code={code1}", (qr, code1)
    print(f"1. TV 1 shows {code1}; its QR decodes to {qr}")

    # 2. The transcode caps the 5760x1080 upload at 3840 px.
    row = wait_transcoded(auth, media["id"])
    timings["upload_to_transcoded_s"] = round(time.monotonic() - t_upload, 1)
    assert (row["width_px"], row["height_px"]) == (3840, 720), row
    assert abs((row["duration_seconds"] or 0) - 20.0) < 0.2, row
    print(f"2. upload transcoded to {row['width_px']}x{row['height_px']}, "
          f"{row['duration_seconds']:.2f} s, {timings['upload_to_transcoded_s']} s after the upload")

    # 3. The wall, TV by TV.
    tv1["code"] = code1
    t0 = time.monotonic()
    wall = api("POST", "/walls/free", auth,
               body={"name": "E2E wall", "media_id": media["id"], "code": code1})
    wall_id = wall["id"]
    assert wall["layout"] == "free" and wall["mode"] == "spanned", wall
    canvas = (wall["canvas_width_px"], wall["canvas_height_px"])
    assert canvas == (3840, 720), canvas
    tv1["cell"] = next(c for c in wall["cells"] if c["col_index"] == 0)
    wait_playing(tv1["page"], 1)
    timings["tv1_claim_to_playing_s"] = round(time.monotonic() - t0, 1)
    for tv in (tv2, tv3):
        tv["code"] = wait_for_code(tv["page"])
        t0 = time.monotonic()
        tv["cell"] = api("POST", f"/walls/{wall_id}/tvs", auth, body={"code": tv["code"]})
        assert tv["cell"]["col_index"] == tv["n"] - 1, tv["cell"]
        wait_playing(tv["page"], tv["n"])
        timings[f"tv{tv['n']}_claim_to_playing_s"] = round(time.monotonic() - t0, 1)
    t_all_playing = time.monotonic()
    for tv in tvs:
        src = tv["page"].evaluate(PLAYING_JS)["src"]
        assert src == f"{API}/uploads/{row['filename']}", (tv["n"], src)
    print(f"3. wall {wall_id}: TV 1 joined by POST /walls/free, TV 2 and TV 3 by /tvs; "
          f"claim to playing {timings['tv1_claim_to_playing_s']} / "
          f"{timings['tv2_claim_to_playing_s']} / {timings['tv3_claim_to_playing_s']} s")

    # b. Crop: boxes on the red, green and blue thirds.
    for tv in tvs:
        api("PUT", placement_path(wall_id, tv["cell"]), auth, body=box_body(tv["x"], False))
    shots = []
    for tv in tvs:
        want = expected_matrix({"x": tv["x"], "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
        got = wait_eval(tv["page"], TRANSFORM_JS,
                        lambda v, want=want: matrix_close(parse_matrix(v), want),
                        6, f"TV {tv['n']}'s crop matrix{tuple(round(v, 3) for v in want)}")
        tv["page"].wait_for_timeout(300)
        path = f"{OUT}/wall-tv{tv['n']}.png"
        tv["page"].screenshot(path=path)
        shots.append(path)
        band, share = dominant_color(path)
        centre = dark_share(path, (0.35, 0.25, 0.65, 0.75))
        edges = max(dark_share(path, (0.0, 0.0, 0.2, 1.0)), dark_share(path, (0.0, 0.85, 1.0, 1.0)))
        assert band == tv["band"] and share >= 0.75, \
            f"TV {tv['n']} shows mostly {band} ({share:.0%}), expected {tv['band']}"
        assert centre >= 0.08, f"TV {tv['n']}: no digit in the middle (dark share {centre:.2f})"
        assert edges <= 0.02, \
            f"TV {tv['n']}: black at the edges ({edges:.2f}), so the slice does not fill the screen"
        print(f"b. TV {tv['n']} ({tv['size'][0]}x{tv['size'][1]} @{tv['scale']}x): {got}; "
              f"{share:.0%} {band}, digit in the middle")
    save_montage(shots, f"{OUT}/wall-montage.png")
    want_sizes = {tv["cell"]["col_index"]: tv["size"] for tv in tvs}
    deadline = time.monotonic() + 10
    while True:
        cells = {c["col_index"]: c for c in api("GET", f"/walls/{wall_id}", auth)["cells"]}
        got_sizes = {k: (cells[k]["screen_w_px"], cells[k]["screen_h_px"])
                     for k in want_sizes if k in cells}
        if got_sizes == want_sizes:
            break
        assert time.monotonic() < deadline, \
            f"screen sizes the TVs reported: {got_sizes}, expected {want_sizes}"
        time.sleep(0.5)
    print(f"b. reported screen sizes (CSS px) by cell: {got_sizes}")
    # b. Decision 6: a box may hang off the video while 5% of it overlaps, and
    # the TV shows black where it is off. TV 1's box, half off the left edge:
    half_off = {"x": -THIRD / 2, "y": 0.0, "w": THIRD, "h": 1.0}
    api("PUT", placement_path(wall_id, tv1["cell"]), auth, body=dict(half_off, highlight=False))
    want_off = expected_matrix(half_off, canvas, tv1["size"])
    wait_eval(tv1["page"], TRANSFORM_JS, lambda v: matrix_close(parse_matrix(v), want_off), 6,
              f"TV 1's half-off crop matrix{tuple(round(v, 3) for v in want_off)}")
    tv1["page"].wait_for_timeout(300)
    half_path = f"{OUT}/wall-tv1-half-off.png"
    tv1["page"].screenshot(path=half_path)
    off = dark_share(half_path, (0.0, 0.0, 0.45, 1.0))
    assert off >= 0.95, f"TV 1: the part of its box off the video is not black ({off:.2f})"
    api("PUT", placement_path(wall_id, tv1["cell"]), auth, body=box_body(0.0, False))
    want_back = expected_matrix({"x": 0.0, "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv1["size"])
    wait_eval(tv1["page"], TRANSFORM_JS, lambda v: matrix_close(parse_matrix(v), want_back), 6,
              "TV 1's crop back on the red third")
    print(f"b. TV 1 with half its box off the video: {off:.0%} black on that side")

    # c. Live move: five placement PUTs for TV 2, one onto blue, then back onto green.
    moves = []
    for i in range(5):
        x = THIRD + (0.05 if i % 2 == 0 else -0.05)
        moves.append(timed_move(tv2["page"], auth, wall_id, tv2, x, canvas))
    median_move = sorted(moves)[len(moves) // 2]
    timings["live_moves_s"] = [round(m, 3) for m in moves]
    timings["live_move_median_s"] = round(median_move, 3)
    assert max(moves) < 1.0, \
        f"a move took {max(moves):.2f} s to reach TV 2 ({timings['live_moves_s']}): " \
        "the Redis path did not deliver it, the tick loop's fallback did"
    print(f"c. live move: PUT to TV 2's transform, median {median_move * 1000:.0f} ms "
          f"(all: {[round(m * 1000) for m in moves]} ms)")
    # Those moves keep TV 2 on green. Onto the blue third the picture itself
    # must change too, not only the matrix.
    t_matrix, t_colour, share = timed_colour_move(
        tv2["page"], auth, wall_id, tv2, 2 * THIRD, "blue", canvas, f"{OUT}/wall-tv2-moved-blue.png")
    timings["live_colour_move_s"] = round(t_colour, 3)
    assert t_colour < 1.0, \
        f"TV 2's picture turned blue {t_colour:.2f} s after its box moved there (transform after " \
        f"{t_matrix:.2f} s): the Redis path did not deliver it, or the picture did not repaint"
    print(f"c. live move onto blue: TV 2's transform after {t_matrix * 1000:.0f} ms, "
          f"picture {share:.0%} blue after {t_colour * 1000:.0f} ms")
    timed_move(tv2["page"], auth, wall_id, tv2, THIRD, canvas)

    # d. Highlight: on since the moves (highlight=true), off within 1 s of the
    # highlight=false PUT (the phone's Done), which rides the moves' Redis path.
    hl = wait_eval(tv2["page"], HIGHLIGHT_JS, lambda v: bool(v) and v["shown"], 3,
                   "TV 2's highlight")
    assert hl["label"] == "TV 2", hl
    for tv in (tv1, tv3):
        other = tv["page"].evaluate(HIGHLIGHT_JS)
        assert other is not None and not other["shown"], (tv["n"], other)
    tv2["page"].screenshot(path=f"{OUT}/wall-tv2-highlight.png")
    t0 = time.monotonic()
    api("PUT", placement_path(wall_id, tv2["cell"]), auth, body=box_body(THIRD, False))
    wait_eval(tv2["page"], HIGHLIGHT_JS, lambda v: bool(v) and not v["shown"], 6,
              "TV 2's highlight to go")
    off_s = time.monotonic() - t0
    timings["highlight_off_s"] = round(off_s, 2)
    # The 6 s wait is only a deadline, so a slow value is measured, not lost.
    assert off_s < 1.0, \
        f"TV 2's highlight went {off_s:.2f} s after highlight=false (bound 1 s): " \
        "the Redis path did not deliver it, the tick loop's fallback did"
    print(f"d. highlight '{hl['label']}' on TV 2 only; gone {timings['highlight_off_s']} s "
          "after highlight=false")

    # e. Sync, despite the shifted clocks.
    for tv in tvs:
        skew = tv["page"].evaluate(SKEW_READ_JS)
        assert abs(skew - tv["skew_ms"]) < 250, (tv["n"], skew, tv["skew_ms"])
    rest_s = 6.0 - (time.monotonic() - t_all_playing)
    if rest_s > 0:
        tv1["page"].wait_for_timeout(int(rest_s * 1000))
    median_ms, spreads = sample_sync_spread_ms([tv["page"] for tv in tvs])
    timings["sync_median_ms"] = round(median_ms, 1)
    assert median_ms < 100, f"currentTime spread: median {median_ms:.0f} ms of {spreads}"
    print(f"e. sync: median spread {median_ms:.0f} ms over {len(spreads)} samples "
          f"(max {spreads[-1]:.0f} ms); TV clocks shifted -3000 / 0 / +2000 ms")

    # f. Remove TV 3: it goes back to a fresh pairing code.
    t0 = time.monotonic()
    api("DELETE", f"/walls/{wall_id}/cells/{tv3['cell']['row_index']}/"
                  f"{tv3['cell']['col_index']}/pairing", auth)
    new_code = wait_for_code(tv3["page"], previous=tv3["code"], timeout_s=30)
    timings["unpair_to_new_code_s"] = round(time.monotonic() - t0, 1)
    tv3["page"].screenshot(path=f"{OUT}/wall-tv3-unpaired.png")
    left = api("GET", f"/walls/{wall_id}", auth)
    assert sorted(c["col_index"] for c in left["cells"]) == [0, 1] and left["cols"] == 2, left
    for tv in (tv1, tv2):
        assert tv["page"].evaluate(MODE_JS) == "wall", (tv["n"], tv["page"].evaluate(MODE_JS))
    print(f"f. TV 3 removed: shows {new_code} {timings['unpair_to_new_code_s']} s later; "
          "the wall keeps TV 1 and TV 2")

    # a. Nothing reached production.
    for tv in tvs:
        assert tv["ws"], f"TV {tv['n']} opened no WebSocket"
        stray = [u for u in tv["ws"] if not u.startswith(f"{WS_PREFIX}{wall_id}/ws?")]
        assert not stray, (tv["n"], stray)
    assert not BLOCKED, f"pages asked for khanshoof.com or production's loopback ports (refused): {BLOCKED}"
    print(f"a. {sum(len(tv['ws']) for tv in tvs)} TV sockets, all to {WS_PREFIX}{wall_id}/ws; "
          "no request to khanshoof.com or production's loopback ports")


def api_wall(browser, auth):
    timings = {}
    # Upload first: the transcode (about 10 s) runs while the TVs start.
    with open(VIDEO, "rb") as f:
        raw, ctype = multipart("file", "wall-test.mp4", f.read(), "video/mp4")
    t_upload = time.monotonic()
    media = api("POST", "/media/upload", auth, raw=raw, content_type=ctype)

    tvs = []
    try:
        for spec in TVS:
            ctx = lan_context(browser, size=spec["size"], scale=spec["scale"],
                              skew_ms=spec["skew_ms"])
            tv = dict(spec, ctx=ctx, page=ctx.new_page(), ws=[])
            tv["page"].on("websocket", lambda w, urls=tv["ws"]: urls.append(w.url))
            tvs.append(tv)
            tv["page"].goto(LAN_PLAY + "/")
        api_wall_checks(auth, media, tvs, timings, t_upload)
    except Exception:
        for tv in tvs:
            try:
                tv["page"].screenshot(path=f"{OUT}/wall-fail-tv{tv['n']}.png")
            except Exception:
                pass
        raise
    finally:
        for tv in tvs:
            tv["ctx"].close()
    return timings


def main():
    os.makedirs(OUT, exist_ok=True)
    auth = api("POST", "/auth/login", body={"username": USER, "password": PASSWORD})["token"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        timings = api_wall(browser, auth)
        browser.close()
    print("timings:", json.dumps(timings))
    print("E2E WALL OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add the `--wall` mode: replace `scripts/e2e-scan-to-pair/run.sh` with this whole file**

Only the header comment (lines 8–12) and the inserted `--wall` block are new. Everything from
`backend_image=...` to the end is the current lines 16–59, unchanged.

```bash
#!/usr/bin/env bash
# End-to-end scan-to-pair test: a TV (the player) and a phone (the dashboard)
# pair against a throwaway backend with its own Postgres and Redis. The player
# and dashboard are built from this working tree; the backend is the current
# image (BACKEND_IMAGE to override). Nothing touches production: the browsers'
# khanshoof.com requests are answered by these containers.
#
#   scripts/e2e-scan-to-pair/run.sh            screenshots in scripts/e2e-scan-to-pair/shots/
#   scripts/e2e-scan-to-pair/run.sh --wall     three TVs on a video wall (e2e_wall.py; see run_wall)
#
# Needs Docker and the signage_renderer image (Playwright). Containers are named
# e2e-pair-* (e2e-wall-* with --wall) and removed by name when the script ends.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

# ---- --wall ------------------------------------------------------------------
# Three TVs on one video wall, set up through the API (e2e_wall.py). Loopback
# mode: the TVs load the player from http://127.0.0.1:13022, a player container
# whose API_BASE_URL is the local API (127.0.0.1:18020). Playwright's routing
# never sees WebSockets, so a TV loaded as https://play.khanshoof.com would open
# its wall socket to PRODUCTION.
#
# The backend is always built from this tree as signage_backend:e2e;
# BACKEND_IMAGE is ignored here (the run tests this branch's walls, and
# :latest is production's tag, never retagged). WEB_CONCURRENCY=4, as in
# production, so a placement PUT and a TV's socket usually land on different
# workers and the move travels through Redis. Names, network and ports differ
# from the pair run's, so the two can run side by side.
#
#   PLAYER_DIR         player to build (default: this tree's player/)
#   E2E_WALL_REPLACE=1 remove leftover e2e-wall-* containers (only your own killed run's)
wall_containers=(e2e-wall-api e2e-wall-db e2e-wall-redis e2e-wall-player e2e-wall-ffmpeg)

cleanup_wall() {
  docker rm -f "${wall_containers[@]}" > /dev/null 2>&1 || true
  docker network rm e2e-wall-net > /dev/null 2>&1 || true
}

run_wall() {
  local player_dir="${PLAYER_DIR:-$repo/player}"
  # Another session's wall run uses these names and ports, and its containers
  # come up over minutes: db and redis first, the API only after the image
  # builds and the test video. Removing any of them would break that run
  # halfway, so every e2e-wall-* name of the stack (not only e2e-wall-api) is
  # checked before the trap is set.
  local busy
  busy="$(docker ps -a --format '{{.Names}}' \
    | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg)$' | tr '\n' ' ' || true)"
  if [[ -n "$busy" && "${E2E_WALL_REPLACE:-0}" != 1 ]]; then
    echo "e2e-wall containers exist: another wall run is in progress, or a killed one left them:" >&2
    # shellcheck disable=SC2086  # $busy is a list of container names
    docker inspect -f '  {{.Name}}  created {{.Created}}  {{.State.Status}}' $busy >&2 || true
    echo "Wait for it. E2E_WALL_REPLACE=1 removes them: use it only for your own killed run's" >&2
    echo "leftovers (no container runs e2e_wall.py, and they were created when that run started)." >&2
    exit 1
  fi
  trap cleanup_wall EXIT
  cleanup_wall

  echo "Starting a throwaway wall stack…"
  docker network create e2e-wall-net > /dev/null
  # 4 workers x (THREADPOOL_SIZE 40 + 1) = 164 connections, over Postgres's
  # default of 100 (budget in backend/db.py; docker-compose.yml uses 200 too).
  docker run -d --name e2e-wall-db --network e2e-wall-net --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=e2e -e POSTGRES_PASSWORD=e2e -e POSTGRES_DB=e2e postgres:16-alpine \
    -c max_connections=200 > /dev/null
  docker run -d --name e2e-wall-redis --network e2e-wall-net redis:7-alpine > /dev/null

  echo "Building signage_backend:e2e and signage_player:e2e-wall…"
  docker build -q -t signage_backend:e2e "$repo/backend" > /dev/null
  docker build -q -t signage_player:e2e-wall "$player_dir" > /dev/null

  # The 20 s 5760x1080 test video: red, green and blue thirds with digits
  # 1, 2, 3 and a white bar sweeping across. Made once (shots/ is gitignored),
  # with the ffmpeg in the backend image; drawbox has no time variable, so the
  # bar is an overlay. Written under a temporary name, so an interrupted run
  # never leaves a truncated video behind.
  mkdir -p "$here/shots"
  if [[ ! -s "$here/shots/wall-test.mp4" ]]; then
    echo "Making the test video…"
    local digit="drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:fontsize=600:fontcolor=black:x=(w-tw)/2:y=(h-th)/2:text="
    docker run --rm --name e2e-wall-ffmpeg --network none --user "$(id -u):$(id -g)" \
      -v "$here/shots":/out --entrypoint ffmpeg signage_backend:e2e -hide_banner -loglevel error -y \
      -f lavfi -i "color=c=0xFF0000:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0x00FF00:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0x0000FF:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0xFFFFFF:s=96x1080:r=30:d=20" \
      -filter_complex "[0]${digit}1[a];[1]${digit}2[b];[2]${digit}3[c];[a][b][c]hstack=inputs=3[bg];[bg][3]overlay=x='mod(t*288\,5760)':y=0:shortest=1,format=yuv420p[v]" \
      -map "[v]" -t 20 -c:v libx264 -preset veryfast -crf 23 -profile:v high -movflags +faststart \
      /out/wall-test.tmp.mp4
    mv "$here/shots/wall-test.tmp.mp4" "$here/shots/wall-test.mp4"
  fi

  for _ in $(seq 1 30); do docker exec e2e-wall-db pg_isready -U e2e -d e2e > /dev/null 2>&1 && break; sleep 1; done
  docker run -d --name e2e-wall-api --network e2e-wall-net -p 127.0.0.1:18020:8000 --tmpfs /app/uploads --tmpfs /app/data \
    -e DATABASE_URL=postgresql://e2e:e2e@e2e-wall-db:5432/e2e -e REDIS_URL=redis://e2e-wall-redis:6379/0 \
    -e ADMIN_USERNAME=demo -e ADMIN_PASSWORD=Khanshoof-Demo-2026 -e SECRET_KEY="e2e-$RANDOM-$RANDOM" \
    -e ALLOWED_ORIGINS=https://app.khanshoof.com,https://play.khanshoof.com,http://127.0.0.1:13022,http://127.0.0.1:13023 \
    -e APP_URL=https://app.khanshoof.com -e API_BASE_URL=http://127.0.0.1:18020 \
    -e PLAYER_BASE_URL=http://127.0.0.1:13022 -e UPLOAD_DIR=/app/uploads -e DATA_DIR=/app/data \
    -e RATE_LIMITS_ENABLED=false -e WEB_CONCURRENCY=4 signage_backend:e2e > /dev/null
  # APP_URL stays production's dashboard: the TV's QR still reads
  # https://app.khanshoof.com/pair?code=..., which the test only decodes.
  docker run -d --name e2e-wall-player -p 127.0.0.1:13022:80 -e PLAYER_VERSION=e2e-wall \
    -e API_BASE_URL=http://127.0.0.1:18020 -e APP_URL=https://app.khanshoof.com \
    signage_player:e2e-wall > /dev/null

  local up=0
  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:18020/health > /dev/null 2>&1 \
        && curl -fsS -o /dev/null http://127.0.0.1:13022/ > /dev/null 2>&1; then up=1; break; fi
    sleep 1
  done
  if [[ "$up" != 1 ]]; then
    echo "The wall stack never came up. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    exit 1
  fi

  # QR decoder and PIL for the test, installed once into .pylib (gitignored).
  if [[ ! -d "$here/.pylib/zxingcpp.libs" && ! -f "$here/.pylib/zxingcpp.cpython-312-x86_64-linux-gnu.so" ]]; then
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$here":/w --entrypoint pip signage_renderer:latest \
      install -q --target /w/.pylib zxing-cpp==2.3.0 pillow==11.3.0
  fi

  echo "Three TVs on a video wall…"
  if ! docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
      -e E2E_API=http://127.0.0.1:18020 -e E2E_PLAY_LAN=http://127.0.0.1:13022 \
      --entrypoint python signage_renderer:latest -I e2e_wall.py; then
    echo "e2e_wall.py failed. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    exit 1
  fi
}

if [[ "${1:-}" == "--wall" ]]; then
  run_wall
  exit 0
fi

backend_image="${BACKEND_IMAGE:-signage_backend:latest}"
containers=(e2e-pair-api e2e-pair-db e2e-pair-redis e2e-pair-player e2e-pair-app)

cleanup() {
  docker rm -f "${containers[@]}" > /dev/null 2>&1 || true
  docker network rm e2e-pair-net > /dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

echo "Starting a throwaway stack…"
docker network create e2e-pair-net > /dev/null
docker run -d --name e2e-pair-db --network e2e-pair-net --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=e2e -e POSTGRES_PASSWORD=e2e -e POSTGRES_DB=e2e postgres:16-alpine > /dev/null
docker run -d --name e2e-pair-redis --network e2e-pair-net redis:7-alpine > /dev/null
for _ in $(seq 1 30); do docker exec e2e-pair-db pg_isready -U e2e -d e2e > /dev/null 2>&1 && break; sleep 1; done

docker run -d --name e2e-pair-api --network e2e-pair-net -p 127.0.0.1:18000:8000 --tmpfs /app/uploads --tmpfs /app/data \
  -e DATABASE_URL=postgresql://e2e:e2e@e2e-pair-db:5432/e2e -e REDIS_URL=redis://e2e-pair-redis:6379/0 \
  -e ADMIN_USERNAME=demo -e ADMIN_PASSWORD=Khanshoof-Demo-2026 -e SECRET_KEY="e2e-$RANDOM-$RANDOM" \
  -e ALLOWED_ORIGINS=https://app.khanshoof.com,https://play.khanshoof.com \
  -e APP_URL=https://app.khanshoof.com -e API_BASE_URL=https://api.khanshoof.com \
  -e PLAYER_BASE_URL=https://play.khanshoof.com -e UPLOAD_DIR=/app/uploads -e DATA_DIR=/app/data \
  -e RATE_LIMITS_ENABLED=false -e WEB_CONCURRENCY=1 "$backend_image" > /dev/null

docker build -q -t signage_player:e2e "$repo/player" > /dev/null
docker build -q -t signage_frontend:e2e "$repo/frontend" > /dev/null
docker run -d --name e2e-pair-player -p 127.0.0.1:13001:80 -e PLAYER_VERSION=e2e \
  -e API_BASE_URL=https://api.khanshoof.com -e APP_URL=https://app.khanshoof.com signage_player:e2e > /dev/null
docker run -d --name e2e-pair-app -p 127.0.0.1:13000:80 -e API_BASE_URL=https://api.khanshoof.com \
  -e PLAYER_BASE_URL=https://play.khanshoof.com -e WALLS_PHASE2_ENABLED=true signage_frontend:e2e > /dev/null

for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:18000/health > /dev/null 2>&1 && break; sleep 1; done

# QR decoder for the test, installed once into .pylib (gitignored).
if [[ ! -d "$here/.pylib/zxingcpp.libs" && ! -f "$here/.pylib/zxingcpp.cpython-312-x86_64-linux-gnu.so" ]]; then
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$here":/w --entrypoint pip signage_renderer:latest \
    install -q --target /w/.pylib zxing-cpp==2.3.0 pillow==11.3.0
fi

mkdir -p "$here/shots"
echo "Pairing a TV with a phone…"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
  --entrypoint python signage_renderer:latest -I e2e_pair.py
```

- [ ] **Step 4: Check syntax and that the pair run is untouched**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
bash -n scripts/e2e-scan-to-pair/run.sh && echo "run.sh syntax OK"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('e2e_wall.py parses')" scripts/e2e-scan-to-pair/e2e_wall.py
diff <(git show HEAD:scripts/e2e-scan-to-pair/run.sh | sed -n '/^backend_image=/,$p') \
     <(sed -n '/^backend_image=/,$p' scripts/e2e-scan-to-pair/run.sh) && echo "pair path byte-identical"
test -x scripts/e2e-scan-to-pair/run.sh && echo "still executable"
```

Expected: `run.sh syntax OK`, `e2e_wall.py parses`, `pair path byte-identical`, `still executable`.
(`ast.parse` is used instead of `py_compile`, so no `__pycache__` lands in the harness directory.)

- [ ] **Step 5: Run it against the player from before Task 8 and watch it fail**

Tasks 1–7's backend is in place, so a red run needs the old player. `fix/player-cache-busting` is the
base branch, so its `player/` is the player as it was before Task 8.

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
rm -rf "$S/player-before-task8" && mkdir -p "$S/player-before-task8"
git -C "$WT" archive fix/player-cache-busting player | tar -x -C "$S/player-before-task8"
cd "$WT" && PLAYER_DIR="$S/player-before-task8/player" scripts/e2e-scan-to-pair/run.sh --wall 2>&1 \
  | tee "$S/e2e-wall-red.log"; echo "exit ${PIPESTATUS[0]}"
```

Run it with the Bash tool's timeout set to 600000 ms, or with `run_in_background`, and wait for the
`exit` line: the default 2-minute timeout would kill it. A run killed before it prints `exit` can skip
its trap (a SIGKILL always does). If its `e2e-wall-*` containers are still there, they are your own
leftovers: wait until Step 1's check prints `no e2e_wall.py runner` (the test container can outlive a
killed client), then rerun once with `E2E_WALL_REPLACE=1` in front of `PLAYER_DIR=...`.

Expected: FAIL (`exit 1`) after about 1–3 minutes. The first run also prints `Making the test video…`.
Checks `1.`, `2.` and `3.` pass, because the old player already handles spanned walls. The traceback
then ends with:

`AssertionError: timed out after 6 s waiting for TV 1's crop matrix(1.5, 0.0, 0.0, 1.5, 0.0, 0.0); last value: 'matrix(1, 0, 0, 1, 0, 0)'`

followed by `e2e_wall.py failed. e2e-wall-api, last 80 log lines:`. The old player only translates the
canvas, so TV 1 shows canvas x 0–1920 at 1:1, with black below y = 720 (see
`scripts/e2e-scan-to-pair/shots/wall-fail-tv1.png`). Afterwards
`docker ps -a --format '{{.Names}}' | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg)$'` prints
nothing (the trap removed the stack).

- [ ] **Step 6: Run it against this branch and watch it pass**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
rm -f "$WT"/scripts/e2e-scan-to-pair/shots/wall-fail-tv*.png   # Step 5's red-run screenshots
cd "$WT" && scripts/e2e-scan-to-pair/run.sh --wall 2>&1 | tee "$S/e2e-wall.log"; echo "exit ${PIPESTATUS[0]}"
```

Run it like Step 5: with the Bash tool's timeout set to 600000 ms, or with `run_in_background`, then
wait for the `exit` line. If the tool killed it and its containers are still there, they are yours:
wait for `no e2e_wall.py runner` as in Step 1, then rerun once with `E2E_WALL_REPLACE=1` in front of
`scripts/e2e-scan-to-pair/run.sh`.

Expected: PASS (`exit 0`) in about 1–3 minutes. The output has this shape (codes, ids and times vary):

```text
Starting a throwaway wall stack…
Building signage_backend:e2e and signage_player:e2e-wall…
Three TVs on a video wall…
1. TV 1 shows K7MP2Q; its QR decodes to https://app.khanshoof.com/pair?code=K7MP2Q
2. upload transcoded to 3840x720, 20.00 s, 1x.x s after the upload
3. wall 1: TV 1 joined by POST /walls/free, TV 2 and TV 3 by /tvs; claim to playing x.x / x.x / x.x s
b. TV 1 (1920x1080 @1x): matrix(1.5, 0, 0, 1.5, 0, 0); 9x% red, digit in the middle
b. TV 2 (960x540 @2x): matrix(0.75, 0, 0, 0.75, -960, 0); 9x% green, digit in the middle
b. TV 3 (1920x1080 @1x): matrix(1.5, 0, 0, 1.5, -3840, 0); 9x% blue, digit in the middle
b. reported screen sizes (CSS px) by cell: {0: (1920, 1080), 1: (960, 540), 2: (1920, 1080)}
b. TV 1 with half its box off the video: 95–100% black on that side
c. live move: PUT to TV 2's transform, median ... ms (all: [...] ms)
c. live move onto blue: TV 2's transform after ... ms, picture 8x-9x% blue after ... ms
d. highlight 'TV 2' on TV 2 only; gone 0.xx s after highlight=false
e. sync: median spread ... ms over 12 samples (max ... ms); TV clocks shifted -3000 / 0 / +2000 ms
f. TV 3 removed: shows R4WZ8N ... s later; the wall keeps TV 1 and TV 2
a. 3 TV sockets, all to ws://127.0.0.1:18020/walls/1/ws; no request to khanshoof.com or production's loopback ports
timings: {...}
E2E WALL OK
exit 0
```

The `a.` socket count can be higher than 3 if a TV reconnected. The blue share on TV 2 is 87–92% when
the sweeping white bar is in its view (the check needs 75%). Each of the five live moves, the move
onto blue (`live_colour_move_s`) and the highlight going off (`highlight_off_s`) take under 1 s, and the
sync median is under 100 ms. The screenshots are `shots/wall-tv1-pairing.png`, `wall-tv1-qr.png`,
`wall-tv1.png`, `wall-tv2.png`, `wall-tv3.png`, `wall-montage.png`, `wall-tv1-half-off.png`,
`wall-tv2-moved-blue.png`, `wall-tv2-highlight.png` and `wall-tv3-unpaired.png`. `wall-tv1-half-off.png`
is black on its left half and red on its right. Open `wall-montage.png` and check that it reads
red 1 | green 2 | blue 3, and `wall-tv2-moved-blue.png` that it shows blue with the digit 3 behind
"TV 2". If a check fails, `wall-fail-tv<n>.png` and the API log tail say why. The bounds are fixed
(R10, R11): when one fails or flakes, investigate and fix the code under test in its own task. Never
relax a threshold here, and never rerun until it passes without reporting the failures.

- [ ] **Step 7: Run the default pair flow, which must still pass unchanged, then on this branch's backend**

The pair run removes the `e2e-pair-*` names when it starts and has no guard of its own, so each command
below first stops if another pair run holds them (R23). Run the two commands one after the other, each
as its own Bash call with the timeout set to 600000 ms (or with `run_in_background`, waiting for its
`exit` line).

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT"
if docker ps -a --format '{{.Names}}' | grep -E '^e2e-pair-(api|db|redis|player|app)$' \
   || docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py \
   || ss -ltn | grep -E ':(18000|13000|13001)\b'; then
  echo "BUSY: another pair run is up; wait for it"; exit 1
fi
echo "no e2e-pair run"
scripts/e2e-scan-to-pair/run.sh 2>&1 | tee "$S/e2e-pair.log" | tail -2; echo "exit ${PIPESTATUS[0]}"
```

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT"
if docker ps -a --format '{{.Names}}' | grep -E '^e2e-pair-(api|db|redis|player|app)$' \
   || docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py \
   || ss -ltn | grep -E ':(18000|13000|13001)\b'; then
  echo "BUSY: another pair run is up; wait for it"; exit 1
fi
echo "no e2e-pair run"
BACKEND_IMAGE=signage_backend:e2e scripts/e2e-scan-to-pair/run.sh 2>&1 | tee "$S/e2e-pair-branch.log" | tail -2; echo "exit ${PIPESTATUS[0]}"
```

Expected, from each command: `no e2e-pair run`, then a `timings: {...}` line, `E2E OK` and `exit 0`.
The first run is the untouched default: production's backend image with this branch's player and the
old in-tree dashboard. The second uses the branch backend that Step 6 built, so it covers Task 4's
binding refactor and D5's pairing limits on the standalone flow. If the tool killed a pair run, its
trap may not have run (a SIGKILL skips it). Once `docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py` prints
nothing and `docker ps -a --format '{{.Names}} {{.CreatedAt}}' | grep '^e2e-pair-'` shows the start
time of that run of yours, remove its leftovers by name, then rerun the command:
`docker rm -f e2e-pair-api e2e-pair-db e2e-pair-redis e2e-pair-player e2e-pair-app; docker network rm e2e-pair-net`.
If the times are not your run's, another session owns them: leave them and ask the user.

- [ ] **Step 8: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add scripts/e2e-scan-to-pair/run.sh scripts/e2e-scan-to-pair/e2e_wall.py
git status --short    # exactly these two files staged; shots/ and .pylib/ stay ignored
git commit -m "test(e2e): three TVs on a free video wall, driven through the API

run.sh --wall builds this branch's backend (signage_backend:e2e, 4
workers) and player, and runs e2e_wall.py in loopback mode. The TVs load
the player from 127.0.0.1 with the local API as API_BASE_URL, because
Playwright never routes WebSockets: a routed play.khanshoof.com TV would
open its wall socket to production. It refuses to start while any
e2e-wall-* container of the stack exists, since another run may own it.

Three TVs (one at DPR 2, clocks shifted -3/0/+2 s) join a free wall
through POST /walls/free and /tvs. The checks: crop matrix and band
colour per TV, black where a box hangs off the video, live placement
under 1 s (Redis path), a move onto the blue third that turns TV 2's
picture blue in under 1 s, highlight on and off (off under 1 s),
currentTime spread under 100 ms, removing a TV brings back its QR, and
no request or socket reaches khanshoof.com or production's loopback
ports. The default pair run is unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

---

### Task 10: PR 1 verification and hand-off

Nothing in this task changes code. It makes sure the plan document is committed (R12), runs every check
on the finished branch, collects the numbers for the PR description, and then **stops and asks the
user** before anything is pushed. Commands use `WT=/home/ahmed/signage/.claude/worktrees/wall-setup` and
`S=/home/ahmed/signage-harness/handoff` (each block creates it). Logs and the PR body go to `$S`,
outside `/tmp`, so they survive a restart while the plan waits for the user's yes, and the PR body can
quote them.

Safety rules for this task:
- Never `docker kill`/`docker rm` by image or ancestor filter (`signage_backend:latest` is production).
- Stop a harness only through its own trap. If `e2e-wall-*` containers exist, wait. Use
  `E2E_WALL_REPLACE=1` only when they are your own run's leftovers:
  `docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -F e2e_wall.py` prints nothing (no
  test runner is alive), and `docker inspect -f '{{.Created}}' e2e-wall-db` matches the start time of a
  run of yours that was interrupted. Otherwise another session owns them: leave them and ask the user.
  The same holds for `e2e-pair-*` (the pair run removes those names when it starts, with no guard) and
  for Task 8's `e2e-wall-geo-*`.
- Each harness run takes minutes. Give each one its own Bash call, with the timeout set to 600000 ms or
  with `run_in_background`, and wait for its exit line. A run the tool kills can skip its trap (a
  SIGKILL always does) and leave its containers behind; those are your own leftovers (see the rule
  above), once no runner of that run is alive.
- Nothing is pushed or opened without the user's explicit yes in this conversation. This task pushes
  PR 1's branch as it is now. Task 16 later adds one more commit to the same branch; pushing that commit
  is part of Task 17's single question, not of this task's (R21).

**Files:**
- Commit (Step 0, only when it is missing or out of date): `docs/superpowers/plans/2026-11-01-phone-wall-setup.md`
  (this plan, R12; Task 1 Step 8 normally committed it already)
- Create: `/home/ahmed/signage-harness/handoff/pr1-body.md`
  (the PR description, outside every repo, not committed)
- Modify: no code
- Test: the full backend suite, `scripts/e2e-scan-to-pair/run.sh` (default and `--wall`), the Chrome 66
  syntax floor, Task 8's player harness (Chromium 151 and 66), and `scripts/check_i18n.py`

**Interfaces:**
- Consumes:
  - Everything Tasks 1–9 committed on `feature/phone-wall-setup`, including the plan document
    (Task 1 Step 8).
  - `scripts/test-backend-in-docker.sh`.
  - `scripts/e2e-scan-to-pair/run.sh [--wall]` (Task 9). Its `timings:` keys `live_moves_s`,
    `live_move_median_s`, `live_colour_move_s`, `highlight_off_s` and `sync_median_ms`.
  - From Task 8, in `/home/ahmed/signage-harness` (outside every repo, so it survives a restart):
    `mockharness/run-wallsetup.sh`, `t_wall_geometry.py`, `cr66_wall_geometry.js` and `mock.py`, plus
    `chrome66/tools/syntax-floor.js` and `chrome66/dl-acorn/package/dist/acorn.js`. Its containers are
    `e2e-wall-geo-py-<pid>`, `e2e-wall-geo-mock` and `e2e-wall-geo-cr66`, on ports 3150, 3151 and 9335.
- Produces: after the user's OK only, the branch `feature/phone-wall-setup` on origin, and a PR with base
  `fix/player-cache-busting` (PR #24's branch; production's player is #22 + #23 + #24). Task 16 later
  commits the phone scenarios onto this branch, locally; Task 17's question covers pushing that (R21).

- [ ] **Step 0: Make sure the plan is committed on this branch (R12)**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
P=docs/superpowers/plans/2026-11-01-phone-wall-setup.md
sed -i -E 's/^- \[x\] \*\*Step/- [ ] **Step/' "$P" 2>/dev/null   # un-tick steps ticked while executing
git ls-files --error-unmatch "$P" && grep -c '^### Task ' "$P"
git status --short -- "$P"; echo "(end of plan status)"
git log --oneline -1 -- "$P"
```

Expected: the path, `17`, `(end of plan status)` with no status line before it, and the plan commit
from Task 1 Step 8 (`docs(walls): implementation plan for phone wall setup`), or Step 0's own
(`docs(walls): the phone wall setup plan, as executed`). Then go on to Step 1.

Otherwise commit it now, before any check and well before Step 10's question:
- **`error: pathspec ... did not match`, or a count other than 17:** the plan is not on the branch.
  Copy the plan file you were handed (all 17 tasks) to
  `docs/superpowers/plans/2026-11-01-phone-wall-setup.md` byte for byte, for example
  `mkdir -p docs/superpowers/plans && cp /tmp/claude-1000/-home-ahmed-signage/39cbf124-cc26-4c98-9af8-19a9e47924c9/scratchpad/plan/plan-assembled.md docs/superpowers/plans/2026-11-01-phone-wall-setup.md`
  when that is the file you were given. Never re-type or summarise it. Then run the block below.
- **A ` M` status line:** the plan's text was corrected while executing (the `sed` above already
  undid ticked boxes). Look at `git diff -- docs/superpowers/plans/2026-11-01-phone-wall-setup.md`; if
  every change is a real correction made on the way (a fixed anchor or command), run the block below.
  If a change is not one you made on purpose, `git restore` that file instead and run this step again.

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
P=docs/superpowers/plans/2026-11-01-phone-wall-setup.md
test "$(grep -c '^### Task ' "$P")" = 17 || { echo "the saved plan is incomplete: save all 17 tasks"; exit 1; }
git add "$P"
git status --short    # exactly the plan staged
git commit -m "docs(walls): the phone wall setup plan, as executed

Seventeen tasks: PR 1 (schema, media probe, layout helpers, free-wall
API, live placement, player, e2e) and PR 2 (the phone's dashboard flow).
Committed at PR 1's verification: the branch lacked this text, or it
was corrected while the plan was executed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git log --oneline -1 -- "$P"
```

Then run this step's first block again: it must print the expected output.

- [ ] **Step 1: Confirm the branch and a clean tree**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current
git status --short
git ls-files --error-unmatch docs/superpowers/plans/2026-11-01-phone-wall-setup.md
git log --oneline fix/player-cache-busting..HEAD
git log --oneline --merges fix/player-cache-busting..HEAD | wc -l
```

Expected: `feature/phone-wall-setup`; empty status; the plan's path; then, newest first, one or more
commits per Task 1–9, the plan commit (Task 1 Step 8, and Step 0's if it made one) and the spec commit
`ca74b28` at the bottom; then `0` (no merge commits).

- [ ] **Step 2: Make sure no other pytest run uses this database**

```bash
docker ps --no-trunc --format '{{.ID}} {{.Names}} {{.Command}}' | grep -F pytest \
  | while read -r id name _; do
      echo "$name $(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id" \
                    | grep '^TEST_DATABASE_URL=' | sed -E 's#^.*/##')"
    done
echo "(end of running pytest containers)"
```

Expected: only `(end of running pytest containers)`, or lines whose last word is another database
(`sawwii_test`, `sawwii_test_walls`, …). If a line ends in `sawwii_test_wallsetup`, another run uses
this database (conftest truncates every table at session start). Wait until it is gone, and never kill
it.

- [ ] **Step 3: Run the full backend suite on the private database**

Run it with the Bash tool's timeout set to 600000 ms, or with `run_in_background` when it may take
longer, and wait for the `exit` line.

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh \
  2>&1 | tee "$S/pytest-full.log" | tail -3; echo "exit ${PIPESTATUS[0]}"
grep -E '^(FAILED|ERROR)' "$S/pytest-full.log" || echo "no FAILED/ERROR lines"
```

Expected: `exit 0`. The last line reads `<N> passed in <T>s` (warnings allowed; no `failed`, no
`error`), followed by `no FAILED/ERROR lines`. The run takes several minutes. Keep the summary line for
the PR body.

- [ ] **Step 4: Run the end-to-end harness: wall, then the pair flow on both backends**

Four commands, each its own Bash call. The three runs take minutes each: give each the 600000 ms
timeout (or `run_in_background`) and wait for its exit line before starting the next. First, check
that nothing else holds the names or ports:

```bash
docker ps -a --format '{{.Names}}  created {{.CreatedAt}}' \
  | grep -E '^(e2e-pair-(api|db|redis|player|app)|e2e-wall-(api|db|redis|player|app|ffmpeg)) ' \
  && echo "BUSY: wait for that run" || echo "no e2e containers"
docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -E 'e2e_(wall|pair)\.py' \
  && echo "BUSY: an e2e runner is alive" || echo "no e2e runner"
(ss -ltn | grep -E ':(18000|13000|13001|18020|13022)\b') && echo "PORT BUSY" || echo "e2e ports free"
```

Expected: `no e2e containers`, `no e2e runner` and `e2e ports free`. On any `BUSY`, wait and check
again (the safety rules say when a leftover is yours). Then the wall run:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
scripts/e2e-scan-to-pair/run.sh --wall 2>&1 | tee "$S/e2e-wall.log" | tail -20; echo "wall exit ${PIPESTATUS[0]}"
```

Expected: check lines `1.` to `a.` (two `c.` lines), a `timings:` line, `E2E WALL OK` and
`wall exit 0`. The five live moves, the move onto blue (`live_colour_move_s`) and `highlight_off_s`
are each under 1 s, and the sync median is under 100 ms. These bounds are fixed (R10, R11): if one
fails or flakes, investigate the code under test (Tasks 6–8) and fix it in its own task, then start
this task again from Step 0. Never relax a bound or rerun until it passes without reporting the
failures. The wall run goes first because it builds `signage_backend:e2e` from the current HEAD.

Then the pair run on production's backend image:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
if docker ps -a --format '{{.Names}}' | grep -E '^e2e-pair-(api|db|redis|player|app)$' \
   || docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py \
   || ss -ltn | grep -E ':(18000|13000|13001)\b'; then
  echo "BUSY: another pair run is up; wait for it"; exit 1
fi
scripts/e2e-scan-to-pair/run.sh 2>&1 | tee "$S/e2e-pair.log" | tail -2; echo "pair exit ${PIPESTATUS[0]}"
```

Expected: a `timings:` line, `E2E OK` and `pair exit 0`. Then the pair run on this branch's backend:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
if docker ps -a --format '{{.Names}}' | grep -E '^e2e-pair-(api|db|redis|player|app)$' \
   || docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py \
   || ss -ltn | grep -E ':(18000|13000|13001)\b'; then
  echo "BUSY: another pair run is up; wait for it"; exit 1
fi
BACKEND_IMAGE=signage_backend:e2e scripts/e2e-scan-to-pair/run.sh 2>&1 | tee "$S/e2e-pair-branch.log" | tail -2; echo "pair (branch backend) exit ${PIPESTATUS[0]}"
```

Expected: a `timings:` line, `E2E OK` and `pair (branch backend) exit 0`.

Keep `$S/e2e-wall.log`. Its `c.`, `d.`, `e.` and `timings:` lines go into the PR body. If the tool
killed a wall run, rerun it once with `E2E_WALL_REPLACE=1` in front, after the checks in the safety
rules. For a killed pair run, Task 9 Step 7 says how to clear its leftovers.

- [ ] **Step 5: Check the Chrome 66 syntax floor and banned constructs**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
C=/home/ahmed/signage-harness/chrome66
node "$C/tools/syntax-floor.js" "$C/dl-acorn/package/dist/acorn.js" player/player.js player/sw.js player/i18n.js \
  | tee "$S/syntax-floor.txt" | grep 'syntax floor'
awk '/syntax floor Chrome/ { if ($NF + 0 > 66) { print "TOO NEW: " $0; bad = 1 } } END { exit bad }' "$S/syntax-floor.txt" \
  && echo "syntax floor OK (Chrome 66 or older)"
echo "--- js"; grep -nE '\?\.|\?\?|\b[0-9]+_[0-9]+\b|flatMap|\.flat\(|globalThis|fromEntries|replaceAll|\.at\(|allSettled' player/player.js
echo "--- css"; grep -nE '\binset\b|(^|[;{ ])gap\s*:|:is\(|:where\(|(^|[;{ ])aspect-ratio\s*:|(^|[;{ ])(scale|translate|rotate)\s*:|focus-visible' player/styles.css
```

Expected:
- **Syntax floor:** `player/player.js: syntax floor Chrome 55`, `player/sw.js: syntax floor Chrome 55`,
  `player/i18n.js: syntax floor Chrome 55`, then `syntax floor OK (Chrome 66 or older)`.
  `syntax-floor.js` always exits 0, so the `awk` line is the gate.
- **After `--- js`:** only the header comment on line 3 (`no ?. or ??, no 1_000 separators`).
- **After `--- css`:** only the lines Task 8 Step 8 lists: the `flexbox gap ... or inset` comment, the
  `no :focus-visible (and` comment, and the two `:focus:not(:focus-visible)` selectors.

If `/home/ahmed/signage-harness/chrome66` is missing, run Task 8 Step 0 again: it copies the harness
from the planning session's scratchpad while that still exists. If both are gone, say so in the hand-off
rather than skipping silently.

- [ ] **Step 6: Re-run Task 8's player checks in Chromium 151 and Chromium 66**

The block stops before starting anything when a container name or a port is taken, so it never stops
another run's mock (R23).

```bash
H=/home/ahmed/signage-harness/mockharness
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
for n in e2e-wall-geo-mock e2e-wall-geo-cr66; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$n"; then echo "$n exists: another run owns it; stop"; exit 1; fi
done
if ss -ltn | grep -E ':(3150|3151|9335)\b'; then echo "PORT BUSY: find out who uses it before going on"; exit 1; fi
echo "names and ports free"
bash "$H/run-wallsetup.sh" t_wall_geometry.py 2>&1 | tee "$S/wall-geometry.log" | tail -3
echo "wall-geometry exit ${PIPESTATUS[0]}"
mkdir -p "$H/shots"
docker run -d --rm --name e2e-wall-geo-mock --network host --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$H:/w" -v "$WT/player:/player:ro" -w /w --entrypoint python signage_renderer:latest /w/mock.py \
  > /dev/null || exit 1
curl -fs --retry 30 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:3150/__mock/log \
  && curl -fs --retry 30 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:3151/index.html \
  || echo "the mock did not answer on 3150/3151"
node "$H/cr66_wall_geometry.js" 2>&1 | tee "$S/cr66-wall.log" | grep -E '^ok: (running|grid cell)|CR66 WALL-GEOMETRY'
rc=${PIPESTATUS[0]}
docker stop e2e-wall-geo-mock > /dev/null
echo "cr66 exit $rc"
```

Expected:
- `names and ports free`.
- The Playwright run (full output in `$S/wall-geometry.log`) ends with `WALL-GEOMETRY OK`, then
  `wall-geometry exit 0`.
- From the Chromium 66 run (full output in `$S/cr66-wall.log`): `ok: running Chrome/66.0.3359.0`,
  `ok: grid cell 0/1: canvas [-1920,0,3840,1080] (was [-960,0,1920,1080])` and
  `CR66 WALL-GEOMETRY OK`, then `cr66 exit 0`. If Node 18 prints an `ExperimentalWarning` about `fetch`,
  it is in the log only and harmless.
- Afterwards `docker ps -a --format '{{.Names}}' | grep e2e-wall-geo-` prints nothing.

If the harness in `/home/ahmed/signage-harness` is missing, run Task 8 Step 0 again (it copies it back
while the planning scratchpad exists), then Task 8 Steps 1, 2 and 9, which recreate `run-wallsetup.sh`,
the test video, `t_wall_geometry.py` and `cr66_wall_geometry.js` there. If Step 0 cannot copy it, say
so in the hand-off rather than skipping silently.

- [ ] **Step 7: Check i18n parity**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup && python3 scripts/check_i18n.py; echo "exit $?"
```

Expected: `i18n OK across frontend, landing, player` and `exit 0`.

- [ ] **Step 8: Review the diff against the PR base**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git diff fix/player-cache-busting...HEAD --stat
git diff --name-only fix/player-cache-busting...HEAD \
  | grep -vE '^(backend/|player/|scripts/e2e-scan-to-pair/|docs/superpowers/)' || echo "only backend/, player/, the harness and docs/superpowers/ changed"
git diff --name-only fix/player-cache-busting...HEAD | grep -E '(^|/)(shots|\.pylib|__pycache__)/|\.pyc$' && echo "STRAY FILES" || echo "no stray files"
git diff --stat fix/player-cache-busting...HEAD -- frontend/ landing/ android-tv/ docker-compose.yml | tail -1
git diff fix/player-cache-busting...HEAD -- backend player scripts | grep -nE '^\+.*(TODO|FIXME|XXX|breakpoint\(\)|print\(f?"DEBUG)' || echo "no TODO/debug lines added"
```

Expected:
- **Changed files:**
  - `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` (the spec, amended in Task 1) and
    `docs/superpowers/plans/2026-11-01-phone-wall-setup.md` (the plan, R12).
  - `backend/db.py`, `backend/media_probe.py`, `backend/wall_layout.py`, `backend/main.py`, `backend/walls.py`.
  - The new tests from Tasks 1–7: `backend/tests/test_walls_free_schema.py`, `test_media_probe.py`,
    `test_media_upload_probe.py`, `test_wall_layout.py`, `test_wall_tv_binding.py`, `test_walls_free.py`
    and `test_walls_live.py`.
  - `player/player.js`, `player/styles.css`, `player/index.html`, `player/i18n/en.json`, `player/i18n/ar.json`.
  - `scripts/e2e-scan-to-pair/run.sh`, `scripts/e2e-scan-to-pair/e2e_wall.py`.
- `only backend/, player/, the harness and docs/superpowers/ changed`.
- `no stray files`.
- An empty line for `frontend/ landing/ android-tv/ docker-compose.yml`: the old dashboard in this
  worktree is never edited, and PR 2 carries the dashboard.
- `no TODO/debug lines added`.

Then read the whole `git diff fix/player-cache-busting...HEAD` once from top to bottom, looking for
leftovers, commented-out code and anything that does not match its task.

- [ ] **Step 9: Write the PR description and fill in the measured values**

````bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cat > "$S/pr1-body.md" <<'EOF'
## Summary

Set up a video wall from a phone, standing in front of the TVs. Every TV shows its usual pairing QR. The phone scans the first one, starts a new "free" wall with a video, and then places each TV's box over that video. The TV shows exactly what is in its box, following the finger live, and all TVs play the video in sync.

This PR is the backend, the player and the end-to-end harness. The phone screens come in PR 2 (`feature/phone-wall-setup-dashboard`, based on `feature/friendly-dashboard`). Once PR 2 exists, one more commit on this branch adds the phone scenarios to `e2e_wall.py`; it is pushed separately.

Spec: `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md`, amended with the plan's decisions. Plan: `docs/superpowers/plans/2026-11-01-phone-wall-setup.md`.

Stacked on #24 (`fix/player-cache-busting`); production's player is #22 + #23 + #24. Merge after #24. The stack merges #24 into #23's branch (`claude/jovial-chaplygin-bf9665`), then #23 into `feature/android-tv`, then #22. Before `fix/player-cache-busting` is deleted, retarget this PR to the branch #24 went into, or GitHub closes it (as it closed #13).

## What's in it

**Backend**
- Additive schema (`ADD COLUMN IF NOT EXISTS`): `walls.layout` ('grid' | 'free'); `wall_cells.place_x/y/w/h`, `screen_w_px/h_px`, `highlight_until_ms`; `media.width_px/height_px/duration_seconds`.
- `media_probe.py`: uploads are probed with ffprobe or Pillow (20 s timeout, never raises). Every video transcode caps the long side at 3840 px (even sizes), so cheap TV decoders can play wall panoramas. The transcoded size is re-probed, and free walls playing that media follow it.
- `wall_layout.py`: pure box maths. The first box, the next box flush to the right, the 5% overlap rule, conversion to canvas px, and fitting a box to a TV's shape.
- The TV binding is extracted from `redeem_wall_cell` into `_bind_screen_to_cell` and its helpers, with no behaviour change, and shared by the new endpoints.
- New endpoints, all admin/editor with an active subscription:
  - `POST /walls/free` (30/minute)
  - `POST /walls/{id}/tvs` (30/minute)
  - `PUT /walls/{id}/cells/{row}/{col}/placement` (204, 1800/minute; once the TV has reported its screen, the stored box takes the TV's shape)
  - `PUT /walls/{id}/video`

  New error codes: `pair.code_unknown`, `pair.code_expired`, `pair.code_used`, `wall.not_free`, `wall.full`, `wall.placement_invalid`, `wall.free_layout`, `wall.media_type_blocked`.
- Free-wall rules:
  - Unpairing a TV deletes its cell, and `cols` follows the highest index left. The wall stays, even with no TVs.
  - `PATCH /walls/{id}` refuses mode, canvas and bezel changes (`wall.free_layout`), and the one-item canvas playlist changes only through `PUT /walls/{id}/video`.
  - Ghost cells (a screen deleted elsewhere) are cleared when the next TV joins.
  - `serialize_wall` adds `online` per cell and `video` for free walls.
- Live placement: a placement is published on Redis channel `khan:wall-geometry:db<N>` to whichever of the 4 workers holds the TV's socket. The tick loop compares each connected cell's box on every pass as a fallback, so without Redis a move takes up to 5 s. New frames: `geometry` (server to TV) and `screen` (TV to server).
- Pairing limits raised for up to 8 TVs behind one venue NAT: poll 240/minute (8 TVs polling every 3 s make 160), request_code 30/minute.
- Standalone playlist defaults are unchanged: videos still default to 10 s.

**Player** (still Chrome 66 safe: syntax floor 55, the `transform` property only, no `inset` or flex `gap`, no new shell files)
- A spanned slice is now scaled to fill the screen: `scale(vw/w, vh/h) translate(-x, -y)` with origin 0 0. Before, a grid cell whose shape differed from the TV showed part of its slice and black. This fixes all spanned walls.
- `geometry` frames re-crop without remounting the media, so the video keeps playing.
- A "TV n" outline shows until `highlight_until_ms`, measured on the server's clock.
- The TV reports its screen size when the socket opens and after resizes.
- The clock offset is now the largest sample of the last 10 minutes (at most 32), timed with `performance.now()`.

**Harness**
- `scripts/e2e-scan-to-pair/run.sh --wall` plus `e2e_wall.py`: three TVs (one at DPR 2, clocks shifted -3 s / 0 / +2 s) join a free wall built through the API, against this branch's backend with 4 workers.
- Loopback mode: the TVs talk to a local API directly, because Playwright does not route WebSockets. The test fails if anything asks for khanshoof.com or for the ports production publishes on the host's loopback (8000, 3000, 3001, 3003).
- `run.sh --wall` refuses to start while any `e2e-wall-*` container of its stack exists, so it never removes another run's containers.
- The default `run.sh` (pair flow) is unchanged.

## Test evidence

- Backend suite (`KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh`): [[PASTE: the last line of pytest-full.log, the "... passed in ...s" summary, exactly as printed]]
- `run.sh --wall`: [[PASTE: the lines from "1." to "E2E WALL OK" from e2e-wall.log, including the "timings:" line]]
  - Live move, placement PUT to TV transform: [[PASTE: the median and the five values from the first "c." line]] (each must be under 1 s)
  - Move onto the blue third, PUT to TV 2's picture turning blue: [[PASTE: the transform and picture times from the "c. live move onto blue" line]] (must be under 1 s)
  - Highlight off after `highlight: false`: [[PASTE: the "highlight_off_s" value from the "timings:" line]] s (must be under 1 s)
  - Sync, currentTime spread across 3 TVs: [[PASTE: the median and max from the "e." line]] (median must be under 100 ms)
- `run.sh`, pair flow on production's backend image: [[PASTE: the "timings:" line and "E2E OK" from e2e-pair.log]]
- `BACKEND_IMAGE=signage_backend:e2e run.sh`, pair flow on this branch's backend: [[PASTE: the "timings:" line and "E2E OK" from e2e-pair-branch.log]]
- Chrome 66 syntax floor: [[PASTE: the three "syntax floor Chrome N" lines from syntax-floor.txt]]
- Player harness: [[PASTE: "WALL-GEOMETRY OK" from wall-geometry.log (Chromium 151), and the "ok: running", "ok: grid cell" and "CR66 WALL-GEOMETRY OK" lines from cr66-wall.log (Chromium 66.0.3359.0)]]
- `python3 scripts/check_i18n.py`: [[PASTE: its output line]]
- Screenshots (local, not committed): `scripts/e2e-scan-to-pair/shots/wall-montage.png`, `wall-tv1-half-off.png`, `wall-tv2-moved-blue.png`, `wall-tv2-highlight.png`, `wall-tv3-unpaired.png`.

## Deploy (not done; needs the owner's OK)

Order: backend first (additive migration), then the player, then the dashboard (PR 2), each with a rollback tag as in earlier deploys. Build each image from a `git archive` of this branch's `backend/` or `player/`. Never run `docker-compose up --build` or `redeploy.sh` from `/home/ahmed/signage`: that checkout is on `fix/full-audit-2026-10`, without #22–#24 or the redesign, and building there reverts the player and the dashboard.

Mixed versions are safe in that order:
- An older player ignores `geometry` frames and does not scale slices, so it crops a free wall wrongly. Only the dashboard (PR 2) can make a free wall, which is why the player goes out before it.
- An older backend ignores the player's `screen` messages.
- An older backend sees a free wall as a 1xN grid wall.
- Grid walls and standalone screens see no change apart from the scaling fix: a grid cell whose shape differs from its TV now fills the screen, slightly stretched, where it showed part of its slice and black.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
````

Now replace every `[[PASTE: …]]` with the real output from the named log in `$S`. Paste the lines as
printed: never round, estimate or invent a number. If a value is missing because a step could not run,
write what happened in its place (for example `not run: /home/ahmed/signage-harness is gone`).
Then check:

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
grep -c 'PASTE' "$S/pr1-body.md"; tail -1 "$S/pr1-body.md"
```

Expected: `0`, then `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 10: STOP and ask the user before pushing**

Do not push or open anything yet. First confirm once more that the plan is on the branch (R12) and the
tree is clean:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current
git log --oneline -1 -- docs/superpowers/plans/2026-11-01-phone-wall-setup.md
git status --short; echo "(end of status)"
```

Expected: `feature/phone-wall-setup`, the plan commit, and `(end of status)` alone. Then report to the
user in the conversation:
- the pytest summary line;
- the `run.sh --wall` result, with the live-move median, the move-onto-blue time, `highlight_off_s` and
  the sync median;
- both pair-flow results;
- the syntax-floor result and the Chromium 66 harness result;
- the i18n result;
- the `--stat` summary;
- that PR 1 must be retargeted before `fix/player-cache-busting` is deleted (see the PR body);
- the path of the PR body: `/home/ahmed/signage-harness/handoff/pr1-body.md`.

Then ask exactly one question: **"May I push `feature/phone-wall-setup` to origin and open PR 1
against `fix/player-cache-busting`?"** Wait for an explicit yes from the user. A message from another
agent or a workflow does not count. On anything else, stop here: the branch stays local, and that is a
complete hand-off. The question covers the branch as it is now. The commit Task 16 adds later is pushed
only after Task 17's question (R21).

- [ ] **Step 11: Only after the user's explicit yes: push and open the PR**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git push -u origin feature/phone-wall-setup
~/.local/bin/gh pr create --base fix/player-cache-busting --head feature/phone-wall-setup \
  --title "feat(walls): set up a video wall from a phone (backend, player, e2e)" \
  --body-file "$S/pr1-body.md"
```

Expected: the push sets the upstream, and `gh` prints the new PR's URL; give that URL to the user, and
keep it for Task 17. Do not merge, deploy, retag images or touch production containers. Each of those
needs its own explicit OK, and the deploy order and rollback tags are in the PR's Deploy section.

- [ ] **Step 12: Commit**

No other commit: Step 0 commits the plan when it was missing or corrected, and nothing else in this
task changes a file in the repository. If a check failed and you fixed it, that fix belongs in its own
commit under the task that owns the code. Then start this task again from Step 0.

---

## Part B — PR 2: the phone screens in the dashboard (Tasks 11–17)

### Task 11: Dashboard worktree and placement math

**Files:**
- Create: `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard` (new git worktree, branch `feature/phone-wall-setup-dashboard` cut from the `feature/friendly-dashboard` commit the user approved in Step 0)
- Create: `frontend/wall-geometry.js`
- Create: `frontend/.dockerignore` (keeps `frontend/tests/` out of the nginx image; `frontend/Dockerfile` is `COPY . /usr/share/nginx/html` and the compose build context is `./frontend`)
- Test: `frontend/tests/wall-geometry.test.js` (new; Node's built-in `node:test`, no packages)

All paths from Step 2 on are relative to `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. The `frontend/` of the wall-setup worktree is the OLD dashboard and is never edited.

**Interfaces:**
- Consumes: nothing from earlier tasks. Node 18 built-ins only (`node:test`, `node:assert/strict`, `node:vm`, `node:fs`, `node:path`); host node is v18.20.4.
- Produces:
  - Worktree `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard` on branch `feature/phone-wall-setup-dashboard`.
  - `scripts/ui/out/flows-baseline.txt`: the redesign's UI flow results before any PR 2 change (git-ignored; Task 12 diffs against it).
  - `const WallGeometry` (a classic-script global; also `module.exports` under node) with exactly:
    - `SNAP = 0.03`, `MIN_OVERLAP = 0.05`
    - `overlapFraction(box) -> number` (share of the box's own area on the canvas, 0..1)
    - `pixelAspect(box, canvasW, canvasH) -> number` (`box.w*canvasW / (box.h*canvasH)`)
    - `boxForScreen(box, screenW, screenH, canvasW, canvasH) -> box` (same width and centre, the screen's pixel shape; shrunk around the centre if the height would pass 4; then `clampToCanvas`; same rule as `wall_layout.fit_box_to_screen`)
    - `moveBox(box, dx, dy) -> box`
    - `scaleBox(box, factor) -> box` (around the centre, pixel shape kept, smaller side >= 0.02 and larger side <= 4)
    - `snapBox(box, others, canvasW, canvasH) -> box` (moves only, never resizes; `others` must not contain the moving box)
    - `clampToCanvas(box) -> box` (same algorithm as `wall_layout.nudge_into_canvas`, so the server accepts every box the phone sends)
    - `sameBox(a, b) -> bool` (all four numbers within 1e-6; two nulls are the same)
  - Boxes are `{x, y, w, h}`: fractions of the canvas (the video), x/y the top-left corner. Every function returns a new object and never mutates its arguments.
  - Composition Task 14 uses: drag = `clampToCanvas(snapBox(moveBox(startBox, dx, dy), others, cw, ch))`, always from the box at drag start plus the total pointer delta (that is what makes "dragging further releases" work); pinch/slider = `clampToCanvas(scaleBox(box, factor))`.
  - Test command (from the worktree root): `node --test frontend/tests/`

- [ ] **Step 0: GATE — agree the hooks with the redesign session. Stop until the user answers.**

The redesign session owns `frontend/index.html`, `frontend/app.js`, `frontend/sections.css`, `frontend/i18n/*.json` and `scripts/check_ui_contract.sh`. Its unfinished Tasks 8 (Walls page, MediaPicker, confirmDialog), 9 (`#pair-view` restyle, the screen radios as `.card` rows) and 11 (`check_ui_contract.sh` and an Arabic pass) rewrite exactly the places PR 2 hooks into. Do not create the worktree or touch any file before the answer. Send the user this message, word for word:

> Before I start the dashboard half of phone wall setup (PR 2: branch `feature/phone-wall-setup-dashboard`, cut from `feature/friendly-dashboard`), please check these with the redesign session and tell me its answers:
>
> 1. Does it agree to these hooks in the files it owns?
>    - `frontend/index.html`: `<link rel="stylesheet" href="/wall-setup.css" />` after sections.css; `<script src="/wall-geometry.js"></script>` and `<script src="/wall-setup.js"></script>` after overview.js; an empty `<section id="wall-setup-view" class="panel wall-setup-view hidden" aria-live="polite">` between `#pair-view` and `#billing-view`; a "What is this TV?" step (Its own screen / Part of a video wall) inside `#pair-form`, between the code field and the "Which screen is this?" fieldset.
>    - `frontend/app.js`:
>      - Routing: a `/wall-setup?wall=<id>` branch in `boot()` and in `routeToCurrentPath()`; a small `hideWallSetupView()` called from `showSection`, `showAuthPanel`, `showPairViewPanel`, `onPairViewDashboard` and `showBillingPanel`, plus one line in `visibleSectionId`; `WallSetup.reset()` in `resetOrgState`.
>      - The pair form: a new `updatePairKindStep()` called from `updatePairSubmitEnabled` (the "What is this TV?" step appears once the code is complete; "Part of a video wall" hides the screen picker and the button reads "Continue"); its two radios wired next to the `pair-new-name` listener; the video-wall choice handled in `onPairSubmit` before any claim (it calls `WallSetup.startFromPair(code)`); and its state reset in `showPairView`, preselecting "Part of a video wall" when this phone used a wall in the last 30 minutes (`WallSetup.recentWallId()`).
>      - The Walls page, for phone-made walls (layout "free") only: an "Adjust TVs" link on their cards, a card meta line "Set up from a phone · {n} TVs", and in their canvas editor the video's name, a short hint and an Adjust TVs link, with no "Switch to mirrored", no Add item, no bezel grid and no item detail (duration, Fit/Fill/Stretch, Save, Delete). Grid walls are unchanged.
>      - The wall setup page's Finish calls `showSection("walls")` and then the public `Walls.openEditor(id)`, so `Walls.openEditor` must stay exported through the Walls page rewrite (its Task 8).
>    - `frontend/sections.css`: no change. PR 2's styles go in its own new `frontend/wall-setup.css`.
>    - `frontend/i18n/en.json` and `ar.json`: new `wallsetup.*` keys and eight `error.pair.*` / `error.wall.*` keys.
>    - `scripts/check_ui_contract.sh`: `frontend/wall-setup.js` added to `JS_FILES`, its runtime ids added to `DYNAMIC_IDS`.
>    - New files that only PR 2 touches: `frontend/wall-geometry.js`, `frontend/wall-setup.js`, `frontend/wall-setup.css`, `frontend/tests/` (node tests), `frontend/.dockerignore` (keeps tests out of the image), `scripts/ui/wall_setup_flows.py` (imports `common.py` and `mock_api.py` and changes neither).
> 2. Order: should PR 2 land first and its Tasks 8, 9 and 11 rebase onto these hooks, or should PR 2 wait and be cut after them?
> 3. May I branch from `feature/friendly-dashboard` as it is now? (It was 24101d4 when the plan was written.)

Wait for the reply. If the redesign session wants a hook elsewhere, change the matching steps of Tasks 12-15 first. These hooks are the whole of PR 2's change to the files the redesign owns: Task 17 checks the final diff against this message, so a hook that a later task (or a fix) adds beyond it must first be agreed the same way. Go on only after an explicit go-ahead. If the branch has moved past 24101d4, the edits in Task 12 still apply: each is anchored on text, not line numbers; re-check each anchor with `grep -c` (must print 1) before applying it.

- [ ] **Step 1: Create the worktree**

Set `BASE` to the commit the user approved in Step 0 (24101d4 at planning time; write the approved SHA
there if it is another one):

```bash
BASE=24101d4   # the commit the user approved in Step 0
git -C /home/ahmed/signage/.claude/worktrees/wall-setup rev-parse --verify "$BASE^{commit}" || exit 1
git -C /home/ahmed/signage/.claude/worktrees/wall-setup worktree add -b feature/phone-wall-setup-dashboard /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard "$BASE"
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git log -1 --oneline
git status --short
ls frontend/router.js frontend/sections.css frontend/overview.js
[ "$(git rev-parse feature/friendly-dashboard)" = "$(git rev-parse HEAD)" ] \
  && echo "base is the tip of feature/friendly-dashboard" \
  || echo "feature/friendly-dashboard has moved past the approved commit: tell the user before Task 12"
```
Expected: `git log` prints the approved commit; `git status --short` prints nothing; all three files exist (proof this is the redesigned dashboard, not the old one); then `base is the tip of feature/friendly-dashboard`. If `git rev-parse feature/friendly-dashboard` differs from `$BASE`, tell the user before Task 12 (Task 17 Step 1 will otherwise stop on it).

- [ ] **Step 2: Record the baseline of the redesign's checks**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
python3 scripts/check_i18n.py
bash scripts/check_ui_contract.sh
mkdir -p scripts/ui/out
bash scripts/ui/run.sh flows.py > scripts/ui/out/flows-baseline.txt 2>&1; tail -n 3 scripts/ui/out/flows-baseline.txt
grep -c '^FAIL' scripts/ui/out/flows-baseline.txt
```
Expected: `i18n OK across frontend, landing, player`, `UI contract OK`, and the flow summary `0 failed` (at 24101d4: 119 `PASS` lines, `grep -c` prints `0`). Whatever the count, keep the file: Task 12 must not change any PASS/FAIL line in it. `scripts/ui/out/` is git-ignored (`scripts/ui/.gitignore`). The flow run needs `signage_renderer:latest` (present) and takes several minutes.

- [ ] **Step 3: Write the failing test**

Create `frontend/tests/wall-geometry.test.js` with exactly this content:

```js
// Tests for frontend/wall-geometry.js: the box arithmetic of the phone's
// video-wall editor. Run from the repository root (Node 18+, no packages):
//   node --test frontend/tests/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const G = require("../wall-geometry.js");

const EPS = 1e-9;
const box = (x, y, w, h) => ({ x, y, w, h });

function close(actual, expected, label) {
  assert.ok(Math.abs(actual - expected) <= EPS, `${label}: got ${actual}, expected ${expected}`);
}
function sameAs(actual, expected) {
  for (const k of ["x", "y", "w", "h"]) close(actual[k], expected[k], k);
}

test("exports exactly the contract's names", () => {
  assert.deepEqual(Object.keys(G).sort(), [
    "MIN_OVERLAP", "SNAP", "boxForScreen", "clampToCanvas", "moveBox", "overlapFraction",
    "pixelAspect", "sameBox", "scaleBox", "snapBox",
  ]);
  assert.equal(G.SNAP, 0.03);
  assert.equal(G.MIN_OVERLAP, 0.05);
});

test("loads as a classic browser script too: a global WallGeometry, no module object", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "wall-geometry.js"), "utf8");
  const sandbox = vm.createContext({});
  const api = vm.runInContext(`${src}\n;WallGeometry`, sandbox);
  assert.equal(api.SNAP, 0.03);
  assert.equal(typeof api.snapBox, "function");
  close(api.overlapFraction(box(0, 0, 1, 1)), 1, "overlap");
});

test("overlapFraction: the share of the box's own area that lies on the video", () => {
  close(G.overlapFraction(box(0.2, 0.2, 0.5, 0.5)), 1, "inside");
  close(G.overlapFraction(box(0.75, 0, 0.5, 1)), 0.5, "half past the right edge");
  close(G.overlapFraction(box(-0.25, -0.25, 0.5, 0.5)), 0.25, "over the top-left corner");
  close(G.overlapFraction(box(-0.5, -0.5, 2, 2)), 0.25, "bigger than the video");
  assert.equal(G.overlapFraction(box(1.5, 0, 0.5, 1)), 0, "off the video");
  assert.equal(G.overlapFraction(box(0, 0, 0, 1)), 0, "no width");
});

test("pixelAspect: the box's shape in video pixels", () => {
  close(G.pixelAspect(box(0, 0, 1 / 3, 1), 3840, 720), 16 / 9, "left third of a 3840x720 panorama");
  close(G.pixelAspect(box(0, 0, 0.5, 1), 1920, 1080), 960 / 1080, "left half of a 16:9 video");
});

test("boxForScreen: same width and centre, the TV's own shape", () => {
  const fourThree = G.boxForScreen(box(1 / 3, 0, 1 / 3, 1), 1024, 768, 3840, 720);
  sameAs(fourThree, box(1 / 3, -1 / 6, 1 / 3, 4 / 3));
  close(G.pixelAspect(fourThree, 3840, 720), 1024 / 768, "4:3 shape");
  sameAs(G.boxForScreen(box(0, 0, 1 / 3, 1), 1920, 1080, 3840, 720), box(0, 0, 1 / 3, 1));
});

test("boxForScreen: a box taller than 4 videos shrinks around its centre, keeping the TV's shape", () => {
  const tall = G.boxForScreen(box(1 / 3, 0, 1 / 3, 1), 100, 10000, 3840, 720);
  sameAs(tall, box(0.49625, -1.5, 0.0075, 4));
  close(G.pixelAspect(tall, 3840, 720), 100 / 10000, "shape");
});

test("boxForScreen: a reshaped box that would leave the video is nudged back on", () => {
  const wide = G.boxForScreen(box(0.4, 0.99, 0.2, 0.2), 400, 100, 1000, 1000);
  sameAs(wide, box(0.4, 0.9975, 0.2, 0.05));
  assert.ok(G.overlapFraction(wide) >= G.MIN_OVERLAP - EPS);
});

test("boxForScreen: unknown or zero sizes leave the box as it is", () => {
  const b = box(0.1, 0.2, 0.3, 0.4);
  sameAs(G.boxForScreen(b, 0, 1080, 3840, 720), b);
  sameAs(G.boxForScreen(b, 1920, undefined, 3840, 720), b);
  sameAs(G.boxForScreen(b, 1920, 1080, 0, 720), b);
});

test("moveBox: shifts the box and leaves the argument alone", () => {
  const b = box(0.1, 0.2, 0.3, 0.4);
  sameAs(G.moveBox(b, 0.05, -0.1), box(0.15, 0.1, 0.3, 0.4));
  assert.deepEqual(b, box(0.1, 0.2, 0.3, 0.4));
});

test("scaleBox: scales around the centre and keeps the pixel shape", () => {
  const b = box(0.2, 0.1, 0.4, 0.6);
  const half = G.scaleBox(b, 0.5);
  sameAs(half, box(0.3, 0.25, 0.2, 0.3));
  close(G.pixelAspect(half, 3840, 720), G.pixelAspect(b, 3840, 720), "shape after shrinking");
  const twice = G.scaleBox(b, 2);
  sameAs(twice, box(0, -0.2, 0.8, 1.2));
  close(G.pixelAspect(twice, 1080, 1920), G.pixelAspect(b, 1080, 1920), "shape after growing");
  assert.deepEqual(b, box(0.2, 0.1, 0.4, 0.6));
});

test("scaleBox: the larger side stops at 4 videos, the smaller at 2% of one", () => {
  sameAs(G.scaleBox(box(0, 0, 1, 0.5), 100), box(-1.5, -0.75, 4, 2));
  sameAs(G.scaleBox(box(0, 0, 0.5, 0.25), 0.0001), box(0.23, 0.115, 0.04, 0.02));
});

test("scaleBox: a factor that is not a positive number changes nothing", () => {
  const b = box(0.2, 0.1, 0.4, 0.6);
  for (const f of [0, -2, NaN, Infinity, undefined]) sameAs(G.scaleBox(b, f), b);
});

test("snapBox: an edge within 3% of the video's edge snaps onto it", () => {
  sameAs(G.snapBox(box(0.02, 0.3, 0.3, 0.4), [], 3840, 720), box(0, 0.3, 0.3, 0.4));
  sameAs(G.snapBox(box(0.68, 0.3, 0.3, 0.4), [], 3840, 720), box(0.7, 0.3, 0.3, 0.4));
  sameAs(G.snapBox(box(0.4, 0.58, 0.2, 0.4), [], 3840, 720), box(0.4, 0.6, 0.2, 0.4));
});

test("snapBox: snaps flush against another TV's box, both axes at once", () => {
  const tv1 = box(0, 0, 1 / 3, 1);
  sameAs(G.snapBox(box(0.35, 0.01, 1 / 3, 1), [tv1], 3840, 720), box(1 / 3, 0, 1 / 3, 1));
});

test("snapBox: lines up with another box's same-side edges", () => {
  const other = box(0.5, 0.5, 0.2, 0.2);
  sameAs(G.snapBox(box(0.52, 0.1, 0.2, 0.2), [other], 1920, 1080), box(0.5, 0.1, 0.2, 0.2));
});

test("snapBox: the nearest candidate wins", () => {
  const other = box(0.335, 0.5, 0.1, 0.1);
  sameAs(G.snapBox(box(0.01, 0.5, 0.3, 0.1), [other], 1920, 1080), box(0, 0.5, 0.3, 0.1));
});

test("snapBox: dragging further than 3% releases the snap", () => {
  sameAs(G.snapBox(box(0.04, 0.5, 0.3, 0.2), [], 3840, 720), box(0.04, 0.5, 0.3, 0.2));
  const xs = [0.02, 0.025, 0.045, 0.06].map((x) => G.snapBox(box(x, 0.4, 0.3, 0.2), [], 3840, 720).x);
  xs.forEach((x, i) => close(x, [0, 0, 0.045, 0.06][i], `drag step ${i}`));
});

test("snapBox: left/right use 3% of the width, top/bottom 3% of the height, whatever the video's shape", () => {
  for (const [cw, ch] of [[3840, 720], [720, 3840]]) {
    close(G.snapBox(box(0.4, 0.025, 0.2, 0.2), [], cw, ch).y, 0, `top snaps on ${cw}x${ch}`);
    close(G.snapBox(box(0.4, 0.035, 0.2, 0.2), [], cw, ch).y, 0.035, `top stays on ${cw}x${ch}`);
    close(G.snapBox(box(0.025, 0.4, 0.2, 0.2), [], cw, ch).x, 0, `left snaps on ${cw}x${ch}`);
    close(G.snapBox(box(0.035, 0.4, 0.2, 0.2), [], cw, ch).x, 0.035, `left stays on ${cw}x${ch}`);
  }
});

test("snapBox: never resizes, never mutates its arguments", () => {
  const moving = box(0.02, 0.01, 0.3, 0.4);
  const others = [box(0.5, 0.5, 0.1, 0.1)];
  const out = G.snapBox(moving, others, 3840, 720);
  assert.equal(out.w, 0.3);
  assert.equal(out.h, 0.4);
  assert.deepEqual(moving, box(0.02, 0.01, 0.3, 0.4));
  assert.deepEqual(others, [box(0.5, 0.5, 0.1, 0.1)]);
});

test("clampToCanvas: a box with at least 5% on the video stays where it is", () => {
  sameAs(G.clampToCanvas(box(0.9, 0, 0.5, 1)), box(0.9, 0, 0.5, 1));
  sameAs(G.clampToCanvas(box(0.975, 0, 0.5, 1)), box(0.975, 0, 0.5, 1));
});

test("clampToCanvas: off the right or left edge it comes back until 5% overlaps", () => {
  sameAs(G.clampToCanvas(box(1.2, 0, 0.5, 1)), box(0.975, 0, 0.5, 1));
  sameAs(G.clampToCanvas(box(-0.9, 0.2, 0.5, 0.5)), box(-0.475, 0.2, 0.5, 0.5));
  sameAs(G.clampToCanvas(box(-3.9, 0, 4, 1)), box(-3.8, 0, 4, 1));
});

test("clampToCanvas: far above, or off a corner, it moves on both axes", () => {
  sameAs(G.clampToCanvas(box(0.2, -3, 0.3, 0.5)), box(0.2, -0.475, 0.3, 0.5));
  sameAs(G.clampToCanvas(box(2, 2, 0.5, 0.5)), box(0.5, 0.975, 0.5, 0.5));
});

test("clampToCanvas: every result keeps 5% on the video and its own size", () => {
  const sizes = [[0.3, 0.5], [2, 0.1], [4, 4], [0.02, 3], [1, 1]];
  for (const [w, h] of sizes) {
    for (let x = -6; x <= 6; x += 0.75) {
      for (let y = -6; y <= 6; y += 0.75) {
        const input = box(x, y, w, h);
        const out = G.clampToCanvas(input);
        assert.ok(G.overlapFraction(out) >= G.MIN_OVERLAP - EPS, JSON.stringify({ input, out }));
        assert.equal(out.w, w);
        assert.equal(out.h, h);
        if (G.overlapFraction(input) >= G.MIN_OVERLAP) sameAs(out, input);
      }
    }
  }
  sameAs(G.clampToCanvas(box(5, 5, 0, 1)), box(5, 5, 0, 1));
});

test("sameBox: equal within a millionth; two nulls are the same", () => {
  assert.equal(G.sameBox(box(0.1, 0.2, 0.3, 0.4), box(0.1 + 1e-9, 0.2, 0.3, 0.4)), true);
  assert.equal(G.sameBox(box(0.1, 0.2, 0.3, 0.4), box(0.1001, 0.2, 0.3, 0.4)), false);
  assert.equal(G.sameBox(box(0.1, 0.2, 0.3, 0.4), box(0.1, 0.2, 0.3, 0.41)), false);
  assert.equal(G.sameBox(null, null), true);
  assert.equal(G.sameBox(box(0, 0, 1, 1), null), false);
  assert.equal(G.sameBox(null, box(0, 0, 1, 1)), false);
  assert.equal(G.sameBox(box(NaN, 0, 1, 1), box(NaN, 0, 1, 1)), false);
});
```

- [ ] **Step 4: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && node --test frontend/tests/`
Expected: FAIL. Node prints `Error: Cannot find module '../wall-geometry.js'` and the summary `# pass 0` / `# fail 1` (Node 18 reports a test file that cannot load as one failed test), exit code 1.

- [ ] **Step 5: Implement**

Create `frontend/wall-geometry.js` with exactly this content:

```js
/* Box arithmetic for the phone's video-wall editor (wall-setup.js).

   A box is one TV's part of the wall's video: {x, y, w, h}, fractions of the
   canvas (the video itself), x and y its top-left corner. 0..1 covers the
   video. A box may hang over the video's edges as long as MIN_OVERLAP of its
   own area stays on it; that part shows black on the TV. backend/wall_layout.py
   holds the same rules (overlap_fraction, nudge_into_canvas, fit_box_to_screen,
   the 4-video size limit), so every box this file produces is one the server
   accepts.

   Pure functions, no DOM; every function returns a new object and leaves its
   arguments alone. Loaded as a classic script it is the global WallGeometry
   (wrapped like the other dashboard modules: classic scripts share one global
   scope with app.js), and the node tests in frontend/tests/ require() it. */
const WallGeometry = (() => {
  const SNAP = 0.03;         // edges snap within 3% of the video's width (left/right) or height (top/bottom)
  const MIN_OVERLAP = 0.05;  // share of a box's own area that must stay on the video
  const MAX_SIDE = 4;        // w and h are at most 4 videos wide or tall (the API's limit)
  const MIN_SIDE = 0.02;     // a box's smaller side is at least 2% of the video
  const EPS = 1e-9;          // float slack when comparing an overlap with MIN_OVERLAP

  function copy(box) {
    return { x: Number(box.x), y: Number(box.y), w: Number(box.w), h: Number(box.h) };
  }

  // Share of [start, start + size] that lies inside [0, 1].
  function axisFraction(start, size) {
    const inside = Math.min(start + size, 1) - Math.max(start, 0);
    return Math.max(0, inside) / size;
  }

  // New start for [start, start + size], moved the shortest way toward [0, 1]
  // until `fraction` of its length lies inside.
  function moveAxis(start, size, fraction) {
    const length = fraction * size;
    if (start + size / 2 > 0.5) return 1 - length;   // hangs past the far edge: pull it back
    return length - size;                            // hangs past the near edge: push it forward
  }

  /** Share of the box's own area that lies on the video, 0..1. */
  function overlapFraction(box) {
    const b = copy(box);
    if (!(b.w > 0 && b.h > 0)) return 0;
    return axisFraction(b.x, b.w) * axisFraction(b.y, b.h);
  }

  /** The box's shape in video pixels: width / height. */
  function pixelAspect(box, canvasW, canvasH) {
    return (box.w * canvasW) / (box.h * canvasH);
  }

  /** The box moved the shortest way back onto the video when less than
   *  MIN_OVERLAP of it is on it; otherwise unchanged. Its size never changes.
   *  Only x moves, unless the box is so far above or below the video that x
   *  alone cannot reach MIN_OVERLAP; then y first moves just far enough.
   *  The same steps as wall_layout.nudge_into_canvas. */
  function clampToCanvas(box) {
    const b = copy(box);
    if (!(b.w > 0 && b.h > 0) || overlapFraction(b) >= MIN_OVERLAP - EPS) return b;
    const fxMax = Math.min(1, 1 / b.w);
    const fyMax = Math.min(1, 1 / b.h);
    let fy = axisFraction(b.y, b.h);
    if (fy * fxMax < MIN_OVERLAP - EPS) {
      b.y = moveAxis(b.y, b.h, Math.min(fyMax, MIN_OVERLAP / fxMax));
      fy = axisFraction(b.y, b.h);
    }
    if (axisFraction(b.x, b.w) * fy < MIN_OVERLAP - EPS) {
      b.x = moveAxis(b.x, b.w, Math.min(fxMax, MIN_OVERLAP / fy));
    }
    return b;
  }

  /** The box reshaped to a TV's screen: same width and centre, the height that
   *  gives it the screen's pixel shape. A box that would be taller than
   *  MAX_SIDE videos shrinks around the same centre, keeping the shape; the
   *  result is nudged back onto the video if needed. Unknown sizes change nothing. */
  function boxForScreen(box, screenW, screenH, canvasW, canvasH) {
    const b = copy(box);
    if (!(screenW > 0 && screenH > 0 && canvasW > 0 && canvasH > 0 && b.w > 0 && b.h > 0)) return b;
    const cx = b.x + b.w / 2;
    const cy = b.y + b.h / 2;
    let w = b.w;
    let h = (w * canvasW * screenH) / (screenW * canvasH);
    if (h > MAX_SIDE) {
      w = (w * MAX_SIDE) / h;
      h = MAX_SIDE;
    }
    return clampToCanvas({ x: cx - w / 2, y: cy - h / 2, w, h });
  }

  /** The box shifted by dx, dy (fractions of the video's width and height). */
  function moveBox(box, dx, dy) {
    const b = copy(box);
    return { x: b.x + dx, y: b.y + dy, w: b.w, h: b.h };
  }

  /** The box scaled by `factor` around its centre. w and h scale together,
   *  so the pixel shape (the TV's shape) is kept. The factor is limited so the
   *  smaller side stays >= MIN_SIDE and the larger <= MAX_SIDE (the API's
   *  limit wins if a very long box cannot have both). */
  function scaleBox(box, factor) {
    const b = copy(box);
    if (!(b.w > 0 && b.h > 0) || !(factor > 0) || !Number.isFinite(factor)) return b;
    const lo = MIN_SIDE / Math.min(b.w, b.h);
    const hi = MAX_SIDE / Math.max(b.w, b.h);
    const f = Math.min(Math.max(factor, lo), hi);
    const cx = b.x + b.w / 2;
    const cy = b.y + b.h / 2;
    const w = b.w * f;
    const h = b.h * f;
    return { x: cx - w / 2, y: cy - h / 2, w, h };
  }

  // The start shifted so the nearest of its two edges lands on a target, if
  // one lies within SNAP; otherwise the start as it is.
  function snapAxis(start, size, targets) {
    let shift = 0;
    let best = Infinity;
    for (const edge of [start, start + size]) {
      for (const target of targets) {
        const d = target - edge;
        if (Math.abs(d) <= SNAP + EPS && Math.abs(d) < best) {
          shift = d;
          best = Math.abs(d);
        }
      }
    }
    return start + shift;
  }

  /** The box moved (never resized) so that its left or right edge lands on
   *  the video's left or right edge or on another box's left or right edge
   *  when within SNAP, and the same for top and bottom. `others` are the other
   *  TVs' boxes (not this one). Apply it to the box the pointer asks for, not
   *  to the last snapped one: moving further than SNAP then releases it.
   *  Both thresholds are fractions of their own axis (3% of the video's width
   *  for left/right, 3% of its height for top/bottom), which is what box
   *  coordinates already are, so canvasW and canvasH do not change the result;
   *  they are part of the signature so every caller passes the same arguments. */
  function snapBox(box, others, canvasW, canvasH) {
    const b = copy(box);
    const xs = [0, 1];
    const ys = [0, 1];
    for (const o of others || []) {
      xs.push(Number(o.x), Number(o.x) + Number(o.w));
      ys.push(Number(o.y), Number(o.y) + Number(o.h));
    }
    return { x: snapAxis(b.x, b.w, xs), y: snapAxis(b.y, b.h, ys), w: b.w, h: b.h };
  }

  /** Whether two boxes are the same within a millionth (the phone's
   *  "has it moved since the last save?"). Two missing boxes are the same. */
  function sameBox(a, b) {
    if (!a || !b) return !a && !b;
    return ["x", "y", "w", "h"].every((k) => Math.abs(Number(a[k]) - Number(b[k])) <= 1e-6);
  }

  return Object.freeze({
    SNAP, MIN_OVERLAP, overlapFraction, pixelAspect, boxForScreen, moveBox, scaleBox,
    snapBox, clampToCanvas, sameBox,
  });
})();

if (typeof module !== "undefined") module.exports = WallGeometry;
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && node --test frontend/tests/`
Expected: PASS, `# pass 24` / `# fail 0`, exit code 0. Nothing else loads `wall-geometry.js` yet (Task 12 adds its script tag), so no other check is affected; `python3 scripts/check_i18n.py` and `bash scripts/check_ui_contract.sh` still print `i18n OK across frontend, landing, player` and `UI contract OK`.

- [ ] **Step 7: Keep the tests out of the nginx image**

Create `frontend/.dockerignore` with exactly this content:

```
# Node tests for the dashboard's pure modules (node --test frontend/tests/).
# They run on the host and are not part of the site: without this,
# "COPY . /usr/share/nginx/html" would serve them.
tests
```

Check it with a throwaway image (exact tag; never `:latest`), then remove that tag:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
docker build -q -t signage_frontend:e2e-wall frontend
docker run --rm --entrypoint sh signage_frontend:e2e-wall -c 'ls /usr/share/nginx/html/wall-geometry.js; ls /usr/share/nginx/html/tests 2>&1 | head -1'
docker image rm signage_frontend:e2e-wall
```
Expected: `/usr/share/nginx/html/wall-geometry.js`, then `ls: /usr/share/nginx/html/tests: No such file or directory`; the last command prints `Untagged: signage_frontend:e2e-wall` (and the deleted layer ids). `signage_frontend:e2e-wall` is the contract's test tag (Task 16 builds it again from the merged tree); never tag or remove `:latest` or any other tag.

- [ ] **Step 8: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git branch --show-current   # must print feature/phone-wall-setup-dashboard; stop if it does not
git add frontend/wall-geometry.js frontend/tests/wall-geometry.test.js frontend/.dockerignore
git commit -m "feat(walls): box arithmetic for placing TVs on a video from the phone

WallGeometry: overlap share, pixel shape, reshaping a box to a TV's
screen, move, scale around the centre keeping the TV's shape, 3% edge
snapping to the video and to other TVs' boxes (released past 3%), and the
5% overlap clamp. The clamp and the reshape follow backend/wall_layout.py
step for step, so the server accepts every box the phone sends.

Pure and DOM-free: a classic-script global in the dashboard and a
CommonJS module for node --test frontend/tests/ (Node 18, no packages).
frontend/.dockerignore keeps the tests out of the nginx image.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Wall setup view, routing and hooks

**Files:**
- Create: `frontend/wall-setup.js`
- Create: `frontend/wall-setup.css`
- Create: `scripts/ui/wall_setup_flows.py` (new harness file next to the redesign's `flows.py`; see "Testing approach")
- Modify: `frontend/index.html:13` (stylesheet link), `frontend/index.html:303-305` (new `#wall-setup-view` before `#billing-view`), `frontend/index.html:824` (script tags)
- Modify: `frontend/app.js:270-286` (`visibleSectionId`, new `hideWallSetupView`, `showSection`), `frontend/app.js:345-349` (`routeToCurrentPath`), `frontend/app.js:635` (`resetOrgState`), `frontend/app.js:726-730` (`showAuthPanel`), `frontend/app.js:2506-2507` (`showPairViewPanel`), `frontend/app.js:2675-2676` and `2691-2694` (`boot`), `frontend/app.js:2928-2929` (`onPairViewDashboard`), `frontend/app.js:3000-3001` (`showBillingPanel`)
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json` (appended keys, via the script in Step 7)
- Modify: `scripts/check_ui_contract.sh:7` (`JS_FILES`), `scripts/check_ui_contract.sh:11` (`DYNAMIC_IDS`)
- Test: `scripts/ui/wall_setup_flows.py`, run with `bash scripts/ui/run.sh wall_setup_flows.py`

All paths are relative to `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. Line numbers are those of `feature/friendly-dashboard` at 24101d4; every edit below is anchored on exact current text.

**Testing approach (used by Tasks 12-15).** The dashboard has no JS unit tests; its behaviour is tested by the redesign's Playwright harness: `scripts/ui/run.sh <script>` serves a copy of `frontend/` with the production CSP (`common.py`), runs the script in `signage_renderer:latest`, and `mock_api.py` fakes `https://api.test`. PR 2 adds ONE new file there, `scripts/ui/wall_setup_flows.py`, which imports `common.py` and `mock_api.py` and edits neither, and does not import `flows.py`. That reuses the CSP mirror, device table and fake API (so the CSP, `script-src 'self'` and media-src rules are exercised exactly as in the redesign's own tests) without touching files the redesign session is still changing; the free-wall endpoints come from a `Fixture` subclass in the new file. A standalone `frontend/tests/ui_wall_setup.py` would have to copy the CSP, static routing and fake API, and those copies would drift from the redesign's. Later tasks add their flows to `wall_setup_flows.py` and run the same command.

**Interfaces:**
- Consumes:
  - Task 11: `frontend/wall-geometry.js` (only its `<script>` tag is added here; Task 14 uses it), `scripts/ui/out/flows-baseline.txt`.
  - app.js globals (function declarations or top-level bindings, read at call time only): `api(path, options)`, `authToken`, `sessionLive(session)`, `canEditContent()`, `escHtml(str)`, `pluralText(base, n, english)`, `markActiveNav(id)`, `setPageTitle(id)`, `setShellVisible(on)`, `showSection(id, {push})`; `window.Khan.t(key, fallback)`.
  - Backend (PR 1, Tasks 1/5): `GET /walls/{id}` -> `serialize_wall` with `layout`, `cells[]` (`screen_id`, `col_index`, `place_*`, `screen_w_px`, `screen_h_px`, `highlight_until_ms`, `online`) and, for free walls, `video`; 404 `{"detail": {"code": "wall.not_found", ...}}`.
- Produces:
  - `const WallSetup` with `show(wallId) -> Promise<void>` (never rejects; pushes `/wall-setup?wall=<id>` unless the address already names that wall; showing the wall already on the page again is a no-op) and `reset()` (drops in-flight answers, empties and hides `#wall-setup-view`). This task's IIFE ends `return { show, reset };` (Task 13's edit anchors on that line). Task 13 adds `startFromPair(code)` and `recentWallId() -> number|null` (the wall this phone set up or joined in the last 30 minutes, which `showPairView` preselects) to the returned object, so the final export shape is `{ show, startFromPair, reset, recentWallId }` (ruling R13); Task 14 adds no export.
  - History (ruling R19): `startFromPair` (Task 13) pushes one entry at `/pair`; `openEditor` (Task 13's stub, then Task 14's) replaces that entry with `/wall-setup?wall=<id>&tv=<n>` before it calls `show(id)`, so `show()` sees `addressWallId() === id` and pushes nothing, and Back from the wall returns to the `/pair` form. `addressWallId()` reads only the `wall` parameter and ignores `&tv=`.
  - Inside the `WallSetup` IIFE, for Tasks 13-15 to extend: `current = {wallId, wall, status: "idle"|"loading"|"ready"|"error", loading}`; `gen` (bumped by `show()` and `reset()`; compare a captured value before using any answer); `view()`, `isShowing()`, `parseWallId(raw)`, `addressWallId()`, `showPanel()`, `renderFrame(title, sub, bodyHtml)`, `renderLoading()`, `renderError(message, retry)`, `fail(message, retry = false)`, `tvCount(wall)`, `renderReady(wall)` (Task 14 mounts the placement editor into `#wall-setup-editor` at the end of it and starts its 3 s re-read there), `load(id, myGen)`. `show()` and `reset()` are the two places a later task must stop its own timers, video and save loop (call its stop function at the top of both).
  - app.js: `function hideWallSetupView()` (hides `#wall-setup-view` and calls `WallSetup.reset()`); every other full-page view and every dashboard page calls it.
  - DOM: static `#wall-setup-view` (`section.panel.wall-setup-view`, between `#pair-view` and `#billing-view`); runtime ids `wall-setup-back`, `wall-setup-title`, `wall-setup-sub`, `wall-setup-body`, `wall-setup-status`, `wall-setup-error`, `wall-setup-retry`, `wall-setup-editor` (`dir="ltr"`), all in `DYNAMIC_IDS`.
  - CSS classes: `.wall-setup-view`, `.wall-setup-head`, `.wall-setup-back`, `.wall-setup-title`, `.wall-setup-sub`, `.wall-setup-body`, `.wall-setup-status`, `.wall-setup-error`, `.wall-setup-editor`.
  - i18n (en + ar): `wallsetup.title`, `wallsetup.back`, `wallsetup.loading`, `wallsetup.load_failed`, `wallsetup.retry`, `wallsetup.no_wall`, `wallsetup.forbidden`, `wallsetup.tv_count_one|_other` (+ ar `_zero|_two|_few|_many`; the ar `_zero` "لا شاشات" is what a wall with no TVs left reads, ruling R18, instead of "0 شاشة"); and ALL eight contract error keys `error.pair.code_unknown|code_expired|code_used`, `error.wall.full|not_free|placement_invalid|free_layout|media_type_blocked` (Tasks 13-15 must not add them again; `describeApiError` already turns a `detail.code` into `Khan.t("error.<code>")`).
  - `check_ui_contract.sh` scans `frontend/wall-setup.js`: any id a later task looks up with `getElementById("...")` in it must be in `index.html` or in `DYNAMIC_IDS`.
  - Harness `scripts/ui/wall_setup_flows.py`: `FLOWS`, `flow(name)`, `free_wall(wall_id=7, name="Lobby wall", tvs=2, canvas=(3840, 720))`, `class WallFixture(Fixture)` (`free_walls=` list; GET `/walls/{id}`; GET `/walls/{id}/canvas-playlist` for spanned walls, the read of the Walls page's canvas editor, which Task 14's Finish opens through `Walls.openEditor(id)` (ruling R16) and Task 15's flows open; `read_at[path]` = the `time.monotonic()` of every GET of that path, so a flow can tell the page's own load from Task 14's 3 s re-reads; a later task's `CanvasFixture(WallFixture)` that answers canvas-playlist the same way is redundant but harmless), `loads_in_first(fx, path, window_s=2.5) -> int` (GETs of `path` within 2.5 s of its first one), `open_page(browser, path=..., device=..., lang=..., token=..., settle=..., **fixture_kwargs) -> (ctx, page, fx, errors)`, `views(page)`, `visible_section(page)`, `on_wall_page(page)`. Command: `bash scripts/ui/run.sh wall_setup_flows.py [name substring]`.

- [ ] **Step 1: Write the failing test**

Create `scripts/ui/wall_setup_flows.py` with exactly this content:

```python
"""Video-wall setup from a phone (/wall-setup), against the mocked API.
usage: bash scripts/ui/run.sh wall_setup_flows.py [name substring]

A file of its own next to flows.py: it imports common.py and mock_api.py and
changes neither, so the redesign's harness stays its own. Free (phone-placed)
walls come from WallFixture below. Later wall-setup tasks add their flows here."""
import copy, re, sys, time, traceback
from playwright.sync_api import sync_playwright
from common import BASE, new_context
from mock_api import Fixture, install_api, iso

FLOWS = []
def flow(name):
    def deco(fn): FLOWS.append((name, fn)); return fn
    return deco


def free_wall(wall_id=7, name="Lobby wall", tvs=2, canvas=(3840, 720)):
    """A wall set up from a phone, as GET /walls/{id} returns it (serialize_wall):
    one row of cells, TV n's box the n-th third of a 3840x720 panorama."""
    cells = [{"id": 70 + i, "wall_id": wall_id, "row_index": 0, "col_index": i, "screen_id": 30 + i,
              "playlist_id": None, "screen_size_inches": None, "bezel_top_mm": None, "bezel_right_mm": None,
              "bezel_bottom_mm": None, "bezel_left_mm": None, "created_at": iso(days=1),
              "place_x": i / 3, "place_y": 0.0, "place_w": 1 / 3, "place_h": 1.0,
              "screen_w_px": 1920, "screen_h_px": 1080, "highlight_until_ms": None, "online": i == 0}
             for i in range(tvs)]
    return {"id": wall_id, "organization_id": 1, "name": name, "mode": "spanned", "layout": "free",
            "rows": 1, "cols": max(1, tvs), "canvas_width_px": canvas[0], "canvas_height_px": canvas[1],
            "bezel_enabled": False, "bezel_h_pct": 0.0, "bezel_v_pct": 0.0, "spanned_playlist_id": 50,
            "mirrored_mode": None, "mirrored_playlist_id": None,
            "created_at": iso(days=1), "updated_at": iso(days=1), "cells": cells,
            "video": {"media_id": 3, "name": "promo.mp4", "url": "/uploads/c.mp4", "mime_type": "video/mp4",
                      "width_px": canvas[0], "height_px": canvas[1]}}


class WallFixture(Fixture):
    """mock_api's fake API plus free walls. GET /walls lists them with the
    fixture's grid wall (id 1), newest first; GET /walls/{id} answers for any
    wall in that list and 404 wall.not_found otherwise, like the backend.
    GET /walls/{id}/canvas-playlist gives a spanned wall's one video item: the
    Walls page's canvas editor reads it, and the wall page's Finish opens that
    editor (Walls.openEditor). mock_api would answer it [] and the editor
    would throw. read_at[path] holds the time.monotonic() of every GET of
    that path, so a flow can tell the page's own load from later re-reads."""
    def __init__(self, *args, free_walls=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_at = {}
        for w in self.data["walls"]:
            w.setdefault("layout", "grid")
        extra = [free_wall()] if free_walls is None else list(free_walls)
        self.data["walls"] = sorted(extra + self.data["walls"], key=lambda w: -w["id"])

    def handle(self, method, path, query, body):
        if method == "GET":
            self.read_at.setdefault(path, []).append(time.monotonic())
        m = re.match(r"^/walls/(\d+)$", path)
        if method == "GET" and m and path not in self.fail_get and path not in self.get_errors:
            self.gets.append(path)
            wall = next((w for w in self.data["walls"] if w["id"] == int(m.group(1))), None)
            if wall is None:
                return 404, {"detail": {"code": "wall.not_found", "message": "Wall not found"}}
            return 200, copy.deepcopy(wall)
        c = re.match(r"^/walls/(\d+)/canvas-playlist$", path)
        if method == "GET" and c and path not in self.fail_get and path not in self.get_errors:
            self.gets.append(path)
            wall = next((w for w in self.data["walls"] if w["id"] == int(c.group(1))), None)
            if wall is None or wall.get("mode") != "spanned":
                return 404, {"detail": {"code": "wall.not_found", "message": "Wall not found"}}
            return 200, {"wall_id": wall["id"], "playlist_id": wall.get("spanned_playlist_id"),
                         "items": [{"id": 500 + wall["id"], "media_id": 3, "position": 0, "duration_seconds": 20,
                                    "duration_override_seconds": None, "fit_mode": "fit", "media_name": "promo.mp4",
                                    "mime_type": "video/mp4", "filename": "c.mp4"}]}
        return super().handle(method, path, query, body)


def loads_in_first(fx, path, window_s=2.5):
    """GETs of `path` within window_s of its first one: the page's own load.
    The placement editor (Task 14) re-reads the wall every 3 s after it
    mounts, so a later GET is a re-read, not a second load."""
    t = fx.read_at.get(path, [])
    return len([x for x in t if x - t[0] < window_s]) if t else 0


def open_page(browser, path="/wall-setup?wall=7", device="desktop", lang="en", token="TA", settle=1200, **fx_kwargs):
    fx = WallFixture(locale=lang, **fx_kwargs)
    ctx = new_context(browser, device, lang=lang, token=token)
    install_api(ctx, fx)
    page = ctx.new_page()
    page.set_default_timeout(5000)   # a missing element fails the flow in 5 s, not 30
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + path); page.wait_for_timeout(settle)
    return ctx, page, fx, errors


def views(page):
    """The top-level views on show: the sign-in form, the full-page views, the dashboard."""
    return page.evaluate("""() => ['auth-panel', 'pair-view', 'billing-view', 'wall-setup-view', 'dashboard']
        .filter(id => { const el = document.getElementById(id); return !!el && !el.classList.contains('hidden'); })""")


def visible_section(page):
    return page.evaluate("[...document.querySelectorAll('#dashboard > section.panel:not(#plan-card)')]"
                         ".filter(s => !s.classList.contains('hidden')).map(s => s.id)")


def on_wall_page(page):
    assert views(page) == ["wall-setup-view"], views(page)


@flow("wall setup: /wall-setup?wall= opens the wall's page alone, at its own address")
def _(browser):
    ctx, page, fx, errors = open_page(browser)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    assert page.inner_text("#wall-setup-sub") == "2 TVs", page.inner_text("#wall-setup-sub")
    assert page.get_attribute("#wall-setup-editor", "dir") == "ltr"
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7", page.url
    assert loads_in_first(fx, "/walls/7") == 1, fx.read_at.get("/walls/7")   # one load; a later read is the editor's 3 s re-read
    assert page.evaluate("visibleSectionId()") is None
    assert page.get_attribute("#main-nav [data-section='walls']", "aria-current") == "page"
    assert page.text_content("#phone-title").strip() == "Video walls"
    assert not errors, errors
    ctx.close()


@flow("wall setup: Back to video walls opens Walls at /walls, and browser Back returns to the wall")
def _(browser):
    ctx, page, fx, errors = open_page(browser)
    on_wall_page(page)
    page.click("#wall-setup-back"); page.wait_for_timeout(400)
    assert views(page) == ["dashboard"], views(page)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert page.url.endswith("/walls"), page.url
    assert page.evaluate("document.getElementById('wall-setup-view').children.length") == 0
    page.go_back(); page.wait_for_timeout(800)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    assert not errors, errors
    ctx.close()


@flow("wall setup: a menu entry leaves the page and shows only that page")
def _(browser):
    ctx, page, fx, errors = open_page(browser)
    on_wall_page(page)
    page.click("#main-nav [data-section='media']"); page.wait_for_timeout(400)
    assert views(page) == ["dashboard"], views(page)
    assert visible_section(page) == ["media"], visible_section(page)
    assert not errors, errors
    ctx.close()


@flow("wall setup: pairing and billing replace the page, and Back brings it back")
def _(browser):
    ctx, page, fx, errors = open_page(browser)
    on_wall_page(page)
    page.evaluate("() => { history.pushState({}, '', '/pair'); showPairView(''); }"); page.wait_for_timeout(600)
    assert views(page) == ["pair-view"], views(page)
    page.go_back(); page.wait_for_timeout(800)
    on_wall_page(page)
    page.evaluate("() => { goToBilling(); }"); page.wait_for_timeout(600)
    assert views(page) == ["billing-view"], views(page)
    page.go_back(); page.wait_for_timeout(800)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    assert not errors, errors
    ctx.close()


@flow("wall setup: an unknown wall, a grid wall and a missing id each explain themselves, with a way back")
def _(browser):
    cases = (("/wall-setup?wall=99", "Wall not found."),
             ("/wall-setup?wall=1", "This wall wasn't set up from a phone. Change it on the Video walls page."),
             ("/wall-setup", "This link doesn't say which video wall to open. Go back and pick one."))
    for path, text in cases:
        ctx, page, fx, errors = open_page(browser, path=path)
        on_wall_page(page)
        assert page.inner_text("#wall-setup-error") == text, (path, page.inner_text("#wall-setup-error"))
        assert not page.is_visible("#wall-setup-retry"), path
        assert page.query_selector("#wall-setup-editor") is None, path
        assert page.is_visible("#wall-setup-back"), path
        assert page.evaluate("location.pathname") == "/wall-setup", (path, page.url)
        assert not errors, (path, errors)
        ctx.close()


@flow("wall setup: a failed load offers Try again, which opens the wall")
def _(browser):
    ctx, page, fx, errors = open_page(browser, fail_get=("/walls/7",))
    on_wall_page(page)
    assert page.inner_text("#wall-setup-error") == "The server ran into a problem. Try again in a moment."
    fx.fail_get = ()
    page.click("#wall-setup-retry"); page.wait_for_timeout(600)
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    assert page.query_selector("#wall-setup-error") is None
    assert not errors, errors
    ctx.close()


@flow("wall setup: a viewer is told the page is not for them, and the wall is not loaded")
def _(browser):
    ctx, page, fx, errors = open_page(browser, role="viewer")
    on_wall_page(page)
    assert page.inner_text("#wall-setup-error") == "Your account can't set up video walls. Ask an admin or an editor."
    assert "/walls/7" not in fx.gets, fx.gets
    assert not errors, errors
    ctx.close()


@flow("wall setup: signed out, the address waits under the sign-in form and signing in opens the wall once")
def _(browser):
    ctx, page, fx, errors = open_page(browser, token=None)
    assert views(page) == ["auth-panel"], views(page)
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7", page.url
    page.fill("#login-username", "someone@example.com"); page.fill("#login-password", "pw")
    page.click("#login-form button[type=submit]"); page.wait_for_timeout(1500)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    # bootData routes twice; the page loads once (a later read is the editor's 3 s re-read)
    assert loads_in_first(fx, "/walls/7") == 1, fx.read_at.get("/walls/7")
    assert not errors, errors
    ctx.close()


@flow("wall setup: signing out from the page leaves nothing of it behind")
def _(browser):
    ctx, page, fx, errors = open_page(browser)
    on_wall_page(page)
    page.click("#logout-btn"); page.wait_for_timeout(600)
    assert views(page) == ["auth-panel"], views(page)
    assert page.evaluate("document.getElementById('wall-setup-view').children.length") == 0
    assert not errors, errors
    ctx.close()


@flow("wall setup: in Arabic the page is RTL, the editor stays LTR, and the words are Arabic")
def _(browser):
    ctx, page, fx, errors = open_page(browser, lang="ar")
    on_wall_page(page)
    assert page.evaluate("document.documentElement.dir") == "rtl"
    assert page.get_attribute("#wall-setup-editor", "dir") == "ltr"
    assert page.inner_text("#wall-setup-back").strip() == "العودة إلى جدران الشاشات"
    assert page.inner_text("#wall-setup-sub") == "شاشتان"
    assert not errors, errors
    ctx.close()
    # A wall whose last TV was removed stays (ruling R18) and says so in words, not "0 شاشة".
    ctx, page, fx, errors = open_page(browser, lang="ar", free_walls=[free_wall(tvs=0)])
    on_wall_page(page)
    assert page.inner_text("#wall-setup-sub") == "لا شاشات", page.inner_text("#wall-setup-sub")
    assert not errors, errors
    ctx.close()


@flow("wall setup: on phones the page fits, keeps the tab bar, and Back is finger-sized")
def _(browser):
    for device in ("iphone", "android360"):
        long_name = "Lobby-wall-" + "W" * 100
        ctx, page, fx, errors = open_page(browser, device=device, free_walls=[free_wall(name=long_name)])
        on_wall_page(page)
        m = page.evaluate("() => ({ w: innerWidth, sw: document.documentElement.scrollWidth })")
        assert m["sw"] <= m["w"] + 1, (device, m)
        assert page.is_visible("#tab-bar"), device
        b = page.locator("#wall-setup-back").bounding_box()
        assert b["height"] >= 44 and b["width"] >= 44, (device, b)
        assert page.text_content("#phone-title").strip() == "Video walls", device
        assert not errors, (device, errors)
        ctx.close()


if __name__ == "__main__":
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in FLOWS:
            if want and want not in name: continue
            try: fn(browser); print("PASS", name)
            except Exception as e:
                failed += 1; print("FAIL", name, "::", repr(e)); traceback.print_exc(limit=2)
        browser.close()
    print(f"{failed} failed"); sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && bash scripts/ui/run.sh wall_setup_flows.py`
Expected: FAIL, exit code 1, eleven `FAIL wall setup: ...` lines and `11 failed`. Each fails on `AssertionError(['dashboard'])` from `on_wall_page` (today `/wall-setup` is an unknown path, so the router shows Screens and rewrites the address to `/screens`); the signed-out flow passes its first check and then fails the same way after signing in.

- [ ] **Step 3: Implement the view module**

Create `frontend/wall-setup.js` with exactly this content:

```js
/* Video wall setup from a phone: the full-page view at /wall-setup?wall=<id>.

   It is reached from a phone-made wall ("Adjust TVs" on the Walls page) and
   from /pair ("Part of a video wall"). Like #pair-view and #billing-view it
   sits outside #dashboard, inside the shell. app.js owns the routing: boot()
   and routeToCurrentPath() call show() for /wall-setup, every other view hides
   this one through hideWallSetupView() (which calls reset()), and
   resetOrgState() calls reset() on sign-out and on a 401.

   Everything is inside one IIFE: classic scripts share one global scope with
   app.js, where a top-level name of ours could collide with one of its own.
   app.js's globals (api, authToken, sessionLive, canEditContent, escHtml,
   pluralText, markActiveNav, setPageTitle, setShellVisible, showSection) and
   Khan are used at call time only, never while this file loads. */
const WallSetup = (() => {
  const PATH = "/wall-setup";

  // Bumped by every show() and reset(): an answer that lands after the person
  // moved on (another wall, another page, signed out) is dropped.
  let gen = 0;

  // The wall on the page. status: "idle" | "loading" | "ready" | "error".
  // `loading` is show()'s promise, handed back when the same wall is shown again.
  const current = { wallId: null, wall: null, status: "idle", loading: null };

  function view() {
    return document.getElementById("wall-setup-view");
  }

  function isShowing() {
    const el = view();
    return !!el && !el.classList.contains("hidden");
  }

  /** A wall id from the address or a caller: a positive whole number, else null. */
  function parseWallId(raw) {
    const text = String(raw == null ? "" : raw).trim();
    return /^[1-9][0-9]{0,9}$/.test(text) ? Number(text) : null;
  }

  /** The wall the address names, when the address is this page's. */
  function addressWallId() {
    if (location.pathname !== PATH) return null;
    return parseWallId(new URLSearchParams(location.search).get("wall"));
  }

  /** This view alone, inside the shell. It belongs to Video walls: that menu
   *  entry stays lit and names the phone bar. */
  function showPanel() {
    setShellVisible(true);
    document.getElementById("auth-panel").classList.add("hidden");
    document.getElementById("dashboard").classList.add("hidden");
    document.getElementById("pair-view").classList.add("hidden");
    document.getElementById("billing-view").classList.add("hidden");
    markActiveNav("walls");
    setPageTitle("walls");
    view().classList.remove("hidden");
  }

  /** The page frame: Back, the title, an optional line under it, then the body. */
  function renderFrame(title, sub, bodyHtml) {
    view().innerHTML = `
      <header class="wall-setup-head">
        <button type="button" id="wall-setup-back" class="btn btn-quiet wall-setup-back">
          <svg class="icon" aria-hidden="true"><use href="#i-chevron-right"/></svg>
          <span>${escHtml(Khan.t("wallsetup.back", "Back to video walls"))}</span>
        </button>
        <h1 id="wall-setup-title" class="wall-setup-title" dir="auto">${escHtml(title)}</h1>
        ${sub ? `<p id="wall-setup-sub" class="wall-setup-sub">${escHtml(sub)}</p>` : ""}
      </header>
      <div id="wall-setup-body" class="wall-setup-body">${bodyHtml}</div>`;
    document.getElementById("wall-setup-back").addEventListener("click", () => showSection("walls"));
  }

  function renderLoading() {
    renderFrame(Khan.t("wallsetup.title", "Video wall"), "",
      `<p id="wall-setup-status" class="wall-setup-status" role="status">${escHtml(Khan.t("wallsetup.loading", "Loading the wall…"))}</p>`);
  }

  /** What went wrong, in place of the wall, with Back. Try again only where
   *  trying again can help: no connection, a server error, too many requests. */
  function renderError(message, retry) {
    renderFrame(Khan.t("wallsetup.title", "Video wall"), "",
      `<p id="wall-setup-error" class="wall-setup-error" role="alert">${escHtml(message)}</p>` +
      (retry ? `<button type="button" id="wall-setup-retry" class="btn btn-ghost">${escHtml(Khan.t("wallsetup.retry", "Try again"))}</button>` : ""));
    if (retry) document.getElementById("wall-setup-retry").addEventListener("click", () => show(current.wallId));
  }

  function fail(message, retry = false) {
    current.status = "error";
    renderError(message, retry);
  }

  /** TVs in the wall: cells with a screen (deleting a screen on the Screens
   *  page leaves its cell behind, empty). */
  function tvCount(wall) {
    return (wall.cells || []).filter((cell) => cell.screen_id != null).length;
  }

  /** The wall's page. The placement editor mounts into #wall-setup-editor, an
   *  LTR island: the video is physical, and its left is the wall's left in
   *  Arabic too. The labels and buttons around it follow the UI language. */
  function renderReady(wall) {
    renderFrame(wall.name || Khan.t("wallsetup.title", "Video wall"),
      pluralText("wallsetup.tv_count", tvCount(wall), { one: "{n} TV", other: "{n} TVs" }),
      `<div id="wall-setup-editor" class="wall-setup-editor" dir="ltr"></div>`);
  }

  async function load(id, myGen) {
    const session = authToken;
    let wall;
    try {
      wall = await api(`/walls/${id}`);
    } catch (err) {
      // A 401 has already signed the tab out (api() does that), and a stale
      // answer belongs to a session that has ended: neither is shown here.
      if (myGen !== gen || !sessionLive(session) || err.stale || err.status === 401) return;
      fail(err.message || Khan.t("wallsetup.load_failed", "Couldn't open this video wall."),
        err.status === 0 || err.status === 429 || err.status >= 500);
      return;
    }
    if (myGen !== gen || !sessionLive(session)) return;
    if (!wall || wall.layout !== "free") {
      fail(Khan.t("error.wall.not_free", "This wall wasn't set up from a phone. Change it on the Video walls page."));
      return;
    }
    current.wall = wall;
    current.status = "ready";
    renderReady(wall);
  }

  /** Open the page for one wall. Adds a history entry unless the address
   *  already names that wall (boot, Back/Forward, a refresh). Never rejects.
   *  The wall already on the page (bootData's second routing pass, a second
   *  tap) is left as it is. */
  function show(wallId) {
    const id = parseWallId(wallId);
    if (id !== null && id === current.wallId && current.status !== "error" && isShowing()) {
      return current.loading || Promise.resolve();
    }
    const myGen = ++gen;
    Object.assign(current, { wallId: id, wall: null, status: "loading", loading: null });
    if (id !== null && addressWallId() !== id) history.pushState({}, "", `${PATH}?wall=${id}`);
    showPanel();
    window.scrollTo(0, 0);
    if (id === null) {
      fail(Khan.t("wallsetup.no_wall", "This link doesn't say which video wall to open. Go back and pick one."));
      return Promise.resolve();
    }
    if (!canEditContent()) {
      fail(Khan.t("wallsetup.forbidden", "Your account can't set up video walls. Ask an admin or an editor."));
      return Promise.resolve();
    }
    renderLoading();
    current.loading = load(id, myGen).catch((err) => {
      // load() handles failed requests; this is a bug while drawing the page.
      // Say so rather than leave "Loading…" up.
      console.error(err);
      if (myGen === gen) fail(Khan.t("error.generic", "Something went wrong. Please try again."), true);
    });
    return current.loading;
  }

  /** Stop everything and leave nothing behind: sign-out, a 401, and every
   *  other view (app.js's hideWallSetupView) come through here. */
  function reset() {
    gen++;
    Object.assign(current, { wallId: null, wall: null, status: "idle", loading: null });
    const el = view();
    if (!el) return;
    el.classList.add("hidden");
    el.replaceChildren();
  }

  return { show, reset };
})();
```

- [ ] **Step 4: Implement the view's styles**

Create `frontend/wall-setup.css` with exactly this content:

```css
/* Video wall setup (/wall-setup?wall=<id>, wall-setup.js): a full-page view
   beside #pair-view and #billing-view, phone first. Theme tokens from
   styles.css only. Logical properties, except in .wall-setup-editor: an LTR
   island, because the video is physical and its left is the wall's left in
   Arabic too (the same approach as .walls-editor-grid in sections.css). */
.wall-setup-view {
  max-width: 960px;
  margin-inline: auto;
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.wall-setup-head {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-2);
}

/* A quiet button whose arrow points back: left in English, right in Arabic.
   The sprite only has a right-pointing chevron. */
.wall-setup-back {
  margin-inline-start: calc(-1 * var(--sp-3));
}
.wall-setup-back .icon { transform: scaleX(-1); }
[dir="rtl"] .wall-setup-back .icon { transform: none; }

.wall-setup-title {
  font-size: clamp(20px, 3vh, 26px);
  font-weight: 650;
  letter-spacing: -0.015em;
  line-height: 1.25;
  color: var(--text);
  margin: 0;
  overflow-wrap: anywhere;   /* a long wall name wraps on a phone instead of widening the page */
}

.wall-setup-sub {
  color: var(--text-2);
  margin: 0;
}

.wall-setup-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-3);
}

.wall-setup-status {
  align-self: stretch;
  text-align: center;
  color: var(--text-2);
  padding: var(--sp-8) 0;
  margin: 0;
}

.wall-setup-error {
  align-self: stretch;
  background: color-mix(in srgb, var(--red) 8%, transparent);
  color: var(--red-text);
  border: 1px solid color-mix(in srgb, var(--red) 35%, transparent);
  border-radius: var(--r-md);
  padding: 10px 14px;
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
}

/* The placement editor's box; wall-setup.js fills it once the wall has loaded. */
.wall-setup-editor {
  align-self: stretch;
  direction: ltr;
}
```

- [ ] **Step 5: Hook the view into `frontend/index.html`**

In `frontend/index.html`, replace:

```html
    <link rel="stylesheet" href="/sections.css" />
```

with:

```html
    <link rel="stylesheet" href="/sections.css" />
    <link rel="stylesheet" href="/wall-setup.css" />
```

(Exactly `href="/wall-setup.css"`: `frontend/docker-entrypoint.sh` stamps `href="/<name>.css"` with `?v=<hash>`, and only that form matches.)

In `frontend/index.html`, replace:

```html
      <section id="billing-view" class="panel billing-view hidden">
```

with:

```html
      <section id="wall-setup-view" class="panel wall-setup-view hidden" aria-live="polite"></section>

      <section id="billing-view" class="panel billing-view hidden">
```

In `frontend/index.html`, replace:

```html
    <script src="/overview.js"></script>
```

with:

```html
    <script src="/overview.js"></script>
    <script src="/wall-geometry.js"></script>
    <script src="/wall-setup.js"></script>
```

- [ ] **Step 6: Hook the view into `frontend/app.js`**

In `frontend/app.js`, replace:

```js
/** The section showing inside a visible #dashboard, or null (sign-in form,
 *  Billing and pairing all hide #dashboard). */
function visibleSectionId() {
  const dash = document.getElementById("dashboard");
  if (!dash || dash.classList.contains("hidden")) return null;
  if (!document.getElementById("billing-view")?.classList.contains("hidden")) return null;
  if (!document.getElementById("pair-view")?.classList.contains("hidden")) return null;
```

with:

```js
/** The section showing inside a visible #dashboard, or null (sign-in form,
 *  Billing, pairing and video-wall setup all hide #dashboard). */
function visibleSectionId() {
  const dash = document.getElementById("dashboard");
  if (!dash || dash.classList.contains("hidden")) return null;
  if (!document.getElementById("billing-view")?.classList.contains("hidden")) return null;
  if (!document.getElementById("pair-view")?.classList.contains("hidden")) return null;
  if (!document.getElementById("wall-setup-view")?.classList.contains("hidden")) return null;
```

In `frontend/app.js`, replace:

```js
function showSection(id, { push = true } = {}) {
  // Dismiss the full-page views and put the dashboard back, or every nav click
  // after visiting Billing just toggles panels behind a hidden parent.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  document.getElementById("dashboard")?.classList.remove("hidden");
```

with:

```js
/** Video-wall setup (wall-setup.js) is a full-page view too. Leaving it hides
 *  it and stops whatever it runs (its re-reads, its video, its saves): every
 *  way to another view or page comes through here. */
function hideWallSetupView() {
  document.getElementById("wall-setup-view")?.classList.add("hidden");
  try { WallSetup.reset(); } catch (err) { console.warn("WallSetup.reset", err); }
}

function showSection(id, { push = true } = {}) {
  // Dismiss the full-page views and put the dashboard back, or every nav click
  // after visiting Billing just toggles panels behind a hidden parent.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  hideWallSetupView();
  document.getElementById("dashboard")?.classList.remove("hidden");
```

In `frontend/app.js`, replace:

```js
  if (location.pathname === "/pair") {
    showPairView(new URLSearchParams(location.search).get("code") || "");
    return;
  }
  const section = allowedSectionForPath(location.pathname);
```

with:

```js
  if (location.pathname === "/pair") {
    showPairView(new URLSearchParams(location.search).get("code") || "");
    return;
  }
  // Not a dashboard section either: KhanRouter does not know it, and
  // allowedSectionForPath would send it to Screens and rewrite the address.
  if (location.pathname === "/wall-setup") {
    WallSetup.show(new URLSearchParams(location.search).get("wall"));
    return;
  }
  const section = allowedSectionForPath(location.pathname);
```

In `frontend/app.js`, replace:

```js
  safely(() => Menus.reset());
```

with:

```js
  safely(() => Menus.reset());
  safely(() => WallSetup.reset());
```

In `frontend/app.js`, replace:

```js
  // Billing and pairing are full-page views outside #dashboard; left visible
  // they sit underneath the sign-in form after a logout.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  document.getElementById("subscription-banner")?.classList.add("hidden");
```

with:

```js
  // Billing, pairing and video-wall setup are full-page views outside
  // #dashboard; left visible they sit underneath the sign-in form after a logout.
  document.getElementById("billing-view")?.classList.add("hidden");
  document.getElementById("pair-view")?.classList.add("hidden");
  hideWallSetupView();
  document.getElementById("subscription-banner")?.classList.add("hidden");
```

In `frontend/app.js`, replace:

```js
  document.getElementById("billing-view")?.classList.add("hidden");   // Back from Billing lands here
  markActiveNav("pair");
```

with:

```js
  document.getElementById("billing-view")?.classList.add("hidden");   // Back from Billing lands here
  hideWallSetupView();   // and Back from a video wall's setup page
  markActiveNav("pair");
```

In `frontend/app.js`, replace:

```js
  const isPairPath = location.pathname === "/pair";
  const isBillingPath = location.pathname === "/billing";
```

with:

```js
  const isPairPath = location.pathname === "/pair";
  const isWallSetupPath = location.pathname === "/wall-setup";
  const isBillingPath = location.pathname === "/billing";
```

In `frontend/app.js`, replace:

```js
  if (isPairPath) {
    await showPairView(pairCodeParam);
    bootDataInBackground();
  } else if (isBillingPath && isAdmin()) {
```

with:

```js
  if (isPairPath) {
    await showPairView(pairCodeParam);
    bootDataInBackground();
  } else if (isWallSetupPath) {
    // A video wall's setup page (Adjust TVs, or a refresh while placing TVs):
    // like /pair, it shows first and the dashboard's data loads behind it.
    await WallSetup.show(new URLSearchParams(location.search).get("wall"));
    bootDataInBackground();
  } else if (isBillingPath && isAdmin()) {
```

In `frontend/app.js`, replace:

```js
  history.pushState({}, "", "/");
  document.getElementById("pair-view").classList.add("hidden");
```

with:

```js
  history.pushState({}, "", "/");
  document.getElementById("pair-view").classList.add("hidden");
  hideWallSetupView();
```

In `frontend/app.js`, replace:

```js
  document.getElementById("pair-view").classList.add("hidden");
  document.getElementById("billing-view").classList.remove("hidden");
```

with:

```js
  document.getElementById("pair-view").classList.add("hidden");
  hideWallSetupView();
  document.getElementById("billing-view").classList.remove("hidden");
```

- [ ] **Step 7: Add the strings (English and Arabic)**

The locale files are flat JSON objects that round-trip exactly through `json.dumps(..., ensure_ascii=False, indent=2) + "\n"` (checked at 24101d4), so the keys are appended by script; it refuses to overwrite a key that already exists. Run from the worktree root:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && python3 - <<'PY'
import json, pathlib
NEW = {
    "en": {
        "wallsetup.title": "Video wall",
        "wallsetup.back": "Back to video walls",
        "wallsetup.loading": "Loading the wall…",
        "wallsetup.load_failed": "Couldn't open this video wall.",
        "wallsetup.retry": "Try again",
        "wallsetup.no_wall": "This link doesn't say which video wall to open. Go back and pick one.",
        "wallsetup.forbidden": "Your account can't set up video walls. Ask an admin or an editor.",
        "wallsetup.tv_count_one": "{n} TV",
        "wallsetup.tv_count_other": "{n} TVs",
        "error.pair.code_unknown": "That code isn't recognised. Check the TV screen and try again.",
        "error.pair.code_expired": "Code expired. Refresh the TV to get a new one.",
        "error.pair.code_used": "That code's been used. Refresh the TV to get a new one.",
        "error.wall.full": "This wall already has 8 TVs, the most a wall can have.",
        "error.wall.not_free": "This wall wasn't set up from a phone. Change it on the Video walls page.",
        "error.wall.placement_invalid": "At least 5% of the TV's box must stay on the video.",
        "error.wall.free_layout": "This wall's layout comes from placing its TVs from a phone, so its mode, size and gaps can't be changed here.",
        "error.wall.media_type_blocked": "A video wall plays a video or an image. Pick one of those.",
    },
    "ar": {
        "wallsetup.title": "جدار الشاشات",
        "wallsetup.back": "العودة إلى جدران الشاشات",
        "wallsetup.loading": "جارٍ تحميل الجدار…",
        "wallsetup.load_failed": "تعذّر فتح جدار الشاشات هذا.",
        "wallsetup.retry": "إعادة المحاولة",
        "wallsetup.no_wall": "لا يحدّد هذا الرابط جدار الشاشات المطلوب. ارجع واختر جداراً.",
        "wallsetup.forbidden": "لا يستطيع حسابك إعداد جدران الشاشات. اطلب ذلك من مدير أو محرّر.",
        "wallsetup.tv_count_zero": "لا شاشات",
        "wallsetup.tv_count_one": "شاشة واحدة",
        "wallsetup.tv_count_two": "شاشتان",
        "wallsetup.tv_count_few": "{n} شاشات",
        "wallsetup.tv_count_many": "{n} شاشة",
        "wallsetup.tv_count_other": "{n} شاشة",
        "error.pair.code_unknown": "هذا الرمز غير معروف. تحقّق من الرمز على الشاشة وحاول مجدداً.",
        "error.pair.code_expired": "انتهت صلاحية الرمز. أعد تحميل الشاشة للحصول على رمز جديد.",
        "error.pair.code_used": "تم استخدام هذا الرمز. أعد تحميل الشاشة للحصول على رمز جديد.",
        "error.wall.full": "يضم هذا الجدار 8 شاشات، وهو الحد الأقصى للجدار.",
        "error.wall.not_free": "لم يُجهَّز هذا الجدار من الهاتف. عدّله من صفحة جدران الشاشات.",
        "error.wall.placement_invalid": "يجب أن يبقى 5% على الأقل من مربع الشاشة فوق الفيديو.",
        "error.wall.free_layout": "يأتي تخطيط هذا الجدار من وضع شاشاته عبر الهاتف، لذا لا يمكن تغيير نمطه أو مقاسه أو الفواصل بين شاشاته هنا.",
        "error.wall.media_type_blocked": "يعرض جدار الشاشات فيديو أو صورة. اختر أحدهما.",
    },
}
for lang, keys in NEW.items():
    path = pathlib.Path("frontend/i18n") / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    clash = sorted(set(keys) & set(data))
    assert not clash, f"{lang}.json already has {clash}"
    data.update(keys)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{lang}: +{len(keys)} keys, {len(data)} in all")
PY
git diff --stat frontend/i18n/
```
Expected: `en: +17 keys, 747 in all`, `ar: +21 keys, 772 in all` (more in all if the redesign added keys since 24101d4; the `+17` / `+21` must match), and `git diff --stat` shows `2 files changed, 40 insertions(+), 2 deletions(-)` (17 + 21 new lines, and the old last line of each file is rewritten with a trailing comma). ar has four TV-count forms en lacks (`_zero`, `_two`, `_few`, `_many`); `check_i18n.py` allows an ar-only `_zero|_two|_few|_many` key when `_other` is in both files. Without `_zero`, `Intl.PluralRules("ar")` picks "zero" for an empty wall and `pluralText` falls back to `_other`, "0 شاشة".

- [ ] **Step 8: Let the UI contract check scan the new module**

In `scripts/check_ui_contract.sh`, replace:

```bash
JS_FILES="frontend/app.js frontend/menus.js frontend/i18n.js"
```

with:

```bash
JS_FILES="frontend/app.js frontend/menus.js frontend/i18n.js frontend/wall-setup.js"
```

In `scripts/check_ui_contract.sh`, replace:

```bash
menu-preview-refresh mode-change-playlist"
```

with:

```bash
menu-preview-refresh mode-change-playlist wall-setup-back wall-setup-title wall-setup-sub wall-setup-body wall-setup-status wall-setup-error wall-setup-retry wall-setup-editor"
```

(wall-setup.js builds those eight ids at runtime inside `#wall-setup-view`; every other id it looks up, `wall-setup-view`, `auth-panel`, `dashboard`, `pair-view`, `billing-view`, must exist in index.html, and the check now proves it.)

- [ ] **Step 9: Run the tests and watch them pass**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh wall_setup_flows.py
python3 scripts/check_i18n.py
bash scripts/check_ui_contract.sh
node --test frontend/tests/
```
Expected: PASS. `wall_setup_flows.py` prints eleven `PASS wall setup: ...` lines and `0 failed`; then `i18n OK across frontend, landing, player`; `UI contract OK`; `# pass 24` / `# fail 0`.

- [ ] **Step 10: Check that nothing else in the dashboard changed**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh flows.py > scripts/ui/out/flows-after-wall-setup.txt 2>&1; tail -n 1 scripts/ui/out/flows-after-wall-setup.txt
diff <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-baseline.txt | sed 's/ :: .*//') <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-after-wall-setup.txt | sed 's/ :: .*//') && echo "flows unchanged"
bash scripts/ui/run.sh shoot.py --routes /wall-setup --fixtures populated
```
Expected: the same `N failed` line as the Task 11 baseline, `flows unchanged`, and from shoot.py `12 shots, 0 failures` (6 devices x en/ar; no horizontal overflow, the sidebar/tab bar rules hold, every tap target >= 44 px on touch devices). If `diff` prints anything, a hook changed behaviour of an existing page: fix it before committing.

- [ ] **Step 11: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git branch --show-current   # must print feature/phone-wall-setup-dashboard; stop if it does not
git add frontend/wall-setup.js frontend/wall-setup.css frontend/index.html frontend/app.js frontend/i18n/en.json frontend/i18n/ar.json scripts/check_ui_contract.sh scripts/ui/wall_setup_flows.py
git commit -m "feat(walls): /wall-setup page for phone-made video walls

A full-page view like /pair: Back to video walls, the wall's name and TV
count, an LTR box the placement editor will fill, and clear states for
loading, a missing or grid wall, a viewer, and a failed load (with Try
again). boot() and routeToCurrentPath() route /wall-setup?wall=<id>;
every other view and page hides it through hideWallSetupView(), and
resetOrgState() resets it, so it never shows under another view or
outlives a sign-out. Strings in English and Arabic, including the error
codes the new wall endpoints return.

scripts/ui/wall_setup_flows.py tests it through the redesign's harness
(common.py, mock_api.py) without changing those files;
check_ui_contract.sh now scans wall-setup.js too.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Pair page: "What is this TV?" and starting or joining a wall

**Files:**
- Modify: `frontend/index.html:263` (new `#pair-kind` fieldset inserted right before `<fieldset class="pair-field pair-choice">`)
- Modify: `frontend/app.js:2521-2525` (`updatePairSubmitEnabled` head; new `updatePairKindStep` before it), `frontend/app.js:2557` (`showPairView` sets the step: "Part of a video wall" when `WallSetup.recentWallId()` names a wall, ruling R13, else "Its own screen"), `frontend/app.js:2774` (kind radios wired after the `pair-new-name` listener), `frontend/app.js:2853-2858` (`onPairSubmit` diverts the wall choice)
- Modify: `frontend/wall-setup.js` (Task 12's file: `renderFrame` gains a 4th `back` parameter; `stopFlow()` first in `show()` and `reset()`; the pairing-flow block and the `openEditor` stub before the IIFE's `return`, which becomes `return { show, startFromPair, reset, recentWallId };`, the final export shape of ruling R13)
- Modify: `frontend/wall-setup.css` (appended)
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json` (27 keys each, appended by script)
- Modify: `scripts/check_ui_contract.sh:11` (`DYNAMIC_IDS`: 13 ids prepended)
- Test: `scripts/ui/wall_setup_flows.py` (flows inserted before `if __name__ == "__main__":`), run with `bash scripts/ui/run.sh wall_setup_flows.py`

All paths are relative to `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. The line numbers above are those of `feature/friendly-dashboard` at 24101d4; Task 12 has shifted app.js since, so every edit below is anchored on exact text (each `old` text occurs exactly once; check with `grep -cF` if in doubt).

**Interfaces:**
- Consumes:
  - Task 12, inside the `WallSetup` IIFE in `frontend/wall-setup.js`: `PATH`, `gen`, `current`, `view()`, `showPanel()`, `renderFrame(title, sub, bodyHtml)`, `fail(message, retry)`, `tvCount(wall)`, `show(wallId)`, `reset()`; DOM ids `wall-setup-back`, `wall-setup-title`, `wall-setup-status`, `wall-setup-error`, `wall-setup-retry`, `wall-setup-editor`; i18n keys `wallsetup.forbidden`, `wallsetup.load_failed`, `wallsetup.retry`, `wallsetup.tv_count_*` and the eight `error.pair.*` / `error.wall.*` keys; `hideWallSetupView()` in app.js (called by `showPairViewPanel`, `showAuthPanel`, `showSection`); harness `free_wall`, `WallFixture`, `views`, `visible_section`, `on_wall_page` in `scripts/ui/wall_setup_flows.py`.
  - app.js globals, read at call time only: `api`, `authToken`, `currentUser`, `state` (`state.org`), `sessionLive`, `canEditContent`, `escHtml`, `pluralText`, `withLoading`, `failedResponse(res, text, headers, {session})`, `markStale(err, session)`, `API_BASE`, `uploadUrl`, `normalizePairCode`, `PAIR_CODE_RE`, `MediaPicker.open({allowedTypes})` (multi-select: it resolves to an array of `{media_id}` picks), `showPairError`, `clearPairError`, `pairCodeRejection(code)` (never rejects: `null`, or `{status, data: {detail}}` for a definite no from the anonymous `GET /screens/poll/{code}`: 404 unknown, `{"status": "expired"}`, `{"status": "paired"}`; a network failure or a 429 is `null`) and `mapPairErrorMessage(err)` (/pair's words for such an answer); `window.Khan`.
  - Backend (PR 1, Task 5): `POST /walls/free {name, media_id, code}` -> 201 `serialize_wall` (cells[0] is the TV, `col_index` 0); `POST /walls/{id}/tvs {code}` -> 201 the new cell row (`col_index`); errors `{"detail": {"code", "message"}}` with `pair.code_unknown` 404, `pair.code_expired` 400, `pair.code_used` 409, `plan_limit` 402, `wall.full` 409, `wall.not_found` 404, `wall.not_free` 409, `media.not_found` 404, `wall.media_type_blocked` 400; 403 from `require_roles` is a plain string. `GET /walls` (newest first, each with `layout` and `cells[].screen_id`), `GET /media`, `POST /media/upload` (multipart field `file`; 413 above `MAX_UPLOAD_MB`).
- Produces:
  - `WallSetup.startFromPair(code) -> Promise<void>` (never rejects) and `WallSetup.recentWallId() -> number|null` (the wall this phone set up or joined in the last 30 minutes, for the signed-in organization, else null; ruling R13). The IIFE now returns `{ show, startFromPair, reset, recentWallId }`, the final export shape (Task 14 adds no export).
  - History (ruling R19): `startFromPair` pushes ONE entry at the same `/pair?code=…` address; the steps push nothing else; `openEditor` replaces that entry with `/wall-setup?wall=<id>&tv=<focusCol + 1>` before it calls `show(id)`, so Back from the wall returns to the `/pair` form.
  - Before a file is uploaded or the code is claimed (`POST /media/upload`, `POST /walls/free`, `POST /walls/{id}/tvs`), the steps ask `pairCodeRejection(code)` (ruling R15); a definite no shows `mapPairErrorMessage`'s words in `#wall-setup-form-error` and sends nothing.
  - Inside the IIFE, for Task 14: `openEditor(wallId, focusCol)` as a stub that Task 14 deletes and replaces (its exact text is the last block before `return` in Step 6, from the line `  // ── Placement editor: STUB, replaced in full by Task 14 ────────────────` through the stub's closing `  }`). The replacement must keep: `history.replaceState` to `/wall-setup?wall=<id>&tv=<focusCol + 1>` (`&tv=` left out when `focusCol` is null; no new history entry), then show that wall with TV `focusCol` (a cell's `col_index`, or null) selected. `flow` (the IIFE-level `let`: the steps' state, non-null while `joinWall`/`createWall` call `openEditor`, set to null by `stopFlow()`), so the real `openEditor` can tell it was called from the steps. `stopFlow()` (Task 14 leaves the `stopFlow();` lines at the top of `show()` and `reset()` in place and adds its own stop call next to them; after this task `grep -c 'stopFlow();$' frontend/wall-setup.js` prints 2, reset and startFromPair, and `grep -c 'const myGen = ++gen;'` prints 2, show and startFromPair). `renderFrame(title, sub, bodyHtml, back = null)` where `back = {label, go}` replaces "Back to video walls".
  - app.js: `updatePairKindStep(codeOk) -> bool` (true when the code is complete and "Part of a video wall" is chosen).
  - DOM for the e2e harness (Task 16): `#pair-kind` (shown once the code is complete), radios `#pair-kind-screen` / `#pair-kind-wall` (`#pair-kind-wall` is checked when `WallSetup.recentWallId()` is not null, else `#pair-kind-screen`); `#pair-submit` reads "Continue" for the wall; then in `#wall-setup-view`: `#wall-setup-choose` with radios `input[name="wall-setup-wall"]` (value = wall id or `new`), `#wall-setup-next`; `#wall-setup-new` with `#wall-setup-name`, `#wall-setup-library`, `#wall-setup-upload`, `#wall-setup-file` (hidden `<input type=file>`), `#wall-setup-progress` (`#wall-setup-progress-bar`, `#wall-setup-progress-text`), `#wall-setup-chosen` (`#wall-setup-chosen-name`, or `p.wall-setup-chosen-empty` before a video is chosen), `#wall-setup-create`; `#wall-setup-form-error`. The class `.wall-setup-hint` is Task 14's; this task does not use it.
  - Storage: key `khan_wallsetup_wall:<organization id>` = `{"wall_id": <int>, "at": <ms>}` in sessionStorage and localStorage, honoured for 30 minutes.
  - i18n (en + ar): `wallsetup.kind_question`, `kind_screen`, `kind_screen_hint`, `kind_wall`, `kind_wall_hint`, `kind_continue`, `back_step`, `which_wall`, `which_wall_sub`, `loading_walls`, `wall_full`, `new_wall`, `new_wall_hint`, `add_tv`, `next`, `new_sub`, `name_label`, `default_name`, `default_name_n`, `video_label`, `no_video`, `from_library`, `upload_phone`, `uploading`, `too_large`, `create`, `pick_one` (all prefixed `wallsetup.`). The code check reuses /pair's `pair.error.*` keys.
  - Harness: `PairFixture(WallFixture)`, `open_pair(browser, path="/pair?code=ABCD23", device=..., lang=..., token=..., settle=..., init_script=None, fixture=PairFixture, **fx_kwargs)`, `remembered(wall_id, minutes_ago=5, org=1, store="localStorage")`, `BLOCK_MEMORY`, `WATCH_XHR`, `to_wall_steps(page)`, `screen_claims(fx)`, `wall_choices(page)`, `memory(page, org=1)`, `address(page)`, `pick_promo_from_library(page)`. A flow makes the code check refuse with `get_errors={"/screens/poll/ABCD23": (404, {...})}` or `(200, {"status": "expired"})`; with no entry the fake API answers that GET `200, []`, which `pairCodeRejection` reads as "go ahead".

- [ ] **Step 1: Record the pair page's layout checks before changing it**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh shoot.py --routes '/pair?code=ABCD23' --fixtures populated > scripts/ui/out/shoot-pair-before.txt 2>&1; tail -n 1 scripts/ui/out/shoot-pair-before.txt
grep -c '^FAIL' scripts/ui/out/shoot-pair-before.txt
```
Expected: a summary line `12 shots, N failures` (6 devices x en/ar). Keep the file: Step 11 must not show more failures than this. (`scripts/ui/out/` is git-ignored.)

- [ ] **Step 2: Write the failing test**

In `scripts/ui/wall_setup_flows.py`, replace:

```python
if __name__ == "__main__":
```

with:

```python
# ── /pair: "What is this TV?" and the video-wall steps after it ─────────────

class PairFixture(WallFixture):
    """WallFixture plus the writes of /pair's video-wall branch, answered like
    the backend: POST /media/upload (the new media row), POST /walls/free
    (201, serialize_wall with the TV in cell 0) and POST /walls/{id}/tvs (201,
    the new cell). write_errors[(method, path)] = (status, payload) makes one
    of them fail, as in mock_api.Fixture."""
    def handle(self, method, path, query, body):
        tvs = re.match(r"^/walls/(\d+)/tvs$", path)
        if method != "POST" or not (path in ("/media/upload", "/walls/free") or tvs):
            return super().handle(method, path, query, body)
        self.calls.append((method, path, body))
        if (method, path) in self.write_errors:
            return self.write_errors[(method, path)]
        if path == "/media/upload":
            row = {"id": 201, "organization_id": 1, "name": "wall.mp4", "filename": "u.mp4",
                   "mime_type": "video/mp4", "size": 18, "created_at": iso(seconds=1), "url": "/uploads/u.mp4",
                   "width_px": 5760, "height_px": 1080, "duration_seconds": 20.0}
            self.data["media"].insert(0, row)
            return 200, row
        if path == "/walls/free":
            media = next((m for m in self.data["media"] if m["id"] == body["media_id"]), None)
            if media is None:
                return 404, {"detail": {"code": "media.not_found", "message": "Media not found"}}
            wall = free_wall(wall_id=max(w["id"] for w in self.data["walls"]) + 1, name=body["name"], tvs=1)
            wall["video"] = {"media_id": media["id"], "name": media["name"], "url": media["url"],
                             "mime_type": media["mime_type"], "width_px": 3840, "height_px": 720}
            self.data["walls"].insert(0, wall)
            return 201, copy.deepcopy(wall)
        wall = next((w for w in self.data["walls"] if w["id"] == int(tvs.group(1))), None)
        if wall is None:
            return 404, {"detail": {"code": "wall.not_found", "message": "Wall not found"}}
        col = len(wall["cells"])
        cell = free_wall(wall_id=wall["id"], tvs=col + 1)["cells"][col]
        cell.update(screen_id=300 + col, online=False, screen_w_px=None, screen_h_px=None)
        wall["cells"].append(cell)
        wall["cols"] = col + 1
        return 201, copy.deepcopy(cell)


def open_pair(browser, path="/pair?code=ABCD23", device="desktop", lang="en", token="TA", settle=1200,
              init_script=None, fixture=PairFixture, **fx_kwargs):
    """Like open_page, for any fixture class, with an optional page script
    that runs before the dashboard's own (seeding storage, watching XHRs)."""
    fx = fixture(locale=lang, **fx_kwargs)
    ctx = new_context(browser, device, lang=lang, token=token)
    install_api(ctx, fx)
    if init_script:
        ctx.add_init_script(init_script)
    page = ctx.new_page()
    page.set_default_timeout(5000)   # a missing element fails the flow in 5 s, not 30
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + path); page.wait_for_timeout(settle)
    return ctx, page, fx, errors


def remembered(wall_id, minutes_ago=5, org=1, store="localStorage"):
    """Page script: this phone set up or joined `wall_id` `minutes_ago` minutes ago."""
    return ("try { window.%s.setItem('khan_wallsetup_wall:%d', JSON.stringify({ wall_id: %d, at: Date.now() - %d })); } catch (e) {}"
            % (store, org, wall_id, minutes_ago * 60000))


# Page script: storage that refuses the remembered wall (a private window,
# blocked site data). Every other key still works, so the sign-in holds.
BLOCK_MEMORY = """(() => {
  const S = Storage.prototype, get = S.getItem, set = S.setItem;
  S.getItem = function (k) { if (String(k).indexOf('khan_wallsetup_wall:') === 0) throw new Error('blocked'); return get.call(this, k); };
  S.setItem = function (k, v) { if (String(k).indexOf('khan_wallsetup_wall:') === 0) throw new Error('blocked'); return set.call(this, k, v); };
})();"""

# Page script: record every XMLHttpRequest (method, address, headers, the
# FormData's "file") in window.__xhr, and while window.__holdXhr is true hold
# its send(): window.__held[i]() sends it, window.__heldXhr[i] is the request.
WATCH_XHR = """(() => {
  window.__xhr = []; window.__held = []; window.__heldXhr = []; window.__holdXhr = false;
  const P = XMLHttpRequest.prototype, open = P.open, setHeader = P.setRequestHeader, send = P.send;
  P.open = function (method, url) { this.__rec = { method: method, url: String(url), headers: {} }; return open.apply(this, arguments); };
  P.setRequestHeader = function (name, value) { if (this.__rec) this.__rec.headers[String(name).toLowerCase()] = value; return setHeader.apply(this, arguments); };
  P.send = function (body) {
    const xhr = this, file = body instanceof FormData ? body.get('file') : null;
    if (xhr.__rec) { xhr.__rec.form = body instanceof FormData; xhr.__rec.file = file ? file.name : null; xhr.__rec.size = file ? file.size : null; window.__xhr.push(xhr.__rec); }
    if (!window.__holdXhr) return send.call(xhr, body);
    window.__heldXhr.push(xhr); window.__held.push(() => send.call(xhr, body));
  };
})();"""


def to_wall_steps(page):
    """On /pair with the code filled in: Part of a video wall, then Continue."""
    page.check("#pair-kind-wall")
    page.click("#pair-submit"); page.wait_for_timeout(600)


def screen_claims(fx):
    """Writes that would pair the TV as a screen of its own."""
    return [c for c in fx.calls if c[1] in ("/screens", "/screens/claim")]


def wall_choices(page):
    return page.evaluate("""() => [...document.querySelectorAll('#wall-setup-choose input[name="wall-setup-wall"]')]
        .map(i => ({ value: i.value, checked: i.checked, disabled: i.disabled,
                     name: i.closest('label').querySelector('.wall-setup-choice-name').textContent.trim(),
                     meta: i.closest('label').querySelector('.wall-setup-choice-meta').textContent.trim() }))""")


def memory(page, org=1):
    return page.evaluate("(k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return 'blocked'; } }",
                         f"khan_wallsetup_wall:{org}")


def address(page):
    return page.evaluate("location.pathname + location.search")


def pick_promo_from_library(page):
    page.click("#wall-setup-library"); page.wait_for_timeout(600)
    page.click(".media-picker-thumb[data-media-id='3']")
    page.click(".media-picker-confirm"); page.wait_for_timeout(600)


@flow("pair: What is this TV? appears once the code is complete, and Its own screen pairs as before")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, path="/pair")
    assert views(page) == ["pair-view"], views(page)
    assert not page.is_visible("#pair-kind")
    page.fill("#pair-code-input", "abcd2"); page.wait_for_timeout(100)
    assert not page.is_visible("#pair-kind")
    page.fill("#pair-code-input", "abcd23"); page.wait_for_timeout(100)
    assert page.is_visible("#pair-kind")
    assert page.inner_text("#pair-kind legend").strip() == "What is this TV?"
    assert page.is_checked("#pair-kind-screen") and not page.is_checked("#pair-kind-wall")
    assert page.is_visible("#pair-existing-select")
    assert page.inner_text("#pair-submit").strip() == "Pair display"
    page.select_option("#pair-existing-select", "1")
    page.click("#pair-submit"); page.wait_for_timeout(600)
    assert ("POST", "/screens/claim", {"code": "ABCD23", "screen_id": 1}) in fx.calls, fx.calls
    assert page.is_visible("#pair-success")
    assert not [c for c in fx.calls if c[1].startswith("/walls")], fx.calls
    assert not errors, errors
    ctx.close()


@flow("pair: Part of a video wall hides the screen picker and says Continue; Enter goes to Which wall? and claims nothing")
def _(browser):
    ctx, page, fx, errors = open_pair(browser)
    page.check("#pair-kind-wall"); page.wait_for_timeout(100)
    assert not page.is_visible("#pair-existing-select")
    assert not page.is_visible("#pair-target-new")
    assert page.inner_text("#pair-submit").strip() == "Continue"
    assert page.is_enabled("#pair-submit")
    page.press("#pair-code-input", "Enter"); page.wait_for_timeout(600)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-title") == "Which wall?"
    assert screen_claims(fx) == [], fx.calls
    assert address(page) == "/pair?code=ABCD23", page.url
    assert not errors, errors
    ctx.close()


@flow("pair: Back from Which wall? returns to What is this TV?, on Its own screen when this phone has no recent wall")
def _(browser):
    ctx, page, fx, errors = open_pair(browser)
    to_wall_steps(page)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-back").strip() == "Back"
    page.click("#wall-setup-back"); page.wait_for_timeout(800)
    assert views(page) == ["pair-view"], views(page)
    assert page.input_value("#pair-code-input") == "ABCD23"
    assert page.is_checked("#pair-kind-screen")
    assert page.is_visible("#pair-existing-select")
    assert page.inner_text("#pair-submit").strip() == "Pair display"
    assert page.evaluate("document.getElementById('wall-setup-view').children.length") == 0
    assert screen_claims(fx) == [], fx.calls
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: Which wall? lists only phone-made walls, newest first, with New video wall preselected")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[free_wall(wall_id=7, name="Lobby wall"),
                                                           free_wall(wall_id=9, name="Atrium wall", tvs=1)])
    to_wall_steps(page)
    assert page.inner_text("#wall-setup-sub") == "Add this TV to one of your video walls, or start a new one."
    assert wall_choices(page) == [
        {"value": "9", "checked": False, "disabled": False, "name": "Atrium wall", "meta": "1 TV"},
        {"value": "7", "checked": False, "disabled": False, "name": "Lobby wall", "meta": "2 TVs"},
        {"value": "new", "checked": True, "disabled": False, "name": "New video wall", "meta": "You'll choose its video next."},
    ], wall_choices(page)   # the grid wall "Menu wall" is not offered
    assert page.inner_text("#wall-setup-next").strip() == "Next"
    page.check("#wall-setup-choose input[value='7']")
    assert page.inner_text("#wall-setup-next").strip() == "Add this TV"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: the wall this phone used in the last 30 minutes is preselected, for this organization, if it has room")
def _(browser):
    cases = ((remembered(7), "7"), (remembered(7, store="sessionStorage"), "7"),
             (remembered(7, minutes_ago=31), "new"), (remembered(7, org=2), "new"), (remembered(99), "new"))
    for script, want in cases:
        ctx, page, fx, errors = open_pair(browser, init_script=script)
        to_wall_steps(page)
        checked = [c["value"] for c in wall_choices(page) if c["checked"]]
        assert checked == [want], (script, checked)
        assert not errors, (script, errors)
        ctx.close()
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7), free_walls=[free_wall(wall_id=7, tvs=8)])
    to_wall_steps(page)
    ch = wall_choices(page)
    assert ch[0] == {"value": "7", "checked": False, "disabled": True, "name": "Lobby wall", "meta": "8 TVs · Full"}, ch
    assert ch[1]["value"] == "new" and ch[1]["checked"], ch
    assert not errors, errors
    ctx.close()


@flow("pair: a wall this phone used in the last 30 minutes preselects Part of a video wall, so the next TV is Continue, then Add this TV")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7))
    assert page.is_checked("#pair-kind-wall") and not page.is_checked("#pair-kind-screen")
    assert page.evaluate("WallSetup.recentWallId()") == 7
    assert page.is_visible("#pair-kind")
    assert not page.is_visible("#pair-existing-select")
    assert page.inner_text("#pair-submit").strip() == "Continue"
    page.click("#pair-submit"); page.wait_for_timeout(600)                     # tap 1
    assert page.inner_text("#wall-setup-title") == "Which wall?"
    assert [c["value"] for c in wall_choices(page) if c["checked"]] == ["7"]
    assert page.inner_text("#wall-setup-next").strip() == "Add this TV"
    page.click("#wall-setup-next"); page.wait_for_timeout(1000)                # tap 2
    assert ("POST", "/walls/7/tvs", {"code": "ABCD23"}) in fx.calls, fx.calls
    on_wall_page(page)
    assert not errors, errors
    ctx.close()
    # A memory older than 30 minutes, or another organization's, leaves Its own screen.
    for script in (remembered(7, minutes_ago=31), remembered(7, org=2)):
        ctx, page, fx, errors = open_pair(browser, init_script=script)
        assert page.is_checked("#pair-kind-screen") and not page.is_checked("#pair-kind-wall"), script
        assert page.evaluate("WallSetup.recentWallId()") is None, script
        assert page.is_visible("#pair-existing-select"), script
        assert page.inner_text("#pair-submit").strip() == "Pair display", script
        assert not errors, (script, errors)
        ctx.close()
    # Without a complete code the step stays hidden and the form is the screen form, memory or not.
    ctx, page, fx, errors = open_pair(browser, path="/pair", init_script=remembered(7))
    assert not page.is_visible("#pair-kind")
    assert page.is_visible("#pair-existing-select")
    assert page.inner_text("#pair-submit").strip() == "Pair display"
    page.fill("#pair-code-input", "abcd23"); page.wait_for_timeout(100)
    assert page.is_visible("#pair-kind") and page.is_checked("#pair-kind-wall")
    assert page.inner_text("#pair-submit").strip() == "Continue"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: Add this TV claims the code into that wall, remembers it and opens its page in place of the steps")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7))
    h0 = page.evaluate("history.length")
    to_wall_steps(page)
    page.click("#wall-setup-next"); page.wait_for_timeout(1000)
    assert ("POST", "/walls/7/tvs", {"code": "ABCD23"}) in fx.calls, fx.calls
    assert screen_claims(fx) == [], fx.calls
    assert "/screens/poll/ABCD23" in fx.gets, fx.gets   # the code was checked before it was claimed (R15)
    on_wall_page(page)
    assert address(page) == "/wall-setup?wall=7&tv=3", page.url   # PairFixture adds the TV as col 2: TV 3
    # R19: startFromPair pushed one entry at /pair, and the wall's address replaced it.
    assert page.evaluate("history.length") == h0 + 1, (h0, page.evaluate("history.length"))
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    assert page.query_selector("#wall-setup-editor") is not None
    m = memory(page)
    assert m["wall_id"] == 7 and page.evaluate("Date.now()") - m["at"] < 60000, m   # written now, not the seeded 5 min ago
    page.go_back(); page.wait_for_timeout(800)   # the steps left no history entries of their own
    assert views(page) == ["pair-view"], views(page)
    assert address(page) == "/pair?code=ABCD23", page.url
    assert page.is_checked("#pair-kind-wall")   # this phone has just joined wall 7 (R13)
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a new wall from the library: name prefilled, one pick only, created with this TV's code")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    to_wall_steps(page)
    assert page.inner_text("#wall-setup-title") == "New video wall"   # no phone-made wall yet: straight to a new one
    assert page.input_value("#wall-setup-name") == "Video wall"
    assert page.inner_text("#wall-setup-chosen").strip() == "No video chosen yet."
    assert not page.is_enabled("#wall-setup-create")
    page.click("#wall-setup-library"); page.wait_for_timeout(600)
    assert page.is_visible(".media-picker-modal")
    shown = page.evaluate("[...document.querySelectorAll('.media-picker-thumb')].map(t => Number(t.dataset.mediaId)).sort((a, b) => a - b)")
    assert shown == [1, 2, 3, 6], shown   # images and the video; not the PDF or the web page
    # MediaPicker picks several; a wall plays one, so two picks are refused, not cut to the first.
    page.click(".media-picker-thumb[data-media-id='3']"); page.click(".media-picker-thumb[data-media-id='1']")
    page.click(".media-picker-confirm"); page.wait_for_timeout(600)
    assert not page.is_visible(".media-picker-modal")
    assert page.inner_text("#wall-setup-form-error") == "A video wall plays one video or image. Pick just one."
    assert page.inner_text("#wall-setup-chosen").strip() == "No video chosen yet."
    assert not page.is_enabled("#wall-setup-create")
    pick_promo_from_library(page)
    assert not page.is_visible("#wall-setup-form-error")
    assert page.inner_text("#wall-setup-chosen-name") == "promo.mp4"
    assert page.is_enabled("#wall-setup-create")
    page.click("#wall-setup-create"); page.wait_for_timeout(1000)
    assert ("POST", "/walls/free", {"name": "Video wall", "media_id": 3, "code": "ABCD23"}) in fx.calls, fx.calls
    assert screen_claims(fx) == [], fx.calls
    on_wall_page(page)
    assert address(page) == "/wall-setup?wall=2&tv=1", page.url   # the new wall's first TV
    assert page.inner_text("#wall-setup-title") == "Video wall"
    assert memory(page)["wall_id"] == 2
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: the default name skips taken names, and Back from the new wall keeps the draft")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[free_wall(wall_id=7, name="Video wall"),
                                                           free_wall(wall_id=8, name="video wall 2")])
    to_wall_steps(page)
    page.click("#wall-setup-next"); page.wait_for_timeout(300)   # New video wall is preselected
    assert page.inner_text("#wall-setup-title") == "New video wall"
    assert page.input_value("#wall-setup-name") == "Video wall 3"
    page.fill("#wall-setup-name", "Atrium")
    page.click("#wall-setup-back"); page.wait_for_timeout(300)
    assert page.inner_text("#wall-setup-title") == "Which wall?"
    page.click("#wall-setup-next"); page.wait_for_timeout(300)
    assert page.input_value("#wall-setup-name") == "Atrium"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: Upload from this phone sends the file by XHR with the session and shows its progress")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[], init_script=WATCH_XHR)
    to_wall_steps(page)
    page.evaluate("() => { window.__holdXhr = true; }")
    with page.expect_file_chooser() as chooser:
        page.click("#wall-setup-upload")
    chooser.value.set_files(files=[{"name": "wall.mp4", "mimeType": "video/mp4", "buffer": b"not really a video"}])
    page.wait_for_timeout(600)   # the code check (a fetch, not an XHR), then the upload starts and is held
    assert page.evaluate("window.__xhr") == [{"method": "POST", "url": "https://api.test/media/upload",
                                             "headers": {"authorization": "Bearer TA"},
                                             "form": True, "file": "wall.mp4", "size": 18}], page.evaluate("window.__xhr")
    page.evaluate("() => window.__heldXhr[0].upload.dispatchEvent(new ProgressEvent('progress', { lengthComputable: true, loaded: 42, total: 100 }))")
    assert page.is_visible("#wall-setup-progress")
    assert page.inner_text("#wall-setup-progress-text") == "Uploading… 42%"
    assert page.evaluate("document.getElementById('wall-setup-progress-bar').value") == 42
    for sel in ("#wall-setup-create", "#wall-setup-library", "#wall-setup-upload"):
        assert not page.is_enabled(sel), sel
    page.evaluate("() => { window.__holdXhr = false; window.__held.shift()(); }"); page.wait_for_timeout(600)
    assert not page.is_visible("#wall-setup-progress")
    assert page.inner_text("#wall-setup-chosen-name") == "wall.mp4"
    assert ("POST", "/media/upload") in [(c[0], c[1]) for c in fx.calls], fx.calls
    page.click("#wall-setup-create"); page.wait_for_timeout(1000)
    assert ("POST", "/walls/free", {"name": "Video wall", "media_id": 201, "code": "ABCD23"}) in fx.calls, fx.calls
    assert address(page) == "/wall-setup?wall=2&tv=1", page.url
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a video over the upload limit is explained, and no wall is created")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    fx.write_errors[("POST", "/media/upload")] = (413, {"detail": "File too large"})
    to_wall_steps(page)
    page.set_input_files("#wall-setup-file", files=[{"name": "big.mp4", "mimeType": "video/mp4", "buffer": b"0" * 64}])
    page.wait_for_timeout(600)
    assert page.inner_text("#wall-setup-form-error") == "That video is too large to upload. Try a shorter or smaller one."
    assert not page.is_visible("#wall-setup-progress")
    assert not page.is_enabled("#wall-setup-create")
    assert not [c for c in fx.calls if c[1] == "/walls/free"], fx.calls
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a file that is neither a video nor an image is refused before upload")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    to_wall_steps(page)
    page.set_input_files("#wall-setup-file", files=[{"name": "menu.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"}])
    page.wait_for_timeout(300)
    assert page.inner_text("#wall-setup-form-error") == "A video wall plays a video or an image. Pick one of those."
    assert not [c for c in fx.calls if c[1] == "/media/upload"], fx.calls
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a 401 on the upload signs the tab out like any other request")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    fx.write_errors[("POST", "/media/upload")] = (401, {"detail": "Not authenticated"})
    to_wall_steps(page)
    page.set_input_files("#wall-setup-file", files=[{"name": "wall.mp4", "mimeType": "video/mp4", "buffer": b"x" * 8}])
    page.wait_for_timeout(800)
    assert views(page) == ["auth-panel"], views(page)
    assert page.evaluate("document.getElementById('wall-setup-view').children.length") == 0
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: refused codes, the plan limit and a missing permission read like /pair")
def _(browser):
    cases = (((404, {"detail": {"code": "pair.code_unknown", "message": "Unknown pairing code"}}),
              "That code isn't recognised. Check the TV screen and try again."),
             ((400, {"detail": {"code": "pair.code_expired", "message": "Pairing code expired"}}),
              "Code expired. Refresh the TV to get a new one."),
             ((409, {"detail": {"code": "pair.code_used", "message": "Pairing code already used"}}),
              "That code's been used. Refresh the TV to get a new one."),
             ((402, {"detail": {"code": "plan_limit", "message": "Screen limit reached"}}),
              "You've reached your plan's screen limit. Upgrade to add more."),
             ((403, {"detail": "Insufficient role"}),
              "Your account doesn't have permission to pair displays."))
    for answer, text in cases:
        ctx, page, fx, errors = open_pair(browser, init_script=remembered(7))
        fx.write_errors[("POST", "/walls/7/tvs")] = answer
        to_wall_steps(page)
        page.click("#wall-setup-next"); page.wait_for_timeout(600)
        assert page.inner_text("#wall-setup-form-error") == text, (answer, page.inner_text("#wall-setup-form-error"))
        assert page.inner_text("#wall-setup-title") == "Which wall?"
        assert address(page) == "/pair?code=ABCD23", (answer, page.url)
        assert not errors, (answer, errors)
        ctx.close()
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    fx.write_errors[("POST", "/walls/free")] = (409, {"detail": {"code": "pair.code_used", "message": "Pairing code already used"}})
    to_wall_steps(page)
    pick_promo_from_library(page)
    page.click("#wall-setup-create"); page.wait_for_timeout(600)
    assert page.inner_text("#wall-setup-form-error") == "That code's been used. Refresh the TV to get a new one."
    assert page.inner_text("#wall-setup-title") == "New video wall"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: the TV's code is checked before an upload and before it is claimed, and a refused code stops there")
def _(browser):
    unknown = (404, {"detail": "Unknown pairing code"})
    expired = (200, {"status": "expired"})
    sent = lambda fx: [c for c in fx.calls if c[1] in ("/media/upload", "/walls/free") or c[1].endswith("/tvs")]
    # Upload from this phone: nothing is sent for a code the TV no longer shows.
    ctx, page, fx, errors = open_pair(browser, free_walls=[], get_errors={"/screens/poll/ABCD23": unknown})
    to_wall_steps(page)
    page.set_input_files("#wall-setup-file", files=[{"name": "wall.mp4", "mimeType": "video/mp4", "buffer": b"x" * 8}])
    page.wait_for_timeout(600)
    assert page.inner_text("#wall-setup-form-error") == "That code isn't recognised. Check the TV screen and try again."
    assert "/screens/poll/ABCD23" in fx.gets, fx.gets
    assert sent(fx) == [], fx.calls
    assert not page.is_visible("#wall-setup-progress")
    assert page.is_enabled("#wall-setup-upload") and page.is_enabled("#wall-setup-library")
    assert not errors, errors
    ctx.close()
    # Create, with a video from the library: the code expired while the person chose.
    ctx, page, fx, errors = open_pair(browser, free_walls=[])
    to_wall_steps(page)
    pick_promo_from_library(page)
    fx.get_errors["/screens/poll/ABCD23"] = expired
    page.click("#wall-setup-create"); page.wait_for_timeout(800)
    assert page.inner_text("#wall-setup-form-error") == "Code expired. Refresh the TV to get a new one."
    assert sent(fx) == [], fx.calls
    assert page.inner_text("#wall-setup-title") == "New video wall"
    assert page.is_enabled("#wall-setup-create")
    assert not errors, errors
    ctx.close()
    # Add this TV to a wall: the same check, the same words.
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7), get_errors={"/screens/poll/ABCD23": expired})
    to_wall_steps(page)
    page.click("#wall-setup-next"); page.wait_for_timeout(800)
    assert page.inner_text("#wall-setup-form-error") == "Code expired. Refresh the TV to get a new one."
    assert sent(fx) == [], fx.calls
    assert page.inner_text("#wall-setup-title") == "Which wall?"
    assert address(page) == "/pair?code=ABCD23", page.url
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a wall that filled up meanwhile is explained and the list read again")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7))
    to_wall_steps(page)
    # Another phone fills the wall while this one is choosing.
    fx.write_errors[("POST", "/walls/7/tvs")] = (409, {"detail": {"code": "wall.full", "message": "Full"}})
    fx.data["walls"] = [free_wall(wall_id=7, tvs=8) if w["id"] == 7 else w for w in fx.data["walls"]]
    before = fx.gets.count("/walls")
    page.click("#wall-setup-next"); page.wait_for_timeout(800)
    assert page.inner_text("#wall-setup-form-error") == "This wall already has 8 TVs, the most a wall can have."
    assert fx.gets.count("/walls") == before + 1, fx.gets
    ch = wall_choices(page)
    assert ch[0]["value"] == "7" and ch[0]["disabled"], ch
    assert [c["value"] for c in ch if c["checked"]] == ["new"], ch
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: when the walls can't be read, Try again reads them again")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, fail_get=("/walls",))
    to_wall_steps(page)
    assert page.inner_text("#wall-setup-error") == "The server ran into a problem. Try again in a moment."
    fx.fail_get = ()
    page.click("#wall-setup-retry"); page.wait_for_timeout(600)
    assert page.inner_text("#wall-setup-title") == "Which wall?"
    assert page.query_selector("#wall-setup-error") is None
    assert [c["value"] for c in wall_choices(page)] == ["7", "new"]
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: storage that refuses the remembered wall changes nothing else")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, init_script=BLOCK_MEMORY)
    to_wall_steps(page)
    assert [c["value"] for c in wall_choices(page) if c["checked"]] == ["new"]
    page.check("#wall-setup-choose input[value='7']")
    page.click("#wall-setup-next"); page.wait_for_timeout(1000)
    assert address(page) == "/wall-setup?wall=7&tv=3", page.url
    assert memory(page) == "blocked"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: a viewer is told the steps are not for them, and nothing is claimed")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, role="viewer")
    to_wall_steps(page)
    on_wall_page(page)
    assert page.inner_text("#wall-setup-error") == "Your account can't set up video walls. Ask an admin or an editor."
    assert not [c for c in fx.calls if c[0] == "POST"], fx.calls
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: in Arabic the steps read in Arabic")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, lang="ar")
    assert page.inner_text("#pair-kind legend").strip() == "ما دور هذه الشاشة؟"
    page.check("#pair-kind-wall")
    assert page.inner_text("#pair-submit").strip() == "متابعة"
    page.click("#pair-submit"); page.wait_for_timeout(600)
    assert page.evaluate("document.documentElement.dir") == "rtl"
    assert page.inner_text("#wall-setup-title") == "أي جدار؟"
    ch = wall_choices(page)
    assert ch[0]["meta"] == "شاشتان" and ch[-1]["name"] == "جدار شاشات جديد", ch
    assert page.inner_text("#wall-setup-next").strip() == "التالي"
    page.click("#wall-setup-next"); page.wait_for_timeout(300)
    assert page.input_value("#wall-setup-name") == "جدار شاشات"
    assert page.inner_text("#wall-setup-upload").strip() == "ارفع من هذا الهاتف"
    assert not errors, errors
    ctx.close()


@flow("wall setup from /pair: on phones each step fits and every choice is finger-sized")
def _(browser):
    def fits(page, where):
        m = page.evaluate("() => ({ w: innerWidth, sw: document.documentElement.scrollWidth })")
        assert m["sw"] <= m["w"] + 1, (where, m)

    def big(page, selectors, where):
        for sel in selectors:
            els = page.query_selector_all(sel)
            assert els, (where, sel)
            for el in els:
                b = el.bounding_box()
                assert b["height"] >= 44 and b["width"] >= 44, (where, sel, b)

    for device in ("iphone", "android360"):
        for lang in ("en", "ar"):
            where = (device, lang)
            ctx, page, fx, errors = open_pair(browser, device=device, lang=lang)
            big(page, ("#pair-kind-screen", "#pair-kind-wall"), where)
            fits(page, where)
            to_wall_steps(page)
            big(page, ("#wall-setup-choose input[name='wall-setup-wall']", "#wall-setup-next", "#wall-setup-back"), where)
            fits(page, where)
            page.click("#wall-setup-next"); page.wait_for_timeout(300)   # New video wall
            big(page, ("#wall-setup-name", "#wall-setup-library", "#wall-setup-upload", "#wall-setup-create"), where)
            assert page.evaluate("parseFloat(getComputedStyle(document.getElementById('wall-setup-name')).fontSize)") >= 16, where
            fits(page, where)
            assert page.is_visible("#tab-bar"), where
            assert not errors, (where, errors)
            ctx.close()


if __name__ == "__main__":
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && bash scripts/ui/run.sh wall_setup_flows.py`
Expected: FAIL, exit code 1, `21 failed`. The 11 Task 12 flows still print `PASS wall setup: ...`. The 21 new flows print `FAIL`: the first with `AssertionError()` once the code is complete (there is no `#pair-kind`); the Arabic one with `TimeoutError('Page.inner_text: Timeout 5000ms exceeded. ... waiting for locator("#pair-kind legend")')`; the preselection one with `TimeoutError('Page.is_checked: ...')` on `#pair-kind-wall`; the phone one with `AssertionError((('iphone', 'en'), '#pair-kind-screen'))`; the other 17 with `TimeoutError('Page.check: Timeout 5000ms exceeded. ... waiting for locator("#pair-kind-wall")')`. (Checked while planning: Tasks 11-12 from the plan plus this step, in a throwaway `signage_renderer:latest` container.)

- [ ] **Step 4: Add "What is this TV?" to the pair form (`frontend/index.html`)**

In `frontend/index.html`, replace:

```html
          <fieldset class="pair-field pair-choice">
```

with:

```html
          <!-- "What is this TV?": shown once the code is complete (app.js
               updatePairKindStep). Its own screen keeps the form below;
               Part of a video wall continues in wall-setup.js. -->
          <fieldset id="pair-kind" class="pair-field pair-kind hidden">
            <legend class="pair-label" data-i18n="wallsetup.kind_question">What is this TV?</legend>
            <div class="pair-kind-options">
              <label class="pair-kind-option">
                <input type="radio" name="pair-kind" id="pair-kind-screen" value="screen" checked />
                <span class="pair-kind-card">
                  <span class="pair-kind-title" data-i18n="wallsetup.kind_screen">Its own screen</span>
                  <span class="pair-kind-hint" data-i18n="wallsetup.kind_screen_hint">It shows its own playlist or schedule.</span>
                </span>
              </label>
              <label class="pair-kind-option">
                <input type="radio" name="pair-kind" id="pair-kind-wall" value="wall" />
                <span class="pair-kind-card">
                  <span class="pair-kind-title" data-i18n="wallsetup.kind_wall">Part of a video wall</span>
                  <span class="pair-kind-hint" data-i18n="wallsetup.kind_wall_hint">One video plays across several TVs.</span>
                </span>
              </label>
            </div>
          </fieldset>

          <fieldset class="pair-field pair-choice">
```

(No element with an id sits inside a `data-i18n` element: check 5 of `check_ui_contract.sh` stays green.)

- [ ] **Step 5: Wire the step into the pair form (`frontend/app.js`)**

In `frontend/app.js`, replace:

```js
function updatePairSubmitEnabled() {
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
  let ok = PAIR_CODE_RE.test(code);
  if (target === "existing") {
```

with:

```js
/** "What is this TV?" (#pair-kind): shown once the code is complete. "Part
 *  of a video wall" hides the screen picker and turns the button into
 *  Continue; wall-setup.js asks the rest. Until the code is complete the
 *  form is the screen form, even when showPairView preselected the wall.
 *  Returns whether the wall branch is on (code complete, wall chosen). */
function updatePairKindStep(codeOk) {
  const wall = codeOk && document.getElementById("pair-kind-wall").checked;
  document.getElementById("pair-kind").classList.toggle("hidden", !codeOk);
  document.getElementById("pair-target-existing").closest("fieldset").classList.toggle("hidden", wall);
  const btn = document.getElementById("pair-submit");
  const key = wall ? "wallsetup.kind_continue" : "pair.submit";
  if (btn.dataset.i18n !== key) {
    btn.dataset.i18n = key;   // so a later applyTranslations keeps these words
    btn.textContent = wall ? Khan.t(key, "Continue") : Khan.t(key, "Pair display");
  }
  return wall;
}

function updatePairSubmitEnabled() {
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
  let ok = PAIR_CODE_RE.test(code);
  if (updatePairKindStep(ok)) {
    // The video-wall branch needs only the code.
    document.getElementById("pair-submit").disabled = !ok;
    return;
  }
  if (target === "existing") {
```

In `frontend/app.js`, replace:

```js
  newNameInput.value = "";
```

with:

```js
  newNameInput.value = "";
  // "What is this TV?": Part of a video wall when this phone set up or joined
  // a wall in the last 30 minutes (ruling R13: the next TV is then Continue,
  // Add this TV), else Its own screen. Pair another display, Back from the
  // video-wall steps and the sign-in resume all come through here.
  let recentWall = null;
  try { recentWall = WallSetup.recentWallId(); } catch (_) { /* wall-setup.js not loaded: Its own screen */ }
  document.getElementById(recentWall !== null ? "pair-kind-wall" : "pair-kind-screen").checked = true;
```

(`showPairView` ends with `updatePairSubmitEnabled()`, which then shows the step and, for Its own screen, the screen picker and "Pair display" again. On a cold `/pair` load `boot()` has set `currentUser` from `/auth/me` before it calls `showPairView`, so `recentWallId()` already knows the organization, and wall-setup.js has run by then: `boot()` awaits `/auth/me` and the locale first, the same assumption Task 12 makes for `/wall-setup`.)

In `frontend/app.js`, replace:

```js
document.getElementById("pair-new-name")      .addEventListener("input",  updatePairSubmitEnabled);
```

with:

```js
document.getElementById("pair-new-name")      .addEventListener("input",  updatePairSubmitEnabled);

// "What is this TV?": Its own screen keeps the form below it; Part of a video
// wall hides it and turns the button into Continue (updatePairKindStep).
document.querySelectorAll('input[name="pair-kind"]').forEach((el) => {
  el.addEventListener("change", () => {
    clearPairError();
    updatePairSubmitEnabled();
  });
});
```

In `frontend/app.js`, replace:

```js
async function onPairSubmit(e) {
  e.preventDefault();
  clearPairError();
  const btn = document.getElementById("pair-submit");
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
```

with:

```js
async function onPairSubmit(e) {
  e.preventDefault();
  clearPairError();
  const btn = document.getElementById("pair-submit");
  const code = normalizePairCode(document.getElementById("pair-code-input").value);
  // "Part of a video wall": wall-setup.js claims the code into a wall
  // (POST /walls/free or /walls/{id}/tvs), so nothing here may claim it
  // first. Enter in the code field submits this form too.
  if (document.getElementById("pair-kind-wall").checked) {
    if (!PAIR_CODE_RE.test(code)) {
      showPairError(Khan.t("pair.error.bad_code", "Enter the full code shown on the TV."));
      return;
    }
    WallSetup.startFromPair(code);
    return;
  }
  const target = document.querySelector('input[name="pair-target"]:checked')?.value;
```

- [ ] **Step 6: Implement the video-wall steps (`frontend/wall-setup.js`)**

In `frontend/wall-setup.js`, replace:

```js
  /** The page frame: Back, the title, an optional line under it, then the body. */
  function renderFrame(title, sub, bodyHtml) {
    view().innerHTML = `
      <header class="wall-setup-head">
        <button type="button" id="wall-setup-back" class="btn btn-quiet wall-setup-back">
          <svg class="icon" aria-hidden="true"><use href="#i-chevron-right"/></svg>
          <span>${escHtml(Khan.t("wallsetup.back", "Back to video walls"))}</span>
        </button>
        <h1 id="wall-setup-title" class="wall-setup-title" dir="auto">${escHtml(title)}</h1>
        ${sub ? `<p id="wall-setup-sub" class="wall-setup-sub">${escHtml(sub)}</p>` : ""}
      </header>
      <div id="wall-setup-body" class="wall-setup-body">${bodyHtml}</div>`;
    document.getElementById("wall-setup-back").addEventListener("click", () => showSection("walls"));
  }
```

with:

```js
  /** The page frame: Back, the title, an optional line under it, then the body.
   *  `back` ({label, go}) replaces "Back to video walls": the steps that start
   *  on /pair go back to the step before them. */
  function renderFrame(title, sub, bodyHtml, back = null) {
    const backLabel = back ? back.label : Khan.t("wallsetup.back", "Back to video walls");
    view().innerHTML = `
      <header class="wall-setup-head">
        <button type="button" id="wall-setup-back" class="btn btn-quiet wall-setup-back">
          <svg class="icon" aria-hidden="true"><use href="#i-chevron-right"/></svg>
          <span>${escHtml(backLabel)}</span>
        </button>
        <h1 id="wall-setup-title" class="wall-setup-title" dir="auto">${escHtml(title)}</h1>
        ${sub ? `<p id="wall-setup-sub" class="wall-setup-sub">${escHtml(sub)}</p>` : ""}
      </header>
      <div id="wall-setup-body" class="wall-setup-body">${bodyHtml}</div>`;
    document.getElementById("wall-setup-back").addEventListener("click", back ? back.go : () => showSection("walls"));
  }
```

In `frontend/wall-setup.js`, replace:

```js
  function show(wallId) {
    const id = parseWallId(wallId);
```

with:

```js
  function show(wallId) {
    stopFlow();   // the steps from /pair end where a wall's page begins
    const id = parseWallId(wallId);
```

In `frontend/wall-setup.js`, replace:

```js
  function reset() {
    gen++;
```

with:

```js
  function reset() {
    stopFlow();
    gen++;
```

In `frontend/wall-setup.js`, replace:

```js
  return { show, reset };
})();
```

with:

```js
  // ── From /pair: "Part of a video wall" ─────────────────────────────────
  // The TV's code is claimed straight into a wall: POST /walls/free starts a
  // new wall with it, POST /walls/{id}/tvs adds it to one. app.js's pair form
  // hands the code over (onPairSubmit) before it claims anything itself.

  const MAX_TVS = 8;                            // backend wall_layout.MAX_TVS
  const REMEMBER_KEY = "khan_wallsetup_wall:";  // + the organization's id
  const REMEMBER_MS = 30 * 60 * 1000;           // a wall set up or joined in the last 30 minutes
  // Kept in both stores: sessionStorage is this tab's own, but a phone's
  // camera app usually opens the next TV's QR link in a new tab, which starts
  // with an empty sessionStorage. This tab's own memory is read first.
  const REMEMBER_STORES = ["sessionStorage", "localStorage"];

  // The steps while they are on the page, else null:
  // { code, walls: all the organization's walls (for the default name),
  //   free: the phone-made ones, newest first, choice: a wall id | "new" | null,
  //   name: the new wall's name, media: its media row, upload: the upload in flight }
  let flow = null;

  /** Abort an upload in flight; its answer is then ignored. */
  function stopUpload() {
    if (!flow || !flow.upload) return;
    const upload = flow.upload;
    flow.upload = null;
    upload.abort();
  }

  /** End the steps: their state, and an upload still in flight. */
  function stopFlow() {
    stopUpload();
    flow = null;
  }

  /** `fn` as an event handler that never leaves a rejection for the global
   *  error toast: a bug inside a step is logged and said in the step. */
  function guard(fn) {
    return (...args) => Promise.resolve()
      .then(() => fn(...args))
      .catch((err) => {
        console.error(err);
        if (flow) showFormError(Khan.t("error.generic", "Something went wrong. Please try again."));
      });
  }

  /** The organization this tab is signed in to: app.js's state.org (the
   *  sign-in's answer or GET /organization), else /auth/me's organization. */
  function orgId() {
    const org = state.org || (currentUser && currentUser.organization) || null;
    return org && org.id != null ? String(org.id) : null;
  }

  function rememberWall(wallId) {
    const org = orgId();
    if (!org) return;
    const value = JSON.stringify({ wall_id: wallId, at: Date.now() });
    REMEMBER_STORES.forEach((name) => {
      try { window[name].setItem(REMEMBER_KEY + org, value); } catch (_) { /* storage blocked: not remembered */ }
    });
  }

  /** The wall this phone set up or joined in the last 30 minutes, for this
   *  organization, or null. Exported: app.js's showPairView preselects "Part
   *  of a video wall" for it (ruling R13), and Which wall? preselects it. */
  function recentWallId() {
    const org = orgId();
    if (!org) return null;
    for (const name of REMEMBER_STORES) {
      try {
        const saved = JSON.parse(window[name].getItem(REMEMBER_KEY + org) || "null");
        const age = saved ? Date.now() - Number(saved.at) : NaN;
        if (saved && Number.isInteger(saved.wall_id) && age >= 0 && age < REMEMBER_MS) return saved.wall_id;
      } catch (_) { /* blocked or unreadable: try the next store */ }
    }
    return null;
  }

  /** "Video wall", or "Video wall 2", 3… when the organization already has a
   *  wall of that name (any wall, grid walls too; case does not matter). */
  function defaultWallName(walls) {
    const taken = new Set(walls.map((w) => String(w.name || "").trim().toLowerCase()));
    const first = Khan.t("wallsetup.default_name", "Video wall");
    if (!taken.has(first.trim().toLowerCase())) return first;
    for (let n = 2; ; n++) {
      const name = Khan.t("wallsetup.default_name_n", "Video wall {n}").replace("{n}", String(n));
      if (!taken.has(name.trim().toLowerCase())) return name;
    }
  }

  function errorCode(err) {
    const detail = err && err.data ? err.data.detail : null;
    return detail && typeof detail === "object" && typeof detail.code === "string" ? detail.code : "";
  }

  /** A refused request, in /pair's words where /pair has them. The wall
   *  endpoints answer with codes, which api() has already turned into
   *  err.message (error.<code>); app.js's mapPairErrorMessage reads the
   *  plain-string details of /screens/claim and /screens/poll instead, so
   *  only codeRefusal below uses it. */
  function flowErrorText(err) {
    if (!err || err.status === 0) return Khan.t("pair.error.network", "Can't reach the server. Please try again.");
    const code = errorCode(err);
    if (code === "pair.code_unknown") return Khan.t("pair.error.unknown_code", "That code isn't recognised. Check the TV screen and try again.");
    if (code === "pair.code_expired") return Khan.t("pair.error.expired", "Code expired. Refresh the TV to get a new one.");
    if (code === "pair.code_used") return Khan.t("pair.error.used", "That code's been used. Refresh the TV to get a new one.");
    if (err.status === 403) return Khan.t("pair.error.forbidden", "Your account doesn't have permission to pair displays.");
    if (err.status === 413) return Khan.t("wallsetup.too_large", "That video is too large to upload. Try a shorter or smaller one.");
    return (err.isApiError && err.message) || Khan.t("error.generic", "Something went wrong. Please try again.");
  }

  /** Ruling R15: before a file is sent or the code is claimed, ask the
   *  endpoint the TV itself polls (app.js pairCodeRejection, the anonymous
   *  GET /screens/poll/{code}) whether the code can still be claimed. A code
   *  lives 5 minutes, and a phone upload can take longer. Resolves to /pair's
   *  words for a definite no (unknown, expired, already used), else "". It
   *  never rejects: a network hiccup or a rate limit counts as "go ahead",
   *  and the claim itself has the final word. */
  async function codeRefusal(code) {
    const rejection = await pairCodeRejection(code);
    return rejection ? mapPairErrorMessage(rejection) : "";
  }

  /** The step's own error line; before a step has drawn one, the page's. */
  function showFormError(message) {
    const el = document.getElementById("wall-setup-form-error");
    if (!el) { fail(message); return; }
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function hideFormError() {
    const el = document.getElementById("wall-setup-form-error");
    if (!el) return;
    el.textContent = "";
    el.classList.add("hidden");
  }

  /** Back to "What is this TV?". startFromPair added a history entry at the
   *  same /pair address, so this is the browser's Back: routeToCurrentPath
   *  shows the pair form again, and hideWallSetupView ends the steps. */
  function backToPair() {
    return { label: Khan.t("wallsetup.back_step", "Back"), go: () => history.back() };
  }

  /** "Part of a video wall" on /pair, for the TV showing `code`: which wall
   *  (or a new one and its video), then that TV's placement. Never rejects. */
  function startFromPair(code) {
    stopFlow();
    const myGen = ++gen;
    Object.assign(current, { wallId: null, wall: null, status: "idle", loading: null });
    flow = { code: normalizePairCode(code), walls: [], free: [], choice: null, name: null, media: null, upload: null };
    // An entry of its own at the same address: Back (the phone's or the
    // page's) returns to "What is this TV?" instead of leaving /pair.
    history.pushState({}, "", location.pathname + location.search);
    showPanel();
    window.scrollTo(0, 0);
    if (!canEditContent()) {
      fail(Khan.t("wallsetup.forbidden", "Your account can't set up video walls. Ask an admin or an editor."));
      return Promise.resolve();
    }
    if (!PAIR_CODE_RE.test(flow.code)) {
      fail(Khan.t("pair.error.bad_code", "Enter the full code shown on the TV."));
      return Promise.resolve();
    }
    return guard(() => loadChoices(myGen))();
  }

  /** GET /walls into the steps. True when the answer is this flow's; false
   *  when it belongs to steps (or a session) that have ended; a failure
   *  that is this flow's is thrown. */
  async function fetchWalls(myGen) {
    const session = authToken;
    let walls;
    try {
      walls = await api("/walls");
    } catch (err) {
      if (myGen !== gen || !flow || !sessionLive(session) || err.stale || err.status === 401) return false;
      throw err;
    }
    if (myGen !== gen || !flow || !sessionLive(session)) return false;
    flow.walls = Array.isArray(walls) ? walls : [];
    flow.free = flow.walls.filter((w) => w.layout === "free").sort((a, b) => b.id - a.id);
    return true;
  }

  async function loadChoices(myGen) {
    if (myGen !== gen || !flow) return;
    const title = Khan.t("wallsetup.which_wall", "Which wall?");
    renderFrame(title, "",
      `<p id="wall-setup-status" class="wall-setup-status" role="status">${escHtml(Khan.t("wallsetup.loading_walls", "Loading your video walls…"))}</p>`,
      backToPair());
    let loaded;
    try {
      loaded = await fetchWalls(myGen);
    } catch (err) {
      renderFrame(title, "",
        `<p id="wall-setup-error" class="wall-setup-error" role="alert">${escHtml(err.message || Khan.t("wallsetup.load_failed", "Couldn't open this video wall."))}</p>` +
        `<button type="button" id="wall-setup-retry" class="btn btn-ghost">${escHtml(Khan.t("wallsetup.retry", "Try again"))}</button>`,
        backToPair());
      document.getElementById("wall-setup-retry").addEventListener("click", guard(() => loadChoices(myGen)));
      return;
    }
    if (!loaded) return;
    if (!flow.free.length) {   // no phone-made wall yet: straight to a new one
      renderNewWall(false);
      return;
    }
    const remembered = recentWallId();
    const usable = flow.free.find((w) => w.id === remembered && tvCount(w) < MAX_TVS);
    flow.choice = usable ? usable.id : "new";
    renderChoose();
  }

  /** Which wall? The phone-made walls, newest first, then New video wall.
   *  A full wall is listed but cannot be picked. `errorText` says why the
   *  list was just read again. */
  function renderChoose(errorText = "") {
    if (!flow) return;
    const rows = flow.free.map((w) => {
      const n = tvCount(w);
      const full = n >= MAX_TVS;
      const meta = pluralText("wallsetup.tv_count", n, { one: "{n} TV", other: "{n} TVs" }) +
        (full ? " · " + Khan.t("wallsetup.wall_full", "Full") : "");
      return `
        <label class="wall-setup-choice">
          <input type="radio" name="wall-setup-wall" value="${w.id}"${flow.choice === w.id && !full ? " checked" : ""}${full ? " disabled" : ""} />
          <span class="wall-setup-choice-card">
            <span class="wall-setup-choice-name" dir="auto">${escHtml(w.name)}</span>
            <span class="wall-setup-choice-meta">${escHtml(meta)}</span>
          </span>
        </label>`;
    }).join("");
    renderFrame(Khan.t("wallsetup.which_wall", "Which wall?"),
      Khan.t("wallsetup.which_wall_sub", "Add this TV to one of your video walls, or start a new one."), `
      <form id="wall-setup-choose" class="wall-setup-form" novalidate>
        <fieldset class="wall-setup-choices" aria-labelledby="wall-setup-title">
          ${rows}
          <label class="wall-setup-choice">
            <input type="radio" name="wall-setup-wall" value="new"${flow.choice === "new" ? " checked" : ""} />
            <span class="wall-setup-choice-card">
              <span class="wall-setup-choice-name">${escHtml(Khan.t("wallsetup.new_wall", "New video wall"))}</span>
              <span class="wall-setup-choice-meta">${escHtml(Khan.t("wallsetup.new_wall_hint", "You'll choose its video next."))}</span>
            </span>
          </label>
        </fieldset>
        <p id="wall-setup-form-error" class="wall-setup-error${errorText ? "" : " hidden"}" role="alert">${escHtml(errorText)}</p>
        <button type="submit" id="wall-setup-next" class="btn btn-primary wall-setup-primary"></button>
      </form>`, backToPair());
    const form = document.getElementById("wall-setup-choose");
    const next = document.getElementById("wall-setup-next");
    const sync = () => {
      if (!flow) return;
      const picked = form.querySelector('input[name="wall-setup-wall"]:checked');
      flow.choice = !picked ? null : picked.value === "new" ? "new" : Number(picked.value);
      next.textContent = flow.choice === "new"
        ? Khan.t("wallsetup.next", "Next")
        : Khan.t("wallsetup.add_tv", "Add this TV");
      next.disabled = flow.choice === null;
    };
    form.addEventListener("change", () => { hideFormError(); sync(); });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!flow || flow.choice === null) return;
      if (flow.choice === "new") renderNewWall(true);
      else guard(joinWall)(flow.choice);
    });
    sync();
  }

  /** Add this TV to a phone-made wall, then place it. */
  async function joinWall(wallId) {
    const btn = document.getElementById("wall-setup-next");
    if (!flow || !btn) return;
    const myGen = gen;
    const session = authToken;
    const code = flow.code;
    hideFormError();
    let cell = null;
    let refusal = "";
    try {
      cell = await withLoading(btn, async () => {
        refusal = await codeRefusal(code);
        if (refusal || myGen !== gen || !flow) return null;   // refused, or the steps ended: claim nothing
        return api(`/walls/${wallId}/tvs`, {
          method: "POST",
          body: JSON.stringify({ code }),
        });
      });
    } catch (err) {
      // A 401 has already signed the tab out; a stale answer is an ended session's.
      if (myGen !== gen || !flow || !sessionLive(session) || err.stale || err.status === 401) return;
      const message = flowErrorText(err);
      if (["wall.full", "wall.not_found", "wall.not_free"].includes(errorCode(err))) {
        // The list on screen is out of date: read it again, then say why.
        try {
          if (!(await fetchWalls(myGen))) return;
        } catch (_) { /* keep the list already on screen */ }
        if (!flow.free.some((w) => w.id === flow.choice && tvCount(w) < MAX_TVS)) flow.choice = "new";
        renderChoose(message);
        return;
      }
      showFormError(message);
      return;
    }
    if (myGen !== gen || !flow || !sessionLive(session)) return;
    if (refusal) {
      showFormError(refusal);
      return;
    }
    rememberWall(wallId);
    openEditor(wallId, cell && Number.isInteger(cell.col_index) ? cell.col_index : null);
  }

  /** New video wall: its name (prefilled) and its video, from the library or
   *  uploaded from this phone. `fromChoose`: Back returns to Which wall?;
   *  otherwise (the organization has no phone-made wall yet) to /pair. */
  function renderNewWall(fromChoose) {
    if (!flow) return;
    if (flow.name === null) flow.name = defaultWallName(flow.walls);
    const back = fromChoose
      ? { label: Khan.t("wallsetup.back_step", "Back"), go: () => { stopUpload(); renderChoose(); } }
      : backToPair();
    renderFrame(Khan.t("wallsetup.new_wall", "New video wall"),
      Khan.t("wallsetup.new_sub", "Name it, then choose the video it plays across its TVs."), `
      <form id="wall-setup-new" class="wall-setup-form" novalidate>
        <label class="wall-setup-field">
          <span class="wall-setup-label">${escHtml(Khan.t("wallsetup.name_label", "Name"))}</span>
          <input type="text" id="wall-setup-name" class="wall-setup-input" maxlength="120" autocomplete="off" dir="auto" value="${escHtml(flow.name)}" />
        </label>
        <div class="wall-setup-field" role="group" aria-labelledby="wall-setup-video-label">
          <span id="wall-setup-video-label" class="wall-setup-label">${escHtml(Khan.t("wallsetup.video_label", "Video"))}</span>
          <div id="wall-setup-chosen" class="wall-setup-chosen"></div>
          <div class="wall-setup-video-actions">
            <button type="button" id="wall-setup-library" class="btn btn-ghost">${escHtml(Khan.t("wallsetup.from_library", "From the library"))}</button>
            <button type="button" id="wall-setup-upload" class="btn btn-ghost">${escHtml(Khan.t("wallsetup.upload_phone", "Upload from this phone"))}</button>
            <input type="file" id="wall-setup-file" class="hidden" accept="video/*,image/*" />
          </div>
          <div id="wall-setup-progress" class="wall-setup-progress hidden" role="status">
            <progress id="wall-setup-progress-bar" max="100" value="0"></progress>
            <span id="wall-setup-progress-text"></span>
          </div>
        </div>
        <p id="wall-setup-form-error" class="wall-setup-error hidden" role="alert"></p>
        <button type="submit" id="wall-setup-create" class="btn btn-primary wall-setup-primary">${escHtml(Khan.t("wallsetup.create", "Create the wall"))}</button>
      </form>`, back);
    const nameInput = document.getElementById("wall-setup-name");
    nameInput.addEventListener("input", () => {
      if (!flow) return;
      flow.name = nameInput.value;
      syncNewWall();
    });
    document.getElementById("wall-setup-library").addEventListener("click", guard(pickFromLibrary));
    // MDN's pattern: a hidden file input opened by a button, which takes the
    // page's look and its touch size; .click() runs inside the tap.
    document.getElementById("wall-setup-upload").addEventListener("click", () => {
      document.getElementById("wall-setup-file").click();
    });
    document.getElementById("wall-setup-file").addEventListener("change", (e) => guard(uploadChosenFile)(e.target));
    document.getElementById("wall-setup-new").addEventListener("submit", (e) => {
      e.preventDefault();
      guard(createWall)();
    });
    renderChosen();
    syncNewWall();
  }

  /** Create needs a name and a video; nothing starts while an upload runs. */
  function syncNewWall() {
    const create = document.getElementById("wall-setup-create");
    if (!create || !flow) return;
    const busy = !!flow.upload;
    create.disabled = busy || !flow.media || !String(flow.name || "").trim();
    document.getElementById("wall-setup-library").disabled = busy;
    document.getElementById("wall-setup-upload").disabled = busy;
  }

  /** The chosen video (or image), previewed from the API (media-src allows
   *  the API origin; a blob: preview of a file before upload would not be). */
  function renderChosen() {
    const box = document.getElementById("wall-setup-chosen");
    if (!box || !flow) return;
    const m = flow.media;
    if (!m) {
      box.innerHTML = `<p class="wall-setup-chosen-empty">${escHtml(Khan.t("wallsetup.no_video", "No video chosen yet."))}</p>`;
      return;
    }
    // Uploads are served by the API; the dashboard's own /uploads/… is index.html.
    const src = m.url ? `${API_BASE}${m.url}` : uploadUrl(m.filename);
    const preview = /^video\//i.test(m.mime_type || "")
      ? `<video class="wall-setup-thumb" src="${escHtml(src)}" muted playsinline preload="metadata"></video>`
      : `<img class="wall-setup-thumb" src="${escHtml(src)}" alt="" />`;
    box.innerHTML = `${preview}<span id="wall-setup-chosen-name" class="wall-setup-chosen-name" dir="auto">${escHtml(m.name || "")}</span>`;
  }

  function setMedia(media) {
    flow.media = media;
    renderChosen();
    syncNewWall();
  }

  /** From the library: MediaPicker, videos and images only. It is a
   *  multi-select picker, and a wall plays one video: several picks are
   *  refused with a message rather than cut to one the person did not
   *  choose. It returns ids only, so the row (name, type, address) comes
   *  from GET /media. */
  async function pickFromLibrary() {
    if (!flow || flow.upload) return;
    const myGen = gen;
    hideFormError();
    let picks;
    try {
      picks = await MediaPicker.open({ allowedTypes: ["video", "image"] });
    } catch (err) {
      if (err && err.cancelled) return;   // closed without a pick
      throw err;
    }
    if (myGen !== gen || !flow || !Array.isArray(picks) || !picks.length) return;
    if (picks.length > 1) {
      showFormError(Khan.t("wallsetup.pick_one", "A video wall plays one video or image. Pick just one."));
      return;
    }
    const mediaId = picks[0].media_id;
    const session = authToken;
    let list;
    try {
      list = await api("/media");
    } catch (err) {
      if (myGen !== gen || !flow || !sessionLive(session) || err.stale || err.status === 401) return;
      showFormError(flowErrorText(err));
      return;
    }
    if (myGen !== gen || !flow || !sessionLive(session)) return;
    const media = (Array.isArray(list) ? list : []).find((m) => m.id === mediaId);
    if (!media) {
      showFormError(Khan.t("error.media.not_found", "Media not found."));
      return;
    }
    setMedia(media);
  }

  /** POST /media/upload with upload progress, which fetch() cannot report.
   *  Same session header, error shape and 401 / subscription handling as
   *  api(): a failed answer goes through app.js's failedResponse() and
   *  markStale(). Resolves to the new media row; `onProgress(fraction)` gets
   *  0..1. The promise has abort(), which rejects it with {cancelled: true}. */
  function uploadWithProgress(file, onProgress) {
    const xhr = new XMLHttpRequest();
    const session = authToken;
    const headers = {};
    if (authToken) headers.Authorization = `Bearer ${authToken}`;
    const promise = new Promise((resolve, reject) => {
      xhr.open("POST", `${API_BASE}/media/upload`);
      Object.keys(headers).forEach((name) => xhr.setRequestHeader(name, headers[name]));
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total);
      });
      xhr.addEventListener("load", () => {
        const text = xhr.responseText || "";
        if (xhr.status >= 200 && xhr.status < 300) {
          let data = null;
          try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
          resolve(data);
          return;
        }
        failedResponse({ status: xhr.status }, text, headers, { session })
          .then((err) => reject(markStale(err, session)), (err) => reject(markStale(err, session)));
      });
      xhr.addEventListener("error", () => {
        const err = new Error(Khan.t("error.network", "Can't reach the server. Check your connection and try again."));
        err.status = 0;
        err.network = true;
        err.isApiError = true;
        reject(markStale(err, session));
      });
      xhr.addEventListener("abort", () => reject({ cancelled: true }));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
    promise.abort = () => xhr.abort();
    return promise;
  }

  function showProgress(fraction) {
    const box = document.getElementById("wall-setup-progress");
    if (!box) return;
    const pct = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    box.classList.remove("hidden");
    document.getElementById("wall-setup-progress-bar").value = pct;
    document.getElementById("wall-setup-progress-text").textContent =
      Khan.t("wallsetup.uploading", "Uploading… {pct}%").replace("{pct}", String(pct));
  }

  function hideProgress() {
    const box = document.getElementById("wall-setup-progress");
    if (box) box.classList.add("hidden");
  }

  /** Upload from this phone. The file is checked here only for being a
   *  video or an image (the browser's type, when it has one); the server
   *  decides the rest, including the size limit (413). The TV's code is
   *  checked first (R15): a long upload for a code that is already dead
   *  would be wasted. */
  async function uploadChosenFile(input) {
    const file = input.files && input.files[0];
    input.value = "";   // choosing the same file again is a new change
    if (!file || !flow || flow.upload) return;
    hideFormError();
    if (file.type && !/^(video|image)\//i.test(file.type)) {
      showFormError(Khan.t("error.wall.media_type_blocked", "A video wall plays a video or an image. Pick one of those."));
      return;
    }
    const myGen = gen;
    const session = authToken;
    // While the code is checked the buttons are disabled as for an upload.
    // Back (stopUpload) clears this placeholder, which drops the check's answer.
    const checking = { abort() {} };
    flow.upload = checking;
    syncNewWall();
    const refusal = await codeRefusal(flow.code);
    if (myGen !== gen || !flow || flow.upload !== checking || !sessionLive(session)) return;
    flow.upload = null;
    if (refusal) {
      syncNewWall();
      showFormError(refusal);
      return;
    }
    const upload = uploadWithProgress(file, (fraction) => {
      if (flow && flow.upload === upload) showProgress(fraction);
    });
    flow.upload = upload;
    showProgress(0);
    syncNewWall();
    upload.then((media) => {
      if (myGen !== gen || !flow || flow.upload !== upload || !sessionLive(session)) return;
      flow.upload = null;
      hideProgress();
      setMedia(media);
    }, (err) => {
      if (myGen !== gen || !flow || flow.upload !== upload) return;   // aborted, or the steps ended
      flow.upload = null;
      hideProgress();
      syncNewWall();
      // A 401 has signed the tab out already; a stale or cancelled one is nobody's.
      if (!err || err.cancelled || err.stale || err.status === 401 || !sessionLive(session)) return;
      showFormError(flowErrorText(err));
    }).catch((err) => console.error(err));
  }

  /** Create the wall with this TV in it, then place the TV. */
  async function createWall() {
    const btn = document.getElementById("wall-setup-create");
    const name = String((flow && flow.name) || "").trim();
    if (!flow || !btn || !name || !flow.media || flow.upload) return;
    const myGen = gen;
    const session = authToken;
    const code = flow.code;
    const mediaId = flow.media.id;
    hideFormError();
    let wall = null;
    let refusal = "";
    try {
      wall = await withLoading(btn, async () => {
        refusal = await codeRefusal(code);
        if (refusal || myGen !== gen || !flow) return null;   // refused, or the steps ended: create nothing
        return api("/walls/free", {
          method: "POST",
          body: JSON.stringify({ name, media_id: mediaId, code }),
        });
      });
    } catch (err) {
      if (myGen !== gen || !flow || !sessionLive(session) || err.stale || err.status === 401) return;
      showFormError(flowErrorText(err));
      return;
    }
    if (myGen !== gen || !flow || !sessionLive(session)) return;
    if (refusal) {
      showFormError(refusal);
      return;
    }
    rememberWall(wall.id);
    const first = (wall.cells || [])[0];
    openEditor(wall.id, first && Number.isInteger(first.col_index) ? first.col_index : 0);
  }

  // ── Placement editor: STUB, replaced in full by Task 14 ────────────────
  /** Open the wall's page with TV `focusCol` (its cell's col_index, or null)
   *  selected for placing. The address becomes /wall-setup?wall=<id>&tv=<n>
   *  (TV n = col_index + 1) in place of the steps' history entry (no new
   *  entry, ruling R19), so Back from the wall goes to the /pair form rather
   *  than through the steps again; show() then finds its address set. */
  function openEditor(wallId, focusCol) {
    const tv = Number.isInteger(focusCol) && focusCol >= 0 ? `&tv=${focusCol + 1}` : "";
    history.replaceState({}, "", `${PATH}?wall=${wallId}${tv}`);
    return show(wallId);
  }

  return { show, startFromPair, reset, recentWallId };
})();
```

- [ ] **Step 7: Style the step and the video-wall steps (`frontend/wall-setup.css`)**

Append to the end of `frontend/wall-setup.css`:

```css
/* ── /pair: "What is this TV?" (index.html #pair-kind) and the steps after it
   (wall-setup.js startFromPair) ─────────────────────────────────────────
   Large choices: each radio is stretched, invisible, over its card, so the
   whole card is the tap target while the native radio stays the control
   that is focused, checked and announced. */
.pair-kind-options,
.wall-setup-choices {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.wall-setup-choices {
  border: none;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.pair-kind-option,
.wall-setup-choice {
  position: relative;
  display: block;
  margin: 0;
  cursor: pointer;
}
.pair-kind-option input[type="radio"],
.wall-setup-choice input[type="radio"] {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}
.pair-kind-card,
.wall-setup-choice-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  min-height: 72px;
  padding: 14px 16px;
  border: 2px solid var(--border-2);
  border-radius: var(--r-lg);
  background: var(--surface);
  transition: border-color var(--t), background var(--t);
}
.pair-kind-title,
.wall-setup-choice-name {
  font-size: 16px;
  font-weight: 650;
  line-height: 1.3;
  color: var(--text);
  overflow-wrap: anywhere;
}
.pair-kind-hint,
.wall-setup-choice-meta {
  font-size: 13px;
  font-weight: 400;
  line-height: 1.4;
  color: var(--text-2);
}
.pair-kind-option input:checked + .pair-kind-card,
.wall-setup-choice input:checked + .wall-setup-choice-card {
  border-color: var(--accent-text);
  background: var(--accent-soft);
}
.pair-kind-option input:focus-visible + .pair-kind-card,
.wall-setup-choice input:focus-visible + .wall-setup-choice-card {
  outline: 2px solid var(--accent-text);
  outline-offset: 2px;
}
.wall-setup-choice input:disabled { cursor: not-allowed; }
.wall-setup-choice input:disabled + .wall-setup-choice-card { opacity: 0.55; }

/* The steps' forms: one column, no wider than the pair card. The base form
   rule (styles.css) is a wrapping row with a bottom margin. */
.wall-setup-form {
  align-self: stretch;
  width: 100%;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: var(--sp-4);
  margin: 0;
}
.wall-setup-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}
.wall-setup-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.wall-setup-input {
  width: 100%;
  font-size: 16px;
  padding: 12px 14px;
  min-height: 48px;
}
.wall-setup-chosen {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  min-width: 0;
}
.wall-setup-thumb {
  width: 96px;
  height: 54px;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: var(--r-sm);
  background: var(--thumb-a);
}
.wall-setup-chosen-name {
  min-width: 0;
  font-weight: 600;
  color: var(--text);
  overflow-wrap: anywhere;
}
/* "No video chosen yet." Not .wall-setup-hint: the placement editor's hint
   has that class, and a rule here would restyle it. */
.wall-setup-chosen-empty {
  margin: 0;
  font-size: 14px;
  color: var(--text-2);
}
.wall-setup-video-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.wall-setup-video-actions .btn {
  flex: 1 1 160px;
  min-height: 48px;
  font-size: 15px;
}
.wall-setup-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: var(--text-2);
}
.wall-setup-progress progress {
  flex: 1 1 auto;
  min-width: 0;
  height: 8px;
  accent-color: var(--accent-fill);
}
.wall-setup-primary {
  min-height: 48px;
  font-size: 16px;
}
```

- [ ] **Step 8: Add the strings (English and Arabic)**

Same method as Task 12 Step 7 (the script refuses to overwrite a key that exists). Run from the worktree root:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && python3 - <<'PY'
import json, pathlib
NEW = {
    "en": {
        "wallsetup.kind_question": "What is this TV?",
        "wallsetup.kind_screen": "Its own screen",
        "wallsetup.kind_screen_hint": "It shows its own playlist or schedule.",
        "wallsetup.kind_wall": "Part of a video wall",
        "wallsetup.kind_wall_hint": "One video plays across several TVs.",
        "wallsetup.kind_continue": "Continue",
        "wallsetup.back_step": "Back",
        "wallsetup.which_wall": "Which wall?",
        "wallsetup.which_wall_sub": "Add this TV to one of your video walls, or start a new one.",
        "wallsetup.loading_walls": "Loading your video walls…",
        "wallsetup.wall_full": "Full",
        "wallsetup.new_wall": "New video wall",
        "wallsetup.new_wall_hint": "You'll choose its video next.",
        "wallsetup.add_tv": "Add this TV",
        "wallsetup.next": "Next",
        "wallsetup.new_sub": "Name it, then choose the video it plays across its TVs.",
        "wallsetup.name_label": "Name",
        "wallsetup.default_name": "Video wall",
        "wallsetup.default_name_n": "Video wall {n}",
        "wallsetup.video_label": "Video",
        "wallsetup.no_video": "No video chosen yet.",
        "wallsetup.from_library": "From the library",
        "wallsetup.upload_phone": "Upload from this phone",
        "wallsetup.uploading": "Uploading… {pct}%",
        "wallsetup.too_large": "That video is too large to upload. Try a shorter or smaller one.",
        "wallsetup.create": "Create the wall",
        "wallsetup.pick_one": "A video wall plays one video or image. Pick just one.",
    },
    "ar": {
        "wallsetup.kind_question": "ما دور هذه الشاشة؟",
        "wallsetup.kind_screen": "شاشة مستقلة",
        "wallsetup.kind_screen_hint": "تعرض قائمة التشغيل أو الجدول الخاص بها.",
        "wallsetup.kind_wall": "جزء من جدار شاشات",
        "wallsetup.kind_wall_hint": "يُعرض فيديو واحد عبر عدة شاشات.",
        "wallsetup.kind_continue": "متابعة",
        "wallsetup.back_step": "رجوع",
        "wallsetup.which_wall": "أي جدار؟",
        "wallsetup.which_wall_sub": "أضف هذه الشاشة إلى أحد جدران الشاشات، أو ابدأ جداراً جديداً.",
        "wallsetup.loading_walls": "جارٍ تحميل جدران الشاشات…",
        "wallsetup.wall_full": "ممتلئ",
        "wallsetup.new_wall": "جدار شاشات جديد",
        "wallsetup.new_wall_hint": "ستختار الفيديو الخاص به في الخطوة التالية.",
        "wallsetup.add_tv": "أضف هذه الشاشة",
        "wallsetup.next": "التالي",
        "wallsetup.new_sub": "سمِّه، ثم اختر الفيديو الذي يُعرض عبر شاشاته.",
        "wallsetup.name_label": "الاسم",
        "wallsetup.default_name": "جدار شاشات",
        "wallsetup.default_name_n": "جدار شاشات {n}",
        "wallsetup.video_label": "الفيديو",
        "wallsetup.no_video": "لم يُختر فيديو بعد.",
        "wallsetup.from_library": "من المكتبة",
        "wallsetup.upload_phone": "ارفع من هذا الهاتف",
        "wallsetup.uploading": "جارٍ الرفع… {pct}%",
        "wallsetup.too_large": "هذا الفيديو أكبر من الحجم المسموح برفعه. جرّب فيديو أقصر أو أصغر حجماً.",
        "wallsetup.create": "أنشئ الجدار",
        "wallsetup.pick_one": "يعرض جدار الشاشات فيديو أو صورة واحدة. اختر واحداً فقط.",
    },
}
for lang, keys in NEW.items():
    path = pathlib.Path("frontend/i18n") / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    clash = sorted(set(keys) & set(data))
    assert not clash, f"{lang}.json already has {clash}"
    data.update(keys)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{lang}: +{len(keys)} keys, {len(data)} in all")
PY
git diff --stat frontend/i18n/
```
Expected: `en: +27 keys, 774 in all`, `ar: +27 keys, 799 in all` (Task 12 left 747 / 772 at 24101d4; more in all if the redesign added keys since, but the `+27` must match), and `git diff --stat` shows `2 files changed, 56 insertions(+), 2 deletions(-)` (27 new lines per file, plus each file's old last line rewritten with a trailing comma). The code check (R15) reuses /pair's `pair.error.*` strings through `mapPairErrorMessage`, so it adds no key.

- [ ] **Step 9: Let the UI contract check know the steps' runtime ids**

In `scripts/check_ui_contract.sh`, replace:

```bash
DYNAMIC_IDS="canvas-item-detail 
```

with:

```bash
DYNAMIC_IDS="wall-setup-choose wall-setup-next wall-setup-form-error wall-setup-new wall-setup-name wall-setup-chosen wall-setup-library wall-setup-upload wall-setup-file wall-setup-progress wall-setup-progress-bar wall-setup-progress-text wall-setup-create canvas-item-detail 
```

(The ids are prepended so the end of the line, which Task 12 extended, stays as it is. Every other id these steps look up is either Task 12's, already listed, or in index.html: `pair-kind`, `pair-kind-screen`, `pair-kind-wall`, `pair-target-existing`.)

- [ ] **Step 10: Run the tests and watch them pass**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh wall_setup_flows.py
python3 scripts/check_i18n.py
bash scripts/check_ui_contract.sh
node --test frontend/tests/
```
Expected: PASS. `wall_setup_flows.py` prints 32 `PASS` lines (Task 12's 11 and these 21) and `0 failed`; then `i18n OK across frontend, landing, player`; `UI contract OK`; `# pass 24` / `# fail 0`. Then check the names Task 14 anchors on:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
grep -c 'stopFlow();$' frontend/wall-setup.js
grep -c 'const myGen = ++gen;' frontend/wall-setup.js
grep -n 'function openEditor\|return { show' frontend/wall-setup.js
```
Expected: `2` (reset and startFromPair; show's call carries a comment), `2` (show and startFromPair), then exactly two lines: the stub `function openEditor(wallId, focusCol) {` and `return { show, startFromPair, reset, recentWallId };`.

(Checked while planning: Tasks 11-13 applied from the plan text to an archive of 24101d4, in a throwaway `signage_renderer:latest` container: 32 PASS, `0 failed`; every anchor in this task matched exactly once; `check_i18n.py` and `check_ui_contract.sh` passed.)

- [ ] **Step 11: Check that nothing else in the dashboard changed**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh flows.py > scripts/ui/out/flows-after-pair-kind.txt 2>&1; tail -n 1 scripts/ui/out/flows-after-pair-kind.txt
diff <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-baseline.txt | sed 's/ :: .*//') <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-after-pair-kind.txt | sed 's/ :: .*//') && echo "flows unchanged"
bash scripts/ui/run.sh shoot.py --routes '/pair?code=ABCD23' --fixtures populated > scripts/ui/out/shoot-pair-after.txt 2>&1; tail -n 1 scripts/ui/out/shoot-pair-after.txt
grep -c '^FAIL' scripts/ui/out/shoot-pair-after.txt
```
Expected: the same `N failed` line as the Task 11 baseline and `flows unchanged` (the redesign's own /pair flows, "router: /pair?code= still opens pairing…", "router: Back from Billing to /pair…", "router: View dashboard from pairing…", still pass); `12 shots, N failures` with N no larger than Step 1's count (the step adds no small tap target, no input under 16px and no overflow). If `diff` prints anything, the hook changed an existing page: fix it before committing.

- [ ] **Step 12: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git branch --show-current   # must print feature/phone-wall-setup-dashboard; stop if it does not
git add frontend/index.html frontend/app.js frontend/wall-setup.js frontend/wall-setup.css frontend/i18n/en.json frontend/i18n/ar.json scripts/check_ui_contract.sh scripts/ui/wall_setup_flows.py
git commit -m "feat(walls): start or join a video wall from the TV's pairing page

/pair now asks \"What is this TV?\" once the code is complete. Its own
screen is the pairing form as before; Part of a video wall turns the
button into Continue, and the form hands the code to wall-setup.js
before anything claims it (Enter in the code field included). When
this phone set up or joined a wall in the last 30 minutes, Part of a
video wall is preselected, so the next TV is Continue, then Add this TV.

Which wall? lists the organization's phone-made walls, newest first,
with that remembered wall preselected (per organization) and full
walls greyed out. A new wall gets a free default name and one video
from the library or uploaded from the phone with progress (XHR, same
session and error handling as api()); a 413 says the video is too
large. Before a file is sent or the code is claimed, the TV's code is
checked against the endpoint the TV polls, so a code that died during
a long upload is caught before the wall is created. The code is
claimed with POST /walls/free or /walls/{id}/tvs, refusals read like
/pair, and the wall's page (/wall-setup?wall=<id>&tv=<n>) replaces the
steps' history entry, so Back returns to the pairing form. The
placement editor itself comes next; openEditor is a stub until then.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Placement editor

**Files:**
- Modify: `frontend/wall-setup.js` (created by Task 12, extended by Task 13; line numbers are those of the file as Task 13 leaves it): insert the editor after `tvCount()` (l.99-101), mount it at the end of `renderReady()` (l.106-110), stop it in `show()` (l.143-145), and in `reset()` (l.170-172, which starts with Task 13's `stopFlow();`), and delete Task 13's `openEditor` stub (l.764-774), whose callers `joinWall` and `createWall` then reach the real one
- Modify: `frontend/wall-setup.css` (created by Task 12): the editor's rules go right after the `.wall-setup-editor` rule (l.72-75); Task 13's appended block stays at the end
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json` (18 appended `wallsetup.editor.*` keys, via the script in Step 6)
- Modify: `scripts/ui/wall_setup_flows.py` (created by Task 12, extended by Task 13): the editor's flows go right before `if __name__ == "__main__":` (l.852), after Task 13's flows
- Test: `scripts/ui/wall_setup_flows.py`, run with `bash scripts/ui/run.sh wall_setup_flows.py "wall setup editor"`

All paths are relative to `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. This task runs after Task 13. Every edit is anchored on exact text as Tasks 12 and 13 leave it, not on line numbers; Step 0 checks each anchor.

**Testing approach.** The same as Tasks 12-13: flows in `scripts/ui/wall_setup_flows.py`, run by the redesign's harness (`scripts/ui/run.sh`: the production CSP from `common.py`, `signage_renderer:latest`, the fake `https://api.test` from `mock_api.py`). This task adds `EditorApi`, a second route in front of the fake API for the editor's two writes (`PUT .../placement`, `DELETE .../pairing`). It records every PUT body, applies the box to the fixture's wall (so the editor's 3 s re-reads see it, as with the backend), and can hold each answer back (`delay_ms`). Sync Playwright cannot sleep inside a route handler: a sleeping handler would stall every other event. So held answers are released by `EditorApi.pump()`, which the flow calls while it waits. Fingers are real touch input: CDP `Input.dispatchTouchEvent` (recon/e2e.md section 5) gives `pointerType: "touch"`, so a missing `touch-action: none` shows up as a `pointercancel` and a failed flow. `page.mouse` would hide that. Task 12's `WallFixture` already answers `GET /walls/{id}/canvas-playlist`, which the Walls page's canvas editor reads when Finish opens it (ruling R16), so this task adds no fixture class.

**Interfaces:**
- Consumes:
  - Task 11: `WallGeometry.SNAP`, `.MIN_OVERLAP`, `.boxForScreen(box, sw, sh, cw, ch)` (the port of `wall_layout.fit_box_to_screen`), `.moveBox(box, dx, dy)`, `.scaleBox(box, factor)`, `.snapBox(box, others, cw, ch)`, `.clampToCanvas(box)`, `.sameBox(a, b)` (within 1e-6). Composition (Task 11): drag = `clampToCanvas(snapBox(moveBox(startBox, dx, dy), others, cw, ch))` from the box at drag start plus the total pointer delta; pinch/slider = `clampToCanvas(scaleBox(box, factor))`. This task then puts every box it draws or sends into its TV's shape with `boxForScreen` (ruling R8).
  - Task 12, inside the `WallSetup` IIFE: `PATH`, `current`, `view()`, `isShowing()`, `parseWallId(raw)`, `addressWallId()`, `fail(message, retry)`, `tvCount(wall)`, `renderReady(wall)`, `show(wallId)`, `reset()`; the DOM `#wall-setup-view` (`aria-live="polite"`), `#wall-setup-sub`, `.wall-setup-editor` (`#wall-setup-editor`, `dir="ltr"`); the i18n keys `wallsetup.tv_count_*` (with the ar `_zero`), `wallsetup.back` and `wallsetup.load_failed`; the harness names `FLOWS`, `flow`, `free_wall`, `WallFixture` (it answers `GET /walls/{id}` and `GET /walls/{id}/canvas-playlist`), `views`, `visible_section`, `BASE`, `new_context`, `install_api`.
  - Task 13, inside the IIFE: `flow` (the `/pair` steps' state, non-null while `joinWall` / `createWall` call `openEditor`; `show()` ends it with `stopFlow()`), `stopFlow()` (first line of `reset()` and of `show()`), and the `openEditor(wallId, focusCol)` stub, which this task deletes: its callers `joinWall` (`openEditor(wallId, cell.col_index)` after `POST /walls/{id}/tvs`) and `createWall` (`openEditor(wall.id, 0)` after `POST /walls/free`) stay. `startFromPair` pushes ONE history entry at `/pair` (ruling R19). The export is `{ show, startFromPair, reset, recentWallId }` (ruling R13). Harness: `PairFixture` (Add this TV appends the TV to wall 7 as `col_index` 2, with no screen size yet), `open_pair(browser, ...)`, `remembered(wall_id)`, `to_wall_steps(page)`, `address(page)`.
  - app.js globals (read at call time only): `api(path, options)`, `authToken`, `API_BASE`, `sessionLive(session)`, `escHtml(str)`, `pluralText(base, n, english)`, `showSection(id)` (it calls `hideWallSetupView()`, which calls `reset()`), `Walls.openEditor(id)` (async: GET `/walls/{id}`, then for a spanned wall GET `/walls/{id}/canvas-playlist`), `window.confirmDialog({title, message, confirmLabel, danger})` (title and confirmLabel are injected as raw HTML; message only has `<` escaped), `Khan.t(key, fallback)`; the existing keys `screens.status_online` ("On" / "تعمل") and `screens.status_offline` ("Off" / "متوقفة").
  - Backend (PR 1): `GET /walls/{id}` -> `serialize_wall` with `layout`, `canvas_width_px/height_px`, `cells[]` (`row_index`, `col_index`, `screen_id`, `place_x/y/w/h`, `screen_w_px/h_px`, `online`) and `video` = `{media_id, name, url, mime_type, width_px, height_px}` or null (D6); `PUT /walls/{id}/cells/0/{col}/placement` body `{x, y, w, h, highlight}` -> 204, limit 1800/minute, which stores the box in the TV's shape once the TV has reported its screen (`fit_box_to_screen`, same width and centre; Task 7, ruling R8); `DELETE /walls/{id}/cells/0/{col}/pairing` deletes a free wall's cell, never the wall (Task 7).
- Produces:
  - Inside the `WallSetup` IIFE: `openEditor(wallId, focusCol) -> Promise<void>`, which never rejects. It opens the editor on a free wall with TV `focusCol` selected: `focusCol` is the cell's `col_index` (TV 1 is 0), or `null` for none. The address becomes `/wall-setup?wall=<id>&tv=<focusCol + 1>` (`&tv=` left out for null) in the CURRENT history entry, never a new one (ruling R19): from Task 13's steps that is the one entry `startFromPair` pushed at `/pair`, so Back from the editor returns to the `/pair` form; `show()` then finds its address set and pushes nothing. When called from the steps (`flow` set) it remembers the TV just added, so that TV's Done says "TV {n} placed. Point your camera at the next TV's QR code." (ruling R17). Also `mountEditor(wall)`, which `renderReady` calls, and `stopEditor()`, which `show()` and `reset()` call. `show(wallId)` and `/wall-setup?wall=<id>` (Adjust TVs, Task 15) open it with no TV selected (Adjust mode: Done says only "TV {n} placed."); `&tv=<n>` in the address selects TV n.
  - Behaviour: every box the phone draws or sends is in its TV's shape once that TV has reported its screen size, the same box the server stores (ruling R8), including boxes sent mid-drag. One finger anywhere on the video drags the selected TV's box: at once from the box itself, after 6 px from anywhere else (so a box hanging almost off the video can still be moved); a tap with no move selects the TV under the finger. Finish shows Video walls with this wall's editor open (`showSection("walls")`, then `Walls.openEditor(wallId)`, ruling R16). After the last TV is removed the wall stays, and the panel says "No TVs in this wall yet. Scan a TV's QR code and choose Part of a video wall." with a link back to Video walls (ruling R18).
  - DOM, for Task 16's phone scenarios: `.wall-setup-stage` (inside `#wall-setup-editor`, `touch-action: none`) holds `.wall-setup-media` (a `video` or `img`) and one `.wall-setup-box[data-col="<col>"]` per TV (`.is-focus` on the selected one; its label is `.wall-setup-box-name`; `.wall-setup-dot.is-online` when online). After the editor comes `.wall-setup-panel`, with `.wall-setup-tv[data-col]` buttons (`aria-pressed`), `.wall-setup-hint`, `.wall-setup-waiting`, `.wall-setup-size-input` (range 5-200), `.wall-setup-done`, `.wall-setup-remove`, `.wall-setup-placed-text`, `.wall-setup-finish`, `.wall-setup-empty` (`.wall-setup-empty-text`, `a.wall-setup-empty-back[href="/walls"]`), `.wall-setup-note` and `.wall-setup-save-error`. No new ids: `check_ui_contract.sh` needs no change.
  - i18n (en + ar): `wallsetup.editor.tv_label|tvs|canvas_label|pick|hint|waiting|size|done|placed|placed_short|finish|remove|remove_title|remove_message|removed|empty|save_failed|remove_failed` (18 keys). `wallsetup.editor.placed` keeps the exact text Task 16 checks: "TV {n} placed. Point your camera at the next TV's QR code."
  - Harness: `EditorApi(fx)` (`.install(ctx)`, `.puts`, `.deletes`, `.delay_ms`, `.put_error`, `.max_in_flight`, `.pump()`, `.settle(page)`, `.wall(id)`), `open_editor_page(browser, path=..., device=..., lang=..., settle=..., **fixture_kwargs) -> (ctx, page, fx, eapi, errors)`, `finger_drag`, `finger_pinch`, `rect_of`, `mid`, `focused_tv`, `toast_count`. Eighteen flows, whose names start with `wall setup editor:`. The file keeps exactly one `if __name__ == "__main__":` line, right after these flows: the anchor Task 15 inserts its flows before.

- [ ] **Step 0: Check the anchors**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git log --oneline -4
grep -c 'function tvCount(wall) {' frontend/wall-setup.js
grep -c 'class="wall-setup-editor" dir="ltr"></div>`);' frontend/wall-setup.js
grep -c 'return current.loading || Promise.resolve();' frontend/wall-setup.js
grep -c 'Placement editor: STUB, replaced in full by Task 14' frontend/wall-setup.js
grep -c 'return { show, startFromPair, reset, recentWallId };' frontend/wall-setup.js
grep -c 'stopFlow();$' frontend/wall-setup.js
grep -A2 -n '^  function reset() {' frontend/wall-setup.js
grep -c '^\.wall-setup-editor {' frontend/wall-setup.css
grep -c '^if __name__ == "__main__":' scripts/ui/wall_setup_flows.py
grep -nE 'function (openEditor|mountEditor|stopEditor|focusTv|edText|setText|capturePointers|rereadWall|applyWall)\b|\blet (ed|justAdded)\b' frontend/wall-setup.js
grep -nE '^(class EditorApi|def (open_editor_page|finger_drag|finger_pinch|rect_of|mid|focused_tv|toast_count))\b' scripts/ui/wall_setup_flows.py
```
Expected: the log shows Tasks 11, 12 and 13 committed. The first five `grep -c` print `1`. `stopFlow();$` prints `2` (reset and startFromPair; show's call carries a comment). The `reset()` grep prints exactly these three lines (Task 13 put `stopFlow();` first; Step 3's edit anchors on all three):
```
170:  function reset() {
171-    stopFlow();
172-    gen++;
```
The `.wall-setup-editor` and `__main__` greps print `1`. The names grep prints exactly one line, Task 13's stub, which Step 3 deletes (its callers stay):
```
770:  function openEditor(wallId, focusCol) {
```
The last grep prints nothing. If any other name in the last two greps already exists, rename this task's function and every use of it before going on. In one function scope a second `function` declaration silently replaces the first, and a second module-level `def` in the flows file does the same. (`grep -c 'const myGen = ++gen;'` would print `2`, show and startFromPair; the show() edit in Step 3 anchors on the three lines from `return current.loading || Promise.resolve();`, which occur once.)

- [ ] **Step 1: Write the failing test**

In `scripts/ui/wall_setup_flows.py`, replace:

```python
if __name__ == "__main__":
```

with:

```python
# ── Placement editor (Task 14) ────────────────────────────────────────────
import json, time, urllib.parse

CORS = {"access-control-allow-origin": "*"}
CELL_WRITE_RE = re.compile(r"^https://api\.test/walls/(\d+)/cells/(\d+)/(\d+)/(placement|pairing)$")


class EditorApi:
    """The placement editor's two writes, on top of a WallFixture, answered
    like the backend: PUT .../placement saves the box on the fixture's cell (so
    the editor's 3 s re-reads see it) and answers 204; DELETE .../pairing drops
    the cell and answers 200. Every PUT body is kept in `puts`.

    `delay_ms` holds each PUT's answer back that long. Sync Playwright cannot
    sleep inside a route handler (it would stall every other event), so held
    answers go out from pump(), which the flow calls while it waits."""
    def __init__(self, fx):
        self.fx = fx
        self.puts = []            # [(col, body)], in the order they reached the API
        self.deletes = []         # paths of DELETE .../pairing
        self.delay_ms = 0
        self.put_error = None     # (status, payload): every PUT fails with it
        self.held = []            # [(due, route, wall_id, col, body)]
        self.in_flight = 0        # PUTs the page has sent and had no answer to yet
        self.max_in_flight = 0

    def install(self, ctx):
        # Registered after install_api, so it runs first; anything else falls through to the fixture.
        ctx.route(CELL_WRITE_RE, self._route)

    def wall(self, wall_id=7):
        return next(w for w in self.fx.data["walls"] if w["id"] == wall_id)

    def _route(self, route, request):
        wall_id, row, col, what = CELL_WRITE_RE.match(request.url).groups()
        wall_id, row, col = int(wall_id), int(row), int(col)
        if what == "placement" and request.method == "PUT":
            body = json.loads(request.post_data)
            self.puts.append((col, body))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            if self.delay_ms:
                self.held.append((time.monotonic() + self.delay_ms / 1000, route, wall_id, col, body))
            else:
                self._answer(route, wall_id, col, body)
        elif what == "pairing" and request.method == "DELETE":
            self.deletes.append(urllib.parse.urlparse(request.url).path)
            w = self.wall(wall_id)
            w["cells"] = [c for c in w["cells"] if not (c["row_index"] == row and c["col_index"] == col)]
            route.fulfill(status=200, content_type="application/json", body="{}", headers=CORS)
        else:
            route.fallback()

    def _answer(self, route, wall_id, col, body):
        self.in_flight -= 1
        if self.put_error:
            status, payload = self.put_error
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload), headers=CORS)
            return
        cell = next((c for c in self.wall(wall_id)["cells"] if c["row_index"] == 0 and c["col_index"] == col), None)
        if cell is None:
            route.fulfill(status=404, content_type="application/json", headers=CORS,
                          body=json.dumps({"detail": {"code": "wall.cell_not_found", "message": "Cell not found"}}))
            return
        cell.update(place_x=body["x"], place_y=body["y"], place_w=body["w"], place_h=body["h"])
        route.fulfill(status=204, headers=CORS)

    def pump(self):
        """Answer every held PUT whose delay is over."""
        now = time.monotonic()
        for item in [h for h in self.held if h[0] <= now]:
            self.held.remove(item)
            self._answer(*item[1:])

    def settle(self, page, quiet_ms=700, timeout_s=10):
        """Wait, answering held PUTs when due, until nothing is held and no
        new PUT has come in for quiet_ms."""
        deadline = time.monotonic() + timeout_s
        count, since = len(self.puts), time.monotonic()
        while time.monotonic() < deadline:
            page.wait_for_timeout(25)
            self.pump()
            if len(self.puts) != count:
                count, since = len(self.puts), time.monotonic()
            elif not self.held and time.monotonic() - since >= quiet_ms / 1000:
                return
        raise AssertionError(f"placements never settled: {len(self.held)} held, {len(self.puts)} sent")


def open_editor_page(browser, path="/wall-setup?wall=7&tv=2", device="iphone", lang="en", settle=1200, **fx_kwargs):
    """Like open_page, with EditorApi in front of the fixture before the page
    loads: selecting TV 2 from the address sends a PUT straight away."""
    fx = WallFixture(locale=lang, **fx_kwargs)
    ctx = new_context(browser, device, lang=lang, token="TA")
    install_api(ctx, fx)
    eapi = EditorApi(fx)
    eapi.install(ctx)
    page = ctx.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + path); page.wait_for_timeout(settle)
    return ctx, page, fx, eapi, errors


def rect_of(page, selector):
    return page.locator(selector).bounding_box()


def mid(r):
    return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2


def focused_tv(page):
    """The col_index of the selected TV's box, or None."""
    return page.evaluate("() => { const b = document.querySelector('.wall-setup-box.is-focus'); return b ? Number(b.dataset.col) : null; }")


def toast_count(page):
    return page.evaluate("document.getElementById('toast-container').children.length")


def finger_drag(page, x0, y0, x1, y1, steps=12, step_ms=16, between=None):
    """One finger from (x0, y0) to (x1, y1), CSS px, as CDP touch events: real
    pointerType "touch", so a stage without touch-action: none gets a
    pointercancel and the drag fails, as it would on a phone. `between` runs
    after every step (EditorApi.pump)."""
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0, "id": 1}]})
        for i in range(1, steps + 1):
            f = i / steps
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove",
                     "touchPoints": [{"x": x0 + (x1 - x0) * f, "y": y0 + (y1 - y0) * f, "id": 1}]})
            page.wait_for_timeout(step_ms)
            if between: between()
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    finally:
        cdp.detach()


def finger_pinch(page, cx, cy, d0, d1, steps=10, step_ms=16):
    """Two fingers on a horizontal line through (cx, cy), from d0 to d1 px apart."""
    def points(d):
        return [{"x": cx - d / 2, "y": cy, "id": 1}, {"x": cx + d / 2, "y": cy, "id": 2}]
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": points(d0)})
        for i in range(1, steps + 1):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": points(d0 + (d1 - d0) * i / steps)})
            page.wait_for_timeout(step_ms)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    finally:
        cdp.detach()


@flow("wall setup editor: every TV's box sits where the wall puts it, over the video playing muted")
def _(browser):
    w = free_wall(tvs=3)
    w["cells"][2].update(place_x=0.7, place_y=0.1, place_w=0.25, place_h=0.5)
    ctx, page, fx, eapi, errors = open_editor_page(browser, path="/wall-setup?wall=7", free_walls=[w])
    st = rect_of(page, ".wall-setup-stage")
    assert abs(st["height"] - st["width"] * 720 / 3840) <= 1, st          # the video's own shape
    want = {0: (0, 0, 1 / 3, 1), 1: (1 / 3, 0, 1 / 3, 1), 2: (0.7, 0.1, 0.25, 0.5)}
    for col, expected in want.items():
        b = rect_of(page, f'.wall-setup-box[data-col="{col}"]')
        got = ((b["x"] - st["x"]) / st["width"], (b["y"] - st["y"]) / st["height"],
               b["width"] / st["width"], b["height"] / st["height"])
        assert all(abs(g - e) < 0.01 for g, e in zip(got, expected)), (col, got, expected)
        assert page.inner_text(f'.wall-setup-box[data-col="{col}"] .wall-setup-box-name') == f"TV {col + 1}"
    assert page.evaluate("document.querySelectorAll('.wall-setup-box .wall-setup-dot.is-online').length") == 1
    video = page.evaluate("""() => { const v = document.querySelector('.wall-setup-stage video');
        return v && { src: v.getAttribute('src'), muted: v.muted, loop: v.loop, inline: v.playsInline, autoplay: v.autoplay }; }""")
    assert video == {"src": "https://api.test/uploads/c.mp4", "muted": True, "loop": True, "inline": True, "autoplay": True}, video
    assert page.evaluate("getComputedStyle(document.querySelector('.wall-setup-stage')).touchAction") == "none"
    assert focused_tv(page) is None
    assert page.inner_text(".wall-setup-hint") == "Tap a TV's box to move or resize it."
    assert not page.is_visible(".wall-setup-done")
    assert page.evaluate("[...document.querySelectorAll('.wall-setup-tv')].map(b => b.dataset.col)") == ["0", "1", "2"]
    assert eapi.puts == [], eapi.puts
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: one finger drags TV 2's box 60 px, and the last placement sent is where it stopped")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    assert focused_tv(page) == 1, focused_tv(page)
    assert eapi.puts[0][0] == 1 and eapi.puts[0][1]["highlight"] is True, eapi.puts   # selecting TV 2 lights its outline
    st, b0 = rect_of(page, ".wall-setup-stage"), rect_of(page, ".wall-setup-box.is-focus")
    sent = len(eapi.puts)
    x, y = mid(b0)
    finger_drag(page, x, y, x + 60, y)
    eapi.settle(page)
    b1 = rect_of(page, ".wall-setup-box.is-focus")
    assert abs((b1["x"] - b0["x"]) - 60) <= 1.5 and abs(b1["y"] - b0["y"]) <= 0.5, (b0, b1)
    assert len(eapi.puts) > sent, eapi.puts
    col, last = eapi.puts[-1]
    assert col == 1 and last["highlight"] is True, eapi.puts[-1]
    assert abs(last["x"] - (1 / 3 + 60 / st["width"])) < 0.005, (last, st)
    assert abs(last["x"] * st["width"] - (b1["x"] - st["x"])) <= 1.5, (last, b1, st)   # what is sent is what is drawn
    assert abs(last["y"]) < 1e-9 and abs(last["w"] - 1 / 3) < 1e-9 and abs(last["h"] - 1) < 1e-9, last
    assert abs(eapi.wall()["cells"][1]["place_x"] - last["x"]) < 1e-12
    page.wait_for_timeout(3500)                    # a re-read after the save leaves the box where it is
    assert abs(rect_of(page, ".wall-setup-box.is-focus")["x"] - b1["x"]) <= 0.5
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7&tv=2"
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: on a slow network one placement is in flight at a time, and the last one carries the final box")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    eapi.delay_ms, eapi.max_in_flight = 500, 0
    sent = len(eapi.puts)
    st, b0 = rect_of(page, ".wall-setup-stage"), rect_of(page, ".wall-setup-box.is-focus")
    x, y = mid(b0)
    finger_drag(page, x, y, x + 60, y, steps=20, step_ms=40, between=eapi.pump)   # about 1 s of moves
    eapi.settle(page)
    moves = eapi.puts[sent:]
    assert eapi.max_in_flight == 1, eapi.max_in_flight
    assert 2 <= len(moves) <= 5, moves             # 20 moves, but each PUT waits for the last answer (500 ms)
    b1 = rect_of(page, ".wall-setup-box.is-focus")
    col, last = moves[-1]
    assert col == 1 and abs(last["x"] - (1 / 3 + 60 / st["width"])) < 0.005, (last, st)
    assert abs(last["x"] * st["width"] - (b1["x"] - st["x"])) <= 1.5, (last, b1, st)
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: a box dragged within 3% of its neighbour snaps flush to it, and dragging on lets go")
def _(browser):
    w = free_wall()
    w["cells"][1]["place_x"] = 0.5               # a gap between TV 1 (0 to 1/3) and TV 2
    ctx, page, fx, eapi, errors = open_editor_page(browser, free_walls=[w])
    eapi.settle(page)
    st, b0 = rect_of(page, ".wall-setup-stage"), rect_of(page, ".wall-setup-box.is-focus")
    x, y = mid(b0)
    finger_drag(page, x, y, x - (0.5 - 1 / 3 - 0.015) * st["width"], y)   # stops 1.5% short of TV 1's right edge
    eapi.settle(page)
    col, last = eapi.puts[-1]
    assert col == 1 and abs(last["x"] - 1 / 3) < 1e-9, last              # flush: its left edge on TV 1's right edge
    tv1, tv2 = rect_of(page, '.wall-setup-box[data-col="0"]'), rect_of(page, ".wall-setup-box.is-focus")
    assert abs(tv2["x"] - (tv1["x"] + tv1["width"])) <= 1, (tv1, tv2)
    x, y = mid(tv2)
    finger_drag(page, x, y, x - 0.08 * st["width"], y)                     # 8% further: past the 3% pull
    eapi.settle(page)
    col, last = eapi.puts[-1]
    assert abs(last["x"] - (1 / 3 - 0.08)) < 0.005, last
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: two fingers and the size slider resize the box around its centre, in its TV's shape")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    x, y = mid(rect_of(page, ".wall-setup-box.is-focus"))
    finger_pinch(page, x, y, 40, 80)                  # the fingers end twice as far apart
    eapi.settle(page)
    col, last = eapi.puts[-1]
    assert col == 1 and abs(last["w"] - 2 / 3) < 0.02 and abs(last["h"] - 2) < 0.06, last
    assert abs(last["w"] / last["h"] - 1 / 3) < 1e-9, last        # still 16:9 in the 3840x720 video's pixels
    assert abs(last["x"] + last["w"] / 2 - 0.5) < 1e-9 and abs(last["y"] + last["h"] / 2 - 0.5) < 1e-9, last
    assert int(page.input_value(".wall-setup-size-input")) >= 195
    page.evaluate("""() => { const s = document.querySelector('.wall-setup-size-input');
        s.value = '50'; s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    eapi.settle(page)
    col, last = eapi.puts[-1]
    assert abs(max(last["w"], last["h"]) - 0.5) < 1e-9 and abs(last["w"] / last["h"] - 1 / 3) < 1e-9, last
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: tapping another TV's box, or its button, selects that TV and lights its outline")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    page.locator('.wall-setup-box[data-col="0"]').tap()
    eapi.settle(page)
    assert focused_tv(page) == 0, focused_tv(page)
    assert eapi.puts[-1][0] == 0 and eapi.puts[-1][1]["highlight"] is True, eapi.puts[-1]
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7&tv=1"
    assert page.get_attribute('.wall-setup-tv[data-col="0"]', "aria-pressed") == "true"
    assert page.get_attribute('.wall-setup-tv[data-col="1"]', "aria-pressed") == "false"
    page.click('.wall-setup-tv[data-col="1"]')
    eapi.settle(page)
    assert focused_tv(page) == 1 and eapi.puts[-1][0] == 1, eapi.puts[-1]
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7&tv=2"
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: a box hanging almost off the video drags from anywhere on the video, and a tap still selects the TV under it")
def _(browser):
    w = free_wall(canvas=(1920, 1080))
    w["cells"][0].update(place_x=0.0, place_y=0.0, place_w=1.0, place_h=1.0)    # TV 1 covers the whole video
    w["cells"][1].update(place_x=0.95, place_y=0.0, place_w=1.0, place_h=1.0)   # TV 2: only 5% on it, a sliver
    ctx, page, fx, eapi, errors = open_editor_page(browser, free_walls=[w])
    eapi.settle(page)
    assert focused_tv(page) == 1, focused_tv(page)
    st = rect_of(page, ".wall-setup-stage")
    x, y = mid(st)                                     # on TV 1's box, far from TV 2's sliver
    finger_drag(page, x, y, x - 60, y)
    eapi.settle(page)
    assert focused_tv(page) == 1, focused_tv(page)     # the drag moved TV 2; it did not select TV 1
    col, last = eapi.puts[-1]
    assert col == 1 and abs(last["x"] - (0.95 - 60 / st["width"])) < 0.005, (last, st)
    assert abs(last["y"]) < 1e-9 and abs(last["w"] - 1) < 1e-9 and abs(last["h"] - 1) < 1e-9, last
    assert eapi.wall()["cells"][0]["place_x"] == 0.0                     # TV 1 stayed where it was
    assert all(c == 1 for c, _ in eapi.puts), eapi.puts
    sent = len(eapi.puts)
    page.locator('.wall-setup-box[data-col="0"]').tap()                  # a tap with no move: TV 1
    eapi.settle(page)
    assert focused_tv(page) == 0, focused_tv(page)
    assert eapi.puts[sent:] and all(c == 0 for c, _ in eapi.puts[sent:]), eapi.puts[sent:]
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7&tv=1"
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: Done takes the outline off the TV and says it is placed, and Finish opens this wall on Video walls")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    page.click(".wall-setup-done")
    eapi.settle(page)
    col, last = eapi.puts[-1]
    assert col == 1 and last["highlight"] is False, eapi.puts[-1]
    assert abs(last["x"] - 1 / 3) < 1e-9 and abs(last["w"] - 1 / 3) < 1e-9, last   # the box stays where it was
    # Opened at /wall-setup?wall=7&tv=2, not right after Add this TV on /pair: no next QR code (R17).
    assert page.inner_text(".wall-setup-placed-text") == "TV 2 placed."
    assert focused_tv(page) is None
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7"
    page.click(".wall-setup-finish"); page.wait_for_timeout(800)
    assert views(page) == ["dashboard"], views(page)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert page.url.endswith("/walls"), page.url
    assert page.is_visible("#walls-editor"), "Finish opens this wall's editor on Video walls (R16)"
    assert page.inner_text("#walls-editor-title") == "Lobby wall"
    reads = fx.gets.count("/walls/7")                  # taken after the Walls editor's own read of the wall
    page.wait_for_timeout(3500)
    assert fx.gets.count("/walls/7") == reads, fx.gets      # the 3 s re-read stopped with the page
    assert page.evaluate("document.querySelectorAll('#wall-setup-view video').length") == 0
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: after Add this TV on /pair the new TV is selected, its Done says to scan the next TV, and Back returns to the pair form")
def _(browser):
    ctx, page, fx, errors = open_pair(browser, init_script=remembered(7))
    eapi = EditorApi(fx)
    eapi.install(ctx)
    h0 = page.evaluate("history.length")
    to_wall_steps(page)
    page.click("#wall-setup-next")                     # Add this TV: PairFixture adds it to wall 7 as TV 3
    page.wait_for_selector('.wall-setup-box[data-col="2"].is-focus')
    eapi.settle(page)
    assert address(page) == "/wall-setup?wall=7&tv=3", page.url
    # R19: startFromPair pushed one entry at /pair, and the editor took it over.
    assert page.evaluate("history.length") == h0 + 1, (h0, page.evaluate("history.length"))
    assert eapi.puts and eapi.puts[0][0] == 2 and eapi.puts[0][1]["highlight"] is True, eapi.puts
    assert page.inner_text(".wall-setup-waiting") == "Waiting for TV 3 to connect…"
    page.click(".wall-setup-done")
    assert page.inner_text(".wall-setup-placed-text") == "TV 3 placed. Point your camera at the next TV's QR code."
    page.click('.wall-setup-tv[data-col="0"]'); page.click(".wall-setup-done")   # a TV that was already there
    assert page.inner_text(".wall-setup-placed-text") == "TV 1 placed."
    eapi.settle(page)
    assert eapi.puts[-1][0] == 0 and eapi.puts[-1][1]["highlight"] is False, eapi.puts[-1]
    page.go_back(); page.wait_for_timeout(800)
    assert views(page) == ["pair-view"], views(page)
    assert address(page) == "/pair?code=ABCD23", page.url
    assert page.evaluate("document.querySelectorAll('#wall-setup-view video').length") == 0
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: Done then Finish at once, on a slow network, still takes the outline off the TV")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    eapi.delay_ms = 500
    st, b0 = rect_of(page, ".wall-setup-stage"), rect_of(page, ".wall-setup-box.is-focus")
    x, y = mid(b0)
    finger_drag(page, x, y, x + 60, y, between=eapi.pump)    # a move is in flight...
    page.click(".wall-setup-done"); page.click(".wall-setup-finish")   # ...when the person leaves
    eapi.settle(page)
    assert views(page) == ["dashboard"], views(page)
    assert page.is_visible("#walls-editor") and page.inner_text("#walls-editor-title") == "Lobby wall"
    col, last = eapi.puts[-1]
    assert col == 1 and last["highlight"] is False, eapi.puts[-1]
    assert abs(last["x"] - (1 / 3 + 60 / st["width"])) < 0.005, (last, st)
    assert eapi.max_in_flight == 1, eapi.max_in_flight
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: Remove TV asks first; Cancel keeps the TV, and confirming unpairs it and drops its box")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    page.click(".wall-setup-remove")
    assert page.inner_text(".confirm-dialog-header h3") == "Remove TV 2 from the wall?"
    page.click(".confirm-dialog-cancel"); page.wait_for_timeout(300)
    assert eapi.deletes == [] and focused_tv(page) == 1, eapi.deletes
    page.click(".wall-setup-remove"); page.click(".confirm-dialog-confirm"); page.wait_for_timeout(600)
    assert eapi.deletes == ["/walls/7/cells/0/1/pairing"], eapi.deletes
    assert page.query_selector('.wall-setup-box[data-col="1"]') is None
    assert page.query_selector('.wall-setup-tv[data-col="1"]') is None
    assert page.inner_text(".wall-setup-note") == "TV 2 was removed from the wall."
    assert page.inner_text("#wall-setup-sub") == "1 TV"
    assert page.evaluate("location.pathname + location.search") == "/wall-setup?wall=7"
    page.wait_for_timeout(3500)                        # the next re-read agrees: it is gone
    assert page.query_selector('.wall-setup-box[data-col="1"]') is None
    assert not page.is_visible(".wall-setup-empty")    # TV 1 is still in the wall
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: removing the last TV keeps the wall, says how to add one, and links back to Video walls")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser, path="/wall-setup?wall=7&tv=1", free_walls=[free_wall(tvs=1)])
    eapi.settle(page)
    assert focused_tv(page) == 0, focused_tv(page)
    assert not page.is_visible(".wall-setup-empty")
    page.click(".wall-setup-remove"); page.click(".confirm-dialog-confirm"); page.wait_for_timeout(600)
    assert eapi.deletes == ["/walls/7/cells/0/0/pairing"], eapi.deletes
    assert page.query_selector(".wall-setup-box") is None
    assert page.inner_text(".wall-setup-empty-text") == \
        "No TVs in this wall yet. Scan a TV's QR code and choose Part of a video wall."
    assert page.inner_text(".wall-setup-note") == "TV 1 was removed from the wall."
    assert page.inner_text("#wall-setup-sub") == "0 TVs"
    assert not page.is_visible(".wall-setup-hint") and not page.is_visible(".wall-setup-done")
    back = page.locator(".wall-setup-empty-back")
    assert back.is_visible() and back.get_attribute("href") == "/walls"
    assert back.inner_text().strip() == "Back to video walls"
    page.wait_for_timeout(3500)                        # a re-read later: the wall is still there, still empty
    assert page.is_visible(".wall-setup-empty")
    assert not [c for c in fx.calls if c[0] == "DELETE"], fx.calls   # nothing deletes the wall (R18)
    back.click(); page.wait_for_timeout(500)
    assert views(page) == ["dashboard"], views(page)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert page.url.endswith("/walls"), page.url
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: the 3 s re-read brings a TV's screen size, its online dot and the video's new file")
def _(browser):
    w = free_wall()
    w["cells"][1].update(screen_w_px=None, screen_h_px=None)        # TV 2 has not connected yet
    ctx, page, fx, eapi, errors = open_editor_page(browser, free_walls=[w])
    eapi.settle(page)
    assert page.inner_text(".wall-setup-waiting") == "Waiting for TV 2 to connect…"
    b = rect_of(page, ".wall-setup-box.is-focus")
    assert abs(b["width"] / b["height"] - 16 / 9) < 0.05, b          # 16:9 until the TV says otherwise
    eapi.wall()["cells"][1].update(screen_w_px=1024, screen_h_px=768, online=True)   # TV 2 connects: a 4:3 screen
    eapi.wall()["video"]["url"] = "/uploads/c.opt.mp4"                 # the transcode renamed the file
    page.wait_for_timeout(3500)
    eapi.settle(page)
    assert not page.is_visible(".wall-setup-waiting")
    col, last = eapi.puts[-1]
    assert col == 1 and abs(last["w"] - 1 / 3) < 1e-9 and abs(last["h"] - 4 / 3) < 1e-9 and abs(last["y"] + 1 / 6) < 1e-9, last
    b = rect_of(page, ".wall-setup-box.is-focus")
    assert abs(b["width"] / b["height"] - 4 / 3) < 0.05, b
    assert page.evaluate("document.querySelectorAll('.wall-setup-box .wall-setup-dot.is-online').length") == 2
    assert page.get_attribute(".wall-setup-stage video", "src") == "https://api.test/uploads/c.opt.mp4"
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: once a TV reports its screen, even mid-drag, every box sent is in its shape, as the server stores it")
def _(browser):
    w = free_wall()
    w["cells"][1].update(screen_w_px=None, screen_h_px=None)        # TV 2 has not reported yet: 16:9
    ctx, page, fx, eapi, errors = open_editor_page(browser, free_walls=[w])
    eapi.settle(page)
    portrait = lambda b: abs((b["w"] * 3840) / (b["h"] * 720) - 1080 / 1920) < 1e-9   # TV 2's shape, in video pixels
    col, first = eapi.puts[-1]
    assert col == 1 and abs(first["w"] / first["h"] - 1 / 3) < 1e-9, first            # 16:9 so far
    sent, marks = len(eapi.puts), []
    def report_mid_drag():
        if not marks:   # the finger has started to move: TV 2 connects, a portrait screen
            eapi.wall()["cells"][1].update(screen_w_px=1080, screen_h_px=1920, online=True)
        marks.append(len(eapi.puts))
    x, y = mid(rect_of(page, ".wall-setup-box.is-focus"))
    # About 4 s of moves, so one of the editor's 3 s re-reads lands while the finger is down.
    finger_drag(page, x, y, x + 60, y, steps=100, step_ms=40, between=report_mid_drag)
    while_down = [b for _, b in eapi.puts[sent:marks[-1]]]
    eapi.settle(page)
    # R8: the phone reshaped the box as soon as it knew, not at the re-read after the finger lifted.
    assert any(portrait(b) for b in while_down), while_down
    col, last = eapi.puts[-1]
    assert col == 1 and portrait(last), last
    assert abs(last["w"] - 1 / 3) < 1e-9, last                     # the same width (and centre) as the server keeps
    st, b = rect_of(page, ".wall-setup-stage"), rect_of(page, ".wall-setup-box.is-focus")
    assert abs(b["width"] - last["w"] * st["width"]) <= 1.5 and abs(b["height"] - last["h"] * st["height"]) <= 1.5, (b, last, st)
    assert not page.is_visible(".wall-setup-waiting")
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: a failed save shows under the video, never as a toast, and a 401 signs out")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser)
    eapi.settle(page)
    eapi.put_error = (500, {"detail": "boom"})
    page.click(".wall-setup-done")
    eapi.settle(page)
    assert page.inner_text(".wall-setup-save-error") == "The server ran into a problem. Try again in a moment."
    assert toast_count(page) == 0
    eapi.put_error = (401, {"detail": "Not authenticated"})
    page.locator('.wall-setup-box[data-col="0"]').tap()
    page.wait_for_timeout(800)
    assert views(page) == ["auth-panel"], views(page)
    assert page.evaluate("document.getElementById('wall-setup-view').children.length") == 0
    assert toast_count(page) == 0
    assert not errors, errors
    ctx.close()


@flow("wall setup editor: in Arabic the words are Arabic, and the video and its boxes stay left-to-right")
def _(browser):
    ctx, page, fx, eapi, errors = open_editor_page(browser, lang="ar")
    eapi.settle(page)
    assert page.evaluate("document.documentElement.dir") == "rtl"
    assert page.evaluate("getComputedStyle(document.querySelector('.wall-setup-stage')).direction") == "ltr"
    assert page.evaluate("getComputedStyle(document.querySelector('.wall-setup-panel')).direction") == "rtl"
    assert page.inner_text('.wall-setup-box[data-col="0"] .wall-setup-box-name') == "شاشة 1"
    assert page.inner_text(".wall-setup-done").strip() == "تم"
    assert page.inner_text(".wall-setup-remove").strip() == "إزالة الشاشة"
    assert page.inner_text(".wall-setup-hint") == "اسحب بإصبع واحد في أي مكان على الفيديو لتحريك المربع. ولتغيير حجمه، باعد بين إصبعين أو قرّبهما، أو استخدم شريط الحجم."
    st, tv1 = rect_of(page, ".wall-setup-stage"), rect_of(page, '.wall-setup-box[data-col="0"]')
    assert abs(tv1["x"] - st["x"]) <= 1, (st, tv1)           # TV 1 on the video's left, as on the wall
    x, y = mid(rect_of(page, ".wall-setup-box.is-focus"))
    finger_drag(page, x, y, x + 60, y)
    eapi.settle(page)
    assert eapi.puts[-1][1]["x"] > 1 / 3 + 0.1, eapi.puts[-1]     # a drag to the right moves the box right
    page.click(".wall-setup-done")
    assert page.inner_text(".wall-setup-placed-text") == "تم وضع الشاشة 2."      # Adjust mode (R17)
    assert page.inner_text(".wall-setup-finish").strip() == "إنهاء"
    assert toast_count(page) == 0 and not errors, errors
    ctx.close()


@flow("wall setup editor: on phones the editor fits the screen and its controls are finger-sized")
def _(browser):
    w = free_wall(tvs=8)
    for i, cell in enumerate(w["cells"]):                    # two rows of four
        cell.update(place_x=(i % 4) / 4, place_y=(i // 4) / 2, place_w=0.25, place_h=0.5)
    for device in ("iphone", "android360"):
        ctx, page, fx, eapi, errors = open_editor_page(browser, device=device, free_walls=[copy.deepcopy(w)])
        m = page.evaluate("() => ({ w: innerWidth, sw: document.documentElement.scrollWidth })")
        assert m["sw"] <= m["w"] + 1, (device, m)
        st = rect_of(page, ".wall-setup-stage")
        assert st["x"] >= 0 and st["x"] + st["width"] <= m["w"], (device, st)
        for sel in (".wall-setup-done", ".wall-setup-remove", '.wall-setup-tv[data-col="7"]', ".wall-setup-size-input"):
            b = rect_of(page, sel)
            assert b["height"] >= 44, (device, sel, b)
            assert sel == ".wall-setup-size-input" or b["width"] >= 44, (device, sel, b)
        assert not errors, (device, errors)
        ctx.close()


@flow("wall setup editor: a wall that shows a picture draws the picture under the boxes")
def _(browser):
    w = free_wall()
    w["video"].update(mime_type="image/jpeg", url="/uploads/a.jpg", name="espresso-hero.jpg")
    ctx, page, fx, eapi, errors = open_editor_page(browser, path="/wall-setup?wall=7", free_walls=[w])
    assert page.query_selector(".wall-setup-stage video") is None
    assert page.get_attribute(".wall-setup-stage img", "src") == "https://api.test/uploads/a.jpg"
    assert page.query_selector('.wall-setup-box[data-col="1"]') is not None
    assert not errors, errors
    ctx.close()


if __name__ == "__main__":
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && bash scripts/ui/run.sh wall_setup_flows.py "wall setup editor"`
Expected: FAIL, exit code 1. There are eighteen `FAIL wall setup editor: ...` lines and `18 failed`. Each fails because the wall's page has no editor yet. Thirteen time out waiting for something the editor draws: `TimeoutError('Locator.bounding_box: Timeout 5000ms exceeded. ... waiting for locator(".wall-setup-stage")')` (the first, slow-network, snap, Done-then-Finish and phone flows), `.wall-setup-box.is-focus` (the pinch flow), `Locator.tap` on `.wall-setup-box[data-col="0"]` (the tap flow), `Page.click` on `.wall-setup-done` (the Done flow and the failed-save flow), `Page.click` on `.wall-setup-remove` (Remove TV), `Page.inner_text` on `.wall-setup-waiting` (the re-read flow), `Page.get_attribute` on `.wall-setup-stage img` (the picture flow), and `Page.wait_for_selector` on `.wall-setup-box[data-col="2"].is-focus` (the flow from /pair: Task 13's stub opens the wall's page, which has no editor). The drag flow, the box-off-the-video flow and the last-TV flow fail with `AssertionError(None)` from `focused_tv(page)`; the mid-drag shape flow with `IndexError('list index out of range')` (no PUT was sent); the Arabic flow with `Error("Page.evaluate: TypeError: Failed to execute 'getComputedStyle' ...")`. The flows of Tasks 12 and 13 are not run (the name filter skips them). (Checked while planning: Tasks 11-13 applied from the plan text to an archive of 24101d4, plus this step, in a throwaway `signage_renderer:latest` container.)

- [ ] **Step 3: Implement the editor in `frontend/wall-setup.js`**

In `frontend/wall-setup.js`, replace:

```js
  function tvCount(wall) {
    return (wall.cells || []).filter((cell) => cell.screen_id != null).length;
  }
```

with:

```js
  function tvCount(wall) {
    return (wall.cells || []).filter((cell) => cell.screen_id != null).length;
  }

  /* ── Placement editor ──────────────────────────────────────────────
     renderReady() mounts it: the stage in #wall-setup-editor (an LTR island:
     the video is physical, so its left is the wall's left in Arabic too) and,
     after it, a panel in the page's own direction with a button per TV, the
     size slider, Done and Remove TV.

     A box is one TV's part of the video: {x, y, w, h}, fractions of the canvas
     (the video), stored in the cell's place_* columns. Every box is drawn in %
     of the stage, in its TV's shape once the TV has reported its screen, as
     the server stores it (ruling R8). The selected TV's box follows one
     finger (drag, from anywhere on the video), two (pinch) and the slider,
     and each change goes to PUT /walls/{id}/cells/0/{col}/placement, which
     moves that TV's picture live. One PUT is in flight at a time. When it
     returns, the newest box goes out if it changed meanwhile, so moves never
     queue up behind a slow network and the last position always lands, even
     when the page is left straight after. The wall is re-read every 3 s for
     TV sizes, online dots, moves made on another phone, and the video's new
     file name once its transcode has finished. stopEditor() (from show() and
     reset()) ends the re-reads and the video. */
  const EDITOR_REREAD_MS = 3000;
  const EDITOR_SAVE_GAP_MS = 50;      // at most 20 PUTs a second; the API allows 1800 a minute
  const EDITOR_DRAG_SLOP_PX = 6;      // a finger that starts off the selected box drags it after this far; less is a tap
  const EDITOR_SIZE_MIN = 5;          // the size slider: the box's larger side, in % of the video's
  const EDITOR_SIZE_MAX = 200;

  // The editor on the page, or null. Every answer that arrives later checks
  // that `ed` is still the same object before it touches the page.
  let ed = null;

  // The TV the /pair steps have just added, {wallId, col}, from openEditor
  // until the editor mounts for that wall: its Done then says to scan the
  // next TV's QR code (ruling R17). reset() drops it.
  let justAdded = null;

  /** A wallsetup.editor.* string, with {n} filled in when n is given. */
  function edText(key, fallback, n) {
    const text = Khan.t(`wallsetup.editor.${key}`, fallback);
    return n == null ? text : text.replace("{n}", String(n));
  }

  /** Change an element's words only when they differ. #wall-setup-view is an
   *  aria-live region and every 3 s re-read redraws the panel: WebKit
   *  replaces the text node even when the words are the same, and a screen
   *  reader would read "2 TVs" and the hint out again. */
  function setText(el, text) {
    if (el && el.textContent !== text) el.textContent = text;
  }

  /** "TV 2" in the UI's language: the words the TV's own outline shows. */
  function tvLabel(col) {
    return edText("tv_label", "TV {n}", col + 1);
  }

  /** Errors of the editor's own promises go to the console; none of them
   *  may reach app.js's global handler, which would toast it. */
  function edAsync(promise) {
    promise.catch((err) => console.error(err));
  }

  /** A cell's box, or null when it has none. */
  function boxOf(cell) {
    if (!cell) return null;
    const box = { x: cell.place_x, y: cell.place_y, w: cell.place_w, h: cell.place_h };
    const numbers = [box.x, box.y, box.w, box.h].every((v) => typeof v === "number" && Number.isFinite(v));
    return numbers && box.w > 0 && box.h > 0 ? box : null;
  }

  function setCellBox(cell, box) {
    cell.place_x = box.x;
    cell.place_y = box.y;
    cell.place_w = box.w;
    cell.place_h = box.h;
  }

  /** The TVs the editor places: row-0 cells with a screen and a box, by TV number. */
  function editorCells(wall) {
    return ((wall && wall.cells) || [])
      .filter((cell) => cell.row_index === 0 && cell.screen_id != null && boxOf(cell))
      .sort((a, b) => a.col_index - b.col_index);
  }

  function cellAt(wall, col) {
    if (col == null) return null;
    return editorCells(wall).find((cell) => cell.col_index === col) || null;
  }

  /** The canvas in px: the video's size (the server's 3840x2160 fallback if unknown). */
  function canvasOf(wall) {
    const w = Number(wall.canvas_width_px);
    const h = Number(wall.canvas_height_px);
    return w > 0 && h > 0 ? { w, h } : { w: 3840, h: 2160 };
  }

  /** The box as the server stores it (ruling R8). Once the TV has reported
   *  its screen size, PUT .../placement keeps the box's width and centre and
   *  gives it the screen's shape (wall_layout.fit_box_to_screen, which
   *  WallGeometry.boxForScreen ports step for step), so the phone does the
   *  same before it draws or sends a box: what the phone shows is what the
   *  server keeps and the TV plays. Before the report the box stays as it is
   *  (16:9, the server's first guess). A box that already has the shape
   *  comes back the same, within WallGeometry.sameBox. */
  function shapeFor(cell, box, canvas) {
    const sw = Number(cell.screen_w_px);
    const sh = Number(cell.screen_h_px);
    if (!(sw > 0 && sh > 0)) return box;
    return WallGeometry.boxForScreen(box, sw, sh, canvas.w, canvas.h);
  }

  /** The TV the address selects (/wall-setup?wall=<id>&tv=<n>), as a col_index. */
  function addressTvCol() {
    if (location.pathname !== PATH) return null;
    const n = new URLSearchParams(location.search).get("tv") || "";
    return /^[1-8]$/.test(n) ? Number(n) - 1 : null;
  }

  /** Keep the address in step with the selected TV, in the same history
   *  entry, so a refresh comes back to the same TV. */
  function writeEditorAddress(me) {
    if (addressWallId() !== me.wallId) return;
    const want = `${PATH}?wall=${me.wallId}${me.focusCol != null ? `&tv=${me.focusCol + 1}` : ""}`;
    if (location.pathname + location.search !== want) history.replaceState(history.state, "", want);
  }

  /** Open the placement editor on a free wall with one TV selected:
   *  `focusCol` is that cell's col_index (TV 1 is 0), or null for none.
   *  Task 13's steps on /pair call it with the answer of POST /walls/free
   *  (col 0) or POST /walls/{id}/tvs (the new cell's col_index). The address
   *  becomes /wall-setup?wall=<id>&tv=<col + 1> in the CURRENT history entry,
   *  never a new one (ruling R19): from the steps that is the one entry
   *  startFromPair pushed at /pair, so Back from the editor returns to the
   *  pair form; show() then finds its address set and pushes nothing.
   *  Called from the steps, it also remembers the TV just added (R17).
   *  Never rejects. */
  function openEditor(wallId, focusCol) {
    const id = parseWallId(wallId);
    if (id === null) return show(null);
    const col = Number.isInteger(focusCol) && focusCol >= 0 && focusCol < 8 ? focusCol : null;
    const url = `${PATH}?wall=${id}${col !== null ? `&tv=${col + 1}` : ""}`;
    if (location.pathname + location.search !== url) history.replaceState(history.state, "", url);
    justAdded = flow && col !== null ? { wallId: id, col } : null;
    const me = ed;
    if (me && me.wallId === id && current.wallId === id && current.status === "ready" && isShowing()) {
      if (justAdded) { me.addedCol = justAdded.col; justAdded = null; }
      if (col !== null && cellAt(me.wall, col)) focusTv(col);
      else me.wantFocus = col;          // a TV added a moment ago: the read below has it
      edAsync(rereadWall());
      return Promise.resolve();
    }
    return show(id);
  }

  /** Build the editor for a free wall in #wall-setup-editor (renderReady). */
  function mountEditor(wall) {
    stopEditor();
    const editor = view() && view().querySelector(".wall-setup-editor");
    if (!editor || !editor.parentElement) return;
    const me = {
      wallId: wall.id, wall, canvas: canvasOf(wall), session: authToken,
      focusCol: null, placedCol: null, wantFocus: addressTvCol(),
      // The TV the /pair steps just added to this wall, or null (Adjust mode).
      addedCol: justAdded && justAdded.wallId === wall.id ? justAdded.col : null,
      els: null, boxEls: new Map(), tvsOrder: null, mediaKey: null,
      pointers: new Map(), gesture: null, noClickUntil: 0,
      queue: new Map(), inFlight: null, lastSendAt: -Infinity, sendTimer: null, draining: false,
      savedAt: new Map(), removedAt: new Map(), removing: null,
      readSeq: 0, rereadTimer: null,
    };
    justAdded = null;
    ed = me;
    editor.innerHTML = `
      <div class="wall-setup-stage" role="img" aria-label="${escHtml(edText("canvas_label", "The wall's video, with a box for each TV"))}">
        <div class="wall-setup-media"></div>
      </div>`;
    const panel = document.createElement("div");
    panel.className = "wall-setup-panel";
    panel.innerHTML = `
      <div class="wall-setup-tvs" role="group" aria-label="${escHtml(edText("tvs", "TVs in this wall"))}"></div>
      <p class="wall-setup-hint"></p>
      <p class="wall-setup-waiting hidden" role="status"></p>
      <label class="wall-setup-size hidden">
        <span>${escHtml(edText("size", "Size"))}</span>
        <input type="range" class="wall-setup-size-input" min="${EDITOR_SIZE_MIN}" max="${EDITOR_SIZE_MAX}" step="1" value="100" />
      </label>
      <div class="wall-setup-actions hidden">
        <button type="button" class="btn btn-primary wall-setup-done">${escHtml(edText("done", "Done"))}</button>
        <button type="button" class="btn btn-danger wall-setup-remove">${escHtml(edText("remove", "Remove TV"))}</button>
      </div>
      <div class="wall-setup-placed hidden" role="status">
        <p class="wall-setup-placed-text"></p>
        <button type="button" class="btn btn-primary wall-setup-finish">${escHtml(edText("finish", "Finish"))}</button>
      </div>
      <div class="wall-setup-empty hidden" role="status">
        <p class="wall-setup-empty-text"></p>
        <a class="btn btn-ghost wall-setup-empty-back" href="/walls">${escHtml(Khan.t("wallsetup.back", "Back to video walls"))}</a>
      </div>
      <p class="wall-setup-note hidden" role="status"></p>
      <p class="wall-setup-save-error hidden" role="alert"></p>`;
    editor.after(panel);
    const inPanel = (selector) => panel.querySelector(selector);
    me.els = {
      stage: editor.querySelector(".wall-setup-stage"), media: editor.querySelector(".wall-setup-media"),
      tvs: inPanel(".wall-setup-tvs"), hint: inPanel(".wall-setup-hint"), waiting: inPanel(".wall-setup-waiting"),
      size: inPanel(".wall-setup-size"), sizeInput: inPanel(".wall-setup-size-input"),
      actions: inPanel(".wall-setup-actions"), done: inPanel(".wall-setup-done"), remove: inPanel(".wall-setup-remove"),
      placed: inPanel(".wall-setup-placed"), placedText: inPanel(".wall-setup-placed-text"), finish: inPanel(".wall-setup-finish"),
      empty: inPanel(".wall-setup-empty"), emptyText: inPanel(".wall-setup-empty-text"), emptyBack: inPanel(".wall-setup-empty-back"),
      note: inPanel(".wall-setup-note"), error: inPanel(".wall-setup-save-error"),
    };
    const stage = me.els.stage;
    stage.addEventListener("pointerdown", onStagePointerDown);
    stage.addEventListener("pointermove", onStagePointerMove);
    stage.addEventListener("pointerup", onStagePointerEnd);
    stage.addEventListener("pointercancel", onStagePointerEnd);
    stage.addEventListener("click", onStageClick);
    me.els.tvs.addEventListener("click", (e) => {
      const btn = e.target.closest(".wall-setup-tv");
      if (btn && ed === me) focusTv(Number(btn.dataset.col));
    });
    me.els.sizeInput.addEventListener("input", onSizeInput);
    me.els.done.addEventListener("click", onEditorDone);
    me.els.remove.addEventListener("click", () => edAsync(onEditorRemove()));
    me.els.finish.addEventListener("click", () => {
      // Ruling R16: Video walls, with this wall's editor open. showSection
      // leaves this page (hideWallSetupView -> reset -> stopEditor); boxes
      // not yet saved still go out. Walls.openEditor reads the wall again;
      // a failure there is logged, never a global toast.
      const wallId = me.wallId;
      showSection("walls");
      edAsync(Promise.resolve().then(() => Walls.openEditor(wallId)));
    });
    me.els.emptyBack.addEventListener("click", (e) => {
      e.preventDefault();               // the dashboard's own routing, as Back to video walls does
      showSection("walls");
    });
    sizeStage(me);
    setEditorMedia(me, wall.video);
    renderEditor(me);
    takeWantedFocus(me);
    scheduleReread(me, EDITOR_REREAD_MS);
  }

  /** End the editor: its re-reads, gestures and video. Boxes not yet saved
   *  still go out, one at a time and in order (leaving the page straight
   *  after a move, or after Done, must not lose it), but not after a sign-out:
   *  the session they belong to has ended. */
  function stopEditor() {
    const me = ed;
    if (!me) return;
    ed = null;
    me.draining = true;
    if (!sessionLive(me.session)) me.queue.clear();
    clearTimeout(me.rereadTimer);
    me.pointers.clear();
    me.gesture = null;
    if (me.els) unloadMedia(me.els.media);
  }

  /** The stage has the video's shape, but is never taller than 60% of the
   *  screen: a portrait video gets a narrower stage instead. */
  function sizeStage(me) {
    const { w, h } = me.canvas;
    me.els.stage.style.aspectRatio = `${w} / ${h}`;
    me.els.stage.style.maxWidth = `calc(60vh * ${w / h})`;
  }

  /** The wall's video (muted, looping, inline) or picture under the boxes.
   *  The transcode renames the file and deletes the original, so a new url on
   *  a re-read is followed at once. */
  function setEditorMedia(me, video) {
    const url = video && typeof video.url === "string" ? video.url : "";
    const kind = !url ? "none" : /^image\//i.test(video.mime_type || "") ? "image" : "video";
    const src = url ? `${API_BASE}${url}` : "";
    const key = `${kind} ${src}`;
    if (key === me.mediaKey) return;
    me.mediaKey = key;
    const holder = me.els.media;
    const playing = holder.querySelector("video");
    if (kind === "video" && playing) {
      playing.src = src;
      playMuted(playing);
      return;
    }
    unloadMedia(holder);
    if (kind === "video") {
      const v = document.createElement("video");
      v.muted = true;
      v.defaultMuted = true;
      v.loop = true;
      v.autoplay = true;
      v.playsInline = true;
      v.preload = "auto";
      v.src = src;
      holder.appendChild(v);
      playMuted(v);
    } else if (kind === "image") {
      const img = document.createElement("img");
      img.alt = "";
      img.src = src;
      holder.appendChild(img);
    }
  }

  function playMuted(v) {
    try {
      const p = v.play();
      if (p && typeof p.catch === "function") p.catch(() => { /* autoplay refused, or the file is not there yet */ });
    } catch (_) { /* a browser whose play() returns nothing */ }
  }

  function unloadMedia(holder) {
    holder.querySelectorAll("video").forEach((v) => {
      try { v.pause(); v.removeAttribute("src"); v.load(); } catch (_) { /* already gone */ }
    });
    holder.replaceChildren();
  }

  function renderEditor(me) {
    renderBoxes(me);
    renderTvButtons(me);
    renderPanel(me);
  }

  function placeBox(el, box) {
    el.style.left = `${box.x * 100}%`;
    el.style.top = `${box.y * 100}%`;
    el.style.width = `${box.w * 100}%`;
    el.style.height = `${box.h * 100}%`;
  }

  /** One box per TV on the stage, kept (not rebuilt) between renders, so a
   *  re-read never replaces the box under a finger. */
  function renderBoxes(me) {
    const seen = new Set();
    editorCells(me.wall).forEach((cell) => {
      const col = cell.col_index;
      seen.add(col);
      let el = me.boxEls.get(col);
      if (!el) {
        el = document.createElement("div");
        el.className = "wall-setup-box";
        el.dataset.col = String(col);
        el.setAttribute("aria-hidden", "true");   // the TV buttons in the panel are the accessible way to pick one
        el.innerHTML = '<span class="wall-setup-box-label"><span class="wall-setup-dot"></span>' +
          '<span class="wall-setup-box-name" dir="auto"></span></span>';
        me.els.stage.appendChild(el);
        me.boxEls.set(col, el);
      }
      setText(el.querySelector(".wall-setup-box-name"), tvLabel(col));
      el.querySelector(".wall-setup-dot").classList.toggle("is-online", !!cell.online);
      el.classList.toggle("is-focus", col === me.focusCol);
      placeBox(el, boxOf(cell));
    });
    me.boxEls.forEach((el, col) => {
      if (!seen.has(col)) { el.remove(); me.boxEls.delete(col); }
    });
  }

  /** A button per TV: picks it (for a box hidden under another, and for
   *  keyboards), with its online state. Rebuilt only when the TVs change, so
   *  the button that has focus keeps it. */
  function renderTvButtons(me) {
    const cells = editorCells(me.wall);
    const order = cells.map((cell) => cell.col_index).join(",");
    if (order !== me.tvsOrder) {
      me.tvsOrder = order;
      me.els.tvs.innerHTML = cells.map((cell) =>
        `<button type="button" class="btn btn-ghost wall-setup-tv" data-col="${cell.col_index}" aria-pressed="false">` +
        '<span class="wall-setup-dot" aria-hidden="true"></span><span class="wall-setup-tv-name" dir="auto"></span>' +
        '<span class="visually-hidden wall-setup-tv-state"></span></button>').join("");
    }
    cells.forEach((cell) => {
      const btn = me.els.tvs.querySelector(`.wall-setup-tv[data-col="${cell.col_index}"]`);
      const selected = cell.col_index === me.focusCol;
      btn.classList.toggle("is-focus", selected);
      btn.setAttribute("aria-pressed", String(selected));
      btn.querySelector(".wall-setup-dot").classList.toggle("is-online", !!cell.online);
      setText(btn.querySelector(".wall-setup-tv-name"), tvLabel(cell.col_index));
      setText(btn.querySelector(".wall-setup-tv-state"), cell.online
        ? Khan.t("screens.status_online", "On") : Khan.t("screens.status_offline", "Off"));
    });
  }

  function renderPanel(me) {
    const els = me.els;
    const cell = cellAt(me.wall, me.focusCol);
    const placed = !cell && me.placedCol != null;
    const none = !editorCells(me.wall).length;
    setText(els.hint, cell
      ? edText("hint", "Drag anywhere on the video with one finger to move the box. Pinch, or use the slider, to resize it.")
      : edText("pick", "Tap a TV's box to move or resize it."));
    els.hint.classList.toggle("hidden", placed || none);
    const waiting = !!cell && !(Number(cell.screen_w_px) > 0 && Number(cell.screen_h_px) > 0);
    setText(els.waiting, waiting ? edText("waiting", "Waiting for TV {n} to connect…", cell.col_index + 1) : "");
    els.waiting.classList.toggle("hidden", !waiting);
    els.size.classList.toggle("hidden", !cell);
    els.actions.classList.toggle("hidden", !cell);
    els.remove.disabled = !cell || me.removing != null;
    // Ruling R17: after the TV the /pair steps just added, the next TV's QR
    // code comes next; any other TV (Adjust mode) is just placed.
    setText(els.placedText, !placed ? ""
      : me.placedCol === me.addedCol
        ? edText("placed", "TV {n} placed. Point your camera at the next TV's QR code.", me.placedCol + 1)
        : edText("placed_short", "TV {n} placed.", me.placedCol + 1));
    els.placed.classList.toggle("hidden", !placed);
    // Ruling R18: a wall whose last TV was removed stays, and says how to add one.
    setText(els.emptyText, none
      ? edText("empty", "No TVs in this wall yet. Scan a TV's QR code and choose Part of a video wall.") : "");
    els.empty.classList.toggle("hidden", !none);
    if (cell) syncSlider(me, boxOf(cell));
  }

  function syncSlider(me, box) {
    const pct = Math.round(100 * Math.max(box.w, box.h));
    const v = String(Math.min(EDITOR_SIZE_MAX, Math.max(EDITOR_SIZE_MIN, pct)));
    if (me.els.sizeInput.value !== v) me.els.sizeInput.value = v;
  }

  function updateTvCount(me) {
    const sub = document.getElementById("wall-setup-sub");
    setText(sub, pluralText("wallsetup.tv_count", tvCount(me.wall), { one: "{n} TV", other: "{n} TVs" }));
  }

  function showEditorMessage(me, text, isError) {
    const on = isError ? me.els.error : me.els.note;
    const off = isError ? me.els.note : me.els.error;
    setText(on, text);
    on.classList.remove("hidden");
    off.classList.add("hidden");
  }

  function hideEditorMessages(me) {
    me.els.note.classList.add("hidden");
    me.els.error.classList.add("hidden");
  }

  function takeWantedFocus(me) {
    const col = me.wantFocus;
    if (col == null) return;
    me.wantFocus = null;
    if (cellAt(me.wall, col)) focusTv(col);
    else writeEditorAddress(me);     // a TV that is not (or no longer) in the wall: drop &tv from the address
  }

  /** Select a TV (or none, with null). Selecting one lights its outline and
   *  label on the TV for 30 s, so the person sees which TV the box is, and
   *  sends its box in the TV's shape if that changed. */
  function focusTv(col) {
    const me = ed;
    if (!me) return;
    const cell = cellAt(me.wall, col);
    if (cell && col === me.focusCol) return;
    me.focusCol = cell ? col : null;
    me.gesture = null;
    if (cell) {
      me.placedCol = null;
      hideEditorMessages(me);
      const shaped = shapeFor(cell, boxOf(cell), me.canvas);
      setCellBox(cell, shaped);
      queuePlacement(me, col, shaped, true);
    }
    writeEditorAddress(me);
    renderEditor(me);
  }

  /** The selected TV's box moved: put it in its TV's shape (R8: the box the
   *  server will store, also when the TV reported its screen mid-drag),
   *  draw it, set the slider, send it. */
  function moveFocusedBox(me, cell, box) {
    const shaped = shapeFor(cell, box, me.canvas);
    if (WallGeometry.sameBox(shaped, boxOf(cell))) return;
    setCellBox(cell, shaped);
    const el = me.boxEls.get(cell.col_index);
    if (el) placeBox(el, shaped);
    syncSlider(me, shaped);
    queuePlacement(me, cell.col_index, shaped, true);
  }

  /** The stage keeps every finger of the gesture, even off the stage. */
  function capturePointers(me) {
    me.pointers.forEach((_, id) => {
      try { me.els.stage.setPointerCapture(id); } catch (_) { /* that finger has lifted already */ }
    });
  }

  function onStagePointerDown(e) {
    const me = ed;
    if (!me) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    // A primary pointer is the first finger down (or the mouse): anything
    // still listed is a finger whose "up" the stage never saw.
    if (e.isPrimary) { me.pointers.clear(); me.gesture = null; }
    me.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const cell = cellAt(me.wall, me.focusCol);
    if (!cell) return;
    const rect = me.els.stage.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return;
    if (me.pointers.size === 1) {
      // One finger drags the selected box: from the box itself at once, and
      // from anywhere else on the video once it has gone EDITOR_DRAG_SLOP_PX
      // (a box hanging off the video may show only a sliver). Until then it
      // is a tap: nothing is captured, so its click keeps its target, and a
      // tap on another TV's box selects that TV.
      const armed = !!e.target.closest(".wall-setup-box.is-focus");
      me.gesture = { kind: "drag", id: e.pointerId, x0: e.clientX, y0: e.clientY, box: boxOf(cell), rect, moved: false, armed };
      if (!armed) return;
    } else if (me.pointers.size === 2) {
      const [a, b] = Array.from(me.pointers.values());
      me.gesture = { kind: "pinch", dist0: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), box: boxOf(cell), rect, moved: false };
    } else {
      return;
    }
    e.preventDefault();
    capturePointers(me);
  }

  function onStagePointerMove(e) {
    const me = ed;
    if (!me || !me.pointers.has(e.pointerId)) return;
    me.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = me.gesture;
    const cell = g ? cellAt(me.wall, me.focusCol) : null;
    if (!cell) return;
    let box;
    if (g.kind === "drag") {
      if (e.pointerId !== g.id) return;
      if (!g.armed) {
        if (Math.hypot(e.clientX - g.x0, e.clientY - g.y0) < EDITOR_DRAG_SLOP_PX) return;
        g.armed = true;                 // a drag after all, from where the finger first came down
        capturePointers(me);
      }
      // Always the box at the start plus the whole way the finger went, so a
      // snapped edge lets go once the finger is past the 3% pull.
      const moved = WallGeometry.moveBox(g.box, (e.clientX - g.x0) / g.rect.width, (e.clientY - g.y0) / g.rect.height);
      const others = editorCells(me.wall).filter((c) => c !== cell).map(boxOf);
      box = WallGeometry.clampToCanvas(WallGeometry.snapBox(moved, others, me.canvas.w, me.canvas.h));
    } else {
      const [a, b] = Array.from(me.pointers.values());
      box = WallGeometry.clampToCanvas(WallGeometry.scaleBox(g.box, Math.hypot(a.x - b.x, a.y - b.y) / g.dist0));
    }
    g.moved = true;
    moveFocusedBox(me, cell, box);
  }

  function onStagePointerEnd(e) {
    const me = ed;
    if (!me || !me.pointers.has(e.pointerId)) return;
    me.pointers.delete(e.pointerId);
    const g = me.gesture;
    if (!g) return;
    if (g.moved) me.noClickUntil = performance.now() + 400;   // lifting after a drag is not a tap
    if (g.kind === "pinch" && me.pointers.size === 1) {
      // One finger left of a pinch: it drags on from where it is.
      const [id, p] = Array.from(me.pointers.entries())[0];
      const cell = cellAt(me.wall, me.focusCol);
      me.gesture = cell
        ? { kind: "drag", id, x0: p.x, y0: p.y, box: boxOf(cell), rect: me.els.stage.getBoundingClientRect(), moved: g.moved, armed: true }
        : null;
      return;
    }
    if (g.kind === "pinch" || e.pointerId === g.id) me.gesture = null;
  }

  function onStageClick(e) {
    const me = ed;
    if (!me || performance.now() < me.noClickUntil) return;
    const el = e.target.closest(".wall-setup-box");
    if (el) focusTv(Number(el.dataset.col));
  }

  /** The slider sets the box's larger side, in % of the video's, around its
   *  centre and in its TV's shape. */
  function onSizeInput() {
    const me = ed;
    const cell = me && !me.gesture ? cellAt(me.wall, me.focusCol) : null;
    if (!cell) return;
    const box = boxOf(cell);
    const want = Number(me.els.sizeInput.value) / 100;
    const now = Math.max(box.w, box.h);
    if (!(want > 0 && now > 0)) return;
    moveFocusedBox(me, cell, WallGeometry.clampToCanvas(WallGeometry.scaleBox(box, want / now)));
  }

  function onEditorDone() {
    const me = ed;
    const cell = me ? cellAt(me.wall, me.focusCol) : null;
    if (!cell) return;
    // highlight: false takes the outline and label off the TV at once.
    queuePlacement(me, cell.col_index, boxOf(cell), false);
    me.placedCol = cell.col_index;
    me.focusCol = null;
    me.gesture = null;
    hideEditorMessages(me);
    writeEditorAddress(me);
    renderEditor(me);
  }

  async function onEditorRemove() {
    const me = ed;
    const cell = me ? cellAt(me.wall, me.focusCol) : null;
    if (!cell || me.removing != null) return;
    const col = cell.col_index;
    const n = col + 1;
    // confirmDialog puts the title and the button label into the page as
    // HTML; only the message is escaped there.
    const ok = await confirmDialog({
      title: escHtml(edText("remove_title", "Remove TV {n} from the wall?", n)),
      message: edText("remove_message", "TV {n} stops showing its part of the video and shows a pairing code again. Scan it to add the TV back.", n),
      confirmLabel: escHtml(edText("remove", "Remove TV")),
      danger: true,
    });
    if (!ok || ed !== me) return;
    const session = authToken;
    me.removing = col;
    hideEditorMessages(me);
    renderPanel(me);
    try {
      await api(`/walls/${me.wallId}/cells/0/${col}/pairing`, { method: "DELETE" });
    } catch (err) {
      // A 401 has already signed the tab out (api() does that).
      if (ed !== me || !sessionLive(session) || (err && err.stale)) return;
      me.removing = null;
      renderPanel(me);
      showEditorMessage(me, (err && err.message) || edText("remove_failed", "Couldn't remove TV {n}. Try again.", n), true);
      return;
    }
    if (ed !== me || !sessionLive(session)) return;
    me.removing = null;
    me.queue.delete(col);
    me.removedAt.set(col, performance.now());
    me.wall.cells = (me.wall.cells || []).filter((c) => !(c.row_index === 0 && c.col_index === col));
    if (me.focusCol === col) me.focusCol = null;
    if (me.placedCol === col) me.placedCol = null;
    if (me.addedCol === col) me.addedCol = null;
    writeEditorAddress(me);
    renderEditor(me);
    updateTvCount(me);
    showEditorMessage(me, edText("removed", "TV {n} was removed from the wall.", n), false);
    edAsync(rereadWall());
  }

  /** Save a box: the newest box per TV waits in `queue` (a newer one
   *  replaces it) while one PUT is in flight. */
  function queuePlacement(me, col, box, highlight) {
    me.queue.set(col, { box, highlight });
    pumpPlacements(me);
  }

  function pumpPlacements(me) {
    if (ed !== me && !me.draining) return;
    if (me.inFlight != null || me.sendTimer || !me.queue.size) return;
    if (!sessionLive(me.session)) { me.queue.clear(); return; }
    const wait = me.lastSendAt + EDITOR_SAVE_GAP_MS - performance.now();
    if (wait > 0) {
      me.sendTimer = setTimeout(() => { me.sendTimer = null; pumpPlacements(me); }, wait);
      return;
    }
    const [col, want] = me.queue.entries().next().value;
    me.queue.delete(col);
    me.inFlight = col;
    me.lastSendAt = performance.now();
    const body = JSON.stringify({ x: want.box.x, y: want.box.y, w: want.box.w, h: want.box.h, highlight: want.highlight });
    api(`/walls/${me.wallId}/cells/0/${col}/placement`, { method: "PUT", body })
      .then(() => {
        if (ed !== me || !sessionLive(me.session)) return;
        me.savedAt.set(col, performance.now());
        me.els.error.classList.add("hidden");
      }, (err) => {
        // Every failure ends here, never as a toast. A 401 has already signed
        // the tab out; a stale answer belongs to a session that has ended.
        if (ed !== me || !sessionLive(me.session) || (err && err.stale)) return;
        if (err && err.status === 404) {         // the TV left the wall, here or on another phone
          me.queue.delete(col);
          edAsync(rereadWall());
          return;
        }
        showEditorMessage(me, (err && err.message) || edText("save_failed", "Couldn't save where TV {n} goes. Try again.", col + 1), true);
      })
      .then(() => {
        me.inFlight = null;
        pumpPlacements(me);
      })
      .catch((err) => console.error(err));
  }

  function scheduleReread(me, ms) {
    if (ed !== me) return;
    clearTimeout(me.rereadTimer);
    me.rereadTimer = setTimeout(() => edAsync(rereadWall()), ms);
  }

  /** Re-read the wall. A later read replaces an earlier one still on its way. */
  async function rereadWall() {
    const me = ed;
    if (!me) return;
    clearTimeout(me.rereadTimer);
    if (document.hidden) { scheduleReread(me, EDITOR_REREAD_MS); return; }   // nobody is looking
    const seq = ++me.readSeq;
    const startedAt = performance.now();
    const session = authToken;
    let wall;
    try {
      wall = await api(`/walls/${me.wallId}`);
    } catch (err) {
      if (ed !== me || seq !== me.readSeq || !sessionLive(session) || (err && err.stale)) return;
      if (err && err.status === 404) {          // the wall was deleted meanwhile
        stopEditor();
        fail(err.message || Khan.t("wallsetup.load_failed", "Couldn't open this video wall."));
        return;
      }
      scheduleReread(me, EDITOR_REREAD_MS);      // no connection for a moment: quietly try again
      return;
    }
    if (ed !== me || seq !== me.readSeq || !sessionLive(session)) return;
    if (wall && wall.layout === "free") applyWall(me, wall, startedAt);
    scheduleReread(me, EDITOR_REREAD_MS);
  }

  /** Take a re-read wall. A box this phone has changed and not saved yet,
   *  or saved after the read set off, keeps the phone's copy; every other
   *  box is the server's, which is how another phone's moves show up here. */
  function applyWall(me, wall, startedAt) {
    const local = new Map(editorCells(me.wall).map((cell) => [cell.col_index, boxOf(cell)]));
    wall.cells = (wall.cells || []).filter((cell) =>
      !(cell.row_index === 0 && startedAt < (me.removedAt.get(cell.col_index) || -Infinity)));
    editorCells(wall).forEach((cell) => {
      const col = cell.col_index;
      const unsaved = (me.gesture && col === me.focusCol) || me.queue.has(col) || me.inFlight === col
        || startedAt < (me.savedAt.get(col) || -Infinity);
      if (unsaved && local.has(col)) setCellBox(cell, local.get(col));
    });
    me.wall = wall;
    current.wall = wall;
    const canvas = canvasOf(wall);
    if (canvas.w !== me.canvas.w || canvas.h !== me.canvas.h) {
      me.canvas = canvas;
      sizeStage(me);
    }
    setEditorMedia(me, wall.video);
    const cell = cellAt(wall, me.focusCol);
    if (me.focusCol != null && !cell) {
      me.focusCol = null;                        // that TV left the wall
      me.gesture = null;
      writeEditorAddress(me);
    } else if (cell && !me.gesture) {
      const box = boxOf(cell);
      const shaped = shapeFor(cell, box, me.canvas);   // the TV has just reported its screen
      if (!WallGeometry.sameBox(shaped, box)) {
        setCellBox(cell, shaped);
        queuePlacement(me, cell.col_index, shaped, true);
      }
    }
    renderEditor(me);
    updateTvCount(me);
    takeWantedFocus(me);
  }
```

In `frontend/wall-setup.js`, replace:

```js
      `<div id="wall-setup-editor" class="wall-setup-editor" dir="ltr"></div>`);
  }
```

with:

```js
      `<div id="wall-setup-editor" class="wall-setup-editor" dir="ltr"></div>`);
    mountEditor(wall);
  }
```

In `frontend/wall-setup.js`, replace:

```js
      return current.loading || Promise.resolve();
    }
    const myGen = ++gen;
```

with:

```js
      return current.loading || Promise.resolve();
    }
    stopEditor();   // another wall, or this one again: the old editor's re-reads and video end here
    const myGen = ++gen;
```

Task 13 made `stopFlow();` the first line of `reset()`; it stays first.

In `frontend/wall-setup.js`, replace:

```js
  function reset() {
    stopFlow();
    gen++;
```

with:

```js
  function reset() {
    stopFlow();
    stopEditor();
    justAdded = null;
    gen++;
```

Delete Task 13's `openEditor` stub, which would otherwise replace the real one above (in one function scope the later declaration wins). Its callers `joinWall` and `createWall` stay as they are.

In `frontend/wall-setup.js`, replace:

```js
  // ── Placement editor: STUB, replaced in full by Task 14 ────────────────
  /** Open the wall's page with TV `focusCol` (its cell's col_index, or null)
   *  selected for placing. The address becomes /wall-setup?wall=<id>&tv=<n>
   *  (TV n = col_index + 1) in place of the steps' history entry (no new
   *  entry, ruling R19), so Back from the wall goes to the /pair form rather
   *  than through the steps again; show() then finds its address set. */
  function openEditor(wallId, focusCol) {
    const tv = Number.isInteger(focusCol) && focusCol >= 0 ? `&tv=${focusCol + 1}` : "";
    history.replaceState({}, "", `${PATH}?wall=${wallId}${tv}`);
    return show(wallId);
  }

  return { show, startFromPair, reset, recentWallId };
```

with:

```js
  return { show, startFromPair, reset, recentWallId };
```

(`WallSetup`'s returned object does not change, `{ show, startFromPair, reset, recentWallId }` (ruling R13): `openEditor` is used inside the IIFE, by Task 13's steps.)

- [ ] **Step 4: Style the editor in `frontend/wall-setup.css`**

In `frontend/wall-setup.css`, replace:

```css
.wall-setup-editor {
  align-self: stretch;
  direction: ltr;
}
```

with:

```css
.wall-setup-editor {
  align-self: stretch;
  direction: ltr;
}

/* The stage: the wall's video at its own shape (aspect-ratio and a 60vh
   height cap are set from wall-setup.js), with every TV's box positioned on
   it in % of the video. It is drawn over a video whatever the theme, so its
   lines are white with a dark halo instead of theme colours. */
.wall-setup-stage {
  --ws-line: #FFFFFF;
  --ws-halo: rgba(0, 0, 0, 0.72);
  position: relative;
  width: 100%;
  margin: 0 auto;
  overflow: hidden;            /* a box may hang over the video's edge; that part is not drawn */
  background: #000000;         /* what a TV shows where its box leaves the video */
  border-radius: var(--r-md);
  touch-action: none;          /* fingers move boxes, not the page; without it Chromium cancels the drag */
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}
.wall-setup-media,
.wall-setup-media > video,
.wall-setup-media > img {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}
.wall-setup-media > video,
.wall-setup-media > img {
  display: block;
  object-fit: contain;         /* the TVs play the wall's item with fit_mode "fit" */
  pointer-events: none;
}

.wall-setup-box {
  position: absolute;
  z-index: 1;
  border: 2px solid var(--ws-line);
  border-radius: 3px;
  box-shadow: 0 0 0 1px var(--ws-halo), inset 0 0 0 1px var(--ws-halo);
  cursor: pointer;
}
.wall-setup-box.is-focus {
  z-index: 2;
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 32%, transparent);
  cursor: grab;
}
.wall-setup-box-label {
  position: absolute;
  top: 3px;
  left: 3px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: calc(100% - 6px);
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--ws-halo);
  color: #FFFFFF;
  font: 600 11px/1.5 var(--font);
  white-space: nowrap;
  overflow: hidden;
  pointer-events: none;
}
.wall-setup-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-3);
}
.wall-setup-dot.is-online { background: var(--dot-green); }

/* The panel under the stage follows the page's direction. */
.wall-setup-panel {
  align-self: stretch;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-3);
}
.wall-setup-tvs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.wall-setup-tv.is-focus {
  border-color: var(--accent-text);
  color: var(--accent-text);
}
.wall-setup-hint,
.wall-setup-note {
  margin: 0;
  color: var(--text-2);
}
.wall-setup-waiting {
  margin: 0;
  color: var(--amber-text);
}
.wall-setup-size {
  align-self: stretch;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  color: var(--text-2);
}
.wall-setup-size-input {
  flex: 1;
  min-width: 0;
  height: 44px;                /* finger-sized; the base input styles leave range inputs alone */
  accent-color: var(--accent-fill);
}
.wall-setup-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.wall-setup-placed {
  align-self: stretch;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
}
.wall-setup-placed-text {
  margin: 0;
  color: var(--text);
}
/* A wall whose last TV was removed (ruling R18): what to do, and the way back. */
.wall-setup-empty {
  align-self: stretch;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: 12px 14px;
  border: 1px dashed var(--border-2);
  border-radius: var(--r-md);
}
.wall-setup-empty-text {
  margin: 0;
  color: var(--text);
}
.wall-setup-save-error {
  align-self: stretch;
  background: color-mix(in srgb, var(--red) 8%, transparent);
  color: var(--red-text);
  border: 1px solid color-mix(in srgb, var(--red) 35%, transparent);
  border-radius: var(--r-md);
  padding: 10px 14px;
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
}
```

- [ ] **Step 5: Run the flows: the English and layout ones pass, the Arabic one does not yet**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && bash scripts/ui/run.sh wall_setup_flows.py "wall setup editor"`
Expected: `1 failed`, exit 1. Seventeen flows print `PASS`. The English flows pass on the `Khan.t` fallbacks, which are the English strings of Step 6. `FAIL wall setup editor: in Arabic the words are Arabic, ... :: AssertionError()` fails at `assert page.inner_text('.wall-setup-box[data-col="0"] .wall-setup-box-name') == "شاشة 1"`: the label reads the fallback `TV 1`, and the console warns `[i18n] missing key: wallsetup.editor.tv_label`.

- [ ] **Step 6: Add the strings (English and Arabic)**

The locale files are flat JSON objects. They round-trip exactly through `json.dumps(..., ensure_ascii=False, indent=2) + "\n"`, as Task 12 relies on. The script appends the keys and refuses to overwrite one that already exists. "On" / "Off" for a TV's state reuse the Screens page's `screens.status_online` / `screens.status_offline`. Run from the worktree root:

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && python3 - <<'PY'
import json, pathlib
NEW = {
    "en": {
        "wallsetup.editor.tv_label": "TV {n}",
        "wallsetup.editor.tvs": "TVs in this wall",
        "wallsetup.editor.canvas_label": "The wall's video, with a box for each TV",
        "wallsetup.editor.pick": "Tap a TV's box to move or resize it.",
        "wallsetup.editor.hint": "Drag anywhere on the video with one finger to move the box. Pinch, or use the slider, to resize it.",
        "wallsetup.editor.waiting": "Waiting for TV {n} to connect…",
        "wallsetup.editor.size": "Size",
        "wallsetup.editor.done": "Done",
        "wallsetup.editor.placed": "TV {n} placed. Point your camera at the next TV's QR code.",
        "wallsetup.editor.placed_short": "TV {n} placed.",
        "wallsetup.editor.finish": "Finish",
        "wallsetup.editor.remove": "Remove TV",
        "wallsetup.editor.remove_title": "Remove TV {n} from the wall?",
        "wallsetup.editor.remove_message": "TV {n} stops showing its part of the video and shows a pairing code again. Scan it to add the TV back.",
        "wallsetup.editor.removed": "TV {n} was removed from the wall.",
        "wallsetup.editor.empty": "No TVs in this wall yet. Scan a TV's QR code and choose Part of a video wall.",
        "wallsetup.editor.save_failed": "Couldn't save where TV {n} goes. Try again.",
        "wallsetup.editor.remove_failed": "Couldn't remove TV {n}. Try again.",
    },
    "ar": {
        "wallsetup.editor.tv_label": "شاشة {n}",
        "wallsetup.editor.tvs": "شاشات هذا الجدار",
        "wallsetup.editor.canvas_label": "فيديو الجدار، وعليه مربع لكل شاشة",
        "wallsetup.editor.pick": "اضغط على مربع إحدى الشاشات لتحريكه أو تغيير حجمه.",
        "wallsetup.editor.hint": "اسحب بإصبع واحد في أي مكان على الفيديو لتحريك المربع. ولتغيير حجمه، باعد بين إصبعين أو قرّبهما، أو استخدم شريط الحجم.",
        "wallsetup.editor.waiting": "بانتظار اتصال الشاشة {n}…",
        "wallsetup.editor.size": "الحجم",
        "wallsetup.editor.done": "تم",
        "wallsetup.editor.placed": "تم وضع الشاشة {n}. وجّه كاميرا الهاتف إلى رمز QR على الشاشة التالية.",
        "wallsetup.editor.placed_short": "تم وضع الشاشة {n}.",
        "wallsetup.editor.finish": "إنهاء",
        "wallsetup.editor.remove": "إزالة الشاشة",
        "wallsetup.editor.remove_title": "إزالة الشاشة {n} من الجدار؟",
        "wallsetup.editor.remove_message": "ستتوقف الشاشة {n} عن عرض جزئها من الفيديو وستعرض رمز ربط من جديد. امسح الرمز لإعادتها إلى الجدار.",
        "wallsetup.editor.removed": "تمت إزالة الشاشة {n} من الجدار.",
        "wallsetup.editor.empty": "لا توجد شاشات في هذا الجدار بعد. امسح رمز QR على إحدى الشاشات واختر «جزء من جدار شاشات».",
        "wallsetup.editor.save_failed": "تعذّر حفظ موضع الشاشة {n}. حاول مجدداً.",
        "wallsetup.editor.remove_failed": "تعذّرت إزالة الشاشة {n}. حاول مجدداً.",
    },
}
for lang, keys in NEW.items():
    path = pathlib.Path("frontend/i18n") / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    clash = sorted(set(keys) & set(data))
    assert not clash, f"{lang}.json already has {clash}"
    data.update(keys)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{lang}: +{len(keys)} keys, {len(data)} in all")
PY
git diff --stat frontend/i18n/
```
Expected: `en: +18 keys, 792 in all` and `ar: +18 keys, 817 in all` (Task 13 left `774` / `799` at 24101d4; more in all if the redesign added keys since, but each `+18` must match). `git diff --stat` shows `2 files changed, 38 insertions(+), 2 deletions(-)`: 18 new lines per file, plus each file's old last line rewritten with a trailing comma.

- [ ] **Step 7: Run the tests and watch them pass**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh wall_setup_flows.py "wall setup editor"
bash scripts/ui/run.sh wall_setup_flows.py
python3 scripts/check_i18n.py
bash scripts/check_ui_contract.sh
node --test frontend/tests/
grep -c 'function openEditor' frontend/wall-setup.js
grep -c 'Placement editor: STUB' frontend/wall-setup.js
grep -c '^if __name__ == "__main__":' scripts/ui/wall_setup_flows.py
```
Expected: PASS.
- The first run prints eighteen `PASS wall setup editor: ...` lines and `0 failed`.
- The second run prints 50 `PASS` lines and `0 failed` (about 4 minutes). It covers every flow in the file: Task 12's eleven, Task 13's twenty-one, and this task's eighteen. Task 12's flows check what this task must not break: one load of `GET /walls/7` on open (`loads_in_first`; the editor's first re-read comes 3 s later), Back, sign-out, Arabic and phone width. Task 13's flows now reach the real `openEditor`: the address `/wall-setup?wall=7&tv=3` (or `wall=2&tv=1`) and one history entry for the steps (R19) still hold.
- `i18n OK across frontend, landing, player`.
- `UI contract OK`. wall-setup.js looks up no new ids: the editor holds element references, and `wall-setup-sub` is already in `DYNAMIC_IDS`.
- `# pass 24` / `# fail 0` (Task 11's geometry tests).
- `1` (the editor's `openEditor`; Task 13's stub is gone), `0`, and `1` (Task 15 inserts its flows before that line).

(Checked while planning: Tasks 11, 12 and 13 applied in order from the plan text to an archive of `feature/friendly-dashboard` at 24101d4, then this task, in throwaway `signage_renderer:latest` containers with `--network none`. Step 2 gave `18 failed`, Step 5 `1 failed` (the Arabic flow, at the label), and this step 50 `PASS`, `0 failed` in 218 s (two more runs of the editor flows: `0 failed` each); every anchor in Step 3 matched exactly once, `node --check` passed, and no name is declared twice in the IIFE. With Task 15 applied on top, its anchors matched once each and its locale script printed `en: +3 keys, 795 in all` / `ar: +3 keys, 820 in all`, and the whole file gave 55 `PASS`, `0 failed`. The redesign's `flows.py` gave 119 `PASS` before and after, with identical lines.)

Then check that the rest of the dashboard did not change:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh flows.py > scripts/ui/out/flows-after-editor.txt 2>&1; tail -n 1 scripts/ui/out/flows-after-editor.txt
diff <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-baseline.txt | sed 's/ :: .*//') <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-after-editor.txt | sed 's/ :: .*//') && echo "flows unchanged"
```
Expected: the same `N failed` line as Task 11's baseline, and `flows unchanged`.

- [ ] **Step 8: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git branch --show-current   # must print feature/phone-wall-setup-dashboard; stop if it does not
git add frontend/wall-setup.js frontend/wall-setup.css frontend/i18n/en.json frontend/i18n/ar.json scripts/ui/wall_setup_flows.py
git commit -m "feat(walls): place each TV's box over the wall's video from the phone

The wall's page now shows the video, muted and looping, across the
phone's width, with every TV's box on it. The selected TV's box follows
one finger anywhere on the video (at once from the box, after 6 px from
elsewhere, so a box hanging off the video can still be moved, and a tap
still selects the TV under it), a pinch and a size slider. Edges snap to
the video and to the other boxes within 3%. Once a TV has reported its
screen, every box is drawn and sent in that TV's shape, the box the
server stores, mid-drag too. Every change goes to
PUT /walls/{id}/cells/0/{col}/placement with one request in flight:
the newest box goes out when it returns, so the TV follows the finger
and the last position always lands, even when the page is left straight
after. Failures show under the video, never as toasts; a 401 signs out.

The wall is re-read every 3 s (TV sizes, online dots, another phone's
moves, the transcoded file's new name) and stops with the page; text
that has not changed is not rewritten, so screen readers stay quiet.
Tap a box or a TV button to select that TV, which lights its outline on
the TV. Done clears it: for the TV just added from /pair it says to scan
the next TV's QR code, otherwise that the TV is placed. Finish opens the
wall on Video walls. Remove TV unpairs it after a confirm; after the
last one the wall stays and says how to add a TV. The video area stays
left-to-right in Arabic; the words follow the language. The address
keeps the selected TV (&tv=<n>) across a refresh. openEditor(wallId,
col) replaces Task 13's stub: from /pair it takes over the steps' one
history entry, so Back returns to the pairing form.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Walls page: Adjust TVs

**Files:**
- Modify: `frontend/app.js:3542` (new `freeWallMeta` before the Walls module's `renderList`), `frontend/app.js:3553-3557` (`renderList`: a free wall's card line reads "Set up from a phone · {n} TVs"), `frontend/app.js:3564-3566` (`renderList`: Adjust TVs on free walls' cards), `frontend/app.js:3572` (its click handler), `frontend/app.js:3709` (new `openWallSetup` before `closeEditor`), `frontend/app.js:3971-4026` (`renderCanvasEditor`, replaced whole: for a free wall the video's name, a hint and Adjust TVs, and no Add item, bezel grid, item detail or mode switch; grid walls as before)
- Modify: `frontend/wall-setup.css` (appended)
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json` (3 keys each, appended by script)
- Test: `scripts/ui/wall_setup_flows.py` (flows inserted before `if __name__ == "__main__":`), run with `bash scripts/ui/run.sh wall_setup_flows.py`

All paths are relative to `/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`. This task runs after Task 14. Line numbers are those of `feature/friendly-dashboard` at 24101d4 (Tasks 12 and 13 have shifted app.js since; Task 14 does not touch app.js); every app.js edit is anchored on exact text inside the `Walls` module, which no earlier task touches. In the other files this task only appends (wall-setup.css, the locale files) or inserts before `if __name__ == "__main__":` (the flows file), so whatever Tasks 13 and 14 put there stays as it is.

**Interfaces:**
- Consumes:
  - Task 12: `WallSetup.show(wallId)` (opens `/wall-setup?wall=<id>`; pushes that address only when the address does not already name the wall); `hideWallSetupView()` (Back to `/walls` hides the page through `showSection`); i18n `wallsetup.tv_count_*` (with the ar `_two` "شاشتان"); harness `free_wall` (its `video.name` is "promo.mp4", 2 TVs), `WallFixture` (it answers GET `/walls/{id}/canvas-playlist` for spanned walls with one `promo.mp4` item, so this task adds no fixture class), `views`, `visible_section`, `on_wall_page`.
  - Task 13: harness `open_pair(browser, path=..., device=..., lang=..., role=..., **fx_kwargs)` (its default fixture `PairFixture` is a `WallFixture`), `address(page)`; i18n `wallsetup.video_label` ("Video" / "الفيديو").
  - Task 14: its editor mounts on `/wall-setup?wall=<id>` (no `&tv=`: no TV selected, Adjust mode, so Done says only "TV {n} placed.", ruling R17), which Adjust TVs opens; its 3 s re-read stops when `hideWallSetupView()` resets the page (`reset()` calls `stopEditor()`). Nothing it adds is called here, and it does not touch app.js. Its 18 flows sit before the flows file's one `if __name__ == "__main__":` line, which Step 2 inserts before.
  - app.js Walls module (private): `state.editing`, `closeEditor()`, `openEditor(id)`, `renderCanvasItemList(wall, items)`, `mountModeSwitchButton(wall)`, `openCanvasMediaPicker(wall)`; globals `api`, `canEditContent()`, `escHtml`, `pluralText`, `Khan`.
  - Backend: `GET /walls` / `GET /walls/{id}` carry `layout` (`"free"` for phone-made walls), `cells[].screen_id` and, for free walls, `video` (`{media_id, name, url, mime_type, width_px, height_px}`); `GET /walls/{id}/canvas-playlist` -> `{wall_id, playlist_id, items[]}`.
- Produces:
  - Walls module (private): `freeWallMeta(w)` (escaped "Set up from a phone · {n}", `{n}` filled with the `wallsetup.tv_count` phrase for the cells that have a screen); `openWallSetup(id, e)`: closes the editor, `history.pushState` to `/wall-setup?wall=<id>`, then `WallSetup.show(id)`; a modified click (new tab or window) is left to the browser.
  - DOM, free walls only (ruling R14): the card's `.walls-meta` reads "Set up from a phone · 2 TVs" (ar "جُهّز من الهاتف · شاشتان"); on the card (editors and admins) `<a class="btn btn-primary" data-action="adjust" href="/wall-setup?wall=<id>">`; in its canvas editor (`#walls-editor-body`) the rail heading "Video" over one `li.canvas-free-video` with the video's name (no click handler), `p.canvas-free-hint` (everyone) and `a.canvas-adjust-tvs[data-action="adjust-tvs"]` (editors and admins); no `#canvas-add-item`, no `.canvas-bezel-grid`, no `#canvas-item-detail` (so no `#canvas-item-duration`, `input[name="fit"]`, `#canvas-item-save` or `#canvas-item-delete`) and no `.walls-mode-switch-btn`. Grid walls are unchanged.
  - i18n (en + ar): `wallsetup.adjust_tvs`, `wallsetup.free_wall_hint`, `wallsetup.card_meta`.
  - Harness: `spanned_grid_wall(wall_id=5, name="Atrium canvas")`, `open_walls(browser, device="desktop", lang="en", role="admin")`.

- [ ] **Step 1: Check the anchors, and record the Walls page's layout checks before changing it**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git log --oneline -5
grep -c '^if __name__ == "__main__":' scripts/ui/wall_setup_flows.py
grep -n -A1 '^  function renderList() {$' frontend/app.js
grep -c '^        <div class="walls-meta">$' frontend/app.js
grep -c 'canEditContent() ? `<div class="walls-actions">' frontend/app.js
grep -cF "card.querySelector('[data-action=\"edit\"]')?.addEventListener(\"click\", () => openEditor(id));" frontend/app.js
grep -c '^  function closeEditor() {$' frontend/app.js
grep -c '^  async function renderCanvasEditor(wall) {$' frontend/app.js
sed -n '/^  async function renderCanvasEditor(wall) {$/,/^  }$/p' frontend/app.js | wc -l
grep -cE '"wallsetup\.(tv_count_other|video_label)"' frontend/i18n/en.json frontend/i18n/ar.json
grep -nE 'function (freeWallMeta|openWallSetup)\b' frontend/*.js
grep -nE '^(def (spanned_grid_wall|open_walls|card_meta)\b|NOT_ON_FREE_WALLS =)' scripts/ui/wall_setup_flows.py
bash scripts/ui/run.sh shoot.py --routes /walls --fixtures populated > scripts/ui/out/shoot-walls-before.txt 2>&1; tail -n 1 scripts/ui/out/shoot-walls-before.txt
grep -c '^FAIL' scripts/ui/out/shoot-walls-before.txt
```
Expected: the log shows Tasks 11 to 14 committed (Task 14's "feat(walls): place each TV's box over the wall's video from the phone" on top). The six single-file `grep -c` print `1` each: the flows file's one `__main__` line (Task 14 left it right after its flows; Step 2 inserts before it) and five of Step 4's six app.js anchors, none of which Tasks 12-14 touch. The sixth, `renderList`, is the `grep -n -A1`, which prints exactly these two lines (Step 4's first edit anchors on both):
```
3619:  function renderList() {
3620-    updateNavBadges();
```
(`3542`/`3543` at 24101d4; Tasks 12 and 13 add 77 lines above it.) The `sed ... | wc -l` prints `56`: `renderCanvasEditor` is still the 56 lines Step 4 replaces whole. The locale grep prints `frontend/i18n/en.json:2` and `frontend/i18n/ar.json:2` (Task 12's `wallsetup.tv_count_other` and Task 13's `wallsetup.video_label`, which this task reads). The last two greps print nothing: a second `function freeWallMeta` in the Walls module, or a second module-level `def open_walls` in the flows file, would silently replace the first; if one exists, rename this task's and every use of it. Then `12 shots, N failures` (6 devices x en/ar). Keep the file: Step 8 must not show more failures than this.

- [ ] **Step 2: Write the failing test**

In `scripts/ui/wall_setup_flows.py`, replace:

```python
if __name__ == "__main__":
```

with:

```python
# ── Walls page: phone-made walls (ruling R14) ───────────────────────────────

def spanned_grid_wall(wall_id=5, name="Atrium canvas"):
    """A spanned wall made with the Walls page's wizard (layout 'grid'): the
    walls whose card and editor must stay exactly as they were."""
    cells = [{"id": 50 + c, "wall_id": wall_id, "row_index": 0, "col_index": c, "screen_id": None,
              "playlist_id": None, "screen_size_inches": None, "bezel_top_mm": None, "bezel_right_mm": None,
              "bezel_bottom_mm": None, "bezel_left_mm": None, "created_at": iso(days=2),
              "place_x": None, "place_y": None, "place_w": None, "place_h": None,
              "screen_w_px": None, "screen_h_px": None, "highlight_until_ms": None, "online": False}
             for c in range(2)]
    return {"id": wall_id, "organization_id": 1, "name": name, "mode": "spanned", "layout": "grid",
            "rows": 1, "cols": 2, "canvas_width_px": 3840, "canvas_height_px": 1080,
            "bezel_enabled": True, "bezel_h_pct": 1.0, "bezel_v_pct": 0.0, "spanned_playlist_id": 51,
            "mirrored_mode": None, "mirrored_playlist_id": None,
            "created_at": iso(days=2), "updated_at": iso(days=2), "cells": cells}


def open_walls(browser, device="desktop", lang="en", role="admin"):
    """/walls with a phone-made wall (7), a spanned grid wall (5) and the
    fixture's mirrored wall (1). Task 12's WallFixture (under Task 13's
    PairFixture) answers the canvas editor's GET /walls/{id}/canvas-playlist."""
    return open_pair(browser, path="/walls", device=device, lang=lang, role=role,
                     free_walls=[free_wall(), spanned_grid_wall()])


def card_meta(page, wall_id):
    return page.inner_text(f".walls-card[data-wall-id='{wall_id}'] .walls-meta").strip()


# A phone-made wall's canvas editor shows none of these (ruling R14).
NOT_ON_FREE_WALLS = (".walls-editor-header .walls-mode-switch-btn", "#canvas-add-item", ".canvas-bezel-grid",
                     "#canvas-item-detail", "#canvas-item-duration", "#walls-editor-body input[name='fit']",
                     "#canvas-item-save", "#canvas-item-delete")


@flow("walls: a phone-made wall's card says how it was made and has Adjust TVs, which opens its setup page; Back returns to Walls")
def _(browser):
    ctx, page, fx, errors = open_walls(browser)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert card_meta(page, 7) == "Set up from a phone · 2 TVs", card_meta(page, 7)
    assert card_meta(page, 5) == "Spanned · 1×2", card_meta(page, 5)    # grid walls keep their line
    assert card_meta(page, 1) == "Mirrored · 1×3", card_meta(page, 1)
    adjust = page.locator(".walls-card[data-wall-id='7'] [data-action='adjust']")
    assert adjust.inner_text().strip() == "Adjust TVs"
    assert adjust.get_attribute("href") == "/wall-setup?wall=7"
    for wid in (5, 1):
        assert page.locator(f".walls-card[data-wall-id='{wid}'] [data-action='adjust']").count() == 0, wid
        assert page.locator(f".walls-card[data-wall-id='{wid}'] [data-action='edit']").count() == 1, wid
    adjust.click(); page.wait_for_timeout(800)
    on_wall_page(page)
    assert address(page) == "/wall-setup?wall=7", page.url
    assert page.inner_text("#wall-setup-title") == "Lobby wall"
    page.go_back(); page.wait_for_timeout(800)
    assert views(page) == ["dashboard"], views(page)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert address(page) == "/walls", page.url
    assert not errors, errors
    ctx.close()


@flow("walls: a phone-made wall's editor shows its video's name and Adjust TVs, with no item settings, Add item, bezel grid or Switch to mirrored")
def _(browser):
    ctx, page, fx, errors = open_walls(browser)
    page.click(".walls-card[data-wall-id='7'] [data-action='edit']"); page.wait_for_timeout(800)
    assert page.inner_text("#walls-editor-title") == "Lobby wall"
    for sel in NOT_ON_FREE_WALLS:
        assert page.locator(sel).count() == 0, sel
    assert page.inner_text("#walls-editor-body .canvas-editor-rail h4").strip() == "Video"
    item = page.locator("#canvas-items-list li")
    assert item.count() == 1 and item.inner_text().strip() == "promo.mp4", item.all_inner_texts()
    item.click(); page.wait_for_timeout(300)   # the name only: nothing to select, change or delete
    assert page.locator("#canvas-item-detail").count() == 0
    assert page.inner_text("#walls-editor-body .canvas-free-hint").strip() == \
        "Set up from a phone. To move its TVs on the video, use Adjust TVs."
    link = page.locator("#walls-editor-body [data-action='adjust-tvs']")
    assert link.inner_text().strip() == "Adjust TVs"
    assert link.get_attribute("href") == "/wall-setup?wall=7"
    link.click(); page.wait_for_timeout(800)
    on_wall_page(page)
    assert address(page) == "/wall-setup?wall=7", page.url
    page.go_back(); page.wait_for_timeout(800)
    assert visible_section(page) == ["walls"], visible_section(page)
    assert not page.is_visible("#walls-editor")   # closed on the way out, so Back shows the list
    assert not errors, errors
    ctx.close()


@flow("walls: a grid wall's canvas editor keeps its item settings and the rest, and opening a phone-made wall next drops them")
def _(browser):
    ctx, page, fx, errors = open_walls(browser)
    page.click(".walls-card[data-wall-id='5'] [data-action='edit']"); page.wait_for_timeout(800)
    assert page.inner_text("#walls-editor-title") == "Atrium canvas"
    assert page.locator(".walls-editor-header .walls-mode-switch-btn").count() == 1
    assert page.locator("#canvas-add-item").count() == 1
    assert page.locator(".canvas-bezel-grid").count() == 1
    assert page.locator("[data-action='adjust-tvs']").count() == 0
    assert page.locator(".canvas-free-hint").count() == 0
    assert page.inner_text("#walls-editor-body .canvas-editor-rail h4").strip() == "Items"
    page.click("#canvas-items-list li[data-item-id]"); page.wait_for_timeout(300)
    assert page.is_visible("#canvas-item-detail") and page.is_visible("#canvas-item-duration")
    assert page.locator("#walls-editor-body input[name='fit']").count() == 3
    assert page.is_visible("#canvas-item-save") and page.is_visible("#canvas-item-delete")
    page.click(".walls-card[data-wall-id='7'] [data-action='edit']"); page.wait_for_timeout(800)
    assert page.inner_text("#walls-editor-title") == "Lobby wall"
    for sel in NOT_ON_FREE_WALLS:
        assert page.locator(sel).count() == 0, sel
    assert page.locator("[data-action='adjust-tvs']").count() == 1
    assert not errors, errors
    ctx.close()


@flow("walls: viewers see no Adjust TVs, on the cards or in the editor, but do see the card line, the video's name and the hint")
def _(browser):
    ctx, page, fx, errors = open_walls(browser, role="viewer")
    assert visible_section(page) == ["walls"], visible_section(page)
    assert page.locator(".walls-card").count() == 3
    assert page.locator("[data-action='adjust']").count() == 0
    assert card_meta(page, 7) == "Set up from a phone · 2 TVs", card_meta(page, 7)
    page.evaluate("() => Walls.openEditor(7)"); page.wait_for_timeout(600)   # viewers have no Edit button
    assert page.is_visible("#walls-editor-body .canvas-free-hint")
    assert page.inner_text("#canvas-items-list li").strip() == "promo.mp4"
    assert page.locator("[data-action='adjust-tvs']").count() == 0
    for sel in NOT_ON_FREE_WALLS:
        assert page.locator(sel).count() == 0, sel
    assert not errors, errors
    ctx.close()


@flow("walls: Adjust TVs and the card line read in Arabic, and the card and the editor fit a phone")
def _(browser):
    cases = (("android360", "ar", "ضبط الشاشات", "جُهّز من الهاتف · شاشتان", "الفيديو",
              "جُهّز هذا الجدار من الهاتف. لتحريك شاشاته على الفيديو، استخدم «ضبط الشاشات»."),
             ("iphone", "en", "Adjust TVs", "Set up from a phone · 2 TVs", "Video",
              "Set up from a phone. To move its TVs on the video, use Adjust TVs."))
    for device, lang, label, meta, heading, hint in cases:
        where = (device, lang)
        ctx, page, fx, errors = open_walls(browser, device=device, lang=lang)
        assert card_meta(page, 7) == meta, (where, card_meta(page, 7))
        adjust = page.locator(".walls-card[data-wall-id='7'] [data-action='adjust']")
        assert adjust.inner_text().strip() == label, (where, adjust.inner_text())
        b = adjust.bounding_box()
        assert b["height"] >= 44 and b["width"] >= 44, (where, b)
        inside = page.evaluate("""() => {
            const card = document.querySelector(".walls-card[data-wall-id='7']").getBoundingClientRect();
            return [...document.querySelectorAll(".walls-card[data-wall-id='7'] .walls-actions > *")].every((el) => {
              const r = el.getBoundingClientRect();
              return r.left >= card.left - 1 && r.right <= card.right + 1;
            });
        }""")
        assert inside, where
        m = page.evaluate("() => ({ w: innerWidth, sw: document.documentElement.scrollWidth })")
        assert m["sw"] <= m["w"] + 1, (where, m)
        page.click(".walls-card[data-wall-id='7'] [data-action='edit']"); page.wait_for_timeout(800)
        assert page.inner_text("#walls-editor-body .canvas-free-hint").strip() == hint, where
        assert page.inner_text("#walls-editor-body .canvas-editor-rail h4").strip() == heading, where
        b = page.locator("#walls-editor-body [data-action='adjust-tvs']").bounding_box()
        assert b["height"] >= 44 and b["width"] >= 44, (where, b)
        m = page.evaluate("() => ({ w: innerWidth, sw: document.documentElement.scrollWidth })")
        assert m["sw"] <= m["w"] + 1, (where, m)
        assert not errors, (where, errors)
        ctx.close()


if __name__ == "__main__":
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && bash scripts/ui/run.sh wall_setup_flows.py`
Expected: FAIL, exit code 1, `5 failed` (about 4 minutes). The 50 flows of Tasks 12, 13 and 14 (11 + 21 + 18) still print `PASS`. The five new flows print `FAIL`, each on its first check of something this task adds: the card flow and the viewer flow with `AssertionError('Spanned · 1×2')` (wall 7's card line is still the grid one), the Arabic/phone flow with `AssertionError((('android360', 'ar'), 'ممتد · 1×2'))`, and the free-wall editor flow and the grid-wall flow with `AssertionError('.walls-editor-header .walls-mode-switch-btn')` (today a phone-made wall's editor mounts "Switch to mirrored"; in the grid-wall flow every check on wall 5 passes first). (Checked while planning: Tasks 11-14 applied from the plan text to an archive of 24101d4, plus this step, in a throwaway `signage_renderer:latest` container with `--network none`: 50 `PASS`, exactly these five `FAIL` lines, `5 failed`, exit 1, in 229 s.)

- [ ] **Step 4: Implement the Walls module changes (`frontend/app.js`)**

In `frontend/app.js`, replace:

```js
  function renderList() {
    updateNavBadges();
```

with:

```js
  /** The card line of a wall set up from a phone (layout "free", ruling
   *  R14). Its TVs sit where they were placed on the video, not in an even
   *  rows×cols grid, so the line says how it was made and how many TVs it
   *  has: "Set up from a phone · 2 TVs" (the count is the TV-count phrase,
   *  so Arabic gets its dual and plural forms). */
  function freeWallMeta(w) {
    const tvs = (w.cells || []).filter((c) => c.screen_id != null).length;
    return escHtml(Khan.t("wallsetup.card_meta", "Set up from a phone · {n}")
      .replace("{n}", pluralText("wallsetup.tv_count", tvs, { one: "{n} TV", other: "{n} TVs" })));
  }

  function renderList() {
    updateNavBadges();
```

In `frontend/app.js`, replace:

```js
        <div class="walls-meta">
          ${w.mode === "mirrored" ? Khan.t("walls.mode_mirrored", "Mirrored")
                                   : Khan.t("walls.mode_spanned", "Spanned")}
          · <bdi dir="ltr">${w.rows}×${w.cols}</bdi>
        </div>
```

with:

```js
        <div class="walls-meta">
          ${w.layout === "free" ? freeWallMeta(w) : `${w.mode === "mirrored" ? Khan.t("walls.mode_mirrored", "Mirrored")
                                   : Khan.t("walls.mode_spanned", "Spanned")}
          · <bdi dir="ltr">${w.rows}×${w.cols}</bdi>`}
        </div>
```

(A grid wall's line comes out exactly as before.)

In `frontend/app.js`, replace:

```js
        ${canEditContent() ? `<div class="walls-actions">
          <button class="btn btn-ghost" data-action="edit">${Khan.t("walls.edit", "Edit")}</button>
```

with:

```js
        ${canEditContent() ? `<div class="walls-actions">
          ${w.layout === "free" ? `<a class="btn btn-primary" data-action="adjust" href="/wall-setup?wall=${w.id}">${
            escHtml(Khan.t("wallsetup.adjust_tvs", "Adjust TVs"))}</a>` : ""}
          <button class="btn btn-ghost" data-action="edit">${Khan.t("walls.edit", "Edit")}</button>
```

In `frontend/app.js`, replace:

```js
      card.querySelector('[data-action="edit"]')?.addEventListener("click", () => openEditor(id));
```

with:

```js
      card.querySelector('[data-action="adjust"]')?.addEventListener("click", (e) => openWallSetup(id, e));
      card.querySelector('[data-action="edit"]')?.addEventListener("click", () => openEditor(id));
```

In `frontend/app.js`, replace:

```js
  function closeEditor() {
```

with:

```js
  /** A phone-made wall's TVs are placed on its own page, /wall-setup?wall=<id>
   *  (wall-setup.js). The editor closes first, so Back from there shows the
   *  list, not this wall's editor; the address is pushed here, so
   *  WallSetup.show finds it already set and adds no second entry. A click
   *  with a modifier key (new tab or window) is the browser's: the link
   *  carries the real address. */
  function openWallSetup(id, e) {
    if (e) {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
    }
    closeEditor();
    history.pushState({}, "", `/wall-setup?wall=${id}`);
    WallSetup.show(id);
  }

  function closeEditor() {
```

The next edit replaces `renderCanvasEditor` whole, from its first line to its closing brace.

In `frontend/app.js`, replace:

```js
  async function renderCanvasEditor(wall) {
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent = wall.name;
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
    state.editing = wall.id;
    body.innerHTML = `
      <div class="canvas-editor-summary">
        <span class="walls-meta">
          ${Khan.t("walls.mode_spanned", "Spanned")} ·
          <bdi dir="ltr">${wall.canvas_width_px}×${wall.canvas_height_px}</bdi> ·
          <bdi dir="ltr">${wall.rows}×${wall.cols}</bdi>
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
```

with:

```js
  async function renderCanvasEditor(wall) {
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent = wall.name;
    const list = await api(`/walls/${wall.id}/canvas-playlist`);
    state.editing = wall.id;
    // Set up from a phone (layout "free", ruling R14): one video, with its TVs
    // placed on it at /wall-setup rather than cells of an even grid, and a
    // mode the server will not change (PATCH answers 409 wall.free_layout).
    // So no item settings (duration, Fit/Fill/Stretch, Save, Delete: deleting
    // the only item would leave every TV black), no Add item, no bezel grid
    // (an even 1×N split would misstate where its TVs are) and no "Switch to
    // mirrored": the video's name, a hint and Adjust TVs instead. Only PUT
    // /walls/{id}/video changes its video; no page offers that yet.
    const free = wall.layout === "free";
    body.innerHTML = `
      <div class="canvas-editor-summary">
        <span class="walls-meta">
          ${Khan.t("walls.mode_spanned", "Spanned")} ·
          <bdi dir="ltr">${wall.canvas_width_px}×${wall.canvas_height_px}</bdi> ·
          <bdi dir="ltr">${wall.rows}×${wall.cols}</bdi>
        </span>
        ${free ? `<p class="canvas-free-hint">${escHtml(Khan.t("wallsetup.free_wall_hint",
          "Set up from a phone. To move its TVs on the video, use Adjust TVs."))}</p>
        ${canEditContent() ? `<a class="btn btn-primary canvas-adjust-tvs" data-action="adjust-tvs" href="/wall-setup?wall=${wall.id}">${
          escHtml(Khan.t("wallsetup.adjust_tvs", "Adjust TVs"))}</a>` : ""}` : ""}
      </div>
      <div class="canvas-editor-grid">
        <div class="canvas-editor-rail">
          <h4>${free ? escHtml(Khan.t("wallsetup.video_label", "Video")) : Khan.t("walls.canvas_items", "Items")}</h4>
          <ul id="canvas-items-list"></ul>
          ${free ? "" : `<button id="canvas-add-item" class="btn">${Khan.t("walls.canvas_add_item", "Add item")}</button>`}
        </div>
        <div class="canvas-editor-preview" id="canvas-preview">
          ${free ? "" : `<div class="canvas-bezel-grid"
               style="grid-template-columns: repeat(${wall.cols}, 1fr);
                      grid-template-rows: repeat(${wall.rows}, 1fr);
                      aspect-ratio: ${wall.canvas_width_px} / ${wall.canvas_height_px};
                      gap: ${wall.bezel_h_pct}% ${wall.bezel_v_pct}%;">
            ${Array.from({length: wall.rows * wall.cols}).map(() =>
              `<div class="canvas-bezel-cell"></div>`).join("")}
          </div>`}
          <div id="canvas-preview-media" class="canvas-preview-media"></div>
        </div>
      </div>
      ${free ? "" : `<div id="canvas-item-detail" class="canvas-item-detail hidden">
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
      </div>`}
    `;
    if (free) {
      // The video's name, as a plain line: no click handler, so nothing opens
      // the item detail (it is not drawn) and nothing changes the one item.
      const first = ((list && list.items) || [])[0];
      const name = (wall.video && wall.video.name) || (first && first.media_name) || "";
      document.getElementById("canvas-items-list").innerHTML =
        `<li class="canvas-free-video"><span dir="auto">${escHtml(name)}</span></li>`;
      body.querySelector('[data-action="adjust-tvs"]')
        ?.addEventListener("click", (e) => openWallSetup(wall.id, e));   // editors and admins only
      return;
    }
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
```

(Grid walls take the same path as before: the same markup, `renderCanvasItemList`, Add item and the mode switch for editors, and the viewer branch that removes Add item, Save and Delete. `openEditor` calls `removeModeSwitchButton()` before every render, so a grid wall's "Switch to mirrored" does not stay behind when a phone-made wall is opened next.)

- [ ] **Step 5: Style the button row, the hint and the video line (`frontend/wall-setup.css`)**

Append to the end of `frontend/wall-setup.css`:

```css
/* ── Walls page: phone-made walls (app.js Walls module) ─────────────────── */
/* Adjust TVs makes three buttons on the card: let them wrap on a phone.
   Grid walls' cards keep their one row. */
.walls-actions:has([data-action="adjust"]) { flex-wrap: wrap; }

.canvas-free-hint {
  margin: var(--sp-2) 0 0;
  max-width: 60ch;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-2);
}
.canvas-adjust-tvs { margin-block-start: var(--sp-3); }

/* The wall's one video, named in the rail: a line, not a row to click
   (sections.css gives the rail's rows a pointer and a hover colour). */
.canvas-editor-rail li.canvas-free-video { cursor: default; overflow-wrap: anywhere; }
.canvas-editor-rail li.canvas-free-video:hover { background: var(--surface); }
```

- [ ] **Step 6: Add the strings (English and Arabic)**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard && python3 - <<'PY'
import json, pathlib
NEW = {
    "en": {
        "wallsetup.adjust_tvs": "Adjust TVs",
        "wallsetup.free_wall_hint": "Set up from a phone. To move its TVs on the video, use Adjust TVs.",
        "wallsetup.card_meta": "Set up from a phone · {n}",
    },
    "ar": {
        "wallsetup.adjust_tvs": "ضبط الشاشات",
        "wallsetup.free_wall_hint": "جُهّز هذا الجدار من الهاتف. لتحريك شاشاته على الفيديو، استخدم «ضبط الشاشات».",
        "wallsetup.card_meta": "جُهّز من الهاتف · {n}",
    },
}
for lang, keys in NEW.items():
    path = pathlib.Path("frontend/i18n") / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    clash = sorted(set(keys) & set(data))
    assert not clash, f"{lang}.json already has {clash}"
    data.update(keys)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{lang}: +{len(keys)} keys, {len(data)} in all")
PY
git diff --stat frontend/i18n/
```
Expected: `en: +3 keys, 795 in all` and `ar: +3 keys, 820 in all` (Task 14 Step 6 left `792` / `817` at 24101d4: Task 13's `774` / `799` plus its 18 `wallsetup.editor.*` keys; more in all if the redesign added keys since, but each `+3` must match), and `git diff --stat` shows `2 files changed, 8 insertions(+), 2 deletions(-)` (3 new lines per file, plus each file's old last line rewritten with a trailing comma). `wallsetup.card_meta`'s `{n}` is filled with the `wallsetup.tv_count` phrase ("2 TVs", "شاشتان"), not a bare number, so it needs no plural forms of its own.

- [ ] **Step 7: Run the tests and watch them pass**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh wall_setup_flows.py
python3 scripts/check_i18n.py
bash scripts/check_ui_contract.sh
node --test frontend/tests/
```
Expected: PASS. `wall_setup_flows.py` prints 55 `PASS` lines, one for every flow in the file (Task 12's 11, Task 13's 21, Task 14's 18 and these 5), and `0 failed` (about 4 minutes); then `i18n OK across frontend, landing, player`; `UI contract OK` (the new markup uses `data-action` selectors and classes, not ids, so `DYNAMIC_IDS` needs nothing); the node tests print `# pass 24` / `# fail 0` (Task 11's geometry tests), as after Task 14.

(Checked while planning: Tasks 11, 12, 13 and 14 applied in order from the plan text to an archive of `feature/friendly-dashboard` at 24101d4, then this task, in throwaway `signage_renderer:latest` containers with `--network none`. Task 14 is the one that deletes Task 13's `openEditor` stub, has `reset()` call `stopEditor()` right after `stopFlow()`, replaces the steps' history entry in `openEditor` and opens `Walls.openEditor` on Finish. Every anchor in this task matched exactly once, `node --check` passed on `app.js`, and no name this task adds was already declared. The whole flows file gave 55 `PASS` and `0 failed`, twice (234 s and 235 s). `check_i18n.py`, `check_ui_contract.sh` and the node tests passed, and `git diff --numstat` on the locale files gave 4 insertions and 1 deletion each.)

- [ ] **Step 8: Check that the rest of the Walls page and the dashboard did not change**

Run:
```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
bash scripts/ui/run.sh flows.py > scripts/ui/out/flows-after-adjust-tvs.txt 2>&1; tail -n 1 scripts/ui/out/flows-after-adjust-tvs.txt
diff <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-baseline.txt | sed 's/ :: .*//') <(grep -E '^(PASS|FAIL) ' scripts/ui/out/flows-after-adjust-tvs.txt | sed 's/ :: .*//') && echo "flows unchanged"
bash scripts/ui/run.sh shoot.py --routes /walls --fixtures populated > scripts/ui/out/shoot-walls-after.txt 2>&1; tail -n 1 scripts/ui/out/shoot-walls-after.txt
diff <(grep '^FAIL' scripts/ui/out/shoot-walls-before.txt) <(grep '^FAIL' scripts/ui/out/shoot-walls-after.txt) && echo "shots unchanged"
```
Expected: the same `N failed` line as the Task 11 baseline, `flows unchanged` (including the redesign's "walls: page renders with the create button and a card" if its Task 8 has landed), and `12 shots, N failures` with `shots unchanged` against Step 1 (the populated fixture's only wall is mirrored, so this proves the page around the change is untouched; the phone-made wall's card is measured by the last flow in Step 7). (Checked while planning, at 24101d4 with Tasks 11-15 applied: `flows.py` gave 119 `PASS` and `0 failed`, line for line the same as before Task 11, and the /walls shots gave `12 shots, 0 failures` before and after.)

- [ ] **Step 9: Commit**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git branch --show-current   # must print feature/phone-wall-setup-dashboard; stop if it does not
git add frontend/app.js frontend/wall-setup.css frontend/i18n/en.json frontend/i18n/ar.json scripts/ui/wall_setup_flows.py
git commit -m "feat(walls): Adjust TVs on phone-made walls in the Walls page

A wall set up from a phone (layout free) gets an Adjust TVs link on its
card and in its canvas editor, which opens /wall-setup?wall=<id> (the
editor closes first, so Back lands on the list). Its card line reads
\"Set up from a phone · 2 TVs\" instead of a rows x cols size. Its
canvas editor shows the video's name and a short hint, and no longer
offers what does not apply to one video placed by TV: the item's
duration, Fit/Fill/Stretch, Save and Delete (deleting the only item
left every TV black), Add item, Switch to mirrored (the server refuses
it with wall.free_layout) or the even 1xN bezel grid, which misstated
where its TVs are. Grid walls are unchanged. Strings in English and
Arabic.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: E2E: set up a wall from the phone

All repository work happens in the PR 1 worktree `/home/ahmed/signage/.claude/worktrees/wall-setup`
(branch `feature/phone-wall-setup`), because the harness lives there. Paths are relative to it. In
commands, `WT` is that worktree, `D` is the PR 2 worktree
`/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard`, `M` is the throwaway merge worktree
`/home/ahmed/signage/.claude/worktrees/wall-e2e-merge`, and `S` is
`/home/ahmed/signage-harness/handoff`, outside `/tmp`, so the logs survive a restart until Task 17 quotes
them. Each command block sets the ones it uses and creates `$S`.

This task runs after Tasks 1-15 are committed: PR 1's backend and player (Tasks 1-9) on
`feature/phone-wall-setup`, and PR 2's dashboard (Tasks 11-15) on `feature/phone-wall-setup-dashboard`.

What it adds, and why it is built this way (all verified while planning):
- **The phone runs in loopback mode, like the TVs.** `run.sh --wall` starts one more container,
  `e2e-wall-app`: the dashboard built from `FRONTEND_DIR` as `signage_frontend:e2e-wall`, on
  `127.0.0.1:13023`, with `API_BASE_URL=http://127.0.0.1:18020` and `PLAYER_BASE_URL=http://127.0.0.1:13022`.
  The dashboard's entrypoint derives its CSP (`connect-src`, `media-src`, `img-src`) from `API_BASE_URL`,
  so the phone talks to the local API directly. Task 9's `ALLOWED_ORIGINS` already lists `:13023`.
- **The phone cannot use the routed `https://app.khanshoof.com`.** `route.fetch()` + `route.fulfill()`
  drops the file part of a multipart body: a 218 KB `<input type=file>` upload reached the server as
  186 bytes. So the phone asserts the TV's QR text (`https://app.khanshoof.com/pair?code=...`), then opens
  the same path on `http://127.0.0.1:13023`.
- **The phone is a Pixel 7.** In Playwright 1.62, `p.devices["Pixel 7"]` gives a 412x839 viewport at
  DPR 2.625, `is_mobile` and `has_touch`. Its `default_browser_type` key is accepted by `new_context`.
  `lan_context(device=...)` passes it through.
- **Drags use CDP `Input.dispatchTouchEvent`.** These are real touch pointer events (`pointerType`
  "touch"); `page.mouse` would give "mouse". Without `touch-action: none` Chromium cancels the drag as a
  scroll, so this also tests that the editor sets it. On a test page, a 48 px CDP drag moved a
  pointer-captured box by exactly 48 CSS px.
- **`set_input_files` works on the hidden `#wall-setup-file`** (`class="hidden"`, which is
  `display: none`). Its `change` event fired with the file's name, size and type.
- **The Arabic phone needs the `khanshoof_lang=ar` cookie.** `Khan.detectInitialLocale` ranks the
  cookie first, then the organization's locale (`'en'` for the demo admin's "Default" org), then the
  browser's language. With `locale="ar"` alone, the page would turn English after sign-in.
  `ctx.add_cookies([{"name": "khanshoof_lang", "value": "ar", "url": LAN_APP}])` sets it on 127.0.0.1.
- **The dashboard opens no WebSockets** (no `WebSocket` in `frontend/*.js`), so the phone needs only the
  HTTP route guard that `lan_context` already installs. The guard refuses and records in `BLOCKED` every
  request to a khanshoof.com host (`KHAN`) or to a port production publishes on this host's loopback
  (`PROD_LOCAL`: 8000, 3000, 3001, 3003; the dashboard falls back to `<page host>:8000` when
  `API_BASE_URL` is empty).
- **Pairing codes live 300 s** (`PAIR_CODE_TTL_SECONDS`), so each TV page is loaded only when its turn
  comes.
- **Reaching `/pair` takes no special handling.** A signed-out phone on `/pair?code=` gets the sign-in
  form, and a password sign-in resumes `/pair` (`pair_resume`). A signed-in phone (same context, later
  TVs) goes straight to the pair form.
- **The run uses a local, unpushed merge of both branches.** It is a detached worktree with
  `git merge --no-ff --no-commit`, never committed or pushed. `FRONTEND_DIR` points at its `frontend/`.
  The two branches touch no common file: since their merge base fb4d388, PR 1's side changes
  `android-tv/`, `player/`, `backend/` and `scripts/e2e-scan-to-pair/`, and the dashboard side changes
  `frontend/`, `scripts/ui/`, `scripts/check_*` and docs. So the merge is clean, and its backend and
  player equal this branch's.
- **`feature/friendly-dashboard` is not on origin.** `git ls-remote` lists only
  `fix/player-cache-busting`. That changes nothing here, but it matters for Task 17's PR: Task 17's one
  question asks whether that branch (the redesign session's) may be pushed, or whether PR 2 waits (R20).
- **This task's commit is local.** It lands on `feature/phone-wall-setup` after Task 10 has handed PR 1
  off (and opened it, if the user said yes). Pushing it is part of Task 17's single question (R21).

The DOM this task drives comes from Tasks 12, 13 and 14 as fixed. Step 1 checks the names in PR 2's code
before anything is written. From Task 13 (the pair page):
- **What is this TV?:** `#pair-kind-wall` is already checked when this phone set up or joined a wall in
  the last 30 minutes (`WallSetup.recentWallId()`, R13), else `#pair-kind-screen` is. So for TV 2 and
  TV 3 the phone needs two taps after the code: Continue, then Add this TV (Which wall? preselects the
  remembered wall).

From Task 14 (the placement editor):
- **The stage:** `.wall-setup-stage` sits inside `#wall-setup-editor`, has the video's shape and
  `touch-action: none`. It holds `.wall-setup-media` with the muted, looping `video`, which fills it
  (absolute, 100% x 100%), and one `.wall-setup-box[data-col="<col_index>"]` per TV, positioned in % of
  the stage.
  - The global `box-sizing: border-box` makes a box's rect exactly the stage's rect times the box's
    fractions.
  - The selected TV's box is `.is-focus` (z-index 2). One finger drags only that box, from the box itself.
- **The address:** when the /pair steps open the editor for a TV, the address becomes
  `/wall-setup?wall=<id>&tv=<n>` in the same history entry (R19), and that TV is selected. Selecting a TV
  PUTs its box with `highlight: true`, so its outline shows at once.
- **Saving:** a drag PUTs `highlight: true` boxes, one in flight, at least 50 ms apart.
- **Done:** the Done button is `.wall-setup-done` in `.wall-setup-panel`, right after
  `#wall-setup-editor` inside `#wall-setup-view`; it PUTs `highlight: false`, which reaches the TV on the
  same Redis path as a move, so the outline must go within 1 s (R11).
- **The message:** for the TV the /pair steps have just added, `.wall-setup-placed-text` then reads "TV {n}
  placed. Point your camera at the next TV's QR code." (`wallsetup.editor.placed`). In Adjust mode it
  reads only "TV {n} placed." (`wallsetup.editor.placed_short`, R17). Every TV here comes from /pair.
- **The video after the transcode:** a re-read (every 3 s) follows a new `video.url`.

**Files:**
- Modify: `scripts/e2e-scan-to-pair/e2e_wall.py:1-4` (docstring head), `:46-47` (docstring end), `:62`
  (`LAN_PLAY`), `:391` (new `check_crop` and `check_sync` before `api_wall_checks`), `:439-459` (the
  crop loop becomes `check_crop`), `:520-531` (the sync block becomes `check_sync`), and `:588-596`
  (the phone part, and `main`). These are the line numbers of Task 9's file as committed.
- Modify: `scripts/e2e-scan-to-pair/run.sh:8-9` (usage), `:32-34` (`FRONTEND_DIR`, `wall_containers`),
  `:41-42` (`run_wall` reads `FRONTEND_DIR`), `:71-73` (dashboard build), `:106-108` (`e2e-wall-app`),
  `:110-120` (health wait) and `:128-131` (`E2E_PHONE`, `E2E_APP_LAN`). These are the line numbers of
  Task 9's file as committed. Lines from `backend_image=` to the end (the pair run) stay byte-identical,
  and so does Task 9's guard against another session's `e2e-wall-*` containers (`:43-58`), whose name
  list already includes `e2e-wall-app`.
- Test: `FRONTEND_DIR=<merge>/frontend scripts/e2e-scan-to-pair/run.sh --wall` (API part, then phone
  part). Also `scripts/e2e-scan-to-pair/run.sh --wall` (API part only, as after Task 9) and the default
  `scripts/e2e-scan-to-pair/run.sh` (pair flow), which must still pass.

**Interfaces:**
- Consumes:
  - **Task 9.** From `e2e_wall.py`:
    - Helpers: `lan_context(browser, locale="en-US", size=(1920, 1080), scale=1, touch=False, skew_ms=0, device=None, block_fonts=True)`,
      `wait_eval(page, js, pred, timeout_s, what="the condition", interval_ms=100)`, `dominant_color(path)`,
      `dark_share(path, box)`, `save_montage(paths, out)`, `sample_sync_spread_ms(pages, ...)`,
      `wait_for_code(page, previous=None, timeout_s=20)`, `decode_qr(element, name)`, `api(...)`,
      `parse_matrix`, `expected_matrix(box, canvas, viewport)`, `matrix_close`, `wait_playing(page, n, timeout_s=40)`
      and `wait_transcoded(auth, media_id, timeout_s=180)`.
    - JS snippets: `TRANSFORM_JS`, `PLAYING_JS`, `HIGHLIGHT_JS` and `SKEW_READ_JS`.
    - Constants and state: `API`, `LAN_PLAY`, `OUT`, `USER`, `PASSWORD`, `VIDEO`, `APP_URL`, `WS_PREFIX`,
      `KHAN`, `PROD_LOCAL`, `BLOCKED`, `THIRD` and `TVS`.
    - Functions: `api_wall(browser, auth)` and `api_wall_checks(...)` (its checks `1.` to `a.`, with
      `highlight_off_s` and `live_colour_move_s` bounded under 1 s).

    From `run.sh`: `wall_containers`, `cleanup_wall`, `run_wall` (it refuses to start while any
    `e2e-wall-(api|db|redis|player|app|ffmpeg)` container exists), `PLAYER_DIR` and `E2E_WALL_REPLACE`
    (only for your own killed run's leftovers, R23).
  - **Task 12.** `#wall-setup-view`; `#wall-setup-editor` (`dir="ltr"`); the address `/wall-setup?wall=<id>`.
  - **Task 13.**
    - Pair form: `#pair-form`, `#pair-code-input`, `#pair-kind` (shown once the code is complete),
      `#pair-kind-screen` / `#pair-kind-wall` (`#pair-kind-wall` checked when `WallSetup.recentWallId()`
      is not null, R13), `#pair-kind legend`, and `#pair-submit` ("Continue" / "متابعة" for the wall).
    - The export `WallSetup = { show, startFromPair, reset, recentWallId }`.
    - Which wall?: `#wall-setup-choose` with `input[name="wall-setup-wall"]` (value = wall id or `new`;
      the wall this phone used in the last 30 min is preselected, else `new`), and `#wall-setup-next`
      ("Next", or "Add this TV" / "أضف هذه الشاشة").
    - New wall: `#wall-setup-new`, `#wall-setup-name` (prefilled "Video wall"), `#wall-setup-file`,
      `#wall-setup-chosen-name`, `#wall-setup-create` and `#wall-setup-form-error`.
    - Legend strings: "What is this TV?" / "ما دور هذه الشاشة؟".
    - Before an upload or a claim, the steps check the code with `GET /screens/poll/{code}` (R15).
  - **Task 14** (Step 1 checks the names):
    - Editor DOM: `#wall-setup-editor .wall-setup-stage` (holding `.wall-setup-media` with a `video`,
      and `.wall-setup-box[data-col]`, with `.is-focus` on the selected box); after it,
      `.wall-setup-panel` with `.wall-setup-done` and `.wall-setup-placed-text`.
    - `openEditor(wallId, col)` (one declaration: Task 14 deletes Task 13's stub) replaces the current
      history entry with `/wall-setup?wall=<id>&tv=<n>` (R19). Called from the /pair steps, its TV's Done
      says "TV {n} placed. Point your camera at the next TV's QR code." (`wallsetup.editor.placed`, R17).
    - While a finger drags, the editor sends `PUT /walls/{id}/cells/0/{col}/placement` with
      `highlight: true`, one request in flight. Done sends `highlight: false`.
    - `setEditorMedia` follows a new `video.url` on the 3 s re-read.
    - Drags compose as in Task 11: `clampToCanvas(snapBox(moveBox(start, dx/stageW, dy/stageH), others, cw, ch))`.
  - **Backend (Tasks 2, 5, 6, 7).**
    - `GET /walls/{id}` has `layout`, `mode`, `name`, `canvas_width_px/height_px`, `video.media_id` and
      `cells[]`. Each cell has `col_index`, `screen_id`, `place_x/y/w/h`, `screen_w_px/h_px` and
      `highlight_until_ms`.
    - `GET /media` has `filename`, `width_px` and `height_px`.
    - D3: the canvas follows the transcode.
    - R3: a TV added by `POST /walls/{id}/tvs` gets the previous box's height in the default 16:9 shape,
      flush right of it; for these 16:9 TVs that is the next third.
- Produces (Task 17 runs these):
  - **`run.sh --wall` with `FRONTEND_DIR=<dir>`.** It builds `signage_frontend:e2e-wall` from `<dir>` and
    runs `e2e-wall-app` on `127.0.0.1:13023`. It passes `E2E_PHONE=1` and
    `E2E_APP_LAN=http://127.0.0.1:13023` to `e2e_wall.py`. Without `FRONTEND_DIR`, `--wall` runs as after
    Task 9 (`E2E_PHONE=0`, no dashboard container).
  - **New names in `e2e_wall.py`:** the constants `LAN_APP` and `PHONE`, and these functions:
    - `touch_drag(page, x0, y0, x1, y1, steps=12, step_ms=16, hold_ms=0, during=None) -> float`
      (returns `time.monotonic()` at the last move)
    - `check_crop(tvs, canvas, prefix, step)` and `check_sync(tvs, t_all_playing, step) -> (median_ms, spreads)`
    - `phone_wall(browser, auth, device) -> timings`
  - **Output:** the lines `P1.` to `P9.`, a `timings:` JSON line with `phone_*` keys (among them
    `phone_move_max_s`, `phone_sync_median_ms` and `phone_tv1_done_to_outline_off_s` to
    `phone_tv3_done_to_outline_off_s`), then `E2E WALL PHONE OK` and `E2E WALL OK`. It fails unless every
    finger-stop-to-TV time and every Done-to-outline-off time is under 1 s (R11) and the sync median is
    under 100 ms (R10). Screenshots are `shots/wall-phone-*.png`.
  - **The merge-worktree recipe in Steps 6-8**, which Task 17 runs again.
  - **One local commit** on `feature/phone-wall-setup`, pushed only after Task 17's question (R21).

- [ ] **Step 1: Prepare (no repo change): both branches done, names and ports free, and the names Tasks 13 and 14 used**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
git -C "$WT" branch --show-current; git -C "$WT" status --short
git -C "$D" branch --show-current; git -C "$D" status --short
git -C "$D" log --oneline feature/friendly-dashboard..feature/phone-wall-setup-dashboard
docker ps -a --format '{{.Names}}  created {{.CreatedAt}}  {{.Status}}' \
  | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg) ' && echo "BUSY: e2e-wall containers exist" || echo "no e2e-wall containers"
docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -F e2e_wall.py \
  && echo "BUSY: an e2e_wall.py runner is alive" || echo "no e2e_wall.py runner"
(ss -ltn | grep -E ':(18020|13022|13023)\b') && echo "PORT BUSY: find out who holds it" || echo "ports 18020/13022/13023 free"
[ -e "$M" ] && echo "$M EXISTS: a run of this plan left it; remove it as in Step 8 first" || echo "no merge worktree"
[ -d "$WT/scripts/e2e-scan-to-pair/.pylib" ] && echo ".pylib present" || echo ".pylib missing (run.sh installs it)"
for s in wall-setup-stage wall-setup-box data-col is-focus wall-setup-panel wall-setup-done wall-setup-placed-text '&tv=' \
         pair-kind-wall pair-kind-screen recentWallId wall-setup-choose wall-setup-next wall-setup-new wall-setup-name \
         wall-setup-file wall-setup-chosen-name wall-setup-create wall-setup-form-error wall-setup-editor; do
  n=$(git -C "$D" grep -c -F "$s" feature/phone-wall-setup-dashboard -- frontend/index.html frontend/wall-setup.js \
      | awk -F: '{ s += $NF } END { print s + 0 }')
  echo "$s $n"
done
git -C "$D" grep -c 'function openEditor' feature/phone-wall-setup-dashboard -- frontend/wall-setup.js
git -C "$D" grep -c -F 'return { show, startFromPair, reset, recentWallId };' feature/phone-wall-setup-dashboard -- frontend/wall-setup.js
git -C "$D" show feature/phone-wall-setup-dashboard:frontend/i18n/en.json | grep -E '"wallsetup\.editor\.placed(_short)?"'
```

Expected:
- **Branches:** `feature/phone-wall-setup` and `feature/phone-wall-setup-dashboard`, both with an empty
  status.
- **PR 2 log:** at least one commit per Task 11-15.
- **Checks:** `no e2e-wall containers`, `no e2e_wall.py runner`, `ports 18020/13022/13023 free` and
  `no merge worktree`.
- **Names:** every name prints a count of 1 or more.
- **openEditor:** `feature/phone-wall-setup-dashboard:frontend/wall-setup.js:1`. One declaration only:
  Task 13's stub is gone (in one function scope the later declaration wins, and the stub would never
  open the editor on the TV).
- **Export:** `feature/phone-wall-setup-dashboard:frontend/wall-setup.js:1` (R13's export shape).
- **en.json:** the two lines
  `"wallsetup.editor.placed": "TV {n} placed. Point your camera at the next TV's QR code.",` and
  `"wallsetup.editor.placed_short": "TV {n} placed.",` (R17), in the file's order.

On a `BUSY` line, wait (R23: never remove a container the plan did not start). `E2E_WALL_REPLACE=1`
(which removes them) is only for your own run's leftovers: `no e2e_wall.py runner` is printed, and the
`created` time matches the start of a run of yours that was interrupted. Otherwise another session owns
them: leave them and ask the user.

If one of the editor names (`wall-setup-stage` to `&tv=`) prints `0`, `openEditor` is not `1`, or an
en.json value differs, Task 14 changed the editor during its review. Read its `frontend/wall-setup.js`.
Then, in Step 2, update the names that differ before going on:
- the constants `STAGE`, `BOX`, `DONE` and `PLACED_JS`;
- the two address checks in `phone_first_tv` and `phone_join`;
- the placed text in `place_tv`.
If `pair-kind-screen`, `recentWallId` or the export line is missing, Task 13 does not preselect "Part of
a video wall" (R13), and `phone_scan(..., preselected=True)` will fail: fix Task 13 first.

- [ ] **Step 2: Write the failing test: the phone part of `scripts/e2e-scan-to-pair/e2e_wall.py`**

Make these seven replacements in `scripts/e2e-scan-to-pair/e2e_wall.py`, with the Edit tool. Each old text
occurs exactly once in Task 9's file. If Task 9's review changed that file, re-check each with
`grep -cF` first.

2a. The docstring's head. Replace:

```python
"""End-to-end video wall test, driven through the API (the phone UI comes
later). Run it with run.sh --wall, which starts the stack: the backend built
from this tree with 4 workers (as in production), its own Postgres and Redis,
and a player container whose API_BASE_URL is that backend.
```

with:

```python
"""End-to-end video wall test. Run it with run.sh --wall, which starts the
stack: the backend built from this tree with 4 workers (as in production), its
own Postgres and Redis, a player container whose API_BASE_URL is that backend
and, when FRONTEND_DIR is set, a dashboard container for the phone (same API).
It builds one wall through the API and then, with E2E_PHONE=1, another the way
a person does: from a phone, through the dashboard (P1-P9 below).
```

2b. The docstring's end. Replace:

```python
Screenshots: shots/wall-*.png (wall-fail-tv<n>.png when a check fails).
"""
```

with:

```python
Screenshots: shots/wall-*.png (wall-fail-tv<n>.png when a check fails).

The phone part (E2E_PHONE=1; run.sh sets it when FRONTEND_DIR is given)
runs next. A Pixel 7 phone uses the dashboard at http://127.0.0.1:13023
(loopback too: route.fetch() would drop the video from the upload) to set up
a second wall, with three new TVs of the same sizes and clocks:
  P1. each TV's QR decodes to https://app.khanshoof.com/pair?code=<code>; the
      phone opens that path on the loopback dashboard (signing in the first
      time), and "What is this TV?" -> "Part of a video wall" (already chosen
      when the phone remembers a wall, else Its own screen) -> Continue
  P2. TV 1: New video wall, name prefilled "Video wall", the test video
      uploaded through the phone's file input, Create: TV 1 plays the wall
  P3. the upload is transcoded to 3840x720, the wall's canvas follows, and
      TV 1 and the phone's editor (muted, looping) play the transcoded file
  P4. each TV's first box is its third (TV 1's from POST /walls/free; TV 2
      and TV 3 flush right of the TV before them: green, then blue), and the
      phone draws every box there; one finger drags it away and back: the
      TV's crop changes while the finger is down and shows the box the server
      ends up with within 1 s of the finger stopping, and the box snaps back
      onto its third exactly; "TV n" outlines only that TV; Done clears the
      outline on the TV within 1 s (highlight=false rides the moves' Redis
      path) and in the database, and the phone says "TV n placed. Point
      your camera at the next TV's QR code." (the TV it has just added)
  P5. TV 2 and TV 3 join in two taps after the code: "What is this TV?"
      already says "Part of a video wall" and "Which wall?" preselects the
      wall this phone set up (both remembered for 30 minutes), so the phone
      taps Continue, then Add this TV
  P6. crop, as in b., on the wall the phone placed
  P7. sync, as in e.
  P8. a phone in Arabic adds a fourth TV and reaches the editor: the page is
      RTL, the editor an LTR island, so TV 1's box is still on the left
  P9. as in a., for all four TVs (no request to khanshoof.com or to
      production's loopback ports); and no page error or CSP refusal on
      either phone
Phone screenshots: shots/wall-phone-*.png (wall-phone-fail-<page>.png when a
check fails).
"""
```

2c. The phone's dashboard address. Replace:

```python
LAN_PLAY = os.environ.get("E2E_PLAY_LAN", "http://127.0.0.1:13022").rstrip("/")
```

with:

```python
LAN_PLAY = os.environ.get("E2E_PLAY_LAN", "http://127.0.0.1:13022").rstrip("/")
# The dashboard the phone uses (run.sh's e2e-wall-app), and whether to use it.
LAN_APP = os.environ.get("E2E_APP_LAN", "http://127.0.0.1:13023").rstrip("/")
PHONE = os.environ.get("E2E_PHONE", "0") == "1"
```

2d. The crop and sync checks become functions, so the phone part runs the same ones. Replace:

```python
def api_wall_checks(auth, media, tvs, timings, t_upload):
```

with:

```python
def check_crop(tvs, canvas, prefix, step):
    """Each TV's box is the third of the canvas at tv["x"]: its .wall-canvas
    transform is scale(screen / box) translate(-box), and its screenshot
    ({OUT}/<prefix>-tv<n>.png) is mostly its own band's colour, with its
    digit in the middle and no black at the edges. Saves <prefix>-montage.png.
    step labels the printed lines ("b." for the API part)."""
    shots = []
    for tv in tvs:
        want = expected_matrix({"x": tv["x"], "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
        got = wait_eval(tv["page"], TRANSFORM_JS,
                        lambda v, want=want: matrix_close(parse_matrix(v), want),
                        6, f"TV {tv['n']}'s crop matrix{tuple(round(v, 3) for v in want)}")
        tv["page"].wait_for_timeout(300)
        path = f"{OUT}/{prefix}-tv{tv['n']}.png"
        tv["page"].screenshot(path=path)
        shots.append(path)
        band, share = dominant_color(path)
        centre = dark_share(path, (0.35, 0.25, 0.65, 0.75))
        edges = max(dark_share(path, (0.0, 0.0, 0.2, 1.0)), dark_share(path, (0.0, 0.85, 1.0, 1.0)))
        assert band == tv["band"] and share >= 0.75, \
            f"TV {tv['n']} shows mostly {band} ({share:.0%}), expected {tv['band']}"
        assert centre >= 0.08, f"TV {tv['n']}: no digit in the middle (dark share {centre:.2f})"
        assert edges <= 0.02, \
            f"TV {tv['n']}: black at the edges ({edges:.2f}), so the slice does not fill the screen"
        print(f"{step} TV {tv['n']} ({tv['size'][0]}x{tv['size'][1]} @{tv['scale']}x): {got}; "
              f"{share:.0%} {band}, digit in the middle")
    save_montage(shots, f"{OUT}/{prefix}-montage.png")


def check_sync(tvs, t_all_playing, step):
    """The TVs' video.currentTime agree within 100 ms (median of 12 samples,
    each 6 s or more into the loop), despite their shifted clocks.
    t_all_playing: when the last of them started playing. Returns
    (median_ms, spreads)."""
    for tv in tvs:
        skew = tv["page"].evaluate(SKEW_READ_JS)
        assert abs(skew - tv["skew_ms"]) < 250, (tv["n"], skew, tv["skew_ms"])
    rest_s = 6.0 - (time.monotonic() - t_all_playing)
    if rest_s > 0:
        tvs[0]["page"].wait_for_timeout(int(rest_s * 1000))
    median_ms, spreads = sample_sync_spread_ms([tv["page"] for tv in tvs])
    assert median_ms < 100, f"currentTime spread: median {median_ms:.0f} ms of {spreads}"
    print(f"{step} sync: median spread {median_ms:.0f} ms over {len(spreads)} samples "
          f"(max {spreads[-1]:.0f} ms); TV clocks shifted -3000 / 0 / +2000 ms")
    return median_ms, spreads


def api_wall_checks(auth, media, tvs, timings, t_upload):
```

2e. The API part's crop loop calls it (its output is unchanged: the same `b.` lines and the same
`wall-tv<n>.png` / `wall-montage.png` files). Replace:

```python
    shots = []
    for tv in tvs:
        want = expected_matrix({"x": tv["x"], "y": 0.0, "w": THIRD, "h": 1.0}, canvas, tv["size"])
        got = wait_eval(tv["page"], TRANSFORM_JS,
                        lambda v, want=want: matrix_close(parse_matrix(v), want),
                        6, f"TV {tv['n']}'s crop matrix{tuple(round(v, 3) for v in want)}")
        tv["page"].wait_for_timeout(300)
        path = f"{OUT}/wall-tv{tv['n']}.png"
        tv["page"].screenshot(path=path)
        shots.append(path)
        band, share = dominant_color(path)
        centre = dark_share(path, (0.35, 0.25, 0.65, 0.75))
        edges = max(dark_share(path, (0.0, 0.0, 0.2, 1.0)), dark_share(path, (0.0, 0.85, 1.0, 1.0)))
        assert band == tv["band"] and share >= 0.75, \
            f"TV {tv['n']} shows mostly {band} ({share:.0%}), expected {tv['band']}"
        assert centre >= 0.08, f"TV {tv['n']}: no digit in the middle (dark share {centre:.2f})"
        assert edges <= 0.02, \
            f"TV {tv['n']}: black at the edges ({edges:.2f}), so the slice does not fill the screen"
        print(f"b. TV {tv['n']} ({tv['size'][0]}x{tv['size'][1]} @{tv['scale']}x): {got}; "
              f"{share:.0%} {band}, digit in the middle")
    save_montage(shots, f"{OUT}/wall-montage.png")
```

with:

```python
    check_crop(tvs, canvas, "wall", "b.")
```

2f. The API part's sync block calls `check_sync` (same `e.` line). Replace:

```python
    # e. Sync, despite the shifted clocks.
    for tv in tvs:
        skew = tv["page"].evaluate(SKEW_READ_JS)
        assert abs(skew - tv["skew_ms"]) < 250, (tv["n"], skew, tv["skew_ms"])
    rest_s = 6.0 - (time.monotonic() - t_all_playing)
    if rest_s > 0:
        tv1["page"].wait_for_timeout(int(rest_s * 1000))
    median_ms, spreads = sample_sync_spread_ms([tv["page"] for tv in tvs])
    timings["sync_median_ms"] = round(median_ms, 1)
    assert median_ms < 100, f"currentTime spread: median {median_ms:.0f} ms of {spreads}"
    print(f"e. sync: median spread {median_ms:.0f} ms over {len(spreads)} samples "
          f"(max {spreads[-1]:.0f} ms); TV clocks shifted -3000 / 0 / +2000 ms")
```

with:

```python
    # e. Sync, despite the shifted clocks.
    median_ms, _ = check_sync(tvs, t_all_playing, "e.")
    timings["sync_median_ms"] = round(median_ms, 1)
```

2g. The phone part, and `main` running it. Replace:

```python
def main():
    os.makedirs(OUT, exist_ok=True)
    auth = api("POST", "/auth/login", body={"username": USER, "password": PASSWORD})["token"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        timings = api_wall(browser, auth)
        browser.close()
    print("timings:", json.dumps(timings))
    print("E2E WALL OK")
```

with:

```python
# ---- P1-P9: a wall set up from a phone (E2E_PHONE=1) ------------------------
#
# run.sh starts e2e-wall-app, the dashboard on 127.0.0.1:13023, when
# FRONTEND_DIR names one with the wall setup screens (the local merge of this
# branch with feature/phone-wall-setup-dashboard). Its API_BASE_URL is the
# local API, so the phone is in loopback mode like the TVs. It cannot be the
# routed https://app.khanshoof.com: route.fetch() drops the file part of a
# multipart body, so the phone's upload would arrive empty. The TV's QR still
# names https://app.khanshoof.com; the phone asserts that, then opens the same
# path on LAN_APP.

# The placement editor, as the dashboard's wall setup (PR 2, Task 14) draws
# it. The stage has the video's shape and holds the video (filling it) and one
# box per TV, positioned in % of the stage; the selected TV's box is .is-focus.
STAGE = "#wall-setup-editor .wall-setup-stage"
BOX = '#wall-setup-editor .wall-setup-box[data-col="%d"]'   # TV n's box; data-col = col_index = n - 1
DONE = "#wall-setup-view .wall-setup-done"
# Far past the 3% snap distance (about 11 px of a 380 px wide video).
DRAG_PX = 48
# Words the checks read, from the dashboard's en.json / ar.json.
PHONE_TEXT = {
    "en": {"kind": "What is this TV?", "continue": "Continue", "add": "Add this TV"},
    "ar": {"kind": "ما دور هذه الشاشة؟", "continue": "متابعة", "add": "أضف هذه الشاشة"},
}

ADDRESS_JS = "() => location.pathname + location.search"
CHOSEN_JS = """() => {
  const e = document.getElementById('wall-setup-chosen-name');
  return e ? e.textContent.trim() : null;
}"""
FORM_ERROR_JS = """() => {
  const e = document.getElementById('wall-setup-form-error');
  return e && !e.classList.contains('hidden') ? e.textContent.trim() : null;
}"""
CHECKED_WALL_JS = """() => [...document.querySelectorAll(
  '#wall-setup-choose input[name="wall-setup-wall"]:checked')].map(i => i.value)"""
PLACED_JS = """() => {
  const e = document.querySelector('#wall-setup-view .wall-setup-placed-text');
  return e ? e.textContent.trim() : null;
}"""
EDITOR_VIDEO_JS = """() => {
  const v = document.querySelector('#wall-setup-editor .wall-setup-stage video');
  return v ? {src: v.currentSrc, ready: v.readyState, paused: v.paused,
              muted: v.muted, loop: v.loop} : null;
}"""
# v: the stage, which has the video's shape (the video fills it).
BOX_JS = """([box, stage]) => {
  const b = document.querySelector(box), v = document.querySelector(stage);
  if (!b || !v) return null;
  const r = b.getBoundingClientRect(), s = v.getBoundingClientRect();
  return {left: r.left, top: r.top, width: r.width, height: r.height,
          vleft: s.left, vtop: s.top, vwidth: s.width, vheight: s.height};
}"""
HIT_JS = """([x, y, box]) => {
  const el = document.elementFromPoint(x, y), b = document.querySelector(box);
  return !!el && !!b && (el === b || b.contains(el));
}"""
SCROLL_JS = "(sel) => document.querySelector(sel).scrollIntoView({block: 'center', inline: 'nearest'})"
DIR_JS = """() => {
  const e = document.getElementById('wall-setup-editor');
  return {page: document.documentElement.dir, lang: document.documentElement.lang,
          editor: e ? e.getAttribute('dir') : null,
          computed: e ? getComputedStyle(e).direction : null};
}"""


def touch_drag(page, x0, y0, x1, y1, steps=12, step_ms=16, hold_ms=0, during=None):
    """One finger from (x0, y0) to (x1, y1), CSS px of page's viewport, as CDP
    touch events. Chromium turns them into pointer events with pointerType
    "touch" (and touch events), as on a phone; page.mouse would say "mouse"
    even in a touch context, and would hide a missing touch-action: none,
    which makes Chromium cancel the drag as a scroll. The finger stays down
    hold_ms at (x1, y1) before it lifts. during(), when given, runs after
    every move and every 50 ms of the hold. Returns time.monotonic() at the
    last move."""
    cdp = page.context.new_cdp_session(page)
    down = False
    try:
        cdp.send("Input.dispatchTouchEvent",
                 {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0, "id": 1}]})
        down = True
        t_last = time.monotonic()
        for i in range(1, steps + 1):
            f = i / steps
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                {"x": x0 + (x1 - x0) * f, "y": y0 + (y1 - y0) * f, "id": 1}]})
            t_last = time.monotonic()
            if during:
                during()
            page.wait_for_timeout(step_ms)
        hold_until = time.monotonic() + hold_ms / 1000.0
        while time.monotonic() < hold_until:
            if during:
                during()
            page.wait_for_timeout(50)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        down = False
        return t_last
    finally:
        if down:   # failed mid-drag: lift the finger, or the next drag adds a second one
            try:
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            except Exception:
                pass
        cdp.detach()


def third_box(x):
    return {"x": x, "y": 0.0, "w": THIRD, "h": 1.0}


def box_is(box, want, tol=1e-4):
    """Boxes are stored as Postgres REAL (4-byte floats): 1/3 comes back as
    0.33333334, so compare with a tolerance."""
    return all(abs(box[k] - want[k]) < tol for k in ("x", "y", "w", "h"))


def fmt_box(box):
    return "(" + ", ".join(f"{k}={box[k]:.4f}" for k in ("x", "y", "w", "h")) + ")"


def cell_of(wall, col):
    cell = next((c for c in wall["cells"] if c["col_index"] == col), None)
    assert cell is not None, f"wall {wall['id']} has no cell {col}: {wall['cells']}"
    return cell


def stored_box(cell):
    return {k: cell[f"place_{k}"] for k in ("x", "y", "w", "h")}


def phone_page(ctx, errors, label):
    """A page on a phone that records its uncaught errors and CSP refusals."""
    page = ctx.new_page()
    page.set_default_timeout(15000)
    page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
    page.on("console", lambda m: errors.append(f"{label}: {m.text}")
            if m.type == "error" and "Content Security Policy" in m.text else None)
    return page


def phone_error(ph):
    """What the wall setup steps say went wrong, if anything."""
    try:
        return ph.evaluate(FORM_ERROR_JS)
    except Exception:
        return None


def phone_box(ph, col):
    """TV col+1's box on the phone, scrolled to the middle of the screen (clear
    of the phone bar and the tab bar): its rect, the stage's rect (v*; CSS px
    of the viewport) and the box's centre."""
    ph.wait_for_selector(BOX % col, state="visible")
    ph.evaluate(SCROLL_JS, BOX % col)
    ph.wait_for_timeout(300)
    r = ph.evaluate(BOX_JS, [BOX % col, STAGE])
    assert r and r["vwidth"] > 0 and r["vheight"] > 0, \
        f"TV {col + 1}'s box over a laid-out video: {r}"
    r["cx"] = r["left"] + r["width"] / 2
    r["cy"] = r["top"] + r["height"] / 2
    return r


def assert_phone_box_at(r, box, n, tol_px=4.0):
    """The phone draws TV n's box where box (fractions of the canvas) puts it
    on the stage, the video's shape, within tol_px (boxes are border-box, so
    this is rounding room)."""
    want = (r["vleft"] + box["x"] * r["vwidth"], r["vtop"] + box["y"] * r["vheight"],
            box["w"] * r["vwidth"], box["h"] * r["vheight"])
    got = (r["left"], r["top"], r["width"], r["height"])
    assert all(abs(g - w) <= tol_px for g, w in zip(got, want)), (
        f"the phone draws TV {n}'s box at {tuple(round(v, 1) for v in got)} (left, top, width, "
        f"height), expected {tuple(round(v, 1) for v in want)} for {fmt_box(box)}")


def drag_and_follow(ph, tv, auth, wall_id, dx_px):
    """Drags TV n's box dx_px sideways with one finger on the phone, watching
    the TV the whole time. Returns (box, seconds, r): the box the server ends
    up with, how long after the finger's last move the TV showed it (0 when it
    already did), and the phone's rects from before the drag. Fails unless
    the TV's crop changed while the finger was still down."""
    n, col, page = tv["n"], tv["n"] - 1, tv["page"]
    r = phone_box(ph, col)
    assert ph.evaluate(HIT_JS, [r["cx"], r["cy"], BOX % col]), \
        f"a finger on TV {n}'s box ({r['cx']:.0f}, {r['cy']:.0f}) would touch something else"
    before = page.evaluate(TRANSFORM_JS)
    samples = []   # (time, the TV's transform)

    def watch():
        samples.append((time.monotonic(), page.evaluate(TRANSFORM_JS)))

    t_last = touch_drag(ph, r["cx"], r["cy"], r["cx"] + dx_px, r["cy"],
                        steps=16, step_ms=30, hold_ms=500, during=watch)
    moved_live = any(m != before for _, m in samples)
    # Past the tick loop's 5 s fallback, so a slow move is measured, not lost.
    deadline = t_last + 7.0
    last = None
    while True:
        wall = api("GET", f"/walls/{wall_id}", auth)
        box = stored_box(cell_of(wall, col))
        want = expected_matrix(box, (wall["canvas_width_px"], wall["canvas_height_px"]), tv["size"])
        for _ in range(5):
            watch()
            page.wait_for_timeout(40)
        if box == last and matrix_close(parse_matrix(samples[-1][1]), want):
            break   # the phone has stopped saving, and the TV shows that box
        assert time.monotonic() < deadline, (
            f"TV {n} never showed the box the drag left, {fmt_box(box)}: transform "
            f"{samples[-1][1]}, expected matrix{tuple(round(v, 3) for v in want)}")
        last = box
    # The first sample from which the TV kept showing the final box.
    t_match = samples[-1][0]
    for t, m in reversed(samples):
        if not matrix_close(parse_matrix(m), want):
            break
        t_match = t
    assert moved_live, (
        f"TV {n}'s crop stayed {before} while the finger was down: the phone saved the box "
        "only when the finger lifted, or the move took the tick loop's slow path")
    return box, max(0.0, t_match - t_last), r


def check_first_box(ph, tv, placed, auth, wall_id):
    """TV n's box before anyone moves it is its third: TV 1's from POST
    /walls/free (the left third: a 16:9 TV on a 16:3 video), TV 2's and TV
    3's flush right of the TV before them (POST /walls/{id}/tvs). The server
    has it, the phone draws it there next to the boxes placed before it, and
    the TV shows that band."""
    n, col = tv["n"], tv["n"] - 1
    deadline = time.monotonic() + 10
    while True:
        wall = api("GET", f"/walls/{wall_id}", auth)
        cell = cell_of(wall, col)
        if (cell["screen_w_px"], cell["screen_h_px"]) == tuple(tv["size"]):
            break
        assert time.monotonic() < deadline, f"TV {n} never reported its screen size: {cell}"
        time.sleep(0.5)
    ph.wait_for_timeout(3500)   # one of the editor's 3 s re-reads: it now has the TV's size
    box = stored_box(cell)
    assert box_is(box, third_box(tv["x"])), \
        f"TV {n}'s first box is {fmt_box(box)}, expected its third {fmt_box(third_box(tv['x']))}"
    for other in placed + [tv]:
        assert_phone_box_at(phone_box(ph, other["n"] - 1), third_box(other["x"]), other["n"])
    want = expected_matrix(box, (wall["canvas_width_px"], wall["canvas_height_px"]), tv["size"])
    wait_eval(tv["page"], TRANSFORM_JS, lambda v: matrix_close(parse_matrix(v), want), 6,
              f"TV {n} to crop its first box")
    tv["page"].wait_for_timeout(300)
    path = f"{OUT}/wall-phone-tv{n}-first-box.png"
    tv["page"].screenshot(path=path)
    band, share = dominant_color(path)
    # Its outline may be up, with the label in the middle: 60%, not 75%.
    assert band == tv["band"] and share >= 0.6, \
        f"TV {n}'s first box shows mostly {band} ({share:.0%}), expected {tv['band']}"
    where = "POST /walls/free" if n == 1 else f"flush right of TV {n - 1}"
    print(f"P4. TV {n}'s first box {fmt_box(box)} ({where}), drawn there on the phone; "
          f"TV {n} shows {share:.0%} {band}")


def place_tv(ph, tv, placed, auth, wall_id, direction, timings):
    """P4 for one TV on the phone's open editor: its first box, a drag away
    and back, its outline, Done. placed: the TVs placed before it."""
    n, col = tv["n"], tv["n"] - 1
    check_first_box(ph, tv, placed, auth, wall_id)
    dx = direction * DRAG_PX
    away, dt_away, r = drag_and_follow(ph, tv, auth, wall_id, dx)
    want_away = dict(third_box(tv["x"]), x=tv["x"] + dx / r["vwidth"])
    assert box_is(away, want_away, tol=0.01), \
        f"after a {dx:+d} px drag TV {n}'s box is {fmt_box(away)}, expected about {fmt_box(want_away)}"
    back, dt_back, _ = drag_and_follow(ph, tv, auth, wall_id, -dx)
    assert box_is(back, third_box(tv["x"])), \
        f"dragged back, TV {n}'s box did not snap onto its third: {fmt_box(back)}"
    assert_phone_box_at(phone_box(ph, col), back, n)
    timings["phone_moves_s"] += [round(dt_away, 3), round(dt_back, 3)]
    assert max(dt_away, dt_back) < 1.0, (
        f"TV {n} showed the dragged box {dt_away:.2f} s / {dt_back:.2f} s after the finger "
        "stopped: the Redis path did not deliver it, the tick loop's fallback did")
    hl = wait_eval(tv["page"], HIGHLIGHT_JS, lambda v: bool(v) and v["shown"], 3,
                   f"TV {n}'s outline while it is placed")
    assert hl["label"] == f"TV {n}", hl
    for other in placed:
        h = other["page"].evaluate(HIGHLIGHT_JS)
        assert h is not None and not h["shown"], (other["n"], h)
    tv["page"].screenshot(path=f"{OUT}/wall-phone-tv{n}-highlight.png")
    ph.screenshot(path=f"{OUT}/wall-phone-tv{n}-placing.png")
    print(f"P4. TV {n}: dragged {dx:+d} px to x={away['x']:.4f} and back, snapped to "
          f"x={back['x']:.4f}; the TV moved while the finger was down and showed each box "
          f"{dt_away * 1000:.0f} / {dt_back * 1000:.0f} ms after it stopped; outline '{hl['label']}'")
    t0 = time.monotonic()
    ph.tap(DONE)
    # The 6 s wait is only a deadline, so a slow value is measured, not lost.
    wait_eval(tv["page"], HIGHLIGHT_JS, lambda v: bool(v) and not v["shown"], 6,
              f"TV {n}'s outline to go after Done")
    off_s = time.monotonic() - t0
    assert off_s < 1.0, (
        f"TV {n}'s outline went {off_s:.2f} s after Done (highlight=false, bound 1 s): "
        "the Redis path did not deliver it, the tick loop's fallback did")
    deadline = time.monotonic() + 3
    while cell_of(api("GET", f"/walls/{wall_id}", auth), col)["highlight_until_ms"] is not None:
        assert time.monotonic() < deadline, f"Done left TV {n}'s highlight_until_ms set"
        time.sleep(0.2)
    wait_eval(ph, PLACED_JS,
              lambda v: v == f"TV {n} placed. Point your camera at the next TV's QR code.", 5,
              f"the phone to say TV {n} is placed")
    ph.screenshot(path=f"{OUT}/wall-phone-tv{n}-done.png")
    timings[f"phone_tv{n}_done_to_outline_off_s"] = round(off_s, 2)
    print(f"P4. TV {n}: Done; its outline went {off_s:.2f} s later (under 1 s), and the phone says "
          f"'TV {n} placed' and to scan the next TV's QR code")


def phone_scan(ph, tv, sign_in, lang="en", preselected=False):
    """P1: TV n shows its QR; the phone opens it on LAN_APP (signing in when
    sign_in) and chooses Part of a video wall. preselected: this phone set up
    or joined a wall in the last 30 minutes, so "What is this TV?" must open
    on Part of a video wall and the phone only taps Continue (R13); otherwise
    it opens on Its own screen. Returns the TV's code."""
    n = tv["n"]
    tv["page"].goto(LAN_PLAY + "/")   # now: a pairing code lives 300 s
    code = wait_for_code(tv["page"])
    qr = decode_qr(tv["page"].locator("#pairing-qr"), f"wall-phone-tv{n}-qr.png")
    assert qr == f"{APP_URL}/pair?code={code}", (qr, code)
    tv["code"] = code
    ph.goto(LAN_APP + qr[len(APP_URL):])
    if sign_in:
        ph.wait_for_selector("#login-form", state="visible")
        ph.wait_for_timeout(800)
        ph.fill("#login-username", USER)
        ph.fill("#login-password", PASSWORD)
        ph.click("#login-form button[type=submit]")
    ph.wait_for_selector("#pair-form", state="visible")
    assert ph.input_value("#pair-code-input") == code, (ph.input_value("#pair-code-input"), code)
    assert ph.evaluate("document.documentElement.lang") == lang, \
        ph.evaluate("document.documentElement.lang")
    assert ph.locator("#pair-kind-wall").count() == 1, (
        f"the dashboard at {LAN_APP} has no 'Part of a video wall' choice (#pair-kind-wall): "
        "FRONTEND_DIR must be the local merge with feature/phone-wall-setup-dashboard")
    ph.wait_for_selector("#pair-kind", state="visible")
    words = PHONE_TEXT[lang]
    assert ph.inner_text("#pair-kind legend").strip() == words["kind"], \
        ph.inner_text("#pair-kind legend")
    kind = {"wall": ph.is_checked("#pair-kind-wall"), "screen": ph.is_checked("#pair-kind-screen")}
    if preselected:
        assert kind == {"wall": True, "screen": False}, (
            f"TV {n}: this phone used a wall in the last 30 minutes, so 'What is this TV?' must "
            f"open on Part of a video wall (R13); checked: {kind}")
    else:
        assert kind == {"wall": False, "screen": True}, \
            f"TV {n}: a phone that used no wall opens 'What is this TV?' on Its own screen; checked: {kind}"
        ph.check("#pair-kind-wall")
    assert ph.inner_text("#pair-submit").strip() == words["continue"], ph.inner_text("#pair-submit")
    ph.screenshot(path=f"{OUT}/wall-phone-tv{n}-{lang}-what-is-this-tv.png")
    ph.click("#pair-submit")
    return code


def phone_join(ph, tv, wall_id, lang, remembered):
    """Which wall?: wall_id (preselected when this phone set it up, else
    picked), Add this TV, and the editor opens on that TV (the address names
    it: &tv=<n>)."""
    n = tv["n"]
    ph.wait_for_selector("#wall-setup-choose", state="visible")
    checked = ph.evaluate(CHECKED_WALL_JS)
    if remembered:
        assert checked == [str(wall_id)], \
            f"Which wall? preselects {checked}, not the wall this phone set up ({wall_id})"
    else:
        ph.check(f'#wall-setup-choose input[value="{wall_id}"]')
    assert ph.inner_text("#wall-setup-next").strip() == PHONE_TEXT[lang]["add"], \
        ph.inner_text("#wall-setup-next")
    ph.screenshot(path=f"{OUT}/wall-phone-tv{n}-{lang}-which-wall.png")
    ph.click("#wall-setup-next")
    try:
        wait_eval(ph, ADDRESS_JS, lambda v: v == f"/wall-setup?wall={wall_id}&tv={n}", 15,
                  f"the phone to open wall {wall_id} for TV {n}")
    except AssertionError as e:
        raise AssertionError(f"{e}; the phone says: {phone_error(ph)!r}") from None
    ph.wait_for_selector(BOX % (n - 1) + ".is-focus", state="visible")


def phone_first_tv(ph, tv, auth, timings):
    """P1-P4 for TV 1: a new wall, its video uploaded from the phone. Returns
    (wall id, the transcoded video's address)."""
    code = phone_scan(ph, tv, sign_in=True)
    print(f"P1. TV 1 shows {code}; the phone opened its QR's path on {LAN_APP}, signed in "
          "and chose Part of a video wall")
    ph.wait_for_selector("#wall-setup-choose, #wall-setup-new", state="visible")
    if ph.is_visible("#wall-setup-choose"):
        # The API part's "E2E wall" is listed; this phone has set up nothing yet.
        assert ph.evaluate(CHECKED_WALL_JS) == ["new"], ph.evaluate(CHECKED_WALL_JS)
        ph.click("#wall-setup-next")
    ph.wait_for_selector("#wall-setup-new", state="visible")
    assert ph.input_value("#wall-setup-name") == "Video wall", ph.input_value("#wall-setup-name")
    ph.screenshot(path=f"{OUT}/wall-phone-tv1-new-wall.png")
    t0 = time.monotonic()
    ph.set_input_files("#wall-setup-file", VIDEO)   # what "Upload from this phone" opens
    try:
        wait_eval(ph, CHOSEN_JS, lambda v: v == os.path.basename(VIDEO), 60, "the phone's upload")
    except AssertionError as e:
        raise AssertionError(f"{e}; the phone says: {phone_error(ph)!r}") from None
    timings["phone_upload_s"] = round(time.monotonic() - t0, 1)
    assert ph.is_enabled("#wall-setup-create")
    ph.screenshot(path=f"{OUT}/wall-phone-tv1-uploaded.png")
    t0 = time.monotonic()
    ph.click("#wall-setup-create")
    try:
        address = wait_eval(ph, ADDRESS_JS,
                            lambda v: bool(re.fullmatch(r"/wall-setup\?wall=[0-9]+&tv=1", v or "")),
                            15, "the phone to open the new wall on TV 1")
    except AssertionError as e:
        raise AssertionError(f"{e}; the phone says: {phone_error(ph)!r}") from None
    wall_id = int(re.search(r"wall=([0-9]+)", address).group(1))
    wall = api("GET", f"/walls/{wall_id}", auth)
    assert (wall["layout"], wall["mode"], wall["name"]) == ("free", "spanned", "Video wall"), wall
    assert [c["col_index"] for c in wall["cells"]] == [0], wall["cells"]
    created = (wall["canvas_width_px"], wall["canvas_height_px"])
    assert created in ((5760, 1080), (3840, 720)), created
    ph.wait_for_selector(BOX % 0 + ".is-focus", state="visible")
    wait_playing(tv["page"], 1)
    timings["phone_tv1_claim_to_playing_s"] = round(time.monotonic() - t0, 1)
    ph.screenshot(path=f"{OUT}/wall-phone-tv1-editor.png")
    print(f"P2. wall {wall_id} 'Video wall' created from the phone (upload "
          f"{timings['phone_upload_s']} s, canvas then {created[0]}x{created[1]}); TV 1 plays it "
          f"{timings['phone_tv1_claim_to_playing_s']} s after Create")

    # P3. The transcode (about 10 s) usually ends after Create: the canvas
    # follows it (D3), and the TV and the phone switch to the new file.
    row = wait_transcoded(auth, wall["video"]["media_id"])
    assert (row["width_px"], row["height_px"]) == (3840, 720), row
    deadline = time.monotonic() + 10
    while True:
        wall = api("GET", f"/walls/{wall_id}", auth)
        if (wall["canvas_width_px"], wall["canvas_height_px"]) == (3840, 720):
            break
        assert time.monotonic() < deadline, f"the wall's canvas never followed the transcode: {wall}"
        time.sleep(0.5)
    opt = f"{API}/uploads/{row['filename']}"
    wait_eval(tv["page"], PLAYING_JS,
              lambda v: bool(v) and v["src"] == opt and v["ready"] >= 3 and not v["paused"],
              40, "TV 1 to play the transcoded video")
    wait_eval(ph, EDITOR_VIDEO_JS,
              lambda v: bool(v) and v["src"] == opt and v["ready"] >= 2 and not v["paused"]
              and v["muted"] and v["loop"],
              15, "the phone's editor to play the transcoded video, muted and looping")
    print(f"P3. transcoded to 3840x720; the wall's canvas followed; TV 1 and the phone's editor "
          f"(muted, looping) play {row['filename']}")
    place_tv(ph, tv, [], auth, wall_id, 1, timings)
    return wall_id, opt


def phone_next_tv(ph, tv, placed, auth, wall_id, opt, timings):
    """P1, P5 and P4 for TV 2 or TV 3, with the phone that set up the wall:
    two taps after the code (Continue, Add this TV; R13)."""
    n = tv["n"]
    phone_scan(ph, tv, sign_in=False, preselected=True)
    t0 = time.monotonic()
    phone_join(ph, tv, wall_id, "en", remembered=True)
    wall = api("GET", f"/walls/{wall_id}", auth)
    assert sorted(c["col_index"] for c in wall["cells"]) == list(range(n)), wall["cells"]
    wait_playing(tv["page"], n)
    tv["t_playing"] = time.monotonic()
    secs = round(tv["t_playing"] - t0, 1)
    timings[f"phone_tv{n}_claim_to_playing_s"] = secs
    src = tv["page"].evaluate(PLAYING_JS)["src"]
    assert src == opt, (n, src)
    print(f"P5. TV {n} ({tv['code']}) joined wall {wall_id} in two taps (Continue, Add this TV): "
          f"Part of a video wall and the wall were preselected; playing {secs} s after Add this TV")
    # TV 3 is dragged left first, so its box stays on the video.
    place_tv(ph, tv, placed, auth, wall_id, -1 if n == 3 else 1, timings)


def phone_arabic(browser, device, auth, wall_id, contexts, pages, errors, timings):
    """P8: a phone in Arabic adds a fourth TV to the wall and reaches the
    editor. The page is right-to-left; the editor is an LTR island, so TV 1's
    box is still at the video's left (the video is physical)."""
    tv = {"n": 4, "size": (1920, 1080), "scale": 1, "skew_ms": 0, "ws": []}
    tv["ctx"] = lan_context(browser, size=tv["size"])
    contexts.append(tv["ctx"])
    tv["page"] = tv["ctx"].new_page()
    tv["page"].on("websocket", lambda w, urls=tv["ws"]: urls.append(w.url))
    pages.append(("tv4", tv["page"]))
    phone = lan_context(browser, locale="ar", device=device)
    contexts.append(phone)
    # The dashboard's language: the khanshoof_lang cookie (the person's own
    # choice) wins over the organization's, which is English for the demo
    # admin; the browser's language only counts before sign-in.
    phone.add_cookies([{"name": "khanshoof_lang", "value": "ar", "url": LAN_APP}])
    ph = phone_page(phone, errors, "ar-phone")
    pages.append(("ar-phone", ph))
    t0 = time.monotonic()
    code = phone_scan(ph, tv, sign_in=True, lang="ar")
    phone_join(ph, tv, wall_id, "ar", remembered=False)
    d = ph.evaluate(DIR_JS)
    assert d == {"page": "rtl", "lang": "ar", "editor": "ltr", "computed": "ltr"}, d
    boxes = [phone_box(ph, col) for col in range(3)]
    for col, r in enumerate(boxes):
        assert_phone_box_at(r, third_box(TVS[col]["x"]), col + 1)
    assert boxes[0]["left"] < boxes[1]["left"] < boxes[2]["left"], \
        f"in Arabic the boxes run {[round(b['left']) for b in boxes]} (TV 1, 2, 3 lefts)"
    wait_playing(tv["page"], 4)
    timings["phone_ar_scan_to_playing_s"] = round(time.monotonic() - t0, 1)
    ph.screenshot(path=f"{OUT}/wall-phone-ar-editor.png")
    print(f"P8. Arabic phone: TV 4 ({code}) joined wall {wall_id}; the page is RTL, the "
          f"editor LTR, TV 1-3's boxes left to right on their thirds; TV 4 plays "
          f"{timings['phone_ar_scan_to_playing_s']} s after its QR showed")
    return tv


def phone_wall(browser, auth, device):
    """P1-P9 (module docstring). Runs after api_wall: its "E2E wall" is in the
    same organization, so Which wall? has a list to choose from. Returns
    the timings."""
    timings = {"phone_moves_s": []}
    errors = []                 # page errors and CSP refusals on the phones
    contexts, pages = [], []    # closed at the end; screenshotted on a failure
    tvs = [dict(spec, ws=[]) for spec in TVS]
    try:
        for tv in tvs:
            tv["ctx"] = lan_context(browser, size=tv["size"], scale=tv["scale"],
                                    skew_ms=tv["skew_ms"])
            contexts.append(tv["ctx"])
            tv["page"] = tv["ctx"].new_page()
            tv["page"].on("websocket", lambda w, urls=tv["ws"]: urls.append(w.url))
            pages.append((f"tv{tv['n']}", tv["page"]))
        phone = lan_context(browser, device=device)
        contexts.append(phone)
        ph = phone_page(phone, errors, "phone")
        pages.append(("phone", ph))

        wall_id, opt = phone_first_tv(ph, tvs[0], auth, timings)
        for i in (1, 2):
            phone_next_tv(ph, tvs[i], tvs[:i], auth, wall_id, opt, timings)
        moves = timings["phone_moves_s"]
        timings["phone_move_max_s"] = max(moves)
        print(f"P4. finger stopped -> TV showed the box: max {max(moves) * 1000:.0f} ms over "
              f"{len(moves)} drags ({[round(m * 1000) for m in moves]} ms)")

        # P6. Crop, on the wall the phone placed.
        wall = api("GET", f"/walls/{wall_id}", auth)
        canvas = (wall["canvas_width_px"], wall["canvas_height_px"])
        assert canvas == (3840, 720), canvas
        for tv in tvs:
            assert tv["page"].evaluate(PLAYING_JS)["src"] == opt, tv["n"]
        check_crop(tvs, canvas, "wall-phone", "P6.")

        # P7. Sync, despite the shifted clocks.
        median_ms, _ = check_sync(tvs, tvs[2]["t_playing"], "P7.")
        timings["phone_sync_median_ms"] = round(median_ms, 1)

        # P8. Arabic.
        tv4 = phone_arabic(browser, device, auth, wall_id, contexts, pages, errors, timings)

        # P9. Nothing reached production, and the phones ran clean.
        for tv in tvs + [tv4]:
            assert tv["ws"], f"TV {tv['n']} opened no WebSocket"
            stray = [u for u in tv["ws"] if not u.startswith(f"{WS_PREFIX}{wall_id}/ws?")]
            assert not stray, (tv["n"], stray)
        assert not BLOCKED, \
            f"pages asked for khanshoof.com or production's loopback ports (refused): {BLOCKED}"
        assert not errors, f"the phones logged page errors or CSP refusals: {errors}"
        print(f"P9. {sum(len(tv['ws']) for tv in tvs + [tv4])} TV sockets, all to "
              f"{WS_PREFIX}{wall_id}/ws; no request to khanshoof.com or production's loopback "
              "ports; no page error or CSP refusal on either phone")
    except Exception:
        for label, page in pages:
            try:
                page.screenshot(path=f"{OUT}/wall-phone-fail-{label}.png")
            except Exception:
                pass
        if errors:
            print("phone page errors / CSP refusals:", errors)
        raise
    finally:
        for ctx in contexts:
            ctx.close()
    return timings


def main():
    os.makedirs(OUT, exist_ok=True)
    auth = api("POST", "/auth/login", body={"username": USER, "password": PASSWORD})["token"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        timings = api_wall(browser, auth)
        if PHONE:
            timings.update(phone_wall(browser, auth, p.devices["Pixel 7"]))
        browser.close()
    print("timings:", json.dumps(timings))
    if PHONE:
        print("E2E WALL PHONE OK")
    print("E2E WALL OK")
```

- [ ] **Step 3: Let `run.sh --wall` start the dashboard: seven replacements in `scripts/e2e-scan-to-pair/run.sh`**

All seven are inside the header and the `--wall` block. Nothing from `backend_image=` to the end changes.

3a. Usage. Replace:

```bash
#   scripts/e2e-scan-to-pair/run.sh            screenshots in scripts/e2e-scan-to-pair/shots/
#   scripts/e2e-scan-to-pair/run.sh --wall     three TVs on a video wall (e2e_wall.py; see run_wall)
```

with:

```bash
#   scripts/e2e-scan-to-pair/run.sh            screenshots in scripts/e2e-scan-to-pair/shots/
#   scripts/e2e-scan-to-pair/run.sh --wall     three TVs on a video wall (e2e_wall.py; see run_wall)
#   FRONTEND_DIR=<dashboard>/frontend scripts/e2e-scan-to-pair/run.sh --wall
#                                              ... then another wall, set up from a phone
```

3b. `FRONTEND_DIR` and the container list. Replace:

```bash
#   PLAYER_DIR         player to build (default: this tree's player/)
#   E2E_WALL_REPLACE=1 remove leftover e2e-wall-* containers (only your own killed run's)
wall_containers=(e2e-wall-api e2e-wall-db e2e-wall-redis e2e-wall-player e2e-wall-ffmpeg)
```

with:

```bash
#   PLAYER_DIR         player to build (default: this tree's player/)
#   FRONTEND_DIR       dashboard to build (unset: no phone part). With it, e2e-wall-app
#                      serves that dashboard on 127.0.0.1:13023 (API_BASE_URL the local
#                      API, PLAYER_BASE_URL the local player) and e2e_wall.py also sets
#                      up a wall from a phone (E2E_PHONE=1). It must have the phone's
#                      wall setup: the local, unpushed merge of this branch with
#                      feature/phone-wall-setup-dashboard, never this tree's frontend/
#                      (the dashboard from before the redesign).
#   E2E_WALL_REPLACE=1 remove leftover e2e-wall-* containers (only your own killed run's)
wall_containers=(e2e-wall-api e2e-wall-db e2e-wall-redis e2e-wall-player e2e-wall-app e2e-wall-ffmpeg)
```

`run_wall`'s guard (Task 9) already refuses to start while any of
`e2e-wall-(api|db|redis|player|app|ffmpeg)` exists, so `e2e-wall-app` needs no change there. The array
only adds it to what `cleanup_wall` removes: the containers this run started.

3c. `run_wall` reads `FRONTEND_DIR`, before Task 9's guard and the trap (a bad `FRONTEND_DIR` stops the
run before it starts or removes anything). Replace:

```bash
run_wall() {
  local player_dir="${PLAYER_DIR:-$repo/player}"
```

with:

```bash
run_wall() {
  local player_dir="${PLAYER_DIR:-$repo/player}"
  local frontend_dir="${FRONTEND_DIR:-}" phone=0
  if [[ -n "$frontend_dir" ]]; then
    if [[ ! -f "$frontend_dir/Dockerfile" ]]; then
      echo "FRONTEND_DIR=$frontend_dir has no Dockerfile: point it at a dashboard's frontend/." >&2
      exit 1
    fi
    frontend_dir="$(cd "$frontend_dir" && pwd)"
    phone=1
  fi
```

3d. Build the dashboard. Replace:

```bash
  echo "Building signage_backend:e2e and signage_player:e2e-wall…"
  docker build -q -t signage_backend:e2e "$repo/backend" > /dev/null
  docker build -q -t signage_player:e2e-wall "$player_dir" > /dev/null
```

with:

```bash
  echo "Building signage_backend:e2e and signage_player:e2e-wall…"
  docker build -q -t signage_backend:e2e "$repo/backend" > /dev/null
  docker build -q -t signage_player:e2e-wall "$player_dir" > /dev/null
  if (( phone )); then
    echo "Building signage_frontend:e2e-wall from $frontend_dir…"
    docker build -q -t signage_frontend:e2e-wall "$frontend_dir" > /dev/null
  fi
```

3e. Run it. Replace:

```bash
  docker run -d --name e2e-wall-player -p 127.0.0.1:13022:80 -e PLAYER_VERSION=e2e-wall \
    -e API_BASE_URL=http://127.0.0.1:18020 -e APP_URL=https://app.khanshoof.com \
    signage_player:e2e-wall > /dev/null
```

with:

```bash
  docker run -d --name e2e-wall-player -p 127.0.0.1:13022:80 -e PLAYER_VERSION=e2e-wall \
    -e API_BASE_URL=http://127.0.0.1:18020 -e APP_URL=https://app.khanshoof.com \
    signage_player:e2e-wall > /dev/null
  # The phone's dashboard, in loopback mode too: its CSP comes from
  # API_BASE_URL, and the video upload goes straight to the local API.
  # Through Playwright's routing (route.fetch) the upload would arrive empty.
  if (( phone )); then
    docker run -d --name e2e-wall-app -p 127.0.0.1:13023:80 -e API_BASE_URL=http://127.0.0.1:18020 \
      -e PLAYER_BASE_URL=http://127.0.0.1:13022 -e WALLS_PHASE2_ENABLED=true \
      signage_frontend:e2e-wall > /dev/null
  fi
```

3f. Wait for it too. Replace:

```bash
  local up=0
  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:18020/health > /dev/null 2>&1 \
        && curl -fsS -o /dev/null http://127.0.0.1:13022/ > /dev/null 2>&1; then up=1; break; fi
    sleep 1
  done
  if [[ "$up" != 1 ]]; then
    echo "The wall stack never came up. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    exit 1
  fi
```

with:

```bash
  local up=0
  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:18020/health > /dev/null 2>&1 \
        && curl -fsS -o /dev/null http://127.0.0.1:13022/ > /dev/null 2>&1 \
        && { (( ! phone )) || curl -fsS -o /dev/null http://127.0.0.1:13023/ > /dev/null 2>&1; }; then
      up=1; break
    fi
    sleep 1
  done
  if [[ "$up" != 1 ]]; then
    echo "The wall stack never came up. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    if (( phone )); then
      echo "e2e-wall-app, last 20 log lines:" >&2
      docker logs --tail 20 e2e-wall-app >&2 || true
    fi
    exit 1
  fi
```

3g. Tell `e2e_wall.py`. Replace:

```bash
  echo "Three TVs on a video wall…"
  if ! docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
      -e E2E_API=http://127.0.0.1:18020 -e E2E_PLAY_LAN=http://127.0.0.1:13022 \
      --entrypoint python signage_renderer:latest -I e2e_wall.py; then
```

with:

```bash
  if (( phone )); then
    echo "Three TVs on a video wall, through the API; then three more from a phone…"
  else
    echo "Three TVs on a video wall…"
  fi
  if ! docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
      -e E2E_API=http://127.0.0.1:18020 -e E2E_PLAY_LAN=http://127.0.0.1:13022 \
      -e E2E_APP_LAN=http://127.0.0.1:13023 -e E2E_PHONE="$phone" \
      --entrypoint python signage_renderer:latest -I e2e_wall.py; then
```

- [ ] **Step 4: Check syntax, and that the pair run is untouched**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
bash -n scripts/e2e-scan-to-pair/run.sh && echo "run.sh syntax OK"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('e2e_wall.py parses')" scripts/e2e-scan-to-pair/e2e_wall.py
diff <(git show HEAD:scripts/e2e-scan-to-pair/run.sh | sed -n '/^backend_image=/,$p') \
     <(sed -n '/^backend_image=/,$p' scripts/e2e-scan-to-pair/run.sh) && echo "pair path byte-identical"
test -x scripts/e2e-scan-to-pair/run.sh && echo "still executable"
grep -c 'e2e-wall-app' scripts/e2e-scan-to-pair/run.sh
grep -nE '^def (touch_drag|check_crop|check_sync|phone_wall)\(' scripts/e2e-scan-to-pair/e2e_wall.py
git diff --stat
```

Expected: `run.sh syntax OK`, `e2e_wall.py parses`, `pair path byte-identical`, `still executable`, and
`5`: the lines naming `e2e-wall-app`, which are the `FRONTEND_DIR` comment, `wall_containers`,
`docker run`, and the health wait's `echo` and `docker logs`. Then the four `def` lines, and
`git diff --stat` showing exactly `scripts/e2e-scan-to-pair/e2e_wall.py` and
`scripts/e2e-scan-to-pair/run.sh`.

(While planning, these seven Python and seven shell replacements were applied in order to Task 9's
files as its fixed plan text writes them. Each old text matched exactly once; the result parsed, every
name it loads is defined, `bash -n` passed, the pair path and Task 9's container guard stayed
byte-identical, and `grep -c e2e-wall-app` gave 5. With `docker` and `curl` stubbed, `run.sh --wall`
built and started `e2e-wall-app` and passed `E2E_PHONE=1` only with `FRONTEND_DIR` set, passed
`E2E_PHONE=0` without it, and stopped before touching Docker when `FRONTEND_DIR` had no Dockerfile. The
new helpers `touch_drag`, `phone_box`, `HIT_JS`, `assert_phone_box_at`, `box_is` and `DIR_JS` were run
in `signage_renderer:latest` on a Pixel 7 context against a stand-in editor page. They gave 16 touch
pointer moves, a 48 px drag reading x=0.1263 on a 380 px video, a snap back to 0, and a lifted finger
after a failing `during()`.)

- [ ] **Step 5: Run it against the redesign's dashboard without PR 2, and watch it fail**

The red run uses `feature/friendly-dashboard`'s `frontend/`, which has no wall setup. Everything up to
the new choice must work (the dashboard container, loopback sign-in, the pair form with the code filled
in), and the phone part must then fail for the right reason.

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
rm -rf "$S/frontend-before-pr2" && mkdir -p "$S/frontend-before-pr2"
git -C "$WT" archive feature/friendly-dashboard frontend | tar -x -C "$S/frontend-before-pr2"
cd "$WT" && FRONTEND_DIR="$S/frontend-before-pr2/frontend" scripts/e2e-scan-to-pair/run.sh --wall 2>&1 \
  | tee "$S/e2e-wall-phone-red.log"; echo "exit ${PIPESTATUS[0]}"
```

Run it with the Bash tool's timeout set to 600000 ms, or with `run_in_background`, and wait for the
`exit` line: the default 2-minute timeout would kill it. A run killed before it prints `exit` can skip
its trap (a SIGKILL always does). If its `e2e-wall-*` containers are still there, they are your own
leftovers: wait until Step 1's check prints `no e2e_wall.py runner` (the test container can outlive a
killed client), then rerun once with `E2E_WALL_REPLACE=1` in front of `FRONTEND_DIR=...`.

Expected: FAIL (`exit 1`) after about 3 minutes.
- **What runs first:** `Building signage_frontend:e2e-wall from …/frontend-before-pr2/frontend…`, then
  `Three TVs on a video wall, through the API; then three more from a phone…`. Then all of Task 9's
  lines, `1.` to `a.`.
- **No phone lines:** there is no `P1.` line, and neither `E2E WALL PHONE OK` nor `E2E WALL OK`.
- **The traceback** ends with
  `AssertionError: the dashboard at http://127.0.0.1:13023 has no 'Part of a video wall' choice (#pair-kind-wall): FRONTEND_DIR must be the local merge with feature/phone-wall-setup-dashboard`,
  followed by `e2e_wall.py failed. e2e-wall-api, last 80 log lines:`.
- **The screenshot:** `scripts/e2e-scan-to-pair/shots/wall-phone-fail-phone.png` shows the redesign's
  pair form, signed in, with TV 1's code filled in. That proves the stack, the loopback dashboard and the
  sign-in all work.
- **Afterwards:** `docker ps -a --format '{{.Names}}' | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg)$'`
  prints nothing (the trap removed the stack).

- [ ] **Step 6: Make the local, unpushed merge of both branches**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
git -C "$WT" worktree add --detach "$M" feature/phone-wall-setup
git -C "$M" merge --no-ff --no-commit feature/phone-wall-setup-dashboard; echo "merge exit $?"
git -C "$M" diff --name-only --diff-filter=U
git -C "$M" diff --cached --stat HEAD | tail -1
git -C "$M" diff --cached --name-only HEAD \
  | grep -vE '^(frontend/|scripts/ui/|scripts/check_ui_contract\.sh$|scripts/check_i18n\.py$|docs/superpowers/)' \
  || echo "the merge brings only the dashboard, its UI checks and docs"
git -C "$M" diff --cached --quiet HEAD -- backend player scripts/e2e-scan-to-pair && echo "backend, player and harness are this branch's"
ls "$M/frontend/wall-setup.js" "$M/frontend/wall-geometry.js" "$M/frontend/wall-setup.css"
```

Expected:
- **The merge:** `Automatic merge went well; stopped before committing as requested`, then
  `merge exit 0`.
- **No conflicts:** nothing from `--diff-filter=U`.
- **Stat line:** a `… files changed, … insertions(+), … deletions(-)` line.
- **Contents:** `the merge brings only the dashboard, its UI checks and docs` and
  `backend, player and harness are this branch's`.
- **Files:** the three dashboard files are listed.

It is a detached worktree with an uncommitted merge, so no branch or commit is created, and nothing can
be pushed. If the merge reports conflicts, stop: the branches touch the same file, which the plan did not
expect. Run `git -C "$M" merge --abort` and report the conflicting files to the user.

The run in Step 7 starts from this worktree (`$WT`), with `FRONTEND_DIR` set to the merge's `frontend/`.
The backend and player it builds equal the merge's (checked above), and the harness is this task's,
still uncommitted.

- [ ] **Step 7: Run it against the merge and watch it pass**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT" && FRONTEND_DIR="$M/frontend" scripts/e2e-scan-to-pair/run.sh --wall 2>&1 \
  | tee "$S/e2e-wall-phone.log"; echo "exit ${PIPESTATUS[0]}"
```

Run it with `run_in_background` and wait for the `exit` line (preferred: at 5-6 minutes it is close to
the Bash tool's 600000 ms cap), or in the foreground with the timeout set to 600000 ms. If the tool
killed it, clear your own leftovers as in Step 5 and run it again.

Expected: PASS (`exit 0`) in about 5-6 minutes. The output has this shape (codes, ids, percentages and
times vary):

```text
Starting a throwaway wall stack…
Building signage_backend:e2e and signage_player:e2e-wall…
Building signage_frontend:e2e-wall from /home/ahmed/signage/.claude/worktrees/wall-e2e-merge/frontend…
Three TVs on a video wall, through the API; then three more from a phone…
1. … through a. (Task 9's API part, unchanged)
P1. TV 1 shows K7MP2Q; the phone opened its QR's path on http://127.0.0.1:13023, signed in and chose Part of a video wall
P2. wall 2 'Video wall' created from the phone (upload 0.x s, canvas then 5760x1080); TV 1 plays it x.x s after Create
P3. transcoded to 3840x720; the wall's canvas followed; TV 1 and the phone's editor (muted, looping) play <uuid>.opt.mp4
P4. TV 1's first box (x=0.0000, y=0.0000, w=0.3333, h=1.0000) (POST /walls/free), drawn there on the phone; TV 1 shows 9x% red
P4. TV 1: dragged +48 px to x=0.1xxx and back, snapped to x=0.0000; the TV moved while the finger was down and showed each box … / … ms after it stopped; outline 'TV 1'
P4. TV 1: Done; its outline went 0.xx s later (under 1 s), and the phone says 'TV 1 placed' and to scan the next TV's QR code
P5. TV 2 (R4WZ8N) joined wall 2 in two taps (Continue, Add this TV): Part of a video wall and the wall were preselected; playing x.x s after Add this TV
P4. TV 2's first box (x=0.3333, y=0.0000, w=0.3333, h=1.0000) (flush right of TV 1), drawn there on the phone; TV 2 shows 9x% green
P4. TV 2: dragged +48 px to x=0.4xxx and back, snapped to x=0.3333; …
P4. TV 2: Done; …
P5. TV 3 (…) joined wall 2 …
P4. TV 3's first box (x=0.6667, …) (flush right of TV 2), …; TV 3 shows 9x% blue
P4. TV 3: dragged -48 px to x=0.5xxx and back, snapped to x=0.6667; …
P4. TV 3: Done; …
P4. finger stopped -> TV showed the box: max … ms over 6 drags ([…] ms)
P6. TV 1 (1920x1080 @1x): matrix(1.5, 0, 0, 1.5, 0, 0); 9x% red, digit in the middle
P6. TV 2 (960x540 @2x): matrix(0.75, 0, 0, 0.75, -960, 0); 9x% green, digit in the middle
P6. TV 3 (1920x1080 @1x): matrix(1.5, 0, 0, 1.5, -3840, 0); 9x% blue, digit in the middle
P7. sync: median spread … ms over 12 samples (max … ms); TV clocks shifted -3000 / 0 / +2000 ms
P8. Arabic phone: TV 4 (…) joined wall 2; the page is RTL, the editor LTR, TV 1-3's boxes left to right on their thirds; TV 4 plays … s after its QR showed
P9. N TV sockets, all to ws://127.0.0.1:18020/walls/2/ws; no request to khanshoof.com or production's loopback ports; no page error or CSP refusal on either phone
timings: {…, "phone_moves_s": […], "phone_tv1_done_to_outline_off_s": 0.xx, …, "phone_move_max_s": …, "phone_sync_median_ms": …, …}
E2E WALL PHONE OK
E2E WALL OK
exit 0
```

`P2` says `canvas then 3840x720` instead when the transcode beat the tap on Create. Then open
`shots/wall-phone-montage.png` (it must read red 1 | green 2 | blue 3),
`shots/wall-phone-tv2-first-box.png` (green, before any drag) and `shots/wall-phone-ar-editor.png`
(Arabic page, boxes left to right).

If a check fails, look at `shots/wall-phone-fail-<page>.png`, the printed phone errors and the API log
tail. Fix the code under test in the task that owns it (PR 2's for the phone, PR 1's for the TV or the
API), not the thresholds here: the 1 s bounds (moves and Done, R11) and the 100 ms sync bound (R10) are
fixed. When one fails or flakes, investigate; never relax it, and never rerun until it passes without
reporting the failures. Then run Steps 8, 6 and 7 again: the merge must be remade after a PR 2 commit.

- [ ] **Step 8: Remove the merge worktree**

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
git -C "$M" merge --abort
git -C "$WT" worktree remove "$M"
git -C "$WT" worktree prune
git -C "$WT" worktree list | grep -F wall-e2e-merge || echo "merge worktree gone"
git -C "$WT" branch --list | grep -c -F wall-e2e-merge
```

Expected: `merge worktree gone` and `0` (a detached worktree made no branch). If `worktree remove`
refuses because the tree is not clean, run `git -C "$M" status --short` and read what is there. Only
build output from this task may be there (none is expected, since Docker only reads the tree). Then run
`git -C "$WT" worktree remove --force "$M"`.

- [ ] **Step 9: Check the wall run without a dashboard, and the pair run, still pass**

Two commands, each its own Bash call with the timeout set to 600000 ms (or with `run_in_background`,
waiting for its `exit` line). First the wall run without `FRONTEND_DIR`:

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT"
scripts/e2e-scan-to-pair/run.sh --wall 2>&1 | tee "$S/e2e-wall-api-only.log" | tail -3; echo "wall exit ${PIPESTATUS[0]}"
grep -cE '^P[0-9]\.|E2E WALL PHONE OK|signage_frontend' "$S/e2e-wall-api-only.log"
```

Expected:
- **Wall run without a dashboard:** the last lines are `timings: {...}` and `E2E WALL OK`, then
  `wall exit 0`.
- **The count:** `0`. Without `FRONTEND_DIR` nothing about the phone or the dashboard runs, as after
  Task 9.

Then the pair run. It removes the `e2e-pair-*` names when it starts and has no guard of its own, so the
command first stops if another pair run holds them (R23):

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT"
if docker ps -a --format '{{.Names}}' | grep -E '^e2e-pair-(api|db|redis|player|app)$' \
   || docker ps --no-trunc --format '{{.Command}}' | grep -F e2e_pair.py \
   || ss -ltn | grep -E ':(18000|13000|13001)\b'; then
  echo "BUSY: another pair run is up; wait for it"; exit 1
fi
echo "no e2e-pair run"
scripts/e2e-scan-to-pair/run.sh 2>&1 | tee "$S/e2e-pair.log" | tail -2; echo "pair exit ${PIPESTATUS[0]}"
```

Expected: `no e2e-pair run`, then `timings: {...}`, `E2E OK` and `pair exit 0`. If the tool killed a
run, Task 9 Steps 5 and 7 say how to tell your own leftovers from another session's.

- [ ] **Step 10: Commit (local only)**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup
git branch --show-current   # must print feature/phone-wall-setup; stop if it does not
git add scripts/e2e-scan-to-pair/run.sh scripts/e2e-scan-to-pair/e2e_wall.py
git status --short    # exactly these two files staged; shots/ and .pylib/ stay ignored
git commit -m "test(e2e): set up the video wall from a phone, through the dashboard

FRONTEND_DIR=<dashboard> run.sh --wall also starts e2e-wall-app (the
dashboard, on the loopback API) and runs e2e_wall.py's phone part. A
Pixel 7 scans each TV's QR, signs in, starts a wall with a video
uploaded from the phone, and places TV 1, 2 and 3 by dragging their
boxes with one finger (CDP touch events). TV 2 and TV 3 join in two
taps, with the wall the phone remembers preselected. Each TV's crop
changes while the finger is down and shows the final box within 1 s of
it stopping. The boxes snap back onto their thirds, and Done clears the
TV's outline within 1 s. Then the API part's crop and sync checks run
on that wall, and an Arabic phone adds a fourth TV and finds an LTR
editor in an RTL page.

The dashboard must be the local, unpushed merge with
feature/phone-wall-setup-dashboard. Without FRONTEND_DIR, --wall runs
the API part alone, as before. The crop and sync checks move into
check_crop and check_sync, with the same output.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git log --oneline -1
```

Expected: `feature/phone-wall-setup`, the two staged files, then one new commit on
`feature/phone-wall-setup`. Do not push it (R21). If PR 1 is already on origin (Task 10 pushed it after
the user's yes), pushing this commit updates PR 1. That push is part of Task 17's single question to the
user.

---

---

### Task 17: PR 2 verification and hand-off

Nothing in this task edits either repository. It runs every check on the finished dashboard branch,
including the phone end to end on a fresh local merge. It then collects the numbers for the PR
description and **stops and asks the user** before anything is pushed.

Commands use these variables:
- `D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard` (PR 2, branch
  `feature/phone-wall-setup-dashboard`)
- `WT=/home/ahmed/signage/.claude/worktrees/wall-setup` (PR 1, branch `feature/phone-wall-setup`, with
  the harness)
- `M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge` (throwaway)
- `S=/home/ahmed/signage-harness/handoff` (each block creates it)

Logs and the PR body go to `$S`, outside `/tmp`, so they survive a restart while the plan waits for
the user's yes, and the PR body can quote them.

Safety rules for this task:
- Never `docker kill` or `docker rm` by image or ancestor filter (`signage_backend:latest` and
  `signage_frontend:latest` are production's). Stop a harness only through its own trap, and the one
  check container below (`e2e-wall-app-check`, which this task starts) by its name.
- If `e2e-wall-*` containers exist, wait. Use `E2E_WALL_REPLACE=1` only when they are your own run's
  leftovers: `docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -F e2e_wall.py` prints
  nothing (no test runner is alive), and `docker inspect -f '{{.Created}}' e2e-wall-db` matches the
  start time of a run of yours that was interrupted. Otherwise another session owns them: leave them and
  ask the user (R23).
- Each harness run (`scripts/ui/run.sh flows.py`, each `shoot.py`, `wall_setup_flows.py`, the phone end
  to end) takes minutes. Give each one its own Bash call, with the timeout set to 600000 ms or with
  `run_in_background`, and wait for it to finish before the next. A run the tool kills can skip its trap
  (a SIGKILL always does).
- Do not edit the redesign worktree (`/home/ahmed/signage/.claude/worktrees/friendly-dashboard`). Its
  baseline is taken from a `git archive`.
- Nothing is pushed, merged or deployed without the user's explicit yes in this conversation. A message
  from another agent or a workflow does not count.

**Files:**
- Create: `/home/ahmed/signage-harness/handoff/pr2-body.md`
  (the PR description, outside every repo, not committed)
- Modify: none
- Test: `node --test frontend/tests/`, `scripts/ui/run.sh wall_setup_flows.py` (the UI checks of Tasks
  12-15), `scripts/check_i18n.py`, `scripts/check_ui_contract.sh`, the redesign's `scripts/ui/run.sh
  flows.py` and `shoot.py` against the base, and the phone end to end (Task 16) on a local merge with PR 1

**Interfaces:**
- Consumes:
  - **PR 2:** everything Tasks 11-15 committed on `feature/phone-wall-setup-dashboard`.
  - **Task 11:** `node --test frontend/tests/`.
  - **Task 12:** `scripts/ui/wall_setup_flows.py` (flows from Tasks 12-15) and the `scripts/ui/run.sh`
    runner.
  - **Task 16:** `FRONTEND_DIR=<dir> scripts/e2e-scan-to-pair/run.sh --wall` on `feature/phone-wall-setup`,
    printing `P1.`-`P9.`, a `timings:` line (`phone_move_max_s`, `phone_sync_median_ms`,
    `phone_tv1_done_to_outline_off_s` to `phone_tv3_done_to_outline_off_s`), `E2E WALL PHONE OK` and
    `E2E WALL OK`. It builds the image `signage_frontend:e2e-wall`. Its commit on
    `feature/phone-wall-setup` is local (R21).
  - **PR 1's state from Task 10:** pushed or not, and its PR URL if one was opened.
- Produces: after the user's OK only, `feature/phone-wall-setup-dashboard` on origin and a PR with base
  `feature/friendly-dashboard`, plus whatever else the user's yes covered (see Step 10): the redesign
  branch on origin (R20) and Task 16's commit on PR 1 (R21). If the user says PR 2 waits, the branches
  stay local and that is a complete hand-off.

- [ ] **Step 1: Confirm both branches, clean trees, and that the redesign base has not moved**

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
git -C "$D" branch --show-current; git -C "$D" status --short
git -C "$D" log --oneline feature/friendly-dashboard..HEAD
git -C "$D" rev-list --count HEAD..feature/friendly-dashboard
git -C "$D" log --merges --oneline feature/friendly-dashboard..HEAD | wc -l
git -C "$WT" branch --show-current; git -C "$WT" status --short
git -C "$WT" log --oneline -1 --grep='set up the video wall from a phone'
```

Expected:
- **PR 2 worktree:** `feature/phone-wall-setup-dashboard`, with an empty status.
- **PR 2 log:** one or more commits per Task 11-15, newest first.
- **Base:** `0` commits on `feature/friendly-dashboard` that PR 2 lacks, and `0` merge commits.
- **PR 1 worktree:** `feature/phone-wall-setup`, with an empty status.
- **Harness:** Task 16's commit line.

If the base count is not `0`, the redesign session has moved `feature/friendly-dashboard` since PR 2 was
cut, and its Tasks 8, 9 or 11 may have changed the hooked markup. Stop and tell the user how many
commits and which files they touch (`git -C "$D" diff --stat HEAD...feature/friendly-dashboard`). Ask
whether to rebase PR 2 onto them. Do not rebase without that answer.

- [ ] **Step 2: Run the node tests**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
node --test frontend/tests/ 2>&1 | tee "$S/node-tests.log" | tail -8; echo "exit ${PIPESTATUS[0]}"
```

Expected: `# pass 24`, `# fail 0` and `exit 0`. These are Task 11's tests; Tasks 12-15 test through the
UI flows and add no node tests.

- [ ] **Step 3: Run the UI checks of Tasks 12-15**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
bash scripts/ui/run.sh wall_setup_flows.py 2>&1 | tee "$S/ui-wall-setup.log" | tail -3; echo "exit ${PIPESTATUS[0]}"
echo "flows: $(grep -c '^@flow(' scripts/ui/wall_setup_flows.py)  PASS: $(grep -c '^PASS ' "$S/ui-wall-setup.log")  FAIL: $(grep -c '^FAIL ' "$S/ui-wall-setup.log")"
```

Run it with the timeout set to 600000 ms (it takes about 5 minutes) or with `run_in_background`.

Expected: `0 failed` and `exit 0`, then `flows: N  PASS: N  FAIL: 0` with the two N equal, one PASS per
flow in the file. As planned, N is 55: Task 12's 11, Task 13's 21, Task 14's 18 (`wall setup editor:
…`) and Task 15's 5. A different N is fine if the file's own count matches it.

- [ ] **Step 4: Run the i18n parity and UI contract guards**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
{ python3 scripts/check_i18n.py; echo "i18n exit $?"; bash scripts/check_ui_contract.sh; echo "contract exit $?"; } 2>&1 | tee "$S/guards.log"
```

Expected: `i18n OK across frontend, landing, player`, `i18n exit 0`, `UI contract OK`, `contract exit 0`.

- [ ] **Step 5: Run the redesign's own UI harness, against the base as it is now**

The baseline comes from a `git archive` of `feature/friendly-dashboard`, not from Task 11's file. So it
is right even if the base moved and PR 2 was rebased (Step 1).

Each harness run takes minutes, and together they pass the Bash tool's 10-minute cap. So run commands
(a) to (g) each as its own Bash call, with `run_in_background` (or the timeout set to 600000 ms), and
wait for each to finish before starting the next: two Playwright runs at once would slow each other and
could make timing-sensitive flows flake. Then run (h), which only compares the outputs. Each command
sets its own variables.

(a) The base: extract it, then run its flows.

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
rm -rf "$S/base-ui" && mkdir -p "$S/base-ui"
git -C "$D" archive feature/friendly-dashboard frontend scripts/ui | tar -x -C "$S/base-ui"
bash "$S/base-ui/scripts/ui/run.sh" flows.py > "$S/flows-base.txt" 2>&1; echo "base flows exit $?"; tail -n 1 "$S/flows-base.txt"
```

(b) PR 2's flows.

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
(cd "$D" && bash scripts/ui/run.sh flows.py) > "$S/flows-final.txt" 2>&1; echo "PR 2 flows exit $?"; tail -n 1 "$S/flows-final.txt"
```

(c) The base's `/pair` layout matrix.

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
bash "$S/base-ui/scripts/ui/run.sh" shoot.py --routes '/pair?code=ABCD23' --fixtures populated \
  > "$S/shoot-base-pair.txt" 2>&1; grep -E '^[0-9]+ shots' "$S/shoot-base-pair.txt"
```

(d) PR 2's `/pair` layout matrix.

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
(cd "$D" && bash scripts/ui/run.sh shoot.py --routes '/pair?code=ABCD23' --fixtures populated) \
  > "$S/shoot-final-pair.txt" 2>&1; grep -E '^[0-9]+ shots' "$S/shoot-final-pair.txt"
```

(e) The base's `/walls` layout matrix.

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
bash "$S/base-ui/scripts/ui/run.sh" shoot.py --routes /walls --fixtures populated \
  > "$S/shoot-base-walls.txt" 2>&1; grep -E '^[0-9]+ shots' "$S/shoot-base-walls.txt"
```

(f) PR 2's `/walls` layout matrix.

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
(cd "$D" && bash scripts/ui/run.sh shoot.py --routes /walls --fixtures populated) \
  > "$S/shoot-final-walls.txt" 2>&1; grep -E '^[0-9]+ shots' "$S/shoot-final-walls.txt"
```

(g) PR 2's new `/wall-setup` page.

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
(cd "$D" && bash scripts/ui/run.sh shoot.py --routes /wall-setup --fixtures populated) \
  > "$S/shoot-final-wall-setup.txt" 2>&1; grep -E '^[0-9]+ shots' "$S/shoot-final-wall-setup.txt"
```

(h) The comparisons (quick). First make sure each of the seven output files above has its summary line
(`N failed` last in a flows file; `12 shots, N failures` in a shoot file, followed by one `FAIL` line
per failure): a run the tool killed has none, and must be run again.

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
echo "flows base: $(tail -n 1 "$S/flows-base.txt")  PR 2: $(tail -n 1 "$S/flows-final.txt")"
diff <(grep -E '^(PASS|FAIL) ' "$S/flows-base.txt" | sed 's/ :: .*//') \
     <(grep -E '^(PASS|FAIL) ' "$S/flows-final.txt" | sed 's/ :: .*//') && echo "flows unchanged"
for r in pair walls; do
  echo "/$r base: $(grep -E '^[0-9]+ shots' "$S/shoot-base-$r.txt")  PR 2: $(grep -E '^[0-9]+ shots' "$S/shoot-final-$r.txt")"
  diff <(grep '^FAIL' "$S/shoot-base-$r.txt") <(grep '^FAIL' "$S/shoot-final-$r.txt") && echo "/$r: shots unchanged"
done
echo "/wall-setup PR 2: $(grep -E '^[0-9]+ shots' "$S/shoot-final-wall-setup.txt")"
```

Expected:
- **(a), (b):** the same `N failed` line for the base and for PR 2 (the exit code is 1 when N is not 0;
  the comparison is what counts).
- **(c)-(g):** each prints a `12 shots, N failures` line.
- **(h):** the two `N failed` lines again, then `flows unchanged`; for `/pair` (the `/pair?code=ABCD23`
  route) and `/walls`, two `12 shots, N failures` summaries with the same N, then `/pair: shots
  unchanged` and `/walls: shots unchanged`; and `/wall-setup PR 2: 12 shots, 0 failures`.

If `diff` prints a line, a PR 2 hook changed an existing page. Find the task whose hook it is, fix it
there (a new commit), and start this task again from Step 1. `$S/base-ui` is a scratch copy; leave it or
delete it.

- [ ] **Step 6: Run the phone end to end on a fresh local merge with PR 1**

Four commands, each its own Bash call: the checks, the merge, the run (in the background), then the
image check. First the checks:

```bash
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
docker ps -a --format '{{.Names}}  created {{.CreatedAt}}  {{.Status}}' \
  | grep -E '^(e2e-wall-(api|db|redis|player|app|ffmpeg)|e2e-wall-app-check) ' && echo "BUSY: e2e-wall containers exist" || echo "no e2e-wall containers"
docker ps --no-trunc --format '{{.Names}} {{.Command}}' | grep -F e2e_wall.py \
  && echo "BUSY: an e2e_wall.py runner is alive" || echo "no e2e_wall.py runner"
(ss -ltn | grep -E ':(18020|13022|13023|13033)\b') && echo "PORT BUSY: find out who holds it" || echo "ports free"
[ -e "$M" ] && echo "$M EXISTS: a run of this plan left it; remove it as below (Then remove the merge) first" || echo "no merge worktree"
```

Expected: `no e2e-wall containers`, `no e2e_wall.py runner`, `ports free` and `no merge worktree`. On
a `BUSY` line, wait: the safety rules say when a leftover is yours. Then the merge:

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
git -C "$WT" worktree add --detach "$M" feature/phone-wall-setup
git -C "$M" merge --no-ff --no-commit feature/phone-wall-setup-dashboard; echo "merge exit $?"
git -C "$M" diff --name-only --diff-filter=U
git -C "$M" diff --cached --quiet HEAD -- backend player scripts/e2e-scan-to-pair && echo "backend, player and harness are PR 1's"
```

Expected: `Automatic merge went well; stopped before committing as requested`, `merge exit 0`, no
conflict lines, and `backend, player and harness are PR 1's`. On a conflict, run
`git -C "$M" merge --abort`, remove the merge as below, and report the conflicting files to the user.

Then the run, with `run_in_background` (it takes about 5-6 minutes, close to the 600000 ms cap of a
foreground call), waiting for its `e2e exit` line:

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$WT" && FRONTEND_DIR="$M/frontend" scripts/e2e-scan-to-pair/run.sh --wall 2>&1 \
  | tee "$S/e2e-wall-phone-final.log" | tail -30; echo "e2e exit ${PIPESTATUS[0]}"
```

Expected: about 5-6 minutes, then `e2e exit 0`. The tail shows the last of Task 9's lines (`c.` to
`a.`), then `P1.` to `P9.`, `timings: {...}`, `E2E WALL PHONE OK` and `E2E WALL OK`. The full log also
has Task 9's lines from `1.`. In the `timings:` line, `phone_move_max_s` and each
`phone_tv<n>_done_to_outline_off_s` are under 1 and `phone_sync_median_ms` is under 100 (the run fails
otherwise). If the tool killed the run, clear your own leftovers (safety rules) and run it again.

Before removing the merge, check the dashboard image that run built from it:

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
docker run --rm --entrypoint sh signage_frontend:e2e-wall -c \
  'cd /usr/share/nginx/html && ls wall-setup.js wall-geometry.js wall-setup.css && { [ ! -e tests ] && echo "no tests/ in the image"; }' \
  2>&1 | tee "$S/image-check.log"
docker run -d --rm --name e2e-wall-app-check -p 127.0.0.1:13033:80 -e API_BASE_URL=http://127.0.0.1:18020 \
  -e PLAYER_BASE_URL=http://127.0.0.1:13022 signage_frontend:e2e-wall > /dev/null
for _ in $(seq 1 20); do curl -fs -o /dev/null http://127.0.0.1:13033/ && break; sleep 0.5; done
curl -fsS http://127.0.0.1:13033/ | grep -oE 'href="/wall-setup\.css\?v=[0-9a-f]{10}"' | tee -a "$S/image-check.log"
curl -fsS -D - -o /dev/null http://127.0.0.1:13033/ | tr -d '\r' | grep -i '^content-security-policy' \
  | grep -oE "(script|media)-src [^;]*" | tee -a "$S/image-check.log"
docker stop e2e-wall-app-check > /dev/null
```

`docker run --name e2e-wall-app-check` refuses to start if a container of that name exists; then it is
not this run's: leave it and ask the user (R23).

Expected (all in `$S/image-check.log`):
- **Files:** `wall-setup.js`, `wall-geometry.js` and `wall-setup.css` are listed, then
  `no tests/ in the image` (`frontend/.dockerignore` works).
- **Stylesheet:** `href="/wall-setup.css?v=<10 hex>"`, so the entrypoint stamps the new stylesheet
  against stale caches.
- **CSP:** `script-src 'self'` and `media-src 'self' http://127.0.0.1:18020`, with no `blob:`.

Then remove the merge:

```bash
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
M=/home/ahmed/signage/.claude/worktrees/wall-e2e-merge
git -C "$M" merge --abort
git -C "$WT" worktree remove "$M"
git -C "$WT" worktree prune
git -C "$WT" worktree list | grep -F wall-e2e-merge || echo "merge worktree gone"
docker ps -a --format '{{.Names}}' | grep -E '^(e2e-wall-(api|db|redis|player|app|ffmpeg)|e2e-wall-app-check)$' \
  || echo "no test containers left"
```

Expected: `merge worktree gone` and `no test containers left`.

If the end to end fails, read `$S/e2e-wall-phone-final.log`, the `shots/wall-phone-fail-*.png` files in
`$WT/scripts/e2e-scan-to-pair/shots/`, and the API log tail. Remove the merge as above, fix the code in
the task that owns it, and start this task again from Step 1.

- [ ] **Step 7: Review the diff against the PR base**

```bash
cd /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
git diff --stat feature/friendly-dashboard...HEAD | tail -1
git diff --name-only feature/friendly-dashboard...HEAD
git diff --name-only feature/friendly-dashboard...HEAD \
  | grep -vE '^(frontend/|scripts/ui/wall_setup_flows\.py$|scripts/check_ui_contract\.sh$)' \
  || echo "only frontend/, wall_setup_flows.py and check_ui_contract.sh changed"
git diff --quiet feature/friendly-dashboard...HEAD -- scripts/ui/common.py scripts/ui/mock_api.py scripts/ui/flows.py \
  scripts/ui/shoot.py scripts/ui/run.sh frontend/nginx.conf frontend/docker-entrypoint.sh frontend/Dockerfile \
  frontend/router.js && echo "redesign harness, CSP, image and router untouched"
git diff feature/friendly-dashboard...HEAD -- frontend | grep -nE '^\+.*(\son[a-z]+="|<script>|eval\(|new Function|blob:)' \
  || echo "no inline handlers, inline scripts, eval or blob: added"
grep -nE '^(const|let|var|function|async function|class) ' frontend/wall-setup.js frontend/wall-geometry.js
grep -c 'href="/wall-setup.css"' frontend/index.html
git diff --name-only feature/friendly-dashboard...HEAD | grep -E '(^|/)(out|__pycache__|node_modules)/|\.pyc$' \
  && echo "STRAY FILES" || echo "no stray files"
git diff feature/friendly-dashboard...HEAD | grep -nE '^\+.*(TODO|FIXME|XXX|console\.log\(|debugger)' \
  || echo "no TODO/debug lines added"
```

Expected:
- **Changed files:** `frontend/.dockerignore`, `frontend/app.js`, `frontend/i18n/ar.json`,
  `frontend/i18n/en.json`, `frontend/index.html`, `frontend/tests/wall-geometry.test.js`,
  `frontend/wall-geometry.js`, `frontend/wall-setup.css`,
  `frontend/wall-setup.js`, `scripts/check_ui_contract.sh` and `scripts/ui/wall_setup_flows.py`.
- **Scope checks:** `only frontend/, wall_setup_flows.py and check_ui_contract.sh changed`,
  `redesign harness, CSP, image and router untouched`, and
  `no inline handlers, inline scripts, eval or blob: added`.
- **Top-level names:** exactly two lines, `frontend/wall-setup.js:…:const WallSetup = (() => {` and
  `frontend/wall-geometry.js:…:const WallGeometry = (() => {`. Every other name lives inside those
  IIFEs, because classic scripts share one global scope with app.js.
- **Stylesheet link:** `1`.
- **Leftovers:** `no stray files` and `no TODO/debug lines added`.

Then read the whole `git diff feature/friendly-dashboard...HEAD` once, from top to bottom. Look for
leftovers, commented-out code, hooks larger than Task 11's Step 0 message promised the redesign session,
and anything that does not match its task.

- [ ] **Step 8: Find out what is on GitHub already**

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
timeout 30 git -C "$D" ls-remote --heads origin feature/friendly-dashboard feature/phone-wall-setup feature/phone-wall-setup-dashboard
git -C "$D" rev-parse feature/friendly-dashboard > "$S/base-sha.txt"; git -C "$D" rev-parse --short feature/friendly-dashboard
git -C "$WT" rev-parse --abbrev-ref 'feature/phone-wall-setup@{upstream}' 2>/dev/null || echo "PR 1's branch has no upstream (not pushed)"
git -C "$WT" log --oneline 'feature/phone-wall-setup@{upstream}..feature/phone-wall-setup' 2>/dev/null
~/.local/bin/gh pr list --head feature/phone-wall-setup --state all --json number,url,state
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep '^signage_frontend:'   # read only
```

Write down these five facts for Steps 9 and 10:
- **(a)** Is `feature/friendly-dashboard` on origin? At planning time it was not: `ls-remote` listed
  only `fix/player-cache-busting`. GitHub cannot open a PR against a base it does not have.
- **(b)** The base's short SHA (the full one is saved in `$S/base-sha.txt` for Step 11).
- **(c)** Is PR 1's branch pushed? Does it have unpushed commits? After Task 16 it has at least Task
  16's commit (R21: that commit lands after Task 10's hand-off and is pushed only on this task's yes).
- **(d)** PR 1's URL, if Task 10 opened it.
- **(e)** What production's dashboard was built from (read only): in the `docker images` lines, the
  `dashboard-<sha>` tag with the same ID as `signage_frontend:latest` (9e50b4b at planning time). Then
  `git -C "$D" rev-list --count <sha>..feature/friendly-dashboard` (7 at planning time) counts the
  redesign commits that deploying PR 2 would also ship.

If `ls-remote` times out (this host's DNS is unreliable), run it again once, then report it as unknown
and treat (a) as no: pushing a branch that is already there at the same SHA changes nothing.

- [ ] **Step 9: Write the PR description and fill in the measured values**

````bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cat > "$S/pr2-body.md" <<'EOF'
## Summary

The phone half of setting up a video wall while standing in front of the TVs. The phone's camera opens a TV's pairing QR (`/pair?code=`), and the person chooses **Part of a video wall**. They start a new wall, or add the TV to a wall this phone used in the last 30 minutes: then "Part of a video wall" and that wall are already chosen, so the next TV takes two taps (Continue, Add this TV). A new wall's name is prefilled, and its video comes from the library or is uploaded from the phone, with progress. The TV's code is checked before anything is uploaded or claimed.

Then they place the TV. Its box sits over the muted, looping video; one finger drags it, a pinch or the slider resizes it, and its edges snap to the other boxes and to the video's edges. The TV shows what is in its box, following the finger, and outlines itself as "TV n" while it is being placed. **Done** ("TV n placed. Point your camera at the next TV's QR code."), then scan the next TV; **Finish** opens the wall in Video walls. **Adjust TVs** on a phone-made wall in the Walls page opens the same editor later.

**Depends on PR 1** ([[PASTE: PR 1's URL from Step 8 (d), or "branch feature/phone-wall-setup, not opened yet"]]), the backend and player half:
- This PR calls PR 1's API:
  - `POST /walls/free`
  - `POST /walls/{id}/tvs`
  - `PUT /walls/{id}/cells/{row}/{col}/placement`
  - `serialize_wall`'s `layout`, `online` and `video` fields
  - the new `pair.*` and `wall.*` error codes
- The TVs follow the phone through PR 1's player.
- Against a backend without PR 1, the new steps cannot create or join a wall and say so. The rest of the dashboard works as before.

Base: `feature/friendly-dashboard`, the redesign. Production's dashboard is [[PASTE: the commit from Step 8 (e)]], so this branch also carries [[PASTE: the count from Step 8 (e)]] redesign commits that are not deployed yet.
- The hooks in the files the redesign owns (`index.html`, `app.js`, `i18n/*.json`, `check_ui_contract.sh`) were agreed with the redesign session before this branch was cut.
- Everything else is in new files.

Spec: `docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md` (on PR 1's branch), Section 5.

## What's in it

- **`frontend/wall-geometry.js`:** the box arithmetic. It snaps edges within 3%, keeps 5% of a box on the video, and resizes while keeping the TV's shape. Node tests are in `frontend/tests/`, kept out of the image by `frontend/.dockerignore`.
- **`frontend/wall-setup.js` and `wall-setup.css`:** the `/wall-setup?wall=<id>` page.
  - It is a full-page view like `/pair`, routed in `boot()` and `routeToCurrentPath()`.
  - Every other view hides it, and sign-out or a 401 resets it.
  - All its code is in one IIFE (`WallSetup`), so nothing collides with app.js's globals.
- **`/pair`:**
  - A "What is this TV?" step: Its own screen (unchanged) or Part of a video wall, preselected when this phone used a wall in the last 30 minutes (`WallSetup.recentWallId()`).
  - The wall steps: Which wall? (phone-made walls, newest first, with the remembered one preselected) and New video wall. Before an upload or a claim they check the TV's code (`GET /screens/poll/{code}`) and say why it cannot be used.
  - Back from the editor returns to the `/pair` form: the steps add one history entry, which the editor's address replaces.
  - The upload goes by XHR, with progress and with the session's 401 and subscription handling.
- **The placement editor:**
  - One-finger drag, pinch and slider, with snapping.
  - One save in flight, so the newest box always lands.
  - A 3 s re-read for TV sizes, online dots and the transcoded video.
  - An LTR island in Arabic; labels follow the UI language.
  - Every box it draws or sends is in its TV's shape once that TV has reported its screen, the box the server stores.
  - Done ("TV n placed. Point your camera at the next TV's QR code." for the TV just added from `/pair`, "TV n placed." when adjusting), Finish (Video walls, with this wall's editor open), and Remove TV. After the last TV is removed the wall stays, and the page says how to add one.
- **Walls page**, for phone-made walls only: **Adjust TVs** on their cards and in their canvas editors, the card line "Set up from a phone · n TVs", and a canvas editor that shows the video's name without the item duration, Fit/Fill/Stretch, Delete item, Add item, the even bezel grid or "Switch to mirrored". Grid walls are unchanged.
- **Strings:** English and Arabic (`wallsetup.*`, plus `error.pair.*` and `error.wall.*` for the new codes).
- **Checks:** `check_ui_contract.sh` now scans `wall-setup.js`. `scripts/ui/wall_setup_flows.py` holds the Playwright flows against the redesign's mock API and CSP mirror; it changes neither `common.py`, `mock_api.py` nor `flows.py`.

## Test evidence

- `node --test frontend/tests/`: [[PASTE: the "# pass" and "# fail" lines from node-tests.log]]
- `scripts/ui/run.sh wall_setup_flows.py` (Tasks 12-15): [[PASTE: the "flows: N  PASS: N  FAIL: 0" line and the "0 failed" line from ui-wall-setup.log]]
- The redesign's flows (`scripts/ui/run.sh flows.py`), against `feature/friendly-dashboard` as it is now: [[PASTE: the base's and this branch's "N failed" lines, and "flows unchanged"]]
- Layout matrix (`shoot.py`, 6 devices x en/ar): [[PASTE: the summary lines for /pair?code=ABCD23 and /walls with "shots unchanged", and the /wall-setup summary line]]
- `check_i18n.py` and `check_ui_contract.sh`: [[PASTE: the two OK lines from guards.log]]
- End to end:
  - The setup: a local, unpushed merge of this branch with PR 1, run as `FRONTEND_DIR=<merge>/frontend scripts/e2e-scan-to-pair/run.sh --wall`.
  - PR 1's backend runs with 4 workers. Three player TVs have clocks shifted -3 s / 0 / +2 s, and one is 960x540 at DPR 2. The phone is a Pixel 7, and a second phone is in Arabic.
  - Nothing reaches khanshoof.com or production's loopback ports.
  - Bounds: finger stopped to TV showing the box, and Done to the TV's outline going, each under 1 s; the TVs' currentTime spread under 100 ms (median).
  - Results: [[PASTE: the lines from "P1." to "E2E WALL OK" from e2e-wall-phone-final.log, including the "timings:" line]]
- The image: [[PASTE: the lines of image-check.log]]

## Deploy (not done; needs the owner's OK)

Order: backend (PR 1, additive migration), then the player (PR 1), then this dashboard, each with a rollback tag as in earlier deploys. The dashboard goes last because its new steps call PR 1's endpoints.
- Deploying this branch also deploys those redesign commits. Agree on it with the redesign session first, or deploy after its own next deploy.
- Build the dashboard image from a `git archive` of this branch (or of the redesign branch once this is merged into it). Never run `docker-compose up --build` or `redeploy.sh` from `/home/ahmed/signage`: that checkout has no redesign, and building there reverts the dashboard and the player.
- Rollback: retag the previous `signage_frontend` image and recreate only the frontend.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
````

Now replace every `[[PASTE: …]]` with the real output from the named log in `$S`. Paste the lines as
printed: never round, estimate or invent a number. If a value is missing because a step could not run,
write what happened in its place.

Then check each bullet of "What's in it" against `git -C "$D" log --format='%s%n%b'
feature/friendly-dashboard..HEAD` and the diff from Step 7. Delete or correct any bullet the branch does
not do (each task's review may have changed it after the plan was written). Then check:

```bash
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
grep -c 'PASTE' "$S/pr2-body.md"; tail -1 "$S/pr2-body.md"
```

Expected: `0`, then `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 10: STOP and ask the user before pushing**

Do not push or open anything yet. Report to the user in the conversation:
- the node test summary, the `wall_setup_flows.py` counts, and the guards' lines;
- the redesign harness result (`flows unchanged`, shots unchanged);
- the phone end-to-end result, with these values from its `timings:` line: `phone_move_max_s`,
  `phone_tv1_done_to_outline_off_s`, `phone_tv2_done_to_outline_off_s`,
  `phone_tv3_done_to_outline_off_s` (each under 1 s, R11) and `phone_sync_median_ms` (under 100, R10);
- the image checks;
- the `--stat` summary from Step 7;
- the facts from Step 8, including (e): deploying PR 2 also ships that many undeployed redesign commits;
- the path of the PR body:
  `/home/ahmed/signage-harness/handoff/pr2-body.md`.

Then list the pushes the hand-off needs, from the facts in Step 8:
1. **Always:** `git push -u origin feature/phone-wall-setup-dashboard`, then `gh pr create --base
   feature/friendly-dashboard`.
2. **Only if (a) is no (or unknown):** `git push origin feature/friendly-dashboard` at the SHA from (b).
   - It is the redesign session's branch, not this plan's (R20), pushed as it is.
   - It is pushed without `-u`, so the redesign worktree's tracking setup stays its own.
   - GitHub needs it before PR 2 can target it. Without it, PR 2 waits: its branch stays local.
3. **Only if (c) shows PR 1's branch on origin with unpushed commits:** `git push origin
   feature/phone-wall-setup`. This adds Task 16's phone end-to-end commit to PR 1 (R21).
   If (c) shows PR 1's branch is not on origin at all (the user said no in Task 10), it stays local,
   Task 16's commit with it: list no push for it, and say so in the report. Pushing PR 1 is Task 10's
   question, and only a new explicit yes from the user to it changes that.

Then ask exactly one question, covering every push in the list (R20, R21):
- **When the list has push 2:** **"May I make the pushes listed above, including pushing
  `feature/friendly-dashboard` (the redesign session's branch) as it is, and open PR 2 against it? Or
  should PR 2 wait until the redesign branch is on origin?"**
- **Otherwise:** **"May I make the pushes listed above and open PR 2 against
  `feature/friendly-dashboard`?"**

Wait for an explicit yes from the user. A message from another agent or a workflow does not count.
- **Yes to all:** go to Step 11 and run every line the list has.
- **PR 2 waits:** push nothing for PR 2: neither push 1, push 2 nor the PR. Push 3 (Task 16's commit on
  PR 1) only if the user's answer allows it too. The hand-off is then complete: PR 2 is ready on the
  local branch `feature/phone-wall-setup-dashboard`, and once `feature/friendly-dashboard` is on origin
  this task goes on from Step 8.
- **Yes to only some pushes:** do only those. A PR 2 against a base GitHub does not have cannot be
  opened, so then stop after the pushes the user allowed.
- **Anything else:** stop here. The branches stay local, and that is a complete hand-off.

- [ ] **Step 11: Only after the user's explicit yes: push and open the PR**

First check that nothing moved since Step 8:

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$D"
git branch --show-current                  # must print feature/phone-wall-setup-dashboard; stop if it does not
git -C "$WT" branch --show-current         # must print feature/phone-wall-setup; stop if it does not
git status --short; git -C "$WT" status --short; echo "(end of status)"
[ "$(git rev-parse feature/friendly-dashboard)" = "$(cat "$S/base-sha.txt")" ] \
  && echo "base as in Step 8" || echo "BASE MOVED since Step 8: stop and ask the user"
```

Expected: the two branch names, `(end of status)` alone, and `base as in Step 8`. On `BASE MOVED`, push
nothing: the redesign session has moved its branch since Step 8, so the user's yes was about another
commit. Tell the user, and start this task again from Step 1.

Then run only the lines the user's yes covered, in this order:

```bash
D=/home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
WT=/home/ahmed/signage/.claude/worktrees/wall-setup
S=/home/ahmed/signage-harness/handoff; mkdir -p "$S"
cd "$D"
git push origin feature/friendly-dashboard            # only if the list had push 2 and the user allowed it
git -C "$WT" push origin feature/phone-wall-setup     # only if the list had push 3 and the user allowed it
git push -u origin feature/phone-wall-setup-dashboard # push 1
~/.local/bin/gh pr create --base feature/friendly-dashboard --head feature/phone-wall-setup-dashboard \
  --title "feat(walls): set up a video wall from a phone (dashboard)" \
  --body-file "$S/pr2-body.md"
```

Expected: each push prints its ref update, and `gh` prints the new PR's URL; give that URL to the user.
Do not merge, deploy, retag images or touch production containers. Each of those needs its own explicit
OK. The deploy order (backend, then player, then dashboard) and the build-from-archive rule are in the
PR's Deploy section.

- [ ] **Step 12: Commit**

No commit: this task changes no file in either repository. If a check failed and you fixed it, that fix
belongs in its own commit under the task that owns the code. Then start this task again from Step 1.

---

## Appendix — interface contract

The tasks use exactly these names, signatures, frame shapes, codes, files, ports and container names.
Where a task and this appendix differ, the task and rulings R1–R23 win.

Spec: /home/ahmed/signage/.claude/worktrees/wall-setup/docs/superpowers/specs/2026-11-01-phone-wall-setup-design.md
Recon notes from planning (verified facts, line numbers, gotchas; nothing in the tasks needs them, and
they live under /tmp, so they may be gone):
/tmp/claude-1000/-home-ahmed-signage/39cbf124-cc26-4c98-9af8-19a9e47924c9/scratchpad/recon/{backend-walls,backend-tests,media,player,dashboard,e2e}.md

#### Plan-level decisions (refinements of the spec; Task 1 amends the spec text to match)

D1. Frames carry `tv_number` (int, col_index + 1), not an English `label`. The player translates
    `wall.tv_label` = "TV {n}" / "شاشة {n}".
D2. Standalone playlist defaults do not change: `_default_duration_seconds` keeps returning 10 for
    videos even though `media.duration_seconds` now exists (remove the forward-hook read; comment why).
D3. After `transcode_video` finishes, `_refresh_free_walls_for_media(media_id)` sets each free wall
    that plays that media to the transcoded size (canvas_width_px/height_px) and the item to the
    probed length (`duration_seconds = max(1, ceil(d))`). This is how "canvas = size after
    transcoding" holds.
D4. The Redis channel is `walls.geometry_channel()` = `"khan:wall-geometry:db<N>"`, where N is the
    logical DB number in `ratelimit.REDIS_URL` (default 0). Tests use their own Redis DB number, so
    they never publish to production's channel (pub/sub ignores logical DBs; the test script points
    at production's Redis server).
D5. `/screens/poll/{code}` limit 30/minute -> 240/minute and `/screens/request_code` 10/minute ->
    30/minute: up to 8 TVs behind one venue NAT poll every 3 s while waiting to be scanned.
D6. `serialize_wall` adds per cell `online` (bool, `is_online(screens.last_seen)`, 90 s rule) and,
    for free walls only, `video` = {media_id, name, url, mime_type, width_px, height_px} or null.
D7. Ghost cells: before choosing an index, `POST /walls/{id}/tvs` deletes the wall's cells whose
    `screen_id IS NULL` (left behind when a screen is deleted from the Screens page).
D8. A free wall's canvas playlist holds exactly one item (the video). `PUT /walls/{id}/video`
    deletes and re-inserts it (new item id), so TVs switch at once.
D9. E2E runs in "loopback mode": TVs load http://127.0.0.1:<port> from a player container whose
    API_BASE_URL is the local API, because Playwright routing never intercepts WebSockets (a routed
    https://play.khanshoof.com TV would open its wall socket to PRODUCTION).

#### Branches and worktrees

- PR 1 (backend, player, harness): branch `feature/phone-wall-setup`, worktree
  /home/ahmed/signage/.claude/worktrees/wall-setup (already exists, HEAD ca74b28 = the spec commit on
  top of fix/player-cache-busting cb2f0a4). PR base: `fix/player-cache-busting`.
- PR 2 (dashboard): branch `feature/phone-wall-setup-dashboard`, cut from `feature/friendly-dashboard`
  (24101d4 at planning time), worktree /home/ahmed/signage/.claude/worktrees/wall-setup-dashboard
  (created by Task 11). PR base: `feature/friendly-dashboard`. GATED: before Task 11 starts, the
  redesign session must have agreed the hooks (its Tasks 8, 9, 11 rewrite #pair-view, the Walls
  editor and check_ui_contract.sh). Ask the user to relay; do not edit the redesign worktree.
- The old dashboard in the wall-setup worktree's frontend/ is NEVER edited.

#### Global constraints (as in the header)

- Player code stays Chrome 66-safe. JS: no `?.`, `??`, numeric separators (`1_000`), object
  spread in literals, `flatMap`, `.flat()`, `globalThis`, `Object.fromEntries`, `replaceAll`,
  `.at()`, `Promise.allSettled`. CSS: no `inset`, flex `gap`, inline `min()`/`max()` (px floors
  only inside the existing `@supports (width: max(1px, 2px))` block), no `:is()`/`:where()`,
  no `aspect-ratio`, no standalone `scale:`/`translate:` properties; never a rule that names only
  `:focus-visible`. Use the `transform` property only.
- The player gets no new shell files (a new file would need sw.js ASSETS + entrypoint shell_hash).
- Never `docker kill` / `docker rm` by image or ancestor filter: `signage_backend:latest` is
  production's image. Never retag `:latest`. Build test images as `signage_backend:e2e`,
  `signage_player:e2e-wall`, `signage_frontend:e2e-wall`.
- Backend tests run ONLY through:
  `KHAN_TEST_DB=sawwii_test_wallsetup KHAN_TEST_REDIS_DB=5 bash scripts/test-backend-in-docker.sh <args>`
  (own DB + own Redis DB; never the shared default). Paths are relative to backend/ (e.g.
  `tests/test_walls_free.py`). Before a full-suite run, check no other session's pytest container
  is running on the same DB.
- Directly inserted test orgs set `subscription_status='active'` and an explicit `screen_limit`;
  assert errors by `res.json()["detail"]["code"]`.
- No pytest-asyncio, fakeredis or freezegun: use `asyncio.run(...)` in sync tests, FakeWS objects,
  and `monkeypatch.setattr(walls_mod, "now_ms", ...)`.
- TestClient `receive_json()` has no timeout: never wait on a frame that may not come; use the
  `_recv_until(ws, type_, timeout=2.0)` helper defined in Task 6 (reads `ws._send_queue.get(timeout=...)`)
  or FakeWS.
- walls.py must never import main (main imports walls at module top). main calls walls functions
  as `import walls as walls_mod` at call time or the existing lazy `from walls import ...`.
- `generate_unique_token` stays a main.py module global (tests monkeypatch it).
- Error details from new endpoints use `http_error(status, code, message)`.
- The dashboard keeps CSP `script-src 'self'` (no inline handlers), and its `media-src` has no
  `blob:`: no local preview of a picked file before upload.
- Every dashboard JS file wraps its code in one IIFE-returned const (top-level names collide
  across classic scripts): `const WallGeometry = ...`, `const WallSetup = (() => { ... })();`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nothing is pushed, merged or deployed without the user's explicit OK.

#### Database (Task 1, backend/db.py, next to the existing ADD COLUMN block ~ lines 475-487)

```sql
ALTER TABLE walls      ADD COLUMN IF NOT EXISTS layout TEXT NOT NULL DEFAULT 'grid' CHECK (layout IN ('grid','free'));
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_x REAL;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_y REAL;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_w REAL;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS place_h REAL;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS screen_w_px INTEGER;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS screen_h_px INTEGER;
ALTER TABLE wall_cells ADD COLUMN IF NOT EXISTS highlight_until_ms BIGINT;
ALTER TABLE media      ADD COLUMN IF NOT EXISTS duration_seconds REAL;
ALTER TABLE media      ADD COLUMN IF NOT EXISTS width_px INTEGER;
ALTER TABLE media      ADD COLUMN IF NOT EXISTS height_px INTEGER;
```

#### backend/media_probe.py (Task 2; new module, no DB access)

```python
PROBE_TIMEOUT_SECONDS = 20
VIDEO_LONG_SIDE_MAX = 3840
TRANSCODE_SCALE_FILTER = "scale='if(gt(iw,ih),min(3840,trunc(iw/2)*2),-2)':'if(gt(iw,ih),-2,min(3840,trunc(ih/2)*2))'"
def probe_media(path: str, mime: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict
    # -> {"width_px": int|None, "height_px": int|None, "duration_seconds": float|None}; never raises
```
main.py: `upload_media` calls `await anyio.to_thread.run_sync(media_probe.probe_media, file_path, content_type)`
before the INSERT and stores the three columns; `transcode_video` adds `"-vf", media_probe.TRANSCODE_SCALE_FILTER`
right after `"-i", input_path`, re-probes the output, updates the row with COALESCE, then (from
Task 5 on) calls `_refresh_free_walls_for_media(media_id)`. Decide probe/transcode with
`_media_class(content_type) == "video"` (case-insensitive) rather than `startswith("video")`.

#### backend/wall_layout.py (Task 3; pure functions, no DB)

```python
DEFAULT_TV_ASPECT = 16 / 9
MIN_OVERLAP = 0.05            # fraction of the box's own area that must lie on the canvas
MAX_TVS = 8
FALLBACK_CANVAS = (3840, 2160)
MAX_BOX_SIDE = 4.0            # w and h are at most 4 canvas widths / heights
def canvas_size_for(width_px, height_px) -> tuple[int, int]       # media size, or FALLBACK_CANVAS
def first_box(canvas_w: int, canvas_h: int, tv_aspect: float = DEFAULT_TV_ASPECT) -> dict
    # {"x": 0.0 (or (1 - w) / 2 when w > 1, ruling R1), "y": 0.0, "w": tv_aspect * canvas_h / canvas_w, "h": 1.0}
    # (w and h shrunk together, keeping the shape, when w would exceed MAX_BOX_SIDE = 4)
def next_box(prev: dict, canvas_w: int, canvas_h: int) -> dict
    # ruling R3: prev's height in the default 16:9 pixel shape, x = prev.x + prev.w, y = prev.y, then nudge_into_canvas
def overlap_fraction(box: dict) -> float  # area(box ∩ [0,1]^2) / (w*h)
def nudge_into_canvas(box: dict) -> dict  # if overlap < MIN_OVERLAP: move x (and y if no vertical overlap) toward the canvas until overlap == MIN_OVERLAP
def validate_box(box: dict) -> bool       # finite numbers, 0 < w <= 4, 0 < h <= 4, overlap_fraction >= MIN_OVERLAP
def box_to_px(box: dict, canvas_w: int, canvas_h: int) -> dict   # {"x","y","w","h"} floats in canvas px
def fit_box_to_screen(box: dict, screen_w: int, screen_h: int, canvas_w: int, canvas_h: int) -> dict
    # same w and centre; h so the box's pixel shape equals screen_w/screen_h; shrunk around the
    # centre when h would pass MAX_BOX_SIDE, then nudge_into_canvas
def has_default_shape(box: dict, canvas_w: int, canvas_h: int) -> bool   # pixel aspect within 1% of 16/9
def lowest_free_index(used) -> int | None   # lowest of 0..MAX_TVS-1 not in used; None when full
```

#### main.py (Tasks 4, 5, 7)

Helpers (Task 4):
```python
def _tv_code_row_or_raise(code: str, now: datetime) -> dict
    # SELECT * FROM pairing_codes WHERE code = ?; missing -> 404 "pair.code_unknown";
    # status != 'pending' -> 409 "pair.code_used"; now > expires_at -> 400 "pair.code_expired"
def _take_tv_code(row_id: int, now: datetime) -> None
    # inside a transaction: UPDATE pairing_codes SET status='paired', claimed_at=now.isoformat()
    #   WHERE id=? AND status='pending'; 0 rows -> 409 "pair.code_used"
def _lock_org(oid: int) -> dict | None   # SELECT id, screen_limit FROM organizations WHERE id=? FOR UPDATE
def _bind_screen_to_cell(wall: dict, cell: dict, org: dict | None, now: datetime, name: str) -> tuple[int, str]
    # body extracted from redeem_wall_cell (reuse/cleanup/limit/insert/link); returns (screen_id, screen_token);
    # precondition: inside transaction(), org row already locked
def _attach_tv_code(row_id: int, screen_id: int) -> None   # UPDATE pairing_codes SET screen_id=? WHERE id=?
```
redeem_wall_cell is refactored to use `_lock_org` + `_bind_screen_to_cell` with no behaviour change.

Models + endpoints (Tasks 5, 7):
```python
class FreeWallCreate(BaseModel): name: str (1..120); media_id: int; code: str (len 6)
class WallTvAdd(BaseModel): code: str (len 6)
class WallPlacement(BaseModel): x: float; y: float; w: float; h: float; highlight: bool = True   # plain floats (R2): wall_layout.validate_box refuses w or h outside (0, 4]
class WallVideoUpdate(BaseModel): media_id: int

POST /walls/free                                  create_free_wall      -> 201 serialize_wall(...), @limiter.limit("30/minute") (R4)
POST /walls/{wall_id}/tvs                         add_wall_tv           -> 201 the new cell row (+ "online"), @limiter.limit("30/minute") (R4)
PUT  /walls/{wall_id}/cells/{row}/{col}/placement put_wall_placement    -> 204, @limiter.limit("1800/minute")
PUT  /walls/{wall_id}/video                       put_wall_video        -> 200 serialize_wall(...)
```
All: `user: dict = Depends(require_roles("admin", "editor"))`, `_sub: dict = Depends(require_active_subscription)`,
`request: Request` where audited/limited. Lock order inside `with transaction():` is always
code claim -> `_lock_org` -> `SELECT ... FROM walls WHERE id=? FOR UPDATE` -> cells.
Other helpers:
```python
def _free_wall_or_404(wall_id: int, oid: int) -> dict   # 404 "wall.not_found"; layout != 'free' -> 409 "wall.not_free"
def _free_wall_media_or_raise(media_id: int, oid: int) -> dict   # 404 "media.not_found"; not image/ or video/ -> 400 "wall.media_type_blocked"
def _free_wall_item_seconds(media: dict) -> int            # max(1, ceil(duration_seconds)) for video with a duration, else 10
def _refresh_free_walls_for_media(media_id: int) -> None  # D3
def _cell_online_map(wall_id: int) -> dict                 # {cell_id: bool}
```
Error codes (new): pair.code_unknown (404), pair.code_expired (400), pair.code_used (409), wall.not_free (409),
wall.full (409), wall.placement_invalid (422), wall.free_layout (409), wall.media_type_blocked (400).
Existing reused: plan_limit (402), wall.not_found (404), wall.cell_not_found (404), media.not_found (404).
Screen name for a free-wall TV: f"{wall['name']} · TV {col_index + 1}".
Highlight: `highlight_until_ms = walls_mod.now_ms() + walls_mod.HIGHLIGHT_SECONDS * 1000` when `highlight`
is true, NULL when false. Once the write has committed (R5), `walls_mod.publish_geometry(wall_id, row, col)`.
PATCH /walls/{id} on a free wall: changing any of mode, mirrored_mode, mirrored_playlist_id,
bezel_enabled, bezel_h_pct, bezel_v_pct, canvas_width_px, canvas_height_px to a DIFFERENT value
-> 409 "wall.free_layout"; renaming works.
DELETE /walls/{id}/cells/{r}/{c}/pairing on a free wall: one transaction (wall FOR UPDATE, delete
screen, delete cell row, cols = GREATEST(1, max(col_index)+1)), then broadcast_bye.

#### walls.py (Task 6)

```python
HIGHLIGHT_SECONDS = 30
GEOMETRY_CHANNEL_BASE = "khan:wall-geometry"
def geometry_channel() -> str                      # D4, read ratelimit.REDIS_URL at call time
def tv_number(cell: dict) -> int                   # cell["col_index"] + 1
def free_cell_geometry(wall: dict, cell: dict) -> dict | None   # px box via wall_layout.box_to_px; None if place_* missing
def _geometry_frame(wall: dict, cell: dict) -> dict
    # {"type": "geometry", "canvas": {"w","h"}, "cell_geometry": {"x","y","w","h"},
    #  "highlight_until_ms": int|None, "tv_number": int, "server_now_ms": int}
def _geom_sig(wall: dict, cell: dict) -> tuple    # (canvas_w, canvas_h, place_x, place_y, place_w, place_h, highlight_until_ms)
_geom_sent: Dict[object, tuple]                    # ws -> last sig sent (hello or geometry); popped in _unregister
def publish_geometry(wall_id: int, row: int, col: int) -> None   # sync; local fast path + Redis publish; never raises
async def _push_geometry(wall_id: int, row: int, col: int) -> None   # send to the local socket if held, de-duped
def _ensure_geometry_subscriber() -> None          # lazy, per event loop; started from wall_ws
async def _geometry_subscriber() -> None           # redis.asyncio, get_message(timeout=5.0), backoff 1..30 s
async def _on_screen_report(wall_id: int, key: tuple, ws, cell_id: int, msg: dict) -> None
```
`_hello_frame` free branch (when `wall.get("layout") == "free"` and the cell has a box): `canvas`,
`cell_geometry` from the box, `bezel {"h_pct": 0.0, "v_pct": 0.0}`, plus `highlight_until_ms` and
`tv_number`. `_hello_config` is NOT extended with place_*/highlight. `_tick_once` loads
`SELECT * FROM wall_cells WHERE wall_id = ?` once per pass, uses real cell rows for re-hello,
and for free walls sends a `geometry` frame when `_geom_sig` differs from `_geom_sent[ws]`.
Client message `{"type": "screen", "w": int, "h": int}`: clamp 100..10000, store
`screen_w_px/screen_h_px` if changed; if the box `has_default_shape` and the screen shape differs by
more than 1%, store `fit_box_to_screen(...)` and send `geometry`.

#### Player (Task 8; player/player.js, styles.css, index.html, i18n/*.json)

- index.html: `<div id="wall-highlight" class="wall-highlight hidden" aria-hidden="true"><span id="wall-highlight-label" class="wall-highlight-label"></span></div>`
  inside `#player`, after `#zones`, before `#empty-state`.
- New/changed functions: `applyWallTransform()`, `onWallGeometry(frame)`, `showWallHighlight(untilMs, n)`,
  `hideWallHighlight()`, `reportWallScreen()`, `noteServerTime(serverNowMs)`; `effectiveNowMs()` keeps its
  name and signature. Clock: samples of `server_now_ms - performance.now()` (monotonic), keep those from the
  last 10 min, at most 32, offset = max; `effectiveNowMs() = performance.now() + wallClockOffsetMs`.
- `.wall-canvas`: `top:0; left:0; transform-origin: 0 0;` and JS sets
  `transform = "scale(" + sx + "," + sy + ") translate(" + (-x) + "px," + (-y) + "px)"` with
  sx = viewportW / cell.w, sy = viewportH / cell.h (viewport = document.documentElement.clientWidth/clientHeight).
- hello compares structurally (spanned vs not), updates transform in place, remounts only when the
  item changes; `geometry` never remounts media.
- i18n key `wall.tv_label`: en "TV {n}", ar "شاشة {n}".

#### E2E harness (Tasks 9, 16; scripts/e2e-scan-to-pair/)

- `run.sh --wall` -> builds `signage_backend:e2e` from `$repo/backend`, `signage_player:e2e-wall`
  from `$repo/player`; (Task 16 also) `signage_frontend:e2e-wall` from `FRONTEND_DIR`.
- Container names `e2e-wall-api`, `e2e-wall-db`, `e2e-wall-redis`, `e2e-wall-player`, `e2e-wall-app`,
  `e2e-wall-ffmpeg`; network `e2e-wall-net`. Ports: API 127.0.0.1:18020, player 127.0.0.1:13022,
  dashboard 127.0.0.1:13023. `WEB_CONCURRENCY=4`, Postgres `-c max_connections=200`.
  ALLOWED_ORIGINS includes http://127.0.0.1:13022,http://127.0.0.1:13023.
- Test video `shots/wall-test.mp4`: 5760x1080, 20 s, red/green/blue bands with digits 1/2/3 and a
  sweeping white bar (verified command in recon/e2e.md section 4).
- Script `e2e_wall.py` (Task 9: API-driven checks; Task 16 adds `phone` scenarios driven through
  the dashboard). Shared helpers in it: `lan_context`, `touch_drag`, `sample_sync_spread_ms`,
  `dominant_color`, `dark_share`, `wait_eval(page, js, pred, timeout_s, what=..., interval_ms=100)`.

#### Dashboard (Tasks 11-15; worktree wall-setup-dashboard)

- `frontend/wall-geometry.js`: `const WallGeometry = (() => { ... })();` (+ `if (typeof module !== "undefined") module.exports = WallGeometry;`)
  exporting `SNAP = 0.03`, `MIN_OVERLAP = 0.05`, `overlapFraction(box)`, `pixelAspect(box, canvasW, canvasH)`,
  `boxForScreen(box, screenW, screenH, canvasW, canvasH)`, `moveBox(box, dx, dy)`, `scaleBox(box, factor)`,
  `snapBox(box, others, canvasW, canvasH)`, `clampToCanvas(box)`, `sameBox(a, b)`.
  Node tests `frontend/tests/wall-geometry.test.js`, run with `node --test frontend/tests/` (host node 18).
- `frontend/wall-setup.js`: `const WallSetup = (() => { ... return { show, startFromPair, reset, recentWallId }; })();`
  `show(wallId)` opens the editor for an existing wall (/wall-setup?wall=<id>);
  `startFromPair(code)` starts the wall branch from /pair; `reset()` stops timers/loops/video;
  `recentWallId()` is the wall this phone set up or joined in the last 30 minutes, or null (R13).
- `frontend/wall-setup.css`; `<link rel="stylesheet" href="/wall-setup.css" />` (exact form, for cache stamping)
  after sections.css; `<script src="/wall-geometry.js"></script><script src="/wall-setup.js"></script>` after overview.js.
- index.html: `<section id="wall-setup-view" class="panel wall-setup-view hidden" aria-live="polite"></section>`
  between #pair-view and #billing-view; contents are built by wall-setup.js.
- i18n keys prefix `wallsetup.`; new `error.pair.code_unknown|code_expired|code_used`,
  `error.wall.full|not_free|placement_invalid|free_layout|media_type_blocked` in en.json and ar.json.
