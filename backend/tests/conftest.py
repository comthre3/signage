import uuid

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
        "password": "testpass1",
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
