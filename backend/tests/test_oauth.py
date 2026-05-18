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
