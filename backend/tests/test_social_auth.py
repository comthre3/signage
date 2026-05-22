"""Tests for the Phase 2.5j social auth (Google + Apple)."""
from db import query_one, query_all, execute


def test_users_password_hash_is_nullable():
    row = query_one(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("users", "password_hash"),
    )
    assert row is not None
    assert row["is_nullable"] == "YES", row


def test_auth_identities_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("auth_identities", "subject_id"),
    )
    assert row is not None


def test_auth_identities_unique_constraint_on_provider_subject():
    """Two rows with the same (provider, subject_id) must conflict."""
    # Insert a sentinel user to FK to
    row = query_one(
        "SELECT id FROM users LIMIT 1"
    )
    if not row:
        # If the test DB is empty, create one
        execute(
            "INSERT INTO organizations (name, slug, plan, screen_limit, "
            "subscription_status, locale, created_at) "
            "VALUES ('SchemaTest', 'schematest', 'starter', 5, "
            "'trialing', 'en', now())"
        )
        org_id = query_one("SELECT id FROM organizations "
                           "WHERE slug = 'schematest'")["id"]
        execute(
            "INSERT INTO users (organization_id, username, password_hash, "
            "is_admin, role, created_at) "
            "VALUES (?, 'schematest@example.com', NULL, 1, 'admin', now())",
            (org_id,),
        )
        row = query_one(
            "SELECT id FROM users WHERE username = 'schematest@example.com'"
        )
    uid = row["id"]

    # First insert succeeds
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link) VALUES (?, 'google', 'duplicate-sub', "
        "'test@example.com')",
        (uid,),
    )
    # Second insert with same (provider, subject_id) must raise
    import psycopg
    raised = False
    try:
        execute(
            "INSERT INTO auth_identities (user_id, provider, subject_id, "
            "email_at_link) VALUES (?, 'google', 'duplicate-sub', "
            "'other@example.com')",
            (uid,),
        )
    except psycopg.errors.UniqueViolation:
        raised = True
    except Exception as exc:
        # Some db.py wrappers re-raise as a different type
        raised = "duplicate" in str(exc).lower() or "unique" in str(exc).lower()
    assert raised, "Expected unique violation on (provider, subject_id)"

    # Cleanup
    execute(
        "DELETE FROM auth_identities WHERE subject_id = 'duplicate-sub'"
    )
