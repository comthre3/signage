"""OAuth 2.1 + PKCE authorization server (Phase 2.5i-1).

All OAuth endpoints live here. Mounted into the main FastAPI app via
backend/main.py.
"""
from __future__ import annotations

import os
import secrets
import hashlib
import base64
import json
import time
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse


router = APIRouter()


def _app_url() -> str:
    return os.getenv("APP_URL", "https://api.khanshoof.com").rstrip("/")


# ── Discovery endpoints ───────────────────────────────────────────────


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata() -> dict:
    base = _app_url()
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
    base = _app_url()
    return {
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": ["api:read", "api:rw"],
        "bearer_methods_supported": ["header"],
    }
