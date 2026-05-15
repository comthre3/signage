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
