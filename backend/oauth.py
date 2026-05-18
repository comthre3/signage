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
