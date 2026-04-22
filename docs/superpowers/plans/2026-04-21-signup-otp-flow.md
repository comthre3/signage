# Signup OTP Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current one-shot `POST /auth/signup` with a three-step email-OTP signup flow: (1) request code, (2) verify code, (3) set password and provision the org/user.

**Architecture:** Backend adds a `pending_signups` table and three endpoints (`/auth/signup/request`, `/auth/signup/verify`, `/auth/signup/complete`). Email delivery is stubbed in dev via a `DEV_MODE=1` env flag that logs the OTP and returns it in the `/auth/signup/request` response so the full flow works locally without an email provider. The legacy `/auth/signup` endpoint is removed. Frontend becomes a 3-step wizard in the existing auth panel. Production email (Resend) will be wired in a follow-up plan once the Resend API key + domain are live.

**Tech Stack:** FastAPI + Pydantic (backend), Postgres 16 via `backend/db.py`, pytest for regression, vanilla JS (no framework) for the frontend wizard.

**OTP parameters (decided):** 6-digit numeric, 10-min expiry, 5 attempts max, 60s resend cooldown. Verification token (post-OTP, pre-password) is a 32-char hex string with a 15-min expiry, single-use.

---

## Prerequisites

Work from `/home/ahmed/signage` on a new branch `feature/signup-otp` off `main`.

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage pull --ff-only 2>&1 || true
git -C /home/ahmed/signage checkout -b feature/signup-otp
```

Docker stack must be up. Regression baseline must be 7 passed:

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

## File Structure

```
backend/
├── main.py             # add 3 endpoints, add OTP helpers, REMOVE /auth/signup
├── db.py               # add pending_signups table in init_db()
└── tests/
    ├── conftest.py     # rewire signed_up_org fixture through 3-step flow
    ├── test_multi_tenancy.py # rewire _signup helper through 3-step flow
    └── test_signup_otp.py    # NEW — unit-ish tests of the three endpoints

frontend/
├── index.html          # replace single signup form with 3 step forms
└── app.js              # replace signup handler with state machine driving 3 forms

docker-compose.yml      # add DEV_MODE=1 to backend service env
```

No new files beyond `test_signup_otp.py`.

---

## Task 1: DB table + OTP helpers + DEV_MODE env

**Files:**
- Modify: `backend/db.py` — add `pending_signups` in `init_db()`
- Modify: `backend/main.py` — add constants, helpers (`generate_otp`, `hash_otp`, `verify_otp`), read `DEV_MODE`
- Modify: `docker-compose.yml` — add `DEV_MODE=1` under `backend.environment`
- Test: `backend/tests/test_signup_otp.py` — NEW

- [ ] **Step 1: Write the failing test for OTP helpers**

Create `backend/tests/test_signup_otp.py`:

```python
from main import generate_otp, hash_otp, verify_otp


def test_generate_otp_is_six_digits_numeric():
    otp = generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()


def test_otp_hash_roundtrip():
    otp = "123456"
    stored = hash_otp(otp)
    assert stored != otp
    assert verify_otp(otp, stored) is True
    assert verify_otp("000000", stored) is False


def test_verify_otp_none_stored_returns_false():
    assert verify_otp("123456", None) is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: import errors / `AttributeError: module 'main' has no attribute 'generate_otp'`.

- [ ] **Step 3: Add OTP helpers + constants to `backend/main.py`**

Add the following block immediately AFTER the existing `verify_password` function (around line 147):

```python
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))
VERIFICATION_TOKEN_TTL_SECONDS = int(os.getenv("VERIFICATION_TOKEN_TTL_SECONDS", "900"))
DEV_MODE = os.getenv("DEV_MODE", "0").lower() in ("1", "true", "yes")


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", otp.encode(), salt, 120000)
    return f"{salt.hex()}${digest.hex()}"


def verify_otp(otp: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", otp.encode(), salt, 120000)
    return secrets.compare_digest(digest.hex(), digest_hex)


def send_signup_otp_email(to_email: str, business_name: str, otp: str) -> None:
    """Dev stub for outbound signup email.

    DEV_MODE writes the OTP to the container log so operators can recover it
    locally without a provider. Production use (Resend) is wired in a separate
    plan once the sawwii.com DNS is pointed and an API key is issued.
    """
    logger.info("SIGNUP_OTP for %s (%s): %s", to_email, business_name, otp)
```

- [ ] **Step 4: Run helper test to confirm it passes**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Add `pending_signups` table to `backend/db.py`**

Locate the `init_db()` function. After the existing `screen_zone_templates` CREATE TABLE (near line 257), add:

```python
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_signups (
                id                             SERIAL PRIMARY KEY,
                email                          TEXT NOT NULL UNIQUE,
                business_name                  TEXT NOT NULL,
                otp_hash                       TEXT NOT NULL,
                attempts                       INTEGER NOT NULL DEFAULT 0,
                expires_at                     TEXT NOT NULL,
                last_sent_at                   TEXT NOT NULL,
                verification_token             TEXT,
                verification_token_expires_at  TEXT,
                created_at                     TEXT NOT NULL
            )
        """)
```

- [ ] **Step 6: Add `DEV_MODE=1` to docker-compose.yml**

Open `/home/ahmed/signage/docker-compose.yml`. Find the `backend:` service's `environment:` block. Add `- DEV_MODE=1` to the list. If `environment:` does not exist under `backend:`, create it.

If the file uses the mapping form (`environment: KEY: value`) rather than the list form, add `DEV_MODE: "1"` instead. Either form is valid YAML.

- [ ] **Step 7: Rebuild backend and run full suite to confirm nothing regressed**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: 10 passed (7 existing + 3 new OTP helper tests).

- [ ] **Step 8: Commit**

```bash
git -C /home/ahmed/signage add backend/db.py backend/main.py backend/tests/test_signup_otp.py docker-compose.yml
git -C /home/ahmed/signage commit -m "feat(backend): OTP helpers + pending_signups table + DEV_MODE flag"
```

---

## Task 2: `POST /auth/signup/request`

**Files:**
- Modify: `backend/main.py` — add `SignupRequestStart` model + endpoint
- Test: `backend/tests/test_signup_otp.py` — add 4 tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_signup_otp.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def otp_client() -> TestClient:
    return TestClient(app)


def _fresh_email() -> str:
    return f"otp-{uuid.uuid4().hex[:8]}@example.com"


def test_signup_request_happy_path_returns_dev_otp(otp_client):
    email = _fresh_email()
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "otp_sent"
    assert "dev_otp" in data
    assert len(data["dev_otp"]) == 6 and data["dev_otp"].isdigit()


def test_signup_request_rejects_invalid_email(otp_client):
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": "notanemail"},
    )
    assert r.status_code == 400


def test_signup_request_rejects_already_registered_email(otp_client):
    email = _fresh_email()
    # First complete a signup by running the full flow — but for now just seed a user
    from db import execute
    from main import hash_password, utc_now_iso
    org_id = execute(
        """
        INSERT INTO organizations (name, slug, plan, screen_limit, subscription_status, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (f"Seed {email}", f"seed-{uuid.uuid4().hex[:6]}", "starter", 3, "trialing", "en", utc_now_iso()),
    )
    execute(
        """
        INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (org_id, email, hash_password("seeded1x"), 1, "admin", utc_now_iso()),
    )
    r = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 400
    assert "registered" in r.json()["detail"].lower()


def test_signup_request_cooldown_blocks_rapid_resend(otp_client):
    email = _fresh_email()
    r1 = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r1.status_code == 200
    r2 = otp_client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r2.status_code == 429
    assert "wait" in r2.json()["detail"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: the four new tests fail with 404 (endpoint not found).

- [ ] **Step 3: Implement the endpoint**

In `backend/main.py`, REPLACE the existing `signup` function (currently lines ~450–508, the `@app.post("/auth/signup")` handler) with the new request endpoint. Paste this block in place of the old one:

```python
class SignupStartRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=200)


@app.post("/auth/signup/request")
def signup_request(payload: SignupStartRequest) -> dict:
    email = payload.email.strip().lower()
    business_name = payload.business_name.strip()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        raise HTTPException(status_code=400, detail="Email is already registered")

    now = datetime.now(timezone.utc)
    existing = query_one("SELECT last_sent_at FROM pending_signups WHERE email = ?", (email,))
    if existing and existing.get("last_sent_at"):
        try:
            last_sent_dt = datetime.fromisoformat(existing["last_sent_at"])
            if (now - last_sent_dt).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {OTP_RESEND_COOLDOWN_SECONDS} seconds before requesting another code.",
                )
        except ValueError:
            pass

    otp = generate_otp()
    otp_hash_val = hash_otp(otp)
    expires_at = (now + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    now_iso = now.isoformat()

    if existing:
        execute(
            """
            UPDATE pending_signups
               SET business_name = ?, otp_hash = ?, attempts = 0,
                   expires_at = ?, last_sent_at = ?,
                   verification_token = NULL, verification_token_expires_at = NULL
             WHERE email = ?
            """,
            (business_name, otp_hash_val, expires_at, now_iso, email),
        )
    else:
        execute(
            """
            INSERT INTO pending_signups
              (email, business_name, otp_hash, attempts, expires_at, last_sent_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (email, business_name, otp_hash_val, 0, expires_at, now_iso, now_iso),
        )

    send_signup_otp_email(email, business_name, otp)
    response: dict = {"status": "otp_sent", "expires_in_seconds": OTP_TTL_SECONDS}
    if DEV_MODE:
        response["dev_otp"] = otp
    return response
```

Also DELETE the now-orphaned `SignupRequest` Pydantic class (the one with `business_name + email + password`) from the same file — it's replaced by `SignupStartRequest`. Search for `class SignupRequest` near line 401 and remove those four lines.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: 7 passed (3 helpers + 4 request). Existing tests in `test_multi_tenancy.py` WILL fail now since `/auth/signup` is gone — that's expected. We fix them in Task 5.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_signup_otp.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /auth/signup/request issues OTP"
```

---

## Task 3: `POST /auth/signup/verify`

**Files:**
- Modify: `backend/main.py` — add endpoint
- Test: `backend/tests/test_signup_otp.py` — 4 more tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_signup_otp.py`:

```python
def _request_otp(client, email: str) -> str:
    r = client.post(
        "/auth/signup/request",
        json={"business_name": "Kebab Corner", "email": email},
    )
    assert r.status_code == 200, r.text
    return r.json()["dev_otp"]


def test_signup_verify_happy_path_returns_token(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["verification_token"]) >= 32
    assert data["business_name"] == "Kebab Corner"


def test_signup_verify_wrong_otp_increments_attempts(otp_client):
    email = _fresh_email()
    _request_otp(otp_client, email)
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    assert r.status_code == 400
    assert "incorrect" in r.json()["detail"].lower()


def test_signup_verify_locks_after_max_attempts(otp_client):
    email = _fresh_email()
    _request_otp(otp_client, email)
    for _ in range(5):
        otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    r = otp_client.post("/auth/signup/verify", json={"email": email, "otp": "000000"})
    assert r.status_code == 400
    assert "too many" in r.json()["detail"].lower()


def test_signup_verify_unknown_email_fails(otp_client):
    r = otp_client.post(
        "/auth/signup/verify",
        json={"email": "nobody@example.com", "otp": "123456"},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py::test_signup_verify_happy_path_returns_token -v
```

Expected: 404 (endpoint missing).

- [ ] **Step 3: Implement the endpoint**

In `backend/main.py`, immediately AFTER the `signup_request` function (from Task 2), add:

```python
class SignupVerifyRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    otp: str = Field(..., min_length=6, max_length=6)


@app.post("/auth/signup/verify")
def signup_verify(payload: SignupVerifyRequest) -> dict:
    email = payload.email.strip().lower()
    row = query_one("SELECT * FROM pending_signups WHERE email = ?", (email,))
    if not row:
        raise HTTPException(status_code=400, detail="No pending signup for this email")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise HTTPException(status_code=400, detail="Code expired. Please request a new one.")

    if (row.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many incorrect attempts. Request a new code.")

    if not verify_otp(payload.otp, row.get("otp_hash")):
        execute(
            "UPDATE pending_signups SET attempts = attempts + 1 WHERE email = ?",
            (email,),
        )
        raise HTTPException(status_code=400, detail="Incorrect code")

    verification_token = secrets.token_hex(16)
    verification_expires = (now + timedelta(seconds=VERIFICATION_TOKEN_TTL_SECONDS)).isoformat()
    execute(
        """
        UPDATE pending_signups
           SET verification_token = ?, verification_token_expires_at = ?, attempts = 0
         WHERE email = ?
        """,
        (verification_token, verification_expires, email),
    )
    return {
        "verification_token": verification_token,
        "business_name": row["business_name"],
        "expires_in_seconds": VERIFICATION_TOKEN_TTL_SECONDS,
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: 11 passed (3 helpers + 4 request + 4 verify).

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_signup_otp.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /auth/signup/verify validates OTP"
```

---

## Task 4: `POST /auth/signup/complete`

**Files:**
- Modify: `backend/main.py` — add endpoint
- Test: `backend/tests/test_signup_otp.py` — 4 more tests

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_signup_otp.py`:

```python
def _verify_otp(client, email: str, otp: str) -> str:
    r = client.post("/auth/signup/verify", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    return r.json()["verification_token"]


def test_signup_complete_happy_path_returns_session(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"]
    assert data["user"]["username"] == email
    assert data["user"]["role"] == "admin"
    assert data["organization"]["plan"] == "starter"
    assert data["organization"]["subscription_status"] == "trialing"


def test_signup_complete_rejects_invalid_token(otp_client):
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": "deadbeef" * 4, "password": "testpass1"},
    )
    assert r.status_code == 400


def test_signup_complete_rejects_reused_token(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r1 = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r1.status_code == 200
    r2 = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "testpass1"},
    )
    assert r2.status_code == 400


def test_signup_complete_enforces_password_policy(otp_client):
    email = _fresh_email()
    otp = _request_otp(otp_client, email)
    vt = _verify_otp(otp_client, email, otp)
    r = otp_client.post(
        "/auth/signup/complete",
        json={"verification_token": vt, "password": "short"},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py::test_signup_complete_happy_path_returns_session -v
```

Expected: 404.

- [ ] **Step 3: Implement the endpoint**

In `backend/main.py`, immediately AFTER the `signup_verify` function, add:

```python
class SignupCompleteRequest(BaseModel):
    verification_token: str = Field(..., min_length=32, max_length=64)
    password: str = Field(..., min_length=8)


@app.post("/auth/signup/complete")
def signup_complete(payload: SignupCompleteRequest) -> dict:
    validate_password(payload.password)
    row = query_one(
        "SELECT * FROM pending_signups WHERE verification_token = ?",
        (payload.verification_token,),
    )
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    now = datetime.now(timezone.utc)
    try:
        vt_expires_dt = datetime.fromisoformat(row["verification_token_expires_at"])
    except (TypeError, ValueError):
        vt_expires_dt = now - timedelta(seconds=1)
    if now > vt_expires_dt:
        raise HTTPException(status_code=400, detail="Verification token expired. Please restart signup.")

    email = row["email"]
    business_name = row["business_name"]

    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        execute("DELETE FROM pending_signups WHERE email = ?", (email,))
        raise HTTPException(status_code=400, detail="Email is already registered")

    slug_base = slugify(business_name)
    slug = slug_base
    counter = 1
    while query_one("SELECT id FROM organizations WHERE slug = ?", (slug,)):
        counter += 1
        slug = f"{slug_base}-{counter}"

    plan_key = "starter"
    plan = PLANS[plan_key]
    trial_ends_at = (now + timedelta(days=14)).isoformat()

    new_org_id = execute(
        """
        INSERT INTO organizations
        (name, slug, plan, screen_limit, subscription_status, trial_ends_at, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (business_name, slug, plan_key, plan["screen_limit"],
         "trialing", trial_ends_at, "en", utc_now_iso()),
    )
    user_id = execute(
        """
        INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_org_id, email, hash_password(payload.password), 1, "admin", utc_now_iso()),
    )
    session_token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user_id, session_token, utc_now_iso(), utc_now_iso()),
    )
    execute("DELETE FROM pending_signups WHERE email = ?", (email,))

    return {
        "token": session_token,
        "user": {
            "id": user_id,
            "username": email,
            "role": "admin",
            "is_admin": True,
        },
        "organization": {
            "id": new_org_id,
            "name": business_name,
            "slug": slug,
            "plan": plan_key,
            "screen_limit": plan["screen_limit"],
            "subscription_status": "trialing",
            "trial_ends_at": trial_ends_at,
        },
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build backend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d backend
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest tests/test_signup_otp.py -v
```

Expected: 15 passed (3 helpers + 4 request + 4 verify + 4 complete).

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py backend/tests/test_signup_otp.py
git -C /home/ahmed/signage commit -m "feat(backend): POST /auth/signup/complete creates org + session"
```

---

## Task 5: Rewire existing tests to the new flow

**Files:**
- Modify: `backend/tests/conftest.py` — rewire `signed_up_org` fixture
- Modify: `backend/tests/test_multi_tenancy.py` — rewire `_signup` helper

The pre-existing suite still uses `POST /auth/signup` in `conftest.py` and `test_multi_tenancy.py`. Those tests are currently RED (endpoint gone). This task makes them green via the 3-step flow.

- [ ] **Step 1: Confirm current failure**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest -v
```

Expected: `test_signup_otp.py::*` all pass (15). `test_multi_tenancy.py::*` and any other test that uses the `signed_up_org` fixture fail with 404/422 on `/auth/signup`.

- [ ] **Step 2: Rewire `conftest.py`**

Open `backend/tests/conftest.py`. Replace the `signed_up_org` fixture (currently lines 30–39) with:

```python
@pytest.fixture
def signed_up_org(client: TestClient, unique_business: dict) -> dict:
    r = client.post(
        "/auth/signup/request",
        json={
            "business_name": unique_business["business_name"],
            "email": unique_business["email"],
        },
    )
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post(
        "/auth/signup/verify",
        json={"email": unique_business["email"], "otp": otp},
    )
    assert r.status_code == 200, r.text
    verification_token = r.json()["verification_token"]
    r = client.post(
        "/auth/signup/complete",
        json={
            "verification_token": verification_token,
            "password": unique_business["password"],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return {
        "token": data["token"],
        "org": data["organization"],
        "user": data["user"],
    }
```

No other fixtures change. `unique_business` still returns the same dict shape.

- [ ] **Step 3: Rewire `test_multi_tenancy.py`**

Open `backend/tests/test_multi_tenancy.py`. Replace the `_signup` helper (lines 8–18) with:

```python
def _signup(client, suffix: str) -> dict:
    email = f"owner-{suffix}@example.com"
    business_name = f"Biz {suffix}"
    password = "testpass1"
    r = client.post(
        "/auth/signup/request",
        json={"business_name": business_name, "email": email},
    )
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post(
        "/auth/signup/verify",
        json={"email": email, "otp": otp},
    )
    assert r.status_code == 200, r.text
    verification_token = r.json()["verification_token"]
    r = client.post(
        "/auth/signup/complete",
        json={"verification_token": verification_token, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()
```

- [ ] **Step 4: Run full suite**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest -v
```

Expected: all 22 tests pass (7 original + 15 new OTP tests). If `test_plan_limits.py` or `test_smoke.py` uses `/auth/signup` directly (not via the fixtures), rewire them the same way — use the fixture if possible; otherwise inline the same 3-call sequence.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add backend/tests/conftest.py backend/tests/test_multi_tenancy.py
# plus any other test files touched in Step 4
git -C /home/ahmed/signage commit -m "test(backend): rewire existing tests through OTP signup flow"
```

---

## Task 6: Frontend signup wizard

**Files:**
- Modify: `frontend/index.html` — replace single signup form with 3 sequential forms
- Modify: `frontend/app.js` — replace signup submit handler with wizard state machine

- [ ] **Step 1: Replace the signup form markup in `frontend/index.html`**

Find the existing `<form id="signup-form" class="auth-form hidden">…</form>` block (currently lines 54–62). Replace that whole block with:

```html
        <form id="signup-request-form" class="auth-form hidden">
          <input type="text"  id="signup-business" placeholder="Business name" required maxlength="100" autocomplete="organization" />
          <input type="email" id="signup-email"    placeholder="Work email"    required autocomplete="email" />
          <button type="submit">Send Code</button>
          <div class="helper-text">
            We'll email you a 6-digit code to verify your address before you set a password.
          </div>
        </form>

        <form id="signup-verify-form" class="auth-form hidden">
          <div class="helper-text" id="signup-verify-intro">
            Enter the 6-digit code sent to <strong id="signup-verify-email">—</strong>.
          </div>
          <input type="text" id="signup-otp" placeholder="123456" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" />
          <button type="submit">Verify</button>
          <div class="helper-text">
            <a href="#" id="signup-resend">Resend code</a> ·
            <a href="#" id="signup-change-email">Use a different email</a>
          </div>
          <div class="helper-text hidden" id="signup-dev-otp"></div>
        </form>

        <form id="signup-password-form" class="auth-form hidden">
          <div class="helper-text">
            Email verified. Set a password to finish creating your account.
          </div>
          <input type="password" id="signup-new-password"     placeholder="Password (min 8, letters + numbers)" required autocomplete="new-password" minlength="8" />
          <input type="password" id="signup-confirm-password" placeholder="Confirm password"                    required autocomplete="new-password" minlength="8" />
          <button type="submit">Create Account</button>
          <div class="helper-text">
            You'll get the <strong>Starter</strong> plan (up to 3 screens) free for 14 days. Cancel anytime.
          </div>
        </form>
```

- [ ] **Step 2: Replace the signup submit handler in `frontend/app.js`**

Find the `/* ── Signup ── */` comment block (currently starts near line 1102) and its handler (ends near line 1123, before `document.getElementById("logout-btn")`). Replace that whole block with:

```javascript
/* ── Signup (3-step OTP wizard) ──────────────────────────────── */
const signupState = { email: "", business_name: "", verification_token: "" };

function signupShowStep(step) {
  const forms = {
    request:  document.getElementById("signup-request-form"),
    verify:   document.getElementById("signup-verify-form"),
    password: document.getElementById("signup-password-form"),
  };
  Object.entries(forms).forEach(([key, form]) => {
    form.classList.toggle("hidden", key !== step);
  });
  const focusMap = {
    request:  "signup-business",
    verify:   "signup-otp",
    password: "signup-new-password",
  };
  document.getElementById(focusMap[step])?.focus();
}

function signupResetDevOtpHint(otp) {
  const el = document.getElementById("signup-dev-otp");
  if (!el) return;
  if (otp) {
    el.textContent = `Dev mode: your code is ${otp}`;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

document.getElementById("signup-request-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const business_name = document.getElementById("signup-business").value.trim();
  const email         = document.getElementById("signup-email").value.trim();
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/request", {
        method: "POST",
        body: JSON.stringify({ business_name, email }),
      });
      signupState.email = email;
      signupState.business_name = business_name;
      document.getElementById("signup-verify-email").textContent = email;
      signupResetDevOtpHint(data.dev_otp || "");
      signupShowStep("verify");
      toast("Code sent. Check the email (or dev log).", "success");
    });
  } catch (err) {
    toast(err.message || "Couldn't send code.", "error");
  }
});

document.getElementById("signup-verify-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const otp = document.getElementById("signup-otp").value.trim();
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/verify", {
        method: "POST",
        body: JSON.stringify({ email: signupState.email, otp }),
      });
      signupState.verification_token = data.verification_token;
      signupShowStep("password");
    });
  } catch (err) {
    toast(err.message || "Verification failed.", "error");
  }
});

document.getElementById("signup-resend").addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/auth/signup/request", {
      method: "POST",
      body: JSON.stringify({
        business_name: signupState.business_name,
        email: signupState.email,
      }),
    });
    signupResetDevOtpHint(data.dev_otp || "");
    toast("New code sent.", "success");
  } catch (err) {
    toast(err.message || "Couldn't resend code.", "error");
  }
});

document.getElementById("signup-change-email").addEventListener("click", (e) => {
  e.preventDefault();
  signupState.email = "";
  signupState.verification_token = "";
  signupResetDevOtpHint("");
  document.getElementById("signup-otp").value = "";
  signupShowStep("request");
});

document.getElementById("signup-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const password        = document.getElementById("signup-new-password").value;
  const confirmPassword = document.getElementById("signup-confirm-password").value;
  if (password !== confirmPassword) {
    toast("Passwords do not match.", "error");
    return;
  }
  try {
    await withLoading(btn, async () => {
      const data = await api("/auth/signup/complete", {
        method: "POST",
        body: JSON.stringify({
          verification_token: signupState.verification_token,
          password,
        }),
      });
      setAuth(data.token, data.user);
      showDashboard();
      await bootData();
      toast(`Welcome to Sawwii, ${signupState.business_name}! Your 14-day trial is active.`, "success", 6000);
      signupState.email = "";
      signupState.business_name = "";
      signupState.verification_token = "";
    });
  } catch (err) {
    toast(err.message || "Sign-up failed.", "error");
  }
});
```

- [ ] **Step 3: Update `showAuthTab()` in `frontend/app.js` so it re-enters the wizard at step 1**

Find `function showAuthTab(which)` (near line 1083). In the existing body, REPLACE the line:

```javascript
  signupForm.classList.toggle("hidden", !isSignup);
```

with:

```javascript
  // three-step wizard — always re-enter at the request step
  const signupRequestForm  = document.getElementById("signup-request-form");
  const signupVerifyForm   = document.getElementById("signup-verify-form");
  const signupPasswordForm = document.getElementById("signup-password-form");
  signupRequestForm .classList.toggle("hidden", !isSignup);
  signupVerifyForm  .classList.add("hidden");
  signupPasswordForm.classList.add("hidden");
```

Also REPLACE the line:

```javascript
  const signupForm = document.getElementById("signup-form");
```

by removing it (no longer needed — the `signupForm` variable is no longer referenced).

- [ ] **Step 4: Rebuild frontend and exercise the flow in the browser**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml build frontend
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d frontend
```

Open `http://192.168.18.192:3000/` in an incognito window. Click **Create Account**. Expect:
1. Step 1: business + email fields. Enter values → click Send Code.
2. Step 2: OTP field appears. Dev-mode hint shows the code. Enter it → click Verify.
3. Step 3: password + confirm fields. Enter matching password → click Create Account.
4. Land on dashboard. Trial card visible.

Also retest the already-working paths:
- Sign In tab still logs an existing user in.
- "Use a different email" link on step 2 resets to step 1.
- "Resend code" link on step 2 pings the request endpoint (after 60s cooldown).

- [ ] **Step 5: Run the backend suite one more time**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: 22 passed.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add frontend/index.html frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): 3-step signup wizard (request/verify/password)"
```

---

## Task 7: Merge + memory update

- [ ] **Step 1: Final regression on the branch**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected: 22 passed.

- [ ] **Step 2: Merge to main**

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/signup-otp -m "Merge signup OTP flow: 3-step request/verify/complete"
git -C /home/ahmed/signage log --oneline -10
```

- [ ] **Step 3: Update roadmap memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`. Under the "Current state" section, add:

```
**Signup OTP flow** (`feature/signup-otp` → merged 2026-04-21):
- 3-step signup: `POST /auth/signup/request` → `POST /auth/signup/verify` → `POST /auth/signup/complete`
- `pending_signups` table; 6-digit OTP, 10-min TTL, 5 attempts, 60s resend cooldown
- Dev mode: OTP returned in response AND logged (backend env `DEV_MODE=1`)
- Old `POST /auth/signup` removed
- 22 regression tests passing
```

Also add to "Open items to confirm before resuming":

```
- Resend API key + verified sender domain (sawwii.com DNS pointed) — needed to replace the dev stub in `send_signup_otp_email()`
- `DEV_MODE` MUST be unset in production before launch (currently `=1` in docker-compose.yml)
```

- [ ] **Step 4: Final report**

One-paragraph summary: commits shipped, endpoints added, tests added, any remaining items for a follow-up plan (Resend wiring, production DEV_MODE flip).

---

## Resume Notes (context-limit mid-plan)

- Task progress: `git -C /home/ahmed/signage log --oneline feature/signup-otp` shows done tasks by `feat(backend):` / `feat(frontend):` prefixes.
- Each task is testable in isolation. Tasks 2–4 rely on Task 1's helpers + table. Task 5 rewires the existing fixtures to match. Task 6 depends on endpoints being live.
- If you hit a DB error on a first run, exec into the backend container and `DELETE FROM pending_signups;` — the table may have stale rows from earlier test attempts.

## Out of Scope (explicit)

- Resend (or any real email provider) integration — follow-up plan once `RESEND_API_KEY` is issued and `sawwii.com` MX is live.
- Rate limiting per IP (beyond per-email cooldown).
- Bot / CAPTCHA protection on signup.
- Soft-delete / retention policy for `pending_signups` rows — rows are removed on success and overwritten on re-request, which is sufficient for now.
- Admin UI for inspecting pending signups.
- Localized OTP email copy (EN/AR) — part of the bilingual Phase 2 plan.
