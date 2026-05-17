"""Tests for the Phase 2.5h agent API platform."""


def test_api_keys_table_exists():
    from db import query_one
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("api_keys", "key_hash"),
    )
    assert row is not None
