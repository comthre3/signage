import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from billing import create_knet_request
from db import init_db, execute, query_all, query_one, utc_now_iso
from email_utils import is_valid_email, send_via_resend
from walls import attach_walls

logger = logging.getLogger("signage")
logging.basicConfig(level=logging.INFO)


def transcode_video(input_path: str, media_id: int) -> None:
    """Re-encode uploaded video to streaming-optimized H.264.

    H.264 (libx264) hardware-decodes on every consumer device — minimal GPU/CPU
    load on signage clients. CRF 23 is visually lossless; +faststart puts the
    moov atom at the file head so the player starts immediately. Bitrate is
    capped at 12 Mbps to bound network use even on 4K source.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not installed — skipping transcode for media %s", media_id)
        return
    base, _ext = os.path.splitext(input_path)
    output_path = f"{base}.opt.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-maxrate", "12M",
        "-bufsize", "24M",
        "-movflags", "+faststart",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        logger.error("Transcode timed out for media %s", media_id)
        if os.path.exists(output_path):
            os.remove(output_path)
        return
    if result.returncode != 0:
        logger.error("ffmpeg failed for media %s: %s", media_id, result.stderr[-400:].decode(errors="ignore"))
        if os.path.exists(output_path):
            os.remove(output_path)
        return
    new_filename = os.path.basename(output_path)
    new_size = os.path.getsize(output_path)
    execute(
        "UPDATE media SET filename = ?, mime_type = ?, size = ? WHERE id = ?",
        (new_filename, "video/mp4", new_size, media_id),
    )
    if os.path.exists(input_path) and input_path != output_path:
        try:
            os.remove(input_path)
        except OSError:
            pass
    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)


UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
PREVIEW_TTL_SECONDS = int(os.getenv("PREVIEW_TTL_SECONDS", "300"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))

DOCS_ENABLED = os.getenv("DOCS_ENABLED", "0").lower() in ("1", "true", "yes")

app = FastAPI(
    title="Menu Signage Backend",
    version="",
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

_RATE_LIMITS_ENABLED = os.getenv("RATE_LIMITS_ENABLED", "1").lower() in ("1", "true", "yes")
limiter = Limiter(key_func=get_remote_address, enabled=_RATE_LIMITS_ENABLED)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self' https://api.khanshoof.com"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def parse_allowed_origins(value: str) -> list[str]:
    if not value or value.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

attach_walls(app)


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


PAIR_CODE_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIR_CODE_LENGTH = 6
PAIR_CODE_TTL_SECONDS = int(os.getenv("PAIR_CODE_TTL_SECONDS", "300"))


def generate_pair_code_v2() -> str:
    return "".join(secrets.choice(PAIR_CODE_CHARSET) for _ in range(PAIR_CODE_LENGTH))


def generate_unique_pair_code_v2() -> str:
    while True:
        code = generate_pair_code_v2()
        if not query_one("SELECT id FROM pairing_codes WHERE code = ?", (code,)):
            return code


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


def http_error(status: int, code: str, message: str) -> HTTPException:
    """Structured error response: detail = {code, message}.

    Frontend reads `code` to look up a localized string; falls back to
    `message` (English) if the code is unrecognized.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})


OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))
VERIFICATION_TOKEN_TTL_SECONDS = int(os.getenv("VERIFICATION_TOKEN_TTL_SECONDS", "900"))
DEV_MODE = os.getenv("DEV_MODE", "0").lower() in ("1", "true", "yes")


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", otp.encode(), salt, 120000)
    return f"{salt.hex()}${digest.hex()}"


def verify_otp(otp: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", otp.encode(), salt, 120000)
    return secrets.compare_digest(digest.hex(), digest_hex)


def _signup_otp_email_html(business_name: str, otp: str) -> str:
    return (
        f"<div style=\"font-family:system-ui,sans-serif;color:#2a2438;max-width:480px\">"
        f"<h1 style=\"font-size:18px;margin:0 0 16px\">Welcome to Khanshoof</h1>"
        f"<p style=\"margin:0 0 12px\">Hi {business_name}, here's your verification code:</p>"
        f"<p style=\"font-size:32px;letter-spacing:6px;font-weight:700;"
        f"background:#fef6e4;padding:16px 24px;border-radius:12px;display:inline-block;"
        f"margin:8px 0\">{otp}</p>"
        f"<p style=\"font-size:13px;color:#6b6480;margin:16px 0 0\">"
        f"This code expires in 10 minutes. If you didn't request it, ignore this email.</p>"
        f"</div>"
    )


def _signup_otp_email_text(business_name: str, otp: str) -> str:
    return (
        f"Hi {business_name},\n\n"
        f"Your Khanshoof verification code: {otp}\n\n"
        f"This code expires in 10 minutes. If you didn't request it, ignore this email.\n"
    )


def send_signup_otp_email(to_email: str, business_name: str, otp: str) -> None:
    """Send the signup OTP via Resend; fall back to logging if no API key.

    Failures are swallowed (logged but not raised) so a flaky email provider
    never 500s the signup endpoint — the OTP is still in the DB and the user
    can hit "resend".
    """
    logger.info("SIGNUP_OTP for %s (%s): %s", to_email, business_name, otp)
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return
    from_addr = os.getenv("RESEND_FROM", "Khanshoof <noreply@khanshoof.com>")
    try:
        send_via_resend(
            api_key=api_key,
            from_addr=from_addr,
            to=to_email,
            subject="Your Khanshoof verification code",
            html=_signup_otp_email_html(business_name, otp),
            text=_signup_otp_email_text(business_name, otp),
        )
    except Exception as exc:
        logger.error("Resend send failed for %s: %s", to_email, exc)


ROLE_LEVELS = {"viewer": 1, "editor": 2, "admin": 3}

PLANS = {
    "starter":    {"screen_limit": 3,    "price_kwd_monthly": 3,  "label": "Starter"},
    "growth":     {"screen_limit": 5,    "price_kwd_monthly": 4,  "label": "Growth"},
    "business":   {"screen_limit": 10,   "price_kwd_monthly": 8,  "label": "Business"},
    "pro":        {"screen_limit": 25,   "price_kwd_monthly": 15, "label": "Pro"},
    "enterprise": {"screen_limit": 9999, "price_kwd_monthly": 0,  "label": "Enterprise"},
}

KWD_TO_USD = Decimal("3.267")
PLAN_PRICING_KWD: dict[str, Decimal] = {
    "starter":  Decimal("3"),
    "growth":   Decimal("4"),
    "business": Decimal("8"),
    "pro":      Decimal("15"),
}
PLAN_SCREEN_LIMITS: dict[str, int] = {
    "starter": 3, "growth": 5, "business": 10, "pro": 25,
}
TERM_MULTIPLIERS: dict[int, int] = {1: 1, 6: 5, 12: 10}
ALLOWED_TIERS  = frozenset(PLAN_PRICING_KWD.keys())
ALLOWED_TERMS  = frozenset(TERM_MULTIPLIERS.keys())
TERM_DAYS      = 30

def _compute_amounts(tier: str, term_months: int) -> tuple[int, Decimal]:
    """Return (amount_kwd_int, amount_usd_display) for a tier/term combo."""
    monthly_kwd = PLAN_PRICING_KWD[tier]
    mult = TERM_MULTIPLIERS[term_months]
    amount_kwd_dec = monthly_kwd * mult
    amount_kwd = int(amount_kwd_dec)
    amount_usd = (amount_kwd_dec * KWD_TO_USD).quantize(Decimal("0.01"))
    return amount_kwd, amount_usd


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise http_error(400, "password_too_short", "Password must be at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        raise http_error(400, "password_no_letter", "Password must include a letter")
    if not re.search(r"\d", password):
        raise http_error(400, "password_no_number", "Password must include a number")


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
               users.role, users.organization_id
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
        "organization_id": session.get("organization_id"),
        "token": token,
    }


def org_id(user: dict) -> int:
    oid = user.get("organization_id")
    if not oid:
        raise HTTPException(status_code=403, detail="No organization for user")
    return int(oid)


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


def serialize_wall(wall: dict, include_cells: bool = True) -> dict:
    out = dict(wall)
    if include_cells:
        out["cells"] = query_all(
            "SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY row_index, col_index",
            (wall["id"],),
        )
    return out


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


class WallCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    mode: str = Field(..., pattern="^(spanned|mirrored)$")
    rows: int = Field(..., ge=1, le=8)
    cols: int = Field(..., ge=1, le=8)
    canvas_width_px: Optional[int] = Field(default=None, ge=320, le=32768)
    canvas_height_px: Optional[int] = Field(default=None, ge=240, le=32768)
    bezel_enabled: bool = False
    spanned_playlist_id: Optional[int] = None
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None


class WallUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None
    bezel_enabled: Optional[bool] = None
    canvas_width_px: Optional[int] = Field(default=None, ge=320, le=32768)
    canvas_height_px: Optional[int] = Field(default=None, ge=240, le=32768)


class WallCellUpdate(BaseModel):
    row_index: int
    col_index: int
    playlist_id: Optional[int] = None
    screen_size_inches: Optional[float] = Field(default=None, ge=5, le=120)
    bezel_top_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_right_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_bottom_mm: Optional[float] = Field(default=None, ge=0, le=200)
    bezel_left_mm: Optional[float] = Field(default=None, ge=0, le=200)


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


class SignupStartRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=200)


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
        org_row = query_one("SELECT id FROM organizations WHERE slug = ?", ("default",))
        if org_row:
            default_org_id = org_row["id"]
        else:
            default_org_id = execute(
                """
                INSERT INTO organizations
                (name, slug, plan, screen_limit, subscription_status, locale, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("Default", "default", "pro", 25, "active", "en", utc_now_iso()),
            )
        execute(
            """
            INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (default_org_id, admin_username, hash_password(admin_password), 1, "admin", utc_now_iso()),
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/plans")
def list_plans() -> dict:
    return {"plans": [{"key": key, **values} for key, values in PLANS.items()]}


def _is_local_request(request: Request) -> bool:
    """True only when the request did NOT come through a proxy/CDN.

    Production sits behind Cloudflare + nginx, both of which add forwarding
    headers. Their absence + a loopback client host means we're talking to
    localhost. Used to gate dev-only debug fields.
    """
    if request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip"):
        return False
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


@app.post("/auth/signup/request")
@limiter.limit("10/5minutes")
def signup_request(request: Request, payload: SignupStartRequest) -> dict:
    email = payload.email.strip().lower()
    business_name = payload.business_name.strip()
    if not is_valid_email(email):
        raise http_error(400, "invalid_email", "Invalid email address")
    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        raise http_error(400, "email_taken", "Email is already registered")

    now = datetime.now(timezone.utc)
    existing = query_one("SELECT last_sent_at FROM pending_signups WHERE email = ?", (email,))
    if existing and existing.get("last_sent_at"):
        try:
            last_sent_dt = datetime.fromisoformat(existing["last_sent_at"])
            if (now - last_sent_dt).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
                raise http_error(429, "otp_cooldown", f"Please wait {OTP_RESEND_COOLDOWN_SECONDS} seconds before requesting another code.")
        except ValueError:
            pass

    otp = generate_otp()
    otp_hash_val = hash_otp(otp)
    expires_at = (now + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    now_iso = now.isoformat()

    if existing:
        execute(
            """
            UPDATE pending_signups
               SET business_name = ?, otp_hash = ?, attempts = 0,
                   expires_at = ?, last_sent_at = ?,
                   verification_token = NULL, verification_token_expires_at = NULL
             WHERE email = ?
            """,
            (business_name, otp_hash_val, expires_at, now_iso, email),
        )
    else:
        execute(
            """
            INSERT INTO pending_signups
              (email, business_name, otp_hash, attempts, expires_at, last_sent_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (email, business_name, otp_hash_val, 0, expires_at, now_iso, now_iso),
        )

    send_signup_otp_email(email, business_name, otp)
    response: dict = {"status": "otp_sent", "expires_in_seconds": OTP_TTL_SECONDS}
    if DEV_MODE and _is_local_request(request):
        response["dev_otp"] = otp
    return response


class SignupVerifyRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    otp: str = Field(..., min_length=6, max_length=6)


@app.post("/auth/signup/verify")
def signup_verify(payload: SignupVerifyRequest) -> dict:
    email = payload.email.strip().lower()
    row = query_one("SELECT * FROM pending_signups WHERE email = ?", (email,))
    if not row:
        raise http_error(400, "no_pending_signup", "No pending signup for this email")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise http_error(400, "otp_expired", "Code expired. Please request a new one.")

    if (row.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        raise http_error(400, "otp_attempts_exceeded", "Too many incorrect attempts. Request a new code.")

    if not verify_otp(payload.otp, row.get("otp_hash")):
        execute(
            "UPDATE pending_signups SET attempts = attempts + 1 WHERE email = ?",
            (email,),
        )
        raise http_error(400, "otp_incorrect", "Incorrect code")

    verification_token = secrets.token_hex(16)
    verification_expires = (now + timedelta(seconds=VERIFICATION_TOKEN_TTL_SECONDS)).isoformat()
    execute(
        """
        UPDATE pending_signups
           SET verification_token = ?, verification_token_expires_at = ?, attempts = 0
         WHERE email = ?
        """,
        (verification_token, verification_expires, email),
    )
    return {
        "verification_token": verification_token,
        "business_name": row["business_name"],
        "expires_in_seconds": VERIFICATION_TOKEN_TTL_SECONDS,
    }


class SignupCompleteRequest(BaseModel):
    verification_token: str = Field(..., min_length=32, max_length=64)
    password: str = Field(..., min_length=1)


@app.post("/auth/signup/complete")
def signup_complete(payload: SignupCompleteRequest) -> dict:
    validate_password(payload.password)
    row = query_one(
        "SELECT * FROM pending_signups WHERE verification_token = ?",
        (payload.verification_token,),
    )
    if not row:
        raise http_error(400, "invalid_verification_token", "Invalid or expired verification token")

    now = datetime.now(timezone.utc)
    try:
        vt_expires_dt = datetime.fromisoformat(row["verification_token_expires_at"])
    except (TypeError, ValueError):
        vt_expires_dt = now - timedelta(seconds=1)
    if now > vt_expires_dt:
        raise http_error(400, "verification_token_expired", "Verification token expired. Please restart signup.")

    email = row["email"]
    business_name = row["business_name"]

    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        execute("DELETE FROM pending_signups WHERE email = ?", (email,))
        raise http_error(400, "email_taken", "Email is already registered")

    slug_base = slugify(business_name)
    slug = slug_base
    counter = 1
    while query_one("SELECT id FROM organizations WHERE slug = ?", (slug,)):
        counter += 1
        slug = f"{slug_base}-{counter}"

    plan_key = "starter"
    plan = PLANS[plan_key]
    trial_ends_at = (now + timedelta(days=5)).isoformat()

    new_org_id = execute(
        """
        INSERT INTO organizations
        (name, slug, plan, screen_limit, subscription_status, trial_ends_at, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (business_name, slug, plan_key, plan["screen_limit"],
         "trialing", trial_ends_at, "en", utc_now_iso()),
    )
    user_id = execute(
        """
        INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_org_id, email, hash_password(payload.password), 1, "admin", utc_now_iso()),
    )
    session_token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user_id, session_token, utc_now_iso(), utc_now_iso()),
    )
    execute("DELETE FROM pending_signups WHERE email = ?", (email,))

    return {
        "token": session_token,
        "user": {
            "id": user_id,
            "username": email,
            "role": "admin",
            "is_admin": True,
        },
        "organization": {
            "id": new_org_id,
            "name": business_name,
            "slug": slug,
            "plan": plan_key,
            "screen_limit": plan["screen_limit"],
            "subscription_status": "trialing",
            "trial_ends_at": trial_ends_at,
            "locale": "en",
        },
    }


@app.get("/organization")
def get_organization(user: dict = Depends(get_current_user)) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    screens_count = query_one(
        "SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?",
        (org["id"],),
    )
    org["screens_used"] = int(screens_count["n"] if screens_count else 0)
    return org


class OrganizationLocaleUpdate(BaseModel):
    locale: str = Field(..., min_length=2, max_length=2)


@app.patch("/organizations/me")
def patch_organization_me(
    payload: OrganizationLocaleUpdate,
    user: dict = Depends(require_roles("admin")),
) -> dict:
    if payload.locale not in ("en", "ar"):
        raise http_error(400, "invalid_locale", "Locale must be 'en' or 'ar'")
    execute(
        "UPDATE organizations SET locale = ? WHERE id = ?",
        (payload.locale, org_id(user)),
    )
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org_id(user),))
    return org


@app.post("/auth/login")
@limiter.limit("10/5minutes")
def login(request: Request, payload: LoginRequest) -> dict:
    user = query_one("SELECT * FROM users WHERE username = ?", (payload.username,))
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise http_error(401, "invalid_credentials", "Invalid credentials")
    cleanup_sessions()
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user["id"], token, utc_now_iso(), utc_now_iso()),
    )
    org = query_one("SELECT * FROM organizations WHERE id = ?", (user["organization_id"],))
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role") or ("admin" if user["is_admin"] else "viewer"),
            "is_admin": bool(user["is_admin"]),
        },
        "organization": {
            "id": org["id"],
            "name": org["name"],
            "slug": org["slug"],
            "plan": org["plan"],
            "screen_limit": org["screen_limit"],
            "subscription_status": org["subscription_status"],
            "trial_ends_at": org["trial_ends_at"],
            "locale": org["locale"],
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
        raise http_error(401, "invalid_current_password", "Invalid current password")
    validate_password(payload.new_password)
    execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(payload.new_password), user["id"]),
    )
    execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    return {"status": "password_changed"}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (user["organization_id"],))
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["is_admin"],
        "organization": {
            "id": org["id"],
            "name": org["name"],
            "slug": org["slug"],
            "plan": org["plan"],
            "screen_limit": org["screen_limit"],
            "subscription_status": org["subscription_status"],
            "trial_ends_at": org["trial_ends_at"],
            "locale": org["locale"],
        },
    }


@app.get("/users")
def list_users(user: dict = Depends(require_roles("admin"))) -> list[dict]:
    rows = query_all(
        "SELECT id, username, is_admin, role, created_at FROM users WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )
    for row in rows:
        row["is_admin"] = bool(row["is_admin"])
        row["role"] = row.get("role") or ("admin" if row["is_admin"] else "viewer")
    return rows


@app.post("/users")
def create_user(payload: UserCreate, user: dict = Depends(require_roles("admin"))) -> dict:
    if query_one("SELECT id FROM users WHERE username = ?", (payload.username,)):
        raise http_error(400, "username_taken", "Username already exists")
    if payload.role not in ROLE_LEVELS:
        raise http_error(400, "invalid_role", "Invalid role")
    validate_password(payload.password)
    user_id = execute(
        "INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            org_id(user),
            payload.username,
            hash_password(payload.password),
            int(payload.role == "admin"),
            payload.role,
            utc_now_iso(),
        ),
    )
    created = query_one(
        "SELECT id, username, is_admin, role, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    created["is_admin"] = bool(created["is_admin"])
    created["role"] = created.get("role") or ("admin" if created["is_admin"] else "viewer")
    return created


@app.put("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, user: dict = Depends(require_roles("admin"))) -> dict:
    target = query_one(
        "SELECT * FROM users WHERE id = ? AND organization_id = ?",
        (user_id, org_id(user)),
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.password:
        validate_password(payload.password)
        execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
    if payload.role is not None:
        if payload.role not in ROLE_LEVELS:
            raise http_error(400, "invalid_role", "Invalid role")
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
def delete_user(user_id: int, user: dict = Depends(require_roles("admin"))) -> dict:
    target = query_one(
        "SELECT * FROM users WHERE id = ? AND organization_id = ?",
        (user_id, org_id(user)),
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return {"status": "deleted"}


@app.get("/sites")
def list_sites(user: dict = Depends(get_current_user)) -> list[dict]:
    return query_all(
        "SELECT * FROM sites WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )


@app.post("/sites")
def create_site(payload: SiteCreate, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    oid = org_id(user)
    slug = slugify(payload.slug or payload.name)
    base_slug = slug
    counter = 1
    while query_one(
        "SELECT id FROM sites WHERE slug = ? AND organization_id = ?",
        (slug, oid),
    ):
        counter += 1
        slug = f"{base_slug}-{counter}"
    site_id = execute(
        "INSERT INTO sites (organization_id, name, slug, created_at) VALUES (?, ?, ?, ?)",
        (oid, payload.name, slug, utc_now_iso()),
    )
    return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))


@app.put("/sites/{site_id}")
def update_site(
    site_id: int, payload: SiteUpdate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    oid = org_id(user)
    site = query_one(
        "SELECT * FROM sites WHERE id = ? AND organization_id = ?",
        (site_id, oid),
    )
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    name = payload.name or site["name"]
    slug = slugify(payload.slug or site["slug"])
    if slug != site["slug"] and query_one(
        "SELECT id FROM sites WHERE slug = ? AND id != ? AND organization_id = ?",
        (slug, site_id, oid),
    ):
        raise HTTPException(status_code=400, detail="Slug already exists")

    execute(
        "UPDATE sites SET name = ?, slug = ? WHERE id = ?",
        (name, slug, site_id),
    )
    return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))


@app.delete("/sites/{site_id}")
def delete_site(site_id: int, user: dict = Depends(require_roles("admin"))) -> dict:
    site = query_one(
        "SELECT * FROM sites WHERE id = ? AND organization_id = ?",
        (site_id, org_id(user)),
    )
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    execute("UPDATE screens SET site_id = NULL WHERE site_id = ?", (site_id,))
    execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return {"status": "deleted"}


@app.get("/screens")
def list_screens(user: dict = Depends(get_current_user)) -> list[dict]:
    oid = org_id(user)
    if user.get("is_admin"):
        rows = query_all(
            """
            SELECT screens.*, sites.name AS site_name, playlists.name AS playlist_name
            FROM screens
            LEFT JOIN sites ON sites.id = screens.site_id
            LEFT JOIN playlists ON playlists.id = screens.playlist_id
            WHERE screens.organization_id = ?
            ORDER BY screens.created_at DESC
            """,
            (oid,),
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
            WHERE screens.organization_id = ?
              AND (screens.owner_user_id = ? OR user_groups.user_id = ?)
            ORDER BY screens.created_at DESC
            """,
            (oid, user["id"], user["id"]),
        )
    return [sanitize_screen(row, include_token=False) for row in rows]


@app.post("/screens")
def create_screen(payload: ScreenCreate, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    oid = org_id(user)
    org = query_one(
        "SELECT screen_limit FROM organizations WHERE id = ?", (oid,)
    )
    if org:
        count_row = query_one(
            "SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?", (oid,)
        )
        current = int(count_row["n"] if count_row else 0)
        limit = int(org["screen_limit"])
        if current >= limit:
            raise http_error(
                402,
                "plan_limit",
                f"Screen limit reached ({current}/{limit}). Upgrade your plan to add more.",
            )
    pair_code = generate_unique_pair_code()
    token = generate_unique_token()
    screen_id = execute(
        """
        INSERT INTO screens (
            organization_id, name, location, resolution, orientation, site_id, owner_user_id,
            pair_code, token, password_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            oid,
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
    return sanitize_screen(screen, include_token=True)


@app.put("/screens/{screen_id}")
def update_screen(
    screen_id: int, payload: ScreenUpdate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
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
def delete_screen(screen_id: int, user: dict = Depends(require_roles("admin"))) -> dict:
    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute("DELETE FROM screens WHERE id = ?", (screen_id,))
    return {"status": "deleted"}


@app.get("/screens/{screen_id}/zones")
def list_screen_zones(
    screen_id: int, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
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
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
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
def list_zone_templates(
    site_id: Optional[int] = None,
    user: dict = Depends(require_roles("admin", "editor")),
) -> list[dict]:
    oid = org_id(user)
    if site_id is not None:
        return query_all(
            "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates WHERE site_id = ? AND organization_id = ? ORDER BY created_at DESC",
            (site_id, oid),
        )
    return query_all(
        "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates WHERE organization_id = ? ORDER BY created_at DESC",
        (oid,),
    )


@app.post("/zone-templates")
def create_zone_template(
    payload: ZoneTemplateCreate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    layout_json = json.dumps([zone.dict() for zone in payload.zones])
    template_id = execute(
        """
        INSERT INTO screen_zone_templates (organization_id, site_id, name, layout_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (org_id(user), payload.site_id, payload.name, layout_json, utc_now_iso()),
    )
    return query_one(
        "SELECT id, site_id, name, layout_json, created_at FROM screen_zone_templates WHERE id = ?",
        (template_id,),
    )


@app.post("/screens/{screen_id}/zone-templates/apply")
def apply_zone_template(
    screen_id: int,
    payload: ZoneTemplateApply,
    user: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    oid = org_id(user)
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, oid),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    template = query_one(
        "SELECT layout_json FROM screen_zone_templates WHERE id = ? AND organization_id = ?",
        (payload.template_id, oid),
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        zones = json.loads(template["layout_json"])
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Template data invalid")
    return update_screen_zones(screen_id, ScreenZonesPayload(zones=zones), user=user)


@app.get("/groups")
def list_groups(user: dict = Depends(require_roles("admin"))) -> list[dict]:
    return query_all(
        "SELECT id, name, created_at FROM groups WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )


@app.post("/groups")
def create_group(payload: GroupCreate, user: dict = Depends(require_roles("admin"))) -> dict:
    group_id = execute(
        "INSERT INTO groups (organization_id, name, created_at) VALUES (?, ?, ?)",
        (org_id(user), payload.name, utc_now_iso()),
    )
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.put("/groups/{group_id}")
def update_group(group_id: int, payload: GroupUpdate, user: dict = Depends(require_roles("admin"))) -> dict:
    group = query_one(
        "SELECT id FROM groups WHERE id = ? AND organization_id = ?",
        (group_id, org_id(user)),
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    execute("UPDATE groups SET name = ? WHERE id = ?", (payload.name, group_id))
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, user: dict = Depends(require_roles("admin"))) -> dict:
    group = query_one(
        "SELECT id FROM groups WHERE id = ? AND organization_id = ?",
        (group_id, org_id(user)),
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,))
    execute("DELETE FROM screen_groups WHERE group_id = ?", (group_id,))
    execute("DELETE FROM groups WHERE id = ?", (group_id,))
    return {"status": "deleted"}


@app.put("/users/{user_id}/groups")
def update_user_groups(
    user_id: int, payload: UserGroupsPayload, user: dict = Depends(require_roles("admin"))
) -> dict:
    oid = org_id(user)
    target = query_one(
        "SELECT id FROM users WHERE id = ? AND organization_id = ?",
        (user_id, oid),
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
    for group_id in payload.group_ids:
        group_check = query_one(
            "SELECT id FROM groups WHERE id = ? AND organization_id = ?",
            (group_id, oid),
        )
        if not group_check:
            continue
        execute(
            "INSERT INTO user_groups (user_id, group_id, created_at) VALUES (?, ?, ?) ON CONFLICT (user_id, group_id) DO NOTHING",
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
    user_id: int, user: dict = Depends(require_roles("admin"))
) -> dict:
    target = query_one(
        "SELECT id FROM users WHERE id = ? AND organization_id = ?",
        (user_id, org_id(user)),
    )
    if not target:
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
    screen_id: int, payload: ScreenGroupsPayload, user: dict = Depends(require_roles("admin"))
) -> dict:
    oid = org_id(user)
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, oid),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute("DELETE FROM screen_groups WHERE screen_id = ?", (screen_id,))
    for group_id in payload.group_ids:
        group_check = query_one(
            "SELECT id FROM groups WHERE id = ? AND organization_id = ?",
            (group_id, oid),
        )
        if not group_check:
            continue
        execute(
            "INSERT INTO screen_groups (screen_id, group_id, created_at) VALUES (?, ?, ?) ON CONFLICT (screen_id, group_id) DO NOTHING",
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
    screen_id: int, user: dict = Depends(require_roles("admin"))
) -> dict:
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
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


class PairRequestStart(BaseModel):
    user_agent: str | None = Field(default=None, max_length=500)


@app.post("/screens/request_code")
@limiter.limit("10/minute")
def request_pair_code(request: Request, payload: PairRequestStart | None = None) -> dict:
    now = datetime.now(timezone.utc)
    code = generate_unique_pair_code_v2()
    device_id = secrets.token_hex(16)
    expires_at = (now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)).isoformat()
    execute(
        """
        INSERT INTO pairing_codes (code, device_id, status, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (code, device_id, "pending", expires_at, now.isoformat()),
    )
    return {
        "code": code,
        "device_id": device_id,
        "expires_at": expires_at,
        "expires_in_seconds": PAIR_CODE_TTL_SECONDS,
    }


@app.get("/screens/poll/{code}")
@limiter.limit("30/minute")
def poll_pair_code(request: Request, code: str) -> dict:
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (code,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown pairing code")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)

    if row["status"] == "pending" and now > expires_dt:
        return {"status": "expired"}

    if row["status"] == "pending":
        return {"status": "pending"}

    if row["status"] == "paired":
        screen = query_one("SELECT * FROM screens WHERE id = ?", (row["screen_id"],))
        if not screen:
            return {"status": "expired"}
        execute(
            "UPDATE screens SET last_seen = ? WHERE id = ?",
            (now.isoformat(), screen["id"]),
        )
        return {
            "status": "paired",
            "screen_id": screen["id"],
            "screen_name": screen["name"],
            "screen_token": screen["token"],
        }

    return {"status": row["status"]}


class PairClaimRequest(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)
    screen_id: int


@app.post("/screens/claim")
def claim_pair_code(
    payload: PairClaimRequest,
    user: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (payload.code,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown pairing code")

    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise HTTPException(status_code=400, detail="Pairing code expired. Ask the display to refresh.")

    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (payload.screen_id, user["organization_id"]),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    if row["status"] == "paired" and row.get("screen_id") and row["screen_id"] != screen["id"]:
        raise HTTPException(status_code=400, detail="This pairing code is already bound to another display.")

    execute(
        """
        UPDATE pairing_codes
           SET status = 'paired', screen_id = ?, claimed_at = ?
         WHERE code = ?
           AND (status = 'pending' OR (status = 'paired' AND screen_id = ?))
        """,
        (screen["id"], now.isoformat(), payload.code, screen["id"]),
    )
    after = query_one("SELECT screen_id FROM pairing_codes WHERE code = ?", (payload.code,))
    if not after or after["screen_id"] != screen["id"]:
        raise HTTPException(status_code=409, detail="This pairing code was just claimed by another display.")
    return {"screen_id": screen["id"], "screen_name": screen["name"]}


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


# -------------------- Walls --------------------

@app.post("/walls", status_code=201)
def create_wall(payload: WallCreate, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    if payload.mode == "mirrored" and not payload.mirrored_mode:
        raise http_error(422, "wall.mirrored_mode_required",
                         "Mirrored walls need a sub-mode (same_playlist or synced_rotation).")
    if payload.mode == "mirrored" and payload.mirrored_mode == "same_playlist" and not payload.mirrored_playlist_id:
        raise http_error(422, "wall.mirrored_playlist_required",
                         "Same-playlist mirrored walls need a playlist.")
    if payload.mirrored_playlist_id is not None:
        own = query_one("SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
                        (payload.mirrored_playlist_id, org_id(user)))
        if not own:
            raise http_error(404, "playlist.not_found", "Playlist not found")
    now = utc_now_iso()
    wall_id = execute(
        """INSERT INTO walls (organization_id, name, mode, rows, cols,
               canvas_width_px, canvas_height_px, bezel_enabled,
               spanned_playlist_id, mirrored_mode, mirrored_playlist_id,
               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (org_id(user), payload.name, payload.mode, payload.rows, payload.cols,
         payload.canvas_width_px, payload.canvas_height_px, payload.bezel_enabled,
         payload.spanned_playlist_id, payload.mirrored_mode, payload.mirrored_playlist_id,
         now, now),
    )
    for r in range(payload.rows):
        for c in range(payload.cols):
            execute(
                "INSERT INTO wall_cells (wall_id, row_index, col_index, created_at) VALUES (?, ?, ?, ?)",
                (wall_id, r, c, now),
            )
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    return serialize_wall(wall)


@app.get("/walls")
def list_walls(user: dict = Depends(get_current_user)) -> list[dict]:
    walls = query_all(
        "SELECT * FROM walls WHERE organization_id = ? ORDER BY id DESC",
        (org_id(user),),
    )
    return [serialize_wall(w) for w in walls]


@app.get("/walls/{wall_id}")
def get_wall(wall_id: int, user: dict = Depends(get_current_user)) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    return serialize_wall(wall)


@app.patch("/walls/{wall_id}")
def patch_wall(wall_id: int, payload: WallUpdate,
               user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return serialize_wall(wall)
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [utc_now_iso(), wall_id]
    execute(f"UPDATE walls SET {sets}, updated_at = ? WHERE id = ?", tuple(params))
    return serialize_wall(query_one("SELECT * FROM walls WHERE id = ?", (wall_id,)))


@app.patch("/walls/{wall_id}/cells")
def patch_wall_cell(wall_id: int, payload: WallCellUpdate,
                    user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    fields = payload.model_dump(exclude_unset=True)
    if payload.playlist_id is not None:
        own = query_one("SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
                        (payload.playlist_id, org_id(user)))
        if not own:
            raise http_error(404, "playlist.not_found", "Playlist not found")
    set_fields = {k: v for k, v in fields.items() if k not in ("row_index", "col_index")}
    if set_fields:
        sets = ", ".join(f"{k} = ?" for k in set_fields.keys())
        params = list(set_fields.values()) + [wall_id, payload.row_index, payload.col_index]
        execute(
            f"UPDATE wall_cells SET {sets} "
            f"WHERE wall_id = ? AND row_index = ? AND col_index = ?",
            tuple(params),
        )
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, payload.row_index, payload.col_index),
    )
    return cell


@app.delete("/walls/{wall_id}", status_code=204)
def delete_wall(wall_id: int, user: dict = Depends(require_roles("admin", "editor"))) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    execute("DELETE FROM walls WHERE id = ?", (wall_id,))
    return None


class WallRedeemRequest(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


def _generate_unique_wall_pair_code() -> str:
    while True:
        code = generate_pair_code_v2()
        if not query_one("SELECT id FROM wall_pairing_codes WHERE code = ?", (code,)):
            return code


@app.post("/walls/{wall_id}/cells/{row}/{col}/pair")
def pair_wall_cell(wall_id: int, row: int, col: int,
                   user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    now = datetime.now(timezone.utc)
    code = _generate_unique_wall_pair_code()
    expires_at = (now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)).isoformat()
    execute(
        """INSERT INTO wall_pairing_codes (code, wall_id, row_index, col_index,
               status, expires_at, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (code, wall_id, row, col, expires_at, now.isoformat()),
    )
    return {"code": code, "expires_at": expires_at,
            "expires_in_seconds": PAIR_CODE_TTL_SECONDS}


@app.post("/walls/cells/redeem")
@limiter.limit("30/minute")
def redeem_wall_cell(request: Request, payload: WallRedeemRequest) -> dict:
    row = query_one("SELECT * FROM wall_pairing_codes WHERE code = ?", (payload.code,))
    if not row:
        raise http_error(404, "wall.pair_code_unknown", "Code not recognized")
    now = datetime.now(timezone.utc)
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if row["status"] == "claimed":
        raise http_error(409, "wall.pair_code_used", "This code was already used")
    if now > expires_dt:
        execute("UPDATE wall_pairing_codes SET status = 'expired' WHERE id = ?", (row["id"],))
        raise http_error(410, "wall.pair_code_expired", "Code expired")

    wall = query_one("SELECT * FROM walls WHERE id = ?", (row["wall_id"],))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall is gone")

    name = f"Wall {wall['name']} ({row['row_index']},{row['col_index']})"
    pair_code = generate_unique_pair_code()
    screen_token = generate_unique_token()
    screen_id = execute(
        """INSERT INTO screens (organization_id, name, pair_code, token, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (wall["organization_id"], name, pair_code, screen_token, now.isoformat()),
    )
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (row["wall_id"], row["row_index"], row["col_index"]),
    )
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (screen_id, cell["id"]))
    execute("UPDATE screens SET wall_cell_id = ? WHERE id = ?", (cell["id"], screen_id))
    execute(
        "UPDATE wall_pairing_codes SET status = 'claimed', claimed_at = ? WHERE id = ?",
        (now.isoformat(), row["id"]),
    )
    return {
        "status": "paired",
        "screen_token": screen_token,
        "wall_id": wall["id"],
        "cell": {"row": row["row_index"], "col": row["col_index"],
                 "rows": wall["rows"], "cols": wall["cols"]},
        "mode": wall["mode"],
    }


@app.delete("/walls/{wall_id}/cells/{row}/{col}/pairing", status_code=204)
def unpair_wall_cell(wall_id: int, row: int, col: int,
                     user: dict = Depends(require_roles("admin", "editor"))) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    cell = query_one(
        "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    if cell["screen_id"]:
        execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (cell["screen_id"],))
    execute("UPDATE wall_cells SET screen_id = NULL WHERE id = ?", (cell["id"],))
    try:
        from walls import broadcast_bye  # type: ignore
        broadcast_bye(wall_id, row, col, "cell_unpaired")
    except Exception:
        pass
    return None


@app.post("/screens/{screen_id}/preview-token")
def create_preview_token(
    screen_id: int, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
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
def list_playlists(user: dict = Depends(get_current_user)) -> list[dict]:
    return query_all(
        "SELECT * FROM playlists WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )


@app.post("/playlists")
def create_playlist(
    payload: PlaylistCreate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    playlist_id = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (org_id(user), payload.name, utc_now_iso()),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.get("/playlists/{playlist_id}")
def get_playlist(playlist_id: int, user: dict = Depends(get_current_user)) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, org_id(user)),
    )
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
    user: dict = Depends(require_roles("admin", "editor")),
) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, org_id(user)),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute(
        "UPDATE playlists SET name = ? WHERE id = ?",
        (payload.name or playlist["name"], playlist_id),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: int, user: dict = Depends(require_roles("admin"))) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, org_id(user)),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    execute("UPDATE screens SET playlist_id = NULL WHERE playlist_id = ?", (playlist_id,))
    return {"status": "deleted"}


@app.post("/playlists/{playlist_id}/items")
def add_playlist_item(
    playlist_id: int, payload: PlaylistItemCreate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    oid = org_id(user)
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, oid),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    media = query_one(
        "SELECT * FROM media WHERE id = ? AND organization_id = ?",
        (payload.media_id, oid),
    )
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
    playlist_id: int, item_id: int, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    playlist = query_one(
        "SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, org_id(user)),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    item = query_one(
        "SELECT * FROM playlist_items WHERE id = ? AND playlist_id = ?",
        (item_id, playlist_id),
    )
    if not item:
        raise HTTPException(status_code=404, detail="Playlist item not found")
    execute("DELETE FROM playlist_items WHERE id = ?", (item_id,))
    return {"status": "deleted"}


@app.get("/media")
def list_media(user: dict = Depends(get_current_user)) -> list[dict]:
    media = query_all(
        "SELECT * FROM media WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id(user),),
    )
    for item in media:
        if item.get("mime_type") == "text/url":
            item["url"] = item["filename"]
        else:
            item["url"] = f"/uploads/{item['filename']}"
    return media


@app.post("/media/upload")
async def upload_media(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(require_roles("admin", "editor")),
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

    content_type = file.content_type or "application/octet-stream"
    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            org_id(user),
            file.filename or filename,
            filename,
            content_type,
            len(contents),
            utc_now_iso(),
        ),
    )
    if content_type.startswith("video"):
        background_tasks.add_task(transcode_video, file_path, media_id)
    item = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    item["url"] = f"/uploads/{item['filename']}"
    return item


@app.post("/media/url")
def create_media_url(
    payload: MediaUrlCreate, user: dict = Depends(require_roles("admin", "editor"))
) -> dict:
    url = payload.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (org_id(user), payload.name, url, "text/url", 0, utc_now_iso()),
    )
    item = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    item["url"] = item["filename"]
    return item


@app.delete("/media/{media_id}")
def delete_media(media_id: int, user: dict = Depends(require_roles("admin", "editor"))) -> dict:
    media = query_one(
        "SELECT * FROM media WHERE id = ? AND organization_id = ?",
        (media_id, org_id(user)),
    )
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    file_path = os.path.join(UPLOAD_DIR, media["filename"])
    if os.path.exists(file_path):
        os.remove(file_path)
    execute("DELETE FROM media WHERE id = ?", (media_id,))
    execute("DELETE FROM playlist_items WHERE media_id = ?", (media_id,))
    return {"status": "deleted"}


# ── Billing ──────────────────────────────────────────────────────────

class BillingCheckoutRequest(BaseModel):
    tier: str = Field(..., description="starter|growth|business|pro")
    term_months: int = Field(..., description="1, 6, or 12")


def _billing_callback_base() -> tuple[str, str]:
    api_base = os.environ.get("API_BASE_URL", "https://api.khanshoof.com").rstrip("/")
    app_base = os.environ.get("APP_URL",      "https://app.khanshoof.com").rstrip("/")
    return api_base, app_base


def _billing_callback_secret() -> str:
    secret = os.environ.get("NIUPAY_CALLBACK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="Billing not configured")
    return secret


def _verify_billing_callback(raw_body: bytes, headers, query_secret: str) -> bool:
    """Two-path auth: HMAC body signature (preferred) or query-string shared secret.

    Fails closed if neither verifies. Constant-time compares throughout.
    """
    webhook_secret = os.environ.get("BILLING_WEBHOOK_SECRET", "").strip()
    sig_header = (headers.get("x-niupay-signature") or headers.get("x-webhook-signature") or "").strip()
    if webhook_secret and sig_header:
        expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig_header):
            return True
    callback_secret = os.environ.get("NIUPAY_CALLBACK_SECRET", "")
    if callback_secret and query_secret:
        if secrets.compare_digest(query_secret, callback_secret):
            return True
    return False


@app.post("/billing/checkout")
def billing_checkout(
    payload: BillingCheckoutRequest,
    user: dict = Depends(require_roles("admin")),
) -> dict:
    if payload.tier not in ALLOWED_TIERS:
        raise HTTPException(status_code=422, detail="Unknown tier")
    if payload.term_months not in ALLOWED_TERMS:
        raise HTTPException(status_code=422, detail="Unknown term")

    amount_kwd, amount_usd = _compute_amounts(payload.tier, payload.term_months)
    org = org_id(user)

    # Rate-limit: reuse pending row < 60 s old
    existing = query_one(
        """
        SELECT trackid, niupay_payment_link FROM payments
         WHERE organization_id = ?
           AND tier            = ?
           AND term_months     = ?
           AND status          = 'pending'
           AND created_at      > now() - interval '60 seconds'
           AND niupay_payment_link IS NOT NULL
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (org, payload.tier, payload.term_months),
    )
    if existing:
        return {
            "payment_url": existing["niupay_payment_link"],
            "trackid": existing["trackid"],
        }

    trackid = "pay_" + secrets.token_hex(16)
    api_base, app_base = _billing_callback_base()
    secret = _billing_callback_secret()
    response_url = f"{api_base}/billing/callback/{trackid}?s={secret}"
    success_url  = f"{app_base}/billing?status=success&trackid={trackid}"
    error_url    = f"{app_base}/billing?status=error&trackid={trackid}"

    # Insert pending row FIRST so the callback can find it even if the request races
    execute(
        """
        INSERT INTO payments
          (organization_id, user_id, trackid, tier, term_months,
           amount_kwd, amount_usd_display, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (org, user["id"], trackid, payload.tier, payload.term_months,
         amount_kwd, str(amount_usd)),
    )

    try:
        resp = create_knet_request(
            trackid=trackid,
            amount_kwd=amount_kwd,
            response_url=response_url,
            success_url=success_url,
            error_url=error_url,
        )
    except Exception as exc:
        execute("UPDATE payments SET status='failed', niupay_result=? WHERE trackid=?",
                (f"request_error:{exc.__class__.__name__}", trackid))
        raise HTTPException(status_code=502, detail="Payment gateway unreachable")

    payment_link = resp.get("paymentLink")
    payment_id   = resp.get("paymentID")
    if not resp.get("status") or not payment_link:
        execute("UPDATE payments SET status='failed', niupay_result=? WHERE trackid=?",
                ("niupay_bad_response", trackid))
        raise HTTPException(status_code=502, detail="Payment gateway rejected the request")

    execute(
        "UPDATE payments SET niupay_payment_id=?, niupay_payment_link=?, updated_at=now() WHERE trackid=?",
        (payment_id, payment_link, trackid),
    )
    return {"payment_url": payment_link, "trackid": trackid}


class BillingCallbackBody(BaseModel):
    result: str | None = None
    trackid: str | None = None
    paymentID: str | None = None
    tranid: str | None = None
    ref: str | None = None
    niutrack: str | None = None


@app.post("/billing/callback/{trackid}")
async def billing_callback(request: Request, trackid: str, s: str = ""):
    raw_body = await request.body()
    if not _verify_billing_callback(raw_body, request.headers, s):
        raise HTTPException(status_code=404)
    try:
        body_dict = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    body = BillingCallbackBody(**body_dict)

    if body.trackid and body.trackid != trackid:
        raise HTTPException(status_code=400, detail="trackid mismatch")

    row = query_one("SELECT * FROM payments WHERE trackid = ?", (trackid,))
    if not row:
        return {"ok": True}   # no-leak 200

    if row["status"] in ("captured", "failed"):
        return {"ok": True}   # idempotent

    captured = (body.result or "").upper() == "CAPTURED"
    if captured:
        term = int(row["term_months"])
        execute(
            """
            UPDATE payments
               SET status='captured',
                   niupay_result=?, niupay_tranid=?, niupay_ref=?, niupay_payment_id=?,
                   updated_at=now()
             WHERE trackid=?
            """,
            (body.result, body.tranid, body.ref, body.paymentID, trackid),
        )
        execute(
            """
            UPDATE organizations
               SET plan               = ?,
                   plan_term_months   = ?,
                   screen_limit       = ?,
                   subscription_status= 'active',
                   paid_through_at    = now() + make_interval(days => ?)
             WHERE id = ?
            """,
            (row["tier"], term, PLAN_SCREEN_LIMITS[row["tier"]], term * TERM_DAYS, row["organization_id"]),
        )
    else:
        execute(
            """
            UPDATE payments
               SET status='failed',
                   niupay_result=?, niupay_tranid=?, niupay_ref=?, niupay_payment_id=?,
                   updated_at=now()
             WHERE trackid=?
            """,
            (body.result, body.tranid, body.ref, body.paymentID, trackid),
        )

    return {"ok": True}


@app.get("/billing/status/{trackid}")
def billing_status(trackid: str, user: dict = Depends(get_current_user)) -> dict:
    row = query_one("SELECT * FROM payments WHERE trackid = ?", (trackid,))
    if not row or row["organization_id"] != org_id(user):
        raise HTTPException(status_code=404, detail="Unknown trackid")
    org = query_one("SELECT paid_through_at FROM organizations WHERE id = ?", (row["organization_id"],))
    return {
        "status":               row["status"],
        "tier":                 row["tier"],
        "term_months":          row["term_months"],
        "amount_kwd":           row["amount_kwd"],
        "amount_usd_display":   str(row["amount_usd_display"]),
        "paid_through_at":      org["paid_through_at"].isoformat() if org and org.get("paid_through_at") else None,
    }


@app.get("/billing/history")
def billing_history(user: dict = Depends(require_roles("admin"))) -> list[dict]:
    rows = query_all(
        """
        SELECT trackid, tier, term_months, amount_kwd, amount_usd_display,
               status, created_at, updated_at
          FROM payments
         WHERE organization_id = ?
           AND status IN ('captured', 'failed')
         ORDER BY created_at DESC
         LIMIT 50
        """,
        (org_id(user),),
    )
    return [
        {
            **r,
            "amount_usd_display": str(r["amount_usd_display"]),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
        }
        for r in rows
    ]
