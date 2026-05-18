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
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from db import execute, query_one, query_all


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

from fastapi.templating import Jinja2Templates

_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_templates_dir)


# In-memory pending-auth state: request_id -> dict of authorize-time params
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
        return RedirectResponse(
            f"{redirect_uri}?error=unsupported_response_type&state={state}",
            status_code=302,
        )
    if code_challenge_method != "S256":
        return RedirectResponse(
            f"{redirect_uri}?error=invalid_request&error_description="
            f"code_challenge_method+must+be+S256&state={state}",
            status_code=302,
        )

    if scope not in ("api:read", "api:rw"):
        return RedirectResponse(
            f"{redirect_uri}?error=invalid_scope&state={state}",
            status_code=302,
        )

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
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    request_id: str = Form(...),
):
    pending = _pending_auth.get(request_id)
    if not pending:
        return _error_response(request, "Login session expired",
                               "Please restart the authorization flow from your MCP client.")

    from main import verify_password
    user_row = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user_row or not verify_password(password, user_row["password_hash"]):
        client_row = _client_or_error(pending["client_id"])
        html = templates.get_template("oauth_login.html").render(
            client_name=client_row["client_name"] if client_row else pending["client_id"],
            request_id=request_id,
            error="Invalid credentials. Try again.",
        )
        return HTMLResponse(html, status_code=401)

    session_token = secrets.token_urlsafe(32)
    from db import utc_now_iso as _utc_now
    execute(
        "INSERT INTO sessions (user_id, token, created_at, last_used) "
        "VALUES (?, ?, ?, ?)",
        (user_row["id"], session_token, _utc_now(), _utc_now()),
    )

    redirect_url = (
        f"/oauth/authorize?response_type=code&client_id={pending['client_id']}"
        f"&redirect_uri={pending['redirect_uri']}&scope={pending['scope']}"
        f"&state={pending['state']}&code_challenge={pending['code_challenge']}"
        f"&code_challenge_method=S256&resume={request_id}"
    )
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
    pending = _pending_auth.pop(request_id, None)
    if not pending:
        return _error_response(request, "Authorization expired",
                               "Please restart from your MCP client.")

    user = _session_user_from_cookie(request)
    if not user:
        return _error_response(request, "Not signed in",
                               "Sign in first to authorize this client.")

    if decision != "allow":
        return RedirectResponse(
            f"{pending['redirect_uri']}?error=access_denied&state={pending['state']}",
            status_code=302,
        )

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

    return RedirectResponse(
        f"{pending['redirect_uri']}?code={code}&state={pending['state']}",
        status_code=302,
    )
