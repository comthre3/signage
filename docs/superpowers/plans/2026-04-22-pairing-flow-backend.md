# Pairing Flow (Pattern B) — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three backend endpoints that enable "Pattern B" TV pairing — player opens `play.sawwii.com`, requests a fresh 5-char code + unique `device_id`, the user scans a QR on their phone, admin confirms on `app.sawwii.com/pair?code=…`, and the player's next poll returns the bound screen's long-lived `token` so it can fetch content.

**Architecture:** One new table `pairing_codes` tracks ephemeral pairing sessions independently of the existing `screens.pair_code` column (which remains for the legacy admin-generates-code flow until Plan 4 retires it). Three FastAPI endpoints: `POST /screens/request_code` (player → code), `GET /screens/poll/{code}` (player polls for claim), `POST /screens/claim` (authenticated admin binds code to a screen). A 5-char alphanumeric code generator avoids confusable glyphs (`O/0/I/1/L`). Expiry is 10 min; a claimed record is idempotent so the player's polling keeps returning the paired payload until a separate cleanup deletes it.

**Tech Stack:** FastAPI + Pydantic, Postgres 16 via `backend/db.py`, pytest. No new third-party deps.

**Pairing constants (decided):**
- Charset: `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (31 chars — uppercase letters + digits, no `O/0/I/1/L`)
- Code length: 5 (≈28.6M combinations)
- Device ID: 32-char hex (`secrets.token_hex(16)`)
- Code TTL: 600 seconds (10 min)
- Claim requires auth role `admin` or `editor` (owner/manager users)
- Post-claim: poll continues to return `{status: "paired", …}` until expiry; cleanup of stale rows is not in scope here

---

## Prerequisites

Work from `/home/ahmed/signage` on a new branch `feature/pairing-pattern-b` off `main`.

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage checkout -b feature/pairing-pattern-b
```

Baseline regression must be 22 passed before starting:

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

## File Structure

```
backend/
├── main.py              # add 3 endpoints + SignupRequest-style Pydantic models + pairing helpers
├── db.py                # add pairing_codes table in init_db()
└── tests/
    └── test_pairing.py  # NEW — 12 tests

(No frontend or player changes in this plan — those are Plan 2 / Plan 3.)
```

---

## Task 1: DB table + code generator helpers

**Files:**
- Modify: `backend/db.py` — add `pairing_codes` in `init_db()`
- Modify: `backend/main.py` — add `PAIR_CODE_CHARSET`, `PAIR_CODE_LENGTH`, `PAIR_CODE_TTL_SECONDS`; add `generate_pair_code_v2` + `generate_unique_pair_code_v2`
- Test: `backend/tests/test_pairing.py` — NEW

- [ ] **Step 1: Write the failing helper tests**

Create `backend/tests/test_pairing.py`:

```python
import re

import pytest
from fastapi.testclient import TestClient

from main import (
    PAIR_CODE_CHARSET,
    PAIR_CODE_LENGTH,
    app,
    generate_pair_code_v2,
)


VALID_CODE = re.compile(f"^[{re.escape(PAIR_CODE_CHARSET)}]{{{PAIR_CODE_LENGTH}}}$")


@pytest.fixture
def pair_client() -> TestClient:
    return TestClient(app)


def test_pair_code_charset_excludes_confusables():
    for ch in "O0I1L":
        assert ch not in PAIR_CODE_CHARSET


def test_generate_pair_code_v2_shape():
    code = generate_pair_code_v2()
    assert VALID_CODE.match(code), code
```

- [ ] **Step 2: Run helper tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: `ImportError: cannot import name 'PAIR_CODE_CHARSET' from 'main'` (or similar).

- [ ] **Step 3: Add constants + generator to `backend/main.py`**

Add the following block immediately AFTER the existing `generate_unique_token()` function (search for `def generate_unique_token` — the block is near line 125):

```python
PAIR_CODE_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIR_CODE_LENGTH = 5
PAIR_CODE_TTL_SECONDS = int(os.getenv("PAIR_CODE_TTL_SECONDS", "600"))


def generate_pair_code_v2() -> str:
    return "".join(secrets.choice(PAIR_CODE_CHARSET) for _ in range(PAIR_CODE_LENGTH))


def generate_unique_pair_code_v2() -> str:
    while True:
        code = generate_pair_code_v2()
        if not query_one("SELECT id FROM pairing_codes WHERE code = ?", (code,)):
            return code
```

`secrets` is already imported at module scope (used by `generate_otp` and password hashing).

- [ ] **Step 4: Run helper tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: 2 passed. (`generate_unique_pair_code_v2` can't be exercised yet — the table doesn't exist.)

- [ ] **Step 5: Add `pairing_codes` table to `backend/db.py`**

Locate `init_db()`. Immediately AFTER the existing `pending_signups` CREATE TABLE (the one added in the OTP plan), add:

```python
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairing_codes (
                id           SERIAL PRIMARY KEY,
                code         TEXT NOT NULL UNIQUE,
                device_id    TEXT NOT NULL UNIQUE,
                status       TEXT NOT NULL DEFAULT 'pending',
                screen_id    INTEGER REFERENCES screens (id) ON DELETE SET NULL,
                expires_at   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                claimed_at   TEXT
            )
        """)
```

Match the surrounding indentation and keep it inside the same `with` / cursor block as the other CREATE TABLEs.

- [ ] **Step 6: Rebuild backend and confirm schema loads**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: **24 passed** (22 existing + 2 new pairing helper tests).

- [ ] **Step 7: Commit**

```bash
git -C /home/ahmed/signage add backend/db.py backend/main.py backend/tests/test_pairing.py
git -C /home/ahmed/signage commit -m "feat(backend): pairing_codes table + v2 code generator"
```

---

## Task 2: `POST /screens/request_code`

**Files:**
- Modify: `backend/main.py` — add `PairRequestStart` model + endpoint
- Test: `backend/tests/test_pairing.py` — append 3 tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pairing.py`:

```python
def test_request_code_returns_code_and_device_id(pair_client):
    r = pair_client.post("/screens/request_code", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert VALID_CODE.match(data["code"]), data
    assert len(data["device_id"]) == 32
    assert data["expires_in_seconds"] == 600
    assert "expires_at" in data


def test_request_code_each_call_is_unique(pair_client):
    r1 = pair_client.post("/screens/request_code", json={}).json()
    r2 = pair_client.post("/screens/request_code", json={}).json()
    assert r1["code"] != r2["code"]
    assert r1["device_id"] != r2["device_id"]


def test_request_code_accepts_empty_body(pair_client):
    r = pair_client.post("/screens/request_code")
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: the three new tests fail with 404 (endpoint not found).

- [ ] **Step 3: Implement the endpoint**

In `backend/main.py`, find the existing `@app.post("/screens/pair")` handler (near line 1386). Immediately BEFORE that legacy handler, add:

```python
class PairRequestStart(BaseModel):
    user_agent: str | None = Field(default=None, max_length=500)


@app.post("/screens/request_code")
def request_pair_code(payload: PairRequestStart | None = None) -> dict:
    now = datetime.now(timezone.utc)
    code = generate_unique_pair_code_v2()
    device_id = secrets.token_hex(16)
    expires_at = (now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)).isoformat()
    execute(
        """
        INSERT INTO pairing_codes (code, device_id, status, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (code, device_id, "pending", expires_at, now.isoformat()),
    )
    return {
        "code": code,
        "device_id": device_id,
        "expires_at": expires_at,
        "expires_in_seconds": PAIR_CODE_TTL_SECONDS,
    }
```

Leave the legacy `@app.post("/screens/pair")` handler intact — Plan 4 retires it.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: **5 passed** (2 helpers + 3 request_code).

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_pairing.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /screens/request_code issues 5-char pair code"
```

---

## Task 3: `GET /screens/poll/{code}` — pending + expired states

**Files:**
- Modify: `backend/main.py` — add endpoint (paired state handled in Task 4)
- Test: `backend/tests/test_pairing.py` — append 4 tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pairing.py`:

```python
from datetime import datetime, timedelta, timezone


def test_poll_returns_pending_for_fresh_code(pair_client):
    r = pair_client.post("/screens/request_code", json={})
    code = r.json()["code"]
    r2 = pair_client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "pending"


def test_poll_unknown_code_returns_404(pair_client):
    r = pair_client.get("/screens/poll/ZZZZZ")
    assert r.status_code == 404


def test_poll_expired_code_returns_expired_status(pair_client):
    from db import execute
    r = pair_client.post("/screens/request_code", json={})
    code = r.json()["code"]
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    r2 = pair_client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "expired"


def test_poll_malformed_code_returns_404(pair_client):
    r = pair_client.get("/screens/poll/toolong1234")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: the four new tests fail with 404 or similar.

- [ ] **Step 3: Implement the endpoint**

In `backend/main.py`, immediately AFTER the `request_pair_code` function added in Task 2, add:

```python
@app.get("/screens/poll/{code}")
def poll_pair_code(code: str) -> dict:
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (code,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown pairing code")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)

    if row["status"] == "pending" and now > expires_dt:
        return {"status": "expired"}

    if row["status"] == "pending":
        return {"status": "pending"}

    # Paired branch — fully populated in Task 4.
    return {"status": row["status"]}
```

Task 4 fills in the `paired` branch; leaving it as a bare echo for now is fine because tests in this task only cover `pending` / `expired` / `404`.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: **9 passed** (2 + 3 + 4).

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_pairing.py
git -C /home/ahmed/signage commit -m "feat(backend): GET /screens/poll/{code} pending/expired"
```

---

## Task 4: `POST /screens/claim` + paired poll response

**Files:**
- Modify: `backend/main.py` — add `PairClaimRequest` + endpoint; extend `poll_pair_code` paired branch
- Test: `backend/tests/test_pairing.py` — append 5 tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pairing.py`:

```python
def _login_as_signed_up_org(pair_client) -> dict:
    """Use the OTP signup flow to get an admin session + org + its default screen.

    Returns {token, org, user, default_screen}. The default screen is created
    by hitting the existing POST /screens endpoint with a minimal body.
    """
    import uuid

    email = f"pair-{uuid.uuid4().hex[:8]}@example.com"
    business_name = f"Pair Biz {uuid.uuid4().hex[:6]}"
    r = pair_client.post(
        "/auth/signup/request",
        json={"business_name": business_name, "email": email},
    )
    otp = r.json()["dev_otp"]
    r = pair_client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = pair_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    session = r.json()
    auth = {"Authorization": f"Bearer {session['token']}"}
    r = pair_client.post("/screens", json={"name": "Default Display"}, headers=auth)
    assert r.status_code == 200, r.text
    return {
        "token": session["token"],
        "org": session["organization"],
        "user": session["user"],
        "default_screen": r.json(),
        "auth": auth,
    }


def test_claim_requires_auth(pair_client):
    r = pair_client.post("/screens/request_code", json={})
    code = r.json()["code"]
    r2 = pair_client.post("/screens/claim", json={"code": code, "screen_id": 1})
    assert r2.status_code == 401


def test_claim_happy_path_marks_paired_and_poll_returns_token(pair_client):
    ctx = _login_as_signed_up_org(pair_client)
    code = pair_client.post("/screens/request_code", json={}).json()["code"]
    r = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 200, r.text
    claim_data = r.json()
    assert claim_data["screen_id"] == ctx["default_screen"]["id"]
    assert claim_data["screen_name"] == "Default Display"

    r2 = pair_client.get(f"/screens/poll/{code}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "paired"
    assert body["screen_id"] == ctx["default_screen"]["id"]
    assert body["screen_name"] == "Default Display"
    assert body["screen_token"] == ctx["default_screen"]["token"]


def test_claim_rejects_screen_from_other_org(pair_client):
    ctx_a = _login_as_signed_up_org(pair_client)
    ctx_b = _login_as_signed_up_org(pair_client)
    code = pair_client.post("/screens/request_code", json={}).json()["code"]
    r = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx_a["default_screen"]["id"]},
        headers=ctx_b["auth"],
    )
    assert r.status_code == 404


def test_claim_rejects_expired_code(pair_client):
    from db import execute
    ctx = _login_as_signed_up_org(pair_client)
    code = pair_client.post("/screens/request_code", json={}).json()["code"]
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (past, code))
    r = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower()


def test_claim_is_idempotent_same_caller_same_screen(pair_client):
    ctx = _login_as_signed_up_org(pair_client)
    code = pair_client.post("/screens/request_code", json={}).json()["code"]
    r1 = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r1.status_code == 200
    r2 = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r2.status_code == 200
    assert r2.json()["screen_id"] == ctx["default_screen"]["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: the five new tests fail (404 on `/screens/claim`, paired poll shape missing).

- [ ] **Step 3: Implement the claim endpoint and extend poll**

In `backend/main.py`, immediately AFTER the `poll_pair_code` function, add:

```python
class PairClaimRequest(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)
    screen_id: int


@app.post("/screens/claim")
def claim_pair_code(
    payload: PairClaimRequest,
    user: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (payload.code,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown pairing code")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise HTTPException(status_code=400, detail="Pairing code expired. Ask the display to refresh.")

    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (payload.screen_id, user["organization_id"]),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    if row["status"] == "paired" and row.get("screen_id") and row["screen_id"] != screen["id"]:
        raise HTTPException(status_code=400, detail="This pairing code is already bound to another display.")

    execute(
        """
        UPDATE pairing_codes
           SET status = 'paired', screen_id = ?, claimed_at = ?
         WHERE code = ?
        """,
        (screen["id"], now.isoformat(), payload.code),
    )
    return {"screen_id": screen["id"], "screen_name": screen["name"]}
```

Now REPLACE the placeholder paired branch inside `poll_pair_code`. The final function body is:

```python
@app.get("/screens/poll/{code}")
def poll_pair_code(code: str) -> dict:
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (code,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown pairing code")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)

    if row["status"] == "pending" and now > expires_dt:
        return {"status": "expired"}

    if row["status"] == "pending":
        return {"status": "pending"}

    if row["status"] == "paired":
        screen = query_one("SELECT * FROM screens WHERE id = ?", (row["screen_id"],))
        if not screen:
            return {"status": "expired"}
        execute(
            "UPDATE screens SET last_seen = ? WHERE id = ?",
            (now.isoformat(), screen["id"]),
        )
        return {
            "status": "paired",
            "screen_id": screen["id"],
            "screen_name": screen["name"],
            "screen_token": screen["token"],
        }

    return {"status": row["status"]}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py -v
```

Expected: **14 passed** (2 + 3 + 4 + 5).

- [ ] **Step 5: Full regression**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: **36 passed** (22 existing + 14 new). Nothing else should regress — the legacy `/screens/pair` and `/screens/{token}/content` endpoints are untouched.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_pairing.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /screens/claim + paired poll response"
```

---

## Task 5: Integration smoke test

**Files:**
- Test: `backend/tests/test_pairing.py` — append 1 end-to-end test

- [ ] **Step 1: Write the integration test**

Append to `backend/tests/test_pairing.py`:

```python
def test_full_flow_request_claim_poll(pair_client):
    # 1. Player requests a code (unauthenticated)
    r = pair_client.post("/screens/request_code", json={})
    assert r.status_code == 200
    code = r.json()["code"]

    # 2. Player polls — still pending
    r = pair_client.get(f"/screens/poll/{code}")
    assert r.json()["status"] == "pending"

    # 3. Admin signs up, logs in, creates a display, and claims the code
    ctx = _login_as_signed_up_org(pair_client)
    r = pair_client.post(
        "/screens/claim",
        json={"code": code, "screen_id": ctx["default_screen"]["id"]},
        headers=ctx["auth"],
    )
    assert r.status_code == 200

    # 4. Player's next poll returns the paired screen's token
    r = pair_client.get(f"/screens/poll/{code}")
    body = r.json()
    assert body["status"] == "paired"
    token = body["screen_token"]

    # 5. Player can now fetch content with that token
    r = pair_client.get(f"/screens/{token}/content")
    assert r.status_code == 200
    assert "items" in r.json()
```

- [ ] **Step 2: Run it**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_pairing.py::test_full_flow_request_claim_poll -v
```

Expected: PASS.

- [ ] **Step 3: Full regression once more**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: **37 passed**.

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add backend/tests/test_pairing.py
git -C /home/ahmed/signage commit -m "test(backend): end-to-end pair request/claim/poll/content"
```

---

## Task 6: Merge + memory update

- [ ] **Step 1: Merge to main**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/pairing-pattern-b \
  -m "Merge Pattern B pairing backend: request_code / poll / claim"
git -C /home/ahmed/signage log --oneline -10
```

- [ ] **Step 2: Rebuild + restart backend**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build backend
```

- [ ] **Step 3: Smoke the live endpoints**

```bash
curl -s -X POST https://api.sawwii.com/screens/request_code | python3 -m json.tool
```

Expected: JSON with `code` (5 chars from the safe charset), `device_id` (32 hex), `expires_at`, `expires_in_seconds: 600`.

```bash
CODE=$(curl -s -X POST https://api.sawwii.com/screens/request_code | python3 -c "import sys,json;print(json.load(sys.stdin)['code'])")
curl -s https://api.sawwii.com/screens/poll/$CODE | python3 -m json.tool
```

Expected: `{"status": "pending"}`.

- [ ] **Step 4: Update roadmap memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`. Under "Current state", add:

```
**Pairing backend (Pattern B)** (`feature/pairing-pattern-b` → merged 2026-04-22):
- `POST /screens/request_code` (player): returns 5-char code (charset `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, no `O/0/I/1/L`) + device_id + 10-min expiry
- `GET /screens/poll/{code}`: returns `{status: pending|expired|paired}`; paired includes `screen_id`, `screen_name`, `screen_token` and bumps `screens.last_seen`
- `POST /screens/claim` (admin/editor auth): binds code to a screen in the caller's org; idempotent on same (caller, screen)
- New `pairing_codes` table; legacy `POST /screens/pair` + `screens.pair_code` column untouched (retired in Plan 4)
- 37 regression tests passing (22 existing + 15 new pairing)
```

Mark Plan 2 (player QR UI) as the next item.

- [ ] **Step 5: Final report**

One-paragraph summary: commits shipped, endpoints added, tests added, and the ready-for-Plan-2 signal (player can start calling `/screens/request_code`).

---

## Out of Scope (explicit, handled in later plans)

- Player-side UI (Plan 2): QR rendering, code-entry fallback, polling loop, client-side caching.
- Admin `/pair?code=…` page (Plan 3): the phone-side page that scans the QR and calls `/screens/claim`.
- Simple-mode dashboard rework (Plan 3).
- Retire the legacy `POST /screens/pair` + `screens.pair_code` column (Plan 4, after Plans 2+3 ship and the old path has no more callers).
- Cleanup job for stale `pairing_codes` rows (schedule-driven sweep). Not needed for MVP — rows are capped at `count(devices) × TTL`; low cardinality.
- Rate limiting per IP on `request_code` (follow-up — add with CAPTCHA or Cloudflare rules once abuse shows up).

## Resume Notes (context-limit mid-plan)

- Progress: `git log --oneline feature/pairing-pattern-b` shows completed tasks by their `feat(backend):` / `test(backend):` commit prefixes.
- All tasks are independently testable. Task 4 depends on Tasks 1–3 being live. Task 5 is a black-box integration test that exercises everything; if it fails, Tasks 1–4 are individually green while Task 5 is red means the problem is glue (e.g., `last_seen` update breaking `sanitize_screen`).
- If you hit schema errors on first run: exec into the backend container and `DELETE FROM pairing_codes;` — stale rows from earlier test attempts can keep `device_id` / `code` UNIQUE constraints locked.
