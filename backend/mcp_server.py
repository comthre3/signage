"""Phase 2.5i-2 — MCP server exposing Khanshoof tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def attach_mcp(app) -> None:
    """Mount an MCP server at /mcp on the FastAPI app.

    Uses stateless HTTP + JSON response mode so each tool call is a plain
    POST/JSON exchange — no SSE framing, no per-session state.

    The StreamableHTTP session manager requires its own lifespan task group.
    Starlette does not propagate lifespan events to mounted sub-apps, so we
    wire it into the parent app via on_event startup/shutdown — the same
    pattern already used elsewhere in main.py.
    """
    mcp = FastMCP(
        "khanshoof",
        instructions=(
            "Khanshoof signage MCP server. Use these tools to list, create, "
            "update, and delete playlists, schedules, screens, walls, and media."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",  # route at / inside sub-app → /mcp/ from parent
    )

    # Build the sub-app; this lazily initialises mcp._session_manager.
    sub_app = mcp.streamable_http_app()

    # Wire the session manager's task-group lifespan into the parent app.
    # StreamableHTTPSessionManager.run() can only be called once per instance,
    # so we capture the context manager and hold it for the full app lifetime.
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

    # Tools are registered in subsequent tasks via _register_tools(mcp).
    # For Task 1, the server is empty.

    app.mount("/mcp", sub_app)
