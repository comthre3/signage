# Phase 1 Lockdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit the currently-uncommitted UI overhaul + multi-tenancy backend work as a clean two-commit history, then add a pytest regression suite that locks in multi-tenant isolation and Starter-plan limit enforcement, before Phase 2 (signup UI, Sawwii branding, Stripe).

**Architecture:** Two focused commits separate the UI/perf overhaul from the multi-tenancy foundation. Tests use FastAPI's `TestClient` against the existing Postgres dev database; each test uses a UUID-suffixed business name/email so signups coexist without a teardown step (acceptable pre-launch). Test files live under `backend/tests/`. No new infrastructure.

**Tech Stack:** pytest 8.3, httpx 0.27 (via FastAPI TestClient), Postgres 16, psycopg 3.2, Docker Compose.

---

### Task 1: Verify working tree matches plan assumptions

**Files:**
- Read only: `/home/ahmed/signage` (git state)

- [ ] **Step 1: Confirm expected uncommitted set**

Run: `git status --short`

Expected output (order may vary, must include all of these):
```
 M backend/Dockerfile
 M backend/db.py
 M backend/main.py
 M backend/requirements.txt
 M docker-compose.yml
 M frontend/app.js
 M frontend/index.html
 M frontend/nginx.conf
 M frontend/styles.css
 M player/nginx.conf
 M player/player.js
?? backend/migrate_sqlite.py
?? docs/superpowers/plans/2026-04-20-phase1-lockdown.md
```

If any of the ` M` files above is missing, or extra `M`/`??` entries appear, stop and reconcile with the user before continuing. (The plan file itself is expected as untracked — it will be committed with the test suite at the end.)

- [ ] **Step 2: Confirm HEAD is clean baseline**

Run: `git log --oneline -3`

Expected: `340d6bc Initial commit: Signage digital display system` (single prior commit). If more commits exist, that's fine — just note what's there.

---

### Task 2: Commit the UI/perf overhaul

**Files:**
- Stage: `frontend/styles.css`, `frontend/app.js`, `frontend/index.html`, `frontend/nginx.conf`, `player/nginx.conf`, `player/player.js`

- [ ] **Step 1: Stage only overhaul files**

```bash
git add frontend/styles.css frontend/app.js frontend/index.html frontend/nginx.conf player/nginx.conf player/player.js
```

- [ ] **Step 2: Verify nothing else is staged**

Run: `git diff --cached --name-only`

Expected exactly these six files (any order):
```
frontend/app.js
frontend/index.html
frontend/nginx.conf
frontend/styles.css
player/nginx.conf
player/player.js
```

If the list differs, `git restore --staged <extra-file>` the extras before committing.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: UI/perf overhaul — retro-modern facelift + mobile responsive

- CSS variables, Google Fonts (Inter + JetBrains Mono), retro scanlines
- Full responsive layout with hamburger nav at 768/480 breakpoints
- Toast notifications replace alert() calls throughout admin
- XSS-safe escHtml/escAttr utilities for innerHTML
- Gzip + proper cache headers in nginx (HTML no-cache, static 7d)
- Player: gzip, responsive preview, online-status pulse

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify commit landed**

Run: `git log --oneline -1`

Expected: a new commit hash with subject `feat: UI/perf overhaul — retro-modern facelift + mobile responsive`.

---

### Task 3: Commit the multi-tenancy + Postgres foundation

**Files:**
- Stage: `backend/db.py`, `backend/main.py`, `backend/requirements.txt`, `backend/Dockerfile`, `backend/migrate_sqlite.py`, `docker-compose.yml`

- [ ] **Step 1: Stage backend + compose files**

```bash
git add backend/db.py backend/main.py backend/requirements.txt backend/Dockerfile backend/migrate_sqlite.py docker-compose.yml
```

- [ ] **Step 2: Verify staged set is exactly these six files**

Run: `git diff --cached --name-only`

Expected:
```
backend/Dockerfile
backend/db.py
backend/main.py
backend/migrate_sqlite.py
backend/requirements.txt
docker-compose.yml
```

- [ ] **Step 3: Confirm no other unstaged modifications remain**

Run: `git status --short`

Expected: only untracked items may appear (e.g., the plan doc itself). No ` M` entries.

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: multi-tenancy foundation — Postgres + organizations + pricing tiers

- Swap SQLite → Postgres 16 (psycopg 3) with DATABASE_URL env var
- Add organizations table: plan, screen_limit, subscription_status,
  trial_ends_at, stripe_customer_id (reserved), stripe_subscription_id
  (reserved), locale
- Add organization_id FK on every tenant-scoped entity
  (users, sites, screens, playlists, media, groups, zone_templates)
- Scope every admin query to caller's organization_id
- /auth/signup creates org + admin user + 14-day Starter trial
- Enforce plan screen_limit on POST /screens (HTTP 402 over-limit)
- Role hierarchy: viewer/editor/admin via require_roles dependency
- Per-org indexes on all scoped tables
- migrate_sqlite.py: one-shot migration under a 'Legacy' org
  (refuses to run if orgs already exist — idempotent safety net)
- docker-compose: Postgres 16 service with healthcheck + volume

Pricing ladder baked into PLANS dict:
  Starter $9.99/3, Growth $12.99/5, Business $24.99/10,
  Pro $49.99/25, Enterprise contact-us.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify clean tree**

Run: `git status --short`

Expected: only the plan doc remains untracked. No ` M` or ` D` entries.

Run: `git log --oneline -3`

Expected: two new commits atop the initial commit (overhaul then multi-tenancy).

---

### Task 4: Add pytest + httpx and a pytest.ini

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/pytest.ini`

- [ ] **Step 1: Append test dependencies**

Append these two lines to the end of `backend/requirements.txt`:

```
pytest==8.3.3
httpx==0.27.2
```

After the edit, `backend/requirements.txt` must end as:
```
fastapi==0.111.0
uvicorn[standard]==0.30.0
python-multipart==0.0.9
psycopg[binary]==3.2.3
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Create pytest.ini**

Write `backend/pytest.ini` with exactly this content:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --tb=short
```

- [ ] **Step 3: Rebuild backend image to install pytest**

```bash
docker-compose build backend
```

Expected: successful build. `pytest` and `httpx` install in the image layer.

- [ ] **Step 4: Verify pytest is callable**

```bash
docker-compose run --rm backend pytest --version
```

Expected output: `pytest 8.3.3` (exact patch may drift by pip resolution — major/minor must match).

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/pytest.ini
git commit -m "$(cat <<'EOF'
chore: add pytest + httpx for regression tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Create pytest fixtures + smoke test

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_smoke.py`

- [ ] **Step 1: Create empty tests package marker**

Write `backend/tests/__init__.py` with no content (empty file).

- [ ] **Step 2: Write conftest.py**

Write `backend/tests/conftest.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def unique_business() -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "business_name": f"Test Biz {suffix}",
        "email": f"owner-{suffix}@example.com",
        "password": "testpass1",
    }


@pytest.fixture
def signed_up_org(client: TestClient, unique_business: dict) -> dict:
    response = client.post("/auth/signup", json=unique_business)
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "token": data["token"],
        "org": data["organization"],
        "user": data["user"],
    }
```

- [ ] **Step 3: Write smoke test**

Write `backend/tests/test_smoke.py`:

```python
def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_signup_creates_starter_org(signed_up_org):
    org = signed_up_org["org"]
    assert signed_up_org["token"]
    assert org["plan"] == "starter"
    assert org["screen_limit"] == 3
    assert org["subscription_status"] == "trialing"
    assert org["trial_ends_at"]
```

- [ ] **Step 4: Run the smoke tests**

Postgres must be running first:
```bash
docker-compose up -d postgres
```

Then:
```bash
docker-compose run --rm backend pytest tests/test_smoke.py
```

Expected: `2 passed`. If you see a connection error, wait a few seconds for Postgres healthcheck and rerun.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_smoke.py
git commit -m "$(cat <<'EOF'
test: add pytest fixtures + smoke test for signup flow

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Multi-tenancy isolation tests

**Files:**
- Create: `backend/tests/test_multi_tenancy.py`

- [ ] **Step 1: Write isolation tests**

Write `backend/tests/test_multi_tenancy.py`:

```python
import uuid


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup(client, suffix: str) -> dict:
    response = client.post(
        "/auth/signup",
        json={
            "business_name": f"Biz {suffix}",
            "email": f"owner-{suffix}@example.com",
            "password": "testpass1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_org_a_cannot_see_or_mutate_org_b_screens(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.post("/screens", json={"name": "A-only"}, headers=_bearer(a["token"]))
    assert r.status_code == 200, r.text
    a_screen_id = r.json()["id"]

    # B lists screens — must not include A's
    r = client.get("/screens", headers=_bearer(b["token"]))
    assert r.status_code == 200
    assert a_screen_id not in [s["id"] for s in r.json()]

    # B tries to update A's screen by id — must 404
    r = client.put(
        f"/screens/{a_screen_id}",
        json={"name": "pwned"},
        headers=_bearer(b["token"]),
    )
    assert r.status_code == 404, r.text

    # B tries to delete A's screen — must 404
    r = client.delete(f"/screens/{a_screen_id}", headers=_bearer(b["token"]))
    assert r.status_code == 404, r.text


def test_org_a_cannot_see_org_b_playlists(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.post(
        "/playlists",
        json={"name": "A-only playlist"},
        headers=_bearer(a["token"]),
    )
    assert r.status_code == 200, r.text
    a_playlist_id = r.json()["id"]

    r = client.get("/playlists", headers=_bearer(b["token"]))
    assert r.status_code == 200
    assert a_playlist_id not in [p["id"] for p in r.json()]


def test_org_a_cannot_see_org_b_users(client):
    a = _signup(client, uuid.uuid4().hex[:8])
    b = _signup(client, uuid.uuid4().hex[:8])

    r = client.get("/users", headers=_bearer(a["token"]))
    assert r.status_code == 200
    a_usernames = [u["username"] for u in r.json()]

    r = client.get("/users", headers=_bearer(b["token"]))
    assert r.status_code == 200
    b_usernames = [u["username"] for u in r.json()]

    assert a["user"]["username"] in a_usernames
    assert a["user"]["username"] not in b_usernames
    assert b["user"]["username"] in b_usernames
    assert b["user"]["username"] not in a_usernames
```

- [ ] **Step 2: Run the isolation tests**

```bash
docker-compose run --rm backend pytest tests/test_multi_tenancy.py
```

Expected: `3 passed`. **If any test fails, stop: there is a real cross-tenant data leak** — investigate and fix before committing.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_multi_tenancy.py
git commit -m "$(cat <<'EOF'
test: lock in multi-tenant isolation across screens, playlists, users

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Plan-limit enforcement tests

**Files:**
- Create: `backend/tests/test_plan_limits.py`

- [ ] **Step 1: Write plan-limit tests**

Write `backend/tests/test_plan_limits.py`:

```python
def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_starter_plan_blocks_fourth_screen(client, signed_up_org):
    """Starter trial limit is 3 screens. The 4th POST must return 402."""
    token = signed_up_org["token"]
    assert signed_up_org["org"]["screen_limit"] == 3

    for i in range(3):
        r = client.post(
            "/screens",
            json={"name": f"ok-{i}"},
            headers=_bearer(token),
        )
        assert r.status_code == 200, f"screen {i + 1}/3 should succeed: {r.text}"

    r = client.post(
        "/screens",
        json={"name": "over-limit"},
        headers=_bearer(token),
    )
    assert r.status_code == 402, f"expected 402, got {r.status_code}: {r.text}"
    assert "limit" in r.json()["detail"].lower()


def test_organization_screens_used_counter(client, signed_up_org):
    token = signed_up_org["token"]

    r = client.get("/organization", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["screens_used"] == 0

    for name in ("s1", "s2"):
        r = client.post("/screens", json={"name": name}, headers=_bearer(token))
        assert r.status_code == 200, r.text

    r = client.get("/organization", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["screens_used"] == 2
```

- [ ] **Step 2: Run the plan-limit tests**

```bash
docker-compose run --rm backend pytest tests/test_plan_limits.py
```

Expected: `2 passed`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_plan_limits.py
git commit -m "$(cat <<'EOF'
test: lock in Starter 3-screen limit + screens_used counter

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Full-suite green + commit plan doc + update memory

**Files:**
- Stage: `docs/superpowers/plans/2026-04-20-phase1-lockdown.md` (this plan)
- Modify: `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`

- [ ] **Step 1: Run the full suite**

```bash
docker-compose run --rm backend pytest
```

Expected: `7 passed` (2 smoke + 3 multi-tenancy + 2 plan-limit).

- [ ] **Step 2: Commit the plan doc**

```bash
git add docs/superpowers/plans/2026-04-20-phase1-lockdown.md
git commit -m "$(cat <<'EOF'
docs: Plan A — Phase 1 lockdown (commits + regression tests)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Verify final git history**

Run: `git log --oneline`

Expected seven new commits atop the initial `340d6bc`:
1. `feat: UI/perf overhaul …`
2. `feat: multi-tenancy foundation …`
3. `chore: add pytest + httpx …`
4. `test: add pytest fixtures + smoke test …`
5. `test: lock in multi-tenant isolation …`
6. `test: lock in Starter 3-screen limit …`
7. `docs: Plan A — Phase 1 lockdown …`

Run: `git status --short`

Expected: empty (clean working tree).

- [ ] **Step 4: Update the roadmap memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`.

Replace the bullet list under "Already implemented in working tree (uncommitted):" with:

```
Already committed (Phase 1 backend complete + locked by regression tests):
- `backend/db.py`: Postgres 16 (psycopg 3), DATABASE_URL env var
- `backend/main.py`: organizations table; organization_id on every
  tenant-scoped entity; queries scoped to caller's org; PLANS dict;
  /auth/signup creates org + 14-day Starter trial; HTTP 402 over screen_limit
- `backend/migrate_sqlite.py`: one-shot SQLite→Postgres migration
- `docker-compose.yml`: Postgres 16 + healthcheck + volume
- `backend/tests/`: 7 regression tests (smoke + multi-tenancy + plan limits)
  Run via: docker-compose run --rm backend pytest
```

Leave the "Still NOT done" section as-is; it still accurately captures Phase 2 scope (signup UI, Sawwii branding, Stripe, bilingual, marketing site).

- [ ] **Step 5: Report completion**

Print:
```
Phase 1 lockdown complete.
- 7 new commits, working tree clean.
- 7 pytest regressions green (smoke, multi-tenancy, plan limits).
- Ready for Plan B (frontend signup + Sawwii branding) or Plan C (Stripe).
```

---

## Notes for future plans

**Plan B (frontend signup + Sawwii branding)** will:
- Add a `/signup` route in the admin SPA with business_name + email + password form.
- POST to existing `/auth/signup` backend endpoint (already done).
- Swap "◈ Signage Admin" → "Sawwii" in `frontend/index.html` + page `<title>`.
- Add a minimal "Your plan" card to the dashboard that reads from `/organization`.

**Plan C (Stripe Checkout + webhooks)** will:
- Install `stripe` Python SDK.
- Add `/billing/checkout-session` endpoint: creates Checkout session for selected plan.
- Add `/billing/webhook` endpoint: verifies signature, updates `organizations.subscription_status` + `stripe_subscription_id` on `customer.subscription.*` events.
- Add `/billing/portal` endpoint for Customer Portal redirect.
- Frontend: upgrade button on the "Your plan" card → redirects to Stripe Checkout.
