#!/usr/bin/env python3
"""One-shot SQLite → Postgres migration.

Run from inside the backend container:
    docker-compose exec backend python migrate_sqlite.py

Creates a "Legacy" organization and copies every row from /app/data/signage.db
into Postgres under that org. Refuses to run if Postgres already has any
organizations (safety).
"""
import os
import sqlite3
import sys

from db import execute, query_one, init_db, utc_now_iso

SQLITE_PATH = "/app/data/signage.db"


def _has(row: sqlite3.Row, key: str) -> bool:
    return key in row.keys()


def main() -> int:
    if not os.path.exists(SQLITE_PATH):
        print(f"No SQLite db at {SQLITE_PATH} — nothing to migrate.")
        return 0

    init_db()
    existing = query_one("SELECT COUNT(*) AS n FROM organizations")
    if existing and int(existing["n"]) > 0:
        print(f"Postgres already has {existing['n']} organization(s). Refusing to migrate.")
        return 1

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    legacy_id = execute(
        """
        INSERT INTO organizations
        (name, slug, plan, screen_limit, subscription_status, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("Legacy", "legacy", "pro", 25, "active", "en", utc_now_iso()),
    )
    print(f"Created Legacy organization id={legacy_id}")

    site_map, user_map, playlist_map = {}, {}, {}
    media_map, screen_map, group_map, zone_map = {}, {}, {}, {}

    for row in src.execute("SELECT * FROM sites"):
        site_map[row["id"]] = execute(
            "INSERT INTO sites (organization_id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (legacy_id, row["name"], row["slug"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM users"):
        user_map[row["id"]] = execute(
            "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                legacy_id,
                row["username"],
                row["password_hash"],
                row["is_admin"],
                row["role"] if _has(row, "role") else "viewer",
                row["created_at"],
            ),
        )

    for row in src.execute("SELECT * FROM playlists"):
        playlist_map[row["id"]] = execute(
            "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
            (legacy_id, row["name"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM media"):
        media_map[row["id"]] = execute(
            "INSERT INTO media (organization_id, name, filename, mime_type, size, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (legacy_id, row["name"], row["filename"], row["mime_type"], row["size"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM groups"):
        group_map[row["id"]] = execute(
            "INSERT INTO groups (organization_id, name, created_at) VALUES (?, ?, ?)",
            (legacy_id, row["name"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM screens"):
        screen_map[row["id"]] = execute(
            """
            INSERT INTO screens (organization_id, name, location, resolution, orientation,
                                 site_id, owner_user_id, pair_code, token, password_hash,
                                 playlist_id, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_id,
                row["name"],
                row["location"],
                row["resolution"],
                row["orientation"],
                site_map.get(row["site_id"]) if _has(row, "site_id") and row["site_id"] else None,
                user_map.get(row["owner_user_id"]) if _has(row, "owner_user_id") and row["owner_user_id"] else None,
                row["pair_code"],
                row["token"],
                row["password_hash"] if _has(row, "password_hash") else None,
                playlist_map.get(row["playlist_id"]) if _has(row, "playlist_id") and row["playlist_id"] else None,
                row["created_at"],
                row["last_seen"] if _has(row, "last_seen") else None,
            ),
        )

    for row in src.execute("SELECT * FROM playlist_items"):
        execute(
            "INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) VALUES (?, ?, ?, ?, ?)",
            (playlist_map[row["playlist_id"]], media_map[row["media_id"]],
             row["duration_seconds"], row["position"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM user_groups"):
        execute(
            "INSERT INTO user_groups (user_id, group_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (user_map[row["user_id"]], group_map[row["group_id"]], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM screen_groups"):
        execute(
            "INSERT INTO screen_groups (screen_id, group_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (screen_map[row["screen_id"]], group_map[row["group_id"]], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM screen_zones"):
        zone_map[row["id"]] = execute(
            "INSERT INTO screen_zones (screen_id, name, x, y, width, height, sort_order, transition_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                screen_map[row["screen_id"]],
                row["name"],
                row["x"],
                row["y"],
                row["width"],
                row["height"],
                row["sort_order"],
                row["transition_ms"] if _has(row, "transition_ms") else 600,
                row["created_at"],
            ),
        )

    for row in src.execute("SELECT * FROM screen_zone_items"):
        execute(
            "INSERT INTO screen_zone_items (zone_id, media_id, duration_seconds, position, created_at) VALUES (?, ?, ?, ?, ?)",
            (zone_map[row["zone_id"]], media_map[row["media_id"]],
             row["duration_seconds"], row["position"], row["created_at"]),
        )

    for row in src.execute("SELECT * FROM screen_zone_templates"):
        execute(
            "INSERT INTO screen_zone_templates (organization_id, site_id, name, layout_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                legacy_id,
                site_map.get(row["site_id"]) if _has(row, "site_id") and row["site_id"] else None,
                row["name"],
                row["layout_json"],
                row["created_at"],
            ),
        )

    print(
        f"Migrated: sites={len(site_map)} users={len(user_map)} playlists={len(playlist_map)} "
        f"media={len(media_map)} groups={len(group_map)} screens={len(screen_map)} zones={len(zone_map)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
