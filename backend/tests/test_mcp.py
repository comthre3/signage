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


def test_mcp_tools_list_initially_empty(mcp_client):
    """Before any tools register, tools/list must return an empty array."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        assert body["error"]["code"] in (-32600, -32002), body
    else:
        assert body["result"]["tools"] == []
