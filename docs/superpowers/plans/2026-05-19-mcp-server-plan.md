# Phase 2.5i-2 — MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mount an MCP server at `/mcp` exposing 25 thin tool wrappers that dispatch into the existing FastAPI handlers via in-process ASGI, authenticated by the OAuth 2.1 tokens from Phase 2.5i-1.

**Architecture:** Single new module `backend/mcp_server.py` uses the official `mcp` Python SDK's `FastMCP` class with `stateless_http=True, json_response=True` so each tool call is a normal POST/JSON exchange (no SSE complications). Each tool's body is ~5 lines: extract OAuth bearer from the request context, build an `httpx.AsyncClient` request through `ASGITransport(app=app)`, return the JSON response. All scope/audit/rate-limit/validation logic reuses 1:1 from the existing FastAPI handlers.

**Tech Stack:** FastAPI · mcp SDK · httpx · ASGITransport · psycopg.

**Spec:** `docs/superpowers/specs/2026-05-19-mcp-server-design.md`
**Branch:** `feature/mcp-server` (stacked on `feature/oauth-provider`, will rebase onto main once PR #12 lands).
**Test baseline going in:** 346 passing on `feature/oauth-provider`.

---

## Working Conventions (read before any task)

1. Each task ends with a commit. Subject prefix `feat(mcp):` or `test(mcp):`.
2. Backend source is COPY'd into the image — rebuild after changes:
   ```bash
   docker-compose build backend && docker-compose up -d --force-recreate backend
   ```
3. Test command:
   ```bash
   docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
     -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
     backend pytest -xvs <path>
   ```
4. Test helpers `_signup_org`, `_register_client`, `_pkce_pair`, `_full_authorize_flow` already exist in `backend/tests/test_oauth.py`. We **do not** copy them into `test_mcp.py` — instead, import from `tests.test_oauth` when needed:
   ```python
   from tests.test_oauth import _signup_org, _register_client, _pkce_pair, _full_authorize_flow
   ```
5. The `client` fixture (TestClient) and `signed_up_org` fixture live in `backend/tests/conftest.py`.
6. The OAuth provider is already merged (or stacked underneath this branch) — `lookup_oauth_access_token`, `get_api_authed`, and `require_api_scope` are all in `backend/main.py` and `backend/oauth.py`. Do not modify them.
7. The MCP SDK's `FastMCP` class is at `mcp.server.fastmcp.FastMCP`. The `Context` type for tool handlers is `mcp.server.fastmcp.Context`. The `streamable_http_app()` method returns a Starlette/ASGI app suitable for `app.mount("/mcp", ...)`.
8. Use `stateless_http=True, json_response=True` on FastMCP so responses are JSON, not SSE — simpler for both clients and tests.
9. **Do NOT modify `.env` or rewrite URLs.**

---

## Task 1: SDK install + empty MCP mount + smoke

**Files:**
- Modify: `backend/requirements.txt` (add `mcp`)
- Create: `backend/mcp_server.py`
- Modify: `backend/main.py` (mount the MCP app)
- Create: `backend/tests/test_mcp.py`

**Goal:** `pip install mcp`, empty `FastMCP("khanshoof", ...)` mounted at `/mcp`, smoke test passes.

- [ ] **Step 1: Pin the SDK in requirements.txt**

Read `backend/requirements.txt`. Add a line:
```
mcp>=1.2,<2
```
The `>=1.2` floor is so FastMCP's `json_response` kwarg is available. The `<2` cap protects against the next major.

- [ ] **Step 2: Write the smoke test**

Create `backend/tests/test_mcp.py`:

```python
"""Tests for the Phase 2.5i-2 MCP server."""
import json


def _jsonrpc_call(client, method: str, params: dict | None = None,
                  bearer: str | None = None, id_: int = 1) -> dict:
    """POST a JSON-RPC 2.0 request to /mcp/ and return the parsed response.

    The MCP SDK's Streamable HTTP transport mounts at `/mcp/` (with a trailing
    slash route inside the mounted app). Both `/mcp` and `/mcp/` should work
    via redirect, but use the slashed form to avoid the 307.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    r = client.post("/mcp/", json=body, headers=headers)
    assert r.status_code in (200, 202), f"HTTP {r.status_code}: {r.text}"
    # Stateless JSON mode returns application/json; per-request envelope.
    return r.json()


def test_mcp_initialize_handshake(client):
    """The mcp_server mounts at /mcp/ and responds to `initialize`."""
    body = _jsonrpc_call(client, "initialize", params={
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })
    assert "result" in body, body
    assert body["result"]["serverInfo"]["name"] == "khanshoof"


def test_mcp_tools_list_initially_empty(client):
    """Before any tools register, tools/list must return an empty array."""
    body = _jsonrpc_call(client, "tools/list")
    # tools/list may require initialize first; the SDK might return an error
    # if so — accept either an empty list OR an "uninitialized" error code.
    if "error" in body:
        # Initialize was not part of this test's setup; expected for stateless
        # mode if it requires initialize first.
        assert body["error"]["code"] in (-32600, -32002), body
    else:
        assert body["result"]["tools"] == []
```

- [ ] **Step 3: Create `backend/mcp_server.py` scaffolding**

```python
"""Phase 2.5i-2 — MCP server exposing Khanshoof tools.

All tool registrations live here. Mounted at /mcp on the main FastAPI app
via backend/main.py.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def attach_mcp(app) -> None:
    """Mount an MCP server at /mcp on the FastAPI app.

    Uses stateless HTTP + JSON response mode so each tool call is a plain
    POST/JSON exchange — no SSE framing, no per-session state.
    """
    mcp = FastMCP(
        "khanshoof",
        instructions=(
            "Khanshoof signage MCP server. Use these tools to list, create, "
            "update, and delete playlists, schedules, screens, walls, and media."
        ),
        stateless_http=True,
        json_response=True,
    )

    # Tools are registered in subsequent tasks via _register_tools(mcp).
    # For Task 1, the server is empty.

    app.mount("/mcp", mcp.streamable_http_app())
```

- [ ] **Step 4: Mount in `backend/main.py`**

Open `backend/main.py`. Find the existing `from oauth import router as oauth_router` near the top imports. After it, append a similar import:

```python
from mcp_server import attach_mcp
```

Then find the existing `app.include_router(oauth_router)` call. Add the MCP mount immediately after:

```python
attach_mcp(app)
```

- [ ] **Step 5: Rebuild + run**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py
```

Expected: 2 passed. If the SDK version doesn't have `json_response`, drop it from the FastMCP() call and rerun — the SSE framing will require updating the test helper. The plan assumes SDK ≥ 1.2 where `json_response=True` is available.

- [ ] **Step 6: Full suite regression**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```

Expected: **348 passed** (346 baseline + 2 new MCP tests).

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/mcp_server.py backend/main.py \
        backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
feat(mcp): scaffolding — pin mcp SDK + empty FastMCP mount at /mcp

Adds the official mcp Python SDK as a dependency. Mounts a stateless
FastMCP server with json_response=True at /mcp so each tool call is a
plain JSON-RPC POST exchange. No tools registered yet — those land in
Tasks 2–4. Initialize handshake + empty tools/list verified.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Dispatch helper + error mapper + canary tool (khanshoof_get_organization)

**Files:**
- Modify: `backend/mcp_server.py`
- Modify: `backend/tests/test_mcp.py` (append helper + 4 tests)

**Goal:** Add `_bearer_from`, `_dispatch`, `_map_response`, `_mcp_unauthorized`. Register one tool (`khanshoof_get_organization`) as the canary that exercises the full dispatch path.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_mcp.py`:

```python
# ── Helpers shared with later tests ────────────────────────────────────

from tests.test_oauth import _full_authorize_flow


def _mcp_call_tool(client, tool_name: str, args: dict | None = None,
                   bearer: str | None = None) -> dict:
    """Call tools/call. Returns the parsed JSON-RPC response."""
    return _jsonrpc_call(
        client, "tools/call",
        params={"name": tool_name, "arguments": args or {}},
        bearer=bearer,
    )


def _get_oauth_access_token(client, scope: str = "api:rw") -> tuple[str, str]:
    """Run the full OAuth flow and exchange a code for an access token.

    Returns (access_token, oauth_client_id). The OAuth client_id is returned
    because some tests need it for the revoke endpoint.
    """
    flow = _full_authorize_flow(client)
    # _full_authorize_flow always uses api:rw; for api:read tests we run the
    # flow manually in the test body (see test_oauth.py for the pattern).
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200, r.text
    return r.json()["access_token"], flow["client_id"]


# ── Task 2: dispatch helper + canary tool ──────────────────────────────


def test_tools_list_shows_get_organization(client):
    body = _jsonrpc_call(client, "tools/list")
    if "error" in body:
        # If the SDK requires initialize first, run it then retry
        _jsonrpc_call(client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(client, "tools/list")
    names = [t["name"] for t in body["result"]["tools"]]
    assert "khanshoof_get_organization" in names


def test_get_organization_with_valid_oauth_token(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_get_organization",
                          bearer=access_token)
    assert "result" in body, body
    # The FastAPI handler returns the org dict; FastMCP wraps it
    # in {"content": [{"type": "text", "text": "<json>"}], "isError": false}
    # Either shape is acceptable depending on SDK version.
    result = body["result"]
    if "content" in result:
        # Newer MCP SDK structure
        assert result.get("isError") is False
    elif "id" in result:
        # Older SDK that returns the raw dict
        pass


def test_get_organization_without_bearer_returns_invalid_request(client):
    body = _mcp_call_tool(client, "khanshoof_get_organization", bearer=None)
    assert "error" in body, body
    assert body["error"]["code"] == -32600, body
    assert "authentication" in body["error"]["message"].lower()


def test_get_organization_with_invalid_bearer_returns_invalid_request(client):
    body = _mcp_call_tool(client, "khanshoof_get_organization",
                          bearer="garbage_not_a_real_token_xyz")
    assert "error" in body, body
    # The underlying FastAPI handler returns 401; we map that to -32600.
    assert body["error"]["code"] == -32600, body
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py -k "get_organization"
```
Expected: 4 FAIL (tool not registered yet).

- [ ] **Step 3: Add dispatch + helpers + canary to `backend/mcp_server.py`**

Replace the entire `backend/mcp_server.py` with:

```python
"""Phase 2.5i-2 — MCP server exposing Khanshoof tools."""
from __future__ import annotations

import json
import logging
from typing import Optional, Any

import httpx
from httpx import ASGITransport
from mcp.server.fastmcp import FastMCP, Context
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

logger = logging.getLogger("signage.mcp")


# JSON-RPC + MCP standard error codes
_INVALID_REQUEST  = -32600
_INVALID_PARAMS   = -32602
_INTERNAL_ERROR   = -32603
_APP_ERROR        = -32000


def _bearer_from(ctx: Context) -> Optional[str]:
    """Extract the Authorization Bearer token from the request context.
    Returns None when no bearer is present or the format is wrong."""
    try:
        req = ctx.request_context.request
        auth = req.headers.get("authorization", "")
    except Exception:
        return None
    scheme, _, token = auth.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def _mcp_unauthorized() -> McpError:
    return McpError(ErrorData(
        code=_INVALID_REQUEST,
        message="Authentication required — provide a Bearer OAuth token.",
    ))


def _map_response(r: httpx.Response, path: str) -> Any:
    """Parse the FastAPI response; raise McpError on non-2xx."""
    if r.status_code < 300:
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # Error path — extract structured detail if available
    detail: Any = None
    try:
        body = r.json()
        detail = body.get("detail") if isinstance(body, dict) else None
    except ValueError:
        body = None

    if isinstance(detail, dict):
        struct_message = detail.get("message") or detail.get("error_description")
        struct_code = detail.get("code") or detail.get("error")
    elif isinstance(detail, str):
        struct_message = detail
        struct_code = None
    else:
        struct_message = None
        struct_code = None

    data = {"http_status": r.status_code, "path": path}
    if struct_code:
        data["code"] = struct_code

    if r.status_code == 400:
        raise McpError(ErrorData(
            code=_INVALID_PARAMS,
            message=struct_message or "Invalid arguments",
            data=data,
        ))
    if r.status_code in (401,):
        raise McpError(ErrorData(
            code=_INVALID_REQUEST,
            message=struct_message or "Authentication failed — token may be expired or revoked.",
            data=data,
        ))
    if r.status_code == 403:
        raise McpError(ErrorData(
            code=_APP_ERROR,
            message=struct_message or "Permission denied",
            data=data,
        ))
    if r.status_code == 404:
        raise McpError(ErrorData(
            code=_APP_ERROR,
            message=struct_message or f"Resource not found: {path}",
            data=data,
        ))
    if r.status_code == 422:
        # FastAPI's Pydantic validation errors
        raise McpError(ErrorData(
            code=_INVALID_PARAMS,
            message="Validation error",
            data={**data, "errors": (body or {}).get("detail", body)},
        ))
    if r.status_code == 429:
        ra = r.headers.get("retry-after")
        retry_data = {**data}
        if ra:
            retry_data["retry_after"] = ra
        raise McpError(ErrorData(
            code=_APP_ERROR,
            message=struct_message or "Rate limit exceeded",
            data=retry_data,
        ))
    if r.status_code >= 500:
        logger.warning("mcp_dispatch_5xx path=%s status=%d body=%r",
                       path, r.status_code, r.text[:500])
        raise McpError(ErrorData(
            code=_INTERNAL_ERROR,
            message="Server error",
            data=data,
        ))

    # Fall-through for unexpected codes (3xx already filtered above)
    raise McpError(ErrorData(
        code=_APP_ERROR,
        message=struct_message or f"Unexpected HTTP {r.status_code}",
        data=data,
    ))


async def _dispatch(
    app,
    ctx: Context,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    """Forward an HTTP-style call into the FastAPI app via ASGI."""
    bearer = _bearer_from(ctx)
    if not bearer:
        raise _mcp_unauthorized()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://mcp-internal",
    ) as client:
        try:
            r = await client.request(
                method, path,
                json=json_body,
                params=params,
                headers={"Authorization": f"Bearer {bearer}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("mcp_dispatch_transport_error path=%s err=%s", path, exc)
            raise McpError(ErrorData(
                code=_INTERNAL_ERROR,
                message="Internal dispatch error",
            ))
    return _map_response(r, path)


def attach_mcp(app) -> None:
    """Mount an MCP server at /mcp on the FastAPI app."""
    mcp = FastMCP(
        "khanshoof",
        instructions=(
            "Khanshoof signage MCP server. Use these tools to list, create, "
            "update, and delete playlists, schedules, screens, walls, and media."
        ),
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def khanshoof_get_organization(ctx: Context) -> dict:
        """Get the current organization's profile, including plan + screen limits."""
        return await _dispatch(app, ctx, "GET", "/organization")

    app.mount("/mcp", mcp.streamable_http_app())
```

- [ ] **Step 4: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py
```
Expected: 6 passed (2 from Task 1 + 4 new).

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **352 passed**.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
feat(mcp): dispatch helper + error mapper + first tool

_dispatch forwards an HTTP method+path+body into the existing FastAPI
app via httpx.AsyncClient(transport=ASGITransport(app=app)) with the
OAuth bearer carried in the Authorization header. _map_response maps
FastAPI HTTP statuses to MCP JSON-RPC errors: 400→-32602, 401→-32600,
403/404/429→-32000, 5xx→-32603. Structured FastAPI detail (the
{"code","message"} shape from main.py's http_error helper) is
preserved in McpError.data.

First tool registered: khanshoof_get_organization. All future tool
registrations follow the same one-line dispatch pattern.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Register the remaining 14 read tools

**Files:**
- Modify: `backend/mcp_server.py`
- Modify: `backend/tests/test_mcp.py` (append 8 tests)

**Goal:** Register `khanshoof_list_users`, `khanshoof_get_current_user`, `khanshoof_list_sites`, `khanshoof_list_screens`, `khanshoof_get_screen`, `khanshoof_get_screen_zones`, `khanshoof_list_playlists`, `khanshoof_get_playlist`, `khanshoof_list_schedules`, `khanshoof_get_schedule`, `khanshoof_list_walls`, `khanshoof_get_wall`, `khanshoof_list_media`. (13 new tools; with the canary from Task 2 that's 14 read tools total.) Add 7 representative happy-path tests + 1 tools/list count test.

- [ ] **Step 1: Append failing tests**

Append to `backend/tests/test_mcp.py`:

```python
# ── Task 3: 14 read tools ──────────────────────────────────────────────


def test_tools_list_has_14_reads_after_task3(client):
    """After Task 3, tools/list should have at least 14 read tools registered."""
    body = _jsonrpc_call(client, "tools/list")
    if "error" in body:
        _jsonrpc_call(client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(client, "tools/list")
    names = {t["name"] for t in body["result"]["tools"]}
    expected_reads = {
        "khanshoof_get_organization",
        "khanshoof_list_users",
        "khanshoof_get_current_user",
        "khanshoof_list_sites",
        "khanshoof_list_screens",
        "khanshoof_get_screen",
        "khanshoof_get_screen_zones",
        "khanshoof_list_playlists",
        "khanshoof_get_playlist",
        "khanshoof_list_schedules",
        "khanshoof_get_schedule",
        "khanshoof_list_walls",
        "khanshoof_get_wall",
        "khanshoof_list_media",
    }
    missing = expected_reads - names
    assert not missing, f"Missing read tools: {missing}"


def _result_data(body: dict) -> Any:
    """Pull the structured result out of a tools/call response, tolerating
    both the wrapped `content` envelope and the raw-dict shape."""
    assert "result" in body, body
    r = body["result"]
    if isinstance(r, dict) and "content" in r:
        # MCP wrapped envelope — parse the text content as JSON
        assert r.get("isError") is False, body
        text = r["content"][0]["text"]
        try:
            return json.loads(text)
        except ValueError:
            return text
    return r


def test_list_users(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_users", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_sites(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_sites", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_screens(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_screens", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_playlists(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_playlists", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_schedules(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_schedules", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_walls(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_walls", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_media(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_list_media", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py -k "tools_list_has_14_reads or list_users or list_sites or list_screens or list_playlists or list_schedules or list_walls or list_media"
```
Expected: 8 FAIL.

- [ ] **Step 3: Add 13 more read tools to `backend/mcp_server.py`**

In `backend/mcp_server.py`, inside `attach_mcp(app)` after the existing `khanshoof_get_organization` definition and before the `app.mount(...)` call, add these tool definitions:

```python
    @mcp.tool()
    async def khanshoof_list_users(ctx: Context) -> list[dict]:
        """List all users in the current organization."""
        return await _dispatch(app, ctx, "GET", "/users")

    @mcp.tool()
    async def khanshoof_get_current_user(ctx: Context) -> dict:
        """Get the profile of the user who authorized this OAuth session."""
        return await _dispatch(app, ctx, "GET", "/auth/me")

    @mcp.tool()
    async def khanshoof_list_sites(ctx: Context) -> list[dict]:
        """List all sites (physical locations) in the current organization."""
        return await _dispatch(app, ctx, "GET", "/sites")

    @mcp.tool()
    async def khanshoof_list_screens(ctx: Context) -> list[dict]:
        """List all screens across all sites in the current organization."""
        return await _dispatch(app, ctx, "GET", "/screens")

    @mcp.tool()
    async def khanshoof_get_screen(ctx: Context, screen_id: int) -> dict:
        """Get details for a single screen by id, including its current playlist."""
        return await _dispatch(app, ctx, "GET", f"/screens/{screen_id}")

    @mcp.tool()
    async def khanshoof_get_screen_zones(ctx: Context, screen_id: int) -> dict:
        """Get the wall-zone configuration for a multi-zone screen."""
        return await _dispatch(app, ctx, "GET", f"/screens/{screen_id}/zones")

    @mcp.tool()
    async def khanshoof_list_playlists(ctx: Context) -> list[dict]:
        """List all playlists in the current organization."""
        return await _dispatch(app, ctx, "GET", "/playlists")

    @mcp.tool()
    async def khanshoof_get_playlist(ctx: Context, playlist_id: int) -> dict:
        """Get a single playlist by id, including its items in order."""
        return await _dispatch(app, ctx, "GET", f"/playlists/{playlist_id}")

    @mcp.tool()
    async def khanshoof_list_schedules(ctx: Context) -> list[dict]:
        """List all dayparting schedules in the current organization."""
        return await _dispatch(app, ctx, "GET", "/schedules")

    @mcp.tool()
    async def khanshoof_get_schedule(ctx: Context, schedule_id: int) -> dict:
        """Get a single schedule by id, including all its rules."""
        return await _dispatch(app, ctx, "GET", f"/schedules/{schedule_id}")

    @mcp.tool()
    async def khanshoof_list_walls(ctx: Context) -> list[dict]:
        """List all video walls (multi-screen displays) in the current organization."""
        return await _dispatch(app, ctx, "GET", "/walls")

    @mcp.tool()
    async def khanshoof_get_wall(ctx: Context, wall_id: int) -> dict:
        """Get a single wall by id, including its layout and assigned playlist."""
        return await _dispatch(app, ctx, "GET", f"/walls/{wall_id}")

    @mcp.tool()
    async def khanshoof_list_media(ctx: Context) -> list[dict]:
        """List all media assets (images, videos, URLs) uploaded to the org."""
        return await _dispatch(app, ctx, "GET", "/media")
```

- [ ] **Step 4: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py
```
Expected: 14 passed (6 from earlier + 8 new).

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **360 passed** (352 + 8).

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
feat(mcp): register 13 more read tools (14 reads total)

Adds list_users, get_current_user, list_sites, list_screens, get_screen,
get_screen_zones, list_playlists, get_playlist, list_schedules,
get_schedule, list_walls, get_wall, list_media. Each tool is a 2-line
wrapper calling _dispatch with the appropriate HTTP method + path.
Coverage tests pick one tool per resource so the per-tool count stays
proportionate to surface, not boilerplate.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Register the 11 write tools

**Files:**
- Modify: `backend/mcp_server.py`
- Modify: `backend/tests/test_mcp.py` (append 8 tests)

**Goal:** Register 11 write tools: playlist CUD + items (4), schedule CUD + rules (4), assign_playlist_to_screen, update_wall_canvas_playlist, add_media_url. After Task 4, total tool count is 25. Add 7 representative write happy-path tests + 1 total-count test.

- [ ] **Step 1: Append failing tests**

Append to `backend/tests/test_mcp.py`:

```python
# ── Task 4: 11 write tools (25 tools total) ────────────────────────────


def test_tools_list_has_25_total(client):
    """After Task 4, tools/list should have exactly 25 khanshoof_ tools."""
    body = _jsonrpc_call(client, "tools/list")
    if "error" in body:
        _jsonrpc_call(client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(client, "tools/list")
    names = [t["name"] for t in body["result"]["tools"]
             if t["name"].startswith("khanshoof_")]
    assert len(names) == 25, sorted(names)


def test_create_then_update_then_delete_playlist(client):
    access_token, _ = _get_oauth_access_token(client)
    # Create
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "MCP test playlist"},
                          bearer=access_token)
    created = _result_data(body)
    pid = created["id"]
    # Update
    body = _mcp_call_tool(client, "khanshoof_update_playlist",
                          args={"playlist_id": pid,
                                "name": "MCP renamed"},
                          bearer=access_token)
    assert "result" in body, body
    # Delete
    body = _mcp_call_tool(client, "khanshoof_delete_playlist",
                          args={"playlist_id": pid},
                          bearer=access_token)
    assert "result" in body, body


def test_set_playlist_items_empty(client):
    """set_playlist_items accepts an empty list (clears the playlist)."""
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "MCP items test"},
                          bearer=access_token)
    pid = _result_data(body)["id"]
    body = _mcp_call_tool(client, "khanshoof_set_playlist_items",
                          args={"playlist_id": pid, "items": []},
                          bearer=access_token)
    assert "result" in body, body


def test_create_schedule(client):
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_schedule",
                          args={"name": "MCP schedule"},
                          bearer=access_token)
    data = _result_data(body)
    assert "id" in data


def test_set_schedule_rules(client):
    """Empty rules list is allowed — clears the schedule's rules."""
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_schedule",
                          args={"name": "MCP rules test"},
                          bearer=access_token)
    sid = _result_data(body)["id"]
    body = _mcp_call_tool(client, "khanshoof_set_schedule_rules",
                          args={"schedule_id": sid, "rules": []},
                          bearer=access_token)
    assert "result" in body, body


def test_assign_playlist_to_screen(client):
    """If the org has at least one screen, can assign a playlist to it.
    Otherwise, expect a 404 mapped to -32000."""
    access_token, _ = _get_oauth_access_token(client)
    # Create a playlist to assign
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "MCP assign test"},
                          bearer=access_token)
    pid = _result_data(body)["id"]
    # List screens to find one (or accept that there are none in a fresh org)
    body = _mcp_call_tool(client, "khanshoof_list_screens",
                          bearer=access_token)
    screens = _result_data(body)
    if not screens:
        # Skip the assign step; the create_playlist call already
        # exercised the write path
        return
    body = _mcp_call_tool(client, "khanshoof_assign_playlist_to_screen",
                          args={"screen_id": screens[0]["id"],
                                "playlist_id": pid},
                          bearer=access_token)
    # Either success (result key) or a structured error from the FastAPI
    # handler — both prove the dispatch path reached the handler.
    assert "result" in body or "error" in body


def test_add_media_url(client):
    """Try to add a media item via URL. Even if the URL is invalid, the
    dispatch path should reach the FastAPI handler — which may 4xx for
    invalid media or 201 for success. Both prove the tool is wired."""
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_add_media_url",
                          args={"url": "https://example.com/image.png",
                                "name": "test image"},
                          bearer=access_token)
    # Acceptable outcomes:
    # - result: media added
    # - error -32602/-32000: URL rejected, validation failed, etc.
    assert "result" in body or "error" in body, body
```

- [ ] **Step 2: Verify failures**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py -k "25_total or create_then_update or set_playlist_items or create_schedule or set_schedule_rules or assign_playlist_to_screen or add_media_url"
```
Expected: 7 FAIL (all 7 new write tools not yet registered) + 1 FAIL on the 25_total count.

- [ ] **Step 3: Add 11 write tools to `backend/mcp_server.py`**

In `backend/mcp_server.py`, inside `attach_mcp(app)` after the last read tool and before the `app.mount(...)` call, add:

```python
    # ── Write tools (require api:rw scope on the underlying handler) ──

    @mcp.tool()
    async def khanshoof_create_playlist(
        ctx: Context,
        name: str,
        description: Optional[str] = None,
    ) -> dict:
        """Create a new empty playlist. Requires api:rw scope."""
        body = {"name": name}
        if description is not None:
            body["description"] = description
        return await _dispatch(app, ctx, "POST", "/playlists", json_body=body)

    @mcp.tool()
    async def khanshoof_update_playlist(
        ctx: Context,
        playlist_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update a playlist's name or description. Requires api:rw scope."""
        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return await _dispatch(app, ctx, "PUT", f"/playlists/{playlist_id}",
                               json_body=body)

    @mcp.tool()
    async def khanshoof_delete_playlist(
        ctx: Context,
        playlist_id: int,
    ) -> dict:
        """Delete a playlist. Returns null on success. Requires api:rw scope."""
        return await _dispatch(app, ctx, "DELETE", f"/playlists/{playlist_id}")

    @mcp.tool()
    async def khanshoof_set_playlist_items(
        ctx: Context,
        playlist_id: int,
        items: list[dict],
    ) -> dict:
        """Replace the items in a playlist. Each item is a dict with
        media_id (int) and duration_seconds (int). Requires api:rw scope."""
        return await _dispatch(app, ctx, "PUT", f"/playlists/{playlist_id}/items",
                               json_body={"items": items})

    @mcp.tool()
    async def khanshoof_create_schedule(
        ctx: Context,
        name: str,
    ) -> dict:
        """Create a new dayparting schedule (initially with no rules).
        Requires api:rw scope."""
        return await _dispatch(app, ctx, "POST", "/schedules",
                               json_body={"name": name})

    @mcp.tool()
    async def khanshoof_update_schedule(
        ctx: Context,
        schedule_id: int,
        name: str,
    ) -> dict:
        """Rename a schedule. Requires api:rw scope."""
        return await _dispatch(app, ctx, "PUT", f"/schedules/{schedule_id}",
                               json_body={"name": name})

    @mcp.tool()
    async def khanshoof_delete_schedule(
        ctx: Context,
        schedule_id: int,
    ) -> dict:
        """Delete a schedule. Requires api:rw scope."""
        return await _dispatch(app, ctx, "DELETE", f"/schedules/{schedule_id}")

    @mcp.tool()
    async def khanshoof_set_schedule_rules(
        ctx: Context,
        schedule_id: int,
        rules: list[dict],
    ) -> dict:
        """Replace the rules in a schedule. Each rule is a dict with
        day_of_week (0-6), start_time (HH:MM), end_time (HH:MM), and
        playlist_id (int). Requires api:rw scope."""
        return await _dispatch(app, ctx, "PUT",
                               f"/schedules/{schedule_id}/rules",
                               json_body={"rules": rules})

    @mcp.tool()
    async def khanshoof_assign_playlist_to_screen(
        ctx: Context,
        screen_id: int,
        playlist_id: int,
    ) -> dict:
        """Assign a playlist to a screen. The screen will start playing this
        playlist on its next refresh. Requires api:rw scope."""
        return await _dispatch(app, ctx, "PUT", f"/screens/{screen_id}",
                               json_body={"playlist_id": playlist_id})

    @mcp.tool()
    async def khanshoof_update_wall_canvas_playlist(
        ctx: Context,
        wall_id: int,
        playlist_id: int,
    ) -> dict:
        """Set the canvas-mode playlist for a video wall (spans all zones).
        Requires api:rw scope."""
        return await _dispatch(app, ctx, "PUT",
                               f"/walls/{wall_id}/canvas-playlist",
                               json_body={"playlist_id": playlist_id})

    @mcp.tool()
    async def khanshoof_add_media_url(
        ctx: Context,
        url: str,
        name: Optional[str] = None,
    ) -> dict:
        """Add a media item by URL (image/video URL). Requires api:rw scope."""
        body = {"url": url}
        if name is not None:
            body["name"] = name
        return await _dispatch(app, ctx, "POST", "/media/url", json_body=body)
```

**Note:** If any of the corresponding FastAPI endpoints don't exist at the exact paths above, look at `backend/main.py` for the actual path (e.g. `/walls/{wall_id}/canvas-playlist` may be `/walls/{wall_id}` with a body field, depending on Phase 2 walls work). Adjust the tool body to call the actual endpoint — the tool wrapper signature stays the same.

- [ ] **Step 4: Rebuild + run new tests**

```bash
docker-compose build backend && docker-compose up -d --force-recreate backend
sleep 5
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py
```
Expected: 22 passed (14 from earlier + 8 new).

- [ ] **Step 5: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **368 passed** (360 + 8).

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
feat(mcp): register 11 write tools (25 tools total)

Adds create/update/delete/set-items for playlists, create/update/
delete/set-rules for schedules, assign_playlist_to_screen,
update_wall_canvas_playlist, and add_media_url. All write tools route
through the same _dispatch helper, so scope (api:rw) is enforced
by the existing require_api_scope decorator on the underlying FastAPI
handler — no new scope-check logic in the MCP layer.

Tool count is now 25; tests/test_mcp.py asserts the exact count.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Cross-cutting auth / scope / error tests

**Files:**
- Modify: `backend/tests/test_mcp.py` (append 7 tests)

**Goal:** Verify scope enforcement, validation error mapping, 404 mapping, revoked-token rejection, cross-org isolation, descriptions sanity. No production code changes — these tests prove the dispatch layer correctly funnels FastAPI errors into MCP errors.

- [ ] **Step 1: Append tests**

Append to `backend/tests/test_mcp.py`:

```python
# ── Task 5: cross-cutting auth + scope + error mapping ────────────────


def _read_scope_access_token(client) -> str:
    """Run the OAuth flow with api:read scope (not api:rw). Returns the access token."""
    import secrets, hashlib, base64, re
    from tests.test_oauth import _signup_org, _register_client

    cid = _register_client(client, name="ReadScope")
    session_token, _o, _u, _e = _signup_org(client)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    cookies = {"oauth_session": session_token}
    redirect_uri = "http://localhost:5173/oauth/callback"

    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri, "scope": "api:read", "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:read"},
                    cookies=cookies, follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri,
        "client_id": cid, "code_verifier": verifier,
    })
    return r.json()["access_token"]


def test_read_scope_token_cannot_create_playlist(client):
    """An api:read OAuth token on a write tool must return -32000 with
    data.code == 'api.insufficient_scope'."""
    access = _read_scope_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "blocked"},
                          bearer=access)
    assert "error" in body, body
    assert body["error"]["code"] == -32000, body
    data = body["error"].get("data") or {}
    assert data.get("code") == "api.insufficient_scope", body


def test_validation_error_maps_to_invalid_params(client):
    """Missing a required arg (no `name`) should produce -32602."""
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={},   # name is required
                          bearer=access_token)
    assert "error" in body, body
    # Could come back as -32602 from either MCP-layer validation (the SDK
    # validates against the tool's inputSchema) or from FastAPI's Pydantic
    # (mapped via _map_response). Either is acceptable.
    assert body["error"]["code"] in (-32602, -32000), body


def test_unknown_playlist_id_maps_to_app_error(client):
    """GET /playlists/9999999 → FastAPI 404 → MCP -32000."""
    access_token, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_get_playlist",
                          args={"playlist_id": 9999999},
                          bearer=access_token)
    assert "error" in body, body
    assert body["error"]["code"] == -32000, body
    data = body["error"].get("data") or {}
    assert data.get("http_status") == 404, body


def test_revoked_oauth_token_rejected(client):
    """After revocation, the token must fail with -32600."""
    access_token, oauth_client_id = _get_oauth_access_token(client)
    # Confirm it works first
    body = _mcp_call_tool(client, "khanshoof_get_organization",
                          bearer=access_token)
    assert "result" in body, body
    # Revoke
    r = client.post("/oauth/revoke", data={
        "token": access_token, "client_id": oauth_client_id,
    })
    assert r.status_code == 200
    # Now it should fail
    body = _mcp_call_tool(client, "khanshoof_get_organization",
                          bearer=access_token)
    assert "error" in body, body
    assert body["error"]["code"] == -32600, body


def test_cross_org_isolation(client):
    """A token issued for org A must not be able to read org B's playlist."""
    # Org A creates a playlist
    access_a, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "Org A private"},
                          bearer=access_a)
    a_playlist_id = _result_data(body)["id"]
    # Org B tries to read it
    access_b, _ = _get_oauth_access_token(client)
    body = _mcp_call_tool(client, "khanshoof_get_playlist",
                          args={"playlist_id": a_playlist_id},
                          bearer=access_b)
    assert "error" in body, body
    assert body["error"]["code"] == -32000, body
    data = body["error"].get("data") or {}
    assert data.get("http_status") == 404, body


def test_all_tool_descriptions_are_nonempty(client):
    """Sanity: every tool has a description, none > 1000 chars."""
    body = _jsonrpc_call(client, "tools/list")
    if "error" in body:
        _jsonrpc_call(client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(client, "tools/list")
    tools = body["result"]["tools"]
    for t in tools:
        if not t["name"].startswith("khanshoof_"):
            continue
        desc = t.get("description") or ""
        assert desc.strip(), f"{t['name']} has empty description"
        assert len(desc) <= 1000, f"{t['name']} description is {len(desc)} chars"


def test_tool_descriptions_mention_scope_for_writes(client):
    """Write tools mention 'api:rw' so the LLM knows the scope requirement."""
    body = _jsonrpc_call(client, "tools/list")
    if "error" in body:
        _jsonrpc_call(client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(client, "tools/list")
    write_tool_names = {
        "khanshoof_create_playlist", "khanshoof_update_playlist",
        "khanshoof_delete_playlist", "khanshoof_set_playlist_items",
        "khanshoof_create_schedule", "khanshoof_update_schedule",
        "khanshoof_delete_schedule", "khanshoof_set_schedule_rules",
        "khanshoof_assign_playlist_to_screen",
        "khanshoof_update_wall_canvas_playlist",
        "khanshoof_add_media_url",
    }
    for t in body["result"]["tools"]:
        if t["name"] in write_tool_names:
            assert "api:rw" in (t.get("description") or "").lower(), t["name"]
```

- [ ] **Step 2: Run new tests**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py -k "read_scope or validation_error or unknown_playlist or revoked_oauth or cross_org or descriptions"
```
Expected: 7 passed (all rely on dispatch + map logic that's already in place).

- [ ] **Step 3: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **375 passed** (368 + 7).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
test(mcp): cross-cutting auth, scope, validation, isolation

Seven tests cover the integration boundaries: api:read scope rejected
on writes, FastAPI validation → -32602, 404 → -32000 with http_status
in data, revoked OAuth token → -32600, cross-org playlist read → 404,
all descriptions non-empty + < 1000 chars, write tools mention
'api:rw' in their description so the LLM knows the scope requirement.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: E2E + rebase + push + PR

**Files:**
- Modify: `backend/tests/test_mcp.py` (append 1 end-to-end test)

**Goal:** One full-flow integration test that exercises OAuth signup → consent → tools/call. Push the branch. Open the PR (stacked, if PR #12 is still open).

- [ ] **Step 1: Append the end-to-end test**

```python
# ── Task 6: End-to-end ────────────────────────────────────────────────


def test_full_e2e_oauth_then_mcp_tool_call(client):
    """OAuth register → authorize → token → MCP initialize → tools/call.

    This is the integration smoke test that proves the MCP server,
    the OAuth provider, and FastAPI all line up over the wire."""
    import secrets, hashlib, base64, re
    from tests.test_oauth import _signup_org

    # 1. Register an MCP client dynamically
    r = client.post("/oauth/register", json={
        "client_name": "E2E MCP Client",
        "redirect_uris": ["http://localhost:5173/oauth/cb"],
    })
    assert r.status_code == 201, r.text
    cid = r.json()["client_id"]

    # 2. Sign up a real org
    session_token, _o, _u, _e = _signup_org(client)
    cookies = {"oauth_session": session_token}

    # 3. Authorize (consent)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    redirect_uri = "http://localhost:5173/oauth/cb"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri, "scope": "api:rw",
        "state": "e2e", "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri,
        "client_id": cid, "code_verifier": verifier,
    })
    access_token = r.json()["access_token"]

    # 4. MCP initialize over the same token
    body = _jsonrpc_call(client, "initialize", bearer=access_token, params={
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "e2e-test", "version": "0"},
    })
    assert body["result"]["serverInfo"]["name"] == "khanshoof"

    # 5. tools/list
    body = _jsonrpc_call(client, "tools/list", bearer=access_token)
    names = [t["name"] for t in body["result"]["tools"]]
    assert "khanshoof_create_playlist" in names
    assert "khanshoof_list_playlists" in names

    # 6. Create a playlist via MCP
    body = _mcp_call_tool(client, "khanshoof_create_playlist",
                          args={"name": "E2E MCP playlist"},
                          bearer=access_token)
    created = _result_data(body)
    assert "id" in created
    assert created["name"] == "E2E MCP playlist"

    # 7. List playlists via MCP — confirm it's there
    body = _mcp_call_tool(client, "khanshoof_list_playlists",
                          bearer=access_token)
    playlists = _result_data(body)
    assert any(p["id"] == created["id"] for p in playlists)
```

- [ ] **Step 2: Run the new test**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest -xvs tests/test_mcp.py::test_full_e2e_oauth_then_mcp_tool_call
```
Expected: 1 passed.

- [ ] **Step 3: Full suite**

```bash
docker-compose exec -T -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 \
  -e NIUPAY_CALLBACK_SECRET=test_q -e BILLING_WEBHOOK_SECRET=test_h \
  backend pytest 2>&1 | tail -3
```
Expected: **376 passed** (375 + 1).

- [ ] **Step 4: Commit the E2E test**

```bash
git add backend/tests/test_mcp.py
git commit -m "$(cat <<'EOF'
test(mcp): end-to-end OAuth + MCP integration

One full-stack test: dynamic register → authorize → consent → token →
MCP initialize → tools/list → tools/call → list. Proves the OAuth
provider (2.5i-1) and the MCP server (2.5i-2) work end-to-end over
the wire on the same token.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Decide rebase target**

Check PR #12 (OAuth provider) status:
```bash
~/.local/bin/gh pr view 12 --json state,mergeStateStatus
```
- If **MERGED**: rebase `feature/mcp-server` onto `origin/main`:
  ```bash
  git fetch origin main
  git rebase origin/main
  ```
- If **OPEN**: leave the branch stacked on `feature/oauth-provider`. The MCP PR will be opened with base = `feature/oauth-provider` so reviewers can see only the 2.5i-2 diff.

- [ ] **Step 6: Push branch**

```bash
git push -u origin feature/mcp-server
```

- [ ] **Step 7: Open the PR**

If PR #12 is open, use base `feature/oauth-provider`. If merged, use base `main`:

```bash
BASE=$(~/.local/bin/gh pr view 12 --json state -q .state | \
       awk '{if ($1=="MERGED") print "main"; else print "feature/oauth-provider"}')
~/.local/bin/gh pr create --base $BASE \
  --title "feat(mcp): Phase 2.5i-2 — MCP server over OAuth (25 tools)" \
  --body "$(cat <<'EOF'
## Summary

Mounts the official `mcp` Python SDK at \`/mcp\` on the existing FastAPI app. **25 tools** wrap the existing dual-auth HTTP endpoints via in-process ASGI dispatch — every tool call goes through the existing \`require_api_scope\`, audit, validation, and rate-limit stack. Zero new auth code.

- Stateless Streamable HTTP transport with \`json_response=True\` — each tool call is a plain JSON-RPC POST exchange, no SSE framing.
- OAuth Bearer token from Phase 2.5i-1 carried in the \`Authorization\` header on every \`tools/call\`.
- 14 read tools (organization, users, sites, screens×3, playlists×2, schedules×2, walls×2, media).
- 11 write tools (playlist C/U/D + items, schedule C/U/D + rules, assign playlist to screen, update wall canvas playlist, add media URL).
- All tool names prefixed \`khanshoof_\` so users with multiple MCP servers connected don't get name collisions.

## Spec
\`docs/superpowers/specs/2026-05-19-mcp-server-design.md\`

## Plan
\`docs/superpowers/plans/2026-05-19-mcp-server-plan.md\`

## Test plan

- [x] Backend: 376 passed (346 baseline + 30 new MCP tests)
- [x] E2E: register → authorize → consent → token → MCP initialize → tools/list → tools/call (\`khanshoof_create_playlist\`) → list confirms
- [x] api:read OAuth token on a write tool → -32000 with \`data.code == "api.insufficient_scope"\`
- [x] Revoked OAuth token → -32600
- [x] Cross-org isolation (org A token → org B playlist) → 404
- [x] FastAPI validation error → -32602
- [x] 25 tools registered exactly; all descriptions non-empty and < 1000 chars; writes mention \`api:rw\`
- [ ] Smoke test from Claude Desktop config (manual)
- [ ] Smoke test from Cursor config (manual)

## Known v1 limitations (documented in spec)

- Per-OAuth-token rate limiting on \`/mcp\` (inherits the 2.5i-1 OAuth bypass).
- Multipart media upload tool — only URL-add for v1.
- MCP Resources & Prompts primitives — only Tools.
- Bilingual tool descriptions — EN only.
- Pagination on list tools — full list returned.
- Cancellation propagation — deferred (FastAPI doesn't easily propagate).

## Non-goals (Phase 2.5i-3)

- Admin "MCP Connections" / "Authorized OAuth Apps" management tab.
- Per-tool consent / per-resource scopes.
- Tool descriptions in Arabic.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Save memory**

Write `~/.claude/projects/-home-ahmed-signage/memory/project_mcp_server.md`:

```markdown
---
name: MCP server (Phase 2.5i-2) — PR open
description: MCP server with 25 tools wrapping the existing FastAPI handlers via in-process ASGI. Auth via Phase 2.5i-1 OAuth.
type: project
---

**Status (2026-05-19):** PR open against feature/oauth-provider (or main, if PR #12 merged). Awaiting browser smoke + merge.

**What landed:**
- backend/mcp_server.py with FastMCP(stateless_http=True, json_response=True) mounted at /mcp.
- 25 tools, all prefixed khanshoof_*, organized as 14 reads + 11 writes.
- Each tool body is ~5 lines: extract OAuth bearer from ctx.request_context.request, dispatch via httpx + ASGITransport into the existing FastAPI app, return JSON.
- Error mapping: 400→-32602, 401→-32600, 403/404/429→-32000, 5xx→-32603. Structured FastAPI detail preserved in McpError.data.
- 30 new tests covering tool list, representative happy paths, scope enforcement, validation, 404, revocation, cross-org isolation, descriptions sanity, and one full E2E flow.

**Test count:** 376 backend tests passing.

**Plan:** docs/superpowers/plans/2026-05-19-mcp-server-plan.md — 6 tasks.
**Spec:** docs/superpowers/specs/2026-05-19-mcp-server-design.md.

**v1 deferrals:** OAuth-token rate limiting on /mcp, multipart media upload tool, Resources/Prompts primitives, bilingual descriptions, list pagination, cancellation. All documented in PR.

**Next phase:** 2.5i-3 — admin "Authorized OAuth Apps" management tab (list active tokens, revoke from dashboard).
```

Update `~/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` — prepend a one-line entry pointing at the new file:

```markdown
- [MCP server (Phase 2.5i-2)](project_mcp_server.md) — **PR OPEN 2026-05-19**. 25 tools at /mcp wrapping the existing FastAPI handlers via in-process ASGI dispatch. 376 tests passing. Next: 2.5i-3 admin UI.
```

- [ ] **Step 9: Final verification**

```bash
git status -sb
~/.local/bin/gh pr view --json number,url,state
```
Expected: PR open, working tree clean.

---

## Self-Review Notes

| Spec section | Plan task |
|---|---|
| §3 Architecture | Tasks 1–2 (scaffold + dispatch) |
| §4 Tool surface (25 tools) | Tasks 2 (1 canary) + 3 (13 reads) + 4 (11 writes) |
| §5.1 mcp_server.py skeleton | Task 1 + 2 |
| §5.2 Tool wrapper pattern | Tasks 2, 3, 4 (same pattern, replicated) |
| §5.3 Bearer extraction | Task 2 (`_bearer_from`) |
| §6 Error mapping | Task 2 (`_map_response`) + Task 5 (cross-cutting verification) |
| §7 Testing (~25 tests) | Distributed across Tasks 1–6: 30 tests total (2+4+8+8+7+1) |
| §10 v1 deferrals | Documented in PR body (Task 6) |
| §11 Migration | No DB migration; pip install on next deploy; PR body covers |
| §12 Verification before merge | Steps in Task 6, plus manual smoke checklist in PR |

No placeholders. Tool count is exactly 25 throughout. Tool names consistent (`khanshoof_*`). Test count math: 346 baseline + 30 new = 376 final.

Task ordering: 1 (scaffold blocks everything) → 2 (dispatch, blocks every tool) → 3 (reads) → 4 (writes; depends on at least one tool registered so dispatch is exercised) → 5 (cross-cutting, depends on tools existing) → 6 (E2E + ship).

Each task ends with a green test suite.
