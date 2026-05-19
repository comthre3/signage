"""OAuth 2.1 + PKCE authorization server (Phase 2.5i-1).

All OAuth endpoints live here. Mounted into the main FastAPI app via
backend/main.py.
"""
from __future__ import annotations

# Several imports below are pre-staged for Tasks 3–6 (authorize / token /
# revoke endpoints) and are unused by the discovery endpoints alone.
import os
import re
import secrets
import hashlib
import base64
import json
import time
import math
from typing import Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from db import execute, query_one, query_all, utc_now_iso


router = APIRouter()


def _api_base_url() -> str:
    # API base, where the OAuth endpoints live. Distinct from APP_URL
    # (frontend SPA, https://app.khanshoof.com).
    return os.getenv("API_BASE_URL", "https://api.khanshoof.com").rstrip("/")


# ── Discovery endpoints ───────────────────────────────────────────────


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata() -> dict:
    base = _api_base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "registration_endpoint": f"{base}/oauth/register",
        "scopes_supported": ["api:read", "api:rw"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "service_documentation": "https://app.khanshoof.com/api-docs.html",
    }


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata() -> dict:
    base = _api_base_url()
    return {
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": ["api:read", "api:rw"],
        "bearer_methods_supported": ["header"],
    }


# ── Dynamic client registration ──────────────────────────────────────


class ClientRegistration(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=200)
    redirect_uris: list[str] = Field(..., min_length=1)


_ALLOWED_SCHEMES_RE = (
    r"^("
    r"https://"
    r"|http://localhost(:\d+)?(/|$)"
    r"|http://127\.0\.0\.1(:\d+)?(/|$)"
    r"|(?!https?://|ftp://|sftp://|ws://|wss://|telnet://|ldap://|ldaps://|file://|gopher://|tftp://)"
    r"[a-z][a-z0-9+\-.]*://"
    r")"
)

_ALLOWED_SCHEMES_PAT = re.compile(_ALLOWED_SCHEMES_RE, re.IGNORECASE)

_FORBIDDEN_SCHEMES = ("data:", "file:", "javascript:", "vbscript:")


def _validate_redirect_uris(uris: list[str]) -> None:
    for uri in uris:
        if not isinstance(uri, str) or len(uri) > 2000:
            raise HTTPException(status_code=400,
                detail={"code": "invalid_redirect_uri",
                        "message": f"Invalid redirect_uri: {uri[:80]}"})
        lower = uri.lower()
        for bad in _FORBIDDEN_SCHEMES:
            if lower.startswith(bad):
                raise HTTPException(status_code=400,
                    detail={"code": "invalid_redirect_uri",
                            "message": f"Forbidden scheme in {uri[:80]}"})
        if not _ALLOWED_SCHEMES_PAT.match(uri):
            raise HTTPException(status_code=400,
                detail={"code": "invalid_redirect_uri",
                        "message": f"Unsupported scheme in {uri[:80]}"})


@router.post("/oauth/register", status_code=201)
def register_client(payload: ClientRegistration) -> dict:
    if len(payload.redirect_uris) > 10:
        raise HTTPException(status_code=400,
            detail={"code": "invalid_client_metadata",
                    "message": "Too many redirect_uris (max 10)"})
    _validate_redirect_uris(payload.redirect_uris)
    client_id = "dyn_" + secrets.token_urlsafe(18)
    execute(
        "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, pre_registered) "
        "VALUES (?, ?, ?::jsonb, false)",
        (client_id, payload.client_name, json.dumps(payload.redirect_uris)),
    )
    return {
        "client_id": client_id,
        "client_name": payload.client_name,
        "redirect_uris": payload.redirect_uris,
        "client_id_issued_at": int(time.time()),
        "token_endpoint_auth_method": "none",
    }


# ── Authorization flow ──────────────────────────────────────────────

_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_templates_dir)


# Short-lived OAuth authorization-flow state. In-memory only — does not survive
# process restart. NOT safe for multi-worker uvicorn (state on worker A is
# invisible on worker B). v1 limitation; spec documents the trade-off.
_pending_auth: dict = {}
_PENDING_AUTH_TTL = 600   # 10 minutes


def _prune_pending_auth():
    now = time.time()
    expired = [k for k, v in _pending_auth.items() if v.get("expires_at", 0) < now]
    for k in expired:
        _pending_auth.pop(k, None)


def _error_response(request: Request, message: str, detail: str = "") -> HTMLResponse:
    html = templates.get_template("oauth_error.html").render(
        message=message, detail=detail,
    )
    return HTMLResponse(html, status_code=400)


def _client_or_error(client_id: str) -> Optional[dict]:
    if not client_id:
        return None
    return query_one(
        "SELECT client_id, client_name, redirect_uris FROM oauth_clients "
        "WHERE client_id = ?",
        (client_id,),
    )


def _redirect_uri_matches(client_row: dict, requested: str) -> bool:
    uris = client_row.get("redirect_uris") or []
    if isinstance(uris, str):
        try:
            uris = json.loads(uris)
        except Exception:
            return False
    return requested in uris


def _session_user_from_cookie(request: Request) -> Optional[dict]:
    token = request.cookies.get("oauth_session")
    if not token:
        return None
    from main import _session_lookup
    return _session_lookup(token)


@router.get("/oauth/authorize")
def authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(...),
    state: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    resume: Optional[str] = Query(None),
):
    client_row = _client_or_error(client_id)
    if not client_row:
        return _error_response(
            request, "Unknown client",
            "The client_id is not registered. Have your MCP client register first.",
        )

    if not _redirect_uri_matches(client_row, redirect_uri):
        return _error_response(
            request, "Invalid redirect_uri",
            "The redirect_uri does not match any registered URI for this client.",
        )

    if response_type != "code":
        qs = urlencode({"error": "unsupported_response_type", "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)
    if code_challenge_method != "S256":
        qs = urlencode({
            "error": "invalid_request",
            "error_description": "code_challenge_method must be S256",
            "state": state,
        })
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    if scope not in ("api:read", "api:rw"):
        qs = urlencode({"error": "invalid_scope", "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    if resume:
        pending = _pending_auth.get(resume)
        if pending and pending["client_id"] == client_id:
            request_id = resume
        else:
            request_id = secrets.token_urlsafe(24)
    else:
        request_id = secrets.token_urlsafe(24)

    _prune_pending_auth()
    _pending_auth[request_id] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "expires_at": time.time() + _PENDING_AUTH_TTL,
    }

    user = _session_user_from_cookie(request)
    if not user:
        html = templates.get_template("oauth_login.html").render(
            client_name=client_row["client_name"],
            request_id=request_id,
            error=None,
        )
        return HTMLResponse(html)

    org_row = query_one(
        "SELECT name FROM organizations WHERE id = ?",
        (user["organization_id"],),
    )
    html = templates.get_template("oauth_consent.html").render(
        client_name=client_row["client_name"],
        organization_name=(org_row or {}).get("name", "your organization"),
        user_email=user["username"],
        requested_scope=scope,
        request_id=request_id,
    )
    return HTMLResponse(html)


@router.post("/oauth/login")
def oauth_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    request_id: str = Form(...),
):
    pending = _pending_auth.get(request_id)
    if not pending:
        return _error_response(request, "Login session expired",
                               "Please restart the authorization flow from your MCP client.")

    from main import (
        verify_password, audit, _client_ip,
        LOGIN_LOCKOUT_WINDOW_SECONDS, LOGIN_LOCKOUT_THRESHOLD,
    )
    client_row = _client_or_error(pending["client_id"])
    client_name = client_row["client_name"] if client_row else pending["client_id"]

    ip = _client_ip(request)

    # ── Lockout check (mirrors main.login) ──────────────────────────
    last_success_row = query_one(
        "SELECT MAX(attempted_at) AS ts FROM login_attempts "
        "WHERE username = ? AND success = true",
        (username,),
    )
    last_success_ts = last_success_row["ts"] if last_success_row else None

    if last_success_ts is not None:
        failure_filter = (
            "WHERE username = ? AND success = false "
            "  AND attempted_at > now() - interval '%d seconds' "
            "  AND attempted_at > ?" % LOGIN_LOCKOUT_WINDOW_SECONDS
        )
        failure_params = (username, last_success_ts)
    else:
        failure_filter = (
            "WHERE username = ? AND success = false "
            "  AND attempted_at > now() - interval '%d seconds'"
            % LOGIN_LOCKOUT_WINDOW_SECONDS
        )
        failure_params = (username,)

    failure_count_row = query_one(
        f"SELECT COUNT(*) AS n FROM login_attempts {failure_filter}",
        failure_params,
    )
    failure_count = int(failure_count_row["n"]) if failure_count_row else 0

    if failure_count >= LOGIN_LOCKOUT_THRESHOLD:
        retry_minutes = math.ceil(LOGIN_LOCKOUT_WINDOW_SECONDS / 60)
        oldest_row = query_one(
            f"SELECT MIN(attempted_at) AS ts FROM login_attempts {failure_filter}",
            failure_params,
        )
        oldest_ts = oldest_row["ts"] if oldest_row else None
        if oldest_ts is not None:
            elapsed = (datetime.now(timezone.utc) - oldest_ts).total_seconds()
            retry_seconds = max(0, int(LOGIN_LOCKOUT_WINDOW_SECONDS - elapsed))
            retry_minutes = math.ceil(retry_seconds / 60)
        audit(request, action="auth.oauth_login.failure", actor=None,
              details={"reason": "account_locked", "username": username})
        html = templates.get_template("oauth_login.html").render(
            client_name=client_name,
            request_id=request_id,
            error=f"Too many failed attempts. Try again in {retry_minutes} minute(s).",
        )
        return HTMLResponse(html, status_code=429)

    # ── Verify password ──────────────────────────────────────────────
    user_row = query_one("SELECT * FROM users WHERE username = ?", (username,))
    ok = bool(user_row) and verify_password(password, user_row["password_hash"])

    # Record attempt regardless of outcome
    execute(
        "INSERT INTO login_attempts (username, success, ip, attempted_at) "
        "VALUES (?, ?, ?, ?)",
        (username, ok, ip, utc_now_iso()),
    )

    if not ok:
        audit(request, action="auth.oauth_login.failure", actor=None,
              details={"reason": "invalid_credentials", "username": username})
        html = templates.get_template("oauth_login.html").render(
            client_name=client_name,
            request_id=request_id,
            error="Invalid credentials. Try again.",
        )
        return HTMLResponse(html, status_code=401)

    session_token = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) "
        "VALUES (?, ?, ?, ?)",
        (user_row["id"], session_token, utc_now_iso(), utc_now_iso()),
    )
    audit(request, action="auth.oauth_login.success",
          actor={"id": user_row["id"], "username": user_row["username"],
                 "organization_id": user_row["organization_id"]})

    qs = urlencode({
        "response_type": "code",
        "client_id": pending["client_id"],
        "redirect_uri": pending["redirect_uri"],
        "scope": pending["scope"],
        "state": pending["state"],
        "code_challenge": pending["code_challenge"],
        "code_challenge_method": "S256",
        "resume": request_id,
    })
    redirect_url = f"/oauth/authorize?{qs}"
    resp = RedirectResponse(redirect_url, status_code=302)
    resp.set_cookie(
        "oauth_session", session_token,
        httponly=True, samesite="lax", max_age=3600,
        secure=os.getenv("API_BASE_URL", "").startswith("https://"),
    )
    return resp


@router.post("/oauth/authorize/decision")
def authorize_decision(
    request: Request,
    request_id: str = Form(...),
    decision: str = Form(...),
    scope: str = Form(...),
):
    user = _session_user_from_cookie(request)
    if not user:
        return _error_response(request, "Not signed in",
                               "Sign in first to authorize this client.")

    pending = _pending_auth.pop(request_id, None)
    if not pending:
        return _error_response(request, "Authorization expired",
                               "Please restart from your MCP client.")

    if decision != "allow":
        qs = urlencode({"error": "access_denied", "state": pending["state"]})
        return RedirectResponse(f"{pending['redirect_uri']}?{qs}", status_code=302)

    if scope not in ("api:read", "api:rw"):
        scope = pending["scope"]

    code = secrets.token_urlsafe(32)
    from main import hash_password
    code_hash = hash_password(code)
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    execute(
        """
        INSERT INTO oauth_authorization_codes
          (code_hash, client_id, organization_id, user_id, scope,
           redirect_uri, code_challenge, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (code_hash, pending["client_id"], user["organization_id"],
         user["id"], scope, pending["redirect_uri"], pending["code_challenge"],
         expires.isoformat()),
    )

    qs = urlencode({"code": code, "state": pending["state"]})
    return RedirectResponse(f"{pending['redirect_uri']}?{qs}", status_code=302)


# ── Token endpoint ──────────────────────────────────────────────────


_ACCESS_TTL_SECONDS  = 3600         # 1 hour
_REFRESH_TTL_SECONDS = 90 * 86400   # 90 days


def _pkce_matches(verifier: str, challenge: str) -> bool:
    """Compute S256 challenge from verifier and compare to stored challenge."""
    if not verifier or not challenge:
        return False
    try:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def _hash_token(token: str) -> str:
    from main import hash_password
    return hash_password(token)


def _verify_token_hash(token: str, hashed: str) -> bool:
    from main import verify_password
    return verify_password(token, hashed)


_TOKEN_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}


def _oauth_error(status: int, error: str, description: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers=_TOKEN_NO_CACHE_HEADERS,
    )


def _issue_tokens(client_id: str, org_id: int, user_id: Optional[int],
                  scope: str) -> JSONResponse:
    """Generate, hash, and store a new (access, refresh) pair. Returns a
    JSONResponse with plaintext tokens and RFC 6749 §5.1 no-store headers."""
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    access_hash = _hash_token(access)
    refresh_hash = _hash_token(refresh)
    now = datetime.now(timezone.utc)
    access_exp = now + timedelta(seconds=_ACCESS_TTL_SECONDS)
    refresh_exp = now + timedelta(seconds=_REFRESH_TTL_SECONDS)
    execute(
        """
        INSERT INTO oauth_tokens
          (access_token_hash, refresh_token_hash, client_id,
           organization_id, user_id, scope,
           access_expires_at, refresh_expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (access_hash, refresh_hash, client_id, org_id, user_id, scope,
         access_exp.isoformat(), refresh_exp.isoformat()),
    )
    return JSONResponse(
        content={
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TTL_SECONDS,
            "scope": scope,
        },
        headers=_TOKEN_NO_CACHE_HEADERS,
    )


def _exchange_authorization_code(
    code: str, client_id: str, redirect_uri: str, code_verifier: str,
) -> JSONResponse:
    # PBKDF2 with random salt → lookup-key is not stable. Fetch by client_id
    # and iterate verify. Filter to live codes only so the iteration set stays
    # tightly bounded; post-iteration checks below defend against TOCTOU.
    candidates = query_all(
        "SELECT id, code_hash, client_id, organization_id, user_id, scope, "
        "       redirect_uri, code_challenge, expires_at, consumed_at "
        "FROM oauth_authorization_codes "
        "WHERE client_id = ? "
        "  AND consumed_at IS NULL "
        "  AND expires_at > now()",
        (client_id,),
    )
    matching = None
    for row in candidates:
        if _verify_token_hash(code, row["code_hash"]):
            matching = row
            break
    if not matching:
        return _oauth_error(400, "invalid_grant",
                            "Unknown, expired, or already-used authorization code")

    if matching["consumed_at"] is not None:
        return _oauth_error(400, "invalid_grant",
                            "Authorization code already used")

    # Expiry check
    expires = matching["expires_at"]
    if hasattr(expires, "isoformat"):
        expires_dt = expires
    else:
        expires_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    if expires_dt < datetime.now(timezone.utc):
        return _oauth_error(400, "invalid_grant",
                            "Authorization code expired")

    if matching["redirect_uri"] != redirect_uri:
        return _oauth_error(400, "invalid_grant",
                            "redirect_uri mismatch")

    if not _pkce_matches(code_verifier, matching["code_challenge"]):
        return _oauth_error(400, "invalid_grant",
                            "PKCE verifier did not match")

    execute(
        "UPDATE oauth_authorization_codes SET consumed_at = now() WHERE id = ?",
        (matching["id"],),
    )

    return _issue_tokens(
        client_id=matching["client_id"],
        org_id=matching["organization_id"],
        user_id=matching["user_id"],
        scope=matching["scope"],
    )


def _refresh_tokens(refresh_token: str, client_id: str) -> JSONResponse:
    candidates = query_all(
        "SELECT id, refresh_token_hash, client_id, organization_id, user_id, "
        "       scope, refresh_expires_at, revoked_at "
        "FROM oauth_tokens WHERE client_id = ? AND revoked_at IS NULL",
        (client_id,),
    )
    matching = None
    for row in candidates:
        if _verify_token_hash(refresh_token, row["refresh_token_hash"]):
            matching = row
            break
    if not matching:
        return _oauth_error(400, "invalid_grant",
                            "Unknown or revoked refresh token")

    expires = matching["refresh_expires_at"]
    if hasattr(expires, "isoformat"):
        expires_dt = expires
    else:
        expires_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    if expires_dt < datetime.now(timezone.utc):
        return _oauth_error(400, "invalid_grant",
                            "Refresh token expired")

    execute(
        "UPDATE oauth_tokens SET revoked_at = now() WHERE id = ?",
        (matching["id"],),
    )
    return _issue_tokens(
        client_id=matching["client_id"],
        org_id=matching["organization_id"],
        user_id=matching["user_id"],
        scope=matching["scope"],
    )


@router.post("/oauth/token")
def token(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
):
    if grant_type == "authorization_code":
        if not all((code, redirect_uri, client_id, code_verifier)):
            return _oauth_error(400, "invalid_request",
                                "Missing required field for authorization_code grant")
        return _exchange_authorization_code(code, client_id, redirect_uri, code_verifier)

    if grant_type == "refresh_token":
        if not all((refresh_token, client_id)):
            return _oauth_error(400, "invalid_request",
                                "Missing required field for refresh_token grant")
        return _refresh_tokens(refresh_token, client_id)

    return _oauth_error(400, "unsupported_grant_type",
                        f"Grant type '{grant_type}' is not supported")
