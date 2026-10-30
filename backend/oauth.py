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

from fastapi import APIRouter, Request, Response, HTTPException, Form, Query
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


# A grant can never exceed what the granting user could do in a session.
# Viewers are read-only everywhere, so read-only is all they can delegate;
# editors and admins may grant api:rw (and every endpoint still applies its
# own role gate to the token -- see require_api_scope in main.py).
_SCOPE_RANK = {"api:read": 1, "api:rw": 2}
_ROLE_MAX_SCOPE = {"admin": "api:rw", "editor": "api:rw", "viewer": "api:read"}


def _cap_scope(scope: str, role: Optional[str]) -> str:
    ceiling = _ROLE_MAX_SCOPE.get(role or "", "api:read")
    return scope if _SCOPE_RANK[scope] <= _SCOPE_RANK[ceiling] else ceiling


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
        requested_scope=_cap_scope(scope, user.get("role")),
        allow_rw=_cap_scope("api:rw", user.get("role")) == "api:rw",
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

    if scope not in _SCOPE_RANK:
        scope = pending["scope"]
    scope = _cap_scope(scope, user.get("role"))

    code = secrets.token_urlsafe(32)
    code_hash = _hash_token(code)
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
    """SHA-256 of an authorization code / access token / refresh token.

    These are secrets.token_urlsafe(32) -- 256 bits from the CSPRNG -- not
    user-chosen passwords, so a slow KDF buys nothing against guessing. What
    it did cost: a random salt makes the stored value unsearchable, so every
    lookup PBKDF2-verified against every live row (~82ms each), and every
    dashboard request is tried as an OAuth token before it is tried as a
    session. A fixed hash is looked up with one indexed equality instead.
    Same reasoning as hash_api_key() in main.py.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _verify_token_hash(token: str, hashed: str) -> bool:
    """PBKDF2 check, for rows written before tokens were SHA-256 indexed."""
    from main import verify_password
    return verify_password(token, hashed)


# Rows minted before _hash_token became SHA-256 hold a PBKDF2 "salt$digest"
# (SHA-256 hex never contains "$"). They are still honoured until they
# expire: an access token is upgraded in place on its next use, a refresh
# token is rotated into a SHA-256 pair. Nothing writes PBKDF2 any more, so
# once a scan finds no live legacy row there can never be one again, and the
# flag turns the fallback off for the life of the process -- after at most
# an hour (access) / 90 days (refresh) a miss costs no key derivation at all.
_legacy_live = {"access": True, "refresh": True}

_TOKEN_COLUMNS = (
    "id, client_id, organization_id, user_id, scope, "
    "access_expires_at, refresh_expires_at, revoked_at"
)


def _legacy_match(kind: str, token: str,
                  client_id: Optional[str] = None) -> Optional[dict]:
    if not _legacy_live[kind]:
        return None
    hash_col = "access_token_hash" if kind == "access" else "refresh_token_hash"
    exp_col = "access_expires_at" if kind == "access" else "refresh_expires_at"
    rows = query_all(
        f"SELECT {_TOKEN_COLUMNS}, {hash_col} AS stored_hash FROM oauth_tokens "
        f"WHERE revoked_at IS NULL AND {exp_col} > now() "
        f"AND strpos({hash_col}, '$') > 0"
    )
    if not rows:
        _legacy_live[kind] = False
        return None
    for row in rows:
        if client_id is not None and row["client_id"] != client_id:
            continue
        if _verify_token_hash(token, row["stored_hash"]):
            return row
    return None


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
    # Codes live ten minutes, so there is no PBKDF2 fallback for them: a code
    # minted before the deploy that switched to SHA-256 just fails and the
    # client restarts the flow.
    matching = query_one(
        "SELECT id, client_id, organization_id, user_id, scope, "
        "       redirect_uri, code_challenge "
        "FROM oauth_authorization_codes "
        "WHERE code_hash = ? AND client_id = ? "
        "  AND consumed_at IS NULL AND expires_at > now()",
        (_hash_token(code), client_id),
    )
    if not matching:
        return _oauth_error(400, "invalid_grant",
                            "Unknown, expired, or already-used authorization code")

    if matching["redirect_uri"] != redirect_uri:
        return _oauth_error(400, "invalid_grant",
                            "redirect_uri mismatch")

    if not _pkce_matches(code_verifier, matching["code_challenge"]):
        return _oauth_error(400, "invalid_grant",
                            "PKCE verifier did not match")

    # Consume atomically: of two concurrent exchanges only one updates a row.
    consumed = execute(
        "UPDATE oauth_authorization_codes SET consumed_at = now() "
        "WHERE id = ? AND consumed_at IS NULL AND expires_at > now()",
        (matching["id"],),
    )
    if consumed != 1:
        return _oauth_error(400, "invalid_grant",
                            "Authorization code already used")

    return _issue_tokens(
        client_id=matching["client_id"],
        org_id=matching["organization_id"],
        user_id=matching["user_id"],
        scope=matching["scope"],
    )


def _refresh_tokens(refresh_token: str, client_id: str) -> JSONResponse:
    matching = query_one(
        f"SELECT {_TOKEN_COLUMNS} FROM oauth_tokens "
        "WHERE refresh_token_hash = ? AND client_id = ? AND revoked_at IS NULL",
        (_hash_token(refresh_token), client_id),
    )
    if not matching:
        matching = _legacy_match("refresh", refresh_token, client_id)
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

    # Rotate atomically: a refresh token replayed concurrently yields one new
    # pair, not two.
    rotated = execute(
        "UPDATE oauth_tokens SET revoked_at = now() "
        "WHERE id = ? AND revoked_at IS NULL",
        (matching["id"],),
    )
    if rotated != 1:
        return _oauth_error(400, "invalid_grant",
                            "Unknown or revoked refresh token")
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


# ── Revocation ───────────────────────────────────────────────────────


@router.post("/oauth/revoke")
def revoke(
    token: str = Form(...),
    client_id: Optional[str] = Form(None),
    token_type_hint: Optional[str] = Form(None),
):
    """RFC 7009: revoke an access or refresh token. Always returns 200."""
    hashed = _hash_token(token)
    revoked = execute(
        "UPDATE oauth_tokens SET revoked_at = now() "
        "WHERE (access_token_hash = ? OR refresh_token_hash = ?) "
        "AND revoked_at IS NULL AND (client_id = ? OR ?::text IS NULL)",
        (hashed, hashed, client_id, client_id),
    )
    if not revoked:
        row = (_legacy_match("access", token, client_id)
               or _legacy_match("refresh", token, client_id))
        if row:
            execute("UPDATE oauth_tokens SET revoked_at = now() WHERE id = ?",
                    (row["id"],))
    return Response(status_code=200)


# ── Access-token lookup (used by get_api_authed in main.py) ──────────


def _grantor(row: dict) -> Optional[dict]:
    """The user a token was granted by, shaped like main._session_lookup's
    user dict, or None if they no longer exist in the token's org."""
    if row.get("user_id") is None:
        return None
    user = query_one(
        "SELECT id, username, is_admin, role, organization_id FROM users "
        "WHERE id = ? AND organization_id = ?",
        (row["user_id"], row["organization_id"]),
    )
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "role": user.get("role") or ("admin" if user["is_admin"] else "viewer"),
        "organization_id": user["organization_id"],
    }


def lookup_oauth_access_token(token: str) -> Optional[dict]:
    """Return the active oauth_tokens row the token matches, with the
    granting user under "user"; None if no live token matches or its user
    is gone. Fire-and-forget last_used_at update."""
    if not token:
        return None
    # Quickly reject API keys (handled separately in get_api_authed).
    if token.startswith("khan_live_"):
        return None
    hashed = _hash_token(token)
    row = query_one(
        f"SELECT {_TOKEN_COLUMNS} FROM oauth_tokens "
        "WHERE access_token_hash = ? "
        "  AND revoked_at IS NULL AND access_expires_at > now()",
        (hashed,),
    )
    upgrade = False
    if not row:
        row = _legacy_match("access", token)
        upgrade = row is not None
    if not row:
        return None
    # The token acts for the user who granted it. If that user is gone
    # (user_id is ON DELETE SET NULL) or has left the org, it is dead.
    grantor = _grantor(row)
    if grantor is None:
        return None
    row["user"] = grantor
    try:
        if upgrade:
            execute(
                "UPDATE oauth_tokens SET access_token_hash = ?, "
                "last_used_at = now() WHERE id = ?",
                (hashed, row["id"]),
            )
        else:
            execute(
                "UPDATE oauth_tokens SET last_used_at = now() WHERE id = ?",
                (row["id"],),
            )
    except Exception as exc:
        from main import logger
        logger.warning("oauth_token_last_used_update_failed id=%s err=%s",
                       row["id"], exc)
    row.pop("stored_hash", None)
    return row
