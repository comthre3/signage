"""Tests for the Phase 2.5i-1 OAuth 2.1 authorization server."""
from db import query_one, query_all


def test_oauth_clients_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_clients", "client_id"),
    )
    assert row is not None


def test_oauth_authorization_codes_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_authorization_codes", "code_hash"),
    )
    assert row is not None


def test_oauth_tokens_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_tokens", "access_token_hash"),
    )
    assert row is not None


def test_pre_registered_clients_seeded():
    """All four known MCP clients should be seeded at init_db() time."""
    rows = query_all(
        "SELECT client_id FROM oauth_clients WHERE pre_registered = true "
        "ORDER BY client_id"
    )
    client_ids = [r["client_id"] for r in rows]
    for expected in ("claude-code", "claude-desktop", "cursor", "zed"):
        assert expected in client_ids, f"Missing pre-registered client: {expected}"


def test_pre_registered_clients_have_friendly_names():
    row = query_one(
        "SELECT client_name FROM oauth_clients WHERE client_id = ?",
        ("claude-desktop",),
    )
    assert row is not None
    assert row["client_name"] == "Claude Desktop"


# ── Discovery endpoints ──────────────────────────────────────────────


def test_well_known_authorization_server_metadata(client):
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorization_endpoint" in body
    assert "token_endpoint" in body
    assert "revocation_endpoint" in body
    assert "registration_endpoint" in body
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "api:read" in body["scopes_supported"]
    assert "api:rw" in body["scopes_supported"]


def test_well_known_protected_resource_metadata(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert body["bearer_methods_supported"] == ["header"]


def test_well_known_no_auth_required(client):
    """Discovery endpoints must be public — no Authorization header."""
    r1 = client.get("/.well-known/oauth-authorization-server")
    r2 = client.get("/.well-known/oauth-protected-resource")
    assert r1.status_code == 200
    assert r2.status_code == 200
