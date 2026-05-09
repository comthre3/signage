"""Tests for the audit() helper and the audit_log table writes."""
import json
from unittest.mock import patch

import pytest
from db import query_one, query_all


def test_audit_helper_writes_full_row(client, signed_up_org):
    """The audit() helper writes a row with all expected fields populated."""
    from main import audit
    actor = {
        "id": signed_up_org["user"]["id"],
        "username": signed_up_org["user"]["username"],
        "organization_id": signed_up_org["org"]["id"],
    }

    class FakeRequest:
        class _Client:
            host = "9.9.9.9"
        client = _Client()
        headers = {"user-agent": "PyTest/1"}

    audit(FakeRequest(), action="test.action",
          actor=actor, target_type="user", target_id=42,
          details={"hello": "world"})

    row = query_one(
        "SELECT * FROM audit_log WHERE action = ? AND target_id = ? "
        "ORDER BY id DESC LIMIT 1",
        ("test.action", "42"),
    )
    assert row is not None
    assert row["actor_user_id"] == actor["id"]
    assert row["actor_username"] == actor["username"]
    assert row["organization_id"] == actor["organization_id"]
    assert row["target_type"] == "user"
    assert row["target_id"] == "42"
    assert row["ip"] == "9.9.9.9"
    assert row["user_agent"] == "PyTest/1"
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details == {"hello": "world"}


def test_audit_helper_swallows_db_error(caplog):
    """If the DB write raises, audit() must NOT propagate."""
    from main import audit
    with patch("main.execute", side_effect=RuntimeError("simulated DB outage")):
        with caplog.at_level("WARNING"):
            audit(None, action="test.action.fails")
    assert any("audit_failed" in rec.getMessage() for rec in caplog.records)


def test_audit_helper_handles_no_actor():
    """audit() with actor=None writes a row with NULL actor fields."""
    from main import audit
    audit(None, action="test.no_actor",
          organization_id=None, details={"reason": "test"})
    row = query_one(
        "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT 1",
        ("test.no_actor",),
    )
    assert row is not None
    assert row["actor_user_id"] is None
    assert row["actor_username"] is None
