"""Playwright driver — web (chromium subprocess) — M1 implementation."""

from __future__ import annotations

import base64
import re
from typing import Any

from thanatos.drivers.base import (
    ActResult,
    AssertResult,
    Evidence,
    PreflightResult,
    SemanticTree,
)

# UI action patterns
# When click "Submit button"
# When type "email@example.com" into "Email field"
_ACT_CLICK_RE = re.compile(
    r'^(?:When\s+)?click\s+"(?P<name>[^"]+)"',
    re.IGNORECASE,
)
_ACT_TYPE_RE = re.compile(
    r'^(?:When\s+)?type\s+"(?P<text>[^"]+)"\s+into\s+"(?P<name>[^"]+)"',
    re.IGNORECASE,
)

# Assertion patterns
# Then page title contains "Dashboard"
# Then element "Success message" is visible
_ASSERT_TITLE_RE = re.compile(
    r'^(?:Then\s+)?page\s+title\s+contains\s+"(?P<text>[^"]+)"',
    re.IGNORECASE,
)
_ASSERT_ELEMENT_VISIBLE_RE = re.compile(
    r'^(?:Then\s+)?element\s+"(?P<name>[^"]+)"\s+is\s+visible',
    re.IGNORECASE,
)
_ASSERT_ELEMENT_TEXT_RE = re.compile(
    r'^(?:Then\s+)?element\s+"(?P<name>[^"]+)"\s+contains\s+"(?P<text>[^"]+)"',
    re.IGNORECASE,
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

    async def act(self, step: str) -> ActResult:
        step = step.strip()

        m = _ACT_CLICK_RE.match(step)
        if m:
            return await self._act_click(m.group("name"))

        m = _ACT_TYPE_RE.match(step)
        if m:
            return await self._act_type(m.group("name"), m.group("text"))

        return ActResult(ok=False, failure_hint=f"unrecognised UI step: {step!r}")

    async def _act_click(self, name: str) -> ActResult:
        if self._page is None:
            return ActResult(ok=False, failure_hint="browser not initialised")
        try:
            elem = self._page.get_by_role("button", name=name)
            count = await elem.count()
            if count == 0:
                elem = self._page.get_by_text(name)
                count = await elem.count()
            if count == 0:
                elem = self._page.locator(f"[aria-label='{name}']")
                count = await elem.count()
            if count == 0:
                return ActResult(ok=False, failure_hint=f"element '{name}' not found")
            await elem.first.click()
            return ActResult(ok=True)
        except Exception as exc:
            return ActResult(ok=False, failure_hint=f"click '{name}' failed: {exc}")

    async def _act_type(self, name: str, text: str) -> ActResult:
        if self._page is None:
            return ActResult(ok=False, failure_hint="browser not initialised")
        try:
            elem = self._page.get_by_role("textbox", name=name)
            count = await elem.count()
            if count == 0:
                elem = self._page.get_by_label(name)
                count = await elem.count()
            if count == 0:
                elem = self._page.locator(f"[placeholder='{name}']")
                count = await elem.count()
            if count == 0:
                return ActResult(ok=False, failure_hint=f"field '{name}' not found")
            await elem.first.fill(text)
            return ActResult(ok=True)
        except Exception as exc:
            return ActResult(ok=False, failure_hint=f"type '{text}' into '{name}' failed: {exc}")

    async def assert_(self, step: str) -> AssertResult:
        step = step.strip()

        m = _ASSERT_TITLE_RE.match(step)
        if m:
            return await self._assert_title_contains(m.group("text"))

        m = _ASSERT_ELEMENT_VISIBLE_RE.match(step)
        if m:
            return await self._assert_element_visible(m.group("name"))

        m = _ASSERT_ELEMENT_TEXT_RE.match(step)
        if m:
            return await self._assert_element_contains(m.group("name"), m.group("text"))

        return AssertResult(ok=False, failure_hint=f"unrecognised assert step: {step!r}")

    async def _assert_title_contains(self, text: str) -> AssertResult:
        if self._page is None:
            return AssertResult(ok=False, failure_hint="browser not initialised")
        try:
            title = await self._page.title()
        except Exception as exc:
            return AssertResult(ok=False, failure_hint=f"get title failed: {exc}")
        if text in title:
            return AssertResult(ok=True)
        return AssertResult(
            ok=False,
            failure_hint=f"page title '{title}' does not contain '{text}'",
        )

    async def _assert_element_visible(self, name: str) -> AssertResult:
        if self._page is None:
            return AssertResult(ok=False, failure_hint="browser not initialised")
        try:
            elem = self._page.get_by_text(name)
            count = await elem.count()
            if count == 0:
                elem = self._page.get_by_role("generic", name=name)
                count = await elem.count()
            if count == 0:
                return AssertResult(
                    ok=False,
                    failure_hint=f"element '{name}' not found",
                )
            visible = await elem.first.is_visible()
            if visible:
                return AssertResult(ok=True)
            return AssertResult(
                ok=False,
                failure_hint=f"element '{name}' is not visible",
            )
        except Exception as exc:
            return AssertResult(
                ok=False,
                failure_hint=f"assert visible '{name}' failed: {exc}",
            )

    async def _assert_element_contains(self, name: str, text: str) -> AssertResult:
        if self._page is None:
            return AssertResult(ok=False, failure_hint="browser not initialised")
        try:
            elem = self._page.get_by_text(name)
            count = await elem.count()
            if count == 0:
                elem = self._page.locator(f"text={name}")
                count = await elem.count()
            if count == 0:
                return AssertResult(
                    ok=False,
                    failure_hint=f"element '{name}' not found",
                )
            inner = await elem.first.inner_text()
            if text in inner:
                return AssertResult(ok=True)
            return AssertResult(
                ok=False,
                failure_hint=f"element '{name}' text '{inner}' does not contain '{text}'",
            )
        except Exception as exc:
            return AssertResult(
                ok=False,
                failure_hint=f"assert contains '{name}' failed: {exc}",
            )

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
