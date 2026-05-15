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


# ── Decision logic + sender ───────────────────────────────────────────
from unittest.mock import patch


def _expire_trial_to(org_id: int, days: int):
    """Set trial_ends_at to now+days (negative for past)."""
    delta = "" if days == 0 else (f" + interval '{days} days'" if days > 0
                                  else f" - interval '{abs(days)} days'")
    execute(
        "UPDATE organizations "
        f"SET subscription_status = 'trialing', "
        f"    trial_ends_at = (now(){delta})::text, "
        f"    paid_through_at = NULL "
        "WHERE id = ?",
        (org_id,),
    )


def _set_active_with_paid_through(org_id: int, days: int):
    delta = "" if days == 0 else (f" + interval '{days} days'" if days > 0
                                  else f" - interval '{abs(days)} days'")
    execute(
        "UPDATE organizations "
        f"SET subscription_status = 'active', "
        f"    trial_ends_at = NULL, "
        f"    paid_through_at = now(){delta} "
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


def _count_claim_rows(org_id: int, reminder_type: str = None) -> int:
    if reminder_type:
        row = query_one(
            "SELECT COUNT(*) AS n FROM subscription_reminders "
            "WHERE organization_id = ? AND reminder_type = ?",
            (org_id, reminder_type),
        )
    else:
        row = query_one(
            "SELECT COUNT(*) AS n FROM subscription_reminders WHERE organization_id = ?",
            (org_id,),
        )
    return int(row["n"]) if row else 0


def test_no_reminder_when_trial_has_lots_of_days(client, monkeypatch):
    from main import _reminder_check_once
    from db import query_one as _qone
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="lots")
    _expire_trial_to(org_id, 10)
    # Fetch the admin email so we can check it wasn't called
    admin_row = _qone(
        "SELECT username FROM users WHERE organization_id = ? AND is_admin = 1 LIMIT 1",
        (org_id,),
    )
    admin_email = admin_row["username"]
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    # Other orgs from prior tests may trigger sends; check only our org
    called_to_args = [call.kwargs.get("to") or (call.args[3] if len(call.args) > 3 else None)
                      for call in mock_send.call_args_list]
    assert admin_email not in called_to_args
    assert _count_claim_rows(org_id) == 0


def test_trial_3day_sends_when_days_le_3(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="3d")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "trial_3day") == 1


def test_trial_3day_does_not_resend_same_window(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="re")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
        first_calls = mock_send.call_count
        _reminder_check_once()
    assert mock_send.call_count == first_calls
    assert _count_claim_rows(org_id, "trial_3day") == 1


def test_trial_0day_sends_when_state_is_trial_expired(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="0d")
    _expire_trial_to(org_id, -1)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "trial_0day") == 1


def test_renewal_7day_sends_when_active_le_7(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="7d")
    _set_active_with_paid_through(org_id, 5)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 1
    assert _count_claim_rows(org_id, "renewal_7day") == 1


def test_renewal_7day_resends_after_new_paid_through_at(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="rew")
    _set_active_with_paid_through(org_id, 5)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
        first_calls = mock_send.call_count
        # Simulate renewal: move paid_through_at to a much later future time
        _set_active_with_paid_through(org_id, 365)
        # Then bring it back inside the 7-day window again (different timestamp)
        _set_active_with_paid_through(org_id, 5)
        _reminder_check_once()
    assert _count_claim_rows(org_id, "renewal_7day") >= 2
    assert mock_send.call_count > first_calls


def test_no_reminder_when_no_admins(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="noadmin")
    _expire_trial_to(org_id, 2)
    execute("UPDATE users SET role = 'viewer', is_admin = 0 WHERE organization_id = ?",
            (org_id,))
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    # No-admins path: send returns 0 but the claim row IS inserted (per spec
    # claim-then-send semantics). Don't assert on count.


def test_no_reminder_when_no_expiry(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="noexp")
    _set_no_expiry(org_id)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    assert _count_claim_rows(org_id) == 0


def test_skipped_when_resend_key_missing(client, monkeypatch):
    from main import _reminder_check_once
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    org_id = _make_test_org(client, suffix="nokey")
    _expire_trial_to(org_id, 2)
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count == 0
    # No claim row either — retry on next tick when key is set
    assert _count_claim_rows(org_id) == 0


def test_send_to_all_admins(client, monkeypatch):
    from main import _reminder_check_once
    from main import hash_password
    monkeypatch.setenv("RESEND_API_KEY", "fake_test_key")
    org_id = _make_test_org(client, suffix="multi")
    _expire_trial_to(org_id, 2)
    import uuid
    second_email = f"second-admin-{uuid.uuid4().hex[:8]}@example.com"
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, now())",
        (org_id, second_email, hash_password("Khanshoof2026Test"), 1, "admin"),
    )
    with patch("main.send_via_resend") as mock_send:
        _reminder_check_once()
    assert mock_send.call_count >= 2


# ── Startup wire ──────────────────────────────────────────────────────


def test_reminder_check_loop_is_defined():
    """Existence test — the async loop function must be importable."""
    from main import _reminder_check_loop
    import inspect
    assert inspect.iscoroutinefunction(_reminder_check_loop)
