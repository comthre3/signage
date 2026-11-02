"""Helpers that bind a TV to a wall cell.

redeem_wall_cell (a TV typing a wall code) shares them with the phone-driven
free-wall endpoints (a phone claiming the standalone code a TV shows in its
QR, POST /walls/free and POST /walls/{id}/tvs). The TV half is the ordinary
/screens/request_code row, claimed with the same conditional UPDATE as
/screens/claim so two phones cannot both take one TV.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import main
from db import execute, query_one, transaction, utc_now_iso
from main import hash_password


def _org(screen_limit=10):
    slug = "tb" + secrets.token_hex(3)
    oid = execute(
        "INSERT INTO organizations (name, slug, subscription_status, screen_limit, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"TB {slug}", slug, "active", screen_limit, utc_now_iso()),
    )
    uid = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, f"u_{slug}@x.com", hash_password("pw"), 1, "admin", utc_now_iso()),
    )
    tok = uuid.uuid4().hex
    execute("INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
            (uid, tok, utc_now_iso()))
    return oid, {"Authorization": f"Bearer {tok}"}


def _tv_code(client):
    """The code a TV in pairing mode shows in its QR."""
    res = client.post("/screens/request_code", json={})
    assert res.status_code == 200, res.text
    return res.json()["code"]


def _past():
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def _refusal(fn, *args):
    with pytest.raises(HTTPException) as caught:
        fn(*args)
    return caught.value.status_code, caught.value.detail["code"]


def test_a_pending_code_row_is_returned(client):
    code = _tv_code(client)
    row = main._tv_code_row_or_raise(code, datetime.now(timezone.utc))
    assert (row["code"], row["status"]) == (code, "pending")
    assert isinstance(row["id"], int)


def test_an_unknown_code_is_404():
    # 0 is not in PAIR_CODE_CHARSET, so no TV can ever show this code.
    assert _refusal(main._tv_code_row_or_raise, "000000",
                    datetime.now(timezone.utc)) == (404, "pair.code_unknown")


def test_an_expired_code_is_400(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?", (_past(), code))
    assert _refusal(main._tv_code_row_or_raise, code,
                    datetime.now(timezone.utc)) == (400, "pair.code_expired")


def test_a_used_code_is_409_even_once_expired(client):
    code = _tv_code(client)
    execute("UPDATE pairing_codes SET status = 'paired', expires_at = ? WHERE code = ?",
            (_past(), code))
    assert _refusal(main._tv_code_row_or_raise, code,
                    datetime.now(timezone.utc)) == (409, "pair.code_used")


def test_taking_a_code_claims_it_once(client):
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with transaction():
        main._take_tv_code(row["id"], now)
    after = query_one("SELECT status, claimed_at FROM pairing_codes WHERE id = ?", (row["id"],))
    assert after["status"] == "paired"
    # Tz-aware: /screens/poll compares it with an aware now; a naive one is a 500.
    assert datetime.fromisoformat(after["claimed_at"]).tzinfo is not None
    with pytest.raises(HTTPException) as caught:
        with transaction():
            main._take_tv_code(row["id"], now)
    assert (caught.value.status_code, caught.value.detail["code"]) == (409, "pair.code_used")


def test_a_refusal_later_in_the_transaction_gives_the_code_back(client):
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with pytest.raises(RuntimeError):
        with transaction():
            main._take_tv_code(row["id"], now)
            raise RuntimeError("a plan limit, say")
    after = query_one("SELECT status, claimed_at FROM pairing_codes WHERE id = ?", (row["id"],))
    assert (after["status"], after["claimed_at"]) == ("pending", None)
    assert client.get(f"/screens/poll/{code}").json() == {"status": "pending"}


def test_attaching_the_screen_lets_the_tvs_poll_collect_its_token(client):
    oid, _headers = _org()
    token = "tok_" + secrets.token_hex(8)
    sid = execute(
        "INSERT INTO screens (organization_id, name, pair_code, token, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (oid, "Lobby · TV 1", secrets.token_hex(3), token, utc_now_iso()),
    )
    code = _tv_code(client)
    now = datetime.now(timezone.utc)
    row = main._tv_code_row_or_raise(code, now)
    with transaction():
        main._take_tv_code(row["id"], now)
        main._attach_tv_code(row["id"], sid)
    assert client.get(f"/screens/poll/{code}").json() == {
        "status": "paired", "screen_id": sid,
        "screen_name": "Lobby · TV 1", "screen_token": token,
    }


def test_lock_org_returns_the_limit_row():
    oid, _headers = _org(screen_limit=7)
    with transaction():
        assert main._lock_org(oid) == {"id": oid, "screen_limit": 7}
        assert main._lock_org(-1) is None


def test_a_redeemed_cell_screen_is_named_after_its_wall_and_cell(client):
    """Characterisation: passes before and after the refactor."""
    oid, h = _org()
    pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                  (oid, "p", utc_now_iso()))
    wall = client.post("/walls", json={
        "name": "Foyer", "mode": "mirrored", "rows": 1, "cols": 2,
        "mirrored_mode": "same_playlist", "mirrored_playlist_id": pid,
    }, headers=h).json()
    code = client.post(f"/walls/{wall['id']}/cells/0/1/pair", headers=h).json()["code"]
    assert client.post("/walls/cells/redeem", json={"code": code}).status_code == 200
    cell = query_one(
        "SELECT screen_id FROM wall_cells WHERE wall_id = ? AND row_index = 0 AND col_index = 1",
        (wall["id"],),
    )
    assert query_one("SELECT name FROM screens WHERE id = ?",
                     (cell["screen_id"],))["name"] == "Wall Foyer (0,1)"
