"""Tests for the Phase 2.5g subscription reminder system."""


def test_subscription_reminders_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("subscription_reminders", "reminder_type"),
    )
    assert row is not None


# ── _claim_reminder ───────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from db import execute, query_one


def _make_test_org(client, suffix="x"):
    """Create a fresh org via signup; return the org id."""
    import uuid
    sfx = uuid.uuid4().hex[:8] + suffix
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Claim {sfx}",
                          "email": f"claim-{sfx}@example.com"})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": f"claim-{sfx}@example.com", "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    assert r.status_code == 200, r.text
    return r.json()["organization"]["id"]


def test_claim_returns_true_on_first_call(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True


def test_claim_returns_false_on_duplicate(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 2, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True
    assert _claim_reminder(org_id, "trial_3day", ts) is False


def test_claim_returns_true_for_different_expires_at(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts1 = datetime(2099, 3, 1, tzinfo=timezone.utc)
    ts2 = datetime(2099, 6, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "renewal_7day", ts1) is True
    assert _claim_reminder(org_id, "renewal_7day", ts2) is True


def test_claim_returns_true_for_different_type_same_expires(client):
    from main import _claim_reminder
    org_id = _make_test_org(client)
    ts = datetime(2099, 4, 1, tzinfo=timezone.utc)
    assert _claim_reminder(org_id, "trial_3day", ts) is True
    assert _claim_reminder(org_id, "trial_0day", ts) is True


# ── Email templates ───────────────────────────────────────────────────
import pytest


def _fake_org(locale="en", name="Test Cafe"):
    return {"id": 1, "name": name, "locale": locale}


def test_trial_3day_en_subject():
    from main import _reminder_template
    subject, _html, _text = _reminder_template("trial_3day", _fake_org("en"), "en")
    assert "3 days" in subject.lower() or "trial" in subject.lower()


def test_trial_3day_ar_subject():
    from main import _reminder_template
    subject, _html, _text = _reminder_template("trial_3day", _fake_org("ar"), "ar")
    assert "تجربة" in subject or "أيام" in subject


def test_trial_0day_en_uses_past_tense():
    from main import _reminder_template
    subject, _html, text = _reminder_template("trial_0day", _fake_org("en"), "en")
    assert "ended" in subject.lower() or "ended" in text.lower()


def test_renewal_7day_en_includes_billing_link():
    from main import _reminder_template
    _subject, html, text = _reminder_template("renewal_7day", _fake_org("en"), "en")
    assert "billing" in html or "billing" in text


def test_renewal_7day_ar_includes_billing_link():
    from main import _reminder_template
    _subject, html, text = _reminder_template("renewal_7day", _fake_org("ar"), "ar")
    assert "billing" in html or "billing" in text


def test_unknown_reminder_type_raises():
    from main import _reminder_template
    with pytest.raises(ValueError):
        _reminder_template("not_a_type", _fake_org("en"), "en")
