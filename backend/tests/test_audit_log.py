"""Tests for the audit() helper and the audit_log table writes."""
import json
import uuid
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


# ── Per-action integration tests (Phase 2.5c §5.6) ────────────────────


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _last_audit(action):
    return query_one(
        "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT 1",
        (action,),
    )


def test_audit_login_success_written(client, signed_up_org):
    r = client.post("/auth/login", json={
        "username": signed_up_org["user"]["username"],
        "password": "Khanshoof2026Test",
    })
    assert r.status_code == 200
    row = _last_audit("auth.login.success")
    assert row is not None
    assert row["actor_username"] == signed_up_org["user"]["username"]


def test_audit_login_failure_invalid_credentials(client, signed_up_org):
    client.post("/auth/login", json={
        "username": signed_up_org["user"]["username"],
        "password": "WrongPass-9999",
    })
    row = _last_audit("auth.login.failure")
    assert row is not None
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details.get("reason") == "invalid_credentials"


def test_audit_logout_written(client, signed_up_org):
    r = client.post("/auth/logout", headers=_bearer(signed_up_org["token"]))
    assert r.status_code == 200, r.text
    row = _last_audit("auth.logout")
    assert row is not None
    assert row["actor_user_id"] == signed_up_org["user"]["id"]


def test_audit_password_change_written(client, signed_up_org):
    r = client.post(
        "/auth/change-password",
        headers=_bearer(signed_up_org["token"]),
        json={"current_password": "Khanshoof2026Test",
              "new_password": "Khanshoof2026Pass2"},
    )
    assert r.status_code == 200, r.text
    row = _last_audit("auth.password_change")
    assert row is not None
    assert row["actor_user_id"] == signed_up_org["user"]["id"]


def test_audit_user_create_written(client, signed_up_org):
    username = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        "/users",
        headers=_bearer(signed_up_org["token"]),
        json={"username": username,
              "password": "Khanshoof2026Pass3",
              "role": "editor"},
    )
    assert r.status_code in (200, 201), r.text
    row = _last_audit("user.create")
    assert row is not None
    assert row["target_type"] == "user"
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details.get("username") == username
    assert details.get("role") == "editor"


def test_audit_user_update_written(client, signed_up_org):
    username = f"tomod-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/users", headers=_bearer(signed_up_org["token"]),
                    json={"username": username,
                          "password": "Khanshoof2026Pass4", "role": "viewer"})
    assert r.status_code in (200, 201), r.text
    user_id = r.json()["id"]
    r = client.put(f"/users/{user_id}", headers=_bearer(signed_up_org["token"]),
                   json={"role": "editor"})
    assert r.status_code == 200, r.text
    row = _last_audit("user.update")
    assert row is not None
    assert row["target_id"] == str(user_id)


def test_audit_user_delete_written(client, signed_up_org):
    username = f"todel-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/users", headers=_bearer(signed_up_org["token"]),
                    json={"username": username,
                          "password": "Khanshoof2026Pass5", "role": "viewer"})
    assert r.status_code in (200, 201), r.text
    user_id = r.json()["id"]
    r = client.delete(f"/users/{user_id}", headers=_bearer(signed_up_org["token"]))
    assert r.status_code in (200, 204), r.text
    row = _last_audit("user.delete")
    assert row is not None
    assert row["target_id"] == str(user_id)
