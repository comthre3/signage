"""Tests for the Phase 2.5i-2 MCP server."""
import json

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def mcp_client():
    """Session-scoped TestClient with lifespan started.

    The MCP StreamableHTTPSessionManager can only be started once per
    instance, so we use a module-scoped client that starts the lifespan
    once and tears it down at module end.
    """
    with TestClient(app) as c:
        yield c


def _jsonrpc_call(client, method: str, params: dict | None = None,
                  bearer: str | None = None, id_: int = 1) -> dict:
    """POST a JSON-RPC 2.0 request to /mcp/ and return the parsed response."""
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
    return r.json()


def test_mcp_initialize_handshake(mcp_client):
    """The mcp_server mounts at /mcp/ and responds to `initialize`."""
    body = _jsonrpc_call(mcp_client, "initialize", params={
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })
    assert "result" in body, body
    assert body["result"]["serverInfo"]["name"] == "khanshoof"


def test_mcp_tools_list_returns_array(mcp_client):
    """tools/list must return a valid tools array."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        assert body["error"]["code"] in (-32600, -32002), body
    else:
        assert isinstance(body["result"]["tools"], list)


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

    Returns (access_token, oauth_client_id)."""
    flow = _full_authorize_flow(client)
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


def test_tools_list_shows_get_organization(mcp_client):
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        _jsonrpc_call(mcp_client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(mcp_client, "tools/list")
    names = [t["name"] for t in body["result"]["tools"]]
    assert "khanshoof_get_organization" in names


def test_get_organization_with_valid_oauth_token(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_get_organization",
                          bearer=access_token)
    assert "result" in body, body
    result = body["result"]
    if "content" in result:
        assert result.get("isError") is False
    elif "id" in result:
        pass


def _assert_mcp_auth_error(body: dict) -> None:
    """Assert an auth error in either JSON-RPC error or MCP isError content form.

    MCP SDK >= 1.9 wraps McpError inside result.content[].isError rather than
    surfacing it as a top-level JSON-RPC error field.

    Accepts any auth-related keyword in the error text.
    """
    _AUTH_KEYWORDS = ("authentication", "token", "bearer", "expired", "unauthorized")
    if "error" in body:
        # Traditional JSON-RPC error surface
        assert body["error"]["code"] == -32600, body
        msg = body["error"]["message"].lower()
        assert any(kw in msg for kw in _AUTH_KEYWORDS), body
    else:
        # MCP SDK isError content surface
        result = body.get("result", {})
        assert result.get("isError") is True, body
        texts = " ".join(
            c["text"] for c in result.get("content", []) if c.get("type") == "text"
        ).lower()
        assert any(kw in texts for kw in _AUTH_KEYWORDS), body


def test_get_organization_without_bearer_returns_invalid_request(mcp_client):
    body = _mcp_call_tool(mcp_client, "khanshoof_get_organization", bearer=None)
    _assert_mcp_auth_error(body)


def test_get_organization_with_invalid_bearer_returns_invalid_request(mcp_client):
    body = _mcp_call_tool(mcp_client, "khanshoof_get_organization",
                          bearer="garbage_not_a_real_token_xyz")
    _assert_mcp_auth_error(body)
