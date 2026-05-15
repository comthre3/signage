"""Tests for the subscription_state helper + require_active_subscription dep
+ /organization response shape (Phase 2.5f)."""
from datetime import datetime, timedelta, timezone

from main import subscription_state, _parse_iso


def _now():
    return datetime.now(timezone.utc)


# ── subscription_state ────────────────────────────────────────────────

def test_trialing_in_future():
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       (_now() + timedelta(days=3)).isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trialing"
    assert s["can_write"] is True
    assert s["days_remaining"] == 3


def test_trialing_expired():
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       (_now() - timedelta(days=1)).isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False
    assert s["days_remaining"] == 0


def test_trialing_exact_boundary():
    """trial_ends_at == now → expired (strict)."""
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       _now().isoformat(),
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False


def test_trialing_no_trial_ends_at():
    org = {"subscription_status": "trialing",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["state"] == "trial_expired"
    assert s["can_write"] is False


def test_active_in_future():
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     (_now() + timedelta(days=10)).isoformat(),
    }
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] == 10


def test_active_lapsed():
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     (_now() - timedelta(days=1)).isoformat(),
    }
    s = subscription_state(org)
    assert s["state"] == "lapsed"
    assert s["can_write"] is False


def test_active_no_expiry():
    """status=active + paid_through_at=NULL → no-expiry override (seeded default)."""
    org = {"subscription_status": "active",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] is None


def test_unknown_status():
    """Unknown status → conservative: allow writes."""
    org = {"subscription_status": "failed",
           "trial_ends_at": None, "paid_through_at": None}
    s = subscription_state(org)
    assert s["can_write"] is True


def test_handles_string_trial_ends_at():
    """trial_ends_at stored as TEXT (ISO string) — must parse cleanly."""
    org = {
        "subscription_status": "trialing",
        "trial_ends_at":       "2099-01-01T00:00:00+00:00",
        "paid_through_at":     None,
    }
    s = subscription_state(org)
    assert s["state"] == "trialing"
    assert s["can_write"] is True


def test_handles_datetime_paid_through():
    """paid_through_at stored as TIMESTAMPTZ — psycopg returns datetime obj."""
    org = {
        "subscription_status": "active",
        "trial_ends_at":       None,
        "paid_through_at":     _now() + timedelta(days=30),
    }
    s = subscription_state(org)
    assert s["state"] == "active"
    assert s["can_write"] is True
    assert s["days_remaining"] == 30


# ── require_active_subscription dependency ────────────────────────────
from db import execute


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _expire_trial(org_id: int):
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'trialing', "
        "    trial_ends_at = (now() - interval '1 day')::text, "
        "    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _lapse_paid(org_id: int):
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'active', "
        "    trial_ends_at = NULL, "
        "    paid_through_at = now() - interval '1 day' "
        "WHERE id = ?",
        (org_id,),
    )


def _set_no_expiry(org_id: int):
    execute(
        "UPDATE organizations "
        "SET subscription_status = 'active', "
        "    trial_ends_at = NULL, "
        "    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _create_simple_playlist_payload():
    return {"name": "Test playlist"}


def test_write_blocked_when_trial_expired(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["detail"]["code"] == "subscription.trial_expired"
    assert body["detail"]["state"] == "trial_expired"
    assert "expires_at" in body["detail"]


def test_write_blocked_when_active_lapsed(client, signed_up_org):
    _lapse_paid(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["detail"]["code"] == "subscription.expired"
    assert body["detail"]["state"] == "lapsed"


def test_write_allowed_when_active_no_expiry(client, signed_up_org):
    _set_no_expiry(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text


def test_write_allowed_when_trialing(client, signed_up_org):
    # signed_up_org is in trialing state with 5 days remaining
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text


def test_read_allowed_when_expired(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.get("/playlists", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text


def test_billing_endpoints_allowed_when_expired(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/billing/checkout",
                    headers=_bearer(signed_up_org["token"]),
                    json={"tier": "starter", "term_months": 1})
    # Anything other than 402 proves the gate did NOT fire (validation
    # errors or success are both fine for this test's purpose).
    assert r.status_code != 402, r.text


def test_auth_endpoints_allowed_when_expired(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/auth/logout", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text


def test_402_response_includes_state_and_expires_at(client, signed_up_org):
    _expire_trial(signed_up_org["org"]["id"])
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    body = r.json()
    detail = body["detail"]
    assert detail["code"] in ("subscription.trial_expired", "subscription.expired")
    assert "state" in detail
    assert "expires_at" in detail
    assert detail.get("message_key", "").startswith("error.subscription.")


def test_unknown_status_does_not_block(client, signed_up_org):
    execute(
        "UPDATE organizations SET subscription_status = 'failed', "
        "trial_ends_at = NULL, paid_through_at = NULL WHERE id = ?",
        (signed_up_org["org"]["id"],),
    )
    r = client.post("/playlists",
                    headers=_bearer(signed_up_org["token"]),
                    json=_create_simple_playlist_payload())
    assert r.status_code in (200, 201), r.text


# ── /organization response shape ──────────────────────────────────────


def test_organization_response_includes_derived_fields(client, signed_up_org):
    r = client.get("/organization", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "state" in body
    assert "can_write" in body
    assert "days_remaining" in body
    assert "expires_at" in body
    assert body["state"] == "trialing"
    assert body["can_write"] is True
    assert isinstance(body["days_remaining"], int)
    assert body["days_remaining"] >= 0


def test_login_response_includes_derived_fields(client, signed_up_org):
    r = client.post("/auth/login", json={
        "username": signed_up_org["user"]["username"],
        "password": "Khanshoof2026Test",
    })
    assert r.status_code == 200, r.text
    org = r.json().get("organization", {})
    assert "state" in org
    assert "can_write" in org
    assert "days_remaining" in org


def test_signup_response_includes_derived_fields(client, unique_business):
    """Fresh signup → response.organization has state=trialing, can_write=True."""
    r = client.post("/auth/signup/request",
                    json={"business_name": unique_business["business_name"],
                          "email": unique_business["email"]})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": unique_business["email"], "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": unique_business["password"]})
    assert r.status_code == 200, r.text
    org = r.json().get("organization", {})
    assert org.get("state") == "trialing"
    assert org.get("can_write") is True
