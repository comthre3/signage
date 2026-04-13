import hashlib
import json
import os
import random
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import init_db, execute, query_all, query_one, utc_now_iso


UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
PREVIEW_TTL_SECONDS = int(os.getenv("PREVIEW_TTL_SECONDS", "300"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))

app = FastAPI(title="Menu Signage Backend")


def parse_allowed_origins(value: str) -> list[str]:
    if not value or value.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "site"


def generate_pair_code() -> str:
    return f"{random.randint(100000, 999999)}"


def generate_unique_pair_code() -> str:
    while True:
        code = generate_pair_code()
        if not query_one("SELECT id FROM screens WHERE pair_code = ?", (code,)):
            return code


def generate_unique_token() -> str:
    while True:
        token = uuid.uuid4().hex
        if not query_one("SELECT id FROM screens WHERE token = ?", (token,)):
            return token


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return secrets.compare_digest(digest.hex(), digest_hex)


ROLE_LEVELS = {"viewer": 1, "editor": 2, "admin": 3}


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        raise HTTPException(status_code=400, detail="Password must include a letter")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Password must include a number")


def is_online(last_seen: Optional[str]) -> bool:
    if not last_seen:
        return False
    try:
        last_seen_dt = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last_seen_dt).total_seconds() < 90


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")
    session = query_one(
        """
        SELECT sessions.token, sessions.user_id, users.username, users.is_admin,
               users.role
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    last_used = session.get("last_used") or session.get("created_at")
    if last_used:
        try:
            last_used_dt = datetime.fromisoformat(last_used)
        except ValueError:
            last_used_dt = None
        if last_used_dt:
            if (datetime.now(timezone.utc) - last_used_dt).total_seconds() > SESSION_TTL_SECONDS:
                execute("DELETE FROM sessions WHERE token = ?", (token,))
                raise HTTPException(status_code=401, detail="Session expired")
    execute(
        "UPDATE sessions SET last_used = ? WHERE token = ?",
        (utc_now_iso(), token),
    )
    return {
        "id": session["user_id"],
        "username": session["username"],
        "is_admin": bool(session["is_admin"]),
        "role": session.get("role") or ("admin" if session["is_admin"] else "viewer"),
        "token": token,
    }


def require_roles(*roles: str):
    def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in ROLE_LEVELS:
            raise HTTPException(status_code=403, detail="Role not permitted")
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return dependency


def can_access_screen(user: dict, screen_id: int) -> bool:
    if user.get("is_admin"):
        return True
    owner = query_one("SELECT id FROM screens WHERE id = ? AND owner_user_id = ?", (screen_id, user["id"]))
    if owner:
        return True
    membership = query_one(
        """
        SELECT screen_groups.id
        FROM screen_groups
        JOIN user_groups ON user_groups.group_id = screen_groups.group_id
        WHERE screen_groups.screen_id = ? AND user_groups.user_id = ?
        LIMIT 1
        """,
        (screen_id, user["id"]),
    )
    return bool(membership)


def require_screen_access(screen_id: int, user: dict) -> None:
    if not can_access_screen(user, screen_id):
        raise HTTPException(status_code=403, detail="Screen access denied")

def sanitize_screen(screen: dict, include_token: bool = False) -> dict:
    sanitized = dict(screen)
    sanitized.pop("password_hash", None)
    if not include_token:
        sanitized.pop("token", None)
    sanitized["is_online"] = is_online(screen.get("last_seen"))
    sanitized["password_set"] = bool(screen.get("password_hash"))
    return sanitized


def cleanup_sessions() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
    execute("DELETE FROM sessions WHERE COALESCE(last_used, created_at) < ?", (cutoff,))


def cleanup_preview_tokens() -> None:
    cutoff = utc_now_iso()
    execute("DELETE FROM preview_tokens WHERE expires_at < ?", (cutoff,))


class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: Optional[str] = None


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


class ScreenCreate(BaseModel):
    name: str = Field(..., min_length=1)
    location: Optional[str] = None
    resolution: Optional[str] = None
    orientation: Optional[str] = None
    site_id: Optional[int] = None
    owner_user_id: Optional[int] = None


class ScreenUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    resolution: Optional[str] = None
    orientation: Optional[str] = None
    site_id: Optional[int] = None
    playlist_id: Optional[int] = None
    owner_user_id: Optional[int] = None


class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1)


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None


class PlaylistItemCreate(BaseModel):
    media_id: int
    duration_seconds: int = Field(10, ge=1, le=3600)


class MediaUrlCreate(BaseModel):
    name: str = Field(..., min_length=1)
    url: str = Field(..., min_length=5)


class ZoneItemPayload(BaseModel):
    media_id: int
    duration_seconds: int = Field(10, ge=0, le=3600)


class ZonePayload(BaseModel):
    id: Optional[int] = None
    name: str = Field(..., min_length=1)
    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    width: float = Field(..., gt=0, le=1)
    height: float = Field(..., gt=0, le=1)
    sort_order: int = 0
    transition_ms: int = Field(600, ge=0, le=5000)
    items: list[ZoneItemPayload] = Field(default_factory=list)


class ScreenZonesPayload(BaseModel):
    zones: list[ZonePayload] = Field(default_factory=list)


class ZoneTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1)
    site_id: Optional[int] = None
    zones: list[ZonePayload] = Field(default_factory=list)


class ZoneTemplateApply(BaseModel):
    template_id: int


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1)


class GroupUpdate(BaseModel):
    name: str = Field(..., min_length=1)


class UserGroupsPayload(BaseModel):
    group_ids: list[int] = Field(default_factory=list)


class ScreenGroupsPayload(BaseModel):
    group_ids: list[int] = Field(default_factory=list)


class PairRequest(BaseModel):
    pair_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)
    role: str = "viewer"


class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8)
    role: Optional[str] = None


@app.on_event("startup")
def startup() -> None:
    init_db()
    cleanup_sessions()
    cleanup_preview_tokens()
    execute("UPDATE screens SET password_hash = NULL WHERE password_hash IS NOT NULL")
    execute("UPDATE users SET must_change_password = 0 WHERE must_change_password IS NOT NULL")
    existing = query_one("SELECT id FROM users LIMIT 1")
    if not existing:
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        validate_password(admin_password)
        execute(
            """
            INSERT INTO users (username, password_hash, is_admin, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (admin_username, hash_password(admin_password), 1, "admin", utc_now_iso()),
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    user = query_one("SELECT * FROM users WHERE username = ?", (payload.username,))
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    cleanup_sessions()
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user["id"], token, utc_now_iso(), utc_now_iso()),
    )
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role") or ("admin" if user["is_admin"] else "viewer"),
            "is_admin": bool(user["is_admin"]),
        },
    }


@app.post("/auth/logout")
def logout(user: dict = Depends(get_current_user)) -> dict:
    execute("DELETE FROM sessions WHERE token = ?", (user["token"],))
    return {"status": "logged_out"}


@app.post("/auth/change-password")
def change_password(
    payload: ChangePasswordRequest, user: dict = Depends(get_current_user)
) -> dict:
    db_user = query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    if not db_user or not verify_password(payload.current_password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid current password")
    validate_password(payload.new_password)
    execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(payload.new_password), user["id"]),
    )
    execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    return {"status": "password_changed"}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["is_admin"],
    }


@app.get("/users")
def list_users(_: dict = Depends(require_roles("admin"))) -> list[dict]:
    rows = query_all("SELECT id, username, is_admin, role, created_at FROM users ORDER BY created_at DESC")
    for row in rows:
        row["is_admin"] = bool(row["is_admin"])
        row["role"] = row.get("role") or ("admin" if row["is_admin"] else "viewer")
    return rows


@app.post("/users")
def create_user(payload: UserCreate, _: dict = Depends(require_roles("admin"))) -> dict:
    if query_one("SELECT id FROM users WHERE username = ?", (payload.username,)):
        raise HTTPException(status_code=400, detail="Username already exists")
    if payload.role not in ROLE_LEVELS:
        raise HTTPException(status_code=400, detail="Invalid role")
    validate_password(payload.password)
    user_id = execute(
        "INSERT INTO users (username, password_hash, is_admin, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            payload.username,
            hash_password(payload.password),
            int(payload.role == "admin"),
            payload.role,
            utc_now_iso(),
        ),
    )
    user = query_one(
        "SELECT id, username, is_admin, role, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    user["is_admin"] = bool(user["is_admin"])
    user["role"] = user.get("role") or ("admin" if user["is_admin"] else "viewer")
    return user


@app.put("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, _: dict = Depends(require_roles("admin"))) -> dict:
    user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.password:
        validate_password(payload.password)
        execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
    if payload.role is not None:
        if payload.role not in ROLE_LEVELS:
            raise HTTPException(status_code=400, detail="Invalid role")
        execute(
            "UPDATE users SET role = ?, is_admin = ? WHERE id = ?",
            (payload.role, int(payload.role == "admin"), user_id),
        )
    updated = query_one(
        "SELECT id, username, is_admin, role, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    updated["is_admin"] = bool(updated["is_admin"])
    updated["role"] = updated.get("role") or ("admin" if updated["is_admin"] else "viewer")
    return updated


@app.delete("/users/{user_id}")
def delete_user(user_id: int, _: dict = Depends(require_roles("admin"))) -> dict:
    user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return {"status": "deleted"}


@app.get("/sites")
def list_sites(_: dict = Depends(get_current_user)) -> list[dict]:
    return query_all("SELECT * FROM sites ORDER BY created_at DESC")


@app.post("/sites")
def create_site(payload: SiteCreate, _: dict = Depends(require_roles("admin", "editor"))) -> dict:
    slug = slugify(payload.slug or payload.name)
    base_slug = slug
    counter = 1
    while query_one("SELECT id FROM sites WHERE slug = ?", (slug,)):
        counter += 1
        slug = f"{base_slug}-{counter}"
    site_id = execute(
        "INSERT INTO sites (name, slug, created_at) VALUES (?, ?, ?)",
        (payload.name, slug, utc_now_iso()),
    )
    return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))


@app.put("/sites/{site_id}")
def update_site(
    site_id: int, payload: SiteUpdate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    site = query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    name = payload.name or site["name"]
    slug = slugify(payload.slug or site["slug"])
    if slug != site["slug"] and query_one(
        "SELECT id FROM sites WHERE slug = ? AND id != ?", (slug, site_id)
    ):
        raise HTTPException(status_code=400, detail="Slug already exists")

    execute(
        "UPDATE sites SET name = ?, slug = ? WHERE id = ?",
        (name, slug, site_id),
    )
    return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))


@app.delete("/sites/{site_id}")
def delete_site(site_id: int, _: dict = Depends(require_roles("admin"))) -> dict:
    site = query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    execute("UPDATE screens SET site_id = NULL WHERE site_id = ?", (site_id,))
    execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return {"status": "deleted"}


@app.get("/screens")
def list_screens(user: dict = Depends(get_current_user)) -> list[dict]:
    if user.get("is_admin"):
        rows = query_all(
            """
            SELECT screens.*, sites.name AS site_name, playlists.name AS playlist_name
            FROM screens
            LEFT JOIN sites ON sites.id = screens.site_id
            LEFT JOIN playlists ON playlists.id = screens.playlist_id
            ORDER BY screens.created_at DESC
            """
        )
    else:
        rows = query_all(
            """
            SELECT DISTINCT screens.*, sites.name AS site_name, playlists.name AS playlist_name
            FROM screens
            LEFT JOIN sites ON sites.id = screens.site_id
            LEFT JOIN playlists ON playlists.id = screens.playlist_id
            LEFT JOIN screen_groups ON screen_groups.screen_id = screens.id
            LEFT JOIN user_groups ON user_groups.group_id = screen_groups.group_id
            WHERE screens.owner_user_id = ? OR user_groups.user_id = ?
            ORDER BY screens.created_at DESC
            """,
            (user["id"], user["id"]),
        )
    return [sanitize_screen(row, include_token=False) for row in rows]


@app.post("/screens")
def create_screen(payload: ScreenCreate, _: dict = Depends(require_roles("admin", "editor"))) -> dict:
    pair_code = generate_unique_pair_code()
    token = generate_unique_token()
    screen_id = execute(
        """
        INSERT INTO screens (
            name, location, resolution, orientation, site_id, owner_user_id,
            pair_code, token, password_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.location,
            payload.resolution,
            payload.orientation,
            payload.site_id,
            payload.owner_user_id,
            pair_code,
            token,
            None,
            utc_now_iso(),
        ),
    )
    screen = query_one("SELECT * FROM screens WHERE id = ?", (screen_id,))
    return sanitize_screen(screen)


@app.put("/screens/{screen_id}")
def update_screen(
    screen_id: int, payload: ScreenUpdate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one("SELECT * FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    execute(
        """
        UPDATE screens
        SET name = ?, location = ?, resolution = ?, orientation = ?,
            site_id = ?, playlist_id = ?, password_hash = ?, owner_user_id = ?
        WHERE id = ?
        """,
        (
            payload.name or screen["name"],
            payload.location if payload.location is not None else screen["location"],
            payload.resolution if payload.resolution is not None else screen["resolution"],
            payload.orientation if payload.orientation is not None else screen["orientation"],
            payload.site_id if payload.site_id is not None else screen["site_id"],
            payload.playlist_id if payload.playlist_id is not None else screen["playlist_id"],
            None,
            payload.owner_user_id if payload.owner_user_id is not None else screen["owner_user_id"],
            screen_id,
        ),
    )
    return sanitize_screen(query_one("SELECT * FROM screens WHERE id = ?", (screen_id,)))


@app.delete("/screens/{screen_id}")
def delete_screen(screen_id: int, _: dict = Depends(require_roles("admin"))) -> dict:
    screen = query_one("SELECT * FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute("DELETE FROM screens WHERE id = ?", (screen_id,))
    return {"status": "deleted"}


@app.get("/screens/{screen_id}/zones")
def list_screen_zones(
    screen_id: int, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one("SELECT id FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not user.get("is_admin"):
        require_screen_access(screen_id, user)
    zones = get_screen_zones(screen_id)
    return {"zones": zones}


@app.put("/screens/{screen_id}/zones")
def update_screen_zones(
    screen_id: int,
    payload: ScreenZonesPayload,
    user: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    screen = query_one("SELECT id FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not user.get("is_admin"):
        require_screen_access(screen_id, user)
    execute(
        "DELETE FROM screen_zone_items WHERE zone_id IN (SELECT id FROM screen_zones WHERE screen_id = ?)",
        (screen_id,),
    )
    execute("DELETE FROM screen_zones WHERE screen_id = ?", (screen_id,))

    for index, zone in enumerate(payload.zones):
        zone_id = execute(
            """
            INSERT INTO screen_zones (screen_id, name, x, y, width, height, sort_order, transition_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                screen_id,
                zone.name,
                zone.x,
                zone.y,
                zone.width,
                zone.height,
                zone.sort_order if zone.sort_order is not None else index,
                zone.transition_ms,
                utc_now_iso(),
            ),
        )
        for item_index, item in enumerate(zone.items):
            execute(
                """
                INSERT INTO screen_zone_items
                (zone_id, media_id, duration_seconds, position, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (zone_id, item.media_id, item.duration_seconds, item_index, utc_now_iso()),
            )
    return {"zones": get_screen_zones(screen_id)}


@app.get("/screens/{token}/layout")
def screen_layout(token: str) -> dict:
    screen = query_one("SELECT * FROM screens WHERE token = ?", (token,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute(
        "UPDATE screens SET last_seen = ? WHERE id = ?",
        (utc_now_iso(), screen["id"]),
    )
    zones = get_screen_zones(screen["id"])
    return {"screen": sanitize_screen(screen), "zones": zones}


@app.get("/preview/{token}/layout")
def preview_layout(token: str) -> dict:
    preview = query_one("SELECT * FROM preview_tokens WHERE token = ?", (token,))
    if not preview:
        raise HTTPException(status_code=404, detail="Preview token not found")
    if preview.get("expires_at"):
        try:
            expires_dt = datetime.fromisoformat(preview["expires_at"])
        except ValueError:
            expires_dt = None
        if expires_dt and expires_dt < datetime.now(timezone.utc):
            execute("DELETE FROM preview_tokens WHERE token = ?", (token,))
            raise HTTPException(status_code=410, detail="Preview token expired")
    screen = query_one("SELECT * FROM screens WHERE id = ?", (preview["screen_id"],))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    zones = get_screen_zones(screen["id"])
    return {"screen": sanitize_screen(screen), "zones": zones}


@app.get("/zone-templates")
def list_zone_templates(site_id: Optional[int] = None, _: dict = Depends(require_roles("admin", "editor"))) -> list[dict]:
    if site_id is not None:
        return query_all(
            "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates WHERE site_id = ? ORDER BY created_at DESC",
            (site_id,),
        )
    return query_all(
        "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates ORDER BY created_at DESC"
    )


@app.post("/zone-templates")
def create_zone_template(
    payload: ZoneTemplateCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    layout_json = json.dumps([zone.dict() for zone in payload.zones])
    template_id = execute(
        """
        INSERT INTO screen_zone_templates (site_id, name, layout_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (payload.site_id, payload.name, layout_json, utc_now_iso()),
    )
    return query_one(
        "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates WHERE id = ?",
        (template_id,),
    )


@app.post("/screens/{screen_id}/zone-templates/apply")
def apply_zone_template(
    screen_id: int,
    payload: ZoneTemplateApply,
    _: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    screen = query_one("SELECT id FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    template = query_one(
        "SELECT layout_json FROM screen_zone_templates WHERE id = ?",
        (payload.template_id,),
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        zones = json.loads(template["layout_json"])
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Template data invalid")
    return update_screen_zones(screen_id, ScreenZonesPayload(zones=zones))


@app.get("/groups")
def list_groups(_: dict = Depends(require_roles("admin"))) -> list[dict]:
    return query_all("SELECT id, name, created_at FROM groups ORDER BY created_at DESC")


@app.post("/groups")
def create_group(payload: GroupCreate, _: dict = Depends(require_roles("admin"))) -> dict:
    group_id = execute(
        "INSERT INTO groups (name, created_at) VALUES (?, ?)",
        (payload.name, utc_now_iso()),
    )
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.put("/groups/{group_id}")
def update_group(group_id: int, payload: GroupUpdate, _: dict = Depends(require_roles("admin"))) -> dict:
    group = query_one("SELECT id FROM groups WHERE id = ?", (group_id,))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    execute("UPDATE groups SET name = ? WHERE id = ?", (payload.name, group_id))
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, _: dict = Depends(require_roles("admin"))) -> dict:
    group = query_one("SELECT id FROM groups WHERE id = ?", (group_id,))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,))
    execute("DELETE FROM screen_groups WHERE group_id = ?", (group_id,))
    execute("DELETE FROM groups WHERE id = ?", (group_id,))
    return {"status": "deleted"}


@app.put("/users/{user_id}/groups")
def update_user_groups(
    user_id: int, payload: UserGroupsPayload, _: dict = Depends(require_roles("admin"))
) -> dict:
    user = query_one("SELECT id FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
    for group_id in payload.group_ids:
        execute(
            "INSERT OR IGNORE INTO user_groups (user_id, group_id, created_at) VALUES (?, ?, ?)",
            (user_id, group_id, utc_now_iso()),
        )
    groups = query_all(
        """
        SELECT groups.id, groups.name
        FROM groups
        JOIN user_groups ON user_groups.group_id = groups.id
        WHERE user_groups.user_id = ?
        """,
        (user_id,),
    )
    return {"user_id": user_id, "groups": groups}


@app.get("/users/{user_id}/groups")
def list_user_groups(
    user_id: int, _: dict = Depends(require_roles("admin"))
) -> dict:
    user = query_one("SELECT id FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    groups = query_all(
        """
        SELECT groups.id, groups.name
        FROM groups
        JOIN user_groups ON user_groups.group_id = groups.id
        WHERE user_groups.user_id = ?
        """,
        (user_id,),
    )
    return {"user_id": user_id, "groups": groups}


@app.put("/screens/{screen_id}/groups")
def update_screen_groups(
    screen_id: int, payload: ScreenGroupsPayload, _: dict = Depends(require_roles("admin"))
) -> dict:
    screen = query_one("SELECT id FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute("DELETE FROM screen_groups WHERE screen_id = ?", (screen_id,))
    for group_id in payload.group_ids:
        execute(
            "INSERT OR IGNORE INTO screen_groups (screen_id, group_id, created_at) VALUES (?, ?, ?)",
            (screen_id, group_id, utc_now_iso()),
        )
    groups = query_all(
        """
        SELECT groups.id, groups.name
        FROM groups
        JOIN screen_groups ON screen_groups.group_id = groups.id
        WHERE screen_groups.screen_id = ?
        """,
        (screen_id,),
    )
    return {"screen_id": screen_id, "groups": groups}


@app.get("/screens/{screen_id}/groups")
def list_screen_groups(
    screen_id: int, _: dict = Depends(require_roles("admin"))
) -> dict:
    screen = query_one("SELECT id FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    groups = query_all(
        """
        SELECT groups.id, groups.name
        FROM groups
        JOIN screen_groups ON screen_groups.group_id = groups.id
        WHERE screen_groups.screen_id = ?
        """,
        (screen_id,),
    )
    return {"screen_id": screen_id, "groups": groups}


def build_screen_payload(screen: dict) -> dict:
    playlist = None
    items = []
    if screen.get("playlist_id"):
        playlist = query_one("SELECT * FROM playlists WHERE id = ?", (screen["playlist_id"],))
        items = query_all(
            """
            SELECT playlist_items.id, playlist_items.duration_seconds,
                   playlist_items.position, media.id AS media_id,
                   media.name, media.filename, media.mime_type
            FROM playlist_items
            JOIN media ON media.id = playlist_items.media_id
            WHERE playlist_items.playlist_id = ?
            ORDER BY playlist_items.position ASC
            """,
            (screen["playlist_id"],),
        )
        for item in items:
            if item.get("mime_type") == "text/url":
                item["url"] = item["filename"]
            else:
                item["url"] = f"/uploads/{item['filename']}"
    return {"playlist": playlist, "items": items}


def get_screen_zones(screen_id: int) -> list[dict]:
    zones = query_all(
        """
        SELECT id, screen_id, name, x, y, width, height, sort_order, transition_ms
        FROM screen_zones
        WHERE screen_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (screen_id,),
    )
    for zone in zones:
        items = query_all(
            """
            SELECT screen_zone_items.id, screen_zone_items.duration_seconds,
                   screen_zone_items.position, media.id AS media_id,
                   media.name, media.filename, media.mime_type
            FROM screen_zone_items
            JOIN media ON media.id = screen_zone_items.media_id
            WHERE screen_zone_items.zone_id = ?
            ORDER BY screen_zone_items.position ASC
            """,
            (zone["id"],),
        )
        for item in items:
            if item.get("mime_type") == "text/url":
                item["url"] = item["filename"]
            else:
                item["url"] = f"/uploads/{item['filename']}"
        zone["items"] = items
    return zones


@app.post("/screens/pair")
def pair_screen(payload: PairRequest) -> dict:
    screen = query_one("SELECT * FROM screens WHERE pair_code = ?", (payload.pair_code,))
    if not screen:
        raise HTTPException(status_code=404, detail="Pairing code not found")
    execute(
        "UPDATE screens SET last_seen = ? WHERE id = ?",
        (utc_now_iso(), screen["id"]),
    )
    response = sanitize_screen(screen)
    response["token"] = screen["token"]
    response["is_online"] = True
    return response


@app.get("/screens/{token}/content")
def screen_content(token: str) -> dict:
    screen = query_one("SELECT * FROM screens WHERE token = ?", (token,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    execute(
        "UPDATE screens SET last_seen = ? WHERE id = ?",
        (utc_now_iso(), screen["id"]),
    )

    payload = build_screen_payload(screen)
    payload["screen"] = sanitize_screen(screen)
    return payload


@app.post("/screens/{screen_id}/preview-token")
def create_preview_token(
    screen_id: int, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one("SELECT * FROM screens WHERE id = ?", (screen_id,))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not user.get("is_admin"):
        require_screen_access(screen_id, user)
    cleanup_preview_tokens()
    token = uuid.uuid4().hex
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_TTL_SECONDS)).isoformat()
    execute(
        "INSERT INTO preview_tokens (screen_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (screen_id, token, utc_now_iso(), expires_at),
    )
    return {"token": token, "expires_at": expires_at}


@app.get("/preview/{token}/content")
def preview_content(token: str) -> dict:
    preview = query_one("SELECT * FROM preview_tokens WHERE token = ?", (token,))
    if not preview:
        raise HTTPException(status_code=404, detail="Preview token not found")
    if preview.get("expires_at"):
        try:
            expires_dt = datetime.fromisoformat(preview["expires_at"])
        except ValueError:
            expires_dt = None
        if expires_dt and expires_dt < datetime.now(timezone.utc):
            execute("DELETE FROM preview_tokens WHERE token = ?", (token,))
            raise HTTPException(status_code=410, detail="Preview token expired")
    screen = query_one("SELECT * FROM screens WHERE id = ?", (preview["screen_id"],))
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    payload = build_screen_payload(screen)
    payload["screen"] = sanitize_screen(screen)
    return payload


@app.get("/playlists")
def list_playlists(_: dict = Depends(get_current_user)) -> list[dict]:
    return query_all("SELECT * FROM playlists ORDER BY created_at DESC")


@app.post("/playlists")
def create_playlist(
    payload: PlaylistCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    playlist_id = execute(
        "INSERT INTO playlists (name, created_at) VALUES (?, ?)",
        (payload.name, utc_now_iso()),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.get("/playlists/{playlist_id}")
def get_playlist(playlist_id: int, _: dict = Depends(get_current_user)) -> dict:
    playlist = query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    items = query_all(
        """
        SELECT playlist_items.id, playlist_items.duration_seconds,
               playlist_items.position, media.id AS media_id,
               media.name, media.filename, media.mime_type
        FROM playlist_items
        JOIN media ON media.id = playlist_items.media_id
        WHERE playlist_items.playlist_id = ?
        ORDER BY playlist_items.position ASC
        """,
        (playlist_id,),
    )
    for item in items:
        if item.get("mime_type") == "text/url":
            item["url"] = item["filename"]
        else:
            item["url"] = f"/uploads/{item['filename']}"
    return {"playlist": playlist, "items": items}


@app.put("/playlists/{playlist_id}")
def update_playlist(
    playlist_id: int,
    payload: PlaylistUpdate,
    _: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    playlist = query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute(
        "UPDATE playlists SET name = ? WHERE id = ?",
        (payload.name or playlist["name"], playlist_id),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: int, _: dict = Depends(require_roles("admin"))) -> dict:
    playlist = query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    execute("UPDATE screens SET playlist_id = NULL WHERE playlist_id = ?", (playlist_id,))
    return {"status": "deleted"}


@app.post("/playlists/{playlist_id}/items")
def add_playlist_item(
    playlist_id: int, payload: PlaylistItemCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    playlist = query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    media = query_one("SELECT * FROM media WHERE id = ?", (payload.media_id,))
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    max_position = query_one(
        "SELECT MAX(position) AS max_position FROM playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    )
    position = (max_position["max_position"] or 0) + 1
    item_id = execute(
        """
        INSERT INTO playlist_items
        (playlist_id, media_id, duration_seconds, position, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (playlist_id, payload.media_id, payload.duration_seconds, position, utc_now_iso()),
    )
    item = query_one("SELECT * FROM playlist_items WHERE id = ?", (item_id,))
    item["media"] = media
    item["url"] = f"/uploads/{media['filename']}"
    return item


@app.delete("/playlists/{playlist_id}/items/{item_id}")
def delete_playlist_item(
    playlist_id: int, item_id: int, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    item = query_one(
        "SELECT * FROM playlist_items WHERE id = ? AND playlist_id = ?",
        (item_id, playlist_id),
    )
    if not item:
        raise HTTPException(status_code=404, detail="Playlist item not found")
    execute("DELETE FROM playlist_items WHERE id = ?", (item_id,))
    return {"status": "deleted"}


@app.get("/media")
def list_media(_: dict = Depends(get_current_user)) -> list[dict]:
    media = query_all("SELECT * FROM media ORDER BY created_at DESC")
    for item in media:
        if item.get("mime_type") == "text/url":
            item["url"] = item["filename"]
        else:
            item["url"] = f"/uploads/{item['filename']}"
    return media


@app.post("/media/upload")
async def upload_media(
    file: UploadFile = File(...), _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    max_bytes = max(MAX_UPLOAD_MB, 1) * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    ext = os.path.splitext(file.filename or "")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)

    media_id = execute(
        """
        INSERT INTO media (name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            file.filename or filename,
            filename,
            file.content_type or "application/octet-stream",
            len(contents),
            utc_now_iso(),
        ),
    )
    item = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    item["url"] = f"/uploads/{item['filename']}"
    return item


@app.post("/media/url")
def create_media_url(
    payload: MediaUrlCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    url = payload.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    media_id = execute(
        """
        INSERT INTO media (name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (payload.name, url, "text/url", 0, utc_now_iso()),
    )
    item = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    item["url"] = item["filename"]
    return item


@app.delete("/media/{media_id}")
def delete_media(media_id: int, _: dict = Depends(require_roles("admin", "editor"))) -> dict:
    media = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    file_path = os.path.join(UPLOAD_DIR, media["filename"])
    if os.path.exists(file_path):
        os.remove(file_path)
    execute("DELETE FROM media WHERE id = ?", (media_id,))
    execute("DELETE FROM playlist_items WHERE media_id = ?", (media_id,))
    return {"status": "deleted"}
