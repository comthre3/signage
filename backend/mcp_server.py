"""Phase 2.5i-2 — MCP server exposing Khanshoof tools."""
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
            "update, and delete playlists, schedules, screens, walls, and media."
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

    app.mount("/mcp", sub_app)
