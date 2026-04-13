import os
import sqlite3
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/signage.db")
    if url.startswith("sqlite:////"):
        return "/" + url.replace("sqlite:////", "", 1)
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "", 1)
    return "./data/signage.db"


def connect() -> sqlite3.Connection:
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def ensure_column(cursor: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL,
            position INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            resolution TEXT,
            orientation TEXT,
            site_id INTEGER,
            owner_user_id INTEGER,
            pair_code TEXT NOT NULL UNIQUE,
            token TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            playlist_id INTEGER,
            created_at TEXT NOT NULL,
            last_seen TEXT,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE SET NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL,
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
            UNIQUE (user_id, group_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screen_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            screen_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (screen_id) REFERENCES screens (id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
            UNIQUE (screen_id, group_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_used TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS preview_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            screen_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (screen_id) REFERENCES screens (id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screen_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            screen_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            width REAL NOT NULL,
            height REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            transition_ms INTEGER NOT NULL DEFAULT 600,
            created_at TEXT NOT NULL,
            FOREIGN KEY (screen_id) REFERENCES screens (id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screen_zone_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL,
            position INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (zone_id) REFERENCES screen_zones (id) ON DELETE CASCADE,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screen_zone_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER,
            name TEXT NOT NULL,
            layout_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE SET NULL
        )
        """
    )

    ensure_column(cursor, "screens", "password_hash", "password_hash TEXT")
    ensure_column(cursor, "screens", "owner_user_id", "owner_user_id INTEGER")
    ensure_column(cursor, "users", "role", "role TEXT NOT NULL DEFAULT 'viewer'")
    ensure_column(
        cursor,
        "screen_zones",
        "transition_ms",
        "transition_ms INTEGER NOT NULL DEFAULT 600",
    )

    cursor.execute("UPDATE users SET role = 'admin' WHERE role = 'viewer' AND is_admin = 1")

    conn.commit()
    conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    conn.commit()
    last_id = cursor.lastrowid
    conn.close()
    return last_id


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
