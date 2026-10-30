import os
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from db import execute, query_one


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


# ── Renewals and plan changes keep the time already paid for ──────────

SECRET = "deadbeef" * 8


def _capture(client: TestClient, trackid: str) -> None:
    r = client.post(
        f"/billing/callback/{trackid}?s={SECRET}",
        json={"result": "CAPTURED", "trackid": trackid, "paymentID": "p", "tranid": "t", "ref": "r", "niutrack": "n"},
    )
    assert r.status_code == 200, r.text


def _org_billing(org_id: int) -> dict:
    return query_one(
        "SELECT plan, screen_limit, subscription_status, paid_through_at, now() AS db_now "
        "FROM organizations WHERE id = ?",
        (org_id,),
    )


def _set_org(org_id: int, *, plan: str, status: str, paid_through_sql: str) -> None:
    execute(
        "UPDATE organizations SET plan = ?, subscription_status = ?, "
        f"paid_through_at = {paid_through_sql} WHERE id = ?",
        (plan, status, org_id),
    )


def _assert_about(actual, expected) -> None:
    assert actual is not None
    assert abs(actual - expected) < timedelta(minutes=1), (actual, expected)


def test_early_renewal_keeps_the_days_still_paid_for(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    _capture(client, _pending_payment(client, signed_up_org, "starter", 1, mock_niupay))
    first = _org_billing(org_id)["paid_through_at"]

    # Renewing a week early (what the renewal_7day email asks for) adds the
    # new month on top of the current one instead of restarting from today.
    _capture(client, _pending_payment(client, signed_up_org, "starter", 1, mock_niupay))
    second = _org_billing(org_id)["paid_through_at"]
    _assert_about(second, first + timedelta(days=30))


def test_renewal_after_lapse_starts_from_now(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    _set_org(org_id, plan="starter", status="active", paid_through_sql="now() - interval '10 days'")
    _capture(client, _pending_payment(client, signed_up_org, "starter", 1, mock_niupay))
    org = _org_billing(org_id)
    _assert_about(org["paid_through_at"], org["db_now"] + timedelta(days=30))


def test_upgrade_converts_remaining_time_at_tier_prices(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    # 60 days of Starter (3 KWD/month) left are worth 12 days of Pro (15 KWD/month).
    _set_org(org_id, plan="starter", status="active", paid_through_sql="now() + interval '60 days'")
    _capture(client, _pending_payment(client, signed_up_org, "pro", 1, mock_niupay))
    org = _org_billing(org_id)
    assert org["plan"] == "pro"
    assert org["screen_limit"] == 25
    _assert_about(org["paid_through_at"], org["db_now"] + timedelta(days=12 + 30))


def test_downgrade_converts_remaining_time_at_tier_prices(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    # 30 days of Pro (15 KWD/month) left are worth 150 days of Starter (3 KWD/month).
    _set_org(org_id, plan="pro", status="active", paid_through_sql="now() + interval '30 days'")
    _capture(client, _pending_payment(client, signed_up_org, "starter", 1, mock_niupay))
    org = _org_billing(org_id)
    assert org["plan"] == "starter"
    _assert_about(org["paid_through_at"], org["db_now"] + timedelta(days=150 + 30))


def test_purchase_does_not_give_a_no_expiry_org_an_expiry(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    # 'active' with paid_through_at NULL = no expiry (first-run org, admin override).
    _set_org(org_id, plan="pro", status="active", paid_through_sql="NULL")
    _capture(client, _pending_payment(client, signed_up_org, "pro", 1, mock_niupay))
    org = _org_billing(org_id)
    assert org["subscription_status"] == "active"
    assert org["paid_through_at"] is None


def test_callback_replay_does_not_extend_twice(
    client: TestClient, signed_up_org: dict, mock_niupay
):
    org_id = signed_up_org["org"]["id"]
    trackid = _pending_payment(client, signed_up_org, "starter", 1, mock_niupay)
    _capture(client, trackid)
    once = _org_billing(org_id)["paid_through_at"]
    _capture(client, trackid)
    assert _org_billing(org_id)["paid_through_at"] == once


def test_callback_that_lost_a_race_does_not_extend_again(
    client: TestClient, signed_up_org: dict, mock_niupay, monkeypatch
):
    """Two deliveries of one callback can both read the row while it is still
    pending; only the one that moves it out of pending may extend the org."""
    import main
    org_id = signed_up_org["org"]["id"]
    trackid = _pending_payment(client, signed_up_org, "starter", 1, mock_niupay)
    _capture(client, trackid)
    once = _org_billing(org_id)["paid_through_at"]

    real_query_one = main.query_one

    def stale_read(sql, params=()):
        row = real_query_one(sql, params)
        if row and sql.strip().startswith("SELECT * FROM payments WHERE trackid"):
            row["status"] = "pending"
        return row

    monkeypatch.setattr(main, "query_one", stale_read)
    _capture(client, trackid)
    assert _org_billing(org_id)["paid_through_at"] == once
