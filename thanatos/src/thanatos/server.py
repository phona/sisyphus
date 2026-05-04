"""MCP stdio server entrypoint — atomic-MCP era.

Surface (mobile / adb scope):

  preflight        — bind to redroid endpoint, must be called once per session
  observe          — uiautomator dump tree (current screen)
  screenshot       — base64-encoded PNG
  tap              — tap element by semantic name
  type             — type text into named field
  wait             — sleep N ms
  wait_for_text    — poll until text appears or timeout
  current_page     — foreground package/activity
  find             — element snapshot (resource-id / text / bounds / center)
  recall           — product knowledge fragments (anchors / flows / pitfalls / contracts)

The accept-agent drives single steps and judges per-AC verdict from the
returned evidence. There is no scenario runner, no DSL regex dispatch.
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from thanatos import __version__
from thanatos.drivers import AdbDriver
from thanatos.runner import recall as _recall

# One driver instance per stdio session — preflight binds the endpoint and the
# atomic tools reuse the connection.
_driver = AdbDriver()


def _ok(payload: dict[str, Any] | list[Any] | None = None, **extra: Any) -> str:
    body: dict[str, Any] = {"ok": True}
    if payload is not None:
        body["data"] = payload
    body.update(extra)
    return _json.dumps(body)


def _fail(hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {"ok": False, "failure_hint": hint}
    body.update(extra)
    return _json.dumps(body)


def _build_server() -> Server:
    server: Server = Server(name="thanatos", version=__version__)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="preflight",
                description=(
                    "Bind to the adb endpoint (redroid host:port, e.g. "
                    "'localhost:5555') and verify uiautomator dump works. "
                    "MUST be called once at session start before any other "
                    "atomic tool. Returns ok + a11y_node_count."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["endpoint"],
                    "properties": {
                        "endpoint": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="observe",
                description=(
                    "Refresh and return the current uiautomator view tree as a "
                    "nested dict. Use this to discover element names "
                    "(text / content-desc / resource-id) before tapping."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="screenshot",
                description=(
                    "Capture a PNG screenshot of the current screen. Returns "
                    "{ok: true, screenshot: <base64>} or {ok: false, ...}."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="tap",
                description=(
                    "Tap the first element whose text / content-desc / "
                    "resource-id matches the given name (case-insensitive "
                    "substring). Refreshes the view tree before tapping."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            ),
            Tool(
                name="type",
                description=(
                    "Focus the named field (tap), then type the text. Useful "
                    "for input fields. Element-name match same as `tap`."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["name", "text"],
                    "properties": {
                        "name": {"type": "string"},
                        "text": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="wait",
                description=(
                    "Sleep for N milliseconds. Use sparingly — prefer "
                    "`wait_for_text` for async UI changes."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["ms"],
                    "properties": {"ms": {"type": "integer", "minimum": 0}},
                },
            ),
            Tool(
                name="wait_for_text",
                description=(
                    "Poll observe() every 500ms until *text* appears anywhere "
                    "in the view tree, or timeout_ms elapses. Returns ok=true "
                    "on appear, ok=false on timeout."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["text", "timeout_ms"],
                    "properties": {
                        "text": {"type": "string"},
                        "timeout_ms": {"type": "integer", "minimum": 1},
                    },
                },
            ),
            Tool(
                name="current_page",
                description=(
                    "Return the foreground {package, activity} from "
                    "`dumpsys window`. Use to verify navigation."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="find",
                description=(
                    "Return a snapshot of the first element matching *name* "
                    "(resource-id / text / content-desc / bounds / center). "
                    "Returns ok=false when not found. Use for debugging "
                    "before deciding to tap."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            ),
            Tool(
                name="recall",
                description=(
                    "Recall product knowledge fragments matching an intent. "
                    "Searches all .md files under the skill directory "
                    "(.thanatos/ in the source repo), scores snippets by "
                    "keyword overlap, and returns the best hits. "
                    "Accept / analyze / challenger MUST call this at session "
                    "start to anchor on the project's product baseline "
                    "(anchors.md / flows.md / pitfalls.md / contracts.md / "
                    "glossary.md)."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["skill_path", "intent"],
                    "properties": {
                        "skill_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to skill.yaml (FILE). recall "
                                "reads its parent directory to find product "
                                "knowledge .md files."
                            ),
                        },
                        "intent": {
                            "type": "string",
                            "description": (
                                "Free-form natural language describing what "
                                "knowledge to recall, e.g. 'login screen "
                                "widgets' / 'payment flow pitfalls' / "
                                "'api error code conventions'."
                            ),
                        },
                        "limit": {"type": "integer", "default": 10},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional YAML frontmatter tags to filter by."
                            ),
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        text = await _dispatch(name, arguments)
        return [TextContent(type="text", text=text)]

    return server


async def _dispatch(name: str, arguments: dict[str, Any]) -> str:
    if name == "preflight":
        result = await _driver.preflight(arguments["endpoint"])
        if not result.ok:
            return _fail(result.failure_hint or "preflight failed")
        return _ok(a11y_node_count=result.a11y_node_count)

    if name == "observe":
        tree = await _driver.observe()
        return _ok({"kind": tree.kind, "tree": tree.payload})

    if name == "screenshot":
        png_b64 = await _driver.screenshot()
        if png_b64 is None:
            return _fail("screenshot capture failed")
        return _ok(screenshot=png_b64)

    if name == "tap":
        result = await _driver.tap_by_name(arguments["name"])
        if not result.ok:
            return _fail(result.failure_hint or "tap failed")
        return _ok()

    if name == "type":
        result = await _driver.type_into(arguments["name"], arguments["text"])
        if not result.ok:
            return _fail(result.failure_hint or "type failed")
        return _ok()

    if name == "wait":
        result = await _driver.wait(int(arguments["ms"]))
        if not result.ok:
            return _fail(result.failure_hint or "wait failed")
        return _ok()

    if name == "wait_for_text":
        result = await _driver.wait_for_text(
            arguments["text"], int(arguments["timeout_ms"])
        )
        if not result.ok:
            return _fail(result.failure_hint or "wait_for_text timeout")
        return _ok()

    if name == "current_page":
        page = await _driver.current_page()
        return _ok(page)

    if name == "find":
        snapshot = await _driver.find_element(arguments["name"])
        if snapshot is None:
            return _fail(f"element {arguments['name']!r} not found")
        return _ok(snapshot)

    if name == "recall":
        hits = _recall(
            arguments["skill_path"],
            arguments["intent"],
            limit=arguments.get("limit", 10),
            tags=arguments.get("tags"),
        )
        return _ok(hits)

    return _fail(f"unknown tool: {name!r}")


async def _serve() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Synchronous entrypoint used by ``python -m thanatos.server`` / Dockerfile."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
