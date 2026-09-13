"""First-run administrator setup.

A fresh deployment has no users and is already internet-facing. These tests pin
the properties that stop it being claimed by whoever loads the page first, and
that stop it being claimable twice.
"""
import os
import uuid
from types import SimpleNamespace

import pytest

from db import execute, query_one


@pytest.fixture
def empty_deployment(monkeypatch):
    """Simulate a deployment awaiting its first admin, without emptying the DB.

    needs_setup() asks a GLOBAL question -- does any user exist -- so the
    honest precondition is an empty database. Two earlier attempts to produce
    one were both worse than the seam used here: deleting from users is refused
    once billing tests exist (payments references it ON DELETE NO ACTION), and
    truncating mid-suite destabilises test_mcp's module-scoped lifespan, whose
    StreamableHTTPSessionManager can only start once per process.

    So the emptiness is stubbed and everything downstream of it is real: the
    token is really issued, really validated, really destroyed, and the admin
    is really created. test_needs_setup_is_false_when_users_exist covers the
    unstubbed function against the populated database the suite already has.
    """
    import main
    state = {"empty": True}
    monkeypatch.setattr(main, "needs_setup", lambda: state["empty"])
    execute("DELETE FROM setup_token")
    main._ensure_setup_token()
    row = query_one("SELECT token FROM setup_token WHERE id = 1")
    token = row["token"] if row else None
    yield SimpleNamespace(token=token, state=state)
    execute("DELETE FROM setup_token")
    try:
        os.remove(main.SETUP_TOKEN_PATH)
    except OSError:
        pass


def _payload(token, **over):
    body = {"setup_token": token, "business_name": f"Biz {uuid.uuid4().hex[:6]}",
            "username": f"admin{uuid.uuid4().hex[:6]}", "password": "Khanshoof2026Setup"}
    body.update(over)
    return body


def test_status_reports_setup_needed_when_no_users(client, empty_deployment):
    assert client.get("/auth/setup-status").json()["needs_setup"] is True


def test_setup_creates_the_first_admin_and_signs_them_in(client, empty_deployment):
    r = client.post("/auth/setup", json=_payload(empty_deployment.token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_admin"] is True
    assert body["token"], "setup should return a usable session"
    # the returned session actually works
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200, me.text


def test_wrong_token_is_rejected(client, empty_deployment):
    body = _payload("not-the-right-token-at-all")
    r = client.post("/auth/setup", json=body)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "setup.bad_token"
    # Assert the specific account was not created, rather than that the whole
    # users table is empty -- the suite shares one populated database.
    assert query_one("SELECT id FROM users WHERE username = ?",
                     (body["username"],)) is None, "a rejected setup created a user"


def test_setup_cannot_be_run_twice(client, empty_deployment):
    assert client.post("/auth/setup", json=_payload(empty_deployment.token)).status_code == 200
    empty_deployment.state["empty"] = False   # the deployment now has an admin
    again = client.post("/auth/setup", json=_payload(empty_deployment.token))
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "setup.already_done"


def test_token_is_destroyed_once_used(client, empty_deployment):
    import main
    assert client.post("/auth/setup", json=_payload(empty_deployment.token)).status_code == 200
    assert query_one("SELECT token FROM setup_token WHERE id = 1") is None
    assert not os.path.exists(main.SETUP_TOKEN_PATH), "token file must not linger"


def test_status_flips_after_setup(client, empty_deployment):
    assert client.get("/auth/setup-status").json()["needs_setup"] is True
    client.post("/auth/setup", json=_payload(empty_deployment.token))
    empty_deployment.state["empty"] = False
    assert client.get("/auth/setup-status").json()["needs_setup"] is False


def test_short_password_is_refused(client, empty_deployment):
    body = _payload(empty_deployment.token, password="short")
    r = client.post("/auth/setup", json=body)
    assert r.status_code >= 400
    assert query_one("SELECT id FROM users WHERE username = ?",
                     (body["username"],)) is None, "a refused password created a user"


def test_token_file_is_not_world_readable(client, empty_deployment):
    import main
    if not os.path.exists(main.SETUP_TOKEN_PATH):
        pytest.skip("token file not written in this environment")
    mode = os.stat(main.SETUP_TOKEN_PATH).st_mode & 0o777
    assert mode == 0o600, f"setup token file is {oct(mode)}, must be 0600"


def test_reissuing_keeps_the_same_token(client, empty_deployment):
    """Multiple workers must not each mint their own token."""
    import main
    main._ensure_setup_token()
    main._ensure_setup_token()
    rows = query_one("SELECT count(*) AS n FROM setup_token")
    assert rows["n"] == 1
    assert query_one("SELECT token FROM setup_token WHERE id = 1")["token"] == empty_deployment.token


def test_needs_setup_is_false_when_users_exist(client):
    """The unstubbed function, against the populated database the suite has."""
    import main
    assert query_one("SELECT id FROM users LIMIT 1") is not None, "suite should have users"
    assert main.needs_setup() is False
    assert client.get("/auth/setup-status").json()["needs_setup"] is False
