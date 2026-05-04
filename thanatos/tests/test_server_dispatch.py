"""thanatos.server._dispatch — atomic-MCP tool dispatch contract.

Tests the JSON envelope contract between MCP tools and the AdbDriver, by
patching the module-level ``_driver`` instance with a stub.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from thanatos import server as server_mod
from thanatos.drivers.base import (
    ActResult,
    Evidence,  # noqa: F401  (kept for future tests)
    PreflightResult,
    SemanticTree,
)


class StubDriver:
    """Records calls and returns scripted responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def preflight(self, endpoint: str) -> PreflightResult:
        self.calls.append(("preflight", {"endpoint": endpoint}))
        return PreflightResult(ok=True, a11y_node_count=42)

    async def observe(self) -> SemanticTree:
        self.calls.append(("observe", {}))
        return SemanticTree(kind="uiautomator", payload={"tag": "hierarchy"})

    async def screenshot(self) -> str | None:
        self.calls.append(("screenshot", {}))
        return "PNG_BASE64_HERE"

    async def tap_by_name(self, name: str) -> ActResult:
        self.calls.append(("tap_by_name", {"name": name}))
        if name == "missing":
            return ActResult(ok=False, failure_hint=f"element {name!r} not found")
        return ActResult(ok=True)

    async def type_into(self, name: str, text: str) -> ActResult:
        self.calls.append(("type_into", {"name": name, "text": text}))
        return ActResult(ok=True)

    async def wait(self, ms: int) -> ActResult:
        self.calls.append(("wait", {"ms": ms}))
        return ActResult(ok=True)

    async def wait_for_text(self, text: str, timeout_ms: int) -> ActResult:
        self.calls.append(("wait_for_text", {"text": text, "timeout_ms": timeout_ms}))
        if text == "never":
            return ActResult(ok=False, failure_hint="text 'never' did not appear")
        return ActResult(ok=True)

    async def current_page(self) -> dict[str, Any]:
        self.calls.append(("current_page", {}))
        return {"package": "com.ttpos.shop", "activity": ".LoginActivity"}

    async def find_element(self, name: str) -> dict[str, Any] | None:
        self.calls.append(("find_element", {"name": name}))
        if name == "missing":
            return None
        return {"text": name, "bounds": "[0,0][100,100]", "center": [50, 50]}


@pytest.fixture
def stub_driver(monkeypatch: pytest.MonkeyPatch) -> StubDriver:
    stub = StubDriver()
    monkeypatch.setattr(server_mod, "_driver", stub)
    return stub


@pytest.mark.asyncio
async def test_preflight_returns_node_count(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("preflight", {"endpoint": "localhost:5555"})
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["a11y_node_count"] == 42
    assert stub_driver.calls[0] == ("preflight", {"endpoint": "localhost:5555"})


@pytest.mark.asyncio
async def test_observe_wraps_semantic_tree(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("observe", {})
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["data"]["kind"] == "uiautomator"
    assert body["data"]["tree"]["tag"] == "hierarchy"


@pytest.mark.asyncio
async def test_screenshot_returns_base64(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("screenshot", {})
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["screenshot"] == "PNG_BASE64_HERE"


@pytest.mark.asyncio
async def test_tap_failure_propagates_failure_hint(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("tap", {"name": "missing"})
    body = json.loads(raw)
    assert body["ok"] is False
    assert "not found" in body["failure_hint"]


@pytest.mark.asyncio
async def test_type_dispatches_with_text(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("type", {"name": "field", "text": "hello"})
    body = json.loads(raw)
    assert body["ok"] is True
    assert stub_driver.calls[0] == ("type_into", {"name": "field", "text": "hello"})


@pytest.mark.asyncio
async def test_wait_for_text_timeout_returns_failure(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch(
        "wait_for_text", {"text": "never", "timeout_ms": 100}
    )
    body = json.loads(raw)
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_current_page_returns_package_activity(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("current_page", {})
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["data"]["package"] == "com.ttpos.shop"


@pytest.mark.asyncio
async def test_find_returns_failure_when_element_missing(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("find", {"name": "missing"})
    body = json.loads(raw)
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_unknown_tool_returns_failure(stub_driver: StubDriver) -> None:
    raw = await server_mod._dispatch("nonexistent", {})
    body = json.loads(raw)
    assert body["ok"] is False
    assert "unknown tool" in body["failure_hint"]


@pytest.mark.asyncio
async def test_recall_dispatches_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_recall(skill_path: str, intent: str, *, limit: int = 10, tags: Any = None) -> list[dict[str, Any]]:
        captured["args"] = (skill_path, intent, limit, tags)
        return [{"kind": "anchors.md", "snippet": "..."}]

    monkeypatch.setattr(server_mod, "_recall", fake_recall)
    raw = await server_mod._dispatch(
        "recall",
        {"skill_path": "/tmp/foo/skill.yaml", "intent": "login widgets", "limit": 3},
    )
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["data"][0]["kind"] == "anchors.md"
    assert captured["args"][0] == "/tmp/foo/skill.yaml"
    assert captured["args"][2] == 3
