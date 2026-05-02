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

## Task 2: Wall CRUD endpoints — create / list / get / patch / delete

**Files:**
- Modify: `backend/main.py` (add Pydantic models near `class ScreenZonesPayload` at ~line 503; add endpoints after `screen_content` at ~line 1697)
- Test: `backend/tests/test_walls_crud.py` (extend existing file)

**Endpoint contract:**
- `POST   /walls` (admin/editor) — body `{name, mode, rows, cols, mirrored_mode?, mirrored_playlist_id?}`. 201 returns wall + auto-created cells.
- `GET    /walls` (any auth) — list walls in caller's org.
- `GET    /walls/{wall_id}` (any auth, org-scoped) — wall + cells.
- `PATCH  /walls/{wall_id}` (admin/editor) — name/mirrored_mode/mirrored_playlist_id/cells.playlist_id (per-cell).
- `DELETE /walls/{wall_id}` (admin/editor) — cascade-delete via FK.

- [ ] **Step 1: Add the failing CRUD tests**

Append to `backend/tests/test_walls_crud.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def admin_token(client):
    # Reuses existing test helpers if present; otherwise inline:
    import secrets, uuid
    from db import execute, query_one, utc_now_iso
    from main import hash_password
    org_slug = "wt" + secrets.token_hex(3)
    org_id = execute(
        "INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
        (f"WallTest {org_slug}", org_slug, utc_now_iso()),
    )
    user_id = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, f"u_{org_slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
        (user_id, token, utc_now_iso()),
    )
    return {"token": token, "org_id": org_id, "user_id": user_id}


def auth(t):
    return {"Authorization": f"Bearer {t['token']}"}


def test_create_mirrored_wall_same_playlist(client, admin_token):
    # Need a playlist first
    from db import execute, utc_now_iso
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (admin_token["org_id"], "p1", utc_now_iso()))
    res = client.post("/walls", json={
        "name": "Lobby Wall", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin_token))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Lobby Wall"
    assert body["mode"] == "mirrored"
    assert body["rows"] == 1 and body["cols"] == 2
    assert body["mirrored_mode"] == "same_playlist"
    assert len(body["cells"]) == 2
    assert {(c["row_index"], c["col_index"]) for c in body["cells"]} == {(0, 0), (0, 1)}


def test_create_wall_grid_bounds(client, admin_token):
    res = client.post("/walls", json={"name": "X", "mode": "mirrored", "rows": 9, "cols": 1,
                                      "mirrored_mode": "same_playlist"},
                      headers=auth(admin_token))
    assert res.status_code == 422


def test_list_walls_org_isolation(client, admin_token):
    # admin_token's org has no walls yet
    res = client.get("/walls", headers=auth(admin_token))
    assert res.status_code == 200
    before = len(res.json())
    # Create one
    client.post("/walls", json={"name": "W", "mode": "mirrored", "rows": 1, "cols": 1,
                                "mirrored_mode": "same_playlist"},
                headers=auth(admin_token))
    res = client.get("/walls", headers=auth(admin_token))
    assert len(res.json()) == before + 1


def test_get_wall_includes_cells(client, admin_token):
    create = client.post("/walls", json={"name": "W2", "mode": "mirrored", "rows": 2, "cols": 2,
                                         "mirrored_mode": "synced_rotation"},
                         headers=auth(admin_token)).json()
    res = client.get(f"/walls/{create['id']}", headers=auth(admin_token))
    assert res.status_code == 200
    assert len(res.json()["cells"]) == 4


def test_patch_wall_name(client, admin_token):
    w = client.post("/walls", json={"name": "Old", "mode": "mirrored", "rows": 1, "cols": 1,
                                    "mirrored_mode": "same_playlist"},
                    headers=auth(admin_token)).json()
    res = client.patch(f"/walls/{w['id']}", json={"name": "New"}, headers=auth(admin_token))
    assert res.status_code == 200
    assert res.json()["name"] == "New"


def test_delete_wall_cascades_cells(client, admin_token):
    from db import query_all
    w = client.post("/walls", json={"name": "X", "mode": "mirrored", "rows": 1, "cols": 2,
                                    "mirrored_mode": "same_playlist"},
                    headers=auth(admin_token)).json()
    res = client.delete(f"/walls/{w['id']}", headers=auth(admin_token))
    assert res.status_code == 204
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (w["id"],))
    assert cells == []
```

- [ ] **Step 2: Run, expect failure (endpoints don't exist)**

```bash
docker cp backend/tests/test_walls_crud.py signage_backend_1:/app/tests/test_walls_crud.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_crud.py -v 2>&1 | tail -20
```

Expected: tests in step 1 fail with 404 / 405 (existing 6 schema tests still pass).

- [ ] **Step 3: Add Pydantic models**

In `backend/main.py`, after the `class ScreenZonesPayload(BaseModel):` block (around line 503), insert:

```python
class WallCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    mode: str = Field(..., pattern="^(spanned|mirrored)$")
    rows: int = Field(..., ge=1, le=8)
    cols: int = Field(..., ge=1, le=8)
    canvas_width_px: Optional[int] = Field(default=None, ge=320, le=32768)
    canvas_height_px: Optional[int] = Field(default=None, ge=240, le=32768)
    bezel_enabled: bool = False
    spanned_playlist_id: Optional[int] = None
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None


class WallUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None
    bezel_enabled: Optional[bool] = None
    canvas_width_px: Optional[int] = Field(default=None, ge=320, le=32768)
    canvas_height_px: Optional[int] = Field(default=None, ge=240, le=32768)


class WallCellUpdate(BaseModel):
    row_index: int
    col_index: int
    playlist_id: Optional[int] = None
    screen_size_inches: Optional[float] = Field(default=None, ge=5, le=120)
    bezel_top_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_right_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_bottom_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_left_mm: Optional[float] = Field(default=None, ge=0, le=200)
```

- [ ] **Step 4: Add a serialization helper**

Add right after `sanitize_screen` (around line 426):

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

- [ ] **Step 5: Add the CRUD endpoints**

Append after the `screen_content` route (around line 1697 — after the `return payload` line of `def screen_content`):

```python
# -------------------- Walls --------------------

@app.post("/walls", status_code=201)
def create_wall(payload: WallCreate, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    if payload.mode == "mirrored" and not payload.mirrored_mode:
        raise http_error(422, "wall.mirrored_mode_required",
                         "Mirrored walls need a sub-mode (same_playlist or synced_rotation).")
    if payload.mode == "mirrored" and payload.mirrored_mode == "same_playlist" and not payload.mirrored_playlist_id:
        raise http_error(422, "wall.mirrored_playlist_required",
                         "Same-playlist mirrored walls need a playlist.")
    if payload.mirrored_playlist_id is not None:
        own = query_one("SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
                        (payload.mirrored_playlist_id, org_id(user)))
        if not own:
            raise http_error(404, "playlist.not_found", "Playlist not found")
    now = utc_now_iso()
    wall_id = execute(
        """INSERT INTO walls (organization_id, name, mode, rows, cols,
               canvas_width_px, canvas_height_px, bezel_enabled,
               spanned_playlist_id, mirrored_mode, mirrored_playlist_id,
               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (org_id(user), payload.name, payload.mode, payload.rows, payload.cols,
         payload.canvas_width_px, payload.canvas_height_px, payload.bezel_enabled,
         payload.spanned_playlist_id, payload.mirrored_mode, payload.mirrored_playlist_id,
         now, now),
    )
    for r in range(payload.rows):
        for c in range(payload.cols):
            execute(
                "INSERT INTO wall_cells (wall_id, row_index, col_index, created_at) VALUES (?, ?, ?, ?)",
                (wall_id, r, c, now),
            )
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    return serialize_wall(wall)


@app.get("/walls")
def list_walls(user: dict = Depends(get_current_user)) -> list[dict]:
    walls = query_all(
        "SELECT * FROM walls WHERE organization_id = ? ORDER BY id DESC",
        (org_id(user),),
    )
    return [serialize_wall(w) for w in walls]


@app.get("/walls/{wall_id}")
def get_wall(wall_id: int, user: dict = Depends(get_current_user)) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    return serialize_wall(wall)


@app.patch("/walls/{wall_id}")
def patch_wall(wall_id: int, payload: WallUpdate,
               user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return serialize_wall(wall)
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [utc_now_iso(), wall_id]
    execute(f"UPDATE walls SET {sets}, updated_at = ? WHERE id = ?", tuple(params))
    return serialize_wall(query_one("SELECT * FROM walls WHERE id = ?", (wall_id,)))


@app.patch("/walls/{wall_id}/cells")
def patch_wall_cell(wall_id: int, payload: WallCellUpdate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    fields = payload.model_dump(exclude_unset=True)
    if payload.playlist_id is not None:
        own = query_one("SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
                        (payload.playlist_id, org_id(user)))
        if not own:
            raise http_error(404, "playlist.not_found", "Playlist not found")
    set_fields = {k: v for k, v in fields.items() if k not in ("row_index", "col_index")}
    if set_fields:
        sets = ", ".join(f"{k} = ?" for k in set_fields.keys())
        params = list(set_fields.values()) + [wall_id, payload.row_index, payload.col_index]
        execute(
            f"UPDATE wall_cells SET {sets} "
            f"WHERE wall_id = ? AND row_index = ? AND col_index = ?",
            tuple(params),
        )
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, payload.row_index, payload.col_index),
    )
    return cell


@app.delete("/walls/{wall_id}", status_code=204)
def delete_wall(wall_id: int, user: dict = Depends(require_roles("admin", "editor"))) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    execute("DELETE FROM walls WHERE id = ?", (wall_id,))
    return None
```

- [ ] **Step 6: Re-run the new tests, expect pass**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker cp backend/tests/test_walls_crud.py signage_backend_1:/app/tests/test_walls_crud.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_crud.py -v 2>&1 | tail -25
```

Expected: 12 passed (6 schema + 6 CRUD).

- [ ] **Step 7: Run full suite**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `109 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_walls_crud.py
git commit -m "$(cat <<'EOF'
feat(walls): CRUD endpoints (create/list/get/patch/delete + per-cell update)

Org-scoped. Mirrored walls auto-create wall_cells on creation. Cell
playlist updates validated against caller's org.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wall pairing — pair-into-cell + redeem + unpair

**Files:**
- Modify: `backend/main.py` (add 3 endpoints after `delete_wall`)
- Test: `backend/tests/test_walls_pairing.py` (new file)

**Endpoint contract:**
- `POST   /walls/{wall_id}/cells/{row}/{col}/pair` (admin/editor) → `{code, expires_in_seconds, expires_at}`. 6-char code, 10-min TTL, status='pending'.
- `POST   /walls/cells/redeem` (unauth) → claim code, create screen scoped to wall's org, set `wall_cells.screen_id`, set `screens.wall_cell_id`. Returns `{status, screen_token, wall_id, cell:{row,col,rows,cols}, mode}`.
- `DELETE /walls/{wall_id}/cells/{row}/{col}/pairing` (admin/editor) → clear `wall_cells.screen_id` + `screens.wall_cell_id`.

- [ ] **Step 1: Write the failing pairing tests**

Create `backend/tests/test_walls_pairing.py`:

```python
import pytest
import secrets, uuid
from fastapi.testclient import TestClient

from db import execute, query_one, utc_now_iso
from main import app, hash_password


@pytest.fixture
def client():
    return TestClient(app)


def make_admin(label="wt"):
    slug = label + secrets.token_hex(3)
    oid = execute("INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
                  (f"O {slug}", slug, utc_now_iso()))
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return {"token": tok, "org_id": oid, "user_id": uid}


def auth(t): return {"Authorization": f"Bearer {t['token']}"}


def make_wall(client, admin, rows=1, cols=2):
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (admin["org_id"], "p", utc_now_iso()))
    return client.post("/walls", json={
        "name": "W", "mode": "mirrored", "rows": rows, "cols": cols,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=auth(admin)).json()


def test_pair_into_cell_returns_code(client):
    a = make_admin()
    w = make_wall(client, a)
    res = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["code"]) == 6
    assert body["expires_in_seconds"] >= 60


def test_redeem_creates_screen_and_binds_cell(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    res = client.post("/walls/cells/redeem", json={"code": code})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "paired"
    assert body["wall_id"] == w["id"]
    assert body["cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 2}
    assert body["mode"] == "mirrored"
    assert len(body["screen_token"]) > 16
    cell = query_one("SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
                     (w["id"],))
    assert cell["screen_id"] is not None
    screen = query_one("SELECT * FROM screens WHERE id = ?", (cell["screen_id"],))
    assert screen["organization_id"] == a["org_id"]
    assert screen["wall_cell_id"] == cell["id"]


def test_redeem_unknown_code_404(client):
    res = client.post("/walls/cells/redeem", json={"code": "ZZZZZZ"})
    assert res.status_code == 404


def test_redeem_double_returns_409(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    client.post("/walls/cells/redeem", json={"code": code})
    res = client.post("/walls/cells/redeem", json={"code": code})
    assert res.status_code == 409


def test_redeem_expired_returns_410(client):
    a = make_admin()
    w = make_wall(client, a)
    body = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()
    # Force expiry
    execute("UPDATE wall_pairing_codes SET expires_at = ? WHERE code = ?",
            ("2020-01-01T00:00:00+00:00", body["code"]))
    res = client.post("/walls/cells/redeem", json={"code": body["code"]})
    assert res.status_code == 410


def test_unpair_clears_cell_and_screen(client):
    a = make_admin()
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    client.post("/walls/cells/redeem", json={"code": code})
    res = client.delete(f"/walls/{w['id']}/cells/0/0/pairing", headers=auth(a))
    assert res.status_code == 204
    cell = query_one("SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 0",
                     (w["id"],))
    assert cell["screen_id"] is None


def test_pair_other_org_404(client):
    a1 = make_admin("a1")
    a2 = make_admin("a2")
    w = make_wall(client, a1)
    res = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a2))
    assert res.status_code == 404
```

- [ ] **Step 2: Run, expect failure**

```bash
docker cp backend/tests/test_walls_pairing.py signage_backend_1:/app/tests/test_walls_pairing.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_pairing.py -v 2>&1 | tail -20
```

Expected: 7 failed (404/405 — endpoints missing).

- [ ] **Step 3: Add the pairing endpoints**

Append in `backend/main.py` after the `delete_wall` route added in Task 2:

```python
class WallRedeemRequest(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


def _generate_unique_wall_pair_code() -> str:
    while True:
        code = generate_pair_code_v2()
        if not query_one("SELECT id FROM wall_pairing_codes WHERE code = ?", (code,)):
            return code


@app.post("/walls/{wall_id}/cells/{row}/{col}/pair")
def pair_wall_cell(wall_id: int, row: int, col: int,
                   user: dict = Depends(require_roles("admin", "editor"))) -> dict:
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
    now = datetime.now(timezone.utc)
    code = _generate_unique_wall_pair_code()
    expires_at = (now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)).isoformat()
    execute(
        """INSERT INTO wall_pairing_codes (code, wall_id, row_index, col_index,
               status, expires_at, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (code, wall_id, row, col, expires_at, now.isoformat()),
    )
    return {"code": code, "expires_at": expires_at,
            "expires_in_seconds": PAIR_CODE_TTL_SECONDS}


@app.post("/walls/cells/redeem")
@limiter.limit("30/minute")
def redeem_wall_cell(request: Request, payload: WallRedeemRequest) -> dict:
    row = query_one("SELECT * FROM wall_pairing_codes WHERE code = ?", (payload.code,))
    if not row:
        raise http_error(404, "wall.pair_code_unknown", "Code not recognized")
    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if row["status"] == "claimed":
        raise http_error(409, "wall.pair_code_used", "This code was already used")
    if now > expires_dt:
        execute("UPDATE wall_pairing_codes SET status = 'expired' WHERE id = ?", (row["id"],))
        raise http_error(410, "wall.pair_code_expired", "Code expired")

    wall = query_one("SELECT * FROM walls WHERE id = ?", (row["wall_id"],))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall is gone")

    # Create a fresh screen in the wall's org
    name = f"Wall {wall['name']} ({row['row_index']},{row['col_index']})"
    pair_code = generate_unique_pair_code()
    screen_token = generate_unique_token()
    screen_id = execute(
        """INSERT INTO screens (organization_id, name, pair_code, token, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (wall["organization_id"], name, pair_code, screen_token, now.isoformat()),
    )
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (row["wall_id"], row["row_index"], row["col_index"]),
    )
    # If cell already had a screen, leave that screen orphaned (admin can delete);
    # the cell now points to the new screen.
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (screen_id, cell["id"]))
    execute("UPDATE screens SET wall_cell_id = ? WHERE id = ?", (cell["id"], screen_id))
    execute(
        "UPDATE wall_pairing_codes SET status = 'claimed', claimed_at = ? WHERE id = ?",
        (now.isoformat(), row["id"]),
    )
    return {
        "status": "paired",
        "screen_token": screen_token,
        "wall_id": wall["id"],
        "cell": {"row": row["row_index"], "col": row["col_index"],
                 "rows": wall["rows"], "cols": wall["cols"]},
        "mode": wall["mode"],
    }


@app.delete("/walls/{wall_id}/cells/{row}/{col}/pairing", status_code=204)
def unpair_wall_cell(wall_id: int, row: int, col: int,
                     user: dict = Depends(require_roles("admin", "editor"))) -> None:
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
        execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (cell["screen_id"],))
    execute("UPDATE wall_cells SET screen_id = NULL WHERE id = ?", (cell["id"],))
    # Notify the connected TV (best-effort; safe if walls module not yet imported)
    try:
        from walls import broadcast_bye  # type: ignore
        broadcast_bye(wall_id, row, col, "cell_unpaired")
    except Exception:
        pass
    return None
```

- [ ] **Step 4: Re-run, expect pass**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_pairing.py -v 2>&1 | tail -20
```

Expected: 7 passed.

- [ ] **Step 5: Run full suite**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `116 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_walls_pairing.py
git commit -m "$(cat <<'EOF'
feat(walls): pair-into-cell + redeem + unpair endpoints

Admin generates a 6-char code bound to (wall, row, col); player redeems
to create a screen in the wall's org and bind it to the cell. Unpair
clears both sides and best-effort notifies the connected TV (when the
walls module is loaded).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: WebSocket endpoint + connection registry (`walls.py`)

**Files:**
- Create: `backend/walls.py`
- Modify: `backend/main.py` (import + register WS route + call `attach_walls(app)`)
- Test: `backend/tests/test_walls_websocket.py` (new)

**Goal:** When a paired wall TV connects to `WS /walls/{wall_id}/ws?screen_token=...`, validate the token, register the socket, send `hello` with `current_play` from the (lazy) tick loop, and forward `play`/`ping`/`bye` frames. Tick loop itself is implemented in Task 5 — Task 4 stubs the loop API so the WS handshake works.

- [ ] **Step 1: Write the WebSocket auth tests**

Create `backend/tests/test_walls_websocket.py`:

```python
import pytest, secrets, uuid
from fastapi.testclient import TestClient

from db import execute, query_one, utc_now_iso
from main import app, hash_password


@pytest.fixture
def client():
    return TestClient(app)


def _make_paired_wall(client):
    slug = "ws" + secrets.token_hex(3)
    oid = execute("INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
                  (f"O {slug}", slug, utc_now_iso()))
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (oid, "p", utc_now_iso()))
    w = client.post("/walls", json={"name": "W", "mode": "mirrored", "rows": 1, "cols": 1,
                                    "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid},
                    headers={"Authorization": f"Bearer {tok}"}).json()
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair",
                       headers={"Authorization": f"Bearer {tok}"}).json()["code"]
    redeem = client.post("/walls/cells/redeem", json={"code": code}).json()
    return w, redeem["screen_token"]


def test_ws_hello_frame_on_connect(client):
    wall, token = _make_paired_wall(client)
    with client.websocket_connect(f"/walls/{wall['id']}/ws?screen_token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello"
        assert msg["wall_id"] == wall["id"]
        assert msg["mode"] == "mirrored"
        assert msg["cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 1}
        assert "server_now_ms" in msg


def test_ws_rejects_unknown_token(client):
    wall, _ = _make_paired_wall(client)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/walls/{wall['id']}/ws?screen_token=zzzzzzz"
        ) as ws:
            ws.receive_json()


def test_ws_rejects_wrong_wall(client):
    wall1, token1 = _make_paired_wall(client)
    wall2, _ = _make_paired_wall(client)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/walls/{wall2['id']}/ws?screen_token={token1}"
        ) as ws:
            ws.receive_json()
```

- [ ] **Step 2: Run, expect failure**

```bash
docker cp backend/tests/test_walls_websocket.py signage_backend_1:/app/tests/test_walls_websocket.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_websocket.py -v 2>&1 | tail -10
```

Expected: 3 failed (404 on WS path).

- [ ] **Step 3: Create `backend/walls.py`**

Create `backend/walls.py`:

```python
"""Wall WebSocket fanout + lazy per-wall tick loop registry.

This module assumes a single-uvicorn-worker deployment. The connection
registry and tick-loop tasks live in process memory; if we ever scale
out workers, every wall's WebSocket connections must land on the same
worker (sticky routing) OR we must add a Redis pub/sub layer between
workers. Out of scope for v1.
"""

import asyncio
import json
import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from db import query_one, query_all, execute, utc_now_iso

router = APIRouter()

# wall_id -> {(row, col): WebSocket}
_connections: Dict[int, Dict[Tuple[int, int], WebSocket]] = defaultdict(dict)
# wall_id -> asyncio.Task
_tick_tasks: Dict[int, asyncio.Task] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def _wall_for_token(wall_id: int, screen_token: str):
    """Return (wall, cell) if token belongs to a screen in this wall, else None."""
    screen = query_one("SELECT * FROM screens WHERE token = ?", (screen_token,))
    if not screen or not screen.get("wall_cell_id"):
        return None
    cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (screen["wall_cell_id"],))
    if not cell or cell["wall_id"] != wall_id:
        return None
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall:
        return None
    return wall, cell, screen


def _hello_frame(wall: dict, cell: dict, current_play: dict | None) -> dict:
    return {
        "type": "hello",
        "wall_id": wall["id"],
        "mode": wall["mode"],
        "cell": {
            "row": cell["row_index"], "col": cell["col_index"],
            "rows": wall["rows"], "cols": wall["cols"],
        },
        "current_play": current_play,
        "server_now_ms": now_ms(),
    }


async def _send_safe(ws: WebSocket, frame: dict) -> bool:
    try:
        await ws.send_text(json.dumps(frame))
        return True
    except Exception:
        return False


async def broadcast(wall_id: int, frame: dict, exclude: Tuple[int, int] | None = None) -> None:
    dead = []
    for key, ws in list(_connections[wall_id].items()):
        if exclude and key == exclude:
            continue
        ok = await _send_safe(ws, frame)
        if not ok:
            dead.append(key)
    for key in dead:
        _connections[wall_id].pop(key, None)


def broadcast_bye(wall_id: int, row: int, col: int, reason: str) -> None:
    """Sync entry-point used by REST handlers (e.g. unpair). Best-effort."""
    ws = _connections.get(wall_id, {}).get((row, col))
    if not ws:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_send_safe(ws, {"type": "bye", "reason": reason,
                                          "server_now_ms": now_ms()}))
    except RuntimeError:
        pass


async def _ensure_tick_loop(wall_id: int) -> None:
    """Lazily create the tick loop. Implementation in Task 5 — for now, no-op."""
    if wall_id in _tick_tasks and not _tick_tasks[wall_id].done():
        return
    # Task 5 will replace this with the real loop.
    return


def current_play_for(wall_id: int, cell: dict) -> dict | None:
    """Return the current play frame for this wall+cell, or None.

    Task 5 fills this in. Until then we return None — clients still get
    a valid hello frame and can fall back to HTTP polling.
    """
    return None


@router.websocket("/walls/{wall_id}/ws")
async def wall_ws(websocket: WebSocket, wall_id: int,
                  screen_token: str = Query(..., min_length=8)):
    info = _wall_for_token(wall_id, screen_token)
    if info is None:
        await websocket.close(code=4401)
        return
    wall, cell, screen = info
    await websocket.accept()
    key = (cell["row_index"], cell["col_index"])
    # Replace any stale connection for this cell.
    old = _connections[wall_id].pop(key, None)
    if old is not None:
        try:
            await old.close(code=4000)
        except Exception:
            pass
    _connections[wall_id][key] = websocket

    await _ensure_tick_loop(wall_id)
    await _send_safe(websocket, _hello_frame(wall, cell, current_play_for(wall_id, cell)))

    execute("UPDATE screens SET last_seen = ? WHERE id = ?", (utc_now_iso(), screen["id"]))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "pong":
                # Clock-sync hint; nothing to do server-side beyond acknowledge.
                continue
            if msg.get("type") == "ready":
                # Could fan out cell_state to admin observers in a future task.
                continue
    except WebSocketDisconnect:
        pass
    finally:
        if _connections[wall_id].get(key) is websocket:
            _connections[wall_id].pop(key, None)
        # If wall has no live connections, optionally cancel its tick loop.
        if not _connections[wall_id]:
            t = _tick_tasks.pop(wall_id, None)
            if t and not t.done():
                t.cancel()


def attach_walls(app: FastAPI) -> None:
    app.include_router(router)
```

- [ ] **Step 4: Wire `walls.py` into `main.py`**

In `backend/main.py`, near the other top-level imports (after `from email_utils import ...` at line 26), add:

```python
from walls import attach_walls
```

Then immediately after `app.mount("/uploads", ...)` (around line 135), add:

```python
attach_walls(app)
```

- [ ] **Step 5: Re-run WS tests, expect pass**

```bash
docker cp backend/walls.py signage_backend_1:/app/walls.py
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_websocket.py -v 2>&1 | tail -15
```

Expected: 3 passed.

- [ ] **Step 6: Run full suite**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `119 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/walls.py backend/main.py backend/tests/test_walls_websocket.py
git commit -m "$(cat <<'EOF'
feat(walls): WebSocket endpoint + connection registry

New backend/walls.py owns the wall connection registry and the WS
route. Tick loop is stubbed (returns None for current_play); Task 5
fills it in. Single-worker assumption documented in module docstring.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Asyncio tick loop — mirrored same_playlist + synced_rotation

**Files:**
- Modify: `backend/walls.py` (replace `_ensure_tick_loop` and `current_play_for`)
- Test: `backend/tests/test_walls_tick_loop.py` (new)

**Goal:** Per-wall asyncio task that walks the playlist timeline and broadcasts `play` frames at item boundaries. Reads playlist + items at start; on `playlist_change` (REST endpoint signals via shared event), reloads. Same-playlist mode: one timeline shared by all cells. Synced-rotation: per-cell timelines that share the same item-index advance — slot duration is the **max** of cells' `items[i].duration_seconds`.

- [ ] **Step 1: Write the tick-loop test**

Create `backend/tests/test_walls_tick_loop.py`:

```python
import asyncio
import pytest

from db import execute, utc_now_iso
import walls as walls_mod


@pytest.mark.asyncio
async def test_same_playlist_play_frame_shape(monkeypatch):
    """Direct call to compute_play_frame for a same_playlist wall returns a play frame."""
    # Arrange: org, playlist, two media items
    org = execute("INSERT INTO organizations (name, slug, created_at) VALUES (?, ?, ?)",
                  ("Tick", "tick" + walls_mod.now_ms().__str__(), utc_now_iso()))
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (org, "p", utc_now_iso()))
    m1 = execute("INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (org, "m1", "a.mp4", "video/mp4", 100, utc_now_iso()))
    m2 = execute("INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (org, "m2", "b.mp4", "video/mp4", 100, utc_now_iso()))
    execute("INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (pid, m1, 5, 0, utc_now_iso()))
    execute("INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (pid, m2, 7, 1, utc_now_iso()))
    wid = execute(
        "INSERT INTO walls (organization_id, name, mode, rows, cols, mirrored_mode, mirrored_playlist_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (org, "W", "mirrored", 1, 1, "same_playlist", pid, utc_now_iso(), utc_now_iso()),
    )
    cell = {"row_index": 0, "col_index": 0, "wall_id": wid}

    frame = walls_mod.current_play_for(wid, cell)
    assert frame is not None
    assert frame["type"] == "play"
    assert frame["item"]["url"].endswith(".mp4")
    assert frame["duration_ms"] in (5000, 7000)
    assert "started_at_ms" in frame and "playlist_signature" in frame


def test_synced_rotation_slot_duration_is_max():
    """Synced-rotation slot uses the slowest cell's duration."""
    durations_per_cell = [[5, 10, 3], [4, 8, 6]]
    expected_slot_durations_ms = [5000, 10000, 6000]
    assert walls_mod.synced_rotation_slot_durations(durations_per_cell) == expected_slot_durations_ms
```

Add `pytest-asyncio` to the backend test environment (already a transitive dep via FastAPI's TestClient toolchain — confirm via `docker exec signage_backend_1 pip show pytest-asyncio`. If missing, add it to `backend/requirements.txt` and rebuild. Skip the `@pytest.mark.asyncio` test if not installed; the second test (sync) still validates lockstep math.).

- [ ] **Step 2: Run, expect failure**

```bash
docker cp backend/tests/test_walls_tick_loop.py signage_backend_1:/app/tests/test_walls_tick_loop.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_tick_loop.py -v 2>&1 | tail -15
```

Expected: failures (functions don't exist or return None).

- [ ] **Step 3: Implement tick-loop helpers in `walls.py`**

In `backend/walls.py`, replace the `current_play_for` stub and the `_ensure_tick_loop` stub with this fuller implementation. Add near the top:

```python
import hashlib

# wall_id -> dict carrying timeline state
_timeline_state: Dict[int, dict] = {}
```

Replace `current_play_for` and `_ensure_tick_loop`:

```python
def _playlist_signature(wall_id: int) -> str:
    rows = query_all(
        """SELECT pi.id, pi.media_id, pi.duration_seconds, pi.position
           FROM playlist_items pi
           JOIN walls w ON w.mirrored_playlist_id = pi.playlist_id
           WHERE w.id = ?
           ORDER BY pi.position""",
        (wall_id,),
    )
    blob = "|".join(f"{r['id']}:{r['media_id']}:{r['duration_seconds']}" for r in rows)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def _load_same_playlist_items(wall_id: int) -> list[dict]:
    rows = query_all(
        """SELECT pi.id, pi.media_id, pi.duration_seconds, pi.position,
                  m.name, m.filename, m.mime_type
           FROM playlist_items pi
           JOIN walls w ON w.mirrored_playlist_id = pi.playlist_id
           JOIN media m ON m.id = pi.media_id
           WHERE w.id = ?
           ORDER BY pi.position""",
        (wall_id,),
    )
    for it in rows:
        if it["mime_type"] == "text/url":
            it["url"] = it["filename"]
        else:
            it["url"] = f"/uploads/{it['filename']}"
    return rows


def _load_per_cell_items(wall_id: int) -> dict[tuple[int, int], list[dict]]:
    cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ?", (wall_id,))
    out: dict[tuple[int, int], list[dict]] = {}
    for cell in cells:
        if not cell["playlist_id"]:
            out[(cell["row_index"], cell["col_index"])] = []
            continue
        rows = query_all(
            """SELECT pi.id, pi.media_id, pi.duration_seconds, pi.position,
                      m.name, m.filename, m.mime_type
               FROM playlist_items pi JOIN media m ON m.id = pi.media_id
               WHERE pi.playlist_id = ? ORDER BY pi.position""",
            (cell["playlist_id"],),
        )
        for it in rows:
            if it["mime_type"] == "text/url":
                it["url"] = it["filename"]
            else:
                it["url"] = f"/uploads/{it['filename']}"
        out[(cell["row_index"], cell["col_index"])] = rows
    return out


def synced_rotation_slot_durations(durations_per_cell: list[list[int]]) -> list[int]:
    """Slot i's duration (ms) = max over cells of items[i].duration * 1000."""
    if not durations_per_cell:
        return []
    n = len(durations_per_cell[0])
    return [max(c[i] for c in durations_per_cell) * 1000 for i in range(n)]


def _build_play_frame(item: dict, started_at_ms: int, signature: str) -> dict:
    return {
        "type": "play",
        "item": {"id": item["id"], "url": item["url"],
                 "mime_type": item["mime_type"], "name": item["name"]},
        "started_at_ms": started_at_ms,
        "duration_ms": item["duration_seconds"] * 1000,
        "playlist_signature": signature,
        "server_now_ms": now_ms(),
    }


def current_play_for(wall_id: int, cell: dict) -> dict | None:
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    if not wall or wall["mode"] != "mirrored":
        return None
    sig = _playlist_signature(wall_id)
    state = _timeline_state.get(wall_id)
    started_at_ms = state["item_started_at_ms"] if state else now_ms()
    index = state["index"] if state else 0
    if wall["mirrored_mode"] == "same_playlist":
        items = _load_same_playlist_items(wall_id)
        if not items:
            return None
        idx = index % len(items)
        return _build_play_frame(items[idx], started_at_ms, sig)
    if wall["mirrored_mode"] == "synced_rotation":
        items_by_cell = _load_per_cell_items(wall_id)
        my = items_by_cell.get((cell["row_index"], cell["col_index"]), [])
        if not my:
            return None
        idx = index % len(my)
        return _build_play_frame(my[idx], started_at_ms, sig)
    return None


async def _tick_loop(wall_id: int):
    """One asyncio task per active wall.

    For same_playlist: walks shared timeline; broadcasts play to all cells.
    For synced_rotation: per-cell items, slot duration = max over cells.
    """
    try:
        while True:
            wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
            if not wall:
                return
            sig = _playlist_signature(wall_id)
            if wall["mirrored_mode"] == "same_playlist":
                items = _load_same_playlist_items(wall_id)
                if not items:
                    await asyncio.sleep(2)
                    continue
                state = _timeline_state.setdefault(wall_id, {"index": 0, "item_started_at_ms": now_ms()})
                idx = state["index"] % len(items)
                state["item_started_at_ms"] = now_ms()
                frame = _build_play_frame(items[idx], state["item_started_at_ms"], sig)
                await broadcast(wall_id, frame)
                await asyncio.sleep(items[idx]["duration_seconds"])
                state["index"] = (state["index"] + 1) % len(items)
            elif wall["mirrored_mode"] == "synced_rotation":
                items_by_cell = _load_per_cell_items(wall_id)
                if not items_by_cell:
                    await asyncio.sleep(2)
                    continue
                lengths = {k: len(v) for k, v in items_by_cell.items() if v}
                if not lengths or len(set(lengths.values())) != 1:
                    # Mismatch — wait and try again (admin will fix)
                    await asyncio.sleep(5)
                    continue
                n = next(iter(lengths.values()))
                state = _timeline_state.setdefault(wall_id, {"index": 0, "item_started_at_ms": now_ms()})
                idx = state["index"] % n
                state["item_started_at_ms"] = now_ms()
                slot_durations = synced_rotation_slot_durations(
                    [[x["duration_seconds"] for x in v] for v in items_by_cell.values()]
                )
                # Per-cell frames
                for (r, c), ws in list(_connections[wall_id].items()):
                    items = items_by_cell.get((r, c), [])
                    if not items:
                        continue
                    frame = _build_play_frame(items[idx], state["item_started_at_ms"], sig)
                    await _send_safe(ws, frame)
                await asyncio.sleep(slot_durations[idx] / 1000.0)
                state["index"] = (state["index"] + 1) % n
            else:
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        return


async def _ensure_tick_loop(wall_id: int) -> None:
    if wall_id in _tick_tasks and not _tick_tasks[wall_id].done():
        return
    _tick_tasks[wall_id] = asyncio.create_task(_tick_loop(wall_id))
```

- [ ] **Step 4: Re-run, expect pass**

```bash
docker cp backend/walls.py signage_backend_1:/app/walls.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_tick_loop.py -v 2>&1 | tail -10
```

Expected: 2 passed.

- [ ] **Step 5: Run full suite**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `121 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/walls.py backend/tests/test_walls_tick_loop.py
git commit -m "$(cat <<'EOF'
feat(walls): per-wall asyncio tick loop (same_playlist + synced_rotation)

Lazy per-wall task broadcasts play frames at item boundaries. Same-playlist
shares one timeline; synced-rotation uses per-cell items with slot-duration
= max-over-cells of items[i].duration. Mismatched item counts pause the
loop until the admin fixes them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `GET /screens/{token}/content` includes `wall_id`

**Files:**
- Modify: `backend/main.py:1684-1697` (the `screen_content` route)
- Test: extend `backend/tests/test_walls_pairing.py`

- [ ] **Step 1: Write the regression test**

Append to `backend/tests/test_walls_pairing.py`:

```python
def test_screen_content_includes_wall_id_for_paired_cells(client):
    a = make_admin("c")
    w = make_wall(client, a)
    code = client.post(f"/walls/{w['id']}/cells/0/0/pair", headers=auth(a)).json()["code"]
    redeem = client.post("/walls/cells/redeem", json={"code": code}).json()
    res = client.get(f"/screens/{redeem['screen_token']}/content")
    assert res.status_code == 200
    body = res.json()
    assert body["wall_id"] == w["id"]
    assert body["wall_cell"] == {"row": 0, "col": 0, "rows": 1, "cols": 2}


def test_screen_content_no_wall_id_for_standalone(client):
    a = make_admin("std")
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) VALUES (?, ?, ?, ?, ?)",
        (a["org_id"], "S", "111111", "tok_" + secrets.token_hex(8), utc_now_iso()),
    )
    tok = query_one("SELECT token FROM screens WHERE id = ?", (sid,))["token"]
    res = client.get(f"/screens/{tok}/content")
    assert "wall_id" not in res.json() or res.json().get("wall_id") is None
```

- [ ] **Step 2: Run, expect failure on the wall test**

```bash
docker cp backend/tests/test_walls_pairing.py signage_backend_1:/app/tests/test_walls_pairing.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_pairing.py::test_screen_content_includes_wall_id_for_paired_cells -v 2>&1 | tail -10
```

Expected: 1 failure (`KeyError: 'wall_id'` or `assert None == ...`).

- [ ] **Step 3: Modify `screen_content` in `backend/main.py`**

Replace the body of `screen_content` (currently lines 1685–1697):

```python
@app.get("/screens/{token}/content")
def screen_content(token: str) -> dict:
    screen = query_one("SELECT * FROM screens WHERE token = ?", (token,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    execute(
        "UPDATE screens SET last_seen = ? WHERE id = ?",
        (utc_now_iso(), screen["id"]),
    )

    payload = build_screen_payload(screen)
    payload["screen"] = sanitize_screen(screen)
    if screen.get("wall_cell_id"):
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (screen["wall_cell_id"],))
        if cell:
            wall = query_one("SELECT * FROM walls WHERE id = ?", (cell["wall_id"],))
            if wall:
                payload["wall_id"] = wall["id"]
                payload["wall_cell"] = {
                    "row": cell["row_index"], "col": cell["col_index"],
                    "rows": wall["rows"], "cols": wall["cols"],
                }
                payload["wall_mode"] = wall["mode"]
    return payload
```

- [ ] **Step 4: Re-run, expect pass**

```bash
docker cp backend/main.py signage_backend_1:/app/main.py
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest tests/test_walls_pairing.py -v 2>&1 | tail -10
```

Expected: all 9 wall-pairing tests pass (7 prior + 2 new).

- [ ] **Step 5: Full suite**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `123 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_walls_pairing.py
git commit -m "$(cat <<'EOF'
feat(walls): expose wall_id + wall_cell on GET /screens/{token}/content

Player uses these new fields to decide whether to enter wall mode (open
WS) or play standalone. Standalone screens see no change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Admin frontend — Walls tab + section markup

**Files:**
- Modify: `frontend/index.html` (add nav button + section)
- Modify: `frontend/styles.css` (wall grid styles)

- [ ] **Step 1: Add the nav button**

In `frontend/index.html`, after the `<button data-section="users" ...>` line (line 29), insert:

```html
          <button data-section="walls" data-i18n="nav.walls">Walls</button>
```

- [ ] **Step 2: Add the Walls section markup**

After the `users` section (find `<section id="users"` around line 357 and locate its closing `</section>`), insert:

```html
        <section id="walls" class="panel hidden">
          <div class="panel-header">
            <h2 data-i18n="walls.title">Walls</h2>
            <button id="walls-create-btn" class="btn btn-primary" data-i18n="walls.create">Create wall</button>
          </div>
          <div id="walls-list" class="walls-grid" aria-live="polite"></div>

          <div id="walls-editor" class="walls-editor hidden">
            <div class="walls-editor-header">
              <h3 id="walls-editor-title">—</h3>
              <button id="walls-editor-close" class="btn btn-ghost" data-i18n="walls.back_to_list">Back</button>
            </div>
            <div id="walls-editor-body"></div>
          </div>

          <div id="walls-pair-modal" class="modal hidden" role="dialog" aria-labelledby="walls-pair-title">
            <div class="modal-card">
              <h3 id="walls-pair-title" data-i18n="walls.pair_title">Pair this cell</h3>
              <p data-i18n="walls.pair_instructions">
                On the TV you want in this position, open <code>play.khanshoof.com</code>,
                tap <strong>Have a code from admin?</strong>, and enter:
              </p>
              <div id="walls-pair-code" class="walls-pair-code">—</div>
              <div id="walls-pair-countdown" class="walls-pair-countdown" data-i18n="walls.pair_expires_label">Expires in —</div>
              <div class="modal-actions">
                <button id="walls-pair-refresh" class="btn" data-i18n="walls.pair_new_code">New code</button>
                <button id="walls-pair-close" class="btn btn-primary" data-i18n="walls.pair_done">Done</button>
              </div>
            </div>
          </div>
        </section>
```

- [ ] **Step 3: Add the wall-grid styles**

Append to `frontend/styles.css`:

```css
/* Walls */
.walls-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-block-start: 12px;
}
.walls-card {
  background: var(--cream-2, #fff8ee);
  border-radius: 12px;
  padding: 14px;
  border: 1px solid var(--border, #e9ddc6);
}
.walls-card h4 { margin: 0 0 6px 0; }
.walls-card .walls-meta { color: var(--muted, #7d6f59); font-size: 0.9em; }
.walls-card .walls-mosaic {
  display: grid; gap: 2px; margin-block-start: 10px;
  background: #2a2a2a; padding: 4px; border-radius: 6px;
  aspect-ratio: 16/9;
}
.walls-card .walls-mosaic-cell {
  background: #444;
  display: flex; align-items: center; justify-content: center;
  color: #ddd; font-size: 0.8em;
}
.walls-card .walls-mosaic-cell.online { background: var(--mint, #c4e7d4); color: #1a3a26; }
.walls-card .walls-mosaic-cell.offline { background: var(--rose, #f3c4c4); color: #5a1a1a; }

.walls-editor {
  margin-block-start: 16px;
}
.walls-editor-grid {
  display: grid;
  gap: 6px;
  background: #2a2a2a;
  padding: 8px;
  border-radius: 8px;
  aspect-ratio: 16/9;
  max-width: 800px;
}
.walls-editor-cell {
  background: #444;
  color: #eee;
  border: 2px dashed transparent;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 8px; cursor: pointer;
  font-size: 0.9em;
}
.walls-editor-cell.empty {
  border-color: #888;
  background: #333;
}
.walls-editor-cell.paired { background: var(--mint, #c4e7d4); color: #1a3a26; }

.walls-pair-code {
  font-size: 2.5em; font-family: var(--font-mono, monospace);
  letter-spacing: 0.15em; text-align: center;
  background: var(--cream, #fff8ee); padding: 16px; border-radius: 10px;
  margin-block: 12px;
}
.walls-pair-countdown { text-align: center; color: var(--muted, #7d6f59); }
```

- [ ] **Step 4: Rebuild the admin container and visually confirm the tab appears**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Open `https://app.khanshoof.com`, log in. Confirm the **Walls** nav button is present (English placeholder text — translation comes in Task 10). Click it; the empty section + Create button render. No JS errors in console.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/styles.css
git commit -m "$(cat <<'EOF'
feat(walls): admin UI — Walls nav + section + editor + pair-modal markup

Static markup + grid styles. Wires up in Task 8 (list + wizard) and
Task 9 (pair modal + cell config + mosaic).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Admin frontend — list rendering + create-wall wizard

**Files:**
- Modify: `frontend/app.js` (add a Walls module — list, create wizard)

The wizard is a 3-step inline form (no third-party modal lib): Step 1 basics (name, mode, rows/cols), Step 2 mode-specific (mirrored sub-mode + playlist picker), Step 3 cells (deferred — pair flow lives in Task 9).

- [ ] **Step 1: Locate the section-show wiring**

Find the existing `data-section` click handler in `frontend/app.js` (search for `data-section`). Confirm switching to a section unhides `#${section}` and hides others.

- [ ] **Step 2: Add the Walls module**

Append to `frontend/app.js`:

```javascript
// ====== Walls ======
const Walls = (() => {
  const state = { walls: [], editing: null, pairing: null };

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem("session_token");
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${path}`,
      { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
    if (!res.ok && res.status !== 204) {
      let body = {};
      try { body = await res.json(); } catch (_) {}
      const code = body?.detail?.code || `http_${res.status}`;
      throw Object.assign(new Error(body?.detail?.message || res.statusText), { code });
    }
    return res.status === 204 ? null : res.json();
  }

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
        <h4>${escapeHtml(w.name)}</h4>
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
        <div class="walls-actions">
          <button class="btn btn-ghost" data-action="edit">${Khan.t("walls.edit", "Edit")}</button>
          <button class="btn btn-danger" data-action="delete">${Khan.t("walls.delete", "Delete")}</button>
        </div>
      </article>
    `).join("");
    root.querySelectorAll("[data-wall-id]").forEach(card => {
      const id = parseInt(card.dataset.wallId, 10);
      card.querySelector('[data-action="edit"]').addEventListener("click", () => openEditor(id));
      card.querySelector('[data-action="delete"]').addEventListener("click", () => deleteWall(id));
    });
  }

  async function createWizard() {
    const playlists = await api("/playlists");
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
          <label><input type="radio" name="mode" value="spanned" disabled />
            ${Khan.t("walls.mode_spanned_phase2", "Spanned (Phase 2 — coming soon)")}</label>
        </fieldset>
        <div class="walls-grid-picker">
          <label>${Khan.t("walls.rows", "Rows")}
            <input name="rows" type="number" min="1" max="8" value="1" required /></label>
          <label>${Khan.t("walls.cols", "Cols")}
            <input name="cols" type="number" min="1" max="8" value="2" required /></label>
        </div>
        <fieldset class="mirrored-fields">
          <legend>${Khan.t("walls.mirrored_submode", "Mirrored sub-mode")}</legend>
          <label><input type="radio" name="mirrored_mode" value="same_playlist" checked />
            ${Khan.t("walls.same_playlist", "Same playlist on all screens")}</label>
          <label><input type="radio" name="mirrored_mode" value="synced_rotation" />
            ${Khan.t("walls.synced_rotation", "Different playlist per cell, synchronized rotation")}</label>
          <label class="same-playlist-only">
            ${Khan.t("walls.playlist", "Playlist")}
            <select name="mirrored_playlist_id" required>
              ${playlists.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("")}
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
  }

  async function submitWizard(ev) {
    ev.preventDefault();
    const f = ev.target;
    const sub = f.mirrored_mode.value;
    const payload = {
      name: f.name.value.trim(),
      mode: f.mode.value,
      rows: parseInt(f.rows.value, 10),
      cols: parseInt(f.cols.value, 10),
      mirrored_mode: sub,
    };
    if (sub === "same_playlist") payload.mirrored_playlist_id = parseInt(f.mirrored_playlist_id.value, 10);
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
    if (!confirm(Khan.t("walls.confirm_delete", "Delete this wall? Paired screens will revert to standalone."))) return;
    try {
      await api(`/walls/${id}`, { method: "DELETE" });
      toast(Khan.t("walls.deleted", "Wall deleted"));
      await loadList();
    } catch (err) {
      toast(err.message || "delete failed", "error");
    }
  }

  function closeEditor() {
    document.getElementById("walls-editor").classList.add("hidden");
    state.editing = null;
  }

  // openEditor and pair-flow are filled in by Task 9.
  async function openEditor(id) {
    state.editing = id;
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

  return {
    onShow: loadList,
    state,
    api,
    loadList,
    openEditor,
    closeEditor,
    renderList,
  };
})();

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
```

In the existing `data-section` click handler (search `data-section` in app.js to locate it), add a branch:

```javascript
// inside the section-switch handler, after the existing dispatch:
if (section === "walls") Walls.onShow();
```

- [ ] **Step 3: Rebuild and smoke**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Click **Walls** in nav. Click **Create wall**. Wizard appears. Submit a 1×2 same-playlist wall against an existing playlist. Toast says "Wall created". List re-renders with the new wall.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "$(cat <<'EOF'
feat(walls): admin list + 3-step create-wall wizard

Mirrored same_playlist + synced_rotation supported. Spanned mode is
disabled in the picker (Phase 2). Editor body is a stub; Task 9 fills
in cell configuration + pair-into-cell flow.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Admin frontend — wall editor (cells, pair modal, live mosaic)

**Files:**
- Modify: `frontend/app.js` (extend the Walls module with `renderEditor` + pair flow + mosaic)

- [ ] **Step 1: Append `renderEditor`, pair flow, and mosaic refresh to the Walls module**

Replace the trailing `return { onShow, state, api, ... };` block in `frontend/app.js`'s Walls module with:

```javascript
  async function renderEditor(id) {
    const wall = await api(`/walls/${id}`);
    const body = document.getElementById("walls-editor-body");
    document.getElementById("walls-editor-title").textContent = wall.name;
    const playlists = wall.mirrored_mode === "synced_rotation" ? await api("/playlists") : [];
    body.innerHTML = `
      <div class="walls-editor-summary">
        <span class="walls-meta">
          ${wall.mode === "mirrored"
            ? Khan.t("walls.mode_mirrored", "Mirrored") + (wall.mirrored_mode === "synced_rotation"
                ? " · " + Khan.t("walls.synced_rotation_short", "Synced rotation")
                : " · " + Khan.t("walls.same_playlist_short", "Same playlist"))
            : Khan.t("walls.mode_spanned", "Spanned")}
          · ${wall.rows}×${wall.cols}
        </span>
      </div>
      <div class="walls-editor-grid"
           style="grid-template-columns: repeat(${wall.cols}, 1fr);">
        ${wall.cells.map(c => renderCellTile(c, wall, playlists)).join("")}
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
        patchCell(wall.id, r, c, { playlist_id: parseInt(ev.target.value, 10) }));
    });
    refreshMosaic(wall.id);
  }

  function renderCellTile(c, wall, playlists) {
    const paired = !!c.screen_id;
    const playlistPicker = wall.mirrored_mode === "synced_rotation"
      ? `<label class="cell-playlist">
           ${Khan.t("walls.playlist", "Playlist")}
           <select data-action="cell-playlist">
             <option value="">—</option>
             ${playlists.map(p =>
               `<option value="${p.id}" ${p.id === c.playlist_id ? "selected" : ""}>${escapeHtml(p.name)}</option>`
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
      renderEditor(wallId);
    };
  }

  let pairTimer = null;
  async function refreshPairCode() {
    if (!state.pairing) return;
    if (pairTimer) clearInterval(pairTimer);
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
      toast(err.message || "pair-code failed", "error");
    }
  }

  async function unpairCell(wallId, row, col) {
    if (!confirm(Khan.t("walls.confirm_unpair", "Unpair this cell?"))) return;
    try {
      await api(`/walls/${wallId}/cells/${row}/${col}/pairing`, { method: "DELETE" });
      toast(Khan.t("walls.unpaired", "Unpaired"));
      renderEditor(wallId);
    } catch (err) { toast(err.message || "unpair failed", "error"); }
  }

  async function patchCell(wallId, row, col, fields) {
    try {
      await api(`/walls/${wallId}/cells`, {
        method: "PATCH",
        body: JSON.stringify({ row_index: row, col_index: col, ...fields }),
      });
      toast(Khan.t("walls.cell_updated", "Cell updated"));
    } catch (err) { toast(err.message || "update failed", "error"); }
  }

  let mosaicTimer = null;
  async function refreshMosaic(wallId) {
    if (mosaicTimer) clearInterval(mosaicTimer);
    const tick = async () => {
      if (state.editing !== wallId) return clearInterval(mosaicTimer);
      try {
        const w = await api(`/walls/${wallId}`);
        // Reflect online/offline on the existing tiles by toggling classes.
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
    mosaicTimer = setInterval(tick, 5000);
  }

  Walls.renderEditor = renderEditor;
```

Note: the `Walls.renderEditor = renderEditor;` at the end attaches the function to the module so the existing `openEditor` (added in Task 8) can call it.

- [ ] **Step 2: Rebuild and smoke**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Open Walls → Edit a wall. The visual grid renders with a tile per cell. On an empty cell click "Pair this screen" — modal opens with a 6-char code and a counting-down "Expires in 9:59". Refresh button generates a new code. Close modal returns to the editor.

- [ ] **Step 3: End-to-end pair smoke**

In a second tab, open `https://play.khanshoof.com`. (The "Have a code from admin?" link is added in Task 11 — for now, manually drive the redeem from the browser console:)

```javascript
fetch("https://api.khanshoof.com/walls/cells/redeem", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ code: "ABC123" })  // <-- code from the modal
}).then(r => r.json()).then(console.log)
```

Confirm the response includes `screen_token`. Re-open the wall editor — the cell now shows as paired.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "$(cat <<'EOF'
feat(walls): admin wall editor — cell tiles, pair modal, mosaic refresh

Per-cell pair / unpair / playlist picker (synced_rotation only) wired
to backend. Pair modal shows code + countdown + new-code button. Live
mosaic polls /walls/:id every 5s while the editor is open.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Admin i18n keys (EN + AR)

**Files:**
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json`
- Verify: `scripts/check_i18n.py`

- [ ] **Step 1: Add the wall keys to `frontend/i18n/en.json`**

Open the file and append these keys (preserving JSON validity — insert before the closing `}`):

```json
"nav.walls": "Walls",
"walls.title": "Walls",
"walls.create": "Create wall",
"walls.empty": "No walls yet. Click \"Create wall\" to start.",
"walls.mode": "Mode",
"walls.mode_mirrored": "Mirrored",
"walls.mode_spanned": "Spanned",
"walls.mode_spanned_phase2": "Spanned (Phase 2 — coming soon)",
"walls.rows": "Rows",
"walls.cols": "Cols",
"walls.name": "Name",
"walls.mirrored_submode": "Mirrored sub-mode",
"walls.same_playlist": "Same playlist on all screens",
"walls.same_playlist_short": "Same playlist",
"walls.synced_rotation": "Different playlist per cell, synchronized rotation",
"walls.synced_rotation_short": "Synced rotation",
"walls.playlist": "Playlist",
"walls.save": "Create",
"walls.cancel": "Cancel",
"walls.created": "Wall created",
"walls.create_failed": "Couldn't create wall",
"walls.deleted": "Wall deleted",
"walls.confirm_delete": "Delete this wall? Paired screens will revert to standalone.",
"walls.edit": "Edit",
"walls.delete": "Delete",
"walls.back_to_list": "Back",
"walls.editor_loading": "Loading…",
"walls.wizard_title": "New wall",
"walls.cell_paired": "Paired",
"walls.cell_unpair": "Unpair",
"walls.cell_pair": "Pair this screen",
"walls.cell_updated": "Cell updated",
"walls.unpaired": "Unpaired",
"walls.confirm_unpair": "Unpair this cell?",
"walls.pair_title": "Pair this cell",
"walls.pair_instructions": "On the TV you want in this position, open play.khanshoof.com, tap \"Have a code from admin?\", and enter:",
"walls.pair_expires_label": "Expires in —",
"walls.pair_expires_in": "Expires in {time}",
"walls.pair_new_code": "New code",
"walls.pair_done": "Done"
```

- [ ] **Step 2: Add the same keys to `frontend/i18n/ar.json`**

```json
"nav.walls": "الجدران",
"walls.title": "الجدران",
"walls.create": "إنشاء جدار",
"walls.empty": "لا توجد جدران بعد. اضغط \"إنشاء جدار\" للبدء.",
"walls.mode": "النمط",
"walls.mode_mirrored": "متطابق",
"walls.mode_spanned": "ممتد",
"walls.mode_spanned_phase2": "ممتد (المرحلة الثانية — قريباً)",
"walls.rows": "الصفوف",
"walls.cols": "الأعمدة",
"walls.name": "الاسم",
"walls.mirrored_submode": "النمط الفرعي للتطابق",
"walls.same_playlist": "نفس قائمة التشغيل على كل الشاشات",
"walls.same_playlist_short": "نفس القائمة",
"walls.synced_rotation": "قائمة لكل خلية، تدوير متزامن",
"walls.synced_rotation_short": "تدوير متزامن",
"walls.playlist": "قائمة التشغيل",
"walls.save": "إنشاء",
"walls.cancel": "إلغاء",
"walls.created": "تم إنشاء الجدار",
"walls.create_failed": "تعذر إنشاء الجدار",
"walls.deleted": "تم حذف الجدار",
"walls.confirm_delete": "حذف هذا الجدار؟ ستعود الشاشات المرتبطة إلى الوضع المستقل.",
"walls.edit": "تعديل",
"walls.delete": "حذف",
"walls.back_to_list": "رجوع",
"walls.editor_loading": "جاري التحميل…",
"walls.wizard_title": "جدار جديد",
"walls.cell_paired": "مرتبطة",
"walls.cell_unpair": "فصل الارتباط",
"walls.cell_pair": "ربط هذه الشاشة",
"walls.cell_updated": "تم تحديث الخلية",
"walls.unpaired": "تم فصل الارتباط",
"walls.confirm_unpair": "فصل ارتباط هذه الخلية؟",
"walls.pair_title": "ربط هذه الخلية",
"walls.pair_instructions": "على الشاشة المراد وضعها في هذا الموقع، افتح play.khanshoof.com، اضغط \"معك رمز من الأدمن؟\"، وأدخل:",
"walls.pair_expires_label": "تنتهي خلال —",
"walls.pair_expires_in": "تنتهي خلال {time}",
"walls.pair_new_code": "رمز جديد",
"walls.pair_done": "تم"
```

- [ ] **Step 3: Verify EN/AR parity**

```bash
python3 scripts/check_i18n.py
```

Expected: zero diff for the `frontend` app.

- [ ] **Step 4: Rebuild and visually verify**

```bash
docker-compose build frontend && docker-compose up -d frontend
```

Toggle to Arabic in the admin. The Walls tab label, page heading, button labels, modal text all flip to Arabic. Grid layout is mirrored (RTL) — cell `(0,0)` appears at the top-right, `(0,cols-1)` at the top-left.

- [ ] **Step 5: Commit**

```bash
git add frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(walls): admin i18n keys (EN + AR) — wall editor + pair modal

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Player — "Have a code from admin?" affordance

**Files:**
- Modify: `player/index.html` (add the link + hidden form to the pairing view)
- Modify: `player/player.js` (toggle handler + redeem call)
- Modify: `player/styles.css` (form styles)

- [ ] **Step 1: Markup**

In `player/index.html`, locate the existing pairing view container (the section that shows the QR + code; visible by default). Inside it, add right before its closing tag:

```html
        <div class="admin-code-affordance">
          <button id="admin-code-toggle" type="button" class="link"
                  data-i18n="pairing.code_from_admin">Have a code from admin? Enter it here.</button>
          <form id="admin-code-form" class="admin-code-form hidden">
            <label for="admin-code-input" data-i18n="pairing.code_input_label">Code</label>
            <input id="admin-code-input" maxlength="6" minlength="6"
                   autocomplete="off" autocapitalize="characters" spellcheck="false"
                   pattern="[A-Z0-9]{6}" />
            <button type="submit" class="btn btn-primary" data-i18n="pairing.code_submit">Submit</button>
            <p id="admin-code-error" class="admin-code-error" role="alert"></p>
          </form>
        </div>
```

- [ ] **Step 2: Wire up in `player/player.js`**

Add inside the existing module (near the other UI element captures, around line 50–80 — before `boot`):

```javascript
const adminCodeToggle = document.getElementById("admin-code-toggle");
const adminCodeForm = document.getElementById("admin-code-form");
const adminCodeInput = document.getElementById("admin-code-input");
const adminCodeError = document.getElementById("admin-code-error");

if (adminCodeToggle && adminCodeForm) {
  adminCodeToggle.addEventListener("click", () => {
    adminCodeForm.classList.toggle("hidden");
    adminCodeInput?.focus();
  });
}
if (adminCodeForm) {
  adminCodeForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    adminCodeError.textContent = "";
    const code = (adminCodeInput.value || "").trim().toUpperCase();
    if (code.length !== 6) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/walls/cells/redeem`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const body = await res.json();
      if (!res.ok) {
        const msgKey = body?.detail?.code === "wall.pair_code_expired"
          ? "pairing.code_expired"
          : "pairing.code_invalid";
        adminCodeError.textContent = Khan.t(msgKey, "Code not recognized.");
        return;
      }
      localStorage.setItem("screen_token", body.screen_token);
      stopPairPoll();
      // Reload — boot() will detect token + wall_id from /content and enter wall mode.
      window.location.reload();
    } catch (err) {
      adminCodeError.textContent = Khan.t("pairing.code_invalid", "Code not recognized.");
    }
  });
}
```

- [ ] **Step 3: Style**

Append to `player/styles.css`:

```css
.admin-code-affordance { margin-block-start: 1.5em; text-align: center; }
.admin-code-affordance .link {
  background: none; border: none; color: var(--accent, #6b8e6b);
  text-decoration: underline; cursor: pointer; font: inherit;
}
.admin-code-form { margin-block-start: 0.75em; display: flex;
  gap: 8px; align-items: center; justify-content: center; flex-wrap: wrap; }
.admin-code-form input {
  font: inherit; padding: 8px 12px; width: 8ch; letter-spacing: 0.15em;
  text-align: center; text-transform: uppercase; border-radius: 6px;
  border: 1px solid var(--border, #e9ddc6);
}
.admin-code-error { color: var(--rose, #b53939); margin-block-start: 0.5em; }
```

- [ ] **Step 4: Rebuild and smoke**

```bash
docker-compose build player && docker-compose up -d player
```

Open `https://play.khanshoof.com`. Pairing view shows the existing self-code + QR plus the new "Have a code from admin?" link. Click it, enter `ZZZZZZ` — error renders. Generate a real code from the admin pair modal (Task 9) and enter it on the player. Page reloads; player enters wall mode (which is still the standalone HTTP path — Task 12 wires WebSocket).

- [ ] **Step 5: Commit**

```bash
git add player/index.html player/player.js player/styles.css
git commit -m "$(cat <<'EOF'
feat(walls): player — \"Have a code from admin?\" pairing affordance

Optional alternate flow alongside the existing self-pair + QR code.
Calls POST /walls/cells/redeem with a 6-char code; stores screen_token
on success; existing standalone customers see the link but can ignore
it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Player — `enterWallMode` (WebSocket + time-anchor seek + HTTP fallback)

**Files:**
- Modify: `player/player.js`

- [ ] **Step 1: Add wall-mode state + WS client**

Append to `player/player.js`, before the `bootI18nThenBoot` IIFE at the bottom:

```javascript
// ====== Wall mode ======
let wallSocket = null;
let wallId = null;
let wallReconnectAttempts = 0;
let wallClockOffsetMs = 0;
let wallLastFrame = null;
const WALL_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

function effectiveNowMs() { return Date.now() + wallClockOffsetMs; }

async function enterWallMode(id) {
  wallId = id;
  setStatus(Khan.t("wall.connecting", "Connecting to wall…"));
  openWallSocket();
}

function openWallSocket() {
  if (!wallId || !screenToken) return;
  const wsBase = API_BASE.replace(/^http/, "ws");
  const url = `${wsBase}/walls/${wallId}/ws?screen_token=${encodeURIComponent(screenToken)}`;
  try { wallSocket?.close(); } catch (_) {}
  wallSocket = new WebSocket(url);

  wallSocket.addEventListener("open", () => {
    wallReconnectAttempts = 0;
  });
  wallSocket.addEventListener("message", (ev) => {
    let frame;
    try { frame = JSON.parse(ev.data); } catch (_) { return; }
    onWallFrame(frame);
  });
  wallSocket.addEventListener("close", () => {
    setStatus(Khan.t("wall.reconnecting", "Reconnecting to wall…"));
    const delay = WALL_RECONNECT_BACKOFF_MS[
      Math.min(wallReconnectAttempts, WALL_RECONNECT_BACKOFF_MS.length - 1)
    ];
    wallReconnectAttempts += 1;
    setTimeout(openWallSocket, delay);
  });
  wallSocket.addEventListener("error", () => { /* close handler will retry */ });
}

function onWallFrame(frame) {
  const clientReceivedMs = Date.now();
  if (typeof frame.server_now_ms === "number") {
    wallClockOffsetMs = frame.server_now_ms - clientReceivedMs;
  }
  if (frame.type === "hello") {
    if (frame.current_play) renderWallPlay(frame.current_play);
  } else if (frame.type === "play") {
    renderWallPlay(frame);
  } else if (frame.type === "ping") {
    try { wallSocket?.send(JSON.stringify({
      type: "pong", client_received_ms: clientReceivedMs, client_now_ms: Date.now(),
    })); } catch (_) {}
  } else if (frame.type === "bye") {
    try { wallSocket?.close(); } catch (_) {}
    wallId = null;
    localStorage.removeItem("screen_token");
    window.location.reload();
  } else if (frame.type === "playlist_change") {
    // Force a re-fetch of /content; subsequent play frames will pick up new media.
    fetchContent().catch(() => {});
  }
}

function renderWallPlay(frame) {
  const item = frame.item;
  if (!item) return;
  if (wallLastFrame && wallLastFrame.item.id === item.id
      && wallLastFrame.started_at_ms === frame.started_at_ms) {
    return; // already rendering this; just keep playing
  }
  wallLastFrame = frame;
  contentEl.innerHTML = "";
  const startedAtMs = frame.started_at_ms;
  const expectedPositionMs = effectiveNowMs() - startedAtMs;
  const node = createMediaNode(item);
  contentEl.appendChild(node);
  if (node.tagName === "VIDEO") {
    node.addEventListener("loadeddata", () => {
      const t = Math.max(0, expectedPositionMs / 1000);
      try { node.currentTime = t; } catch (_) {}
      node.play().catch(() => {});
    }, { once: true });
    setInterval(() => correctVideoDrift(node, frame), 2000);
  }
}

function createMediaNode(item) {
  const mime = item.mime_type || "";
  if (mime.startsWith("video/")) {
    const v = document.createElement("video");
    v.src = item.url; v.muted = true; v.autoplay = true; v.playsInline = true;
    v.style.cssText = "position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000;";
    return v;
  }
  if (mime.startsWith("image/")) {
    const i = document.createElement("img");
    i.src = item.url; i.alt = item.name || "";
    i.style.cssText = "position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000;";
    return i;
  }
  if (mime === "text/url") {
    const f = document.createElement("iframe");
    f.src = item.url; f.style.cssText = "position:fixed;inset:0;width:100%;height:100%;border:0;background:#000;";
    return f;
  }
  // PDF and others
  const f = document.createElement("iframe");
  f.src = item.url; f.style.cssText = "position:fixed;inset:0;width:100%;height:100%;border:0;background:#000;";
  return f;
}

function correctVideoDrift(video, frame) {
  if (!video.isConnected) return;
  const expectedSec = (effectiveNowMs() - frame.started_at_ms) / 1000;
  const delta = (video.currentTime - expectedSec) * 1000;
  if (Math.abs(delta) > 200) {
    try { video.currentTime = Math.max(0, expectedSec); } catch (_) {}
    video.playbackRate = 1.0;
  } else if (Math.abs(delta) > 50) {
    video.playbackRate = delta < 0 ? 1.02 : 0.98;
    setTimeout(() => { video.playbackRate = 1.0; }, 1000);
  }
}
```

- [ ] **Step 2: Hook `enterWallMode` into the boot path**

In `player/player.js`, find `boot()` (line ~477). After the existing `await fetchContent();` line (around 508), modify the surrounding block:

```javascript
  setStatus(Khan.t("status.loading_content", "Loading content..."));
  const layout = await fetchLayout();
  const contentResp = await (await fetch(
    `${API_BASE}/screens/${screenToken}/content`)).json().catch(() => null);
  if (contentResp?.wall_id) {
    renderSingleLayout();
    await enterWallMode(contentResp.wall_id);
  } else if (layout?.zones && layout.zones.length > 0) {
    layoutSignature = getLayoutSignature(layout.zones);
    renderZonesLayout(layout.zones);
  } else {
    renderSingleLayout();
    await fetchContent();
  }
  startRefreshLoop();
```

(Replaces lines 501–510. Note: this calls `/content` once during boot to detect wall membership; subsequent fetches keep the existing 60s cadence.)

In `startRefreshLoop` (around line 449), add a guard at the top so it becomes a no-op when the WS is up:

```javascript
function startRefreshLoop() {
  if (refreshLoopStarted) return;
  refreshLoopStarted = true;
  setInterval(() => {
    if (wallSocket && wallSocket.readyState === WebSocket.OPEN) return; // WS drives playback
    if (zonesEl && !zonesEl.classList.contains("hidden")) {
      // ...existing zone-fetch branch
    } else {
      fetchContent().catch(...);
    }
  }, 60000); // bumped from 15000 — wall fallback only kicks in on WS death
}
```

(Keep the existing inner branches — only add the early-return guard and bump the interval to 60000.)

- [ ] **Step 3: Rebuild and smoke**

```bash
docker-compose build player && docker-compose up -d player
```

Open the player on the paired TV (or laptop). Browser dev tools → Network → WS — confirm an open `/walls/.../ws` connection, frames arriving. Pull the WS server (`docker-compose stop backend && sleep 5 && docker-compose start backend`) — confirm reconnect attempts log; once back, hello frame triggers playback resume.

- [ ] **Step 4: Commit**

```bash
git add player/player.js
git commit -m "$(cat <<'EOF'
feat(walls): player — enterWallMode with WS, time-anchor seek, HTTP fallback

WebSocket drives playback when in a wall. Server-time anchor + per-frame
clock-offset estimate keep video drift inside 200ms (hard seek) / 50ms
(rate nudge). HTTP /content polling is downshifted to 60s and short-
circuited while the WS is open.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Player i18n keys (EN + AR)

**Files:**
- Modify: `player/i18n/en.json`, `player/i18n/ar.json`

- [ ] **Step 1: Append to `player/i18n/en.json`**

```json
"pairing.code_from_admin": "Have a code from admin? Enter it here.",
"pairing.code_input_label": "Code",
"pairing.code_submit": "Submit",
"pairing.code_invalid": "Code not recognized.",
"pairing.code_expired": "Code expired. Ask admin for a new one.",
"wall.connecting": "Connecting to wall…",
"wall.reconnecting": "Reconnecting to wall…"
```

- [ ] **Step 2: Append to `player/i18n/ar.json`**

```json
"pairing.code_from_admin": "معك رمز من الأدمن؟ أدخله هنا.",
"pairing.code_input_label": "الرمز",
"pairing.code_submit": "إرسال",
"pairing.code_invalid": "الرمز غير معروف.",
"pairing.code_expired": "انتهت صلاحية الرمز. اطلب رمزاً جديداً من الأدمن.",
"wall.connecting": "جاري الاتصال بالجدار…",
"wall.reconnecting": "جاري إعادة الاتصال بالجدار…"
```

- [ ] **Step 3: Parity check + rebuild**

```bash
python3 scripts/check_i18n.py
docker-compose build player && docker-compose up -d player
```

- [ ] **Step 4: Commit**

```bash
git add player/i18n/en.json player/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(walls): player i18n keys (EN + AR) — admin-code + wall connecting/reconnecting

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Nginx WebSocket upgrade config

**Files:**
- Modify: nginx config for the API host (`api.khanshoof.com`).

The repo's nginx configs live under each frontend service folder; the API host is fronted by Cloudflare Tunnel + uvicorn directly. Cloudflare Tunnel handles WS upgrade out of the box (verified in spec §6 rollout note 5), but if there's an nginx layer in the API path, it needs the upgrade headers.

- [ ] **Step 1: Locate the API nginx config**

```bash
grep -rln "proxy_pass\s*http://backend" --include="*.conf" --include="*.nginx" .
```

If the API is fronted only by Cloudflare Tunnel → uvicorn (no nginx), this task is a **documentation-only step** — record that fact in the plan-of-record and skip steps 2–3.

- [ ] **Step 2 (only if nginx fronts the API): Add WS upgrade**

In the matching server / location block:

```nginx
location /walls/ {
  proxy_pass http://backend;
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  proxy_set_header Host $host;
  proxy_read_timeout 3600s;
}
```

- [ ] **Step 3 (only if nginx fronts the API): Reload + smoke**

```bash
docker-compose exec api-nginx nginx -t
docker-compose exec api-nginx nginx -s reload
```

Open the player; verify the WS connection succeeds via dev tools.

- [ ] **Step 4: Commit (or skip if doc-only)**

```bash
git add path/to/nginx.conf
git commit -m "$(cat <<'EOF'
feat(walls): nginx — WebSocket upgrade headers on /walls/* (or document N/A)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: End-to-end smoke + final regression sweep

**Files:** none modified.

- [ ] **Step 1: Run the full backend test suite once more**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -5
```

Expected: `123 passed` (97 baseline + 26 new wall tests, ±a couple from the optional async test).

- [ ] **Step 2: Manual mirrored same_playlist smoke (per spec §6)**

1. Create a 2×1 mirrored wall against an existing playlist with ≥2 video items.
2. Pair two browser tabs of `play.khanshoof.com` into cells (0,0) and (0,1) using the admin-code affordance.
3. Confirm both tabs flip items together, drift <200ms by eye.
4. Close one tab abruptly; confirm the other keeps playing.
5. Reopen the closed tab with the same paired token; confirm catch-up within ~2s.

- [ ] **Step 3: Manual synced_rotation smoke**

1. Edit the wall, set `mirrored_mode = synced_rotation`.
2. Set per-cell playlists with **matching** item counts (e.g., both have 2 items).
3. Confirm cells advance index in lockstep, slot duration = max of the two.
4. Set mismatched item counts. Confirm tick loop pauses gracefully (no errors in backend log).

- [ ] **Step 4: Standalone regression**

1. Pair a fresh TV via the existing self-code/QR flow (no admin code).
2. Confirm playback works exactly as before; no WS connection in dev tools.

- [ ] **Step 5: Bilingual smoke**

1. Toggle admin to Arabic. Walls editor + pair modal render RTL.
2. Toggle player to Arabic. "معك رمز من الأدمن؟" link works; redeem flow works.

- [ ] **Step 6: Final tag + push**

```bash
git push -u origin feature/multi-screen-walls-phase1
```

Open a PR titled "feat: multi-screen walls — Phase 1 (mirrored)". The branch base is `feature/security-hardening` (until that one merges to main; once it does, rebase this branch onto main and re-target the PR).

---

## Done

At this point Phase 1 (mirrored walls) is feature-complete: data model, REST + WS endpoints, asyncio tick loop, admin UI, player WS client, bilingual i18n, regression suite green. Phase 2 (spanned walls + canvas editor + bezel math) gets its own plan written after this one is in production.

**REQUIRED SUB-SKILL when finishing:** Use `superpowers:finishing-a-development-branch` to decide between merge / PR / cleanup based on test pass rate and review readiness.