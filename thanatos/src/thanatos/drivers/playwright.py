"""Playwright driver — web (chromium subprocess).

Atomic-MCP era: this driver still implements the three-method Driver Protocol
(``preflight`` / ``observe`` / ``capture_evidence``). Per-step actions are not
exposed via MCP yet — the dogfood path is mobile (adb), and the playwright
atomic surface lands when the first web acceptance lab needs it.
"""

from __future__ import annotations

import base64
from typing import Any

from thanatos.drivers.base import (
    Evidence,
    PreflightResult,
    SemanticTree,
)


class PlaywrightDriver:
    name: str = "playwright"

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._endpoint: str | None = None

    async def preflight(self, endpoint: str) -> PreflightResult:
        self._endpoint = endpoint
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            return PreflightResult(
                ok=False,
                failure_hint=f"playwright not installed: {exc}",
            )

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._page = await self._browser.new_page()
            await self._page.goto(endpoint, wait_until="networkidle")
        except Exception as exc:
            return PreflightResult(
                ok=False,
                failure_hint=f"browser launch or navigate failed: {exc}",
            )

        try:
            snapshot = await self._page.accessibility.snapshot()
            node_count = self._count_a11y_nodes(snapshot)
        except Exception:
            node_count = None

        return PreflightResult(ok=True, a11y_node_count=node_count)

    async def observe(self) -> SemanticTree:
        if self._page is None:
            return SemanticTree(kind="a11y", payload={})
        try:
            snapshot = await self._page.accessibility.snapshot()
        except Exception:
            snapshot = {}
        return SemanticTree(kind="a11y", payload=snapshot or {})

    async def capture_evidence(self) -> Evidence:
        screenshot_b64: str | None = None
        dom: str | None = None
        if self._page is not None:
            try:
                png_bytes = await self._page.screenshot()
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception:
                pass
            try:
                dom = await self._page.content()
            except Exception:
                pass
        return Evidence(screenshot=screenshot_b64, dom=dom)

    def _count_a11y_nodes(self, node: dict[str, Any] | None) -> int:
        if node is None:
            return 0
        count = 1
        for child in node.get("children") or []:
            count += self._count_a11y_nodes(child)
        return count
