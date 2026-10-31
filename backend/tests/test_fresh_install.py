"""Schema creation against an empty database.

The suite's own database is truncated between runs, never dropped, so every
table already exists by the time a test runs. That hides any statement in
init_db that touches a table before the statement that creates it: an upgrade
of an existing deployment works, while a brand-new install -- a new server, or
a restore onto an empty Postgres -- crashes on boot and never serves a request.

This builds the schema from nothing inside a throwaway Postgres schema, on a
connection of its own, so it can neither see nor disturb the suite's tables.
"""
import uuid

import psycopg
from psycopg.rows import dict_row

from db import _init_db_locked, get_dsn


def test_init_db_builds_the_whole_schema_from_an_empty_database():
    schema = f"fresh_{uuid.uuid4().hex[:12]}"
    conn = psycopg.connect(get_dsn(), autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')

        _init_db_locked(conn)
        _init_db_locked(conn)  # and it stays idempotent once everything exists

        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'api_keys'",
                (schema,),
            )
            columns = {r["column_name"] for r in cur.fetchall()}
        assert "key_sha256" in columns
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.close()
