# Multi-Screen Walls — Phase 1 (Mirrored) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship "mirrored walls" — let an admin link N physical screens at one venue into a single logical "wall" whose screens advance through their playlists in lockstep, sub-100ms drift on LAN. Two sub-modes: every cell shows the same playlist, OR every cell has its own playlist with synchronized item-advance ticks.

**Architecture:** Additive-only DB migrations (`walls`, `wall_cells`, `wall_pairing_codes`, plus three new columns on existing tables — `screens.wall_cell_id`, `organizations.walls_enabled`, `playlists.kind`). Backend gains a per-wall asyncio "tick loop" that owns the canonical playback timeline and broadcasts `play` frames over a new WebSocket endpoint `/walls/{wall_id}/ws`. Each player TV in a wall opens that WebSocket on boot and seeks its media to a server-anchored timestamp; HTTP polling stays as a 60s fallback. Standalone (non-wall) screens are untouched — their code path is unchanged.

**Tech Stack:** FastAPI 0.111 (native WebSocket), asyncio, Postgres 16, vanilla JS admin/player frontends, IBM Plex i18n (EN+AR), Cloudflare Tunnel (handles WS upgrade — no nginx tweak required).

**Spec:** `docs/superpowers/specs/2026-05-01-multi-screen-wall-sync-design.md`

---

## File Structure

**New files:**
- `backend/walls.py` — wall registry, asyncio tick loop, WebSocket connection management. Keeps wall logic out of the already-2200-line `main.py`.
- `backend/tests/test_walls_crud.py` — wall create/list/get/patch/delete + org isolation.
- `backend/tests/test_walls_pairing.py` — pair-into-cell + redeem + unpair.
- `backend/tests/test_walls_websocket.py` — WS auth, hello frame, play frame, disconnect.
- `backend/tests/test_walls_tick_loop.py` — same_playlist / synced_rotation timing semantics with fake clock.

**Modified files:**
- `backend/db.py` — add tables + columns inside `init_db()`.
- `backend/main.py` — add wall REST endpoints (CRUD, pairing) + register the WS route from `walls.py`. Modify `GET /screens/{token}/content` to include `wall_id` when applicable.
- `frontend/index.html` — Walls tab + walls list section + wall editor section markup.
- `frontend/app.js` — wall list / wizard / pair-into-cell modal / cell config / live mosaic.
- `frontend/styles.css` — wall grid + cell + mosaic styles.
- `frontend/i18n/en.json`, `frontend/i18n/ar.json` — wall keys.
- `player/index.html` — "Have a code from admin?" link in pairing view.
- `player/player.js` — `enterWallMode`, WS client, time-anchor seek, fallback polling.
- `player/styles.css` — minor tweaks for the admin-code input.
- `player/i18n/en.json`, `player/i18n/ar.json` — wall + admin-code keys.

**Untouched:** all existing endpoints, `pairing_codes` table, `screens/pair`, zones, templates, billing, signup flow.

---

## Conventions (read before starting any task)

- **Branch:** `feature/multi-screen-walls-phase1`, branched from current tip of `feature/security-hardening` (not yet merged to main, but stable).
- **Test runner:** the backend container does NOT bind-mount source. To run tests, copy files in then exec pytest. The block below is `RUN_TESTS` — it appears verbatim in every test step.

  ```bash
  RUN_TESTS='for f in backend/tests/test_*.py; do
    docker cp "$f" "signage_backend_1:/app/tests/$(basename $f)";
  done;
  docker cp backend/main.py signage_backend_1:/app/main.py;
  docker cp backend/db.py signage_backend_1:/app/db.py;
  docker cp backend/walls.py signage_backend_1:/app/walls.py 2>/dev/null || true;
  docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -20'
  ```

- **Single test:** swap the final command for `pytest tests/test_walls_crud.py::test_name -v`.
- **Frontend rebuild:** admin/player containers also use COPY (no volumes), so any change to `frontend/*` or `player/*` requires `docker-compose build frontend && docker-compose up -d frontend` (or `player`) before visual smoke.
- **Commits:** one commit per task at the end. Conventional-commits style: `feat(walls): ...`, `test(walls): ...`, `fix(walls): ...`. Co-author tag matches existing repo style.
- **Don't break existing behavior.** Run the full pytest suite after every backend task — it must stay green (current baseline: 97 passing). New tests are additive.

---

## Task 0: Branch setup + sanity-check baseline

**Files:** none modified.

- [ ] **Step 1: Create the feature branch from the current tip**

```bash
cd /home/ahmed/signage
git status   # confirm clean
git checkout -b feature/multi-screen-walls-phase1
```

Expected: `Switched to a new branch 'feature/multi-screen-walls-phase1'`

- [ ] **Step 2: Confirm backend container is up and healthy**

```bash
docker-compose ps backend postgres
```

Expected: both `Up (healthy)`. If not: `docker-compose up -d backend` and wait for health.

- [ ] **Step 3: Run baseline test suite to capture starting state**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `97 passed` (or whatever the current count is — record it; new tests will add to this).

- [ ] **Step 4: No commit. Branch is empty until Task 1.**

---

## Task 1: Schema migrations — walls, wall_cells, wall_pairing_codes, additive columns

**Files:**
- Modify: `backend/db.py:100-291` (the `init_db()` body)
- Test: `backend/tests/test_walls_crud.py` (new file)

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/test_walls_crud.py`:

```python
from db import connect, init_db


def test_walls_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'walls'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "organization_id", "name", "mode", "rows", "cols",
        "canvas_width_px", "canvas_height_px", "bezel_enabled",
        "spanned_playlist_id", "mirrored_mode", "mirrored_playlist_id",
        "created_at", "updated_at",
    }
    missing = expected - cols
    assert not missing, f"walls table missing columns: {missing}"


def test_wall_cells_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_cells'
            ORDER BY column_name
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    expected = {
        "id", "wall_id", "row_index", "col_index", "screen_id",
        "screen_size_inches",
        "bezel_top_mm", "bezel_right_mm", "bezel_bottom_mm", "bezel_left_mm",
        "playlist_id", "created_at",
    }
    missing = expected - cols
    assert not missing, f"wall_cells table missing columns: {missing}"


def test_wall_pairing_codes_table_exists():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'wall_pairing_codes'
        """)
        cols = {r["column_name"] for r in cur.fetchall()}
    assert {"id", "code", "wall_id", "row_index", "col_index", "status",
            "expires_at", "created_at", "claimed_at"}.issubset(cols)


def test_screens_has_wall_cell_id_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'screens' AND column_name = 'wall_cell_id'
        """)
        assert cur.fetchone() is not None


def test_organizations_has_walls_enabled_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'organizations' AND column_name = 'walls_enabled'
        """)
        assert cur.fetchone() is not None


def test_playlists_has_kind_column():
    init_db()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'playlists' AND column_name = 'kind'
        """)
        assert cur.fetchone() is not None
```

- [ ] **Step 2: Run the test — expect failures**

```bash
docker cp backend/tests/test_walls_crud.py signage_backend_1:/app/tests/test_walls_crud.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_crud.py -v 2>&1 | tail -20
```

Expected: 6 failed (`walls table missing columns: ...` etc.).

- [ ] **Step 3: Add the schema in `db.py`**

In `backend/db.py`, inside `init_db()`, immediately AFTER the existing `pairing_codes` CREATE TABLE block (line ~291) and BEFORE the first `ALTER TABLE` line, insert:

```python
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS walls (
                id                   SERIAL PRIMARY KEY,
                organization_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name                 TEXT NOT NULL,
                mode                 TEXT NOT NULL CHECK (mode IN ('spanned','mirrored')),
                rows                 INTEGER NOT NULL CHECK (rows BETWEEN 1 AND 8),
                cols                 INTEGER NOT NULL CHECK (cols BETWEEN 1 AND 8),
                canvas_width_px      INTEGER,
                canvas_height_px     INTEGER,
                bezel_enabled        BOOLEAN NOT NULL DEFAULT false,
                spanned_playlist_id  INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                mirrored_mode        TEXT CHECK (mirrored_mode IN ('same_playlist','synced_rotation')),
                mirrored_playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_walls_org ON walls(organization_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wall_cells (
                id                 SERIAL PRIMARY KEY,
                wall_id            INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
                row_index          INTEGER NOT NULL,
                col_index          INTEGER NOT NULL,
                screen_id          INTEGER REFERENCES screens(id) ON DELETE SET NULL,
                screen_size_inches NUMERIC(4,1),
                bezel_top_mm       NUMERIC(5,2),
                bezel_right_mm     NUMERIC(5,2),
                bezel_bottom_mm    NUMERIC(5,2),
                bezel_left_mm      NUMERIC(5,2),
                playlist_id        INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                created_at         TEXT NOT NULL,
                UNIQUE (wall_id, row_index, col_index)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_cells_wall ON wall_cells(wall_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_cells_screen ON wall_cells(screen_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wall_pairing_codes (
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
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_pairing_codes_wall ON wall_pairing_codes(wall_id)")
```

Then in the existing ALTER TABLE block (around line 293), append:

```python
        cursor.execute("ALTER TABLE screens       ADD COLUMN IF NOT EXISTS wall_cell_id INTEGER REFERENCES wall_cells(id) ON DELETE SET NULL")
        cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS walls_enabled BOOLEAN NOT NULL DEFAULT true")
        cursor.execute("ALTER TABLE playlists     ADD COLUMN IF NOT EXISTS kind          TEXT NOT NULL DEFAULT 'standard' CHECK (kind IN ('standard','wall_canvas'))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_screens_wall_cell ON screens(wall_cell_id)")
```

- [ ] **Step 4: Re-run the test, expect all 6 to pass**

```bash
docker cp backend/db.py signage_backend_1:/app/db.py
docker cp backend/tests/test_walls_crud.py signage_backend_1:/app/tests/test_walls_crud.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_crud.py -v 2>&1 | tail -20
```

Expected: `6 passed`.

- [ ] **Step 5: Run full suite, confirm no regressions**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `103 passed` (97 baseline + 6 new).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_walls_crud.py
git commit -m "$(cat <<'EOF'
feat(walls): schema for walls, wall_cells, wall_pairing_codes

Additive migration. New tables for the multi-screen wall feature plus
three additive columns on existing tables (screens.wall_cell_id,
organizations.walls_enabled, playlists.kind). Existing rows behave
exactly as before.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

I'll continue the plan in segments to fit token limits. Pause here — should I keep writing tasks 2 onward (wall CRUD endpoints) into the same file?