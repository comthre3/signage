# Phase 2.5i-2 — MCP Server Design

**Status:** Approved 2026-05-19 (brainstorm)
**Branch (when implemented):** `feature/mcp-server`
**Depends on:** Phase 2.5i-1 (OAuth provider) — PR #12

---

## 1. Goal

Expose the Khanshoof signage HTTP API as a Model Context Protocol (MCP) server so Claude Desktop, Claude Code, Cursor, Zed, and other MCP-capable clients can drive content management directly through agent conversations.

Authentication uses the OAuth 2.1 + PKCE tokens issued by Phase 2.5i-1. No new credential type is introduced. Tool surface is the curated set of read + content-write operations already available via the dual-auth FastAPI endpoints.

## 2. Constraints (decided in brainstorm)

| # | Decision | Rationale |
|---|---|---|
| Q1 | Use the official `mcp` Python SDK | Protocol framing is non-trivial; SDK is Anthropic-stewarded and tracks spec |
| Q2 | Tool surface = **read + content writes** (~25 tools) | Admin ops (users, keys, sites) stay in the dashboard |
| Q3 | Dispatch via in-process `httpx.AsyncClient` + `ASGITransport(app=app)` | Reuses 1:1 the existing scope/audit/rate-limit/validation infrastructure |
| Q4 | Tool names are `khanshoof_<verb_resource>` | Namespaced to avoid collision when users have multiple MCP servers |
| Q5 | No MCP-specific audit entries | Per-action `audit_log` entries already fire on the underlying HTTP calls |

## 3. Architecture

```
+-----------------------+
| MCP client            |
| (Claude Desktop etc.) |
+-----------+-----------+
            | Streamable HTTP (POST /mcp)
            | Authorization: Bearer <oauth_access_token>
            v
+-------------------------------+
|  FastAPI app (mounted at /mcp) |
|  via mcp SDK's streamable_http |
+-----------+-------------------+
            |
            | dispatch to @server.tool() handler
            v
+--------------------+         +------------------------+
| Tool wrapper body  |  ASGI   | Existing FastAPI route |
| - read bearer      | ------> | (require_api_scope,    |
| - build payload    |   in-   |  audit, rate limit,    |
| - httpx.dispatch   | process |  validation, handler)  |
| - map errors       | <------ +------------------------+
+--------------------+
```

Everything orange (existing) is unchanged. Everything blue (new) is `backend/mcp_server.py` + a one-line mount in `backend/main.py`.

## 4. Tool surface (25 tools)

All tool names use the `khanshoof_` prefix. Each tool's `inputSchema` is a hand-written JSON Schema; descriptions are LLM-readable English. Read tools accept `api:read` or `api:rw`; write tools require `api:rw` (enforced by the existing `require_api_scope` decorator on the underlying FastAPI handler).

### Organization & users (3 reads)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_get_organization` | GET | `/organization` |
| `khanshoof_list_users` | GET | `/users` |
| `khanshoof_get_current_user` | GET | `/auth/me` |

### Sites (1 read)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_sites` | GET | `/sites` |

### Screens (4)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_screens` | GET | `/screens` |
| `khanshoof_get_screen` | GET | `/screens/{screen_id}` |
| `khanshoof_assign_playlist_to_screen` | PUT | `/screens/{screen_id}` (body: `{playlist_id}`) |
| `khanshoof_get_screen_zones` | GET | `/screens/{screen_id}/zones` |

### Playlists (6)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_playlists` | GET | `/playlists` |
| `khanshoof_get_playlist` | GET | `/playlists/{playlist_id}` |
| `khanshoof_create_playlist` | POST | `/playlists` |
| `khanshoof_update_playlist` | PUT | `/playlists/{playlist_id}` |
| `khanshoof_delete_playlist` | DELETE | `/playlists/{playlist_id}` |
| `khanshoof_set_playlist_items` | PUT | `/playlists/{playlist_id}/items` |

### Schedules (6)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_schedules` | GET | `/schedules` |
| `khanshoof_get_schedule` | GET | `/schedules/{schedule_id}` |
| `khanshoof_create_schedule` | POST | `/schedules` |
| `khanshoof_update_schedule` | PUT | `/schedules/{schedule_id}` |
| `khanshoof_delete_schedule` | DELETE | `/schedules/{schedule_id}` |
| `khanshoof_set_schedule_rules` | PUT | `/schedules/{schedule_id}/rules` |

### Walls (3)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_walls` | GET | `/walls` |
| `khanshoof_get_wall` | GET | `/walls/{wall_id}` |
| `khanshoof_update_wall_canvas_playlist` | PUT | `/walls/{wall_id}/canvas-playlist` |

### Media (2)
| Tool | Method | Path |
|---|---|---|
| `khanshoof_list_media` | GET | `/media` |
| `khanshoof_add_media_url` | POST | `/media/url` (URL-based add; multipart upload deferred to dashboard) |

### Out of scope (in the dashboard only)
- Screen pair-code create/delete — too sensitive for LLM mediation
- User CRUD, API key CRUD, site CRUD — admin-dashboard operations
- Audit log query — not natural for an agent workflow
- Subscription / billing / Stripe checkout flows
- Multipart media upload (binary stream over MCP is awkward)

## 5. Dispatch flow

### 5.1 `mcp_server.py` skeleton

```python
"""Phase 2.5i-2 — MCP server exposing Khanshoof tools."""
from typing import Optional
import httpx
from httpx import ASGITransport
from mcp.server.fastmcp import FastMCP
from fastapi import HTTPException


def attach_mcp(app):
    """Mount the MCP server onto the existing FastAPI app at /mcp."""
    mcp = FastMCP("khanshoof", instructions=_INSTRUCTIONS)

    async def _dispatch(ctx, method: str, path: str,
                        json_body: Optional[dict] = None,
                        params: Optional[dict] = None) -> dict:
        """Forward the call into the FastAPI app via ASGI."""
        bearer = _bearer_from(ctx)
        if not bearer:
            raise _mcp_unauthorized()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://mcp-internal",
        ) as client:
            r = await client.request(
                method, path,
                json=json_body, params=params,
                headers={"Authorization": f"Bearer {bearer}"},
            )
        return _map_response(r)

    # 25 @mcp.tool() registrations follow, all calling _dispatch().
    _register_tools(mcp, _dispatch)

    app.mount("/mcp", mcp.streamable_http_app())
```

`backend/main.py` adds one line at the end of the app setup:

```python
from mcp_server import attach_mcp
attach_mcp(app)
```

### 5.2 Tool wrapper pattern

Every tool follows the same shape:

```python
@mcp.tool()
async def khanshoof_list_playlists(ctx: Context) -> list[dict]:
    """List all playlists in the organization."""
    return await _dispatch(ctx, "GET", "/playlists")


@mcp.tool()
async def khanshoof_create_playlist(
    ctx: Context,
    name: str,
    description: Optional[str] = None,
) -> dict:
    """Create a new playlist. Requires api:rw scope."""
    return await _dispatch(ctx, "POST", "/playlists",
                           json_body={"name": name,
                                      "description": description})


@mcp.tool()
async def khanshoof_assign_playlist_to_screen(
    ctx: Context,
    screen_id: int,
    playlist_id: int,
) -> dict:
    """Assign a playlist to a screen. Requires api:rw scope."""
    return await _dispatch(ctx, "PUT", f"/screens/{screen_id}",
                           json_body={"playlist_id": playlist_id})
```

### 5.3 Bearer extraction

The MCP SDK passes the request `Context` to each tool function. For the Streamable HTTP transport, the underlying Starlette request is available at `ctx.request_context.request`. We extract the `Authorization` header from there:

```python
def _bearer_from(ctx) -> Optional[str]:
    try:
        req = ctx.request_context.request
        auth = req.headers.get("authorization", "")
    except Exception:
        return None
    scheme, _, token = auth.partition(" ")
    return token if scheme.lower() == "bearer" else None
```

## 6. Error mapping

| FastAPI response | MCP error code | Message strategy |
|---|---|---|
| 2xx | (success) | Return `r.json()` directly as tool result |
| 400 with structured `detail` | `INVALID_PARAMS` (-32602) | `message=detail.message`, `data.code=detail.code` |
| 400 with string `detail` | `INVALID_PARAMS` (-32602) | `message=detail`, no data |
| 401 | `INVALID_REQUEST` (-32600) | "Authentication failed — token may be expired or revoked" |
| 403 | application error (-32000) | "Permission denied" + structured code (e.g. `api.insufficient_scope`) in data |
| 404 | application error (-32000) | "Resource not found: `<path>`" |
| 429 | application error (-32000) | message + `data.retry_after` (from `Retry-After` header) |
| 5xx | `INTERNAL_ERROR` (-32603) | Generic "Server error"; full response logged server-side via `logger.warning` |
| network/transport failure | `INTERNAL_ERROR` (-32603) | "Internal dispatch error" |

Implementation lives in a single `_map_response(r) -> dict` helper that returns the parsed body on 2xx or raises `mcp.shared.exceptions.McpError(ErrorData(code=..., message=..., data=...))` otherwise.

## 7. Testing

### 7.1 Test session helper

```python
def _mcp_test_session(client, bearer: str) -> dict:
    """Open a Streamable HTTP MCP session via the test client. Returns the
    session response so per-test code can issue tools/list, tools/call etc."""
    return client.post("/mcp", json={"jsonrpc":"2.0", "id":1,
                                     "method":"initialize", ...},
                       headers={"Authorization": f"Bearer {bearer}"})
```

The MCP SDK ships its own test helper utilities; we use whichever fits cleanest with the existing pytest setup. If the SDK helpers prove inconvenient over Streamable HTTP, we hand-write the JSON-RPC envelopes — 30 lines of test infrastructure.

### 7.2 Test coverage (~25 tests)

**Tool list (1)**: `tools/list` returns exactly 25 tools, each with non-empty `description` and a valid `inputSchema`.

**Per-tool happy path (~15 representative tests)**: not 25 — we don't need to test every CRUD verb on every resource. Cover: list + get + create + update + delete for one resource (playlists), plus one tool per other resource (sites, screens, schedules, walls, media, users, organization).

**Cross-cutting (~9 tests)**:
- Missing bearer → `INVALID_REQUEST` -32600
- Invalid bearer → `INVALID_REQUEST` -32600
- Revoked OAuth token (via /oauth/revoke first) → `INVALID_REQUEST` -32600
- `api:read` token calling a write tool → -32000 with `data.code == "api.insufficient_scope"`
- FastAPI validation error (e.g. missing required field in `create_playlist`) → -32602
- 404 on unknown ID → -32000 with `data.path` populated
- Subscription expired (mock the gate) → -32000 — preserves Phase 2.5f trial enforcement
- Unauthorized to access another org's resource (cross-org isolation) → 404
- All tool descriptions are non-empty and < 1000 chars (sanity)

### 7.3 Test count target

Existing baseline at 2.5i-1 merge: 346. New tests: ~25. New target: **~371**.

## 8. File layout & deltas

| File | Change | Lines |
|---|---|---|
| `backend/mcp_server.py` | NEW — SDK setup + 25 tool registrations + dispatch helper + error mapper | ~400 |
| `backend/main.py` | Append `from mcp_server import attach_mcp; attach_mcp(app)` | +2 |
| `backend/tests/test_mcp.py` | NEW — ~25 tests + test session helper | ~600 |
| `backend/requirements.txt` | Add `mcp` (the official SDK) | +1 |
| `docs/superpowers/specs/2026-05-19-mcp-server-design.md` | This document | — |

Total new code: ~1,000 lines, of which ~600 are tests and ~400 are the actual server. No frontend changes (admin UI for MCP connections is Phase 2.5i-3).

## 9. Failure modes & operational notes

**MCP SDK ABI churn.** The MCP spec is young; the Python SDK may break compatibility across minor versions. Pin to a specific version in `requirements.txt`. Re-pin deliberately, not on a Dependabot whim.

**ASGITransport in-process call cost.** Per-call cost is microseconds — no socket, no serialization across processes. The cost lives entirely in the FastAPI handler itself (DB query, etc.), so latency is identical to direct HTTP usage of the same endpoint.

**Streamable HTTP server-sent events on long-running tools.** Some tools (e.g. listing 10k media items) could conceivably take seconds. The MCP SDK handles server-side streaming; we just `return` the final result. Progress notifications are deferred — no current tool needs them.

**OAuth token expiry mid-session.** Access tokens expire after 1 hour. An MCP session that lasts longer just gets a 401 on the next tool call; the client refreshes via `/oauth/token` (refresh grant) and retries. No special handling needed in the MCP server.

**Cross-org isolation.** The OAuth token carries `organization_id`. Every FastAPI handler already filters by `principal.organization_id`. The MCP layer adds no new isolation logic and therefore introduces no new cross-org risk. Test 7.2 verifies.

**Tool descriptions.** These are what the LLM sees when deciding *which* tool to call. Each must be one sentence, action-oriented, mention scope when relevant. We'll iterate on description quality post-launch — the existing test only asserts non-empty.

## 10. v1 deferrals (documented in PR)

- Per-OAuth-token rate limiting on `/mcp` (inherits the 2.5i-1 OAuth bypass).
- Multipart media upload tool — only URL-add for v1.
- MCP Resources & Prompts primitives — only Tools in v1.
- Bilingual tool descriptions (EN only in v1).
- Pagination on list tools (full list returned; paginate when N becomes painful).
- Cancellation propagation (MCP supports it; FastAPI doesn't easily; deferred).
- Admin UI for managing authorized OAuth apps / live MCP connections — Phase 2.5i-3.

## 11. Migration / rollout

- No DB migration. No env var changes.
- `pip install -r requirements.txt` picks up the new `mcp` dep on next deploy.
- `/mcp` endpoint goes live on the next backend rebuild. Discovery metadata from 2.5i-1 doesn't advertise this; MCP clients connect to `/mcp` directly per user config.
- Backwards compatible — does not change any existing endpoint behavior.

## 12. Verification before merge

- [ ] 371 backend tests passing
- [ ] `tools/list` over the wire shows 25 tools with descriptions
- [ ] Smoke test: register a dynamic OAuth client, complete the consent flow, call `khanshoof_list_playlists` end-to-end (real curl, not just pytest)
- [ ] Smoke test from Claude Desktop: add the Khanshoof MCP server config, sign in via the OAuth flow, ask "list my playlists" and verify the assistant gets a clean response
- [ ] No regression in any of the 346 pre-Phase tests
