"""FastMCP wiring for the sisyphus-dispatch server (stdio transport, M0).

Tool implementations live in `queries.py` so unit tests can exercise
them without booting the MCP SDK. This module just glues the queries
to FastMCP `@tool()` registration and owns the stdio runtime.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..config import settings
from ..store.db import close_pool, get_pool, init_pool
from . import queries

mcp = FastMCP("sisyphus-dispatch")


@mcp.tool()
async def get_req_state(req_id: str) -> dict | None:
    """Return current state of a REQ, or null if it doesn't exist.

    The returned object exposes `req_id`, `project_id`, `state`,
    `created_at`, `updated_at`, the last `history` entry as
    `last_event`, and `context_keys` (top-level keys of the JSONB
    context — body intentionally redacted).
    """
    return await queries.fetch_req_state(get_pool(), req_id)


@mcp.tool()
async def list_reqs(
    state: str | None = None,
    limit: int = queries.LIST_LIMIT_DEFAULT,
) -> list[dict]:
    """List the most-recently-updated REQs, optionally filtered by state.

    `limit` is clamped to [1, 200] server-side. `state`, when supplied,
    must be a valid `ReqState` value (e.g. "analyzing", "pr-ci-running",
    "done"); an unknown value raises an error.
    """
    return await queries.fetch_reqs(get_pool(), state=state, limit=limit)


async def run_stdio() -> None:
    """Init the asyncpg pool, run the MCP stdio loop, then tear down."""
    await init_pool(settings.pg_dsn)
    try:
        await mcp.run_stdio_async()
    finally:
        await close_pool()
