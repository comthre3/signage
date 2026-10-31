"""Phase 2.5i-2 — MCP server exposing Khanshoof tools."""
import logging
from typing import Optional, Any

import httpx
from httpx import ASGITransport
from mcp.server.fastmcp import FastMCP, Context
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

logger = logging.getLogger("signage.mcp")


# JSON-RPC + MCP standard error codes
_INVALID_REQUEST  = -32600
_INVALID_PARAMS   = -32602
_INTERNAL_ERROR   = -32603
_APP_ERROR        = -32000


def _bearer_from(ctx: Context) -> Optional[str]:
    """Extract the Authorization Bearer token from the request context."""
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


# ── HTTP-level authentication ─────────────────────────────────────────
#
# The MCP authorization spec has the server answer a request without a valid
# token with HTTP 401 and a WWW-Authenticate header naming the protected-
# resource metadata (RFC 9728); that 401 is what makes a client start OAuth.
# FastMCP alone answers 200 to initialize/tools/list and returns an in-band
# tool error on tools/call, so clients never learned they had to sign in.


def _resource_metadata_url() -> str:
    from oauth import _api_base_url
    return f"{_api_base_url()}/.well-known/oauth-protected-resource"


def _token_is_valid(token: str) -> bool:
    """Same acceptance as every REST endpoint: API key, OAuth access token
    (whose granting user still exists), or dashboard session."""
    from fastapi import HTTPException
    from main import get_api_authed
    try:
        get_api_authed(f"Bearer {token}")
    except HTTPException:
        return False
    return True


def _unauthorized(error: Optional[str]) -> JSONResponse:
    challenge = f'Bearer resource_metadata="{_resource_metadata_url()}"'
    if error:
        challenge += f', error="{error}"'
    return JSONResponse(
        status_code=401,
        content={
            "error": error or "unauthorized",
            "error_description": (
                "The access token is invalid, expired or revoked."
                if error else "Authentication required: sign in with OAuth."
            ),
        },
        headers={"WWW-Authenticate": challenge},
    )


class _RequireBearer:
    """ASGI wrapper around the MCP app: no valid Bearer token, no MCP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            response = _unauthorized(None)
        elif not await run_in_threadpool(_token_is_valid, token):
            response = _unauthorized("invalid_token")
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


def _map_response(r: httpx.Response, path: str) -> Any:
    """Parse the FastAPI response; raise McpError on non-2xx."""
    if r.status_code < 300:
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

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
    if r.status_code == 401:
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
    """Mount an MCP server at /mcp on the FastAPI app.

    See Task 1 commit for the lifespan wiring rationale (Starlette doesn't
    propagate lifespan to mounted sub-apps, and StreamableHTTPSessionManager
    requires its task group to be running before any request can land).
    """
    mcp = FastMCP(
        "khanshoof",
        instructions=(
            "Khanshoof signage MCP server. Use these tools to list, create, "
            "update, and delete playlists, schedules, screens, walls, and media. "
            "For recipes (images→playlist, website→menu, dayparting) and v1 "
            "limitations, fetch https://app.khanshoof.com/AGENTS.md before "
            "tackling anything non-trivial."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    async def khanshoof_get_organization(ctx: Context) -> dict:
        """Get the current organization's profile, including plan + screen limits."""
        return await _dispatch(app, ctx, "GET", "/organization")

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
        # There is no GET /screens/{id}; the list carries the same fields
        # (site_name, playlist_name included) under the same access rules.
        screens = await _dispatch(app, ctx, "GET", "/screens")
        for screen in screens or []:
            if screen.get("id") == screen_id:
                return screen
        raise McpError(ErrorData(
            code=_APP_ERROR,
            message=f"Screen not found: {screen_id}",
            data={"http_status": 404, "path": f"/screens/{screen_id}"},
        ))

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
    async def khanshoof_list_schedules(ctx: Context) -> dict:
        """List all dayparting schedules in the current organization. Returns {items: [...]}."""
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
    async def khanshoof_add_playlist_item(
        ctx: Context,
        playlist_id: int,
        media_id: int,
        duration_seconds: Optional[int] = None,
    ) -> dict:
        """Add a media item to a playlist. duration_seconds defaults to the
        media type's standard duration if omitted. Requires api:rw scope."""
        body: dict = {"media_id": media_id}
        if duration_seconds is not None:
            body["duration_seconds"] = duration_seconds
        return await _dispatch(app, ctx, "POST", f"/playlists/{playlist_id}/items",
                               json_body=body)

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
        playlist_id (int), start_time (HH:MM), end_time (HH:MM),
        days_of_week (int bitmask 1-127), and position (int, default 0).
        Pass an empty list to clear all rules. Requires api:rw scope."""
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
    async def khanshoof_add_canvas_playlist_item(
        ctx: Context,
        wall_id: int,
        media_id: int,
        position: int,
        duration_override_seconds: Optional[int] = None,
        fit_mode: Optional[str] = None,
    ) -> dict:
        """Add a media item to a spanned (canvas-mode) wall's playlist.
        position is required (0-based index). fit_mode is one of
        'fit', 'fill', or 'stretch' (default 'fit'). Requires api:rw scope."""
        body: dict = {"media_id": media_id, "position": position}
        if duration_override_seconds is not None:
            body["duration_override_seconds"] = duration_override_seconds
        if fit_mode is not None:
            body["fit_mode"] = fit_mode
        return await _dispatch(app, ctx, "POST",
                               f"/walls/{wall_id}/canvas-playlist/items",
                               json_body=body)

    @mcp.tool()
    async def khanshoof_add_media_url(
        ctx: Context,
        url: str,
        name: str,
    ) -> dict:
        """Add a media item by URL (image/video URL). Both url and name are
        required. Requires api:rw scope."""
        return await _dispatch(app, ctx, "POST", "/media/url",
                               json_body={"url": url, "name": name})

    sub_app = mcp.streamable_http_app()

    _cm: object = None

    @app.on_event("startup")
    async def _mcp_startup() -> None:
        nonlocal _cm
        _cm = mcp._session_manager.run()
        await _cm.__aenter__()

    @app.on_event("shutdown")
    async def _mcp_shutdown() -> None:
        nonlocal _cm
        if _cm is not None:
            await _cm.__aexit__(None, None, None)

    app.mount("/mcp", _RequireBearer(sub_app))
    app.add_middleware(_ServeBareMcpPath)


class _ServeBareMcpPath:
    """Serve "/mcp" as "/mcp/" instead of redirecting to it.

    The documented endpoint is https://api.khanshoof.com/mcp. Starlette's mount
    answers it with a 307 to /mcp/ built from the scheme the backend sees --
    http, behind the tunnel -- so MCP clients were bounced to plain http, and
    the http->https hop after that can turn their POST into a GET. Rewriting
    the path in place needs no redirect and no trust in forwarded headers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)
