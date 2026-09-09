import os
import uuid

# Force tests onto a dedicated database before `db`/`main` ever connect.
# Never trust the ambient DATABASE_URL as-is (it's the production DSN from
# .env) — tests must not be able to write to production even by accident.
# Reuse its host/credentials but always pin the database name to sawwii_test
# unless TEST_DATABASE_URL is explicitly set.
_prod_dsn = os.environ.get(
    "DATABASE_URL", "postgresql://sawwii:sawwii@postgres:5432/sawwii"
)
_base, _, _ = _prod_dsn.rpartition("/")
_TEST_DSN = os.environ.get("TEST_DATABASE_URL", f"{_base}/sawwii_test")
_dbname = _TEST_DSN.rsplit("/", 1)[-1].split("?")[0]
if "test" not in _dbname.lower():
    raise RuntimeError(
        f"Refusing to run tests against database {_dbname!r} — it doesn't "
        "look like a test database. Set TEST_DATABASE_URL to a DSN whose "
        "database name contains 'test'."
    )
os.environ["DATABASE_URL"] = _TEST_DSN

import pytest
from fastapi.testclient import TestClient

from db import init_db
from main import app


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema() -> None:
    init_db()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def unique_business() -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "business_name": f"Test Biz {suffix}",
        "email": f"owner-{suffix}@example.com",
        "password": "Khanshoof2026Test",
    }


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
