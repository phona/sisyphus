"""MCP stdio server entrypoint.

Registers three tools: ``run_scenario`` / ``run_all`` / ``recall``. All three
dispatch to :mod:`thanatos.runner` (M0 stub — see ``run_scenario`` body).
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from thanatos import __version__
from thanatos.runner import recall as _recall
from thanatos.runner import run_all as _run_all
from thanatos.runner import run_scenario as _run_scenario


def _build_server() -> Server:
    server: Server = Server(name="thanatos", version=__version__)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="run_scenario",
                description=(
                    "Run a single scenario from a spec.md against an endpoint. "
                    "M0 returns pass=false with a scaffold-only hint."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "spec_path", "scenario_id", "endpoint"],
                    "properties": {
                        "skill_path": {"type": "string"},
                        "spec_path": {"type": "string"},
                        "scenario_id": {"type": "string"},
                        "endpoint": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="run_all",
                description=(
                    "Run every scenario in a spec.md against an endpoint. "
                    "M0 returns one stub result per scenario."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "spec_path", "endpoint"],
                    "properties": {
                        "skill_path": {"type": "string"},
                        "spec_path": {"type": "string"},
                        "endpoint": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="recall",
                description=(
                    "Recall product knowledge fragments matching an intent. "
                    "M0 always returns an empty list."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "intent"],
                    "properties": {
                        "skill_path": {"type": "string"},
                        "intent": {"type": "string"},
                    },
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json as _json

        if name == "run_scenario":
            res = _run_scenario(
                arguments["skill_path"],
                arguments["spec_path"],
                arguments["scenario_id"],
                arguments["endpoint"],
            )
            return [TextContent(type="text", text=_json.dumps(res.to_dict()))]
        if name == "run_all":
            results = _run_all(
                arguments["skill_path"],
                arguments["spec_path"],
                arguments["endpoint"],
            )
            return [
                TextContent(
                    type="text",
                    text=_json.dumps([r.to_dict() for r in results]),
                )
            ]
        if name == "recall":
            hits = _recall(arguments["skill_path"], arguments["intent"])
            return [TextContent(type="text", text=_json.dumps(hits))]
        raise ValueError(f"unknown tool: {name!r}")

    return server


async def _serve() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Synchronous entrypoint used by ``python -m thanatos.server`` / Dockerfile."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
