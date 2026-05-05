"""ADB driver — Android via redroid (sidecar pod).

Atomic-MCP era: this driver exposes single-step operations (tap / type / wait /
wait_for_text / current_page / find_element / observe / screenshot). The
accept-agent calls them one at a time over MCP, judging per-AC verdict from
returned evidence. There is no scenario parser, no DSL regex dispatch.

Connection contract:
- ``preflight(endpoint)`` must be called once at session start to bind to the
  redroid endpoint (e.g. ``localhost:5555``) and verify uiautomator dump works.
- All atomic methods reuse the bound endpoint stored on the instance.
"""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import xml.etree.ElementTree as ET
from typing import Any

from thanatos.drivers.base import (
    ActResult,
    Evidence,
    PreflightResult,
    SemanticTree,
)


def _node_matches_name(node: ET.Element, name: str) -> bool:
    """Check if a view node matches the given semantic name."""
    name_lower = name.lower()
    for attr in ("text", "content-desc", "resource-id"):
        val = node.get(attr, "")
        if val and name_lower in val.lower():
            return True
    return False


def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """Parse uiautomator bounds like '[0,100][200,300]' → (left, top, right, bottom)."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2


class AdbDriver:
    name: str = "adb"

    def __init__(self) -> None:
        self._endpoint: str | None = None
        self._last_tree: dict[str, Any] | None = None
        self._last_xml: str | None = None

    # ── subprocess helpers ────────────────────────────────────────────────

    def _adb_cmd(self, *args: str) -> list[str]:
        cmd = ["adb"]
        if self._endpoint is not None:
            cmd += ["-s", self._endpoint]
        return cmd + list(args)

    async def _adb(self, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
        if shutil.which("adb") is None:
            return 127, "", "adb binary not found in PATH"
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._adb_cmd(*args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return 127, "", "adb binary not found in PATH"
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", "adb command timed out"
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def _uiautomator_dump(self) -> tuple[int, str, str]:
        rc, _, stderr = await self._adb(
            "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"
        )
        if rc != 0:
            return rc, "", stderr
        rc2, stdout2, stderr2 = await self._adb("shell", "cat", "/sdcard/window_dump.xml")
        return rc2, stdout2, stderr2

    # ── view-tree helpers ─────────────────────────────────────────────────

    def _xml_to_tree(self, xml_text: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
        except ET.ParseError:
            return {"error": "xml_parse_failed", "nodes": []}
        return self._node_to_dict(root)

    def _node_to_dict(self, node: ET.Element) -> dict[str, Any]:
        children = [self._node_to_dict(c) for c in node]
        result: dict[str, Any] = {
            "tag": node.tag,
            "children": children,
        }
        for attr in (
            "index", "text", "resource-id", "class", "package", "content-desc",
            "checkable", "checked", "clickable", "enabled", "focusable",
            "focused", "scrollable", "long-clickable", "password", "selected",
            "bounds",
        ):
            val = node.get(attr)
            if val is not None:
                result[attr] = val
        return result

    def _count_nodes(self, node: dict[str, Any]) -> int:
        return 1 + sum(self._count_nodes(c) for c in node.get("children", []))

    def _find_node(self, node: dict[str, Any], name: str) -> dict[str, Any] | None:
        if _node_matches_name_dict(node, name):
            return node
        for child in node.get("children", []):
            found = self._find_node(child, name)
            if found is not None:
                return found
        return None

    def _any_node_contains(self, node: dict[str, Any], text: str) -> bool:
        text_lower = text.lower()
        for attr in ("text", "content-desc", "resource-id"):
            val = node.get(attr, "")
            if val and text_lower in val.lower():
                return True
        return any(self._any_node_contains(c, text) for c in node.get("children", []))

    async def _ensure_tree(self, *, refresh: bool = False) -> dict[str, Any] | None:
        if not refresh and self._last_tree is not None:
            return self._last_tree
        rc, xml_text, _ = await self._uiautomator_dump()
        if rc != 0 or not xml_text.strip():
            return None
        self._last_xml = xml_text
        self._last_tree = self._xml_to_tree(xml_text)
        return self._last_tree

    # ── Driver Protocol ───────────────────────────────────────────────────

    async def preflight(self, endpoint: str) -> PreflightResult:
        if shutil.which("adb") is None:
            return PreflightResult(ok=False, failure_hint="adb binary not found in PATH")

        self._endpoint = endpoint

        if ":" in endpoint:
            rc, _, _ = await self._adb("connect", endpoint, timeout=10.0)
            if rc != 0:
                return PreflightResult(
                    ok=False, failure_hint=f"adb connect {endpoint} failed"
                )

        rc, stdout, stderr = await self._adb("shell", "echo", "ping", timeout=10.0)
        if rc != 0 or "ping" not in stdout:
            return PreflightResult(
                ok=False, failure_hint=f"adb shell echo failed: {stderr or stdout}"
            )

        rc, xml_text, stderr = await self._uiautomator_dump()
        if rc != 0 or not xml_text.strip():
            return PreflightResult(
                ok=False, failure_hint=f"uiautomator dump failed: {stderr}"
            )

        tree = self._xml_to_tree(xml_text)
        node_count = self._count_nodes(tree)
        self._last_tree = tree
        self._last_xml = xml_text

        if node_count < 5:
            return PreflightResult(
                ok=False,
                failure_hint=f"uiautomator dump returned only {node_count} nodes (< 5)",
                a11y_node_count=node_count,
            )

        return PreflightResult(ok=True, a11y_node_count=node_count)

    async def observe(self) -> SemanticTree:
        # Always refresh on observe — accept-agent expects current state.
        tree = await self._ensure_tree(refresh=True)
        if tree is None:
            return SemanticTree(kind="uiautomator", payload={"error": "dump_failed"})
        return SemanticTree(kind="uiautomator", payload=tree)

    async def capture_evidence(self) -> Evidence:
        screenshot_b64 = await self._screenshot_b64()
        dom: str | None = None
        rc2, xml_text, _ = await self._uiautomator_dump()
        if rc2 == 0 and xml_text.strip():
            dom = xml_text
            self._last_xml = xml_text
            self._last_tree = self._xml_to_tree(xml_text)
        return Evidence(screenshot=screenshot_b64, dom=dom)

    # ── Atomic operations exposed via MCP tools ───────────────────────────

    async def screenshot(self) -> str | None:
        """Return base64-encoded PNG screenshot, or None if capture failed."""
        return await self._screenshot_b64()

    async def _screenshot_b64(self) -> str | None:
        # screencap -p 是 raw PNG 二进制; 走 _adb 会被 utf-8 decode("replace") 损坏
        # (非 UTF-8 字节全变 U+FFFD, 几 KB PNG 缩到几十字节). 这里直 subprocess
        # 拿 stdout bytes, 再 base64.
        if shutil.which("adb") is None:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._adb_cmd("exec-out", "screencap", "-p"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=15.0
            )
        except (FileNotFoundError, TimeoutError):
            return None
        if (proc.returncode or 0) != 0 or not stdout_b:
            return None
        try:
            return base64.b64encode(stdout_b).decode("ascii")
        except Exception:
            return None

    async def tap_by_name(self, name: str) -> ActResult:
        """Tap the first element whose text/content-desc/resource-id matches *name*."""
        tree = await self._ensure_tree(refresh=True)
        if tree is None:
            return ActResult(ok=False, failure_hint="view tree not available for tap")
        node = self._find_node(tree, name)
        if node is None:
            return ActResult(
                ok=False, failure_hint=f"element '{name}' not found in view tree"
            )
        bounds_str = node.get("bounds", "")
        bounds = _parse_bounds(bounds_str)
        if bounds is None:
            return ActResult(
                ok=False, failure_hint=f"element '{name}' has no parseable bounds"
            )
        x, y = _center(bounds)
        rc, _, stderr = await self._adb("shell", "input", "tap", str(x), str(y))
        if rc != 0:
            return ActResult(ok=False, failure_hint=f"tap ({x},{y}) failed: {stderr}")
        return ActResult(ok=True)

    async def type_into(self, name: str, text: str) -> ActResult:
        """Focus the named field (tap), then ``input text`` the value."""
        tap_result = await self.tap_by_name(name)
        if not tap_result.ok:
            return ActResult(
                ok=False,
                failure_hint=f"focus field '{name}' failed: {tap_result.failure_hint}",
            )
        rc, _, stderr = await self._adb("shell", "input", "text", text)
        if rc != 0:
            return ActResult(ok=False, failure_hint=f"type '{text}' failed: {stderr}")
        return ActResult(ok=True)

    async def wait(self, ms: int) -> ActResult:
        """Sleep *ms* milliseconds. Use sparingly — prefer ``wait_for_text``."""
        if ms < 0:
            return ActResult(ok=False, failure_hint=f"negative wait: {ms}")
        await asyncio.sleep(ms / 1000.0)
        return ActResult(ok=True)

    async def wait_for_text(self, text: str, timeout_ms: int) -> ActResult:
        """Poll ``observe()`` until *text* appears anywhere in the view tree, or timeout."""
        if timeout_ms <= 0:
            return ActResult(ok=False, failure_hint=f"non-positive timeout: {timeout_ms}")
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
        while True:
            tree = await self._ensure_tree(refresh=True)
            if tree is not None and self._any_node_contains(tree, text):
                return ActResult(ok=True)
            if asyncio.get_event_loop().time() >= deadline:
                return ActResult(
                    ok=False,
                    failure_hint=f"text {text!r} did not appear within {timeout_ms}ms",
                )
            await asyncio.sleep(0.5)

    async def current_page(self) -> dict[str, str | None]:
        """Return current foreground ``{package, activity}`` from dumpsys window."""
        rc, stdout, stderr = await self._adb(
            "shell", "dumpsys", "window", "windows", timeout=10.0
        )
        if rc != 0:
            return {"package": None, "activity": None, "error": stderr or "dumpsys failed"}
        # Look for "mCurrentFocus=Window{... <package>/<activity>}" or
        # "mFocusedApp=...ActivityRecord{... <package>/<activity>}"
        m = re.search(
            r"(?:mCurrentFocus|mFocusedApp)=[^\s]+\s+(?:[^\s]+\s+)?(?P<pkg>[\w\.]+)/(?P<act>[\w\.\$]+)",
            stdout,
        )
        if m is None:
            return {"package": None, "activity": None}
        return {"package": m.group("pkg"), "activity": m.group("act")}

    async def find_element(self, name: str) -> dict[str, Any] | None:
        """Return a snapshot dict of the first node matching *name* (or None).

        Snapshot includes resource-id / text / content-desc / bounds / clickable
        — enough for the accept-agent to disambiguate without re-observing.
        """
        tree = await self._ensure_tree(refresh=True)
        if tree is None:
            return None
        node = self._find_node(tree, name)
        if node is None:
            return None
        out: dict[str, Any] = {}
        for attr in (
            "text", "resource-id", "content-desc", "class", "package", "bounds",
            "clickable", "enabled", "focusable", "focused", "selected",
        ):
            if attr in node:
                out[attr] = node[attr]
        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is not None:
            out["center"] = list(_center(bounds))
        return out


def _node_matches_name_dict(node: dict[str, Any], name: str) -> bool:
    """Dict-tree variant of :func:`_node_matches_name` (XML element variant unused)."""
    name_lower = name.lower()
    for attr in ("text", "content-desc", "resource-id"):
        val = node.get(attr, "")
        if val and name_lower in val.lower():
            return True
    return False
