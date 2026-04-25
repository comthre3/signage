import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def billing_env(monkeypatch):
    monkeypatch.setenv("NIUPAY_API_KEY", "test-key")
    monkeypatch.setenv("NIUPAY_MODE", "1")
    monkeypatch.setenv("NIUPAY_CALLBACK_SECRET", "deadbeef" * 8)


@pytest.fixture
def mock_niupay():
    """Patch backend.billing.create_knet_request to return a canned success."""
    with patch("main.create_knet_request") as m:
        m.return_value = {
            "status": True,
            "message": "Proceed to Knet",
            "paymentID": "6555084431783610",
            "paymentLink": "https://www.knetpaytest.com.kw/hppaction/fake",
        }
        yield m


def test_checkout_happy_path_creates_pending_row(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    token = signed_up_org["token"]
    r = client.post(
        "/billing/checkout",
        json={"tier": "starter", "term_months": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["payment_url"] == "https://www.knetpaytest.com.kw/hppaction/fake"
    assert data["trackid"].startswith("pay_")
    # Niupay called exactly once with our canonical payload
    assert mock_niupay.call_count == 1
    kwargs = mock_niupay.call_args.kwargs
    assert kwargs["amount_kwd"] == 3
    assert kwargs["response_url"].startswith("https://api.khanshoof.com/billing/callback/")
    assert "?s=" in kwargs["response_url"]


def test_checkout_rejects_unknown_tier(client: TestClient, signed_up_org: dict):
    r = client.post(
        "/billing/checkout",
        json={"tier": "platinum", "term_months": 1},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 422


def test_checkout_rejects_unknown_term(client: TestClient, signed_up_org: dict):
    r = client.post(
        "/billing/checkout",
        json={"tier": "starter", "term_months": 3},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 422


def test_checkout_requires_auth(client: TestClient):
    r = client.post("/billing/checkout", json={"tier": "starter", "term_months": 1})
    assert r.status_code == 401


def test_checkout_rate_limits_duplicate_pending(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    token = signed_up_org["token"]
    first = client.post(
        "/billing/checkout",
        json={"tier": "growth", "term_months": 6},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = client.post(
        "/billing/checkout",
        json={"tier": "growth", "term_months": 6},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["trackid"] == second.json()["trackid"]
    assert mock_niupay.call_count == 1   # second call reused first row's URL


def _pending_payment(client: TestClient, signed_up_org: dict, tier: str, term: int, mock_niupay) -> str:
    r = client.post(
        "/billing/checkout",
        json={"tier": tier, "term_months": term},
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert r.status_code == 200
    return r.json()["trackid"]


def test_callback_rejects_wrong_secret(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "starter", 1, mock_niupay)
    r = client.post(
        f"/billing/callback/{trackid}?s=wrong",
        json={"result": "CAPTURED", "trackid": trackid, "paymentID": "x", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 404


def test_callback_unknown_trackid_is_200_noop(client: TestClient):
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/pay_nope?s={secret}",
        json={"result": "CAPTURED", "trackid": "pay_nope"},
    )
    assert r.status_code == 200


def test_callback_captured_transitions_payment_and_org(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "starter", 6, mock_niupay)
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/{trackid}?s={secret}",
        json={"result": "CAPTURED", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 200
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    data = status.json()
    assert data["status"] == "captured"
    assert data["tier"] == "starter"
    assert data["term_months"] == 6
    assert data["paid_through_at"] is not None


def test_callback_non_captured_marks_failed(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "growth", 1, mock_niupay)
    secret = "deadbeef" * 8
    r = client.post(
        f"/billing/callback/{trackid}?s={secret}",
        json={"result": "HOST_TIMEOUT", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 200
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    )
    assert status.json()["status"] == "failed"


def test_callback_captured_is_idempotent(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    trackid = _pending_payment(client, signed_up_org, "pro", 12, mock_niupay)
    secret = "deadbeef" * 8
    body = {"result": "CAPTURED", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"}
    first  = client.post(f"/billing/callback/{trackid}?s={secret}", json=body)
    second = client.post(f"/billing/callback/{trackid}?s={secret}", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    status = client.get(
        f"/billing/status/{trackid}",
        headers={"Authorization": f"Bearer {signed_up_org['token']}"},
    ).json()
    paid_through = status["paid_through_at"]
    assert paid_through is not None
