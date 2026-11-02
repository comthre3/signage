import asyncio
import hashlib
import hmac
import json
import logging
import math
import mimetypes
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

from fastapi.responses import HTMLResponse
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import Headers
from starlette.responses import FileResponse, StreamingResponse
import anyio
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from billing import create_knet_request
import ratelimit
from db import advisory_lock, init_db, execute, query_all, query_one, transaction, utc_now_iso
import media_probe
import menu_render
import menus as menus_domain
from hibp import check_hibp_breach
from email_utils import is_valid_email, send_via_resend
from oauth import router as oauth_router
from mcp_server import attach_mcp
import wall_layout
from walls import attach_walls

logger = logging.getLogger("signage")
logging.basicConfig(level=logging.INFO)


def transcode_video(input_path: str, media_id: int) -> None:
    """Re-encode uploaded video to streaming-optimized H.264.

    H.264 (libx264) hardware-decodes on every consumer device — minimal GPU/CPU
    load on signage clients. CRF 23 is visually lossless; +faststart puts the
    moov atom at the file head so the player starts immediately. Bitrate is
    capped at 12 Mbps to bound network use even on 4K source.

    The long side is capped at media_probe.VIDEO_LONG_SIDE_MAX (3840 px): cheap
    TV decoders often stop at 4096 px, and a video wall plays one file across
    every TV. The cap can change the size, so the output is probed again and
    the row gets the size and length of the file the TVs actually play.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not installed — skipping transcode for media %s", media_id)
        return
    base, _ext = os.path.splitext(input_path)
    output_path = f"{base}.opt.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", media_probe.TRANSCODE_SCALE_FILTER,
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
    probed = media_probe.probe_media(output_path, "video/mp4")
    # COALESCE: whatever this second probe cannot read keeps the upload's value.
    execute(
        "UPDATE media SET filename = ?, mime_type = ?, size = ?, "
        "width_px = COALESCE(?::integer, width_px), "
        "height_px = COALESCE(?::integer, height_px), "
        "duration_seconds = COALESCE(?::real, duration_seconds) "
        "WHERE id = ?",
        (new_filename, "video/mp4", new_size,
         probed["width_px"], probed["height_px"], probed["duration_seconds"], media_id),
    )
    if os.path.exists(input_path) and input_path != output_path:
        try:
            os.remove(input_path)
        except OSError:
            pass
    logger.info("Transcoded media %s → %s (%d bytes)", media_id, new_filename, new_size)
    # A free wall made while this ran was sized from the upload (5760x1080,
    # say); the file it plays is now capped at 3840 px (3840x720).
    _refresh_free_walls_for_media(media_id)


UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
PREVIEW_TTL_SECONDS = int(os.getenv("PREVIEW_TTL_SECONDS", "300"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))

def _origin_of(url: str) -> str:
    match = re.match(r"^(https?://[^/?#\s]+)", (url or "").strip())
    return match.group(1) if match else ""


# Who may frame a file from /uploads: this origin, the dashboard and the
# player (it shows PDF playlist items in an iframe). See add_security_headers.
_UPLOAD_FRAME_ANCESTORS = " ".join(["'self'"] + [
    origin for origin in (_origin_of(os.getenv("APP_URL", "")),
                          _origin_of(os.getenv("PLAYER_BASE_URL", ""))) if origin])

RENDERER_URL   = os.getenv("RENDERER_URL", "").rstrip("/")
RENDERER_TOKEN = os.getenv("RENDERER_TOKEN", "")

MAX_BOARDS_PER_REQUEST = 60

DOCS_ENABLED = os.getenv("DOCS_ENABLED", "0").lower() in ("1", "true", "yes")

app = FastAPI(
    title="Menu Signage Backend",
    version="",
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

_RATE_LIMITS_ENABLED = os.getenv("RATE_LIMITS_ENABLED", "1").lower() in ("1", "true", "yes")

# Counters live in Redis so every uvicorn worker shares one budget; without it
# an advertised "10/minute" would be 10 per worker. How the limiter keys and
# degrades when Redis is unreachable is in ratelimit.build_limiter.
limiter = ratelimit.build_limiter(_RATE_LIMITS_ENABLED)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Threads are Postgres connections here; see the budget in db.py.
from db import bound_threadpool
app.add_event_handler("startup", bound_threadpool)

from social_auth import attach_social_auth
attach_social_auth(app)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self' https://api.khanshoof.com"
    # Uploads are served from this origin, beside the OAuth consent pages and
    # their cookie. An SVG (or anything that is not plain media) opened here
    # directly could run script, so it gets a sandbox with no script at all.
    # Raster images, videos and PDFs keep the normal policy: the dashboard
    # opens them directly and the player frames PDFs, which a sandbox breaks.
    # Decided from the file's extension, not the response: a 304 carries no
    # Content-Type, and the browser stores the 304's headers.
    if request.url.path.startswith("/uploads/"):
        guessed = mimetypes.guess_type(request.url.path)[0] or ""
        if guessed == "image/svg+xml" or not guessed.startswith(("image/", "video/", "application/pdf")):
            csp = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        else:
            csp = response.headers["Content-Security-Policy"]
        # Framed by our own dashboard and player only (the player shows PDFs
        # in an iframe), which X-Frame-Options cannot express: its DENY
        # blocked every PDF playlist item outright.
        del response.headers["X-Frame-Options"]
        response.headers["Content-Security-Policy"] = f"{csp}; frame-ancestors {_UPLOAD_FRAME_ANCESTORS}"
        # Public files: the player's service worker fetches them with CORS to
        # cache them, from whatever origin a deployment serves the player on.
        if "access-control-allow-origin" not in response.headers:
            response.headers["Access-Control-Allow-Origin"] = "*"
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

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangedStaticFiles(StaticFiles):
    """StaticFiles that also answers a single byte range with 206.

    Starlette 0.37 ignores Range and always sends the whole file with a 200.
    Video elements seek with Range requests -- a wall cell seeking to the
    shared timeline position of an uncached video jumped back to the start --
    and iOS Safari will not play video at all from a server that cannot
    answer them. Path resolution (and so traversal protection) stays
    StaticFiles' own: only a FileResponse it already decided to send is
    narrowed. Multiple or malformed ranges get the whole file, as RFC 9110
    permits.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code != 200 or not isinstance(response, FileResponse):
            return response
        response.headers["Accept-Ranges"] = "bytes"
        match = _RANGE_RE.match(Headers(scope=scope).get("range", "").strip())
        if scope["method"] != "GET" or not match or match.groups() == ("", ""):
            return response
        size = os.stat(response.path).st_size
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        else:
            start, end = max(size - int(last), 0), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() in ("etag", "last-modified", "accept-ranges")}
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(_file_slice(response.path, start, end - start + 1),
                                 status_code=206, headers=headers,
                                 media_type=response.media_type)


async def _file_slice(path: str, start: int, length: int, chunk: int = 256 * 1024):
    async with await anyio.open_file(path, "rb") as f:
        await f.seek(start)
        while length > 0:
            data = await f.read(min(chunk, length))
            if not data:
                break
            length -= len(data)
            yield data


os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", RangedStaticFiles(directory=UPLOAD_DIR), name="uploads")

attach_walls(app)
app.include_router(oauth_router)
attach_mcp(app)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "site"


def generate_pair_code() -> str:
    # secrets, not random: a Mersenne Twister's next outputs can be recovered
    # from earlier ones, and this code alone hands out the screen's token.
    return f"{100000 + secrets.randbelow(900000)}"


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


def hash_api_key(full_key: str) -> str:
    """SHA-256 of an API key, hex encoded.

    Deliberately NOT the PBKDF2 used for passwords. A slow KDF exists to make
    brute-forcing a *low-entropy* human-chosen secret expensive. API keys are
    secrets.token_urlsafe(24) -- 192 bits from the CSPRNG -- so guessing one is
    infeasible no matter how fast the hash is, and the KDF only bought ~82ms of
    CPU burned on every single authenticated request. Plain SHA-256 over a
    high-entropy token is the standard choice here.
    """
    return hashlib.sha256(full_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Caller stores prefix + hash; returns
    full_key to the operator ONCE (never seen again)."""
    suffix = secrets.token_urlsafe(24)
    full_key = f"khan_live_{suffix}"
    prefix = full_key[:API_KEY_PREFIX_LEN]
    hashed = hash_api_key(full_key)
    return full_key, prefix, hashed


def _touch_api_key(key_id: int) -> None:
    """Fire-and-forget last_used_at bump; never fail the request over it."""
    try:
        execute("UPDATE api_keys SET last_used_at = now() WHERE id = ?", (key_id,))
    except Exception as exc:
        logger.warning("api_key_last_used_update_failed id=%s err=%s", key_id, exc)


API_KEY_COLUMNS = "id, organization_id, key_hash, scope, key_prefix"


def lookup_api_key(bearer_token: str) -> Optional[dict]:
    """Return active api_key row if the bearer matches; else None.
    Fire-and-forget update of last_used_at."""
    if not bearer_token or not bearer_token.startswith("khan_live_"):
        return None

    # Fast path: one indexed equality lookup, no key derivation at all.
    sha = hash_api_key(bearer_token)
    row = query_one(
        f"SELECT {API_KEY_COLUMNS} FROM api_keys "
        "WHERE key_sha256 = ? AND revoked_at IS NULL",
        (sha,),
    )
    if row:
        _touch_api_key(row["id"])
        return row

    # Legacy path: keys minted before key_sha256 existed still carry only a
    # PBKDF2 hash. Verify once the old way, then upgrade the row in place so
    # this key never pays that cost again.
    prefix = bearer_token[:API_KEY_PREFIX_LEN]
    candidates = query_all(
        f"SELECT {API_KEY_COLUMNS} FROM api_keys "
        "WHERE key_prefix = ? AND key_sha256 IS NULL AND revoked_at IS NULL",
        (prefix,),
    )
    for row in candidates:
        if verify_password(bearer_token, row["key_hash"]):
            try:
                execute("UPDATE api_keys SET key_sha256 = ? WHERE id = ?",
                        (sha, row["id"]))
            except Exception as exc:
                logger.warning("api_key_sha_backfill_failed id=%s err=%s",
                               row["id"], exc)
            _touch_api_key(row["id"])
            return row
    return None


PLAN_API_LIMITS = {
    "starter":    {"per_minute": 30,   "per_hour": 500},
    "growth":     {"per_minute": 100,  "per_hour": 5000},
    "business":   {"per_minute": 500,  "per_hour": 25000},
    "pro":        {"per_minute": 2000, "per_hour": 100000},
    "enterprise": {"per_minute": 5000, "per_hour": 250000},
}




def _api_key_rate_check(principal) -> None:
    """Raise 429 if the principal's API key has exceeded its tier limits.
    Sessions are NOT rate-limited here.

    Counters are shared across workers via Redis (see backend/ratelimit.py);
    they degrade to per-process counters if Redis is down.
    """
    if principal.api_key is None:
        return
    key_id = principal.api_key["id"]
    org = query_one("SELECT plan FROM organizations WHERE id = ?",
                    (principal.organization_id,))
    plan = (org or {}).get("plan", "starter")
    limits = PLAN_API_LIMITS.get(plan, PLAN_API_LIMITS["starter"])

    for window_s, limit_key, window_label in ((60, "per_minute", "60"),
                                              (3600, "per_hour", "3600")):
        limit = limits[limit_key]
        count = ratelimit.incr_window(f"apikey:{key_id}", window_s)
        if count > limit:
            retry_after = ratelimit.window_reset_in(window_s)
            raise HTTPException(
                status_code=429,
                headers={
                    "Retry-After":           str(retry_after),
                    "X-RateLimit-Limit":     str(limit),
                    "X-RateLimit-Window":    window_label,
                    "X-RateLimit-Remaining": "0",
                },
                detail={"code": "rate_limited",
                        "message": ("Per-minute rate limit exceeded"
                                    if window_s == 60
                                    else "Per-hour rate limit exceeded")},
            )


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


# last_used is rewritten at most this often rather than on every request,
# which would make each authenticated call a write. The idle clock can thus
# run up to this far ahead of the true last use.
_SESSION_TOUCH_SECONDS = 60


def _session_lookup(token: str) -> Optional[dict]:
    """Look up an active session by bearer token. Returns user dict or None.

    A session idle longer than SESSION_TTL_SECONDS is deleted and refused
    here, on the request; cleanup_sessions() only sweeps at startup and login.
    """
    session = query_one(
        """
        SELECT sessions.token, sessions.user_id, sessions.created_at,
               sessions.last_used, users.username, users.is_admin,
               users.role, users.organization_id
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    )
    if not session:
        return None
    idle = None
    last_used = session.get("last_used") or session.get("created_at")
    if last_used:
        try:
            last_used_dt = datetime.fromisoformat(last_used)
        except ValueError:
            last_used_dt = None
        if last_used_dt:
            if last_used_dt.tzinfo is None:
                last_used_dt = last_used_dt.replace(tzinfo=timezone.utc)
            idle = (datetime.now(timezone.utc) - last_used_dt).total_seconds()
            if idle > SESSION_TTL_SECONDS:
                execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
    if idle is None or idle > min(_SESSION_TOUCH_SECONDS, SESSION_TTL_SECONDS / 2):
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
    """Carries whichever identity authenticated the request.

    user is set for a session and for an OAuth access token (the user who
    granted it); it is None only for an API key, which acts for the org.
    """

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
    """Triple-mode auth. Accepts a session bearer token, an API key, or an OAuth access token."""
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

    # OAuth access token (Phase 2.5i-1)
    from oauth import lookup_oauth_access_token
    oauth_row = lookup_oauth_access_token(token)
    if oauth_row:
        # Acts as the user who granted it, so require_api_scope applies that
        # user's current role exactly as it would to their own session.
        return AuthedPrincipal(
            user=oauth_row["user"],
            organization_id=oauth_row["organization_id"],
            scope=oauth_row["scope"],
        )

    user = _session_lookup(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
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
      - API key / OAuth token: scope must be in allowed_api_scopes
      - Session / OAuth token: the user's current role must be in session_roles
        (an OAuth token is checked against the user who granted it, so it can
        never do more than that user's own session could)
      - API key: tier rate limit applied (sessions/OAuth not rate-limited here)
    """
    def dep(principal: AuthedPrincipal = Depends(get_api_authed)) -> AuthedPrincipal:
        if principal.scope != "session":
            # Machine principal (API key or OAuth access token) — enforce scope
            if principal.scope not in allowed_api_scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code":    "api.insufficient_scope",
                        "message": f"This endpoint requires one of: {', '.join(allowed_api_scopes)}",
                        "scope":   principal.scope,
                    },
                )
        if principal.user is not None:
            role = principal.user.get("role", "viewer")
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


_ORG_SCOPED_KINDS = {
    "playlists": "playlist", "sites": "site", "users": "user",
    "media": "media", "schedules": "schedule",
}


def require_org_rows(table: str, ids, oid: int) -> None:
    """404 unless every id names a row of `table` belonging to organization oid.

    For ids a write is about to STORE as a reference -- a screen's playlist, a
    zone item's media, a wall's playlist. Fetching by id is org-scoped
    everywhere, but a stored reference is followed later without an org filter
    (build_screen_payload, get_screen_zones), so an unchecked one hands one
    organization's content to another. None entries mean "no reference".
    """
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return
    found = query_all(
        f"SELECT id FROM {table} WHERE organization_id = ? AND id = ANY(?)",
        (oid, list(wanted)),
    )
    if len(found) != len(wanted):
        kind = _ORG_SCOPED_KINDS[table]
        raise http_error(404, f"{kind}.not_found", f"{kind.capitalize()} not found")

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
        cells = query_all(
            "SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY row_index, col_index",
            (wall["id"],),
        )
        online = _cell_online_map(wall["id"])
        for cell in cells:
            cell["online"] = online.get(cell["id"], False)
        out["cells"] = cells
    if wall.get("layout") == "free":
        out["video"] = _free_wall_video(wall)
    return out


def _cell_online_map(wall_id: int) -> dict:
    """{cell id: whether its TV checked in within the last 90 s}. A wall TV
    re-reads its content every 60 s, which keeps screens.last_seen fresh."""
    rows = query_all(
        "SELECT wc.id, s.last_seen FROM wall_cells wc "
        "LEFT JOIN screens s ON s.id = wc.screen_id WHERE wc.wall_id = ?",
        (wall_id,),
    )
    return {r["id"]: is_online(r["last_seen"]) for r in rows}


def _free_wall_video(wall: dict) -> dict | None:
    """The one item a free wall plays, as the phone's editor draws it."""
    if not wall.get("spanned_playlist_id"):
        return None
    row = query_one(
        """SELECT m.id, m.name, m.filename, m.mime_type, m.width_px, m.height_px
           FROM playlist_items pi JOIN media m ON m.id = pi.media_id
           WHERE pi.playlist_id = ?
           ORDER BY pi.position ASC, pi.id ASC LIMIT 1""",
        (wall["spanned_playlist_id"],),
    )
    if not row:
        return None
    return {
        "media_id": row["id"], "name": row["name"],
        "url": f"/uploads/{row['filename']}", "mime_type": row["mime_type"],
        "width_px": row["width_px"], "height_px": row["height_px"],
    }


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
    password: str  # policy enforced by validate_password(), see UserUpdate
    role: str = "viewer"


class UserUpdate(BaseModel):
    # Length is validate_password()'s job: a Field minimum here answers first,
    # with raw English the dashboard cannot localize, quoting the wrong number.
    password: Optional[str] = None
    role: Optional[str] = None


class SignupStartRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=200)


@app.on_event("startup")
def startup() -> None:
    init_db()
    cleanup_sessions()
    cleanup_preview_tokens()
    _bootstrap_default_admin()
    _ensure_setup_token()


SETUP_TOKEN_PATH = os.path.join(os.getenv("DATA_DIR", "./data"), "setup-token.txt")


def needs_setup() -> bool:
    """True when this deployment has no users and awaits its first admin."""
    return query_one("SELECT id FROM users LIMIT 1") is None


def _publish_setup_token(token: str) -> None:
    """Put the token where an operator can reach it, and nobody else can.

    Written to the log AND to a file: an operator who deploys unattended has no
    log to watch, and one who deploys interactively should not have to hunt for
    a file. 0600 so it is readable only by the account running the service, and
    deleted the moment setup completes.
    """
    logger.warning(
        "SETUP REQUIRED: no users exist. Create the first administrator with "
        "this one-time token: %s", token)
    try:
        os.makedirs(os.path.dirname(SETUP_TOKEN_PATH) or ".", exist_ok=True)
        # Open with 0600 from the start rather than chmod-ing afterwards, which
        # would leave a window where the token is world-readable.
        fd = os.open(SETUP_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
    except OSError as exc:
        logger.warning("could not write %s: %s (token is in the log above)",
                       SETUP_TOKEN_PATH, exc)


def _clear_setup_token() -> None:
    execute("DELETE FROM setup_token")
    try:
        os.remove(SETUP_TOKEN_PATH)
    except OSError:
        pass


def _ensure_setup_token() -> None:
    """Issue a setup token if this deployment still needs its first admin.

    Runs under the migration advisory lock: every worker executes startup, and
    without it four workers would mint four tokens and race to insert them.
    """
    with advisory_lock():
        if not needs_setup():
            _clear_setup_token()   # stale token from a since-completed setup
            return
        existing = query_one("SELECT token FROM setup_token WHERE id = 1")
        if existing:
            _publish_setup_token(existing["token"])
            return
        token = secrets.token_urlsafe(32)
        execute("INSERT INTO setup_token (id, token) VALUES (1, ?)", (token,))
        _publish_setup_token(token)


def _bootstrap_default_admin() -> None:
    """Seed the first admin on an empty database, if ADMIN_PASSWORD asks to.

    Optional. With ADMIN_PASSWORD unset -- the default since redeploy.sh
    stopped writing one -- nothing is seeded and the first administrator is
    created in the browser with the one-time setup token. A value the password
    policy refuses is logged and skipped the same way rather than raised:
    raising here aborts startup, so uvicorn exits before the setup page can
    ever be served and the backend crash-loops on a brand-new deployment.

    Runs under the migration advisory lock because every uvicorn worker
    executes startup(): without it, all workers would observe an empty users
    table simultaneously and race to INSERT the same username, and every loser
    would crash on the UNIQUE constraint. The check is re-read inside the lock
    so only the first worker through does any work.
    """
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_password:
        return
    with advisory_lock():
        if needs_setup():
            admin_username = os.getenv("ADMIN_USERNAME", "admin")
            try:
                validate_password(admin_password)
            except HTTPException as exc:
                reason = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
                logger.error(
                    "ADMIN_PASSWORD not used (%s); no admin seeded. Create the "
                    "first administrator in the browser with the setup token.",
                    reason)
                return
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


def _unique_org_slug(business_name: str) -> str:
    """Organization slug, suffixed until unused. Same rule signup applies."""
    base = slugify(business_name)
    slug = base
    counter = 1
    while query_one("SELECT id FROM organizations WHERE slug = ?", (slug,)):
        counter += 1
        slug = f"{base}-{counter}"
    return slug


class SetupPayload(BaseModel):
    setup_token: str = Field(..., min_length=8, max_length=128)
    business_name: str = Field(..., min_length=1, max_length=120)
    username: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


@app.get("/auth/setup-status")
def setup_status() -> dict:
    """Whether this deployment still awaits its first administrator.

    Unauthenticated by necessity -- it is what the login screen asks before
    anyone can authenticate. It leaks only whether the deployment is empty,
    which is already obvious to anyone who tries to log in.
    """
    return {"needs_setup": needs_setup()}


@app.post("/auth/setup")
@limiter.limit("5/minute")
def complete_setup(request: Request, payload: SetupPayload) -> dict:
    """Create the first administrator, once, against a one-time token.

    The token exists because this endpoint has to be reachable by someone with
    no account, on a deployment that is already internet-facing. Without it the
    first stranger to load the page owns the deployment. It is printed to the
    log and written to a 0600 file, both reachable only with server access.
    """
    # Serialized so two simultaneous submissions cannot both create an admin.
    with advisory_lock():
        if not needs_setup():
            raise http_error(409, "setup.already_done",
                             "This deployment already has an administrator")
        row = query_one("SELECT token FROM setup_token WHERE id = 1")
        if not row:
            raise http_error(409, "setup.unavailable",
                             "No setup token has been issued for this deployment")
        if not secrets.compare_digest(payload.setup_token, row["token"]):
            logger.warning("setup_token_mismatch ip=%s", _client_ip(request))
            raise http_error(403, "setup.bad_token", "Setup token is not valid")

        validate_password(payload.password)

        now = utc_now_iso()
        org_id_new = execute(
            """
            INSERT INTO organizations
            (name, slug, plan, screen_limit, subscription_status, locale, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payload.business_name, _unique_org_slug(payload.business_name), "pro", 25,
             "active", "en", now),
        )
        user_id = execute(
            """
            INSERT INTO users (organization_id, username, password_hash, is_admin, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (org_id_new, payload.username, hash_password(payload.password), 1, "admin", now),
        )
        _clear_setup_token()

    token = secrets.token_hex(32)
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) VALUES (?, ?, ?, ?)",
        (user_id, token, utc_now_iso(), utc_now_iso()),
    )
    logger.warning("setup_completed org=%s user=%s", org_id_new, payload.username)
    return {"token": token,
            "user": {"id": user_id, "username": payload.username, "is_admin": True},
            "organization": {"id": org_id_new, "name": payload.business_name}}


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
    from oauth import revoke_user_grants
    revoke_user_grants(user["id"])
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
            "paid_through_at": org["paid_through_at"],
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

    def _bound(name: str, value: str) -> datetime:
        # Parsed here rather than passed to Postgres as text, where a value it
        # cannot read as a timestamp failed the whole query with a 500. A bare
        # date or a time without an offset is taken as UTC.
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            raise http_error(422, "audit_log.date_invalid",
                             f"{name} is not an ISO 8601 date or time: {value}")
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

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
        params.append(_bound("since", since))
    if until:
        where.append("created_at <= ?")
        params.append(_bound("until", until))
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


# ── API Key management endpoints (session-only, admin) ────────────────


class ApiKeyCreate(BaseModel):
    name:  str = Field(..., min_length=1, max_length=200)
    scope: str = Field(..., pattern="^api:(read|rw)$")


@app.post("/api-keys", status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    full_key, prefix, hashed = generate_api_key()
    key_id = execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, key_sha256, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (org_id(user), payload.name, prefix, hashed, hashed, payload.scope, user["id"]),
    )
    return {
        "id":         key_id,
        "name":       payload.name,
        "key_prefix": prefix,
        "key":        full_key,
        "scope":      payload.scope,
        "created_at": utc_now_iso(),
    }


@app.get("/api-keys")
def list_api_keys(user: dict = Depends(require_roles("admin"))) -> dict:
    rows = query_all(
        """
        SELECT id, name, key_prefix, scope, created_at, last_used_at, revoked_at
        FROM api_keys
        WHERE organization_id = ?
        ORDER BY created_at DESC
        """,
        (org_id(user),),
    )

    def fmt(v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    items = []
    for r in rows:
        items.append({
            "id":           r["id"],
            "name":         r["name"],
            "key_prefix":   r["key_prefix"],
            "scope":        r["scope"],
            "created_at":   fmt(r["created_at"]),
            "last_used_at": fmt(r["last_used_at"]),
            "revoked_at":   fmt(r["revoked_at"]),
        })
    return {"items": items}


@app.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    user: dict = Depends(require_roles("admin")),
) -> None:
    row = query_one(
        "SELECT id FROM api_keys WHERE id = ? AND organization_id = ?",
        (key_id, org_id(user)),
    )
    if not row:
        raise http_error(404, "api_key.not_found", "API key not found")
    execute(
        "UPDATE api_keys SET revoked_at = now() WHERE id = ? AND revoked_at IS NULL",
        (key_id,),
    )


@app.post("/api-keys/{key_id}/rotate")
def rotate_api_key(
    key_id: int,
    user: dict = Depends(require_roles("admin")),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    old = query_one(
        "SELECT id, name, scope FROM api_keys "
        "WHERE id = ? AND organization_id = ? AND revoked_at IS NULL",
        (key_id, org_id(user)),
    )
    if not old:
        raise http_error(404, "api_key.not_found", "API key not found")
    execute("UPDATE api_keys SET revoked_at = now() WHERE id = ?", (key_id,))
    full_key, prefix, hashed = generate_api_key()
    new_id = execute(
        "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, key_sha256, scope, created_by_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (org_id(user), old["name"], prefix, hashed, hashed, old["scope"], user["id"]),
    )
    return {
        "id":           new_id,
        "name":         old["name"],
        "key_prefix":   prefix,
        "key":          full_key,
        "scope":        old["scope"],
        "created_at":   utc_now_iso(),
        "rotated_from": key_id,
    }


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
    if payload.password is not None:
        validate_password(payload.password)
        execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
        # A reset is how an admin locks out a compromised account, so like
        # /auth/change-password it ends the user's sessions and OAuth grants --
        # sparing only the session making this request, when it resets itself.
        execute("DELETE FROM sessions WHERE user_id = ? AND token <> ?",
                (user_id, user.get("token") or ""))
        from oauth import revoke_user_grants
        revoke_user_grants(user_id)
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
    from oauth import revoke_user_grants
    revoke_user_grants(user_id)    # first: oauth_tokens.user_id is ON DELETE SET NULL
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
        # ValueError: a key that is not a zone name at all ("/etc/x", "../x",
        # "Asia/", a non-TZif file such as "zone.tab").
        except (ZoneInfoNotFoundError, ValueError):
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
    # Per-user visibility applies to anything acting as a user -- a session
    # or an OAuth token -- and org-wide reads only to API keys (no user).
    if user.get("is_admin") or principal.user is None:
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
    require_org_rows("sites", [payload.site_id], oid)
    require_org_rows("users", [payload.owner_user_id], oid)
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
    user = principal.user or {}
    if principal.user is not None and not user.get("is_admin"):
        require_screen_access(screen_id, user)

    # Only what the request names is written, so a concurrent change to any
    # other column is not overwritten from this request's earlier read. The
    # references are nullable: model_fields_set separates an explicit null
    # ("No playlist", "Unassigned" -- clear it) from a field left out (keep
    # it), and each is checked against the caller's org first.
    changes = {}
    if payload.name:
        changes["name"] = payload.name
    for column in ("location", "resolution", "orientation"):
        if getattr(payload, column) is not None:
            changes[column] = getattr(payload, column)
    for column, table in (("site_id", "sites"), ("playlist_id", "playlists"),
                          ("owner_user_id", "users"), ("schedule_id", "schedules")):
        if column in payload.model_fields_set:
            changes[column] = getattr(payload, column)
            require_org_rows(table, [changes[column]], oid)

    if changes:
        sets = ", ".join(f"{column} = ?" for column in changes)
        execute(f"UPDATE screens SET {sets} WHERE id = ?", (*changes.values(), screen_id))

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
    if principal.user is not None and not user.get("is_admin"):
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
    require_org_rows("media", [i.media_id for z in payload.zones for i in z.items], org_id(user))

    # Replace-style write: the old layout must survive any failure below.
    with transaction():
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
    zones = playable_zones(screen["id"])
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
    zones = playable_zones(screen["id"])
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
    require_org_rows("sites", [payload.site_id], org_id(user))
    require_org_rows("media", [i.media_id for z in payload.zones for i in z.items], org_id(user))
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


def _next_days(days: int) -> int:
    """The day after each day in a Mon=bit0..Sun=bit6 mask (Sunday -> Monday)."""
    return ((days << 1) | (days >> 6)) & 0x7F


def _rule_pieces(rule: dict) -> list:
    """A rule as (days_of_week, start, end) pieces, none wrapping midnight.

    A rule that wraps midnight belongs to the day it STARTS on: Friday
    22:00-02:00 is Friday night, so its after-midnight piece runs on Saturday.
    This is how resolve_active_playlist plays it.
    """
    s, e, days = rule["start_time"], rule["end_time"], rule["days_of_week"]
    if s <= e:
        return [(days, s, e)]
    return [(days, s, _time_type(23, 59, 59, 999999)),
            (_next_days(days), _time_type(0, 0), e)]


def _rules_overlap(a: dict, b: dict) -> bool:
    """True iff rules a and b are ever active on the same day at the same time."""
    for a_days, a_s, a_e in _rule_pieces(a):
        for b_days, b_s, b_e in _rule_pieces(b):
            if a_days & b_days and a_s < b_e and b_s < a_e:
                return True
    return False


def _parse_time(s: str) -> _time_type:
    """Parse HH:MM or HH:MM:SS into datetime.time.

    ScheduleRuleIn's pattern checks only the shape, so "25:99" gets here;
    an out-of-range field is the caller's error (422), not ours (500).
    """
    parts = s.split(":")
    h = int(parts[0]); m = int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    try:
        return _time_type(h, m, sec)
    except ValueError:
        raise http_error(422, "schedule.time_invalid", f"Invalid time: {s}")


def _site_timezone(site_id: Optional[int]) -> ZoneInfo:
    """Look up the site's IANA tz; fall back to Asia/Kuwait."""
    if site_id is None:
        return _KUWAIT_TZ
    row = query_one("SELECT timezone FROM sites WHERE id = ?", (site_id,))
    if not row or not row.get("timezone"):
        return _KUWAIT_TZ
    try:
        return ZoneInfo(row["timezone"])
    except (ZoneInfoNotFoundError, ValueError):     # ValueError: malformed key
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
    yesterday_bit = 1 << ((now_local.weekday() - 1) % 7)
    now_t = now_local.time()

    rules = query_all(
        "SELECT id, playlist_id, start_time, end_time, days_of_week "
        "FROM schedule_rules WHERE schedule_id = ?",
        (screen["schedule_id"],),
    )
    for rule in rules:
        start, end = rule["start_time"], rule["end_time"]
        # Past midnight, a wrapping rule is still the night it started on:
        # at Saturday 01:00, Friday 22:00-02:00 needs Friday's bit, not
        # Saturday's (see _rule_pieces, which the overlap check uses).
        day_bit = yesterday_bit if start > end and now_t < start else weekday_bit
        if not (rule["days_of_week"] & day_bit):
            continue
        if _time_in_window(now_t, start, end):
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


def playable_zones(screen_id: int) -> list[dict]:
    """Zones as the PLAYER should see them, which is not always what exists.

    A zone layout in which no zone holds a single item is not a layout -- it is
    an unfinished one. Handing it to the player puts it into zones mode, where
    it faithfully renders empty regions and shows black, while ignoring the
    playlist the screen already has assigned. That reads to an operator as "the
    screen is broken", with nothing anywhere saying why.

    Returning [] instead lets the player take its existing playlist path. The
    threshold is deliberately ALL zones empty: if even one zone has content the
    layout is real and wins, so an operator filling zones one at a time is not
    yanked back to an old playlist mid-edit.

    Only the player-facing endpoints use this. The dashboard editor reads
    /screens/{screen_id}/zones and must keep seeing empty zones to edit them.
    """
    zones = get_screen_zones(screen_id)
    return zones if any(z.get("items") for z in zones) else []


class PairRequestStart(BaseModel):
    user_agent: str | None = Field(default=None, max_length=500)


# Up to 8 TVs of one wall can sit behind one venue NAT (a single client IP),
# each asking for a fresh code when its last one expires.
@app.post("/screens/request_code")
@limiter.limit("30/minute")
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


# The same 8 TVs each poll every 3 s while waiting to be scanned: 8 x 20 = 160
# a minute, plus the phone's code checks (GET /screens/poll) from the same NAT.
@app.get("/screens/poll/{code}")
@limiter.limit("240/minute")
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
        # Anyone holding the code gets the screen's token, so hand it out only
        # while the TV that showed the code can still be polling for it (every
        # few seconds). Later, a code read off the screen unlocks nothing.
        try:
            claimed_dt = datetime.fromisoformat(row["claimed_at"])
        except (TypeError, ValueError):
            claimed_dt = None
        if claimed_dt is None or now > claimed_dt + timedelta(seconds=PAIR_CODE_TTL_SECONDS):
            return {"status": "expired"}
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
@limiter.limit("10/minute")
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
    # Only the current mode's fields are stored. A spanned wall used to keep
    # whatever mirrored_* it was sent, unchecked, and switching it to mirrored
    # later played that playlist -- another organization's, if so named.
    mirrored_mode = mirrored_playlist_id = spanned_playlist_id = None
    if payload.mode == "spanned":
        _validate_spanned_fields(payload)
    elif payload.mode == "mirrored":
        if not payload.mirrored_mode:
            raise http_error(422, "wall.mirrored_mode_required",
                             "Mirrored walls need a sub-mode (same_playlist or synced_rotation).")
        if payload.mirrored_mode == "same_playlist" and not payload.mirrored_playlist_id:
            raise http_error(422, "wall.mirrored_playlist_required",
                             "Same-playlist mirrored walls need a playlist.")
        require_org_rows("playlists", [payload.mirrored_playlist_id], org_id(user))
        mirrored_mode, mirrored_playlist_id = payload.mirrored_mode, payload.mirrored_playlist_id

    now = utc_now_iso()

    # For spanned walls, auto-create the wall_canvas playlist BEFORE the wall row,
    # so we can FK the wall to it atomically.
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
         spanned_playlist_id, mirrored_mode, mirrored_playlist_id,
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
    # A free wall's canvas is its video and its boxes are placed from the
    # phone: mode, canvas and bezel settings mean nothing there, and a mode
    # change would delete its canvas playlist. Values are compared, so a form
    # that re-sends the unchanged ones can still rename the wall.
    if wall.get("layout") == "free":
        locked = ("mode", "mirrored_mode", "mirrored_playlist_id", "bezel_enabled",
                  "bezel_h_pct", "bezel_v_pct", "canvas_width_px", "canvas_height_px")
        if any(k in fields and fields[k] != wall.get(k) for k in locked):
            raise http_error(409, "wall.free_layout",
                             "A video wall set up from a phone keeps its layout; "
                             "only its name can change here.")
    require_org_rows("playlists", [fields.get("mirrored_playlist_id")], org_id(user))

    # Mode change side effects: drop the outgoing mode's playlist reference,
    # create the incoming one. Pairings (wall_cells.screen_id) are preserved.
    # Only a spanned wall's canvas playlist belongs to the wall (create_wall
    # made it); a mirrored wall plays a playlist the user picked, which other
    # screens and schedules may also use, so it is detached, never deleted.
    mode_change = "mode" in fields and fields["mode"] != wall["mode"]
    if mode_change and wall["mode"] == "mirrored":
        fields["mirrored_playlist_id"] = None
        fields["mirrored_mode"] = None
    elif mode_change:
        # Entering mirrored: its settings come from this request alone, never
        # from values left on the row while it was spanned.
        fields.setdefault("mirrored_mode", None)
        fields.setdefault("mirrored_playlist_id", None)
    after = {**wall, **fields}
    # Checked only when this request touches the mirrored setup: the playlist
    # is ON DELETE SET NULL, and a wall whose playlist was deleted must still
    # accept a rename or a bezel change.
    if after["mode"] == "mirrored" and {"mode", "mirrored_mode", "mirrored_playlist_id"} & set(fields):
        if not after.get("mirrored_mode"):
            raise http_error(422, "wall.mirrored_mode_required",
                             "Mirrored walls need a sub-mode (same_playlist or synced_rotation).")
        if after["mirrored_mode"] == "same_playlist" and not after.get("mirrored_playlist_id"):
            raise http_error(422, "wall.mirrored_playlist_required",
                             "Same-playlist mirrored walls need a playlist.")

    if mode_change:
        if wall["mode"] == "spanned" and wall.get("spanned_playlist_id"):
            execute("DELETE FROM playlists WHERE id = ? AND organization_id = ? AND kind = 'wall_canvas'",
                    (wall["spanned_playlist_id"], org_id(user)))
            fields["spanned_playlist_id"] = None
        if fields["mode"] == "spanned":
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
    # Its cell screens were created by redeem for those cells alone, and its
    # canvas playlist by create_wall for this wall alone (unpair_wall_cell has
    # the same reasoning). Left behind, the screens count toward the plan with
    # no device, and the playlist lingers in the Playlists list.
    paired = query_all(
        "SELECT row_index, col_index, id, screen_id FROM wall_cells "
        "WHERE wall_id = ? AND screen_id IS NOT NULL",
        (wall_id,),
    )
    with transaction():
        execute(
            "DELETE FROM screens WHERE organization_id = ? AND wall_cell_id IN "
            "(SELECT id FROM wall_cells WHERE wall_id = ?)",
            (wall["organization_id"], wall_id),
        )
        execute("DELETE FROM walls WHERE id = ?", (wall_id,))
        if wall.get("spanned_playlist_id"):
            execute("DELETE FROM playlists WHERE id = ? AND organization_id = ? AND kind = 'wall_canvas'",
                    (wall["spanned_playlist_id"], wall["organization_id"]))
    for cell in paired:
        try:
            from walls import broadcast_bye  # type: ignore
            broadcast_bye(wall_id, cell["row_index"], cell["col_index"], "wall_deleted")
        except Exception:
            logger.exception("wall_bye_failed wall_id=%s cell=%s,%s",
                             wall_id, cell["row_index"], cell["col_index"])
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


# ---- Binding a TV to a wall cell ----
#
# Shared by /walls/cells/redeem (a TV typing a wall code) and the phone-driven
# free-wall endpoints (a phone claiming the standalone code a TV shows in its
# QR). Every caller holds one transaction and takes its locks in one order --
# TV code claim, organization, wall, cells -- so concurrent requests queue
# instead of deadlocking.


def _tv_code_row_or_raise(code: str, now: datetime) -> dict:
    """The pending standalone pairing code a TV is showing, or the refusal the
    phone translates: unknown 404, already paired 409, expired 400.

    Unlike /screens/claim, which re-binds a code to the screen it already
    names, these callers have no screen yet, so any status but 'pending'
    means another phone (or the Screens page) got there first.
    """
    row = query_one("SELECT * FROM pairing_codes WHERE code = ?", (code,))
    if not row:
        raise http_error(404, "pair.code_unknown", "Pairing code not recognized.")
    if row["status"] != "pending":
        raise http_error(409, "pair.code_used", "This TV is already paired.")
    try:
        expires_dt = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        expires_dt = now - timedelta(seconds=1)
    if now > expires_dt:
        raise http_error(400, "pair.code_expired",
                         "Pairing code expired. Ask the TV to show a new one.")
    return row


def _take_tv_code(row_id: int, now: datetime) -> None:
    """Claim the code inside the caller's transaction.

    The conditional UPDATE is /screens/claim's: of two phones claiming one TV
    only one matches status 'pending', and the other gets 409. claimed_at is
    tz-aware because /screens/poll compares it with an aware clock.
    """
    claimed = execute(
        "UPDATE pairing_codes SET status = 'paired', claimed_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now.isoformat(), row_id),
    )
    if not claimed:
        raise http_error(409, "pair.code_used", "This TV was just paired from another phone.")


def _lock_org(oid: int) -> dict | None:
    """Lock the organization row for the plan's screen-limit check.

    Take it before any INSERT in the transaction: an INSERT that references
    the organization takes a KEY SHARE lock on its row, and two requests that
    each hold one and then ask for FOR UPDATE deadlock.
    """
    return query_one(
        "SELECT id, screen_limit FROM organizations WHERE id = ? FOR UPDATE", (oid,)
    )


def _bind_screen_to_cell(wall: dict, cell: dict, org: dict | None, now: datetime,
                         name: str) -> tuple[int, str]:
    """Give the cell its screen and return (screen_id, screen_token).

    A cell's screen is a screens row like any other, so a new one is subject
    to the plan's screen_limit exactly as in create_screen. A cell that has a
    screen reuses it and rotates its token, which unplugs the TV that held it,
    instead of adding a second screen that would keep claiming the cell and
    counting toward the limit. ``name`` names a new screen only.

    Precondition: inside transaction(), with ``org`` locked by _lock_org.
    """
    oid = wall["organization_id"]
    reuse = None
    if cell["screen_id"]:
        reuse = query_one(
            "SELECT id FROM screens WHERE id = ? AND organization_id = ?",
            (cell["screen_id"], oid),
        )
    # Screens an earlier re-pair left claiming this cell: their TVs lost
    # the cell long ago, and they still count toward the limit.
    execute(
        "DELETE FROM screens WHERE wall_cell_id = ? AND id <> ?",
        (cell["id"], reuse["id"] if reuse else 0),
    )
    pair_code = generate_unique_pair_code()
    screen_token = generate_unique_token()
    if reuse:
        screen_id = reuse["id"]
        execute(
            "UPDATE screens SET token = ?, pair_code = ?, wall_cell_id = ? WHERE id = ?",
            (screen_token, pair_code, cell["id"], screen_id),
        )
    else:
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
        screen_id = execute(
            """INSERT INTO screens (organization_id, name, pair_code, token, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (oid, name, pair_code, screen_token, now.isoformat()),
        )
        execute("UPDATE screens SET wall_cell_id = ? WHERE id = ?", (cell["id"], screen_id))
    execute("UPDATE wall_cells SET screen_id = ? WHERE id = ?", (screen_id, cell["id"]))
    return screen_id, screen_token


def _attach_tv_code(row_id: int, screen_id: int) -> None:
    """Point a claimed code at its new screen, so the TV's next poll collects
    the token. Do it in the same transaction as _take_tv_code: a poll that
    sees 'paired' with no screen answers 'expired', and the TV drops its code.
    """
    execute("UPDATE pairing_codes SET screen_id = ? WHERE id = ?", (screen_id, row_id))


@app.post("/walls/cells/redeem")
@limiter.limit("30/minute")
def redeem_wall_cell(request: Request, payload: WallRedeemRequest) -> dict:
    """Bind the TV that typed ``code`` to its wall cell.

    A cell's screen is a screens row like any other, so a new one is subject to
    the plan's screen_limit exactly as in create_screen. Re-pairing a cell
    reuses its screen and rotates the token -- which unplugs the TV that held
    it -- instead of adding a second screen that would keep claiming the cell
    and counting toward the limit.

    Everything after the cheap checks is one transaction: the code is claimed
    with a conditional UPDATE so two concurrent redeems of it cannot both win,
    and the organization row is locked so concurrent redeems of different codes
    cannot together overshoot the limit. A refusal rolls the claim back, so the
    code still works after an upgrade.
    """
    from db import connect as _db_connect

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
    oid = wall["organization_id"]

    with _db_connect().transaction():
        claimed = execute(
            "UPDATE wall_pairing_codes SET status = 'claimed', claimed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now.isoformat(), row["id"]),
        )
        if not claimed:
            raise http_error(409, "wall.pair_code_used", "This code was already used")
        org = _lock_org(oid)
        cell = query_one(
            "SELECT * FROM wall_cells WHERE wall_id = ? AND row_index = ? AND col_index = ? "
            "FOR UPDATE",
            (row["wall_id"], row["row_index"], row["col_index"]),
        )
        if not cell:
            raise http_error(404, "wall.cell_not_found", "Cell not found")
        _screen_id, screen_token = _bind_screen_to_cell(
            wall, cell, org, now,
            f"Wall {wall['name']} ({row['row_index']},{row['col_index']})",
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
    if wall.get("layout") == "free":
        # A free wall's cell exists only for its TV (POST /walls/{id}/tvs made
        # both), so removing the TV removes the cell. Its index, and with it the
        # "TV n" label, is free for the next TV, and cols follows the highest
        # index left (at least 1: walls.cols is CHECKed 1..8). The wall row is
        # locked, as /tvs locks it, so a TV joining meanwhile sees a
        # consistent set of cells. The wall itself stays, even with no TV
        # left (R18).
        with transaction():
            query_one("SELECT id FROM walls WHERE id = ? FOR UPDATE", (wall_id,))
            cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell["id"],))
            if cell:
                if cell["screen_id"]:
                    execute(
                        "DELETE FROM screens WHERE id = ? AND organization_id = ? "
                        "AND wall_cell_id = ?",
                        (cell["screen_id"], wall["organization_id"], cell["id"]),
                    )
                execute("DELETE FROM wall_cells WHERE id = ?", (cell["id"],))
                execute(
                    """UPDATE walls
                          SET cols = GREATEST(1, COALESCE(
                                  (SELECT MAX(col_index) + 1 FROM wall_cells
                                    WHERE wall_id = ?), 1)),
                              updated_at = ?
                        WHERE id = ?""",
                    (wall_id, utc_now_iso(), wall_id),
                )
    else:
        if cell["screen_id"]:
            # The screen was created by redeem for this cell alone, and its TV is
            # about to be told "bye" (it forgets the token and shows a pairing
            # code). Kept, the row would have no device yet still count toward the
            # plan's screen limit, so each unpair/re-pair leaked a screen.
            execute(
                "DELETE FROM screens WHERE id = ? AND organization_id = ? AND wall_cell_id = ?",
                (cell["screen_id"], wall["organization_id"], cell["id"]),
            )
            execute("UPDATE screens SET wall_cell_id = NULL WHERE id = ?", (cell["screen_id"],))
        execute("UPDATE wall_cells SET screen_id = NULL WHERE id = ?", (cell["id"],))
    # Reaches the TV directly when its socket is on this worker; otherwise the
    # wall's tick loop on the worker holding it notices within seconds. Only
    # after the removal has committed (R5): the tick loop re-reads the cells.
    try:
        from walls import broadcast_bye  # type: ignore
        broadcast_bye(wall_id, row, col, "cell_unpaired")
    except Exception:
        logger.exception("wall_bye_failed wall_id=%s cell=%s,%s", wall_id, row, col)
    return None


# ---- Free walls: video walls set up from a phone ----
#
# A free wall (layout 'free', always mode 'spanned') is built TV by TV from a
# phone standing in front of the screens. Its canvas is its one video, and
# each TV's portion is a box placed by hand, stored in wall_cells.place_* as
# fractions of the canvas. A cell exists only while a TV holds it: row 0,
# col_index the lowest free 0..7, and walls.cols = highest used index + 1, so
# code that walks range(cols) still finds every cell.


class FreeWallCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    media_id: int
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


class WallTvAdd(BaseModel):
    code: str = Field(..., min_length=PAIR_CODE_LENGTH, max_length=PAIR_CODE_LENGTH)


def _free_wall_or_404(wall_id: int, oid: int) -> dict:
    wall = query_one("SELECT * FROM walls WHERE id = ? AND organization_id = ?",
                     (wall_id, oid))
    if not wall:
        raise http_error(404, "wall.not_found", "Wall not found")
    if wall.get("layout") != "free":
        raise http_error(409, "wall.not_free", "This wall was not set up from a phone.")
    return wall


def _free_wall_media_or_raise(media_id: int, oid: int) -> dict:
    """The organization's image or video. Stricter than the canvas playlist's
    _is_allowed_canvas_mime, which also takes PDFs: a free wall's canvas is
    the picture itself."""
    media = query_one("SELECT * FROM media WHERE id = ? AND organization_id = ?",
                      (media_id, oid))
    if not media:
        raise http_error(404, "media.not_found", "Media not found")
    if _media_class(media["mime_type"] or "") not in ("image", "video"):
        raise http_error(400, "wall.media_type_blocked",
                         "A video wall plays one video or image.")
    return media


def _lock_free_wall_media(media_id: int, oid: int) -> dict:
    """The media row read again FOR SHARE, inside the caller's transaction.

    The canvas size and the item's length come from this read. A transcode
    finishing meanwhile stores the new size with an UPDATE that waits for this
    commit, and only then runs _refresh_free_walls_for_media (D3), which then
    finds the wall. Read before the transaction, the size could be the
    upload's while the refresh has already run and missed the new wall.
    """
    media = query_one("SELECT * FROM media WHERE id = ? AND organization_id = ? FOR SHARE",
                      (media_id, oid))
    if not media:
        raise http_error(404, "media.not_found", "Media not found")
    return media


def _free_wall_item_seconds(media: dict) -> int:
    """How long a free wall's single item lasts: the video's probed length,
    so every loop restarts it on all TVs together. playlist_items stores whole
    seconds. Images, and videos whose length is unknown, hold for 10 s."""
    duration = media.get("duration_seconds")
    if (_media_class(media.get("mime_type") or "") == "video"
            and isinstance(duration, (int, float))
            and math.isfinite(duration) and duration > 0):
        return max(1, math.ceil(duration))
    return 10


@app.post("/walls/free", status_code=201)
@limiter.limit("30/minute")
def create_free_wall(request: Request, payload: FreeWallCreate,
                     user: dict = Depends(require_roles("admin", "editor")),
                     _sub: dict = Depends(require_active_subscription)) -> dict:
    """Start a video wall from a phone: the wall, its video and its first TV.

    ``code`` is the standalone pairing code the first TV shows in its QR. One
    transaction claims it, creates the wall (canvas = the media's size, or
    3840x2160 when unknown), its canvas playlist with the one item, and cell
    (0, 0) with the TV's screen, then points the code at that screen so the
    TV's next poll collects its token. A refused code, the wrong media or a
    full plan leaves nothing behind, and the code keeps working. The limit
    (R4) caps a script guessing codes: any pending TV code can be claimed.
    """
    oid = org_id(user)
    now = datetime.now(timezone.utc)
    code_row = _tv_code_row_or_raise(payload.code, now)
    _free_wall_media_or_raise(payload.media_id, oid)    # refuse early, before any lock
    stamp = now.isoformat()
    with transaction():
        _take_tv_code(code_row["id"], now)
        org = _lock_org(oid)
        media = _lock_free_wall_media(payload.media_id, oid)
        canvas_w, canvas_h = wall_layout.canvas_size_for(media.get("width_px"),
                                                         media.get("height_px"))
        box = wall_layout.first_box(canvas_w, canvas_h)
        playlist_id = execute(
            "INSERT INTO playlists (organization_id, name, kind, created_at) "
            "VALUES (?, ?, 'wall_canvas', ?)",
            (oid, f"Canvas: {payload.name}", stamp),
        )
        wall_id = execute(
            """INSERT INTO walls (organization_id, name, mode, layout, rows, cols,
                   canvas_width_px, canvas_height_px, bezel_enabled,
                   bezel_h_pct, bezel_v_pct, spanned_playlist_id,
                   created_at, updated_at)
               VALUES (?, ?, 'spanned', 'free', 1, 1, ?, ?, false, 0, 0, ?, ?, ?)""",
            (oid, payload.name, canvas_w, canvas_h, playlist_id, stamp, stamp),
        )
        execute(
            """INSERT INTO playlist_items
                   (playlist_id, media_id, position, duration_seconds,
                    duration_override_seconds, fit_mode, created_at)
               VALUES (?, ?, 0, ?, NULL, 'fit', ?)""",
            (playlist_id, media["id"], _free_wall_item_seconds(media), stamp),
        )
        cell_id = execute(
            """INSERT INTO wall_cells (wall_id, row_index, col_index,
                   place_x, place_y, place_w, place_h, created_at)
               VALUES (?, 0, 0, ?, ?, ?, ?, ?)""",
            (wall_id, box["x"], box["y"], box["w"], box["h"], stamp),
        )
        wall = query_one("SELECT * FROM walls WHERE id = ?", (wall_id,))
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
        screen_id, _token = _bind_screen_to_cell(wall, cell, org, now,
                                                 f"{wall['name']} · TV 1")
        _attach_tv_code(code_row["id"], screen_id)
    audit(request, action="wall.create", actor=user,
          target_type="wall", target_id=wall_id,
          details={"name": payload.name, "mode": "spanned", "layout": "free",
                   "media_id": media["id"]})
    return serialize_wall(wall)


@app.post("/walls/{wall_id}/tvs", status_code=201)
@limiter.limit("30/minute")
def add_wall_tv(request: Request, wall_id: int, payload: WallTvAdd,
                user: dict = Depends(require_roles("admin", "editor")),
                _sub: dict = Depends(require_active_subscription)) -> dict:
    """Add the TV showing ``code`` to a free wall, at its lowest free index.

    Its first box has the height of the most recently added TV's box in the
    default 16:9 shape (R3), flush to that box's right edge and top-aligned:
    for identical 16:9 TVs that is the previous box's size, and next to a TV
    refit to portrait it is still 16:9, so this TV's own screen report can
    refit it. If less than 5% of it would then be on the video, it moves back
    until 5% is. The wall row is locked before the index is chosen, after the
    organization (the order every wall transaction uses), so two phones
    adding TVs at once get different indexes rather than a unique-constraint
    500. Rate-limited like POST /walls/free (R4).
    """
    oid = org_id(user)
    now = datetime.now(timezone.utc)
    _free_wall_or_404(wall_id, oid)
    code_row = _tv_code_row_or_raise(payload.code, now)
    stamp = now.isoformat()
    with transaction():
        _take_tv_code(code_row["id"], now)
        org = _lock_org(oid)
        wall = query_one(
            "SELECT * FROM walls WHERE id = ? AND organization_id = ? FOR UPDATE",
            (wall_id, oid),
        )
        if not wall:
            raise http_error(404, "wall.not_found", "Wall not found")
        # A screen deleted from the Screens page leaves its cell behind with no
        # screen (ON DELETE SET NULL): a box with no TV, holding its index
        # forever. Clear such cells before choosing.
        execute("DELETE FROM wall_cells WHERE wall_id = ? AND screen_id IS NULL", (wall_id,))
        cells = query_all("SELECT * FROM wall_cells WHERE wall_id = ? ORDER BY id", (wall_id,))
        index = wall_layout.lowest_free_index({c["col_index"] for c in cells})
        if index is None:
            raise http_error(409, "wall.full",
                             f"A video wall holds up to {wall_layout.MAX_TVS} TVs.")
        placed = [c for c in cells
                  if None not in (c["place_x"], c["place_y"], c["place_w"], c["place_h"])]
        canvas_w, canvas_h = wall_layout.canvas_size_for(wall["canvas_width_px"],
                                                         wall["canvas_height_px"])
        if placed:
            last = placed[-1]          # cells are in id order: the most recently added
            box = wall_layout.next_box({"x": last["place_x"], "y": last["place_y"],
                                        "w": last["place_w"], "h": last["place_h"]},
                                       canvas_w, canvas_h)
        else:
            box = wall_layout.first_box(canvas_w, canvas_h)
        cell_id = execute(
            """INSERT INTO wall_cells (wall_id, row_index, col_index,
                   place_x, place_y, place_w, place_h, created_at)
               VALUES (?, 0, ?, ?, ?, ?, ?, ?)""",
            (wall_id, index, box["x"], box["y"], box["w"], box["h"], stamp),
        )
        cols = max([c["col_index"] for c in cells] + [index]) + 1
        execute("UPDATE walls SET cols = ?, updated_at = ? WHERE id = ?",
                (cols, stamp, wall_id))
        cell = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
        screen_id, _token = _bind_screen_to_cell(wall, cell, org, now,
                                                 f"{wall['name']} · TV {index + 1}")
        _attach_tv_code(code_row["id"], screen_id)
    audit(request, action="wall.tv_add", actor=user,
          target_type="wall", target_id=wall_id,
          details={"col_index": index, "screen_id": screen_id})
    out = query_one("SELECT * FROM wall_cells WHERE id = ?", (cell_id,))
    out["online"] = _cell_online_map(wall_id).get(cell_id, False)
    return out


def _refresh_free_walls_for_media(media_id: int) -> None:
    """Size the free walls that play this media to the file they now play.

    Called when transcode_video finishes (D3). POST /walls/free usually runs
    before that, so the canvas came from the upload's probe (5760x1080, say),
    while the transcoded file is capped at 3840 px (3840x720). The item's
    length likewise came from the original. Boxes are fractions of the canvas,
    so they keep their place. Grid walls are not touched. Best effort: a
    failure is logged and never undoes the finished transcode.
    """
    try:
        media = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
        if not media:
            return
        playlist_ids = [r["spanned_playlist_id"] for r in query_all(
            """SELECT DISTINCT w.spanned_playlist_id
               FROM walls w JOIN playlist_items pi ON pi.playlist_id = w.spanned_playlist_id
               WHERE w.layout = 'free' AND pi.media_id = ?""",
            (media_id,),
        )]
        if not playlist_ids:
            return
        if media.get("width_px") and media.get("height_px"):
            canvas_w, canvas_h = wall_layout.canvas_size_for(media["width_px"],
                                                             media["height_px"])
            execute(
                "UPDATE walls SET canvas_width_px = ?, canvas_height_px = ?, updated_at = ? "
                "WHERE layout = 'free' AND spanned_playlist_id = ANY(?)",
                (canvas_w, canvas_h, utc_now_iso(), playlist_ids),
            )
        execute(
            "UPDATE playlist_items SET duration_seconds = ? "
            "WHERE media_id = ? AND playlist_id = ANY(?)",
            (_free_wall_item_seconds(media), media_id, playlist_ids),
        )
    except Exception:
        logger.exception("free_wall_refresh_failed media_id=%s", media_id)


class WallPlacement(BaseModel):
    # Plain floats (ruling R2): wall_layout.validate_box decides -- finite,
    # 0 < w <= 4, 0 < h <= 4 and at least 5% on the video -- so every refusal
    # is 422 wall.placement_invalid, the code the phone translates.
    x: float
    y: float
    w: float
    h: float
    highlight: bool = True

    @field_validator("x", "y", "w", "h", mode="before")
    @classmethod
    def _int_beyond_float_range(cls, value):
        # Pydantic refuses a JSON integer too big for a float with its own
        # list-detail 422. As an infinity it reaches validate_box and gets
        # the same code as any other box that is not on the video.
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                return float(value)
            except OverflowError:
                return math.inf if value > 0 else -math.inf
        return value


class WallVideoUpdate(BaseModel):
    media_id: int


@app.put("/walls/{wall_id}/cells/{row}/{col}/placement", status_code=204)
@limiter.limit("1800/minute")
def put_wall_placement(request: Request, wall_id: int, row: int, col: int,
                       payload: WallPlacement,
                       user: dict = Depends(require_roles("admin", "editor")),
                       _sub: dict = Depends(require_active_subscription)) -> None:
    """Move or resize one TV's box while the phone drags it.

    The phone keeps one request in flight and sends the newest box when it
    returns, so a drag runs this several times a second: two short reads and
    one UPDATE, with no transaction and no audit. Once the TV has reported its
    screen size, the stored box is the requested one in the TV's shape (same
    width and centre, R8), so a phone that has not seen the report yet cannot
    stretch the picture. The TV hears of it through publish_geometry (Redis,
    to whichever worker holds its socket), or from that worker's tick loop
    within 5 s when Redis is down. ``highlight`` outlines the TV and shows its
    number for HIGHLIGHT_SECONDS after the last move; the phone sends false on
    Done, which clears it at once.
    """
    import walls as walls_mod

    wall = _free_wall_or_404(wall_id, org_id(user))
    cell = query_one(
        "SELECT id, screen_w_px, screen_h_px FROM wall_cells "
        "WHERE wall_id = ? AND row_index = ? AND col_index = ?",
        (wall_id, row, col),
    )
    if not cell:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    box = {"x": payload.x, "y": payload.y, "w": payload.w, "h": payload.h}
    # Validate what the phone sent first: fit_box_to_screen nudges a box back
    # onto the video, which would turn an invalid request into a valid one.
    if not wall_layout.validate_box(box):
        raise http_error(422, "wall.placement_invalid",
                         "At least 5% of the TV's box must be on the video.")
    if cell["screen_w_px"] and cell["screen_h_px"]:
        canvas_w, canvas_h = wall_layout.canvas_size_for(wall["canvas_width_px"],
                                                         wall["canvas_height_px"])
        box = wall_layout.fit_box_to_screen(box, cell["screen_w_px"], cell["screen_h_px"],
                                            canvas_w, canvas_h)
        if not wall_layout.validate_box(box):
            raise http_error(422, "wall.placement_invalid",
                             "At least 5% of the TV's box must be on the video.")
    highlight_until_ms = (
        (walls_mod.now_ms() + walls_mod.HIGHLIGHT_SECONDS * 1000) if payload.highlight else None
    )
    updated = execute(
        "UPDATE wall_cells SET place_x = ?, place_y = ?, place_w = ?, place_h = ?, "
        "highlight_until_ms = ? WHERE id = ?",
        (box["x"], box["y"], box["w"], box["h"], highlight_until_ms, cell["id"]),
    )
    if not updated:
        raise http_error(404, "wall.cell_not_found", "Cell not found")
    # The UPDATE ran in autocommit, so it has committed: the local push and
    # the other workers re-read the box from the database (R5).
    walls_mod.publish_geometry(wall_id, row, col)
    return None


@app.put("/walls/{wall_id}/video")
def put_wall_video(request: Request, wall_id: int, payload: WallVideoUpdate,
                   user: dict = Depends(require_roles("admin", "editor")),
                   _sub: dict = Depends(require_active_subscription)) -> dict:
    """Swap a free wall's video.

    The canvas takes the new media's size, and the boxes keep their fractions,
    so each TV shows the same part of the new picture. The tick loop's config
    check re-sends hello when the size changed. The item is deleted and
    re-inserted rather than updated: its new id makes the TVs switch at their
    next tick even when the new video has the old one's size and length (D8).
    """
    oid = org_id(user)
    _free_wall_or_404(wall_id, oid)
    _free_wall_media_or_raise(payload.media_id, oid)    # refuse early, before any lock
    stamp = utc_now_iso()
    with transaction():
        # Same lock order as /tvs (organization, then wall). Recreating a
        # missing canvas playlist below INSERTs a row that references the
        # organization.
        _lock_org(oid)
        wall = query_one(
            "SELECT * FROM walls WHERE id = ? AND organization_id = ? FOR UPDATE",
            (wall_id, oid),
        )
        if not wall:
            raise http_error(404, "wall.not_found", "Wall not found")
        # Size and length from a FOR SHARE read: a transcode finishing now
        # waits for this commit, and its refresh (D3) then finds this wall.
        media = _lock_free_wall_media(payload.media_id, oid)
        canvas_w, canvas_h = wall_layout.canvas_size_for(media.get("width_px"),
                                                         media.get("height_px"))
        playlist_id = wall.get("spanned_playlist_id")
        if not playlist_id:
            # Its canvas playlist was deleted (walls.spanned_playlist_id is
            # ON DELETE SET NULL): give the wall a new one.
            playlist_id = execute(
                "INSERT INTO playlists (organization_id, name, kind, created_at) "
                "VALUES (?, ?, 'wall_canvas', ?)",
                (oid, f"Canvas: {wall['name']}", stamp),
            )
        execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        execute(
            """INSERT INTO playlist_items
                   (playlist_id, media_id, position, duration_seconds,
                    duration_override_seconds, fit_mode, created_at)
               VALUES (?, ?, 0, ?, NULL, 'fit', ?)""",
            (playlist_id, media["id"], _free_wall_item_seconds(media), stamp),
        )
        execute(
            "UPDATE walls SET canvas_width_px = ?, canvas_height_px = ?, "
            "spanned_playlist_id = ?, updated_at = ? WHERE id = ?",
            (canvas_w, canvas_h, playlist_id, stamp, wall_id),
        )
    audit(request, action="wall.video", actor=user,
          target_type="wall", target_id=wall_id,
          details={"media_id": media["id"]})
    return serialize_wall(query_one("SELECT * FROM walls WHERE id = ?", (wall_id,)))


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


def _refuse_free_wall_canvas_edit(wall: dict) -> None:
    """A free wall's canvas playlist is its one video (D8): only
    PUT /walls/{id}/video changes it. Reading it stays allowed."""
    if wall.get("layout") == "free":
        raise http_error(409, "wall.free_layout",
                         "A video wall set up from a phone plays one video; "
                         "change it with PUT /walls/{id}/video.")


def _refuse_free_wall_playlist_edit(playlist_id: int) -> None:
    """D8 through the generic playlist endpoints. GET /playlists lists a free
    wall's 'Canvas: <name>' playlist like any other, and the Playlists page and
    the MCP playlist tools act on it by id; adding or removing an item, or
    deleting the playlist, would break 'one item, changed only by PUT /video'."""
    wall = query_one(
        "SELECT id, layout FROM walls WHERE spanned_playlist_id = ? AND layout = 'free'",
        (playlist_id,),
    )
    if wall:
        _refuse_free_wall_canvas_edit(wall)


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
    _refuse_free_wall_canvas_edit(wall)
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
    _refuse_free_wall_canvas_edit(wall)
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
    _refuse_free_wall_canvas_edit(wall)
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
    _refuse_free_wall_playlist_edit(playlist_id)
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
        # Not media.duration_seconds, although uploads now store the probed
        # length: standalone playlists keep the 10 s default they always had
        # (a probed two-hour video would otherwise get a 7200 s item, past
        # PlaylistItemCreate's own 3600 s cap). Only a free video wall's one
        # canvas item plays for the probed length.
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
    _refuse_free_wall_playlist_edit(playlist_id)
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


# ── Menus (Plan A) ────────────────────────────────────────────────────────

class MenuCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    template: str = "dark-classic"


@app.get("/ai/capabilities")
def ai_capabilities() -> dict:
    """Feature flags the dashboard uses to hide what isn't configured."""
    return {
        "menus":       bool(RENDERER_URL),
        "menu_import": bool(RENDERER_URL) and bool(os.getenv("ANTHROPIC_API_KEY", "")),
    }


@app.get("/menus/templates")
def list_menu_templates(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    from menu_render import list_templates  # Task 4; static fallback until then
    return {"items": list_templates()}


@app.get("/menus")
def list_menus_endpoint(
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    return {"items": menus_domain.list_menus(principal.organization_id)}


@app.post("/menus", status_code=201)
def create_menu_endpoint(
    payload: MenuCreate,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    if payload.template not in menus_domain.TEMPLATE_IDS:
        raise http_error(400, "menu.bad_template", "Unknown template")
    menu_id = menus_domain.create_menu(principal.organization_id, payload.name, payload.template)
    audit(request, action="menu.create", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    return menus_domain.get_menu_tree(principal.organization_id, menu_id)


@app.get("/menus/{menu_id}")
def get_menu_endpoint(
    menu_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    tree = menus_domain.get_menu_tree(principal.organization_id, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    tree["renders"] = _current_renders(menu_id)  # Task 6 fills this; returns [] until then
    return tree


@app.get("/menus/{menu_id}/preview", response_class=HTMLResponse)
def preview_menu_endpoint(
    menu_id: int,
    kind: str = "board",
    language: str = "en",
    aspect: str = "16:9",
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
):
    """The menu as HTML, exactly as the renderer would receive it.

    Deliberately does NOT go through the Playwright renderer: an author looking
    at their own menu should not wait on a render job, and a signup-seeded
    sample must be viewable on a deployment whose renderer is down. Rendering
    stays required only to put a menu onto a screen.
    """
    tree = menus_domain.get_menu_tree(principal.organization_id, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    try:
        tpl = menu_render.get_template(tree["template"])
    except KeyError:
        raise http_error(500, "menu.bad_template",
                         f"Menu references unknown template {tree['template']!r}")

    # Validate against what THIS template declares rather than the global sets:
    # a template that only ships 16:9 should not silently render a squashed 9:16.
    if kind not in tpl.get("kinds", []):
        raise http_error(400, "menu.bad_kind",
                         f"Template '{tree['template']}' supports kinds: {', '.join(tpl.get('kinds', []))}")
    if aspect not in tpl.get("aspects", []):
        raise http_error(400, "menu.bad_aspect",
                         f"Template '{tree['template']}' supports aspects: {', '.join(tpl.get('aspects', []))}")
    if language not in menu_render.LANGUAGES:
        raise http_error(400, "menu.bad_language",
                         f"Language must be one of: {', '.join(menu_render.LANGUAGES)}")

    logo_url = menu_render.resolve_logo_data_uri(
        principal.organization_id, menu_id, tree["brand"], UPLOAD_DIR)
    html = menu_render.build_html(tree, tree["template"], kind, language, aspect,
                                  logo_url=logo_url)
    # no-store: a preview must reflect the edit that was just made.
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.put("/menus/{menu_id}")
def update_menu_endpoint(
    menu_id: int,
    payload: menus_domain.MenuTreeIn,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    tree = menus_domain.replace_menu_tree(principal.organization_id, menu_id, payload)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    audit(request, action="menu.update", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    tree["renders"] = _current_renders(menu_id)
    return tree


@app.delete("/menus/{menu_id}", status_code=204)
def delete_menu_endpoint(
    menu_id: int,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
):
    if not menus_domain.delete_menu(principal.organization_id, menu_id):
        raise http_error(404, "menu.not_found", "Menu not found")
    audit(request, action="menu.delete", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id)
    return Response(status_code=204)


class RenderRequest(BaseModel):
    languages: list[str] = Field(default_factory=lambda: ["en", "ar"])
    aspects:   list[str] = Field(default_factory=lambda: ["16:9"])
    kinds:     list[str] = Field(default_factory=lambda: ["board"])


@app.post("/menus/{menu_id}/render", status_code=202)
async def render_menu_endpoint(
    menu_id: int,
    payload: RenderRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    from menu_render import plan_renders, run_render_job
    if not RENDERER_URL:
        raise http_error(503, "menu.renderer_unavailable", "Rendering is not configured on this server")
    tree = menus_domain.get_menu_tree(principal.organization_id, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    try:
        specs = plan_renders(tree, tree["template"], payload.languages, payload.aspects, payload.kinds)
    except ValueError as exc:
        raise http_error(400, "menu.bad_render_request", str(exc))
    if len(specs) > MAX_BOARDS_PER_REQUEST:
        raise http_error(400, "menu.too_many_boards",
                         f"That would render {len(specs)} boards; narrow the languages, aspects or kinds "
                         f"(max {MAX_BOARDS_PER_REQUEST}).")
    background_tasks.add_task(run_render_job, principal.organization_id, menu_id, specs,
                              renderer_url=RENDERER_URL, renderer_token=RENDERER_TOKEN, upload_dir=UPLOAD_DIR)
    audit(request, action="menu.render", actor=principal.user, target_type="menu",
          target_id=menu_id, organization_id=principal.organization_id,
          details={"boards": len(specs)})
    return {"queued": len(specs)}


@app.get("/menus/{menu_id}/renders")
def list_menu_renders_endpoint(
    menu_id: int,
    principal: AuthedPrincipal = Depends(require_api_scope("api:read", "api:rw")),
) -> dict:
    if not menus_domain.get_menu_tree(principal.organization_id, menu_id):
        raise http_error(404, "menu.not_found", "Menu not found")
    return {"items": _current_renders(menu_id)}


_KIND_ORDER = {"board": 0, "category": 1, "promo": 2}
_LANG_ORDER = {"en": 0, "ar": 1, "bi": 2}


@app.post("/menus/{menu_id}/playlist")
def menu_playlist_endpoint(
    menu_id: int,
    request: Request,
    principal: AuthedPrincipal = Depends(require_api_scope("api:rw", session_roles=("admin", "editor"))),
    _sub: dict = Depends(require_active_subscription),
) -> dict:
    oid = principal.organization_id
    tree = menus_domain.get_menu_tree(oid, menu_id)
    if not tree:
        raise http_error(404, "menu.not_found", "Menu not found")
    renders = [r for r in _current_renders(menu_id) if r["status"] == "ready" and r["media_id"]]
    if not renders:
        raise http_error(409, "menu.no_boards", "Render the boards before creating a playlist")
    cat_pos = {c["id"]: i for i, c in enumerate(tree["categories"])}
    renders.sort(key=lambda r: (_KIND_ORDER[r["kind"]], cat_pos.get(r["category_id"], 0),
                                r["item_id"] or 0, _LANG_ORDER[r["language"]], r["aspect"]))
    name = f"Menu — {tree['name']}"
    playlist = None
    if tree.get("playlist_id"):
        playlist = query_one("SELECT * FROM playlists WHERE id = ? AND organization_id = ?",
                             (tree["playlist_id"], oid))
    if not playlist:
        pid = execute("INSERT INTO playlists (organization_id, name, created_at) VALUES (?, ?, ?)",
                      (oid, name, utc_now_iso()))
        execute("UPDATE menus SET playlist_id = ? WHERE id = ?", (pid, menu_id))
    else:
        pid = playlist["id"]
        execute("UPDATE playlists SET name = ? WHERE id = ?", (name, pid))
        execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
    for pos, r in enumerate(renders, start=1):
        execute("INSERT INTO playlist_items (playlist_id, media_id, duration_seconds, position, created_at) "
                "VALUES (?, ?, 10, ?, ?)", (pid, r["media_id"], pos, utc_now_iso()))
    audit(request, action="menu.playlist", actor=principal.user, target_type="playlist",
          target_id=pid, organization_id=oid, details={"menu_id": menu_id, "boards": len(renders)})
    out = query_one("SELECT * FROM playlists WHERE id = ?", (pid,))
    out["items"] = query_all(
        "SELECT pi.id, pi.duration_seconds, pi.position, m.id AS media_id, m.name, m.filename "
        "FROM playlist_items pi JOIN media m ON m.id = pi.media_id WHERE pi.playlist_id = ? ORDER BY pi.position",
        (pid,))
    return out


def _current_renders(menu_id: int) -> list[dict]:
    """Latest render per (kind, category, item, language, aspect). Populated by Task 6."""
    rows = query_all(
        """
        SELECT DISTINCT ON (kind, COALESCE(category_id, 0), COALESCE(item_id, 0), language, aspect)
               r.id, r.kind, r.category_id, r.item_id, r.language, r.aspect, r.status,
               r.media_id, r.render_hash, r.error, r.created_at, m.filename
        FROM menu_renders r LEFT JOIN media m ON m.id = r.media_id
        WHERE r.menu_id = ?
        ORDER BY kind, COALESCE(category_id, 0), COALESCE(item_id, 0), language, aspect, r.created_at DESC
        """,
        (menu_id,),
    )
    for r in rows:
        r["url"] = f"/uploads/{r['filename']}" if r.get("filename") else None
        r.pop("filename", None)
    return rows


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
    _refuse_free_wall_playlist_edit(playlist_id)
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


import mimetypes

# What /media/upload accepts: extension -> the type it is served as. /uploads
# is served from the API origin, so an uploaded .html or script would run there
# (stored XSS); the player only renders images, videos and PDFs anyway.
# StaticFiles picks Content-Type from the extension, which is why the extension
# itself must be on this list, not just the client's Content-Type claim.
UPLOAD_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".bmp": "image/bmp", ".svg": "image/svg+xml",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    # Containers browsers cannot play but ffmpeg reads: transcode_video turns
    # every video upload into an H.264 .opt.mp4, which is what the player gets.
    ".3gp": "video/3gpp", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".ts": "video/mp2t",
    ".mts": "video/mp2t", ".m2ts": "video/mp2t", ".ogv": "video/ogg",
    ".pdf": "application/pdf",
}
# The image's mimetypes table lacks some of these (.webp, .m4v, .mkv); without
# them StaticFiles serves text/plain, which nosniff makes the player refuse.
for _ext, _mime in UPLOAD_TYPES.items():
    mimetypes.add_type(_mime, _ext)


def _media_class(mime: str) -> str | None:
    mime = mime.split(";")[0].strip().lower()
    if mime == "application/pdf":
        return "pdf"
    major = mime.split("/")[0]
    return major if major in ("image", "video") else None


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
    ext = os.path.splitext(file.filename or "")[1].lower()
    content_type = file.content_type or "application/octet-stream"
    # A generic type is no claim at all (curl -F sends it for .mp4): take the
    # extension's own. Otherwise the claim and the extension must agree, so a
    # row never says "image" for a file served as something else.
    if content_type.split(";")[0].strip().lower() == "application/octet-stream":
        content_type = UPLOAD_TYPES.get(ext, content_type)
    ext_class = _media_class(UPLOAD_TYPES.get(ext, ""))
    if ext_class is None or _media_class(content_type) != ext_class:
        raise http_error(415, "media.unsupported_type",
                         "Only images, videos and PDFs can be uploaded.")
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)
    # Size and length before answering: a free video wall's canvas is its
    # video's size. In a worker thread, since ffprobe may take up to 20 s and
    # this worker's event loop also carries the wall sockets.
    probed = await anyio.to_thread.run_sync(media_probe.probe_media, file_path, content_type)

    media_id = execute(
        """
        INSERT INTO media (organization_id, name, filename, mime_type, size, created_at,
                           width_px, height_px, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            principal.organization_id,
            file.filename or filename,
            filename,
            content_type,
            len(contents),
            utc_now_iso(),
            probed["width_px"],
            probed["height_px"],
            probed["duration_seconds"],
        ),
    )
    # _media_class, not startswith("video"): the stored claim keeps the
    # client's case, and a "VIDEO/MP4" upload must still be transcoded (and
    # capped at 3840 px).
    if _media_class(content_type) == "video":
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
                (f"niupay_bad_response: {resp.get('message') or str(resp)[:200]}",
                 trackid))
        raise HTTPException(status_code=502, detail="Payment gateway rejected the request")

    execute(
        "UPDATE payments SET niupay_payment_id=?, niupay_payment_link=?, updated_at=now() WHERE trackid=?",
        (payment_id, payment_link, trackid),
    )
    return {"payment_url": payment_link, "trackid": trackid}


class BillingCallbackBody(BaseModel):
    # A JSON callback may carry the ids as numbers; store them as text.
    model_config = {"coerce_numbers_to_str": True}

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
    # NiuPay's request endpoint accepts only form-encoding (see billing.py), so
    # its callback may arrive as a form too: a form body and a JSON one get the
    # same handling. Verification above is on the raw bytes either way.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        body_dict = {k: v for k, v in form.items() if isinstance(v, str)}
    else:
        try:
            body_dict = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body_dict, dict):
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
        # The status check above is only a fast path: two deliveries of one
        # callback can both pass it. Only the one whose UPDATE moves the row out
        # of pending extends the org, in the same transaction -- extending
        # paid_through_at is not idempotent.
        with transaction():
            claimed = execute(
                """
                UPDATE payments
                   SET status='captured',
                       niupay_result=?, niupay_tranid=?, niupay_ref=?, niupay_payment_id=?,
                       updated_at=now()
                 WHERE trackid=? AND status NOT IN ('captured', 'failed')
                """,
                (body.result, body.tranid, body.ref, body.paymentID, trackid),
            )
            if claimed:
                old_plan_row = query_one("SELECT plan FROM organizations WHERE id = ? FOR UPDATE",
                                         (row["organization_id"],))
                old_plan = old_plan_row["plan"] if old_plan_row else None
                # Paid time still left is credit, not forfeited (renewing early is
                # what the renewal_7day email asks for). It carries over as-is on
                # the same tier and is converted at the two tiers' monthly prices
                # on a change of tier -- so an upgrade cannot buy the rest of a
                # year at the dearer tier for one month's price. An 'active' org
                # with no paid_through_at has no expiry (first-run org, admin
                # override) and keeps none.
                old_price = PLAN_PRICING_KWD.get(old_plan)
                credit_ratio = float(old_price / PLAN_PRICING_KWD[row["tier"]]) if old_price else 1.0
                execute(
                    """
                    UPDATE organizations
                       SET plan               = ?,
                           plan_term_months   = ?,
                           screen_limit       = ?,
                           subscription_status= 'active',
                           paid_through_at    = CASE
                               WHEN subscription_status = 'active' AND paid_through_at IS NULL THEN NULL
                               ELSE now() + GREATEST(paid_through_at - now(), interval '0') * ?
                                          + make_interval(days => ?)
                           END
                     WHERE id = ?
                    """,
                    (row["tier"], term, PLAN_SCREEN_LIMITS[row["tier"]], credit_ratio,
                     term * TERM_DAYS, row["organization_id"]),
                )
        if claimed:
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
             WHERE trackid=? AND status NOT IN ('captured', 'failed')
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
