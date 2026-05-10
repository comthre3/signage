# Phase 2.5e — Dayparting (Player Scheduling) — Design

**Date:** 2026-05-10
**Branch:** `feature/dayparting` (fresh, branched from `main` after PR #6 squash-merge `4a236db`)
**Predecessor:** Phase 2.5d (offline asset caching) merged on main. Phase 2.5c (security) is on PR #5, still open — independent of this work.

---

## 1. Goal

Multi-location chains can define a named, reusable Schedule (e.g., "Standard Daypart") populated with playlist-time-DOW rules, and assign it to any number of screens. The backend picks each screen's active playlist based on the screen's site timezone and pushes `playlist_change` frames over the existing WebSocket at boundaries. The player needs zero code changes — it already consumes those frames.

## 2. Existing State (do not regress)

- Each screen has one `playlist_id` (FK to `playlists`) — current "what to play" source. Resolved by `build_screen_payload(screen)` in `backend/main.py`.
- The `screen_zones` table supports multi-zone screens with their own item lists — orthogonal to this work; not touched.
- Walls (`mirrored` and `spanned`) are orthogonal — not touched.
- WebSocket already exists and pushes `playlist_change` frames when admins edit a playlist; player handles them by calling `fetchContent()`. The dayparting tick re-uses this exact channel.
- No timezone column exists on any table; backend stores TIMESTAMPTZ but all logic is UTC.

## 3. Design Choices (recap from brainstorm)

1. **Granularity:** time-of-day + day-of-week. No date ranges, no per-day exceptions in v1.
2. **Timezone:** per-site (`sites.timezone`, default `Asia/Kuwait`). IANA tz name.
3. **Resolution:** server-side + 60s WS-push tick.
4. **Fallback:** `screens.playlist_id` (existing default) when no rule matches.
5. **Schedule model:** reusable `schedules` table + `schedule_rules`, attached via `screens.schedule_id`.
6. **Overlap policy:** rules within the same schedule cannot overlap on any (day, time) combo. 422 at create/update.
7. **Cross-midnight:** supported. If `end_time < start_time`, the rule wraps midnight (e.g., 22:00–02:00).

## 4. Non-Goals (deferred)

- Date ranges (Christmas Dec 1-25, Summer Jun 1 – Aug 31)
- Per-day exceptions (closed Christmas Day, special menu Valentine's)
- "Closed" placeholder content for unscheduled gaps (modeled instead as a wide rule covering 00:00–23:59 if needed)
- Schedule preview / dry-run UI (admin sees only the current state)
- Per-zone scheduling (a multi-zone screen runs one schedule for its primary playlist; zones aren't dayparted in v1)
- Holiday calendars
- Leader-elected tick for multi-replica backend (single-replica is the current architecture)

## 5. Component A — Schema

### 5.1 `sites.timezone` (new column)

```sql
ALTER TABLE sites ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kuwait';
```

IANA timezone name. Default fits the GCC focus.

### 5.2 `schedules` (new table)

```sql
CREATE TABLE IF NOT EXISTS schedules (
  id              SERIAL PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_schedules_org ON schedules (organization_id);
```

Names are not unique. Operators may have "Daypart" and "Daypart (test)".

### 5.3 `schedule_rules` (new table)

```sql
CREATE TABLE IF NOT EXISTS schedule_rules (
  id              SERIAL PRIMARY KEY,
  schedule_id     INTEGER NOT NULL REFERENCES schedules(id)  ON DELETE CASCADE,
  playlist_id     INTEGER NOT NULL REFERENCES playlists(id)  ON DELETE CASCADE,
  start_time      TIME NOT NULL,
  end_time        TIME NOT NULL,
  days_of_week    SMALLINT NOT NULL,        -- bitmask 0–127, bit 0 = Mon, bit 6 = Sun
  position        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_schedule_rules_schedule ON schedule_rules (schedule_id);
```

- `start_time`/`end_time`: postgres `TIME` (no date, no tz). Site's tz applies during resolution.
- `end_time` exclusive: rule "11:00–14:00" matches 11:00:00.0 through 13:59:59.999. Avoids double-match at exact boundaries.
- DOW bitmask: bit 0 = Monday, bit 6 = Sunday (ISO week).
- `position`: display ordering only; doesn't affect resolution.

### 5.4 `screens.schedule_id` (new column)

```sql
ALTER TABLE screens ADD COLUMN IF NOT EXISTS schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL;
```

`NULL` means no schedule → fall back to `screens.playlist_id`. Existing screens default to NULL — current behavior preserved exactly.

## 6. Component B — Resolution Logic

### 6.1 `resolve_active_playlist(screen) -> Optional[int]`

New helper in `backend/main.py`. Replaces the direct `screen.playlist_id` lookup in `build_screen_payload()`.

```python
def resolve_active_playlist(screen: dict) -> Optional[int]:
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

### 6.2 `_time_in_window(now, start, end) -> bool`

Handles cross-midnight rules:

```python
def _time_in_window(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end               # normal: 11:00–14:00
    return now >= start or now < end            # wrap:   22:00–02:00
```

### 6.3 `_site_timezone(site_id) -> ZoneInfo`

Looks up `sites.timezone`. Falls back to `ZoneInfo("Asia/Kuwait")` if site is missing or column has an invalid string. Uses Python stdlib `zoneinfo`.

```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def _site_timezone(site_id: Optional[int]) -> ZoneInfo:
    if site_id is None:
        return ZoneInfo("Asia/Kuwait")
    row = query_one("SELECT timezone FROM sites WHERE id = ?", (site_id,))
    if not row or not row.get("timezone"):
        return ZoneInfo("Asia/Kuwait")
    try:
        return ZoneInfo(row["timezone"])
    except ZoneInfoNotFoundError:
        logger.warning("invalid_site_timezone site_id=%s tz=%s", site_id, row["timezone"])
        return ZoneInfo("Asia/Kuwait")
```

### 6.4 `build_screen_payload()` — single-line change

Replace `screen.get("playlist_id")` with `resolve_active_playlist(screen)`. Everything downstream (querying playlist_items, building URLs, returning JSON) is unchanged.

## 7. Component C — Boundary Tick

Single asyncio task started at app startup. Runs forever.

```python
SCHEDULE_TICK_SECONDS = 60

async def _schedule_boundary_tick():
    last_seen: dict[int, Optional[int]] = {}
    while True:
        await asyncio.sleep(SCHEDULE_TICK_SECONDS)
        try:
            screens = query_all(
                "SELECT id, organization_id, schedule_id, playlist_id, site_id "
                "FROM screens WHERE schedule_id IS NOT NULL"
            )
            for s in screens:
                current = resolve_active_playlist(s)
                if last_seen.get(s["id"], "uninit") != current:
                    last_seen[s["id"]] = current
                    await broadcast_to_screen(s["id"], {"type": "playlist_change"})
        except Exception as exc:
            logger.warning("schedule_tick_failed: %s", exc)


@app.on_event("startup")
async def _start_schedule_tick():
    asyncio.create_task(_schedule_boundary_tick())
```

- Only walks scheduled screens — avoids work for non-scheduled.
- `broadcast_to_screen` already exists; reusing the existing channel means no player changes.
- `"uninit"` sentinel ensures the first tick after startup doesn't push a spurious refresh.
- Errors are caught + logged; the task can never crash the app.
- 60s lag at boundaries; tunable.

**Multi-replica caveat:** if we ever run more than one backend replica, both run the tick → duplicate WS frames → player re-fetches twice. Wasted bandwidth, no correctness issue. Future fix: leader-elect or external scheduler. YAGNI today.

## 8. Component D — Backend CRUD

### 8.1 Schedule endpoints

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/schedules` | admin/editor | `{name}` | `{id, name, created_at, rules: []}` |
| GET | `/schedules` | admin/editor | — | `{items: [...]}` (org-scoped) |
| GET | `/schedules/{id}` | admin/editor | — | `{id, name, rules: [...]}` |
| PUT | `/schedules/{id}` | admin/editor | `{name}` | `{id, name, ...}` |
| DELETE | `/schedules/{id}` | admin | — | 204 |
| PUT | `/schedules/{id}/rules` | admin/editor | `{rules: [{playlist_id, start_time, end_time, days_of_week, position}]}` | `{rules: [...]}` |

`PUT /schedules/{id}/rules` is **replace-all** semantics: existing rules are deleted, new rules inserted in the order given. Simpler client logic than per-row PUT/DELETE/POST. Atomic in a single transaction.

### 8.2 Validation

`POST /schedules`, `PUT /schedules/{id}/rules` validate before commit:

- `name` non-empty, ≤200 chars
- `playlist_id` belongs to caller's org
- `start_time`, `end_time` valid `HH:MM` or `HH:MM:SS` strings
- `days_of_week` integer in `[1, 127]` (≥1 day required)
- **Overlap detection:** for each pair of rules in the request, if their `days_of_week` share any bit AND their time windows overlap (using `_time_in_window` semantics), reject with `422 schedule.rule_overlap`.

### 8.3 Site / screen endpoint extensions

- `PUT /sites/{id}` accepts `timezone` field. Validates with `ZoneInfo(...)` — invalid → `422 site.timezone_invalid`.
- `PUT /screens/{id}` accepts `schedule_id` field (nullable int). Validates schedule belongs to caller's org.

## 9. Component E — Admin UI

### 9.1 Top-level "Schedules" nav

```html
<button data-section="schedules" data-i18n="nav.schedules">Schedules</button>
```

Position between "Playlists" and "Walls". Visible to `admin` and `editor`; hidden from `viewer` (mirrors the playlist-write gating pattern).

### 9.2 Section markup — two views in one section, toggled by JS

**List view:** card grid of schedules. Each card: name, rule count, "Edit" / "Delete" buttons. Top: "+ New schedule" button.

**Editor view:**
- Schedule name input (required, max 200 chars).
- Table of rules. Columns: playlist `<select>`, start `<input type="time">`, end `<input type="time">`, 7 day-of-week checkboxes (Mon–Sun), delete button.
- Below table: "+ Add rule" button.
- Bottom: "Save" / "Cancel" buttons.

### 9.3 `Schedules` IIFE — `frontend/app.js`

Modeled after the existing `Walls` and `MediaPicker` modules. Public methods:
- `Schedules.show()` — called by `showSection("schedules")`. Loads list via `GET /schedules`.
- `Schedules.openEditor(id?)` — opens editor for new or existing schedule.
- `Schedules.saveCurrent()` — `POST` (new) or `PUT` (existing) the schedule, then `PUT /schedules/{id}/rules` to replace-all rules.
- `Schedules.delete(id)` — `confirmDialog` + `DELETE /schedules/{id}`.

DOW UI: 7 checkboxes labeled with locale-appropriate day names. Convert to/from bitmask client-side.

Time inputs: native `<input type="time">`. Stored as `"HH:MM"`; backend parses as `TIME`.

Overlap detection: server-enforced via 422. Client surfaces the error as a localized `toast(msg, "error")`.

### 9.4 Site detail — timezone picker

Site edit form gets a `<select id="site-timezone">` populated with a curated list:

```javascript
const TZ_OPTIONS = [
  "Asia/Kuwait", "Asia/Riyadh", "Asia/Dubai", "Asia/Qatar",
  "Asia/Bahrain", "Asia/Muscat", "Asia/Baghdad",
  "Africa/Cairo", "Asia/Amman", "Asia/Beirut",
  "UTC",
];
```

We avoid shipping the full ~600-entry IANA list. Operators outside this set request additions; we add to the constant.

### 9.5 Screen detail — schedule picker

Existing screen edit form gets one new dropdown: "Schedule" with options "None — use default playlist" + each schedule by name. Stored on PUT as `schedule_id` (nullable).

The existing `playlist_id` dropdown stays visible — it's the fallback when no rule matches.

### 9.6 i18n — new keys (~20)

In both `frontend/i18n/en.json` and `frontend/i18n/ar.json` (MSA):

```
nav.schedules
schedules.title, schedules.new, schedules.empty, schedules.rule_count
schedules.editor.name, schedules.editor.rules, schedules.editor.add_rule
schedules.editor.playlist, schedules.editor.start, schedules.editor.end, schedules.editor.days
schedules.editor.save, schedules.editor.cancel, schedules.editor.delete
schedules.dow.mon, .tue, .wed, .thu, .fri, .sat, .sun
schedules.overlap_error
screen.field.schedule, screen.schedule.none
site.field.timezone
toast.schedule_saved, toast.schedule_deleted
```

i18n parity gated by `scripts/check_i18n.py` as for previous phases.

## 10. Testing

### 10.1 Backend tests (`backend/tests/test_schedules.py` — new file, ~25 tests)

**Schema:**
- `test_schedules_table_exists`
- `test_schedule_rules_table_exists`
- `test_sites_has_timezone_column`

**Schedule CRUD:**
- `test_post_schedule_creates_row`
- `test_post_schedule_requires_role` (viewer → 403)
- `test_post_schedule_org_scoped`
- `test_get_schedules_lists_only_own_org`
- `test_put_schedule_updates_name`
- `test_delete_schedule_cascades_rules`

**Rules CRUD:**
- `test_put_rules_replaces_all`
- `test_post_rule_validates_time_order_when_not_wrapping`
- `test_post_rule_allows_wrap_midnight`
- `test_post_rule_rejects_overlap_same_day`
- `test_post_rule_allows_overlap_different_days`
- `test_post_rule_rejects_invalid_dow`
- `test_post_rule_requires_at_least_one_day`

**Resolver:**
- `test_resolve_no_schedule_returns_default_playlist`
- `test_resolve_with_schedule_picks_matching_rule` (mock `datetime.now` to specific instant)
- `test_resolve_falls_back_when_no_rule_matches`
- `test_resolve_handles_wrap_midnight`
- `test_resolve_uses_site_timezone`
- `test_resolve_with_unknown_tz_falls_back_to_kuwait`

**Site timezone:**
- `test_put_site_accepts_timezone`
- `test_put_site_rejects_invalid_timezone`
- `test_default_site_timezone_is_kuwait`

### 10.2 Frontend smoke (manual)

Listed in PR test plan body:
- Create a site, set timezone Asia/Riyadh.
- Create a schedule "Daypart" with breakfast 6–11 Mon-Fri / lunch 11–14 Mon-Fri / weekend 9–17 Sat-Sun.
- Assign to a screen.
- Watch DevTools → Network → WS frames at boundary times. Verify `playlist_change` arrives within 60s.
- Edit a rule mid-day; verify propagation.
- Delete the schedule from the screen page; verify fallback to default `playlist_id`.
- Try overlapping rules; verify 422 surfaces as a localized toast (EN + AR).

## 11. File Layout

| File | Change |
|---|---|
| `backend/db.py` | Add 2 tables + 2 indices + 2 ALTER TABLEs in `init_db()` |
| `backend/main.py` | `_site_timezone`, `_time_in_window`, `resolve_active_playlist`, replace lookup in `build_screen_payload`, 6 schedule/rule endpoints, extend `PUT /sites` and `PUT /screens`, asyncio tick task |
| `backend/tests/test_schedules.py` | NEW — ~25 tests |
| `frontend/index.html` | `nav.schedules` button, `<section id="schedules">` block with list + editor views, schedule `<select>` on screen edit form, timezone `<select>` on site edit form |
| `frontend/app.js` | `Schedules` IIFE (~250 lines), `TZ_OPTIONS` constant, wire `showSection("schedules") → Schedules.show()`, schedule picker handler in screen-edit, timezone handler in site-edit |
| `frontend/styles.css` | Schedule list grid + editor table styling |
| `frontend/i18n/en.json`, `frontend/i18n/ar.json` | ~20 new keys |

## 12. Failure Modes

| Failure | Behavior |
|---|---|
| Boundary tick task crashes | `try/except` in the loop swallows + logs warning. Task continues. App stays up. |
| Site timezone string is invalid | `_site_timezone` falls back to `Asia/Kuwait`. Logged warning. No screen breaks. |
| Schedule has zero rules | Resolutions all fall back to `screens.playlist_id`. Equivalent to no schedule. |
| Rule references a deleted playlist | `ON DELETE CASCADE` removes the rule with the playlist. Resolver skips to fallback. |
| Resolver runs at exact rule boundary | `start <= now < end` semantics. A rule ending at 11:00 doesn't match 11:00:00; the next rule starting at 11:00 does. No double-match, no gap. |
| Backend restart mid-day | `last_seen` dict re-primes from current state; first post-restart tick pushes nothing. Player doesn't get a spurious refresh. |
| Two backend replicas | Both push duplicate WS frames; player re-fetches twice (idempotent). Wasted bandwidth, no correctness issue. |
| Player offline at boundary | When player reconnects, it re-fetches via `fetchContent`; backend resolver returns the now-active playlist. No special handling needed. |

## 13. Migration / Rollout

1. `ALTER TABLE` columns are idempotent (`ADD COLUMN IF NOT EXISTS`). New tables use `CREATE TABLE IF NOT EXISTS`. Postgres-safe.
2. Existing screens get `schedule_id = NULL` automatically. Current behavior preserved.
3. Existing sites get `timezone = 'Asia/Kuwait'` default. Correct for current Kuwait-based customers.
4. No data backfill needed.
5. Existing playlist_change WS push pipeline already handles the new tick-driven frames — player needs zero changes.

## 14. Out of Scope (queued)

- Date ranges (Christmas Dec 1-25, Summer specials)
- Per-day exceptions / holiday calendars
- "Closed" placeholder content for unscheduled gaps
- Schedule preview / dry-run UI
- Per-zone scheduling
- Leader-elected tick for multi-replica backend
- Audit-log entries for schedule changes (currently only auth + admin user actions are audited; could extend to `schedule.create/update/delete` in a follow-up)

## 15. Next Initiative After This One

User's stated post-2.5d sequence: Dayparting [this PR] → Land remaining PRs → Trial expiry enforcement → Subscription renewal reminders.
