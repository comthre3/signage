import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import random
import re
import secrets
import shutil
import subprocess
import time
import uuid
from collections import defaultdict
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
from hibp import check_hibp_breach
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


# ── API keys (Phase 2.5h) ─────────────────────────────────────────────

API_KEY_PREFIX_LEN = 12   # "khan_live_" (10 chars) + 2 randomness chars


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Caller stores prefix + hash; returns
    full_key to the operator ONCE (never seen again)."""
    suffix = secrets.token_urlsafe(24)
    full_key = f"khan_live_{suffix}"
    prefix = full_key[:API_KEY_PREFIX_LEN]
    hashed = hash_password(full_key)
    return full_key, prefix, hashed


def lookup_api_key(bearer_token: str) -> Optional[dict]:
    """Return active api_key row if the bearer matches; else None.
    Fire-and-forget update of last_used_at."""
    if not bearer_token or not bearer_token.startswith("khan_live_"):
        return None
    prefix = bearer_token[:API_KEY_PREFIX_LEN]
    candidates = query_all(
        "SELECT id, organization_id, key_hash, scope, key_prefix FROM api_keys "
        "WHERE key_prefix = ? AND revoked_at IS NULL",
        (prefix,),
    )
    for row in candidates:
        if verify_password(bearer_token, row["key_hash"]):
            try:
                execute(
                    "UPDATE api_keys SET last_used_at = now() WHERE id = ?",
                    (row["id"],),
                )
            except Exception as exc:
                logger.warning("api_key_last_used_update_failed id=%s err=%s",
                               row["id"], exc)
            return row
    return None


PLAN_API_LIMITS = {
    "starter":    {"per_minute": 30,   "per_hour": 500},
    "growth":     {"per_minute": 100,  "per_hour": 5000},
    "business":   {"per_minute": 500,  "per_hour": 25000},
    "pro":        {"per_minute": 2000, "per_hour": 100000},
    "enterprise": {"per_minute": 5000, "per_hour": 250000},
}

_rate_buckets: dict = defaultdict(lambda: {"min": [], "hour": []})


def _api_key_rate_check(principal) -> None:
    """Raise 429 if the principal's API key has exceeded its tier limits.
    Sessions are NOT rate-limited here."""
    if principal.api_key is None:
        return
    key_id = principal.api_key["id"]
    org = query_one("SELECT plan FROM organizations WHERE id = ?",
                    (principal.organization_id,))
    plan = (org or {}).get("plan", "starter")
    limits = PLAN_API_LIMITS.get(plan, PLAN_API_LIMITS["starter"])

    now = time.time()
    bucket = _rate_buckets[key_id]
    bucket["min"]  = [t for t in bucket["min"]  if t > now - 60]
    bucket["hour"] = [t for t in bucket["hour"] if t > now - 3600]

    if len(bucket["min"]) >= limits["per_minute"]:
        oldest = min(bucket["min"])
        retry_after = max(1, int(60 - (now - oldest)))
        raise HTTPException(
            status_code=429,
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     str(limits["per_minute"]),
                "X-RateLimit-Window":    "60",
                "X-RateLimit-Remaining": "0",
            },
            detail={"code": "rate_limited",
                    "message": "Per-minute rate limit exceeded"},
        )

    if len(bucket["hour"]) >= limits["per_hour"]:
        oldest = min(bucket["hour"])
        retry_after = max(1, int(3600 - (now - oldest)))
        raise HTTPException(
            status_code=429,
            headers={
                "Retry-After":           str(retry_after),
                "X-RateLimit-Limit":     str(limits["per_hour"]),
                "X-RateLimit-Window":    "3600",
                "X-RateLimit-Remaining": "0",
            },
            detail={"code": "rate_limited",
                    "message": "Per-hour rate limit exceeded"},
        )

    bucket["min"].append(now)
    bucket["hour"].append(now)


def http_error(status: int, code: str, message: str) -> HTTPException:
    """Structured error response: detail = {code, message}.

    Frontend reads `code` to look up a localized string; falls back to
    `message` (English) if the code is unrecognized.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _client_ip(request) -> str | None:
    """Best-effort client IP, honoring forwarding headers from CF/nginx."""
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip() or None
    return (request.client.host if request.client else None) or None


def audit(
    request,
    *,
    action: str,
    actor: dict | None = None,
    target_type: str | None = None,
    target_id=None,
    details: dict | None = None,
    organization_id: int | None = None,
) -> None:
    """Best-effort audit-log write. Never raises — only logs warnings."""
    try:
        ip = _client_ip(request) if request is not None else None
        ua = (request.headers.get("user-agent") if request is not None else None) or None
        org_id = organization_id
        if org_id is None and actor:
            org_id = actor.get("organization_id")
        execute(
            """
            INSERT INTO audit_log
              (organization_id, actor_user_id, actor_username, action,
               target_type, target_id, ip, user_agent, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                org_id,
                actor.get("id") if actor else None,
                actor.get("username") if actor else None,
                action,
                target_type,
                str(target_id) if target_id is not None else None,
                ip,
                ua,
                json.dumps(details) if details is not None else None,
                utc_now_iso(),
            ),
        )
    except Exception as exc:
        logger.warning("audit_failed action=%s err=%s", action, exc)


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


LOGIN_LOCKOUT_WINDOW_SECONDS  = 900   # 15 minutes
LOGIN_LOCKOUT_THRESHOLD       = 5
LOGIN_ATTEMPTS_RETENTION_DAYS = 30

PASSWORD_MIN_LENGTH = 12


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise http_error(
            400, "password_too_short",
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
        )
    if not re.search(r"[a-z]", password):
        raise http_error(
            400, "password_no_lowercase",
            "Password must include a lowercase letter",
        )
    if not re.search(r"[A-Z]", password):
        raise http_error(
            400, "password_no_uppercase",
            "Password must include an uppercase letter",
        )
    if not re.search(r"\d", password):
        raise http_error(
            400, "password_no_number",
            "Password must include a number",
        )
    if check_hibp_breach(password):
        raise http_error(
            400, "password_breached",
            "This password has appeared in known data breaches. Choose a different one.",
        )


def is_online(last_seen: Optional[str]) -> bool:
    if not last_seen:
        return False
    try:
        last_seen_dt = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last_seen_dt).total_seconds() < 90


def _session_lookup(token: str) -> Optional[dict]:
    """Look up an active session by bearer token. Returns user dict or None.

    Idle TTL eviction + last_used update preserved from the original
    get_current_user body.
    """
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
        return None
    last_used = session.get("last_used") or session.get("created_at")
    if last_used:
        try:
            last_used_dt = datetime.fromisoformat(last_used)
        except ValueError:
            last_used_dt = None
        if last_used_dt:
            if (datetime.now(timezone.utc) - last_used_dt).total_seconds() > SESSION_TTL_SECONDS:
                execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
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


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")
    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


class AuthedPrincipal:
    """Carries whichever identity authenticated the request."""

    def __init__(
        self,
        *,
        user: Optional[dict] = None,
        api_key: Optional[dict] = None,
        organization_id: int,
        scope: str,
    ):
        self.user = user
        self.api_key = api_key
        self.organization_id = organization_id
        self.scope = scope


def get_api_authed(authorization: Optional[str] = Header(None)) -> AuthedPrincipal:
    """Dual-mode auth. Accepts a session bearer token OR an API key."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    key_row = lookup_api_key(token)
    if key_row:
        return AuthedPrincipal(
            api_key=key_row,
            organization_id=key_row["organization_id"],
            scope=key_row["scope"],
        )

    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return AuthedPrincipal(
        user=user,
        organization_id=user["organization_id"],
        scope="session",
    )


_ALL_SESSION_ROLES = ("admin", "editor", "viewer")


def require_api_scope(
    *allowed_api_scopes: str,
    session_roles: tuple = _ALL_SESSION_ROLES,
):
    """Gate an endpoint:
      - API-keyed: scope must be in allowed_api_scopes, then tier rate limit applied
      - Session-authed: user's role must be in session_roles (not rate-limited here)
    """
    def dep(principal: AuthedPrincipal = Depends(get_api_authed)) -> AuthedPrincipal:
        if principal.api_key is not None:
            if principal.scope not in allowed_api_scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "api.insufficient_scope",
                        "message": f"This endpoint requires one of: {', '.join(allowed_api_scopes)}",
                        "scope":   principal.scope,
                    },
                )
        else:
            role = (principal.user or {}).get("role", "viewer")
            if role not in session_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "insufficient_role",
                        "message": f"Role {role} not permitted",
                    },
                )
        _api_key_rate_check(principal)
        return principal
    return dep


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


def require_active_subscription(principal: AuthedPrincipal = Depends(get_api_authed)) -> dict:
    """Block write operations when the org's subscription is expired/lapsed.

    Used as a FastAPI dependency, alongside require_roles when both are needed:
        Depends(require_roles("admin"))         # role check
        Depends(require_active_subscription)    # subscription check
    Both run; both must pass.
    Accepts both session tokens and API keys via get_api_authed.
    """
    org = query_one(
        "SELECT id, subscription_status, trial_ends_at, paid_through_at "
        "FROM organizations WHERE id = ?",
        (principal.organization_id,),
    )
    if not org:
        raise http_error(403, "no_organization", "No organization for user")

    state = subscription_state(org)
    if not state["can_write"]:
        code = ("subscription.trial_expired" if state["state"] == "trial_expired"
                else "subscription.expired")
        raise HTTPException(
            status_code=402,
            detail={
                "code":        code,
                "message":     "Subscription required to make changes.",
                "message_key": "error." + code,
                "state":       state["state"],
                "expires_at":  state["expires_at"],
            },
        )
    return principal.user if principal.user is not None else {}


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
    execute(
        "DELETE FROM login_attempts "
        f"WHERE attempted_at < now() - interval '{LOGIN_ATTEMPTS_RETENTION_DAYS} days'"
    )


def cleanup_preview_tokens() -> None:
    cutoff = utc_now_iso()
    execute("DELETE FROM preview_tokens WHERE expires_at < ?", (cutoff,))


class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: Optional[str] = None


class SiteUpdate(BaseModel):
    name:     Optional[str] = None
    slug:     Optional[str] = None
    timezone: Optional[str] = None


class ScreenCreate(BaseModel):
    name: str = Field(..., min_length=1)
    location: Optional[str] = None
    resolution: Optional[str] = None
    orientation: Optional[str] = None
    site_id: Optional[int] = None
    owner_user_id: Optional[int] = None


class ScreenUpdate(BaseModel):
    name:          Optional[str] = None
    location:      Optional[str] = None
    resolution:    Optional[str] = None
    orientation:   Optional[str] = None
    site_id:       Optional[int] = None
    playlist_id:   Optional[int] = None
    owner_user_id: Optional[int] = None
    schedule_id:   Optional[int] = None    # new (Phase 2.5e)


class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1)


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)


class ScheduleRuleIn(BaseModel):
    playlist_id:  int
    start_time:   str = Field(..., pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_time:     str = Field(..., pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    days_of_week: int = Field(..., ge=1, le=127)
    position:     int = 0


class ScheduleRulesIn(BaseModel):
    rules: list[ScheduleRuleIn]


class PlaylistItemCreate(BaseModel):
    media_id: int
    duration_seconds: Optional[int] = Field(None, ge=1, le=3600)


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
    bezel_h_pct: float = Field(default=0.0, ge=0.0, le=10.0)
    bezel_v_pct: float = Field(default=0.0, ge=0.0, le=10.0)
    spanned_playlist_id: Optional[int] = None
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None


class WallUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    mode: Optional[str] = Field(default=None, pattern="^(spanned|mirrored)$")
    mirrored_mode: Optional[str] = Field(default=None, pattern="^(same_playlist|synced_rotation)$")
    mirrored_playlist_id: Optional[int] = None
    bezel_enabled: Optional[bool] = None
    bezel_h_pct: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    bezel_v_pct: Optional[float] = Field(default=None, ge=0.0, le=10.0)
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

    org_row = query_one("SELECT * FROM organizations WHERE id = ?", (new_org_id,))
    sub_state = subscription_state(org_row)

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
            # Phase 2.5f derived fields:
            "state": sub_state["state"],
            "can_write": sub_state["can_write"],
            "days_remaining": sub_state["days_remaining"],
            "expires_at": sub_state["expires_at"],
        },
    }


@app.get("/organization")
def get_organization(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    org = query_one("SELECT * FROM organizations WHERE id = ?", (principal.organization_id,))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    screens_count = query_one(
        "SELECT COUNT(*) AS n FROM screens WHERE organization_id = ?",
        (org["id"],),
    )
    org["screens_used"] = int(screens_count["n"] if screens_count else 0)
    sub_state = subscription_state(org)
    org["state"] = sub_state["state"]
    org["can_write"] = sub_state["can_write"]
    org["days_remaining"] = sub_state["days_remaining"]
    org["expires_at"] = sub_state["expires_at"]
    return org


class OrganizationLocaleUpdate(BaseModel):
    locale: str = Field(..., min_length=2, max_length=2)


@app.patch("/organizations/me")
def patch_organization_me(
    payload: OrganizationLocaleUpdate,
    user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
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
    ip = _client_ip(request)

    # ── Lockout check ────────────────────────────────────────────────
    last_success_row = query_one(
        "SELECT MAX(attempted_at) AS ts FROM login_attempts "
        "WHERE username = ? AND success = true",
        (payload.username,),
    )
    last_success_ts = last_success_row["ts"] if last_success_row else None

    if last_success_ts is not None:
        failure_filter = (
            "WHERE username = ? AND success = false "
            "  AND attempted_at > now() - interval '%d seconds' "
            "  AND attempted_at > ?" % LOGIN_LOCKOUT_WINDOW_SECONDS
        )
        failure_params = (payload.username, last_success_ts)
    else:
        failure_filter = (
            "WHERE username = ? AND success = false "
            "  AND attempted_at > now() - interval '%d seconds'"
            % LOGIN_LOCKOUT_WINDOW_SECONDS
        )
        failure_params = (payload.username,)

    failure_count_row = query_one(
        f"SELECT COUNT(*) AS n FROM login_attempts {failure_filter}",
        failure_params,
    )
    failure_count = int(failure_count_row["n"]) if failure_count_row else 0

    if failure_count >= LOGIN_LOCKOUT_THRESHOLD:
        oldest_row = query_one(
            f"SELECT MIN(attempted_at) AS ts FROM login_attempts {failure_filter}",
            failure_params,
        )
        oldest_ts = oldest_row["ts"] if oldest_row else None
        retry_after = LOGIN_LOCKOUT_WINDOW_SECONDS
        if oldest_ts is not None:
            elapsed = (datetime.now(timezone.utc) - oldest_ts).total_seconds()
            retry_after = max(0, int(LOGIN_LOCKOUT_WINDOW_SECONDS - elapsed))
        audit(request, action="auth.login.failure", actor=None,
              details={"reason": "account_locked", "username": payload.username})
        raise HTTPException(
            status_code=429,
            detail={
                "code": "account_locked",
                "message": "Too many failed login attempts. Try again later.",
                "message_key": "auth.account_locked",
                "retry_after_seconds": int(retry_after),
            },
        )

    # ── Verify password ──────────────────────────────────────────────
    user = query_one("SELECT * FROM users WHERE username = ?", (payload.username,))
    ok = bool(user) and verify_password(payload.password, user["password_hash"])

    # Record attempt regardless of outcome
    execute(
        "INSERT INTO login_attempts (username, success, ip, attempted_at) "
        "VALUES (?, ?, ?, ?)",
        (payload.username, ok, ip, utc_now_iso()),
    )

    if not ok:
        audit(request, action="auth.login.failure", actor=None,
              details={"reason": "invalid_credentials", "username": payload.username})
        raise http_error(401, "invalid_credentials", "Invalid credentials")

    cleanup_sessions()
    token = uuid.uuid4().hex
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user["id"], token, utc_now_iso(), utc_now_iso()),
    )
    org = query_one("SELECT * FROM organizations WHERE id = ?", (user["organization_id"],))
    audit(request, action="auth.login.success",
          actor={"id": user["id"], "username": user["username"],
                 "organization_id": user["organization_id"]})
    sub_state = subscription_state(org)
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
            # Phase 2.5f derived fields:
            "state": sub_state["state"],
            "can_write": sub_state["can_write"],
            "days_remaining": sub_state["days_remaining"],
            "expires_at": sub_state["expires_at"],
        },
    }


@app.post("/auth/logout")
def logout(request: Request, user: dict = Depends(get_current_user)) -> dict:
    execute("DELETE FROM sessions WHERE token = ?", (user["token"],))
    audit(request, action="auth.logout", actor=user)
    return {"status": "logged_out"}


@app.post("/auth/change-password")
def change_password(
    request: Request, payload: ChangePasswordRequest, user: dict = Depends(get_current_user)
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
    audit(request, action="auth.password_change", actor=user)
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


# ── Schedules (Phase 2.5e) ────────────────────────────────────────────

def _schedule_row_to_dict(row: dict, rules: list[dict]) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "rules": rules,
    }


def _rule_row_to_dict(row: dict) -> dict:
    return {
        "id":           row["id"],
        "playlist_id":  row["playlist_id"],
        "start_time":   row["start_time"].strftime("%H:%M") if hasattr(row["start_time"], "strftime") else row["start_time"],
        "end_time":     row["end_time"].strftime("%H:%M") if hasattr(row["end_time"], "strftime") else row["end_time"],
        "days_of_week": row["days_of_week"],
        "position":     row["position"],
    }


def _load_schedule(sid: int, org: int) -> Optional[dict]:
    row = query_one(
        "SELECT id, name, created_at FROM schedules WHERE id = ? AND organization_id = ?",
        (sid, org),
    )
    if not row:
        return None
    rule_rows = query_all(
        "SELECT id, playlist_id, start_time, end_time, days_of_week, position "
        "FROM schedule_rules WHERE schedule_id = ? "
        "ORDER BY position ASC, id ASC",
        (sid,),
    )
    return _schedule_row_to_dict(row, [_rule_row_to_dict(r) for r in rule_rows])


@app.post("/schedules", status_code=201)
def create_schedule(
    payload: ScheduleCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    org = principal.organization_id
    sid = execute(
        "INSERT INTO schedules (organization_id, name) VALUES (?, ?)",
        (org, payload.name),
    )
    return _load_schedule(sid, org)


@app.get("/schedules")
def list_schedules(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    rows = query_all(
        "SELECT id, name, created_at FROM schedules WHERE organization_id = ? "
        "ORDER BY created_at DESC",
        (principal.organization_id,),
    )
    items = [_schedule_row_to_dict(r, []) for r in rows]
    return {"items": items}


@app.get("/schedules/{sid}")
def get_schedule(
    sid: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    sched = _load_schedule(sid, principal.organization_id)
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    return sched


@app.put("/schedules/{sid}")
def update_schedule(
    sid: int,
    payload: ScheduleUpdate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    org = principal.organization_id
    sched = _load_schedule(sid, org)
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    if payload.name is not None:
        execute("UPDATE schedules SET name = ? WHERE id = ?", (payload.name, sid))
    return _load_schedule(sid, org)


@app.delete("/schedules/{sid}", status_code=204)
def delete_schedule(
    sid: int,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin",),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> None:
    sched = _load_schedule(sid, principal.organization_id)
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")
    execute("DELETE FROM schedules WHERE id = ?", (sid,))


@app.put("/schedules/{sid}/rules")
def replace_schedule_rules(
    sid: int,
    payload: ScheduleRulesIn,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    org = principal.organization_id
    sched = _load_schedule(sid, org)
    if not sched:
        raise http_error(404, "schedule.not_found", "Schedule not found")

    # Validate each rule
    parsed = []
    for r_in in payload.rules:
        owned = query_one(
            "SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
            (r_in.playlist_id, org),
        )
        if not owned:
            raise http_error(404, "playlist.not_found",
                             f"Playlist {r_in.playlist_id} not found")
        parsed.append({
            "playlist_id":  r_in.playlist_id,
            "start_time":   _parse_time(r_in.start_time),
            "end_time":     _parse_time(r_in.end_time),
            "days_of_week": r_in.days_of_week,
            "position":     r_in.position,
        })

    # Overlap check (across all pairs)
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            if _rules_overlap(parsed[i], parsed[j]):
                raise http_error(422, "schedule.rule_overlap",
                                 f"Rules {i} and {j} overlap on a shared day")

    # Replace-all
    execute("DELETE FROM schedule_rules WHERE schedule_id = ?", (sid,))
    for r in parsed:
        execute(
            "INSERT INTO schedule_rules "
            "(schedule_id, playlist_id, start_time, end_time, days_of_week, position) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, r["playlist_id"], r["start_time"], r["end_time"],
             r["days_of_week"], r["position"]),
        )

    return _load_schedule(sid, org)


@app.get("/audit-log")
def get_audit_log(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    action: Optional[str] = None,
    actor_id: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    user: dict = Depends(require_roles("admin")),
) -> dict:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where = ["organization_id = ?"]
    params: list = [org_id(user)]
    if action:
        where.append("action = ?")
        params.append(action)
    if actor_id is not None:
        where.append("actor_user_id = ?")
        params.append(actor_id)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at <= ?")
        params.append(until)
    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS n FROM audit_log WHERE {where_sql}",
        tuple(params),
    )
    total = int(total_row["n"]) if total_row else 0

    rows = query_all(
        f"""
        SELECT id, organization_id, actor_user_id, actor_username, action,
               target_type, target_id, ip, user_agent, details, created_at
        FROM audit_log
        WHERE {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params) + (limit, offset),
    )

    items = []
    for r in rows:
        details = r["details"]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                pass
        items.append({
            "id": r["id"],
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "actor": (
                {"id": r["actor_user_id"], "username": r["actor_username"]}
                if r["actor_user_id"] is not None or r["actor_username"]
                else None
            ),
            "action": r["action"],
            "target": (
                {"type": r["target_type"], "id": r["target_id"]}
                if r["target_type"] or r["target_id"]
                else None
            ),
            "ip": r["ip"],
            "user_agent": r["user_agent"],
            "details": details,
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.post("/users")
def create_user(request: Request, payload: UserCreate, user: dict = Depends(require_roles("admin")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
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
    audit(request, action="user.create", actor=user,
          target_type="user", target_id=created["id"],
          details={"username": created["username"], "role": created["role"]})
    return created


@app.put("/users/{user_id}")
def update_user(request: Request, user_id: int, payload: UserUpdate, user: dict = Depends(require_roles("admin")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
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
    after_role = payload.role if payload.role is not None else (target.get("role") if target else None)
    audit(request, action="user.update", actor=user,
          target_type="user", target_id=user_id,
          details={
              "before": {"role": target.get("role") if target else None},
              "after":  {"role": after_role},
              "password_changed": bool(payload.password),
          })
    return updated


@app.delete("/users/{user_id}")
def delete_user(request: Request, user_id: int, user: dict = Depends(require_roles("admin")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
    target = query_one(
        "SELECT * FROM users WHERE id = ? AND organization_id = ?",
        (user_id, org_id(user)),
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(request, action="user.delete", actor=user,
          target_type="user", target_id=user_id,
          details={"username": (target or {}).get("username")})
    return {"status": "deleted"}


@app.get("/sites")
def list_sites(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list[dict]:
    return query_all(
        "SELECT * FROM sites WHERE organization_id = ? ORDER BY created_at DESC",
        (principal.organization_id,),
    )


@app.post("/sites")
def create_site(payload: SiteCreate, user: dict = Depends(require_roles("admin", "editor")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
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
    site_id: int, payload: SiteUpdate, user: dict = Depends(require_roles("admin", "editor")),
    _sub: dict = Depends(require_active_subscription),
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

    if payload.timezone is not None:
        try:
            ZoneInfo(payload.timezone)
        except ZoneInfoNotFoundError:
            raise http_error(422, "site.timezone_invalid",
                             f"Unknown timezone: {payload.timezone}")

    timezone = payload.timezone if payload.timezone is not None else site["timezone"]

    execute(
        "UPDATE sites SET name = ?, slug = ?, timezone = ? WHERE id = ?",
        (name, slug, timezone, site_id),
    )
    return query_one("SELECT * FROM sites WHERE id = ?", (site_id,))


@app.delete("/sites/{site_id}")
def delete_site(site_id: int, user: dict = Depends(require_roles("admin")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
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
def list_screens(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list[dict]:
    oid = principal.organization_id
    user = principal.user or {}
    if user.get("is_admin") or principal.api_key is not None:
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
        uid = user.get("id")
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
            (oid, uid, uid),
        )
    return [sanitize_screen(row, include_token=False) for row in rows]


@app.post("/screens")
def create_screen(payload: ScreenCreate, user: dict = Depends(require_roles("admin", "editor")),
                  _sub: dict = Depends(require_active_subscription)) -> dict:
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
    screen_id: int,
    payload: ScreenUpdate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    oid = principal.organization_id
    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, oid),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    # Validate schedule ownership; use model_fields_set so explicit None
    # (detach) is distinguished from "field not provided".
    if "schedule_id" in payload.model_fields_set:
        if payload.schedule_id is not None:
            owned = query_one(
                "SELECT id FROM schedules WHERE id = ? AND organization_id = ?",
                (payload.schedule_id, oid),
            )
            if not owned:
                raise http_error(404, "schedule.not_found", "Schedule not found")

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

    # Handle schedule_id separately (nullable detach requires model_fields_set)
    if "schedule_id" in payload.model_fields_set:
        execute(
            "UPDATE screens SET schedule_id = ? WHERE id = ?",
            (payload.schedule_id, screen_id),
        )

    return sanitize_screen(query_one("SELECT * FROM screens WHERE id = ?", (screen_id,)))


@app.delete("/screens/{screen_id}")
def delete_screen(request: Request, screen_id: int, user: dict = Depends(require_roles("admin")),
                  _sub: dict = Depends(require_active_subscription)) -> dict:
    screen = query_one(
        "SELECT * FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, org_id(user)),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    execute("DELETE FROM screens WHERE id = ?", (screen_id,))
    audit(request, action="screen.unpair", actor=user,
          target_type="screen", target_id=screen_id,
          details={"screen_name": (screen or {}).get("name")})
    return {"status": "deleted"}


@app.get("/screens/{screen_id}/zones")
def list_screen_zones(
    screen_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    screen = query_one(
        "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
        (screen_id, principal.organization_id),
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    user = principal.user or {}
    if not user.get("is_admin") and principal.api_key is None:
        require_screen_access(screen_id, user)
    zones = get_screen_zones(screen_id)
    return {"zones": zones}


@app.put("/screens/{screen_id}/zones")
def update_screen_zones(
    screen_id: int,
    payload: ScreenZonesPayload,
    user: dict = Depends(require_roles("admin", "editor")),
    _sub: dict = Depends(require_active_subscription),
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
    payload: ZoneTemplateCreate, user: dict = Depends(require_roles("admin", "editor")),
    _sub: dict = Depends(require_active_subscription),
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
    _sub: dict = Depends(require_active_subscription),
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
def create_group(payload: GroupCreate, user: dict = Depends(require_roles("admin")),
                 _sub: dict = Depends(require_active_subscription)) -> dict:
    group_id = execute(
        "INSERT INTO groups (organization_id, name, created_at) VALUES (?, ?, ?)",
        (org_id(user), payload.name, utc_now_iso()),
    )
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.put("/groups/{group_id}")
def update_group(group_id: int, payload: GroupUpdate, user: dict = Depends(require_roles("admin")),
                 _sub: dict = Depends(require_active_subscription)) -> dict:
    group = query_one(
        "SELECT id FROM groups WHERE id = ? AND organization_id = ?",
        (group_id, org_id(user)),
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    execute("UPDATE groups SET name = ? WHERE id = ?", (payload.name, group_id))
    return query_one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, user: dict = Depends(require_roles("admin")),
                 _sub: dict = Depends(require_active_subscription)) -> dict:
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
    user_id: int, payload: UserGroupsPayload, user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
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
    screen_id: int, payload: ScreenGroupsPayload, user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
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


# ── Subscription state (Phase 2.5f) ───────────────────────────────────


def _parse_iso(value) -> Optional[datetime]:
    """Accept str (ISO) or already-parsed datetime; return tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def subscription_state(org: dict) -> dict:
    """Derive subscription state from raw org columns.

    Returns:
      {
        "state":          "trialing" | "trial_expired" | "active" | "lapsed",
        "can_write":      bool,
        "days_remaining": int | None,
        "expires_at":     ISO string | None,
      }

    Convention: subscription_status='active' with paid_through_at IS NULL
    means "no expiry" (seeded default org, admin override).
    """
    status = org.get("subscription_status") or "trialing"
    now = datetime.now(timezone.utc)

    def _days_until(ts) -> int:
        """Whole days remaining, rounded up so +3d still shows 3."""
        import math
        return max(0, math.ceil((ts - now).total_seconds() / 86400))

    if status == "trialing":
        ts = _parse_iso(org.get("trial_ends_at"))
        if ts and ts > now:
            return {
                "state":          "trialing",
                "can_write":      True,
                "days_remaining": _days_until(ts),
                "expires_at":     ts.isoformat(),
            }
        return {
            "state":          "trial_expired",
            "can_write":      False,
            "days_remaining": 0,
            "expires_at":     ts.isoformat() if ts else None,
        }

    if status == "active":
        ts = _parse_iso(org.get("paid_through_at"))
        if ts is None:
            return {"state": "active", "can_write": True,
                    "days_remaining": None, "expires_at": None}
        if ts > now:
            return {
                "state":          "active",
                "can_write":      True,
                "days_remaining": _days_until(ts),
                "expires_at":     ts.isoformat(),
            }
        return {
            "state":          "lapsed",
            "can_write":      False,
            "days_remaining": 0,
            "expires_at":     ts.isoformat(),
        }

    return {"state": status, "can_write": True,
            "days_remaining": None, "expires_at": None}


# ── Subscription reminders (Phase 2.5g) ───────────────────────────────


def _claim_reminder(org_id: int, reminder_type: str, expires_at: datetime) -> bool:
    """Try to claim the right to send this reminder. Returns True iff newly claimed.

    Race-safe across replicas via UNIQUE(org, type, expires_at) + ON CONFLICT.
    A `True` return means "this caller got the row in; you may send." A `False`
    means "someone else already sent this; skip."
    """
    row = query_one(
        """
        INSERT INTO subscription_reminders (organization_id, reminder_type, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT (organization_id, reminder_type, expires_at) DO NOTHING
        RETURNING id
        """,
        (org_id, reminder_type, expires_at),
    )
    return row is not None


def _reminder_template(reminder_type: str, org: dict, locale: str) -> tuple[str, str, str]:
    """Return (subject, html, text) for the given reminder + locale."""
    is_ar = locale == "ar"
    biz   = org.get("name") or ("شاشاتك" if is_ar else "your business")
    base  = os.getenv("APP_URL", "https://app.khanshoof.com").rstrip("/")
    cta   = f"{base}/?section=billing"
    if reminder_type == "trial_3day":
        return _tpl_trial_3day(biz, cta, is_ar)
    if reminder_type == "trial_0day":
        return _tpl_trial_0day(biz, cta, is_ar)
    if reminder_type == "renewal_7day":
        return _tpl_renewal_7day(biz, cta, is_ar)
    raise ValueError(f"Unknown reminder_type: {reminder_type}")


def _tpl_trial_3day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "تنتهي تجربتك خلال ٣ أيام"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"تنتهي تجربة Khanshoof المجانية خلال ٣ أيام. "
            f"للاستمرار في تعديل المحتوى، اشترك من هنا:\n{cta}\n\n"
            f"الشاشات ستستمر في تشغيل المحتوى المخزّن لديها.\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>تنتهي تجربة Khanshoof المجانية خلال ٣ أيام. "
            f"للاستمرار في تعديل المحتوى، <a href=\"{cta}\">اشترك من هنا</a>.</p>"
            f"<p>الشاشات ستستمر في تشغيل المحتوى المخزّن لديها.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your trial ends in 3 days"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof trial ends in 3 days. "
            f"To keep editing content past then, subscribe here:\n{cta}\n\n"
            f"Your screens will keep playing their current content.\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof trial ends in 3 days. "
            f"To keep editing content past then, <a href=\"{cta}\">subscribe here</a>.</p>"
            f"<p>Your screens will keep playing their current content.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text


def _tpl_trial_0day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "انتهت تجربتك"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"انتهت تجربة Khanshoof المجانية. "
            f"لمتابعة إجراء التغييرات على المحتوى، اشترك من هنا:\n{cta}\n\n"
            f"الشاشات ستستمر في تشغيل المحتوى الحالي بلا انقطاع.\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>انتهت تجربة Khanshoof المجانية. "
            f"لمتابعة إجراء التغييرات على المحتوى، <a href=\"{cta}\">اشترك من هنا</a>.</p>"
            f"<p>الشاشات ستستمر في تشغيل المحتوى الحالي بلا انقطاع.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your trial has ended"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof trial has ended. "
            f"To resume making changes to your content, subscribe here:\n{cta}\n\n"
            f"Your screens are still playing their current content with no interruption.\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof trial has ended. "
            f"To resume making changes to your content, <a href=\"{cta}\">subscribe here</a>.</p>"
            f"<p>Your screens are still playing their current content with no interruption.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text


def _tpl_renewal_7day(biz: str, cta: str, is_ar: bool) -> tuple[str, str, str]:
    if is_ar:
        subject = "يجدّد اشتراكك خلال ٧ أيام"
        text = (
            f"مرحبًا {biz}،\n\n"
            f"اشتراك Khanshoof الحالي ينتهي خلال ٧ أيام. "
            f"للتجديد قبل أن تفقد القدرة على تعديل المحتوى، اضغط هنا:\n{cta}\n\n"
            f"— فريق Khanshoof"
        )
        html = (
            f"<p>مرحبًا {biz}،</p>"
            f"<p>اشتراك Khanshoof الحالي ينتهي خلال ٧ أيام. "
            f"للتجديد قبل أن تفقد القدرة على تعديل المحتوى، <a href=\"{cta}\">اضغط هنا</a>.</p>"
            f"<p>— فريق Khanshoof</p>"
        )
    else:
        subject = "Your subscription renews in 7 days"
        text = (
            f"Hi {biz},\n\n"
            f"Your Khanshoof subscription ends in 7 days. "
            f"To renew before losing the ability to edit content, visit:\n{cta}\n\n"
            f"— The Khanshoof team"
        )
        html = (
            f"<p>Hi {biz},</p>"
            f"<p>Your Khanshoof subscription ends in 7 days. "
            f"To renew before losing the ability to edit content, <a href=\"{cta}\">visit your billing page</a>.</p>"
            f"<p>— The Khanshoof team</p>"
        )
    return subject, html, text


REMINDER_TICK_SECONDS    = 3600   # 1 hour
TRIAL_3DAY_THRESHOLD     = 3
RENEWAL_7DAY_THRESHOLD   = 7


def _send_reminder(org: dict, reminder_type: str) -> int:
    """Send `reminder_type` email to all admins of `org`. Returns 1 if any
    send succeeded, 0 otherwise.

    When RESEND_API_KEY is missing, returns 0 WITHOUT inserting a claim row.
    Callers that have already claimed must handle the case where this returns 0
    after a successful claim — they keep the claim row (documented tradeoff:
    all-admins-bounced means no retry). Only the no-key path is special.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.info("reminder_skipped_no_resend_key org=%s type=%s",
                    org.get("id"), reminder_type)
        return 0

    admins = query_all(
        "SELECT username FROM users WHERE organization_id = ? "
        "AND role = 'admin' AND is_admin = 1",
        (org["id"],),
    )
    if not admins:
        logger.warning("reminder_no_admins org=%s type=%s",
                       org.get("id"), reminder_type)
        return 0

    locale = (org.get("locale") or "en").lower()
    subject, html, text = _reminder_template(reminder_type, org, locale)
    from_addr = os.getenv("RESEND_FROM", "Khanshoof <noreply@khanshoof.com>")

    any_ok = False
    for admin in admins:
        to_email = admin["username"]
        try:
            send_via_resend(
                api_key=api_key, from_addr=from_addr, to=to_email,
                subject=subject, html=html, text=text,
            )
            any_ok = True
        except Exception as exc:
            logger.error("reminder_send_failed org=%s type=%s to=%s err=%s",
                         org.get("id"), reminder_type, to_email, exc)
    return 1 if any_ok else 0


def _maybe_send_reminders_for_org(org: dict) -> int:
    """Send any applicable reminder for this org. Returns count sent (0 or 1).

    Pre-check: when RESEND_API_KEY is missing, skip the claim too — so a
    retry on the next tick (with the key set) will succeed.
    """
    if not os.getenv("RESEND_API_KEY", "").strip():
        return 0

    state = subscription_state(org)
    days = state.get("days_remaining")
    expires_at_iso = state.get("expires_at")
    if expires_at_iso is None:
        return 0
    expires_at = _parse_iso(expires_at_iso)

    if state["state"] == "trialing":
        if days is not None and days <= TRIAL_3DAY_THRESHOLD:
            if _claim_reminder(org["id"], "trial_3day", expires_at):
                return _send_reminder(org, "trial_3day")
        return 0

    if state["state"] == "trial_expired":
        if _claim_reminder(org["id"], "trial_0day", expires_at):
            return _send_reminder(org, "trial_0day")
        return 0

    if state["state"] == "active":
        if days is not None and days <= RENEWAL_7DAY_THRESHOLD:
            if _claim_reminder(org["id"], "renewal_7day", expires_at):
                return _send_reminder(org, "renewal_7day")
        return 0

    return 0


def _reminder_check_once() -> int:
    """One pass through all orgs. Returns count of reminders sent.
    Pure-Python; testable without the asyncio wrapper."""
    orgs = query_all(
        "SELECT id, name, locale, subscription_status, trial_ends_at, paid_through_at "
        "FROM organizations"
    )
    sent = 0
    for org in orgs:
        try:
            sent += _maybe_send_reminders_for_org(org)
        except Exception as exc:
            logger.warning("reminder_check_org_failed org=%s err=%s",
                           org.get("id"), exc)
    return sent


async def _reminder_check_loop():
    """Background task: every REMINDER_TICK_SECONDS, walk all orgs and
    send reminders that haven't been sent yet. Errors swallowed; never
    crashes the app."""
    while True:
        await asyncio.sleep(REMINDER_TICK_SECONDS)
        try:
            _reminder_check_once()
        except Exception as exc:
            logger.warning("reminder_check_loop_failed: %s", exc)


@app.on_event("startup")
async def _start_reminder_loop():
    asyncio.create_task(_reminder_check_loop())


# ── Dayparting (Phase 2.5e) ───────────────────────────────────────────
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import time as _time_type

_KUWAIT_TZ = ZoneInfo("Asia/Kuwait")


def _time_in_window(now: _time_type, start: _time_type, end: _time_type) -> bool:
    """True iff `now` is inside [start, end), handling wrap-midnight rules."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _time_windows_overlap(s1, e1, s2, e2) -> bool:
    """True iff the two TIME windows overlap. Both may wrap midnight."""
    def expand(s, e):
        if s <= e:
            return [(s, e)]
        return [(s, _time_type(23, 59, 59, 999999)), (_time_type(0, 0), e)]
    for a_s, a_e in expand(s1, e1):
        for b_s, b_e in expand(s2, e2):
            if a_s < b_e and b_s < a_e:
                return True
    return False


def _rules_overlap(a: dict, b: dict) -> bool:
    """True iff rules a and b share at least one day AND time windows overlap."""
    if not (a["days_of_week"] & b["days_of_week"]):
        return False
    return _time_windows_overlap(
        a["start_time"], a["end_time"],
        b["start_time"], b["end_time"],
    )


def _parse_time(s: str) -> _time_type:
    """Parse HH:MM or HH:MM:SS into datetime.time."""
    parts = s.split(":")
    h = int(parts[0]); m = int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    return _time_type(h, m, sec)


def _site_timezone(site_id: Optional[int]) -> ZoneInfo:
    """Look up the site's IANA tz; fall back to Asia/Kuwait."""
    if site_id is None:
        return _KUWAIT_TZ
    row = query_one("SELECT timezone FROM sites WHERE id = ?", (site_id,))
    if not row or not row.get("timezone"):
        return _KUWAIT_TZ
    try:
        return ZoneInfo(row["timezone"])
    except ZoneInfoNotFoundError:
        logger.warning("invalid_site_timezone site_id=%s tz=%s", site_id, row["timezone"])
        return _KUWAIT_TZ


def resolve_active_playlist(screen: dict) -> Optional[int]:
    """Return the playlist_id that should currently play on this screen.

    Resolution order:
      1. If screen has schedule_id AND a rule matches now-in-site-tz → rule.playlist_id
      2. Else → screen.playlist_id (may be None)
    """
    if not screen.get("schedule_id"):
        return screen.get("playlist_id")

    site_tz = _site_timezone(screen.get("site_id"))
    now_local = datetime.now(site_tz)
    weekday_bit = 1 << now_local.weekday()      # Mon=0..Sun=6
    now_t = now_local.time()

    rules = query_all(
        "SELECT id, playlist_id, start_time, end_time, days_of_week "
        "FROM schedule_rules WHERE schedule_id = ?",
        (screen["schedule_id"],),
    )
    for rule in rules:
        if not (rule["days_of_week"] & weekday_bit):
            continue
        if _time_in_window(now_t, rule["start_time"], rule["end_time"]):
            return rule["playlist_id"]

    return screen.get("playlist_id")            # fallback to default


def build_screen_payload(screen: dict) -> dict:
    playlist = None
    items = []
    active_playlist_id = resolve_active_playlist(screen)
    if active_playlist_id:
        playlist = query_one("SELECT * FROM playlists WHERE id = ?", (active_playlist_id,))
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
            (active_playlist_id,),
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
    _sub: dict = Depends(require_active_subscription),
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
def pair_screen(request: Request, payload: PairRequest) -> dict:
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
    audit(request, action="screen.pair", actor=None,
          target_type="screen", target_id=screen["id"],
          details={"screen_name": screen.get("name"), "site_id": screen.get("site_id")})
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
    if screen.get("wall_cell_id"):
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (screen["wall_cell_id"],))
        if cell:
            wall = query_one("SELECT * FROM walls WHERE id = ?", (cell["wall_id"],))
            if wall:
                payload["wall_id"] = wall["id"]
                payload["wall_cell"] = {
                    "row": cell["row_index"], "col": cell["col_index"],
                    "rows": wall["rows"], "cols": wall["cols"],
                }
                payload["wall_mode"] = wall["mode"]
    return payload


# -------------------- Walls --------------------

_VALID_CANVAS_RESOLUTIONS = {(1920, 1080), (3840, 2160), (7680, 4320)}


def _validate_spanned_fields(payload: WallCreate) -> None:
    if (payload.canvas_width_px, payload.canvas_height_px) not in _VALID_CANVAS_RESOLUTIONS:
        raise http_error(400, "wall.canvas_resolution_invalid",
                         "Canvas resolution must be 1080p, 4K, or 8K.")
    if payload.cols * payload.bezel_h_pct >= 100 or payload.rows * payload.bezel_v_pct >= 100:
        raise http_error(400, "wall.bezel_too_large",
                         "Bezel percentages too large — visible area would collapse.")


@app.post("/walls", status_code=201)
def create_wall(request: Request, payload: WallCreate,
                user: dict = Depends(require_roles("admin", "editor")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
    if payload.mode == "spanned":
        _validate_spanned_fields(payload)
    elif payload.mode == "mirrored":
        if not payload.mirrored_mode:
            raise http_error(422, "wall.mirrored_mode_required",
                             "Mirrored walls need a sub-mode (same_playlist or synced_rotation).")
        if payload.mirrored_mode == "same_playlist" and not payload.mirrored_playlist_id:
            raise http_error(422, "wall.mirrored_playlist_required",
                             "Same-playlist mirrored walls need a playlist.")
        if payload.mirrored_playlist_id is not None:
            own = query_one("SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
                            (payload.mirrored_playlist_id, org_id(user)))
            if not own:
                raise http_error(404, "playlist.not_found", "Playlist not found")

    now = utc_now_iso()

    # For spanned walls, auto-create the wall_canvas playlist BEFORE the wall row,
    # so we can FK the wall to it atomically.
    spanned_playlist_id = payload.spanned_playlist_id
    if payload.mode == "spanned":
        spanned_playlist_id = execute(
            "INSERT INTO playlists (organization_id, name, kind, created_at) "
            "VALUES (?, ?, 'wall_canvas', ?)",
            (org_id(user), f"Canvas: {payload.name}", now),
        )
        bezel_enabled = (payload.bezel_h_pct > 0 or payload.bezel_v_pct > 0)
    else:
        bezel_enabled = payload.bezel_enabled

    wall_id = execute(
        """INSERT INTO walls (organization_id, name, mode, rows, cols,
               canvas_width_px, canvas_height_px, bezel_enabled,
               bezel_h_pct, bezel_v_pct,
               spanned_playlist_id, mirrored_mode, mirrored_playlist_id,
               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (org_id(user), payload.name, payload.mode, payload.rows, payload.cols,
         payload.canvas_width_px, payload.canvas_height_px, bezel_enabled,
         payload.bezel_h_pct, payload.bezel_v_pct,
         spanned_playlist_id, payload.mirrored_mode, payload.mirrored_playlist_id,
         now, now),
    )
    for r in range(payload.rows):
        for c in range(payload.cols):
            execute(
                "INSERT INTO wall_cells (wall_id, row_index, col_index, created_at) VALUES (?, ?, ?, ?)",
                (wall_id, r, c, now),
            )
    wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
    audit(request, action="wall.create", actor=user,
          target_type="wall", target_id=wall_id,
          details={"name": payload.name, "mode": payload.mode,
                   "rows": payload.rows, "cols": payload.cols})
    return serialize_wall(wall)


@app.get("/walls")
def list_walls(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list[dict]:
    walls = query_all(
        "SELECT * FROM walls WHERE organization_id = ? ORDER BY id DESC",
        (principal.organization_id,),
    )
    return [serialize_wall(w) for w in walls]


@app.get("/walls/{wall_id}")
def get_wall(
    wall_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, principal.organization_id))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    return serialize_wall(wall)


@app.patch("/walls/{wall_id}")
def patch_wall(wall_id: int, payload: WallUpdate,
               user: dict = Depends(require_roles("admin", "editor")),
               _sub: dict = Depends(require_active_subscription)) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")

    fields = payload.model_dump(exclude_unset=True)

    # Mode change side effects: delete outgoing playlist, create incoming.
    # Pairings (wall_cells.screen_id) are preserved.
    if "mode" in fields and fields["mode"] != wall["mode"]:
        new_mode = fields["mode"]
        if wall["mode"] == "mirrored" and wall.get("mirrored_playlist_id"):
            execute("DELETE FROM playlists WHERE id = ?",
                    (wall["mirrored_playlist_id"],))
            fields["mirrored_playlist_id"] = None
            fields["mirrored_mode"] = None
        elif wall["mode"] == "spanned" and wall.get("spanned_playlist_id"):
            execute("DELETE FROM playlists WHERE id = ?",
                    (wall["spanned_playlist_id"],))
            fields["spanned_playlist_id"] = None
        if new_mode == "spanned":
            new_pl = execute(
                "INSERT INTO playlists (organization_id, name, kind, created_at) "
                "VALUES (?, ?, 'wall_canvas', ?)",
                (org_id(user), f"Canvas: {wall['name']}", utc_now_iso()),
            )
            fields["spanned_playlist_id"] = new_pl

    if not fields:
        return serialize_wall(wall)

    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [utc_now_iso(), wall_id]
    execute(f"UPDATE walls SET {sets}, updated_at = ? WHERE id = ?", tuple(params))
    return serialize_wall(query_one("SELECT * FROM walls WHERE id = ?", (wall_id,)))


@app.patch("/walls/{wall_id}/cells")
def patch_wall_cell(wall_id: int, payload: WallCellUpdate,
                    user: dict = Depends(require_roles("admin", "editor")),
                    _sub: dict = Depends(require_active_subscription)) -> dict:
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
def delete_wall(request: Request, wall_id: int,
                user: dict = Depends(require_roles("admin", "editor")),
                _sub: dict = Depends(require_active_subscription)) -> None:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, org_id(user)))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    execute("DELETE FROM walls WHERE id = ?", (wall_id,))
    audit(request, action="wall.delete", actor=user,
          target_type="wall", target_id=wall_id,
          details={"name": (wall or {}).get("name")})
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
                   user: dict = Depends(require_roles("admin", "editor")),
                   _sub: dict = Depends(require_active_subscription)) -> dict:
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
                     user: dict = Depends(require_roles("admin", "editor")),
                     _sub: dict = Depends(require_active_subscription)) -> None:
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


# ---- Spanned-mode canvas playlists ----

_ALLOWED_CANVAS_MIME_PREFIXES = ("image/", "video/")
_ALLOWED_CANVAS_EXACT_MIMES = {"application/pdf"}


def _is_allowed_canvas_mime(mime: str) -> bool:
    if mime in _ALLOWED_CANVAS_EXACT_MIMES:
        return True
    return any(mime.startswith(p) for p in _ALLOWED_CANVAS_MIME_PREFIXES)


def _load_spanned_wall_or_404(wall_id: int, owner_org_id: int) -> dict:
    wall = query_one(
        "SELECT * FROM walls WHERE id = ? AND organization_id = ?",
        (wall_id, owner_org_id),
    )
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    if wall["mode"] != "spanned" or not wall.get("spanned_playlist_id"):
        raise http_error(404, "wall.not_spanned", "Wall is not spanned.")
    return wall


@app.get("/walls/{wall_id}/canvas-playlist")
def get_canvas_playlist(
    wall_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    items = query_all(
        """SELECT pi.id, pi.media_id, pi.position, pi.duration_seconds,
                  pi.duration_override_seconds, pi.fit_mode,
                  m.name AS media_name, m.mime_type, m.filename
           FROM playlist_items pi JOIN media m ON m.id = pi.media_id
           WHERE pi.playlist_id = ?
           ORDER BY pi.position ASC, pi.id ASC""",
        (wall["spanned_playlist_id"],),
    )
    return {"wall_id": wall_id, "playlist_id": wall["spanned_playlist_id"], "items": items}


class CanvasItemCreate(BaseModel):
    media_id: int
    position: int = Field(..., ge=0)
    duration_override_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    fit_mode: str = Field(default="fit", pattern="^(fit|fill|stretch)$")


@app.post("/walls/{wall_id}/canvas-playlist/items", status_code=201)
def add_canvas_item(
    wall_id: int,
    payload: CanvasItemCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    media = query_one(
        "SELECT * FROM media WHERE id = ? AND organization_id = ?",
        (payload.media_id, principal.organization_id),
    )
    if not media:
        raise http_error(404, "media.not_found", "Media not found")
    if not _is_allowed_canvas_mime(media["mime_type"]):
        raise http_error(400, "wall.canvas_media_type_blocked",
                         "URL embeds aren't supported on spanned walls. "
                         "Use mirrored mode for URL media.")
    duration_seconds = payload.duration_override_seconds or 5
    item_id = execute(
        """INSERT INTO playlist_items
               (playlist_id, media_id, position, duration_seconds,
                duration_override_seconds, fit_mode, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (wall["spanned_playlist_id"], payload.media_id, payload.position,
         duration_seconds, payload.duration_override_seconds, payload.fit_mode,
         utc_now_iso()),
    )
    if media["mime_type"] == "application/pdf":
        _ensure_pdf_rasterized(media, wall)
    return query_one("SELECT * FROM playlist_items WHERE id = ?", (item_id,))


class CanvasItemUpdate(BaseModel):
    position: Optional[int] = Field(default=None, ge=0)
    duration_override_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    fit_mode: Optional[str] = Field(default=None, pattern="^(fit|fill|stretch)$")


@app.patch("/walls/{wall_id}/canvas-playlist/items/{item_id}")
def patch_canvas_item(
    wall_id: int,
    item_id: int,
    payload: CanvasItemUpdate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    item = query_one(
        "SELECT * FROM playlist_items WHERE id = ? AND playlist_id = ?",
        (item_id, wall["spanned_playlist_id"]),
    )
    if not item:
        raise http_error(404, "playlist_item.not_found", "Item not found")
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields.keys())
        params = list(fields.values()) + [item_id]
        execute(f"UPDATE playlist_items SET {sets} WHERE id = ?", tuple(params))
    return query_one("SELECT * FROM playlist_items WHERE id = ?", (item_id,))


@app.delete("/walls/{wall_id}/canvas-playlist/items/{item_id}", status_code=204)
def delete_canvas_item(
    wall_id: int,
    item_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> None:
    wall = _load_spanned_wall_or_404(wall_id, principal.organization_id)
    execute(
        "DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?",
        (item_id, wall["spanned_playlist_id"]),
    )


def _ensure_pdf_rasterized(media: dict, wall: dict) -> None:
    """Synchronously rasterize a PDF to the wall's canvas size if not already done."""
    from pathlib import Path
    from pdf_render import rasterize_pdf, PdfRenderError  # type: ignore
    out_dir = (Path(UPLOAD_DIR) / "pdf_pages" / str(media["id"])
               / f"canvas_{wall['canvas_width_px']}x{wall['canvas_height_px']}")
    if out_dir.exists() and any(out_dir.iterdir()):
        return  # Already rendered for this resolution.
    pdf_path = Path(UPLOAD_DIR) / media["filename"]
    try:
        rasterize_pdf(str(pdf_path), str(out_dir),
                      width_px=wall["canvas_width_px"],
                      height_px=wall["canvas_height_px"])
        execute("UPDATE media SET pdf_pages_status = 'ready' WHERE id = ?",
                (media["id"],))
    except PdfRenderError as exc:
        execute("UPDATE media SET pdf_pages_status = 'error' WHERE id = ?",
                (media["id"],))
        raise http_error(500, "wall.pdf_rasterize_failed",
                         f"Couldn't render PDF: {exc}")


@app.post("/screens/{screen_id}/preview-token")
def create_preview_token(
    screen_id: int, user: dict = Depends(require_roles("admin", "editor")),
    _sub: dict = Depends(require_active_subscription),
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
def list_playlists(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list[dict]:
    return query_all(
        "SELECT * FROM playlists WHERE organization_id = ? ORDER BY created_at DESC",
        (principal.organization_id,),
    )


@app.post("/playlists")
def create_playlist(
    payload: PlaylistCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    playlist_id = execute(
        "INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
        (principal.organization_id, payload.name, utc_now_iso()),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.get("/playlists/{playlist_id}")
def get_playlist(
    playlist_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, principal.organization_id),
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
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, principal.organization_id),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute(
        "UPDATE playlists SET name = ? WHERE id = ?",
        (payload.name or playlist["name"], playlist_id),
    )
    return query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))


@app.delete("/playlists/{playlist_id}")
def delete_playlist(
    playlist_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    playlist = query_one(
        "SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, principal.organization_id),
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    execute("UPDATE screens SET playlist_id = NULL WHERE playlist_id = ?", (playlist_id,))
    return {"status": "deleted"}


def _default_duration_seconds(media: dict) -> int:
    """Default playlist-item duration when caller omits duration_seconds."""
    mime = (media.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return 10
    if mime.startswith("video/"):
        # forward-hook: media table has no duration_seconds column yet,
        # so this currently always falls through to the 10s default.
        stored = media.get("duration_seconds")
        if isinstance(stored, (int, float)) and stored > 0:
            return max(1, math.ceil(stored))
        return 10
    if mime == "application/pdf":
        return 30
    if mime == "text/url":
        return 10
    return 10


@app.post("/playlists/{playlist_id}/items")
def add_playlist_item(
    playlist_id: int,
    payload: PlaylistItemCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    oid = principal.organization_id
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
    duration_seconds = (
        payload.duration_seconds
        if payload.duration_seconds is not None
        else _default_duration_seconds(media)
    )
    item_id = execute(
        """
        INSERT INTO playlist_items
        (playlist_id, media_id, duration_seconds, position, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (playlist_id, payload.media_id, duration_seconds, position, utc_now_iso()),
    )
    item = query_one("SELECT * FROM playlist_items WHERE id = ?", (item_id,))
    item["media"] = media
    item["url"] = f"/uploads/{media['filename']}"
    return item


@app.delete("/playlists/{playlist_id}/items/{item_id}")
def delete_playlist_item(
    playlist_id: int,
    item_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    playlist = query_one(
        "SELECT id FROM playlists WHERE id = ? AND organization_id = ?",
        (playlist_id, principal.organization_id),
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
def list_media(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> list[dict]:
    media = query_all(
        "SELECT * FROM media WHERE organization_id = ? ORDER BY created_at DESC",
        (principal.organization_id,),
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
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
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
            principal.organization_id,
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
    payload: MediaUrlCreate,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    url = payload.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (principal.organization_id, payload.name, url, "text/url", 0, utc_now_iso()),
    )
    item = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    item["url"] = item["filename"]
    return item


@app.delete("/media/{media_id}")
def delete_media(
    media_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope(
        "api:rw", session_roles=("admin", "editor"),
    )),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    media = query_one(
        "SELECT * FROM media WHERE id = ? AND organization_id = ?",
        (media_id, principal.organization_id),
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
        old_plan_row = query_one("SELECT plan FROM organizations WHERE id = ?", (row["organization_id"],))
        old_plan = old_plan_row["plan"] if old_plan_row else None
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
        audit(request, action="billing.plan_change",
              actor=None, organization_id=row["organization_id"],
              target_type="organization", target_id=row["organization_id"],
              details={"from_plan": old_plan, "to_plan": row["tier"], "term_months": term})
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
