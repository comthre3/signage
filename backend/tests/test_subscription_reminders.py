"""Tests for the Phase 2.5g subscription reminder system."""


def test_subscription_reminders_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("subscription_reminders", "reminder_type"),
    )
    assert row is not None
