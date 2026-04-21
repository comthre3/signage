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
    response = client.post("/auth/signup", json=unique_business)
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "token": data["token"],
        "org": data["organization"],
        "user": data["user"],
    }
