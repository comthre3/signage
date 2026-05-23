"""Phase 2.5j — Social auth (Google + Apple)."""
from __future__ import annotations

import os
import time
import json
import secrets
import logging
from typing import Optional, Any
from pathlib import Path

import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

logger = logging.getLogger("signage.social_auth")


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY env var must be set")
    return key


def _api_base_url() -> str:
    return os.getenv("API_BASE_URL", "https://api.khanshoof.com").rstrip("/")


def _app_url() -> str:
    return os.getenv("APP_URL", "https://app.khanshoof.com").rstrip("/")


# ── Signed state for CSRF ──────────────────────────────────────────────


def _sign_state(provider: str, intent: str, return_to: str,
                ttl_seconds: int = 300) -> str:
    """Sign a state token for the OAuth CSRF cookie + URL parameter."""
    now = int(time.time())
    payload = {
        "kind": "social_state",
        "provider": provider,
        "intent": intent,
        "return_to": return_to or "/",
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def _verify_state(state: str) -> dict:
    """Verify and decode a state token. Raises on invalid/expired."""
    payload = jwt.decode(state, _secret_key(), algorithms=["HS256"],
                         options={"require": ["exp", "iat", "kind"]})
    if payload.get("kind") != "social_state":
        raise jwt.InvalidTokenError("Wrong state token kind")
    return payload


# ── Signed stash for "complete signup" handoff ─────────────────────────


def _sign_stash(provider: str, subject_id: str, email: str,
                name: Optional[str], ttl_seconds: int = 600) -> str:
    now = int(time.time())
    payload = {
        "kind": "social_stash",
        "provider": provider,
        "subject_id": subject_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def _verify_stash(token: str) -> dict:
    payload = jwt.decode(token, _secret_key(), algorithms=["HS256"],
                         options={"require": ["exp", "iat", "kind"]})
    if payload.get("kind") != "social_stash":
        raise jwt.InvalidTokenError("Wrong stash token kind")
    return payload


# ── Apple client_secret JWT ────────────────────────────────────────────


_apple_secret_cache: dict = {"secret": None, "exp": 0}


def _load_apple_private_key() -> bytes:
    path = os.getenv("APPLE_PRIVATE_KEY_PATH")
    if not path:
        raise RuntimeError("APPLE_PRIVATE_KEY_PATH not set")
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Apple private key file not found at {path}")
    return p.read_bytes()


def _apple_client_secret_jwt() -> str:
    """Mint a fresh client_secret JWT for Apple's token endpoint.

    Cached 25min, regenerated before the 30min expiry."""
    now = int(time.time())
    cached = _apple_secret_cache
    if cached["secret"] and cached["exp"] > now + 60:
        return cached["secret"]

    team_id = os.getenv("APPLE_TEAM_ID")
    key_id = os.getenv("APPLE_KEY_ID")
    client_id = os.getenv("APPLE_CLIENT_ID")
    if not all([team_id, key_id, client_id]):
        raise RuntimeError("Apple env vars not configured")

    exp = now + 1500  # 25 minutes
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": exp,
        "aud": "https://appleid.apple.com",
        "sub": client_id,
    }
    secret = jwt.encode(payload, _load_apple_private_key(),
                        algorithm="ES256", headers={"kid": key_id})
    _apple_secret_cache["secret"] = secret
    _apple_secret_cache["exp"] = exp
    return secret


# ── JWKS cache + ID token verification ─────────────────────────────────


_jwks_cache: dict = {}   # url -> {keys, fetched_at}
_JWKS_TTL_SECONDS = 86400


def _clear_jwks_cache() -> None:
    """Test helper: drop the cache so respx mocks fire on next verify."""
    _jwks_cache.clear()
    _apple_secret_cache["secret"] = None
    _apple_secret_cache["exp"] = 0


async def _fetch_jwks(url: str) -> dict:
    now = time.time()
    cached = _jwks_cache.get(url)
    if cached and cached["fetched_at"] + _JWKS_TTL_SECONDS > now:
        return cached["jwks"]
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        jwks = r.json()
    _jwks_cache[url] = {"jwks": jwks, "fetched_at": now}
    return jwks


async def _verify_id_token(id_token: str, *, jwks_url: str, algorithm: str,
                            audience: str, issuer: list[str] | str) -> dict:
    jwks = await _fetch_jwks(jwks_url)
    header = jwt.get_unverified_header(id_token)
    key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")),
               None)
    if not key:
        raise HTTPException(status_code=400, detail={
            "code": "key_not_found",
            "message": "ID token signing key not in provider JWKS",
        })
    public_key = jwt.PyJWK(key).key
    try:
        payload = jwt.decode(id_token, public_key,
                             algorithms=[algorithm],
                             audience=audience, issuer=issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_id_token",
            "message": f"ID token verification failed: {exc}",
        })
    return payload


async def verify_google_id_token(id_token: str, audience: str) -> dict:
    payload = await _verify_id_token(
        id_token,
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        algorithm="RS256",
        audience=audience,
        issuer=["accounts.google.com", "https://accounts.google.com"],
    )
    if not payload.get("email_verified"):
        raise HTTPException(status_code=400, detail={
            "code": "email_not_verified",
            "message": "Google has not verified this email",
        })
    return payload


async def verify_apple_id_token(id_token: str, audience: str) -> dict:
    # Apple always sets email_verified=true (they own the email)
    return await _verify_id_token(
        id_token,
        jwks_url="https://appleid.apple.com/auth/keys",
        algorithm="ES256",
        audience=audience,
        issuer="https://appleid.apple.com",
    )


# ── Endpoint mounting ──────────────────────────────────────────────────


from fastapi import APIRouter, Request, Form, Query

router = APIRouter()


_STATE_COOKIE = "auth_csrf"
_STATE_TTL = 300
_STASH_TTL = 600


def _provider_configured(provider: str) -> bool:
    if provider == "google":
        return bool(os.getenv("GOOGLE_CLIENT_ID")
                    and os.getenv("GOOGLE_CLIENT_SECRET"))
    if provider == "apple":
        return bool(os.getenv("APPLE_CLIENT_ID")
                    and os.getenv("APPLE_TEAM_ID")
                    and os.getenv("APPLE_KEY_ID")
                    and os.getenv("APPLE_PRIVATE_KEY_PATH"))
    return False


def _provider_not_configured() -> HTTPException:
    return HTTPException(status_code=503, detail={
        "code": "provider_not_configured",
        "message": "This sign-in provider is not configured on the server.",
    })


def _google_redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI",
                     f"{_api_base_url()}/auth/google/callback")


def _apple_redirect_uri() -> str:
    return os.getenv("APPLE_REDIRECT_URI",
                     f"{_api_base_url()}/auth/apple/callback")


def _build_google_authorize_url(state: str) -> str:
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def _build_apple_authorize_url(state: str) -> str:
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": os.getenv("APPLE_CLIENT_ID"),
        "redirect_uri": _apple_redirect_uri(),
        "response_type": "code id_token",
        "response_mode": "form_post",
        "scope": "name email",
        "state": state,
    })
    return f"https://appleid.apple.com/auth/authorize?{params}"


@router.get("/auth/providers")
def auth_providers() -> dict:
    """Report which social-auth providers have env vars configured.
    Frontend uses this to hide buttons for disabled providers."""
    return {
        "google": _provider_configured("google"),
        "apple":  _provider_configured("apple"),
    }


@router.get("/auth/google/start")
def google_start(intent: str = Query("signup"),
                 return_to: str = Query("/")):
    if not _provider_configured("google"):
        raise _provider_not_configured()
    state = _sign_state(provider="google", intent=intent,
                        return_to=return_to, ttl_seconds=_STATE_TTL)
    resp = RedirectResponse(_build_google_authorize_url(state),
                            status_code=302)
    resp.set_cookie(
        _STATE_COOKIE, state,
        httponly=True, samesite="lax", max_age=_STATE_TTL,
        secure=_api_base_url().startswith("https://"),
    )
    return resp


@router.get("/auth/apple/start")
def apple_start(intent: str = Query("signup"),
                return_to: str = Query("/")):
    if not _provider_configured("apple"):
        raise _provider_not_configured()
    state = _sign_state(provider="apple", intent=intent,
                        return_to=return_to, ttl_seconds=_STATE_TTL)
    resp = RedirectResponse(_build_apple_authorize_url(state),
                            status_code=302)
    resp.set_cookie(
        _STATE_COOKIE, state,
        httponly=True, samesite="lax", max_age=_STATE_TTL,
        secure=_api_base_url().startswith("https://"),
    )
    return resp


def _validate_csrf(request: Request, state: str) -> dict:
    cookie_state = request.cookies.get(_STATE_COOKIE)
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_state",
            "message": "CSRF state cookie missing or mismatched.",
        })
    try:
        return _verify_state(state)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_state",
            "message": f"State token invalid: {exc}",
        })


def _issue_session(user_id: int) -> str:
    """Mirror /auth/login's session-issue pattern."""
    import uuid
    from db import execute, utc_now_iso
    token = uuid.uuid4().hex
    now = utc_now_iso()
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) "
        "VALUES (?, ?, ?, ?)",
        (user_id, token, now, now),
    )
    return token


def _finalize_social_signin(request: Request, provider: str,
                            subject_id: str, email: str,
                            name: Optional[str], return_to: str
                            ) -> RedirectResponse:
    """Decide between log-in (existing identity), auto-link, or stash."""
    from db import query_one, execute
    audit = _audit_or_noop()

    email = email.lower().strip()
    identity = query_one(
        "SELECT id, user_id FROM auth_identities "
        "WHERE provider = ? AND subject_id = ?",
        (provider, subject_id),
    )
    if identity:
        execute(
            "UPDATE auth_identities SET last_used_at = now() WHERE id = ?",
            (identity["id"],),
        )
        token = _issue_session(identity["user_id"])
        audit(request, action="auth.social.signin",
              actor={"id": identity["user_id"]},
              details={"provider": provider})
        return RedirectResponse(_bounce_with_token(token, return_to),
                                status_code=302)

    user = query_one("SELECT id FROM users WHERE username = ?", (email,))
    if user:
        execute(
            "INSERT INTO auth_identities (user_id, provider, subject_id, "
            "email_at_link, name_at_link, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, now())",
            (user["id"], provider, subject_id, email, name),
        )
        token = _issue_session(user["id"])
        audit(request, action="auth.social.linked",
              actor={"id": user["id"]},
              details={"provider": provider})
        return RedirectResponse(_bounce_with_token(token, return_to),
                                status_code=302)

    stash = _sign_stash(provider=provider, subject_id=subject_id,
                        email=email, name=name, ttl_seconds=_STASH_TTL)
    return RedirectResponse(
        _bounce_with_stash(stash, suggested_name=name or ""),
        status_code=302,
    )


def _audit_or_noop():
    """Return main.audit or a no-op if main can't be imported (e.g., tests)."""
    try:
        from main import audit
        return audit
    except Exception:
        def _noop(*a, **kw): pass
        return _noop


def _bounce_with_token(token: str, return_to: str) -> str:
    import urllib.parse
    qs = urllib.parse.urlencode({"token": token, "return_to": return_to})
    return f"{_app_url()}/auth-bounce?{qs}"


def _bounce_with_stash(stash: str, suggested_name: str) -> str:
    import urllib.parse
    qs = urllib.parse.urlencode({
        "complete_signup": stash,
        "suggested_name": suggested_name,
    })
    return f"{_app_url()}/auth-bounce?{qs}"


@router.get("/auth/google/callback")
async def google_callback(request: Request,
                          code: Optional[str] = Query(None),
                          state: Optional[str] = Query(None),
                          error: Optional[str] = Query(None)):
    if not _provider_configured("google"):
        raise _provider_not_configured()
    if error:
        import urllib.parse
        qs = urllib.parse.urlencode({"error": error})
        return RedirectResponse(f"{_app_url()}/auth-bounce?{qs}",
                                status_code=302)
    if not code or not state:
        raise HTTPException(status_code=400, detail={
            "code": "missing_params",
            "message": "Missing code or state query parameter.",
        })
    state_payload = _validate_csrf(request, state)
    return_to = state_payload.get("return_to", "/")

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        })
    if r.status_code >= 400:
        logger.warning("google_token_exchange_failed status=%d body=%r",
                       r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={
            "code": "provider_unavailable",
            "message": "Google rejected the authorization code.",
        })
    id_token = r.json().get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail={
            "code": "no_id_token",
            "message": "Google did not return an id_token.",
        })

    payload = await verify_google_id_token(id_token, os.getenv("GOOGLE_CLIENT_ID"))
    return _finalize_social_signin(
        request,
        provider="google",
        subject_id=payload["sub"],
        email=payload["email"],
        name=payload.get("name"),
        return_to=return_to,
    )


@router.post("/auth/apple/callback")
async def apple_callback(request: Request,
                         code: str = Form(...),
                         state: str = Form(...),
                         id_token: str = Form(...),
                         user: Optional[str] = Form(None)):
    """Apple POSTs the callback (response_mode=form_post)."""
    if not _provider_configured("apple"):
        raise _provider_not_configured()
    state_payload = _validate_csrf(request, state)
    return_to = state_payload.get("return_to", "/")

    # Verify the inline id_token first
    audience = os.getenv("APPLE_CLIENT_ID")
    payload = await verify_apple_id_token(id_token, audience)

    # Exchange code at Apple's token endpoint
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("https://appleid.apple.com/auth/token", data={
            "code": code,
            "client_id": audience,
            "client_secret": _apple_client_secret_jwt(),
            "redirect_uri": _apple_redirect_uri(),
            "grant_type": "authorization_code",
        })
    if r.status_code >= 400:
        logger.warning("apple_token_exchange_failed status=%d body=%r",
                       r.status_code, r.text[:200])
        raise HTTPException(status_code=400, detail={
            "code": "provider_unavailable",
            "message": "Apple rejected the authorization code.",
        })
    canonical_id_token = r.json().get("id_token")
    if canonical_id_token:
        payload = await verify_apple_id_token(canonical_id_token, audience)

    # Capture name on first sign-in (Apple only sends it once)
    name = None
    if user:
        try:
            user_data = json.loads(user)
            n = user_data.get("name", {})
            parts = [n.get("firstName", ""), n.get("lastName", "")]
            name = " ".join(p for p in parts if p).strip() or None
        except (json.JSONDecodeError, AttributeError):
            name = None

    return _finalize_social_signin(
        request,
        provider="apple",
        subject_id=payload["sub"],
        email=payload["email"],
        name=name,
        return_to=return_to,
    )


# ── Complete-signup (shared between providers) ─────────────────────────


from pydantic import BaseModel, Field


class CompleteSocialSignup(BaseModel):
    stash_token: str
    business_name: str = Field(..., min_length=1, max_length=200)


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "org"


def _unique_slug(base: str) -> str:
    """Append -N until the slug is unused."""
    from db import query_one
    slug = base
    n = 1
    while query_one("SELECT id FROM organizations WHERE slug = ?", (slug,)):
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.post("/auth/{provider}/complete-signup")
def complete_social_signup(provider: str, payload: CompleteSocialSignup,
                           request: Request):
    if provider not in ("google", "apple"):
        raise HTTPException(status_code=404, detail={
            "code": "unknown_provider",
            "message": f"Unknown provider: {provider}",
        })
    if not _provider_configured(provider):
        raise _provider_not_configured()
    try:
        stash = _verify_stash(payload.stash_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "stash_invalid",
            "message": f"Stash token invalid or expired: {exc}",
        })
    if stash["provider"] != provider:
        raise HTTPException(status_code=400, detail={
            "code": "stash_provider_mismatch",
            "message": "Stash token was issued for a different provider.",
        })

    from datetime import datetime, timedelta, timezone
    from db import query_one, execute, utc_now_iso
    email = stash["email"]
    subject_id = stash["subject_id"]
    name = stash.get("name")

    if query_one("SELECT id FROM users WHERE username = ?", (email,)):
        raise HTTPException(status_code=409, detail={
            "code": "email_taken",
            "message": "An account with this email already exists.",
        })
    if query_one(
        "SELECT id FROM auth_identities WHERE provider = ? AND subject_id = ?",
        (provider, subject_id),
    ):
        raise HTTPException(status_code=409, detail={
            "code": "identity_taken",
            "message": "This identity is already linked to another account.",
        })

    slug = _unique_slug(_slug(payload.business_name))
    trial_ends_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    execute(
        "INSERT INTO organizations (name, slug, plan, screen_limit, "
        "subscription_status, trial_ends_at, locale, created_at) "
        "VALUES (?, ?, 'starter', 5, 'trialing', ?, 'en', now())",
        (payload.business_name, slug, trial_ends_at),
    )
    org = query_one("SELECT * FROM organizations WHERE slug = ?", (slug,))
    execute(
        "INSERT INTO users (organization_id, username, password_hash, "
        "is_admin, role, created_at) "
        "VALUES (?, ?, NULL, 1, 'admin', ?)",
        (org["id"], email, utc_now_iso()),
    )
    user = query_one("SELECT * FROM users WHERE username = ?", (email,))
    execute(
        "INSERT INTO auth_identities (user_id, provider, subject_id, "
        "email_at_link, name_at_link, last_used_at) "
        "VALUES (?, ?, ?, ?, ?, now())",
        (user["id"], provider, subject_id, email, name),
    )

    # Re-SELECT org to get the just-inserted trial_ends_at from the DB
    org = query_one("SELECT * FROM organizations WHERE id = ?", (org["id"],))
    from main import subscription_state
    sub_state = subscription_state(org)

    token = _issue_session(user["id"])
    audit = _audit_or_noop()
    audit(request, action="auth.social.signup",
          actor={"id": user["id"]},
          details={"provider": provider,
                   "business_name": payload.business_name})
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "is_admin": bool(user["is_admin"]),
        },
        "organization": {
            "id": org["id"],
            "name": org["name"],
            "slug": org["slug"],
            "plan": org["plan"],
            "screen_limit": org["screen_limit"],
            "subscription_status": org["subscription_status"],
            "trial_ends_at": trial_ends_at,
            "locale": org.get("locale", "en"),
            # Phase 2.5f derived fields:
            "state": sub_state["state"],
            "can_write": sub_state["can_write"],
            "days_remaining": sub_state["days_remaining"],
            "expires_at": sub_state["expires_at"],
        },
    }


def attach_social_auth(app) -> None:
    """Mount the social auth router on the main FastAPI app."""
    app.include_router(router)
