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
                    "M1 executes driver act/assert steps and returns pass/fail with evidence."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "spec_path", "scenario_id", "endpoint"],
                    "properties": {
                        "skill_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to skill.yaml (FILE, not the .thanatos/ "
                                "directory). Example: "
                                "/workspace/source/<repo>/.thanatos/skill.yaml"
                            ),
                        },
                        "spec_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to a spec.md file containing the "
                                "Scenario block. May live anywhere; common paths: "
                                "<repo>/.thanatos/spec-fixtures/*.spec.md or "
                                "<repo>/openspec/changes/<REQ>/specs/.../spec.md"
                            ),
                        },
                        "scenario_id": {
                            "type": "string",
                            "description": (
                                "Identifier in the `#### Scenario: <id> — ...` heading "
                                "(e.g. ttpos-shop-smoke-001 / REQ-1004-S1). Must match exactly."
                            ),
                        },
                        "endpoint": {
                            "type": "string",
                            "description": (
                                "Driver-specific endpoint. adb: host:port (e.g. "
                                "localhost:5555). playwright: URL. http: base URL."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="run_all",
                description=(
                    "Run every scenario in a spec.md against an endpoint. "
                    "M1 executes all scenarios and returns results with evidence."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "spec_path", "endpoint"],
                    "properties": {
                        "skill_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to skill.yaml (FILE, not directory). "
                                "Example: /workspace/source/<repo>/.thanatos/skill.yaml"
                            ),
                        },
                        "spec_path": {
                            "type": "string",
                            "description": "Absolute path to spec.md file.",
                        },
                        "endpoint": {
                            "type": "string",
                            "description": (
                                "Driver-specific endpoint (adb host:port / playwright URL / http base URL)."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="recall",
                description=(
                    "Recall product knowledge fragments matching an intent. "
                    "Searches all .md files under the skill directory, scores "
                    "snippets by keyword overlap, and returns the best hits."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "intent"],
                    "properties": {
                        "skill_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to skill.yaml (FILE). recall reads its "
                                "parent directory to find anchors.md / flows.md / pitfalls.md / etc."
                            ),
                        },
                        "intent": {
                            "type": "string",
                            "description": (
                                "Free-form natural language describing what knowledge to recall, "
                                "e.g. 'login screen widgets' or 'payment flow pitfalls'."
                            ),
                        },
                        "limit": {"type": "integer", "default": 10},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional YAML frontmatter tags to filter by. "
                                "Files without matching tags are excluded."
                            ),
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json as _json

        if name == "run_scenario":
            res = await _run_scenario(
                arguments["skill_path"],
                arguments["spec_path"],
                arguments["scenario_id"],
                arguments["endpoint"],
            )
            return [TextContent(type="text", text=_json.dumps(res.to_dict()))]
        if name == "run_all":
            results = await _run_all(
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
            hits = _recall(
                arguments["skill_path"],
                arguments["intent"],
                limit=arguments.get("limit", 10),
                tags=arguments.get("tags"),
            )
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
