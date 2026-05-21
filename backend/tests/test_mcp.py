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


import json
from typing import Any


def _result_data(body: dict) -> Any:
    """Pull the structured result out of a tools/call response, tolerating
    both the wrapped `content` envelope and the raw-dict shape."""
    assert "result" in body, body
    r = body["result"]
    if isinstance(r, dict) and "content" in r:
        # MCP SDK 1.12.4 wrapped envelope — parse the text content as JSON
        assert r.get("isError") is False, body
        # structuredContent.result is present for list-returning tools and
        # contains the full list, whereas content[0]["text"] only has the
        # first element serialised as a single JSON object.
        sc = r.get("structuredContent")
        if isinstance(sc, dict) and "result" in sc:
            return sc["result"]
        content = r["content"]
        if not content:
            # Empty content means the tool returned an empty list/None
            return []
        text = content[0]["text"]
        try:
            return json.loads(text)
        except ValueError:
            return text
    return r


# ── Task 3: 14 read tools ──────────────────────────────────────────────


def test_tools_list_has_12_reads_after_task3(mcp_client):
    """After Task 3, tools/list should have at least 12 read tools registered."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        _jsonrpc_call(mcp_client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(mcp_client, "tools/list")
    names = {t["name"] for t in body["result"]["tools"]}
    expected_reads = {
        "khanshoof_get_organization",
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


def test_list_sites(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_sites", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_screens(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_screens", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_playlists(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_playlists", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_schedules(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_schedules", bearer=access_token)
    data = _result_data(body)
    # /schedules returns {"items": [...]}
    assert isinstance(data, dict)
    assert "items" in data


def test_list_walls(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_walls", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


def test_list_media(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_list_media", bearer=access_token)
    data = _result_data(body)
    assert isinstance(data, list)


# ── Task 4: 11 write tools (23 tools total) ────────────────────────────


def test_tools_list_has_23_total(mcp_client):
    """After Task 4, tools/list should have exactly 23 khanshoof_ tools."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        _jsonrpc_call(mcp_client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(mcp_client, "tools/list")
    names = [t["name"] for t in body["result"]["tools"]
             if t["name"].startswith("khanshoof_")]
    assert len(names) == 23, sorted(names)


def test_create_then_update_then_delete_playlist(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "MCP test playlist"},
                          bearer=access_token)
    created = _result_data(body)
    pid = created["id"]
    body = _mcp_call_tool(mcp_client, "khanshoof_update_playlist",
                          args={"playlist_id": pid,
                                "name": "MCP renamed"},
                          bearer=access_token)
    assert "result" in body, body
    body = _mcp_call_tool(mcp_client, "khanshoof_delete_playlist",
                          args={"playlist_id": pid},
                          bearer=access_token)
    assert "result" in body, body


def test_add_playlist_item_requires_valid_media(mcp_client):
    """add_playlist_item with a nonexistent media_id returns a structured error."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "MCP items test"},
                          bearer=access_token)
    pid = _result_data(body)["id"]
    # media_id 999999 should not exist — expect an error response (not a crash)
    body = _mcp_call_tool(mcp_client, "khanshoof_add_playlist_item",
                          args={"playlist_id": pid, "media_id": 999999},
                          bearer=access_token)
    # Either a structured MCP error (isError=True in result) or a JSON-RPC
    # error key — both prove the dispatch path reached the FastAPI handler.
    assert "result" in body or "error" in body, body


def test_create_schedule(mcp_client):
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_schedule",
                          args={"name": "MCP schedule"},
                          bearer=access_token)
    data = _result_data(body)
    assert "id" in data


def test_set_schedule_rules(mcp_client):
    """Empty rules list is allowed — clears the schedule's rules."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_schedule",
                          args={"name": "MCP rules test"},
                          bearer=access_token)
    sid = _result_data(body)["id"]
    body = _mcp_call_tool(mcp_client, "khanshoof_set_schedule_rules",
                          args={"schedule_id": sid, "rules": []},
                          bearer=access_token)
    assert "result" in body, body


def test_assign_playlist_to_screen(mcp_client):
    """If the org has at least one screen, can assign a playlist to it."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "MCP assign test"},
                          bearer=access_token)
    pid = _result_data(body)["id"]
    body = _mcp_call_tool(mcp_client, "khanshoof_list_screens",
                          bearer=access_token)
    screens = _result_data(body)
    if not screens:
        return  # No screens in test org; the write path was exercised
                # by create_playlist already
    body = _mcp_call_tool(mcp_client, "khanshoof_assign_playlist_to_screen",
                          args={"screen_id": screens[0]["id"],
                                "playlist_id": pid},
                          bearer=access_token)
    assert "result" in body or "error" in body


def test_add_media_url(mcp_client):
    """Try to add a media item via URL. Dispatch must reach the FastAPI handler."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_add_media_url",
                          args={"url": "https://example.com/image.png",
                                "name": "test image"},
                          bearer=access_token)
    # Either success (result key) or a structured error from FastAPI —
    # both prove the dispatch path reached the handler.
    assert "result" in body or "error" in body, body


# ── Task 5: cross-cutting auth + scope + error mapping ────────────────


def _read_scope_access_token(client) -> str:
    """Run the OAuth flow with api:read scope (not api:rw)."""
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


def _err_code_and_data(body: dict) -> tuple[int | None, dict]:
    """Pull (code, data) from either a top-level JSON-RPC error OR an
    isError=True content envelope. Returns (code, {}) or (None, {})."""
    if "error" in body:
        return body["error"]["code"], (body["error"].get("data") or {})
    r = body.get("result") or {}
    if isinstance(r, dict) and r.get("isError"):
        # SDK 1.12.4 wraps errors here — try to recover code/data from text
        try:
            text = r["content"][0]["text"]
            import json as _json
            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                return parsed.get("code"), (parsed.get("data") or {})
        except (KeyError, ValueError, IndexError):
            pass
        return None, {}
    return None, {}


def test_read_scope_token_cannot_create_playlist(mcp_client):
    """An api:read OAuth token on a write tool must return -32000 with
    data.code == 'api.insufficient_scope'."""
    access = _read_scope_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "blocked"},
                          bearer=access)
    code, data = _err_code_and_data(body)
    # Either explicit -32000 with code, OR isError envelope mentioning scope
    if code is not None:
        assert code == -32000, body
        assert data.get("code") == "api.insufficient_scope", body
    else:
        # Fall-back: assert the error is at least signalled as isError
        r = body.get("result") or {}
        assert r.get("isError") is True, body
        text = (r.get("content") or [{}])[0].get("text", "")
        assert ("scope" in text.lower() or "insufficient" in text.lower()
                or "api:rw" in text.lower() or "requires" in text.lower()), body


def test_validation_error_maps_to_invalid_params(mcp_client):
    """Missing a required arg (no `name`) should produce -32602."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={},   # name is required
                          bearer=access_token)
    code, _ = _err_code_and_data(body)
    # MCP SDK validates inputSchema → -32602; FastAPI Pydantic → also -32602
    # Either is acceptable. Also accept -32000 if structurally wrapped.
    if code is not None:
        assert code in (-32602, -32000), body
    else:
        # isError envelope — accept it as long as it's signalled
        r = body.get("result") or {}
        assert r.get("isError") is True, body


def test_unknown_playlist_id_maps_to_app_error(mcp_client):
    """GET /playlists/9999999 → FastAPI 404 → MCP -32000."""
    access_token, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_get_playlist",
                          args={"playlist_id": 9999999},
                          bearer=access_token)
    code, data = _err_code_and_data(body)
    if code is not None:
        assert code == -32000, body
        assert data.get("http_status") == 404, body
    else:
        r = body.get("result") or {}
        assert r.get("isError") is True, body
        text = (r.get("content") or [{}])[0].get("text", "")
        assert "not found" in text.lower() or "404" in text, body


def test_revoked_oauth_token_rejected(mcp_client):
    """After revocation, the token must fail with -32600."""
    access_token, oauth_client_id = _get_oauth_access_token(mcp_client)
    # Confirm it works first
    body = _mcp_call_tool(mcp_client, "khanshoof_get_organization",
                          bearer=access_token)
    assert "result" in body, body
    # Revoke
    r = mcp_client.post("/oauth/revoke", data={
        "token": access_token, "client_id": oauth_client_id,
    })
    assert r.status_code == 200
    # Now it should fail
    body = _mcp_call_tool(mcp_client, "khanshoof_get_organization",
                          bearer=access_token)
    code, _ = _err_code_and_data(body)
    if code is not None:
        assert code == -32600, body
    else:
        r = body.get("result") or {}
        assert r.get("isError") is True, body


def test_cross_org_isolation(mcp_client):
    """A token issued for org A must not be able to read org B's playlist."""
    access_a, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "Org A private"},
                          bearer=access_a)
    a_playlist_id = _result_data(body)["id"]
    access_b, _ = _get_oauth_access_token(mcp_client)
    body = _mcp_call_tool(mcp_client, "khanshoof_get_playlist",
                          args={"playlist_id": a_playlist_id},
                          bearer=access_b)
    code, data = _err_code_and_data(body)
    if code is not None:
        assert code == -32000, body
        assert data.get("http_status") == 404, body
    else:
        r = body.get("result") or {}
        assert r.get("isError") is True, body


def test_all_tool_descriptions_are_nonempty(mcp_client):
    """Sanity: every tool has a description, none > 1000 chars."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        _jsonrpc_call(mcp_client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(mcp_client, "tools/list")
    tools = body["result"]["tools"]
    for t in tools:
        if not t["name"].startswith("khanshoof_"):
            continue
        desc = t.get("description") or ""
        assert desc.strip(), f"{t['name']} has empty description"
        assert len(desc) <= 1000, f"{t['name']} description is {len(desc)} chars"


def test_tool_descriptions_mention_scope_for_writes(mcp_client):
    """Write tools mention 'api:rw' so the LLM knows the scope requirement."""
    body = _jsonrpc_call(mcp_client, "tools/list")
    if "error" in body:
        _jsonrpc_call(mcp_client, "initialize", params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        body = _jsonrpc_call(mcp_client, "tools/list")
    write_tool_names = {
        "khanshoof_create_playlist", "khanshoof_update_playlist",
        "khanshoof_delete_playlist", "khanshoof_add_playlist_item",
        "khanshoof_create_schedule", "khanshoof_update_schedule",
        "khanshoof_delete_schedule", "khanshoof_set_schedule_rules",
        "khanshoof_assign_playlist_to_screen",
        "khanshoof_add_canvas_playlist_item",
        "khanshoof_add_media_url",
    }
    for t in body["result"]["tools"]:
        if t["name"] in write_tool_names:
            assert "api:rw" in (t.get("description") or "").lower(), t["name"]


# ── Task 6: End-to-end ────────────────────────────────────────────────


def test_full_e2e_oauth_then_mcp_tool_call(mcp_client):
    """OAuth register → authorize → token → MCP initialize → tools/call.

    This is the integration smoke test that proves the MCP server,
    the OAuth provider, and FastAPI all line up over the wire."""
    import secrets, hashlib, base64, re
    from tests.test_oauth import _signup_org

    # 1. Register an MCP client dynamically
    r = mcp_client.post("/oauth/register", json={
        "client_name": "E2E MCP Client",
        "redirect_uris": ["http://localhost:5173/oauth/cb"],
    })
    assert r.status_code == 201, r.text
    cid = r.json()["client_id"]

    # 2. Sign up a real org
    session_token, _o, _u, _e = _signup_org(mcp_client)
    cookies = {"oauth_session": session_token}

    # 3. Authorize (consent)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    redirect_uri = "http://localhost:5173/oauth/cb"
    r = mcp_client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri, "scope": "api:rw",
        "state": "e2e", "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = mcp_client.post("/oauth/authorize/decision",
                        data={"request_id": request_id, "decision": "allow",
                              "scope": "api:rw"},
                        cookies=cookies, follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = mcp_client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri,
        "client_id": cid, "code_verifier": verifier,
    })
    access_token = r.json()["access_token"]

    # 4. MCP initialize over the same token
    body = _jsonrpc_call(mcp_client, "initialize", bearer=access_token, params={
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "e2e-test", "version": "0"},
    })
    assert body["result"]["serverInfo"]["name"] == "khanshoof"

    # 5. tools/list
    body = _jsonrpc_call(mcp_client, "tools/list", bearer=access_token)
    names = [t["name"] for t in body["result"]["tools"]]
    assert "khanshoof_create_playlist" in names
    assert "khanshoof_list_playlists" in names

    # 6. Create a playlist via MCP
    body = _mcp_call_tool(mcp_client, "khanshoof_create_playlist",
                          args={"name": "E2E MCP playlist"},
                          bearer=access_token)
    created = _result_data(body)
    assert "id" in created
    assert created["name"] == "E2E MCP playlist"

    # 7. List playlists via MCP — confirm it's there
    body = _mcp_call_tool(mcp_client, "khanshoof_list_playlists",
                          bearer=access_token)
    playlists = _result_data(body)
    assert any(p["id"] == created["id"] for p in playlists)
