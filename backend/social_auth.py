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
    return jwt.decode(state, _secret_key(), algorithms=["HS256"],
                      options={"require": ["exp", "iat", "kind"]})


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


def _fetch_jwks(url: str) -> dict:
    now = time.time()
    cached = _jwks_cache.get(url)
    if cached and cached["fetched_at"] + _JWKS_TTL_SECONDS > now:
        return cached["jwks"]
    with httpx.Client(timeout=10.0) as c:
        r = c.get(url)
        r.raise_for_status()
        jwks = r.json()
    _jwks_cache[url] = {"jwks": jwks, "fetched_at": now}
    return jwks


def _verify_id_token(id_token: str, *, jwks_url: str, algorithm: str,
                     audience: str, issuer: list[str] | str) -> dict:
    jwks = _fetch_jwks(jwks_url)
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


def verify_google_id_token(id_token: str, audience: str) -> dict:
    payload = _verify_id_token(
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


def verify_apple_id_token(id_token: str, audience: str) -> dict:
    # Apple always sets email_verified=true (they own the email)
    return _verify_id_token(
        id_token,
        jwks_url="https://appleid.apple.com/auth/keys",
        algorithm="ES256",
        audience=audience,
        issuer="https://appleid.apple.com",
    )
