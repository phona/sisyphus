"""`python -m orchestrator.dispatch_mcp` → run the stdio MCP server."""
from __future__ import annotations

import asyncio

from .server import run_stdio


def main() -> None:
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
