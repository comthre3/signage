"""Startup must not destroy persisted credential state.

`startup()` used to wipe two columns on every container boot:

    UPDATE screens SET password_hash = NULL WHERE password_hash IS NOT NULL
    UPDATE users SET must_change_password = 0 WHERE must_change_password IS NOT NULL

Both dated from the initial commit and were leftover development
convenience. Neither column is reachable from an API today (see the
review notes on `update_screen`), but the wipes guarantee that anything
built on top of them would silently lose state on every restart. These
tests pin the columns down so that stays impossible.
"""

from fastapi.testclient import TestClient

from db import execute, query_one
from main import startup


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_screen_password_hash_survives_restart(client: TestClient, signed_up_org: dict):
    r = client.post(
        "/screens",
        json={"name": "Lobby restart probe"},
        headers=_bearer(signed_up_org["token"]),
    )
    assert r.status_code == 200, r.text
    screen_id = r.json()["id"]

    # No endpoint can set a screen password today, so seed it directly.
    execute(
        "UPDATE screens SET password_hash = ? WHERE id = ?",
        ("pbkdf2_sha256$120000$deadbeef$notarealhash", screen_id),
    )

    startup()  # simulated container restart

    row = query_one("SELECT password_hash FROM screens WHERE id = ?", (screen_id,))
    assert row is not None
    assert row["password_hash"] == "pbkdf2_sha256$120000$deadbeef$notarealhash"


def test_must_change_password_survives_restart(client: TestClient, signed_up_org: dict):
    user_id = signed_up_org["user"]["id"]
    execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (user_id,))

    startup()  # simulated container restart

    row = query_one("SELECT must_change_password FROM users WHERE id = ?", (user_id,))
    assert row is not None
    assert row["must_change_password"] == 1
