import os
import re
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

_local = threading.local()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://sawwii:sawwii@postgres:5432/sawwii",
    )


def connect() -> psycopg.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and not conn.closed:
        return conn
    dsn = get_dsn()
    last_err = None
    for _ in range(30):
        try:
            conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
            _local.conn = conn
            return conn
        except psycopg.OperationalError as err:
            last_err = err
            time.sleep(1)
    raise RuntimeError(f"Could not connect to Postgres: {last_err}")


def _translate_placeholders(sql: str) -> str:
    out = []
    in_single = False
    in_double = False
    for c in sql:
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
        elif c == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(c)
    return "".join(out)


_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)


def execute(sql: str, params: tuple = ()) -> int:
    sql = _translate_placeholders(sql)
    conn = connect()
    if _INSERT_RE.match(sql) and not _RETURNING_RE.search(sql):
        returning_sql = sql.rstrip().rstrip(";") + " RETURNING id"
        try:
            with conn.cursor() as cur:
                cur.execute(returning_sql, params)
                row = cur.fetchone()
            return int(row["id"]) if row else 0
        except psycopg.errors.UndefinedColumn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    sql = _translate_placeholders(sql)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    sql = _translate_placeholders(sql)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None


def init_db() -> None:
    conn = connect()
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id                     SERIAL PRIMARY KEY,
                name                   TEXT NOT NULL,
                slug                   TEXT NOT NULL UNIQUE,
                plan                   TEXT NOT NULL DEFAULT 'starter',
                screen_limit           INTEGER NOT NULL DEFAULT 3,
                subscription_status    TEXT NOT NULL DEFAULT 'trialing',
                trial_ends_at          TEXT,
                stripe_customer_id     TEXT,
                stripe_subscription_id TEXT,
                locale                 TEXT NOT NULL DEFAULT 'en',
                created_at             TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sites (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                slug            TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                UNIQUE (organization_id, slug)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                filename        TEXT NOT NULL,
                mime_type       TEXT NOT NULL,
                size            BIGINT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_items (
                id               SERIAL PRIMARY KEY,
                playlist_id      INTEGER NOT NULL REFERENCES playlists (id) ON DELETE CASCADE,
                media_id         INTEGER NOT NULL REFERENCES media (id)     ON DELETE CASCADE,
                duration_seconds INTEGER NOT NULL,
                position         INTEGER NOT NULL,
                created_at       TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                username        TEXT NOT NULL UNIQUE,
                password_hash   TEXT NOT NULL,
                is_admin        INTEGER NOT NULL DEFAULT 0,
                role            TEXT NOT NULL DEFAULT 'viewer',
                created_at      TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screens (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                location        TEXT,
                resolution      TEXT,
                orientation     TEXT,
                site_id         INTEGER REFERENCES sites (id)     ON DELETE SET NULL,
                owner_user_id   INTEGER REFERENCES users (id)     ON DELETE SET NULL,
                pair_code       TEXT NOT NULL UNIQUE,
                token           TEXT NOT NULL UNIQUE,
                password_hash   TEXT,
                playlist_id     INTEGER REFERENCES playlists (id) ON DELETE SET NULL,
                created_at      TEXT NOT NULL,
                last_seen       TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                UNIQUE (organization_id, name)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users (id)  ON DELETE CASCADE,
                group_id   INTEGER NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                UNIQUE (user_id, group_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screen_groups (
                id         SERIAL PRIMARY KEY,
                screen_id  INTEGER NOT NULL REFERENCES screens (id) ON DELETE CASCADE,
                group_id   INTEGER NOT NULL REFERENCES groups (id)  ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                UNIQUE (screen_id, group_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                token      TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used  TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preview_tokens (
                id         SERIAL PRIMARY KEY,
                screen_id  INTEGER NOT NULL REFERENCES screens (id) ON DELETE CASCADE,
                token      TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screen_zones (
                id            SERIAL PRIMARY KEY,
                screen_id     INTEGER NOT NULL REFERENCES screens (id) ON DELETE CASCADE,
                name          TEXT NOT NULL,
                x             DOUBLE PRECISION NOT NULL,
                y             DOUBLE PRECISION NOT NULL,
                width         DOUBLE PRECISION NOT NULL,
                height        DOUBLE PRECISION NOT NULL,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                transition_ms INTEGER NOT NULL DEFAULT 600,
                created_at    TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screen_zone_items (
                id               SERIAL PRIMARY KEY,
                zone_id          INTEGER NOT NULL REFERENCES screen_zones (id) ON DELETE CASCADE,
                media_id         INTEGER NOT NULL REFERENCES media (id)        ON DELETE CASCADE,
                duration_seconds INTEGER NOT NULL,
                position         INTEGER NOT NULL,
                created_at       TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screen_zone_templates (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER REFERENCES organizations (id) ON DELETE CASCADE,
                site_id         INTEGER REFERENCES sites (id) ON DELETE SET NULL,
                name            TEXT NOT NULL,
                layout_json     TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_signups (
                id                             SERIAL PRIMARY KEY,
                email                          TEXT NOT NULL UNIQUE,
                business_name                  TEXT NOT NULL,
                otp_hash                       TEXT NOT NULL,
                attempts                       INTEGER NOT NULL DEFAULT 0,
                expires_at                     TEXT NOT NULL,
                last_sent_at                   TEXT NOT NULL,
                verification_token             TEXT,
                verification_token_expires_at  TEXT,
                created_at                     TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairing_codes (
                id           SERIAL PRIMARY KEY,
                code         TEXT NOT NULL UNIQUE,
                device_id    TEXT NOT NULL UNIQUE,
                status       TEXT NOT NULL DEFAULT 'pending',
                screen_id    INTEGER REFERENCES screens (id) ON DELETE SET NULL,
                expires_at   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                claimed_at   TEXT
            )
        """)

        cursor.execute("ALTER TABLE screens      ADD COLUMN IF NOT EXISTS password_hash        TEXT")
        cursor.execute("ALTER TABLE screens      ADD COLUMN IF NOT EXISTS owner_user_id        INTEGER")
        cursor.execute("ALTER TABLE users        ADD COLUMN IF NOT EXISTS role                 TEXT NOT NULL DEFAULT 'viewer'")
        cursor.execute("ALTER TABLE users        ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE screen_zones ADD COLUMN IF NOT EXISTS transition_ms        INTEGER NOT NULL DEFAULT 600")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sites_org       ON sites       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_playlists_org   ON playlists   (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_org       ON media       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_org       ON users       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_screens_org     ON screens     (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_org      ON groups      (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_templates_org   ON screen_zone_templates (organization_id)")

        cursor.execute("UPDATE users SET role = 'admin' WHERE role = 'viewer' AND is_admin = 1")

        cursor.execute(
            """
            ALTER TABLE organizations
              ADD COLUMN IF NOT EXISTS paid_through_at   TIMESTAMPTZ NULL,
              ADD COLUMN IF NOT EXISTS plan_term_months  INTEGER     NULL
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
              id                  SERIAL PRIMARY KEY,
              organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id             INTEGER NOT NULL REFERENCES users(id),
              trackid             TEXT    NOT NULL UNIQUE,
              tier                TEXT    NOT NULL,
              term_months         INTEGER NOT NULL,
              amount_kwd          INTEGER NOT NULL,
              amount_usd_display  NUMERIC(10,2) NOT NULL,
              status              TEXT    NOT NULL DEFAULT 'pending',
              niupay_payment_id   TEXT    NULL,
              niupay_tranid       TEXT    NULL,
              niupay_result       TEXT    NULL,
              niupay_ref          TEXT    NULL,
              created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_payments_org_status ON payments(organization_id, status)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_payments_pending_key "
            "ON payments(organization_id, tier, term_months) WHERE status='pending'"
        )
