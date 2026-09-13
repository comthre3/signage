import os
import re
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import lru_cache

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


# SQL strings here are static literals, so the same handful of strings are
# translated over and over -- this ran a per-character Python loop on every
# query. Caching makes a repeat call ~138x cheaper. Bounded so that any
# dynamically-built SQL cannot grow the cache without limit.
@lru_cache(maxsize=2048)
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


# Arbitrary but fixed application id for the schema-migration advisory lock.
_INIT_DB_LOCK_ID = 8412771903


@contextmanager
def advisory_lock(lock_id: int = _INIT_DB_LOCK_ID):
    """Serialize a block across every worker and replica via Postgres.

    Used for startup work that must happen exactly once even though each
    uvicorn worker runs it: schema DDL, and seeding the bootstrap admin.
    Session-scoped, so a worker that dies mid-block releases the lock when its
    connection drops rather than wedging every other worker forever.
    """
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))


def init_db() -> None:
    """Create/upgrade the schema.

    Serialized behind a Postgres advisory lock because the backend runs
    multiple uvicorn workers, each of which calls this on startup. Concurrent
    CREATE/ALTER of the same objects deadlocks or errors in Postgres even with
    IF NOT EXISTS -- the guards make the statements idempotent, not concurrent.
    The first worker in does the DDL; the rest block here briefly and then find
    everything already present. The lock is session-scoped and released in the
    finally block, so a crashed worker cannot wedge startup permanently (the
    connection dying releases it either way).
    """
    with advisory_lock():
        _init_db_locked(connect())


def _init_db_locked(conn) -> None:
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS walls (
                id                   SERIAL PRIMARY KEY,
                organization_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name                 TEXT NOT NULL,
                mode                 TEXT NOT NULL CHECK (mode IN ('spanned','mirrored')),
                rows                 INTEGER NOT NULL CHECK (rows BETWEEN 1 AND 8),
                cols                 INTEGER NOT NULL CHECK (cols BETWEEN 1 AND 8),
                canvas_width_px      INTEGER,
                canvas_height_px     INTEGER,
                bezel_enabled        BOOLEAN NOT NULL DEFAULT false,
                spanned_playlist_id  INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                mirrored_mode        TEXT CHECK (mirrored_mode IN ('same_playlist','synced_rotation')),
                mirrored_playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_walls_org ON walls(organization_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wall_cells (
                id                 SERIAL PRIMARY KEY,
                wall_id            INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
                row_index          INTEGER NOT NULL,
                col_index          INTEGER NOT NULL,
                screen_id          INTEGER REFERENCES screens(id) ON DELETE SET NULL,
                screen_size_inches NUMERIC(4,1),
                bezel_top_mm       NUMERIC(5,2),
                bezel_right_mm     NUMERIC(5,2),
                bezel_bottom_mm    NUMERIC(5,2),
                bezel_left_mm      NUMERIC(5,2),
                playlist_id        INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                created_at         TEXT NOT NULL,
                UNIQUE (wall_id, row_index, col_index)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_cells_wall ON wall_cells(wall_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_cells_screen ON wall_cells(screen_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wall_pairing_codes (
                id           SERIAL PRIMARY KEY,
                code         TEXT NOT NULL UNIQUE,
                device_id    TEXT,
                wall_id      INTEGER NOT NULL REFERENCES walls(id) ON DELETE CASCADE,
                row_index    INTEGER NOT NULL,
                col_index    INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','claimed','expired')),
                expires_at   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                claimed_at   TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wall_pairing_codes_wall ON wall_pairing_codes(wall_id)")

        # ── Phase 2.5c: security hardening ──────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
              id            SERIAL PRIMARY KEY,
              username      TEXT NOT NULL,
              attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              success       BOOLEAN NOT NULL,
              ip            TEXT
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_attempts_username_ts "
            "ON login_attempts (username, attempted_at DESC)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
              id              SERIAL PRIMARY KEY,
              organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
              actor_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
              actor_username  TEXT,
              action          TEXT NOT NULL,
              target_type     TEXT,
              target_id       TEXT,
              ip              TEXT,
              user_agent      TEXT,
              details         JSONB,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_org_ts "
            "ON audit_log (organization_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_actor "
            "ON audit_log (actor_user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_action "
            "ON audit_log (action, created_at DESC)"
        )

        cursor.execute("ALTER TABLE screens      ADD COLUMN IF NOT EXISTS password_hash        TEXT")
        cursor.execute("ALTER TABLE screens      ADD COLUMN IF NOT EXISTS owner_user_id        INTEGER")
        cursor.execute("ALTER TABLE users        ADD COLUMN IF NOT EXISTS role                 TEXT NOT NULL DEFAULT 'viewer'")
        cursor.execute("ALTER TABLE users        ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE screen_zones ADD COLUMN IF NOT EXISTS transition_ms        INTEGER NOT NULL DEFAULT 600")
        cursor.execute("ALTER TABLE screens       ADD COLUMN IF NOT EXISTS wall_cell_id INTEGER REFERENCES wall_cells(id) ON DELETE SET NULL")
        cursor.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS walls_enabled BOOLEAN NOT NULL DEFAULT true")
        cursor.execute("ALTER TABLE playlists     ADD COLUMN IF NOT EXISTS kind          TEXT NOT NULL DEFAULT 'standard' CHECK (kind IN ('standard','wall_canvas'))")
        cursor.execute("ALTER TABLE walls          ADD COLUMN IF NOT EXISTS bezel_h_pct REAL NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE walls          ADD COLUMN IF NOT EXISTS bezel_v_pct REAL NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE media          ADD COLUMN IF NOT EXISTS pdf_pages_status TEXT")
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN IF NOT EXISTS duration_override_seconds INTEGER")
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN IF NOT EXISTS fit_mode TEXT NOT NULL DEFAULT 'fit' CHECK (fit_mode IN ('fit','fill','stretch'))")
        # API keys are high-entropy random tokens (secrets.token_urlsafe(24) =
        # 192 bits), not user-chosen passwords, so they do not need a slow KDF.
        # key_sha256 lets an authenticated request resolve with one indexed
        # lookup instead of a PBKDF2-120k verification (~82ms of CPU each).
        # Nullable: rows minted before this column are upgraded in place on
        # their next successful use. See lookup_api_key() in main.py.
        # First-run setup. A deployment with no users publishes a one-time token
        # that must be presented to create the first administrator, so that
        # whoever merely reaches the URL first cannot claim the deployment.
        # Stored here rather than in process memory because the backend runs
        # several workers, which would otherwise each mint a different token.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS setup_token (
              id         INTEGER PRIMARY KEY DEFAULT 1,
              token      TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              CONSTRAINT setup_token_single_row CHECK (id = 1)
            )
        """)
        cursor.execute("ALTER TABLE api_keys      ADD COLUMN IF NOT EXISTS key_sha256 TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_sha256 ON api_keys (key_sha256) WHERE key_sha256 IS NOT NULL")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_screens_wall_cell ON screens(wall_cell_id)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sites_org       ON sites       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_playlists_org   ON playlists   (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_org       ON media       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_org       ON users       (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_screens_org     ON screens     (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_org      ON groups      (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_templates_org   ON screen_zone_templates (organization_id)")

        # ── Menus (Plan A) ──────────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menus (
                id               SERIAL PRIMARY KEY,
                organization_id  INTEGER NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
                name             TEXT NOT NULL,
                brand            JSONB NOT NULL DEFAULT '{}'::jsonb,
                template         TEXT NOT NULL DEFAULT 'dark-classic',
                source           JSONB,
                playlist_id      INTEGER REFERENCES playlists (id) ON DELETE SET NULL,
                last_rendered_at TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_categories (
                id         SERIAL PRIMARY KEY,
                menu_id    INTEGER NOT NULL REFERENCES menus (id) ON DELETE CASCADE,
                name_en    TEXT NOT NULL,
                name_ar    TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id             SERIAL PRIMARY KEY,
                category_id    INTEGER NOT NULL REFERENCES menu_categories (id) ON DELETE CASCADE,
                name_en        TEXT NOT NULL,
                name_ar        TEXT NOT NULL DEFAULT '',
                description_en TEXT,
                description_ar TEXT,
                price          NUMERIC(10,3),
                price_note     TEXT,
                badges         JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_available   BOOLEAN NOT NULL DEFAULT true,
                sort_order     INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_renders (
                id          SERIAL PRIMARY KEY,
                menu_id     INTEGER NOT NULL REFERENCES menus (id) ON DELETE CASCADE,
                kind        TEXT NOT NULL CHECK (kind IN ('board','category','promo')),
                category_id INTEGER REFERENCES menu_categories (id) ON DELETE CASCADE,
                item_id     INTEGER REFERENCES menu_items (id) ON DELETE CASCADE,
                language    TEXT NOT NULL CHECK (language IN ('en','ar','bi')),
                aspect      TEXT NOT NULL CHECK (aspect IN ('16:9','9:16')),
                status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed')),
                media_id    INTEGER REFERENCES media (id) ON DELETE SET NULL,
                render_hash TEXT NOT NULL,
                error       TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS menu_imports (
                id              SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
                menu_id         INTEGER REFERENCES menus (id) ON DELETE SET NULL,
                status          TEXT NOT NULL DEFAULT 'queued',
                step            TEXT,
                source          JSONB NOT NULL,
                error           JSONB,
                usage           JSONB,
                created_by      INTEGER,
                created_at      TEXT NOT NULL,
                finished_at     TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menus_org          ON menus (organization_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_cats_menu     ON menu_categories (menu_id, sort_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_items_cat     ON menu_items (category_id, sort_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_renders_menu  ON menu_renders (menu_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_imports_org   ON menu_imports (organization_id, created_at DESC)")

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
        cursor.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS niupay_payment_link TEXT NULL")

        # ── Phase 2.5e: dayparting ──────────────────────────────────────
        cursor.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kuwait'")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
              id              SERIAL PRIMARY KEY,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name            TEXT NOT NULL,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedules_org "
            "ON schedules (organization_id)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule_rules (
              id              SERIAL PRIMARY KEY,
              schedule_id     INTEGER NOT NULL REFERENCES schedules(id)  ON DELETE CASCADE,
              playlist_id     INTEGER NOT NULL REFERENCES playlists(id)  ON DELETE CASCADE,
              start_time      TIME NOT NULL,
              end_time        TIME NOT NULL,
              days_of_week    SMALLINT NOT NULL,
              position        INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedule_rules_schedule "
            "ON schedule_rules (schedule_id)"
        )

        cursor.execute("ALTER TABLE screens ADD COLUMN IF NOT EXISTS schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL")

        # ── Phase 2.5g: subscription reminders ──────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_reminders (
              id              SERIAL PRIMARY KEY,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              reminder_type   TEXT NOT NULL,
              expires_at      TIMESTAMPTZ NOT NULL,
              sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (organization_id, reminder_type, expires_at)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscription_reminders_org "
            "ON subscription_reminders (organization_id, reminder_type)"
        )
        # ── Phase 2.5h: agent API ────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
              id                 SERIAL PRIMARY KEY,
              organization_id    INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              name               TEXT NOT NULL,
              key_prefix         TEXT NOT NULL,
              key_hash           TEXT NOT NULL,
              scope              TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_used_at       TIMESTAMPTZ,
              revoked_at         TIMESTAMPTZ
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_org "
            "ON api_keys (organization_id, revoked_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_prefix "
            "ON api_keys (key_prefix)"
        )

        # ── Phase 2.5j: Social auth (Google + Apple) ────────────────────
        cursor.execute(
            "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_identities (
              id             SERIAL PRIMARY KEY,
              user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              provider       TEXT NOT NULL CHECK (provider IN ('google', 'apple')),
              subject_id     TEXT NOT NULL,
              email_at_link  TEXT NOT NULL,
              name_at_link   TEXT,
              created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_used_at   TIMESTAMPTZ,
              UNIQUE (provider, subject_id)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_identities_user "
            "ON auth_identities (user_id)"
        )

        # ── Phase 2.5i-1: OAuth 2.1 provider ─────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
              id              SERIAL PRIMARY KEY,
              client_id       TEXT NOT NULL UNIQUE,
              client_name     TEXT NOT NULL,
              redirect_uris   JSONB NOT NULL,
              pre_registered  BOOLEAN NOT NULL DEFAULT false,
              registered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
              id              SERIAL PRIMARY KEY,
              code_hash       TEXT NOT NULL UNIQUE,
              client_id       TEXT NOT NULL,
              organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              scope           TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              redirect_uri    TEXT NOT NULL,
              code_challenge  TEXT NOT NULL,
              expires_at      TIMESTAMPTZ NOT NULL,
              consumed_at     TIMESTAMPTZ,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
              id                  SERIAL PRIMARY KEY,
              access_token_hash   TEXT NOT NULL UNIQUE,
              refresh_token_hash  TEXT NOT NULL UNIQUE,
              client_id           TEXT NOT NULL,
              organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
              user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
              scope               TEXT NOT NULL CHECK (scope IN ('api:read', 'api:rw')),
              granted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
              access_expires_at   TIMESTAMPTZ NOT NULL,
              refresh_expires_at  TIMESTAMPTZ NOT NULL,
              last_used_at        TIMESTAMPTZ,
              revoked_at          TIMESTAMPTZ
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_access "
            "ON oauth_tokens (access_token_hash) WHERE revoked_at IS NULL"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh "
            "ON oauth_tokens (refresh_token_hash) WHERE revoked_at IS NULL"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_org "
            "ON oauth_tokens (organization_id, revoked_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_client "
            "ON oauth_tokens (client_id) WHERE revoked_at IS NULL"
        )

        # Pre-registered MCP clients (idempotent — ON CONFLICT DO NOTHING)
        _PRE_REGISTERED_CLIENTS = [
            ("claude-desktop", "Claude Desktop",
             '["claude-desktop://oauth/callback", "http://localhost:5173/oauth/callback"]'),
            ("claude-code",    "Claude Code",
             '["claude-code://oauth/callback"]'),
            ("cursor",         "Cursor",
             '["cursor://oauth/callback"]'),
            ("zed",            "Zed",
             '["zed://oauth/callback"]'),
        ]
        for cid, name, uris_json in _PRE_REGISTERED_CLIENTS:
            cursor.execute(
                "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, pre_registered) "
                "VALUES (%s, %s, %s::jsonb, true) "
                "ON CONFLICT (client_id) DO NOTHING",
                (cid, name, uris_json),
            )
