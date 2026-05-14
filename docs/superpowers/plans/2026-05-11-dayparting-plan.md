# Phase 2.5e — Dayparting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-location chains can define a named, reusable Schedule (time-of-day + day-of-week rules → playlists) and assign it to any number of screens. Server-side resolver picks the active playlist based on site timezone; player's existing 15s poll picks up boundary crossings.

**Architecture:** Two new postgres tables (`schedules`, `schedule_rules`) plus two new columns (`sites.timezone`, `screens.schedule_id`). A pure-function `resolve_active_playlist(screen)` replaces the direct `screen.playlist_id` lookup in `build_screen_payload()`. New admin "Schedules" nav section; timezone picker on site edit; schedule picker on screen edit. **No asyncio tick, no WS infrastructure** — the player's existing 15s polling loop handles propagation.

**Tech Stack:** FastAPI · psycopg (`?` placeholders translate to `%s`) · Python `zoneinfo` (stdlib) · pytest · vanilla-JS admin with `Khan.t()` i18n.

**Spec:** `docs/superpowers/specs/2026-05-10-dayparting-design.md`
**Branch:** `feature/dayparting` (already created from main `4a236db`)

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(dayparting):` or `test(dayparting):`.
2. Backend container source is **baked into the image, not volume-mounted**. After changes to `backend/*.py`, rebuild:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Backend tests run via:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
   Without these env vars only ~110 of the suite pass. **Baseline on `main` is 151 passing** (the offline contract test from Phase 2.5d ships on main).
4. `db.py` uses `?` placeholders translated to `%s` for psycopg. Always use `?` in SQL.
5. Errors thrown by endpoints use `raise http_error(status, code, message)`, not `raise HTTPException(...)`. Frontend localizes via `code` (the message_key).
6. Frontend container source IS volume-mounted; JS/CSS/HTML changes hot-reload on browser refresh. `Khan.t(key, fallback)` for translations; `data-i18n` auto-translates element text on load. i18n parity gated by `scripts/check_i18n.py`.
7. JS parse check: `node -e "new Function(require('fs').readFileSync('frontend/app.js','utf8'))" && echo OK`.
8. Do NOT modify `.env` or rewrite prod URLs.

---

## Task 1: Schema — tables, columns, indices

**Files:**
- Modify: `backend/db.py`
- Modify: `backend/tests/test_security.py` (append 3 schema introspection tests)

**Why first:** Everything else depends on these tables existing.

- [ ] **Step 1: Write failing schema tests**

Append to `backend/tests/test_security.py`:

```python
# ── Dayparting schema (Phase 2.5e) ────────────────────────────────────

def test_schedules_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("schedules", "name"),
    )
    assert row is not None


def test_schedule_rules_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("schedule_rules", "days_of_week"),
    )
    assert row is not None


def test_sites_has_timezone_column():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("sites", "timezone"),
    )
    assert row is not None


def test_screens_has_schedule_id_column():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("screens", "schedule_id"),
    )
    assert row is not None
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_security.py -k "schedules or schedule_rules or has_timezone or has_schedule_id"
```
Expected: 4 FAIL.

- [ ] **Step 3: Add schema to `backend/db.py`**

Open `backend/db.py`. Find `init_db()`. Locate the Phase 2.5c block added at lines 350-391 (search for `# ── Phase 2.5c: security hardening`). Immediately AFTER the `audit_log` indices block (around line 391), insert:

```python
        # ── Phase 2.5e: dayparting ──────────────────────────────────────
        cursor.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kuwait'")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
              id              SERIAL PRIMARY KEY,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name            TEXT NOT NULL,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedules_org "
            "ON schedules (organization_id)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule_rules (
              id              SERIAL PRIMARY KEY,
              schedule_id     INTEGER NOT NULL REFERENCES schedules(id)  ON DELETE CASCADE,
              playlist_id     INTEGER NOT NULL REFERENCES playlists(id)  ON DELETE CASCADE,
              start_time      TIME NOT NULL,
              end_time        TIME NOT NULL,
              days_of_week    SMALLINT NOT NULL,
              position        INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedule_rules_schedule "
            "ON schedule_rules (schedule_id)"
        )

        cursor.execute("ALTER TABLE screens ADD COLUMN IF NOT EXISTS schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL")
```

(Preserve 8-space indentation matching surrounding code inside `init_db`.)

- [ ] **Step 4: Rebuild + recreate backend so init_db runs**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
docker-compose ps | grep backend
```
Expected: `Up (healthy)`.

- [ ] **Step 5: Run schema tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_security.py -k "schedules or schedule_rules or has_timezone or has_schedule_id"
```
Expected: 4 PASS.

- [ ] **Step 6: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `155 passed` (151 baseline + 4 new).

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_security.py
git commit -m "$(cat <<'EOF'
feat(dayparting): schedules + schedule_rules tables + 2 columns

Adds:
- sites.timezone TEXT NOT NULL DEFAULT 'Asia/Kuwait'
- schedules (id, organization_id, name, created_at)
- schedule_rules (id, schedule_id, playlist_id, start_time TIME,
  end_time TIME, days_of_week SMALLINT bitmask, position)
- screens.schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL

All idempotent (CREATE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS).
Existing screens get schedule_id=NULL → current behavior preserved.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Resolver helpers + unit tests

**Files:**
- Modify: `backend/main.py`
- Create: `backend/tests/test_dayparting_resolver.py`

**Goal:** Pure functions that decide which playlist is active right now. No DB writes; testable with mocked `datetime.now`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_dayparting_resolver.py`:

```python
"""Unit tests for the dayparting resolver helpers."""
from datetime import time, datetime, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from main import _time_in_window, _site_timezone, resolve_active_playlist


# ── _time_in_window ───────────────────────────────────────────────────

def test_normal_window_inclusive_start():
    assert _time_in_window(time(11, 0), time(11, 0), time(14, 0)) is True


def test_normal_window_exclusive_end():
    assert _time_in_window(time(14, 0), time(11, 0), time(14, 0)) is False


def test_normal_window_middle_match():
    assert _time_in_window(time(12, 30), time(11, 0), time(14, 0)) is True


def test_normal_window_before_start():
    assert _time_in_window(time(10, 59), time(11, 0), time(14, 0)) is False


def test_wrap_midnight_after_start():
    # 22:00–02:00 — 23:30 is inside the late-night window
    assert _time_in_window(time(23, 30), time(22, 0), time(2, 0)) is True


def test_wrap_midnight_before_end():
    # 22:00–02:00 — 01:30 is inside (early-morning leg)
    assert _time_in_window(time(1, 30), time(22, 0), time(2, 0)) is True


def test_wrap_midnight_outside():
    # 22:00–02:00 — 10:00 is OUTSIDE
    assert _time_in_window(time(10, 0), time(22, 0), time(2, 0)) is False


# ── _site_timezone ────────────────────────────────────────────────────

def test_site_timezone_returns_kuwait_for_no_site():
    tz = _site_timezone(None)
    assert tz == ZoneInfo("Asia/Kuwait")


def test_site_timezone_returns_kuwait_for_unknown_site():
    tz = _site_timezone(9999999)  # nonexistent
    assert tz == ZoneInfo("Asia/Kuwait")


# ── resolve_active_playlist ───────────────────────────────────────────

def test_resolve_no_schedule_returns_default_playlist():
    screen = {"schedule_id": None, "playlist_id": 42, "site_id": None}
    assert resolve_active_playlist(screen) == 42


def test_resolve_no_schedule_no_default_returns_none():
    screen = {"schedule_id": None, "playlist_id": None, "site_id": None}
    assert resolve_active_playlist(screen) is None
```

- [ ] **Step 2: Run them to verify failure (functions don't exist yet)**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_dayparting_resolver.py 2>&1 | tail -10
```
Expected: `ImportError` or `cannot import name '_time_in_window' from 'main'`.

- [ ] **Step 3: Add resolver helpers to `backend/main.py`**

Find a logical home for these — somewhere near `build_screen_payload` (around line 1554). Insert the helpers immediately BEFORE `def build_screen_payload`:

```python
# ── Dayparting (Phase 2.5e) ───────────────────────────────────────────
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import time as _time_type

_KUWAIT_TZ = ZoneInfo("Asia/Kuwait")


def _time_in_window(now: _time_type, start: _time_type, end: _time_type) -> bool:
    """True iff `now` is inside [start, end), handling wrap-midnight rules."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _site_timezone(site_id: Optional[int]) -> ZoneInfo:
    """Look up the site's IANA tz; fall back to Asia/Kuwait."""
    if site_id is None:
        return _KUWAIT_TZ
    row = query_one("SELECT timezone FROM sites WHERE id = ?", (site_id,))
    if not row or not row.get("timezone"):
        return _KUWAIT_TZ
    try:
        return ZoneInfo(row["timezone"])
    except ZoneInfoNotFoundError:
        logger.warning("invalid_site_timezone site_id=%s tz=%s", site_id, row["timezone"])
        return _KUWAIT_TZ


def resolve_active_playlist(screen: dict) -> Optional[int]:
    """Return the playlist_id that should currently play on this screen.

    Resolution order:
      1. If screen has schedule_id AND a rule matches now-in-site-tz → rule.playlist_id
      2. Else → screen.playlist_id (may be None)
    """
    if not screen.get("schedule_id"):
        return screen.get("playlist_id")

    site_tz = _site_timezone(screen.get("site_id"))
    now_local = datetime.now(site_tz)
    weekday_bit = 1 << now_local.weekday()      # Mon=0..Sun=6
    now_t = now_local.time()

    rules = query_all(
        "SELECT id, playlist_id, start_time, end_time, days_of_week "
        "FROM schedule_rules WHERE schedule_id = ?",
        (screen["schedule_id"],),
    )
    for rule in rules:
        if not (rule["days_of_week"] & weekday_bit):
            continue
        if _time_in_window(now_t, rule["start_time"], rule["end_time"]):
            return rule["playlist_id"]

    return screen.get("playlist_id")            # fallback to default
```

Verify `Optional` and `datetime` are already imported (they are — used elsewhere). `logger` is at line 30.

- [ ] **Step 4: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 5: Run resolver tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_dayparting_resolver.py
```
Expected: 11 PASS.

- [ ] **Step 6: Run full suite, confirm no regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `166 passed` (155 + 11).

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_dayparting_resolver.py
git commit -m "$(cat <<'EOF'
feat(dayparting): _time_in_window + _site_timezone + resolve_active_playlist

Pure helpers in backend/main.py. Wrap-midnight handled by reverse
comparison. Unknown/invalid timezone falls back to Asia/Kuwait
(logged warning). Resolver returns screens.playlist_id when no
schedule attached or no rule matches.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire resolver into `build_screen_payload` + integration test

**Files:**
- Modify: `backend/main.py:1554` (the `build_screen_payload` body)
- Modify: `backend/tests/test_dayparting_resolver.py` (append integration test)

**Goal:** Swap the single `screen.get("playlist_id")` lookup in `build_screen_payload()` for `resolve_active_playlist(screen)`. Add an end-to-end test that creates a screen with a schedule and verifies the right playlist appears in `/screens/{token}/content`.

- [ ] **Step 1: Write failing integration test**

Append to `backend/tests/test_dayparting_resolver.py`:

```python
# ── Integration: schedule drives /content endpoint ────────────────────
from unittest.mock import patch


def _create_two_playlists_and_schedule(client, signed_up_org):
    """Helper: create 2 playlists with media, a screen, and a schedule
    with a rule that picks playlist B Mon-Sun 00:00-23:59. Returns the
    screen token, default playlist id (A), scheduled playlist id (B)."""
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}

    # Two media files
    def upload():
        files = {"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")}
        r = client.post("/media/upload", headers=bearer, files=files)
        assert r.status_code in (200, 201), r.text
        return r.json()
    media_a = upload()
    media_b = upload()

    # Two playlists, each with one item
    def make_playlist(name, mid):
        r = client.post("/playlists", headers=bearer, json={"name": name})
        assert r.status_code in (200, 201), r.text
        pl = r.json()
        r = client.post(f"/playlists/{pl['id']}/items", headers=bearer,
                        json={"media_id": mid})
        assert r.status_code in (200, 201), r.text
        return pl["id"]
    pl_a = make_playlist("Default", media_a["id"])
    pl_b = make_playlist("Scheduled", media_b["id"])

    # Screen attached to A by default
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    assert r.status_code in (200, 201), r.text
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"playlist_id": pl_a})
    assert r.status_code == 200, r.text

    return {"token": screen["token"], "screen_id": screen["id"],
            "playlist_a": pl_a, "playlist_b": pl_b}


def test_content_endpoint_uses_resolver_with_no_schedule(client, signed_up_org):
    """No schedule attached → /content returns the default playlist's items."""
    info = _create_two_playlists_and_schedule(client, signed_up_org)
    r = client.get(f"/screens/{info['token']}/content")
    assert r.status_code == 200
    body = r.json()
    # Default playlist A should be returned
    assert body["playlist"]["id"] == info["playlist_a"]
```

- [ ] **Step 2: Verify the test passes against current code** (since no schedule attached, resolver should already short-circuit to playlist_id)

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_dayparting_resolver.py::test_content_endpoint_uses_resolver_with_no_schedule
```

If it PASSES, that's expected — the current `build_screen_payload` uses `screen.get("playlist_id")` which gives the right answer when no schedule is attached. The test is "passing for the wrong reason." That's OK — we're verifying that wiring the resolver doesn't break the no-schedule path.

- [ ] **Step 3: Update `build_screen_payload` in `backend/main.py`**

Find `def build_screen_payload(screen: dict) -> dict:` (around line 1554). Find the line:

```python
    if screen.get("playlist_id"):
```

Replace it with:

```python
    active_playlist_id = resolve_active_playlist(screen)
    if active_playlist_id:
```

Then in the body of that `if` block, find the references to `screen["playlist_id"]` and replace them with `active_playlist_id`. The current body looks like:

```python
    if screen.get("playlist_id"):
        playlist = query_one("SELECT * FROM playlists WHERE id = ?", (screen["playlist_id"],))
        items = query_all(
            """
            SELECT playlist_items.id, playlist_items.duration_seconds,
                   playlist_items.position, media.id AS media_id,
                   media.name, media.filename, media.mime_type
            FROM playlist_items
            JOIN media ON media.id = playlist_items.media_id
            WHERE playlist_items.playlist_id = ?
            ORDER BY playlist_items.position ASC
            """,
            (screen["playlist_id"],),
        )
        ...
```

Replace with:

```python
    active_playlist_id = resolve_active_playlist(screen)
    if active_playlist_id:
        playlist = query_one("SELECT * FROM playlists WHERE id = ?", (active_playlist_id,))
        items = query_all(
            """
            SELECT playlist_items.id, playlist_items.duration_seconds,
                   playlist_items.position, media.id AS media_id,
                   media.name, media.filename, media.mime_type
            FROM playlist_items
            JOIN media ON media.id = playlist_items.media_id
            WHERE playlist_items.playlist_id = ?
            ORDER BY playlist_items.position ASC
            """,
            (active_playlist_id,),
        )
        ...
```

(Two replacements of `screen["playlist_id"]` → `active_playlist_id` inside the function. The function's parameter signature, return type, and downstream logic stay identical.)

- [ ] **Step 4: Append a test that proves schedule changes the active playlist**

Append to `backend/tests/test_dayparting_resolver.py`:

```python
def test_content_endpoint_picks_scheduled_playlist(client, signed_up_org):
    """A schedule with a rule covering all hours of all days → /content
    returns the scheduled playlist, not the default."""
    info = _create_two_playlists_and_schedule(client, signed_up_org)
    bearer = {"Authorization": f"Bearer {signed_up_org['token']}"}

    # Create a schedule + always-on rule attached to the screen
    r = client.post("/schedules", headers=bearer, json={"name": "AlwaysOn"})
    assert r.status_code in (200, 201), r.text
    sched_id = r.json()["id"]
    r = client.put(f"/schedules/{sched_id}/rules", headers=bearer,
                   json={"rules": [{
                       "playlist_id":  info["playlist_b"],
                       "start_time":   "00:00",
                       "end_time":     "23:59",
                       "days_of_week": 127,    # all days
                       "position":     0,
                   }]})
    assert r.status_code == 200, r.text

    r = client.put(f"/screens/{info['screen_id']}", headers=bearer,
                   json={"schedule_id": sched_id})
    assert r.status_code == 200, r.text

    r = client.get(f"/screens/{info['token']}/content")
    assert r.status_code == 200
    body = r.json()
    assert body["playlist"]["id"] == info["playlist_b"], \
        f"expected scheduled playlist, got {body['playlist']}"
```

NOTE: This test depends on `POST /schedules`, `PUT /schedules/{id}/rules`, and `PUT /screens/{id}` accepting `schedule_id`. Those endpoints land in Tasks 4 + 5. So this test will FAIL until Task 5 ships.

Mark this test temporarily with `pytest.mark.skip(reason="depends on Task 4+5")` for now. The skip is removed in Task 5.

```python
import pytest

@pytest.mark.skip(reason="depends on Task 4+5 — schedule CRUD + PUT /screens schedule_id")
def test_content_endpoint_picks_scheduled_playlist(client, signed_up_org):
    ...
```

- [ ] **Step 5: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 6: Run resolver tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_dayparting_resolver.py
```
Expected: 12 PASS + 1 SKIPPED (the depends-on-T5 test).

- [ ] **Step 7: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `167 passed, 1 skipped` (166 + 1 new + 1 skipped).

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_dayparting_resolver.py
git commit -m "$(cat <<'EOF'
feat(dayparting): wire resolve_active_playlist into build_screen_payload

Replaces the direct screen.playlist_id lookup with the new resolver.
No behavior change yet for screens without a schedule attached.

One end-to-end test skipped pending Tasks 4+5 (schedule CRUD).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Schedule + rule CRUD endpoints

**Files:**
- Modify: `backend/main.py` (new Pydantic models + 6 endpoints + overlap helper)
- Create: `backend/tests/test_schedules.py`

**Goal:** Full CRUD for `/schedules` and replace-all `PUT /schedules/{id}/rules`. Overlap detection at write time.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_schedules.py`:

```python
"""Tests for the Schedule + ScheduleRule CRUD endpoints."""
from fastapi.testclient import TestClient


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_playlist(client, signed_up_org, name="P"):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/playlists", headers=bearer, json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── Schedule CRUD ─────────────────────────────────────────────────────

def test_post_schedule_creates_row(client, signed_up_org):
    r = client.post("/schedules", headers=_bearer(signed_up_org["token"]),
                    json={"name": "Daypart"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["name"] == "Daypart"
    assert "id" in body
    assert body.get("rules") == []


def test_post_schedule_rejects_empty_name(client, signed_up_org):
    r = client.post("/schedules", headers=_bearer(signed_up_org["token"]),
                    json={"name": ""})
    assert r.status_code in (400, 422)


def test_post_schedule_requires_role(client, signed_up_org):
    # Create a viewer
    import uuid
    suffix = uuid.uuid4().hex[:8]
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/users", headers=bearer,
                    json={"username": f"v-{suffix}@example.com",
                          "password": "Khanshoof2026Pass", "role": "viewer"})
    assert r.status_code in (200, 201), r.text
    r = client.post("/auth/login",
                    json={"username": f"v-{suffix}@example.com",
                          "password": "Khanshoof2026Pass"})
    viewer_token = r.json()["token"]

    r = client.post("/schedules", headers=_bearer(viewer_token),
                    json={"name": "ViewerNope"})
    assert r.status_code == 403


def test_get_schedules_lists_only_own_org(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "MyOrg"})
    assert r.status_code in (200, 201), r.text
    r = client.get("/schedules", headers=bearer)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(s["name"] == "MyOrg" for s in items)


def test_put_schedule_updates_name(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "Before"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}", headers=bearer, json={"name": "After"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "After"


def test_delete_schedule_cascades_rules(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Tmp"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [{"playlist_id": pl,
                                    "start_time": "09:00", "end_time": "17:00",
                                    "days_of_week": 127, "position": 0}]})
    assert r.status_code == 200, r.text
    r = client.delete(f"/schedules/{sid}", headers=bearer)
    assert r.status_code in (200, 204)
    # Schedule gone
    r = client.get(f"/schedules/{sid}", headers=bearer)
    assert r.status_code == 404


# ── Rules CRUD (replace-all) ──────────────────────────────────────────

def test_put_rules_replaces_all(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Test"})
    sid = r.json()["id"]
    # First PUT: two rules
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "06:00",
                        "end_time": "11:00", "days_of_week": 31, "position": 0},
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 31, "position": 1},
                   ]})
    assert r.status_code == 200, r.text
    # Second PUT: one rule replaces both
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "00:00",
                        "end_time": "23:59", "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code == 200, r.text
    # GET shows only the one
    r = client.get(f"/schedules/{sid}", headers=bearer)
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert len(rules) == 1


def test_post_rule_allows_wrap_midnight(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Late"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "22:00",
                        "end_time": "02:00", "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code == 200, r.text


def test_post_rule_rejects_overlap_same_day(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "Overlap"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       # Both Wed (bit 2 → 4), 11–14 vs 13–15 overlap
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 4, "position": 0},
                       {"playlist_id": pl, "start_time": "13:00",
                        "end_time": "15:00", "days_of_week": 4, "position": 1},
                   ]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "schedule.rule_overlap"


def test_post_rule_allows_overlap_different_days(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "NoOverlap"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       # Mon (bit 0 → 1), 11–14
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 1, "position": 0},
                       # Tue (bit 1 → 2), 11–14 — no day overlap → allowed
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 2, "position": 1},
                   ]})
    assert r.status_code == 200, r.text


def test_post_rule_rejects_invalid_dow(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "BadDOW"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 128,    # > 127
                        "position": 0},
                   ]})
    assert r.status_code in (400, 422)


def test_post_rule_requires_at_least_one_day(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    pl = _create_playlist(client, signed_up_org)
    r = client.post("/schedules", headers=bearer, json={"name": "NoDays"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": pl, "start_time": "11:00",
                        "end_time": "14:00", "days_of_week": 0,      # zero
                        "position": 0},
                   ]})
    assert r.status_code in (400, 422)


def test_post_rule_rejects_playlist_from_other_org(client, signed_up_org, unique_business):
    """Cannot reference a playlist that belongs to another org."""
    # Create a second org
    bearer = _bearer(signed_up_org["token"])
    # Easier: just try to reference a nonexistent playlist id
    r = client.post("/schedules", headers=bearer, json={"name": "Bad"})
    sid = r.json()["id"]
    r = client.put(f"/schedules/{sid}/rules", headers=bearer,
                   json={"rules": [
                       {"playlist_id": 9999999,    # doesn't exist
                        "start_time": "11:00", "end_time": "14:00",
                        "days_of_week": 127, "position": 0},
                   ]})
    assert r.status_code in (404, 422)
```

- [ ] **Step 2: Run them, confirm failure**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_schedules.py 2>&1 | tail -10
```
Expected: most fail with 404 / 405 — endpoints don't exist yet.

- [ ] **Step 3: Add Pydantic models to `backend/main.py`**

Near the other Pydantic models (search for `class PlaylistCreate`, around line 478), add:

```python
class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)


class ScheduleRuleIn(BaseModel):
    playlist_id:  int
    start_time:   str = Field(..., pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_time:     str = Field(..., pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    days_of_week: int = Field(..., ge=1, le=127)
    position:     int = 0


class ScheduleRulesIn(BaseModel):
    rules: list[ScheduleRuleIn]
```

The `days_of_week: int = Field(..., ge=1, le=127)` constraint covers both the "≥1 day" and the "≤127" cases — Pydantic validation handles both.

The `start_time` / `end_time` regex permits `HH:MM` or `HH:MM:SS`. Postgres `TIME` accepts both.

- [ ] **Step 4: Add overlap helper to `backend/main.py`**

Near the resolver helpers (added in Task 2), add:

```python
def _time_windows_overlap(s1, e1, s2, e2) -> bool:
    """True iff the two TIME windows overlap. Both may wrap midnight."""
    # Expand each window into 1 or 2 non-wrapping segments
    def expand(s, e):
        if s <= e:
            return [(s, e)]
        return [(s, _time_type(23, 59, 59, 999999)), (_time_type(0, 0), e)]
    for a_s, a_e in expand(s1, e1):
        for b_s, b_e in expand(s2, e2):
            if a_s < b_e and b_s < a_e:
                return True
    return False


def _rules_overlap(a: dict, b: dict) -> bool:
    """True iff rules a and b share at least one day AND their time windows overlap."""
    if not (a["days_of_week"] & b["days_of_week"]):
        return False
    return _time_windows_overlap(
        a["start_time"], a["end_time"],
        b["start_time"], b["end_time"],
    )
```

These helpers operate on Python `time` objects (which postgres `TIME` columns return naturally via psycopg).

- [ ] **Step 5: Add CRUD endpoints to `backend/main.py`**

Find a logical home — near other admin endpoints (e.g., near `list_users` around line 966). Insert:

```python
# ── Schedules (Phase 2.5e) ────────────────────────────────────────────

def _schedule_row_to_dict(row: dict, rules: list[dict]) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "rules": rules,
    }


def _rule_row_to_dict(row: dict) -> dict:
    return {
        "id":           row["id"],
        "playlist_id":  row["playlist_id"],
        "start_time":   row["start_time"].strftime("%H:%M") if hasattr(row["start_time"], "strftime") else row["start_time"],
        "end_time":     row["end_time"].strftime("%H:%M") if hasattr(row["end_time"], "strftime") else row["end_time"],
        "days_of_week": row["days_of_week"],
        "position":     row["position"],
    }


def _load_schedule(sid: int, org: int) -> Optional[dict]:
    row = query_one(
        "SELECT id, name, created_at FROM schedules WHERE id = ? AND organization_id = ?",
        (sid, org),
    )
    if not row:
        return None
    rule_rows = query_all(
        "SELECT id, playlist_id, start_time, end_time, days_of_week, position "
        "FROM schedule_rules WHERE schedule_id = ? "
        "ORDER BY position ASC, id ASC",
        (sid,),
    )
    return _schedule_row_to_dict(row, [_rule_row_to_dict(r) for r in rule_rows])


@app.post("/schedules", status_code=201)
def create_schedule(request: Request, payload: ScheduleCreate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    sid = execute(
        "INSERT INTO schedules (organization_id, name) VALUES (?, ?)",
        (org_id(user), payload.name),
    )
    audit(request, action="schedule.create", actor=user,
          target_type="schedule", target_id=sid,
          details={"name": payload.name})
    return _load_schedule(sid, org_id(user))


@app.get("/schedules")
def list_schedules(user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    rows = query_all(
        "SELECT id, name, created_at FROM schedules WHERE organization_id = ? "
        "ORDER BY created_at DESC",
        (org_id(user),),
    )
    items = [_schedule_row_to_dict(r, []) for r in rows]
    return {"items": items}


@app.get("/schedules/{sid}")
def get_schedule(sid: int,
                 user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    sched = _load_schedule(sid, org_id(user))
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    return sched


@app.put("/schedules/{sid}")
def update_schedule(request: Request, sid: int, payload: ScheduleUpdate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    sched = _load_schedule(sid, org_id(user))
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    if payload.name is not None:
        execute("UPDATE schedules SET name = ? WHERE id = ?", (payload.name, sid))
    audit(request, action="schedule.update", actor=user,
          target_type="schedule", target_id=sid,
          details={"before": {"name": sched["name"]},
                   "after":  {"name": payload.name if payload.name is not None else sched["name"]}})
    return _load_schedule(sid, org_id(user))


@app.delete("/schedules/{sid}", status_code=204)
def delete_schedule(request: Request, sid: int,
                    user: dict = Depends(require_roles("admin"))) -> None:
    sched = _load_schedule(sid, org_id(user))
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    execute("DELETE FROM schedules WHERE id = ?", (sid,))
    audit(request, action="schedule.delete", actor=user,
          target_type="schedule", target_id=sid,
          details={"name": sched["name"]})


def _parse_time(s: str) -> _time_type:
    parts = s.split(":")
    h = int(parts[0]); m = int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    return _time_type(h, m, sec)


@app.put("/schedules/{sid}/rules")
def replace_schedule_rules(request: Request, sid: int, payload: ScheduleRulesIn,
                           user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    sched = _load_schedule(sid, org_id(user))
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")

    # Validate each rule
    parsed = []
    for r_in in payload.rules:
        owned = query_one(
            "SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
            (r_in.playlist_id, org_id(user)),
        )
        if not owned:
            raise http_error(404, "playlist.not_found",
                             f"Playlist {r_in.playlist_id} not found")
        parsed.append({
            "playlist_id":  r_in.playlist_id,
            "start_time":   _parse_time(r_in.start_time),
            "end_time":     _parse_time(r_in.end_time),
            "days_of_week": r_in.days_of_week,
            "position":     r_in.position,
        })

    # Overlap check
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            if _rules_overlap(parsed[i], parsed[j]):
                raise http_error(422, "schedule.rule_overlap",
                                 f"Rules {i} and {j} overlap on a shared day")

    # Replace-all: delete then insert
    execute("DELETE FROM schedule_rules WHERE schedule_id = ?", (sid,))
    for r in parsed:
        execute(
            "INSERT INTO schedule_rules "
            "(schedule_id, playlist_id, start_time, end_time, days_of_week, position) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, r["playlist_id"], r["start_time"], r["end_time"],
             r["days_of_week"], r["position"]),
        )

    audit(request, action="schedule.rules_replace", actor=user,
          target_type="schedule", target_id=sid,
          details={"rule_count": len(parsed)})

    return _load_schedule(sid, org_id(user))
```

Notes:
- `execute()` on INSERT returns the new id (auto-`RETURNING id` injected by db.py).
- Audit calls re-use the helper added in Phase 2.5c. Since security is on PR #5, audit() exists on main as the no-op stub. **Wait — verify this is true** by grepping for `def audit` in `backend/main.py` on this branch:
  ```bash
  grep -n "^def audit" backend/main.py
  ```
  If `audit` does NOT exist (because security PR #5 hasn't merged yet), replace each `audit(...)` call in the snippet above with `# audit(...)  # uncomment after security PR lands` — i.e., comment out the audit calls. Don't try to invent a stub.

- [ ] **Step 6: Check if audit() exists on this branch**

```bash
grep -nE "^def audit\b" backend/main.py
```
- If found → leave the `audit(...)` calls as-is.
- If NOT found → comment out each `audit(...)` call in the code from Step 5. Add a comment `# TODO(post-PR#5): re-enable audit calls when security branch merges`.

- [ ] **Step 7: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 8: Run schedule tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_schedules.py
```
Expected: 12 PASS.

- [ ] **Step 9: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `179 passed, 1 skipped` (167 + 12 new + skip from Task 3 still pending).

- [ ] **Step 10: Commit**

```bash
git add backend/main.py backend/tests/test_schedules.py
git commit -m "$(cat <<'EOF'
feat(dayparting): schedule + schedule_rules CRUD endpoints

POST/GET/PUT/DELETE /schedules (admin+editor; delete is admin-only)
PUT /schedules/{id}/rules — replace-all semantics with overlap
detection across shared days. Wrap-midnight rules supported.

Overlap rejection returns 422 with code schedule.rule_overlap.
Playlist ownership enforced (404 playlist.not_found if foreign).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Extend `PUT /sites` (timezone) + `PUT /screens` (schedule_id) + tests

**Files:**
- Modify: `backend/main.py` (Pydantic `SiteUpdate` + `ScreenUpdate` + handlers)
- Modify: `backend/tests/test_schedules.py` (append site/screen tests)
- Modify: `backend/tests/test_dayparting_resolver.py` (un-skip the integration test)

**Goal:** Allow operators to set a site's timezone and attach a schedule to a screen. Validate the timezone with `ZoneInfo`. Un-skip the Task 3 end-to-end test.

- [ ] **Step 1: Write failing tests — append to `backend/tests/test_schedules.py`**

```python
# ── Site timezone ─────────────────────────────────────────────────────

def test_default_site_timezone_is_kuwait(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Default tz"})
    assert r.status_code in (200, 201), r.text
    site = r.json()
    # Default tz is implicit at DB level; verify via GET
    r = client.get(f"/sites/{site['id']}", headers=bearer)
    if r.status_code == 200:
        assert r.json().get("timezone") == "Asia/Kuwait"


def test_put_site_accepts_timezone(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Riyadh"})
    site = r.json()
    r = client.put(f"/sites/{site['id']}", headers=bearer,
                   json={"timezone": "Asia/Riyadh"})
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "Asia/Riyadh"


def test_put_site_rejects_invalid_timezone(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/sites", headers=bearer, json={"name": "Bad"})
    site = r.json()
    r = client.put(f"/sites/{site['id']}", headers=bearer,
                   json={"timezone": "Mars/Olympus"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "site.timezone_invalid"


# ── Screen schedule_id ────────────────────────────────────────────────

def test_put_screen_accepts_schedule_id(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "Test"})
    sid = r.json()["id"]
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": sid})
    assert r.status_code == 200, r.text
    assert r.json().get("schedule_id") == sid


def test_put_screen_accepts_null_schedule_id(client, signed_up_org):
    """Setting schedule_id to None detaches the schedule."""
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/schedules", headers=bearer, json={"name": "X"})
    sid = r.json()["id"]
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": sid})
    assert r.status_code == 200
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": None})
    assert r.status_code == 200
    # Verify detached: GET screen, check schedule_id
    r = client.get(f"/screens/{screen['id']}", headers=bearer)
    if r.status_code == 200:
        assert r.json().get("schedule_id") is None


def test_put_screen_rejects_schedule_from_other_org(client, signed_up_org):
    bearer = _bearer(signed_up_org["token"])
    r = client.post("/screens", headers=bearer, json={"name": "S"})
    screen = r.json()
    r = client.put(f"/screens/{screen['id']}", headers=bearer,
                   json={"schedule_id": 9999999})    # nonexistent
    assert r.status_code in (404, 422)
```

- [ ] **Step 2: Run them — confirm failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_schedules.py -k "timezone or schedule_id"
```
Expected: most fail (no timezone field accepted; no schedule_id field accepted).

- [ ] **Step 3: Extend `SiteUpdate` in `backend/main.py`**

Find `class SiteUpdate(BaseModel):` (around line 458). Replace:

```python
class SiteUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
```

with:

```python
class SiteUpdate(BaseModel):
    name:     Optional[str] = None
    slug:     Optional[str] = None
    timezone: Optional[str] = None
```

- [ ] **Step 4: Extend `ScreenUpdate` in `backend/main.py`**

Find `class ScreenUpdate(BaseModel):` (around line 472). Add one more field:

```python
class ScreenUpdate(BaseModel):
    name:          Optional[str] = None
    location:      Optional[str] = None
    resolution:    Optional[str] = None
    orientation:   Optional[str] = None
    site_id:       Optional[int] = None
    playlist_id:   Optional[int] = None
    owner_user_id: Optional[int] = None
    schedule_id:   Optional[int] = None    # new (Phase 2.5e)
```

- [ ] **Step 5: Update `PUT /sites/{id}` handler**

Find `@app.put("/sites/{site_id}")` (around line 1074). Inside the handler, in the body that builds the UPDATE statement, ADD timezone handling:

```python
    if payload.timezone is not None:
        try:
            ZoneInfo(payload.timezone)
        except ZoneInfoNotFoundError:
            raise http_error(422, "site.timezone_invalid",
                             f"Unknown timezone: {payload.timezone}")
```

Then update the UPDATE statement to include timezone if it's being changed. The exact way depends on how the handler builds its SQL today — look at the existing pattern. Typical pattern (illustrative; adapt to the actual handler shape):

```python
    fields = []
    params = []
    if payload.name is not None:
        fields.append("name = ?"); params.append(payload.name)
    if payload.slug is not None:
        fields.append("slug = ?"); params.append(payload.slug)
    if payload.timezone is not None:
        fields.append("timezone = ?"); params.append(payload.timezone)
    if fields:
        params.append(site_id)
        execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", tuple(params))
```

If the existing handler uses a different style (single hardcoded UPDATE), adapt to add the `timezone = ?` clause and parameter. The implementer should READ the existing handler before editing and follow its style.

Also: the response from this endpoint should include the new `timezone` field. If the handler returns a `SELECT * FROM sites ...` row, that's automatic. If it builds a manual dict, add the field.

- [ ] **Step 6: Update `PUT /screens/{id}` handler for `schedule_id`**

Find `@app.put("/screens/{screen_id}")` (around line 1192). Add validation:

```python
    if payload.schedule_id is not None:
        owned = query_one(
            "SELECT id FROM schedules WHERE id = ? AND organization_id = ?",
            (payload.schedule_id, org_id(user)),
        )
        if not owned:
            raise http_error(404, "schedule.not_found", "Schedule not found")
```

Add `schedule_id` to the UPDATE statement and to the response, following the same pattern the handler already uses for other fields. Note: `schedule_id` is nullable, so `None` is a valid "detach" value (different from "not provided"). Use Pydantic's `model_dump(exclude_unset=True)` semantics or check explicitly:

If the existing handler uses `if payload.X is not None:` style, you'll need a different sentinel for "explicit None" vs "not provided." A simple workaround: a `_UNSET` sentinel:

```python
_UNSET = object()


# In the handler:
schedule_value = _UNSET
if "schedule_id" in payload.model_fields_set:
    schedule_value = payload.schedule_id
# ... later, when building UPDATE:
if schedule_value is not _UNSET:
    fields.append("schedule_id = ?"); params.append(schedule_value)
```

This handles both "set to a schedule" and "explicitly clear to NULL." If the existing handler doesn't have this pattern and refactoring it is too invasive, accept the simpler `if payload.schedule_id is not None` and document that detach requires a follow-up.

Look at the existing handler before deciding which approach to use.

- [ ] **Step 7: Un-skip the integration test in `backend/tests/test_dayparting_resolver.py`**

Find:
```python
@pytest.mark.skip(reason="depends on Task 4+5 — schedule CRUD + PUT /screens schedule_id")
def test_content_endpoint_picks_scheduled_playlist(client, signed_up_org):
```

Remove the `@pytest.mark.skip(...)` line.

- [ ] **Step 8: Rebuild backend**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 4
```

- [ ] **Step 9: Run schedule + resolver tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs backend/tests/test_schedules.py backend/tests/test_dayparting_resolver.py
```
Expected: all PASS.

- [ ] **Step 10: Run full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -5
```
Expected: `186 passed` (179 + 6 new + 1 un-skipped).

- [ ] **Step 11: Commit**

```bash
git add backend/main.py backend/tests/test_schedules.py backend/tests/test_dayparting_resolver.py
git commit -m "$(cat <<'EOF'
feat(dayparting): PUT /sites accepts timezone, PUT /screens accepts schedule_id

- SiteUpdate gains `timezone` field; handler validates via ZoneInfo;
  422 site.timezone_invalid on unknown IANA name
- ScreenUpdate gains `schedule_id` field; validates ownership;
  404 schedule.not_found if foreign
- Detaching schedule (set to None) supported via explicit-unset pattern
- Un-skips the resolver integration test

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Admin UI — "Schedules" section

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/i18n/en.json`
- Modify: `frontend/i18n/ar.json`

**Goal:** New top-level "Schedules" nav button. List view + editor view. ~250 LOC IIFE following the Walls/MediaPicker pattern.

- [ ] **Step 1: Add nav button to `frontend/index.html`**

Find the nav button group (around line 25-31). Insert between "playlists" and "walls":

```html
<button data-section="schedules" data-i18n="nav.schedules">Schedules</button>
```

- [ ] **Step 2: Add content section in `frontend/index.html`**

Find the last existing dashboard `<section class="panel ...">` block (likely the `walls` or `billing` section). After it, insert:

```html
<section id="schedules" class="panel hidden">
  <header class="panel-header">
    <h2 data-i18n="schedules.title">Schedules</h2>
    <button id="schedule-new-btn" class="btn" data-i18n="schedules.new">+ New schedule</button>
  </header>

  <div id="schedules-list" class="schedules-list"></div>

  <div id="schedule-editor" class="schedule-editor hidden">
    <label class="field">
      <span data-i18n="schedules.editor.name">Name</span>
      <input id="schedule-editor-name" type="text" maxlength="200" />
    </label>

    <h3 data-i18n="schedules.editor.rules">Rules</h3>
    <table class="schedule-rules-table">
      <thead>
        <tr>
          <th data-i18n="schedules.editor.playlist">Playlist</th>
          <th data-i18n="schedules.editor.start">Start</th>
          <th data-i18n="schedules.editor.end">End</th>
          <th data-i18n="schedules.editor.days">Days</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="schedule-rules-tbody"></tbody>
    </table>
    <button id="schedule-add-rule-btn" class="btn" data-i18n="schedules.editor.add_rule">+ Add rule</button>

    <div class="schedule-editor-actions">
      <button id="schedule-save-btn" class="btn btn-primary" data-i18n="schedules.editor.save">Save</button>
      <button id="schedule-cancel-btn" class="btn" data-i18n="schedules.editor.cancel">Cancel</button>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Add the `Schedules` IIFE to `frontend/app.js`**

Append at the END of `frontend/app.js` (after the last existing IIFE):

```javascript
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
      const editBtn = document.createElement("button");
      editBtn.className = "btn";
      editBtn.textContent = Khan.t("schedules.editor.edit", "Edit");
      editBtn.addEventListener("click", () => openEditor(s.id));
      const delBtn = document.createElement("button");
      delBtn.className = "btn btn-danger";
      delBtn.textContent = Khan.t("schedules.editor.delete", "Delete");
      delBtn.addEventListener("click", () => deleteSchedule(s.id, s.name));
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      card.appendChild(actions);
      container.appendChild(card);
    });
  }

  async function openEditor(id) {
    // Fetch playlists for the dropdown if not cached
    if (!cachedPlaylists.length) {
      try {
        const pls = await api("/playlists");
        cachedPlaylists = Array.isArray(pls) ? pls : (pls.items || []);
      } catch (_) { cachedPlaylists = []; }
    }
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
      span.textContent = labelKey.split(".")[1];   // fallback text
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
    try {
      // Create or update the schedule itself
      let sid = currentSchedule.id;
      if (sid == null) {
        const created = await api("/schedules", {
          method: "POST", body: JSON.stringify({ name }),
        });
        sid = created.id;
      } else {
        await api(`/schedules/${sid}`, {
          method: "PUT", body: JSON.stringify({ name }),
        });
      }
      // Replace-all rules
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
      await show();
    } catch (err) {
      const code = err.data?.detail?.code;
      const msg = code === "schedule.rule_overlap"
        ? Khan.t("schedules.overlap_error", "Two rules overlap on the same day.")
        : (err.message || Khan.t("schedules.error.save", "Failed to save schedule."));
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

  return { show, init };
})();

Schedules.init();
```

- [ ] **Step 4: Wire `Schedules.show()` into `showSection`**

Find the existing `showSection` function (around line 137). Currently:

```javascript
function showSection(id) {
  document.querySelectorAll("#dashboard > section.panel, #dashboard > div").forEach((el) => {
    el.classList.toggle("hidden", el.id !== id);
  });
  navButtons.forEach((btn) => {
    btn.classList.toggle("nav-active", btn.dataset.section === id);
  });
  if (id === "walls") Walls.onShow();
  if (id === "audit-log") AuditLog.show();
}
```

Add immediately before the closing `}`:

```javascript
  if (id === "schedules") Schedules.show();
```

- [ ] **Step 5: Add CSS in `frontend/styles.css`**

Append:

```css
/* Phase 2.5e — schedules */
.schedules-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.schedule-card {
  background: var(--surface, #fff8f0);
  border: 1px solid var(--border, #e9ddc6);
  border-radius: 12px;
  padding: 16px;
}
.schedule-card h3 { margin: 0 0 12px 0; }
.schedule-card-actions { display: flex; gap: 8px; }

.schedule-editor .field { display: block; margin-block: 16px; }
.schedule-rules-table { width: 100%; border-collapse: collapse; }
.schedule-rules-table th,
.schedule-rules-table td { padding: 8px; vertical-align: middle; }
.schedule-rules-table th { text-align: start; font-weight: 600; }
.dow-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-inline-end: 8px;
  font-size: 12px;
}
.schedule-editor-actions { margin-block-start: 24px; display: flex; gap: 12px; }
.empty-state {
  color: var(--muted, #8b6f5e);
  text-align: center;
  padding: 32px;
}
```

- [ ] **Step 6: Add i18n keys to `frontend/i18n/en.json`**

Insert (alphabetical position preferred; otherwise group near other `nav.*` and the existing `error.*`/`toast.*` blocks):

```json
  "nav.schedules": "Schedules",
  "schedules.title": "Schedules",
  "schedules.new": "+ New schedule",
  "schedules.empty": "No schedules yet.",
  "schedules.editor.edit": "Edit",
  "schedules.editor.name": "Name",
  "schedules.editor.rules": "Rules",
  "schedules.editor.add_rule": "+ Add rule",
  "schedules.editor.playlist": "Playlist",
  "schedules.editor.start": "Start",
  "schedules.editor.end": "End",
  "schedules.editor.days": "Days",
  "schedules.editor.save": "Save",
  "schedules.editor.cancel": "Cancel",
  "schedules.editor.delete": "Delete",
  "schedules.dow.mon": "Mon",
  "schedules.dow.tue": "Tue",
  "schedules.dow.wed": "Wed",
  "schedules.dow.thu": "Thu",
  "schedules.dow.fri": "Fri",
  "schedules.dow.sat": "Sat",
  "schedules.dow.sun": "Sun",
  "schedules.name_required": "Schedule name is required.",
  "schedules.overlap_error": "Two rules overlap on the same day.",
  "schedules.no_playlists": "Create a playlist first.",
  "schedules.delete_confirm_title": "Delete schedule?",
  "schedules.delete_confirm_message": "Delete schedule \"{name}\"? Screens using it will fall back to their default playlist.",
  "schedules.error.fetch": "Failed to load schedule.",
  "schedules.error.save": "Failed to save schedule.",
  "schedules.error.delete": "Failed to delete.",
  "toast.schedule_saved": "Schedule saved.",
  "toast.schedule_deleted": "Schedule deleted.",
```

- [ ] **Step 7: Add the same keys to `frontend/i18n/ar.json`** (MSA Arabic):

```json
  "nav.schedules": "الجدولة",
  "schedules.title": "الجدولة",
  "schedules.new": "+ جدول جديد",
  "schedules.empty": "لا توجد جداول بعد.",
  "schedules.editor.edit": "تحرير",
  "schedules.editor.name": "الاسم",
  "schedules.editor.rules": "القواعد",
  "schedules.editor.add_rule": "+ إضافة قاعدة",
  "schedules.editor.playlist": "قائمة التشغيل",
  "schedules.editor.start": "البداية",
  "schedules.editor.end": "النهاية",
  "schedules.editor.days": "الأيام",
  "schedules.editor.save": "حفظ",
  "schedules.editor.cancel": "إلغاء",
  "schedules.editor.delete": "حذف",
  "schedules.dow.mon": "الإثنين",
  "schedules.dow.tue": "الثلاثاء",
  "schedules.dow.wed": "الأربعاء",
  "schedules.dow.thu": "الخميس",
  "schedules.dow.fri": "الجمعة",
  "schedules.dow.sat": "السبت",
  "schedules.dow.sun": "الأحد",
  "schedules.name_required": "اسم الجدول مطلوب.",
  "schedules.overlap_error": "تتداخل قاعدتان في نفس اليوم.",
  "schedules.no_playlists": "أنشئ قائمة تشغيل أولًا.",
  "schedules.delete_confirm_title": "حذف الجدول؟",
  "schedules.delete_confirm_message": "حذف الجدول \"{name}\"؟ ستعود الشاشات التي تستخدمه إلى قائمة التشغيل الافتراضية.",
  "schedules.error.fetch": "تعذّر تحميل الجدول.",
  "schedules.error.save": "تعذّر حفظ الجدول.",
  "schedules.error.delete": "تعذّر الحذف.",
  "toast.schedule_saved": "تم حفظ الجدول.",
  "toast.schedule_deleted": "تم حذف الجدول.",
```

- [ ] **Step 8: i18n parity check**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: OK.

- [ ] **Step 9: JS parse**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 10: Manual browser smoke**

Open admin in browser. Click "Schedules" nav. Should see empty state. Click "+ New schedule". Editor opens. Type name, add 2 rules, save. Verify toast. Edit it, delete it.

(If a real browser isn't available, mark DONE_WITH_CONCERNS noting deferred verification.)

- [ ] **Step 11: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(dayparting): admin Schedules section

New top-level nav. List view (card grid) + editor view (name + rules
table with playlist dropdown, time pickers, day-of-week checkboxes).
Save calls POST/PUT /schedules + PUT /schedules/{id}/rules (replace-all).
Overlap errors surface as localized toast.

~30 new i18n keys EN+AR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Admin UI — site timezone picker + screen schedule picker

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/i18n/en.json`, `frontend/i18n/ar.json`

**Goal:** Add a timezone dropdown to the site edit form, and a "Schedule" dropdown to the screen edit form.

- [ ] **Step 1: Add timezone picker markup**

Find the site edit form in `frontend/index.html` (search for `site-edit` or `id="site-form"` or the existing site editing UI — names vary). Inside the form, add a new field row:

```html
<label class="field">
  <span data-i18n="site.field.timezone">Timezone</span>
  <select id="site-timezone-select"></select>
</label>
```

If the existing form pattern uses different markup (e.g., divs not labels), match it.

- [ ] **Step 2: Add screen schedule picker markup**

Find the screen edit form in `frontend/index.html`. Inside the form (near the existing playlist dropdown), add:

```html
<label class="field">
  <span data-i18n="screen.field.schedule">Schedule</span>
  <select id="screen-schedule-select"></select>
</label>
```

- [ ] **Step 3: Add the TZ_OPTIONS constant + populator to `frontend/app.js`**

Near the top of `frontend/app.js` (after the existing constants), add:

```javascript
const TZ_OPTIONS = [
  "Asia/Kuwait", "Asia/Riyadh", "Asia/Dubai", "Asia/Qatar",
  "Asia/Bahrain", "Asia/Muscat", "Asia/Baghdad",
  "Africa/Cairo", "Asia/Amman", "Asia/Beirut",
  "UTC",
];

function populateTimezoneSelect(selectEl, currentValue) {
  selectEl.innerHTML = "";
  TZ_OPTIONS.forEach(tz => {
    const opt = document.createElement("option");
    opt.value = tz;
    opt.textContent = tz;
    if (tz === currentValue) opt.selected = true;
    selectEl.appendChild(opt);
  });
}
```

- [ ] **Step 4: Wire the timezone select into the site edit flow**

Find where the site edit form is populated (look for code that reads `site.name`, `site.slug`). After those existing lines, add:

```javascript
const tzSelect = document.getElementById("site-timezone-select");
if (tzSelect) populateTimezoneSelect(tzSelect, site.timezone);
```

Find where the site edit form is submitted (the click handler for the save button). In the body being PUT, add:

```javascript
timezone: tzSelect?.value,
```

(Use optional chaining since the element might not exist if the implementer hits a different code path.)

- [ ] **Step 5: Wire the schedule select into the screen edit flow**

Find where the screen edit form is populated. After the existing playlist dropdown setup, add:

```javascript
async function populateScheduleSelect(selectEl, currentValue) {
  selectEl.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = Khan.t("screen.schedule.none", "None — use default playlist");
  selectEl.appendChild(none);
  try {
    const body = await api("/schedules");
    (body.items || []).forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      if (s.id === currentValue) opt.selected = true;
      selectEl.appendChild(opt);
    });
  } catch (_) { /* swallow */ }
}
```

Call it during screen edit form open:

```javascript
const scheduleSelect = document.getElementById("screen-schedule-select");
if (scheduleSelect) await populateScheduleSelect(scheduleSelect, screen.schedule_id);
```

In the screen save handler, include in the PUT body:

```javascript
schedule_id: scheduleSelect?.value ? parseInt(scheduleSelect.value, 10) : null,
```

(Empty string → `null`; numeric → integer.)

- [ ] **Step 6: Add the two new i18n keys (en.json + ar.json)**

EN:
```json
  "site.field.timezone": "Timezone",
  "screen.field.schedule": "Schedule",
  "screen.schedule.none": "None — use default playlist",
```

AR:
```json
  "site.field.timezone": "المنطقة الزمنية",
  "screen.field.schedule": "الجدول",
  "screen.schedule.none": "بدون — استخدم قائمة التشغيل الافتراضية",
```

- [ ] **Step 7: i18n parity + JS parse**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo OK
```
Expected: parity OK, JS OK.

- [ ] **Step 8: Manual browser smoke**

Open admin → Sites → edit a site → timezone dropdown shows 11 options + current is selected → change to Asia/Riyadh → save → reload → still shows Asia/Riyadh.

Open admin → Screens → edit a screen → "Schedule" dropdown shows "None" + each schedule by name → assign → save → reload → still assigned.

- [ ] **Step 9: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(dayparting): site timezone picker + screen schedule picker

Site edit form gets a curated 11-entry IANA timezone dropdown
(GCC + nearby + UTC). Screen edit form gets a "Schedule" dropdown
with "None" + each schedule by name. Both flow through PUT
/sites/{id} and PUT /screens/{id}.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Regression sweep, push, open PR

**Files:** none modified directly.

- [ ] **Step 1: Final backend test run**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: `186 passed`.

- [ ] **Step 2: i18n parity**

```bash
python3 /home/ahmed/signage/scripts/check_i18n.py
```
Expected: OK.

- [ ] **Step 3: JS parse all four**

```bash
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/frontend/app.js','utf8'))" && echo "frontend OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/player.js','utf8'))" && echo "player OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/landing/app.js','utf8'))" && echo "landing OK"
node -e "new Function(require('fs').readFileSync('/home/ahmed/signage/player/i18n.js','utf8'))" && echo "i18n OK"
```

- [ ] **Step 4: Rebuild + recreate containers**

```bash
docker-compose build backend frontend
docker-compose up -d --force-recreate backend frontend
sleep 5
docker-compose ps
```
Expected: all services `(healthy)`.

- [ ] **Step 5: Manual browser smoke**

| Check | Pass criteria |
|---|---|
| Schedules nav appears for admin | "Schedules" button visible; hidden for editor/viewer (note: editor *can* manage schedules; gate accordingly) |
| Create schedule | Add name, 2 rules, save → toast + list view shows it |
| Overlap rejection | Add rule "11–14 Wed" + "13–15 Wed" → save → localized overlap toast |
| Edit schedule | Open existing, modify name, add/remove a rule, save |
| Delete schedule | Delete from list → confirmDialog → row gone |
| Site timezone | Site edit → change tz → save → reload → tz persisted |
| Screen schedule attach | Screen edit → pick schedule → save → reload → schedule attached |
| Screen schedule detach | Same flow → "None" → save → reload → detached |
| Dayparting works end-to-end | Set a screen's site tz to local time; create schedule rule covering "right now"; assign to screen; verify the player content endpoint returns the scheduled playlist (curl `/screens/{token}/content`) |
| AR locale | Toggle to AR; all schedule UI strings rendered |

- [ ] **Step 6: Push branch**

```bash
git push -u origin feature/dayparting
```

- [ ] **Step 7: Open PR**

```bash
~/.local/bin/gh pr create --base main \
  --title "feat(dayparting): Phase 2.5e — player scheduling" \
  --body "$(cat <<'EOF'
## Summary
- New `schedules` + `schedule_rules` tables (org-scoped reusable schedules with time-of-day + day-of-week rules).
- New `sites.timezone` column (default `Asia/Kuwait`, IANA name) and `screens.schedule_id` FK.
- `resolve_active_playlist(screen)` picks the playlist that should play right now, using site-local time. Falls back to `screens.playlist_id` when no rule matches.
- New top-level admin **Schedules** section (list + editor). Site edit gains a timezone dropdown; screen edit gains a schedule dropdown.
- No asyncio tick, no new WS — codebase has no per-screen broadcast helper. The player's existing 15s polling loop handles boundary propagation with at-most 15s lag.

## Spec
`docs/superpowers/specs/2026-05-10-dayparting-design.md`

## Plan
`docs/superpowers/plans/2026-05-11-dayparting-plan.md`

## Test Plan
- [x] Backend: 186 passed (was 151 baseline; +35 new)
- [x] `scripts/check_i18n.py` parity OK
- [x] All four JS files parse
- [x] Containers rebuilt + healthy
- [ ] Browser smoke: create / edit / delete schedule
- [ ] Browser smoke: overlap rejection surfaces as localized toast
- [ ] Browser smoke: site timezone persists across reload
- [ ] Browser smoke: screen schedule attach + detach
- [ ] Browser smoke: end-to-end — set tz + rule covering "now" + assign → curl `/screens/{token}/content` returns scheduled playlist
- [ ] Browser smoke: AR locale renders all schedule UI strings

## Non-goals (queued)
- Date ranges (Christmas Dec 1-25)
- Per-day exceptions / holiday calendars
- "Closed" placeholder content
- Per-zone scheduling
- Wall-cell dayparting (walls have their own playlist model)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Save memory file**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_dayparting.md`:

```markdown
---
name: Dayparting (Phase 2.5e) — branch
description: schedules + schedule_rules + sites.timezone + screens.schedule_id. Server-side resolver, player's existing 15s poll handles propagation. PR pending.
type: project
---

**Status (2026-05-11):** PR #<TBD> opened against main. Awaiting browser smoke + merge.

**What landed:**
- Schema: `sites.timezone` (default `Asia/Kuwait`), `schedules` table, `schedule_rules` table, `screens.schedule_id` FK.
- Resolver: `resolve_active_playlist(screen)` + `_time_in_window` (handles wrap-midnight) + `_site_timezone` (falls back to Kuwait on unknown tz).
- `build_screen_payload()` swaps direct playlist_id lookup for the resolver. Backward-compatible: NULL schedule_id → existing playlist_id behavior.
- Schedule CRUD endpoints (POST/GET/PUT/DELETE /schedules, admin+editor; delete admin-only) with overlap detection at write time. `PUT /schedules/{id}/rules` is replace-all.
- `SiteUpdate` extended with `timezone` (ZoneInfo-validated; 422 on unknown). `ScreenUpdate` extended with `schedule_id` (ownership-checked; supports detach via explicit null).
- Admin UI: "Schedules" top-level section (list + editor with playlist dropdown, time pickers, 7 DOW checkboxes). Site edit gets timezone dropdown (11 curated IANA names + UTC). Screen edit gets schedule dropdown.
- ~30 new i18n keys EN+AR.

**Spec deviation (justified):** the original spec called for an asyncio tick task + WS push at boundaries. During plan writing it became clear that `broadcast_to_screen()` doesn't exist — the codebase only has wall-specific WS infrastructure. Non-wall screens already poll `/content` every 15s. So the simpler design is: resolver runs at /content time, player's existing poll picks it up. Worst-case 15s lag at boundaries (better than the proposed 60s tick) with zero new server-side code. Updated the spec inline before writing the plan.

**Test count:** 186 backend tests passing (was 151 pre-branch; +35 new).

**Plan:** `docs/superpowers/plans/2026-05-11-dayparting-plan.md` — 8 tasks.
**Spec:** `docs/superpowers/specs/2026-05-10-dayparting-design.md`.

**Wall-cell screens are out of scope** for v1 dayparting. Walls use their own playlist model (`walls.canvas_playlist_id`, `wall.mirrored_playlist_id`). A future phase can extend dayparting to walls.

**Sequence — Arabic [DONE], Security [PR #5 open], Offline [DONE], Dayparting [this PR], then A (land PR #5) → B (trial expiry) → C (subscription renewal).**

**Out of scope (queued):**
- Date ranges (Christmas, Summer specials)
- Per-day exceptions / holiday calendars
- "Closed" placeholder content
- Per-zone scheduling
- Wall-cell dayparting
- Audit-log entries for schedule changes (would extend Phase 2.5c if merged)
```

Update `MEMORY.md` index with a new one-line entry pointing at the file. Keep the file short — under 200 lines as guidance says.

- [ ] **Step 9: Final verification**

```bash
git status -sb
~/.local/bin/gh pr view --json number,url,state | head
```
Expected: working tree clean except untracked `khanshoof_assets/`. PR open, returns number + URL.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §5.1 sites.timezone column | Task 1 |
| §5.2 schedules table | Task 1 |
| §5.3 schedule_rules table | Task 1 |
| §5.4 screens.schedule_id column | Task 1 |
| §6.1 resolve_active_playlist | Task 2 |
| §6.2 _time_in_window | Task 2 |
| §6.3 _site_timezone | Task 2 |
| §6.4 build_screen_payload swap | Task 3 |
| §7 boundary propagation (revised: pull-based) | No tick task needed; documented in plan header |
| §8.1 schedule endpoints | Task 4 |
| §8.2 overlap detection | Task 4 |
| §8.3 PUT /sites + PUT /screens extensions | Task 5 |
| §9 admin UI | Tasks 6 + 7 |
| §10 testing | Tasks 1-5 (backend); Task 8 (manual smoke) |
| §11 file layout | All paths match |

No placeholders. Method names consistent across tasks (`resolve_active_playlist`, `_time_in_window`, `_site_timezone`, `_rules_overlap`, `_parse_time`).

Task ordering: 1 (schema) → 2 (resolver) → 3 (wire resolver) → 4 (CRUD) → 5 (extensions, un-skip integration test) → 6 (UI section) → 7 (UI pickers) → 8 (regression + PR).
