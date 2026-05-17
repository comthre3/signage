"""Tests for the Phase 2.5h agent API platform."""


def test_api_keys_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("api_keys", "key_hash"),
    )
    assert row is not None


# ── generate_api_key + lookup_api_key ─────────────────────────────────
import re
import uuid
import time
from db import execute, query_one


def _signup_org(client, suffix=None):
    """Create a fresh org via signup. Returns (token, org_id, user_id)."""
    sfx = suffix or uuid.uuid4().hex[:8]
    r = client.post("/auth/signup/request",
                    json={"business_name": f"Biz {sfx}",
                          "email": f"a-{sfx}@example.com"})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": f"a-{sfx}@example.com", "otp": otp})
    assert r.status_code == 200, r.text
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["organization"]["id"], body["user"]["id"]


def _mint_key_row(org_id, scope="api:rw", name="test", creator=None):
    """Mint via the low-level helpers (not the HTTP endpoint, which lands in Task 6)."""
    from main import generate_api_key
    full_key, prefix, hashed = generate_api_key()
    execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, name, prefix, hashed, scope, creator),
    )
    return full_key, prefix


def test_generate_api_key_format():
    from main import generate_api_key
    full_key, prefix, hashed = generate_api_key()
    assert re.match(r"^khan_live_[A-Za-z0-9_-]{32,}$", full_key), \
        f"unexpected key format: {full_key}"


def test_generate_api_key_prefix_is_first_12_chars():
    from main import generate_api_key, API_KEY_PREFIX_LEN
    full_key, prefix, _ = generate_api_key()
    assert prefix == full_key[:API_KEY_PREFIX_LEN]
    assert API_KEY_PREFIX_LEN == 12


def test_generate_api_key_hash_not_plaintext():
    from main import generate_api_key
    full_key, _, hashed = generate_api_key()
    assert full_key not in hashed
    assert "$" in hashed


def test_lookup_returns_row_for_valid_key(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    row = lookup_api_key(full_key)
    assert row is not None
    assert row["organization_id"] == org_id
    assert row["scope"] == "api:rw"


def test_lookup_returns_none_for_unknown_prefix(client):
    from main import lookup_api_key
    assert lookup_api_key("khan_live_zzzzzzzzzzzzzz") is None


def test_lookup_returns_none_for_bad_scheme(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id)
    mangled = "rats_live_" + full_key[len("khan_live_"):]
    assert lookup_api_key(mangled) is None


def test_lookup_returns_none_for_revoked_key(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    execute("UPDATE api_keys SET revoked_at = now() WHERE key_prefix = ?", (prefix,))
    assert lookup_api_key(full_key) is None


def test_lookup_updates_last_used_at(client):
    from main import lookup_api_key
    _t, org_id, _u = _signup_org(client)
    full_key, prefix = _mint_key_row(org_id)
    before = query_one("SELECT last_used_at FROM api_keys WHERE key_prefix = ?", (prefix,))
    assert before["last_used_at"] is None
    lookup_api_key(full_key)
    time.sleep(0.1)
    after = query_one("SELECT last_used_at FROM api_keys WHERE key_prefix = ?", (prefix,))
    assert after["last_used_at"] is not None


# ── AuthedPrincipal + get_api_authed + require_api_scope ──────────────


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_get_api_authed_accepts_session_token(client):
    session_token, org_id, _u = _signup_org(client)
    r = client.get("/organization", headers=_bearer(session_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == org_id


def test_get_api_authed_accepts_api_key(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:rw")
    r = client.get("/organization", headers=_bearer(full_key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == org_id


def test_get_api_authed_rejects_missing_header(client):
    r = client.get("/organization")
    assert r.status_code == 401


def test_get_api_authed_rejects_bad_scheme(client):
    r = client.get("/organization", headers={"Authorization": "Basic dGVzdA=="})
    assert r.status_code == 401


def test_get_api_authed_rejects_unknown_key(client):
    r = client.get("/organization", headers=_bearer("khan_live_garbage_does_not_exist_xxxxxxxxxx"))
    assert r.status_code == 401


def test_read_scope_can_GET_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    r = client.get("/playlists", headers=_bearer(full_key))
    assert r.status_code == 200, r.text


def test_read_scope_cannot_POST_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:read")
    r = client.post("/playlists", headers=_bearer(full_key),
                    json={"name": "denied"})
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["code"] == "api.insufficient_scope"


def test_rw_scope_can_POST_playlists(client):
    _t, org_id, _u = _signup_org(client)
    full_key, _ = _mint_key_row(org_id, scope="api:rw")
    r = client.post("/playlists", headers=_bearer(full_key),
                    json={"name": "allowed"})
    assert r.status_code in (200, 201), r.text


def test_session_passes_all_gates(client):
    session_token, org_id, _u = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(session_token),
                    json={"name": "session"})
    assert r.status_code in (200, 201), r.text


# ── Org-scoping (API keys can only see/modify their own org) ──────────


def test_api_key_cannot_see_other_orgs_playlists(client):
    """An API key for org A must NOT see playlists created by org B."""
    _ta, org_a, _ua = _signup_org(client)
    key_a, _ = _mint_key_row(org_a, scope="api:rw")
    tb, org_b, _ub = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(tb), json={"name": "B's playlist"})
    assert r.status_code in (200, 201), r.text
    b_pl_id = r.json()["id"]
    r = client.get("/playlists", headers=_bearer(key_a))
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()]
    assert b_pl_id not in ids


def test_api_key_cannot_modify_other_orgs_playlists(client):
    _ta, org_a, _ua = _signup_org(client)
    key_a, _ = _mint_key_row(org_a, scope="api:rw")
    tb, org_b, _ub = _signup_org(client)
    r = client.post("/playlists", headers=_bearer(tb), json={"name": "B's playlist"})
    b_pl_id = r.json()["id"]
    r = client.delete(f"/playlists/{b_pl_id}", headers=_bearer(key_a))
    assert r.status_code == 404, r.text
