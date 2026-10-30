"""Idle session expiry, enforced on the request itself.

cleanup_sessions() only sweeps at startup and login, so between those a
session idle past SESSION_TTL_SECONDS must be refused by the per-request
lookup -- which never saw the session's timestamps and so never expired one.
"""
from datetime import datetime, timedelta, timezone

import main
from db import execute, query_one


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _set_last_used(token, seconds_ago):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    execute("UPDATE sessions SET last_used = ? WHERE token = ?", (stamp, token))
    return stamp


def _row(token):
    return query_one("SELECT last_used FROM sessions WHERE token = ?", (token,))


def test_session_idle_past_ttl_is_refused_and_deleted(client, signed_up_org):
    token = signed_up_org["token"]
    _set_last_used(token, main.SESSION_TTL_SECONDS + 60)
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401, r.text
    assert _row(token) is None


def test_session_idle_past_ttl_is_refused_on_api_routes(client, signed_up_org):
    """The dual-auth (session / API key / OAuth) path shares the lookup."""
    token = signed_up_org["token"]
    _set_last_used(token, main.SESSION_TTL_SECONDS + 60)
    r = client.get("/schedules", headers=_bearer(token))
    assert r.status_code == 401, r.text


def test_session_without_last_used_expires_from_created_at(client, signed_up_org):
    token = signed_up_org["token"]
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=main.SESSION_TTL_SECONDS + 60)).isoformat()
    execute("UPDATE sessions SET last_used = NULL, created_at = ? WHERE token = ?",
            (old, token))
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401, r.text


def test_session_ttl_follows_setting(client, signed_up_org, monkeypatch):
    monkeypatch.setattr(main, "SESSION_TTL_SECONDS", 120)
    token = signed_up_org["token"]
    _set_last_used(token, 300)
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401, r.text


def test_session_within_ttl_is_kept_and_refreshed(client, signed_up_org):
    token = signed_up_org["token"]
    stale = _set_last_used(token, main.SESSION_TTL_SECONDS - 600)
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 200, r.text
    refreshed = _row(token)["last_used"]
    assert datetime.fromisoformat(refreshed) > datetime.fromisoformat(stale)


def test_recently_used_session_is_not_rewritten_on_every_request(client, signed_up_org):
    """A write per authenticated request is load the idle clock does not need."""
    token = signed_up_org["token"]
    recent = _set_last_used(token, 10)
    for _ in range(3):
        r = client.get("/auth/me", headers=_bearer(token))
        assert r.status_code == 200, r.text
    assert _row(token)["last_used"] == recent
