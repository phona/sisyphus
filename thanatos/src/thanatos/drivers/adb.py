"""ADB driver — Android via redroid (sidecar pod) — M2 implementation.

Uses ``adb`` CLI (installed in the thanatos image) to drive a redroid container
over the endpoint passed to ``preflight`` (e.g. ``localhost:5555``).

Semantic layer: ``uiautomator dump`` XML → parsed view tree.
Evidence: ``adb exec-out screencap -p`` (base64 PNG) + XML dump.
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
    AssertResult,
    Evidence,
    PreflightResult,
    SemanticTree,
)

# Act patterns
# When tap "Submit button"
# When type "hello" into "Search field"
_ACT_TAP_RE = re.compile(
    r'^(?:When\s+)?(?:tap|click|press)\s+"(?P<name>[^"]+)"',
    re.IGNORECASE,
)
_ACT_TYPE_RE = re.compile(
    r'^(?:When\s+)?type\s+"(?P<text>[^"]+)"\s+into\s+"(?P<name>[^"]+)"',
    re.IGNORECASE,
)

# Assert patterns
# Then element "Success message" is visible
# Then element "Title" contains "Welcome"
# Then screen contains "Loading"
_ASSERT_ELEMENT_VISIBLE_RE = re.compile(
    r'^(?:Then\s+)?element\s+"(?P<name>[^"]+)"\s+is\s+visible',
    re.IGNORECASE,
)
_ASSERT_ELEMENT_CONTAINS_RE = re.compile(
    r'^(?:Then\s+)?element\s+"(?P<name>[^"]+)"\s+contains\s+"(?P<text>[^"]+)"',
    re.IGNORECASE,
)
_ASSERT_SCREEN_CONTAINS_RE = re.compile(
    r'^(?:Then\s+)?screen\s+contains\s+"(?P<text>[^"]+)"',
    re.IGNORECASE,
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
        """Build an adb command list targeting the connected endpoint."""
        cmd = ["adb"]
        if self._endpoint is not None:
            cmd += ["-s", self._endpoint]
        return cmd + list(args)

    async def _adb(self, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
        """Run an adb sub-command and return (returncode, stdout, stderr)."""
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
        return proc.returncode or 0, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")

    async def _uiautomator_dump(self) -> tuple[int, str, str]:
        """Run uiautomator dump and return (rc, xml_or_error, stderr)."""
        rc, _, stderr = await self._adb("shell", "uiautomator", "dump", "/sdcard/window_dump.xml")
        if rc != 0:
            return rc, "", stderr
        rc2, stdout2, stderr2 = await self._adb("shell", "cat", "/sdcard/window_dump.xml")
        return rc2, stdout2, stderr2

    # ── view-tree helpers ─────────────────────────────────────────────────

    def _xml_to_tree(self, xml_text: str) -> dict[str, Any]:
        """Parse uiautomator XML into a nested dict tree."""
        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
        except ET.ParseError:
            return {"error": "xml_parse_failed", "nodes": []}
        return self._node_to_dict(root)

    def _node_to_dict(self, node: ET.Element) -> dict[str, Any]:
        """Convert an XML element to a dict preserving key attributes."""
        children = [self._node_to_dict(c) for c in node]
        result: dict[str, Any] = {
            "tag": node.tag,
            "children": children,
        }
        for attr in ("index", "text", "resource-id", "class", "package",
                     "content-desc", "checkable", "checked", "clickable",
                     "enabled", "focusable", "focused", "scrollable",
                     "long-clickable", "password", "selected", "bounds"):
            val = node.get(attr)
            if val is not None:
                result[attr] = val
        return result

    def _count_nodes(self, node: dict[str, Any]) -> int:
        return 1 + sum(self._count_nodes(c) for c in node.get("children", []))

    def _find_node(self, node: dict[str, Any], name: str) -> dict[str, Any] | None:
        """Depth-first search for a node whose text/content-desc/resource-id matches name."""
        if _node_matches_name(node, name):
            return node
        for child in node.get("children", []):
            found = self._find_node(child, name)
            if found is not None:
                return found
        return None

    def _any_node_contains(self, node: dict[str, Any], text: str) -> bool:
        """Return True if any node in the tree contains the text."""
        text_lower = text.lower()
        for attr in ("text", "content-desc", "resource-id"):
            val = node.get(attr, "")
            if val and text_lower in val.lower():
                return True
        return any(self._any_node_contains(c, text) for c in node.get("children", []))

    # ── Driver protocol ───────────────────────────────────────────────────

    async def preflight(self, endpoint: str) -> PreflightResult:
        if shutil.which("adb") is None:
            return PreflightResult(
                ok=False,
                failure_hint="adb binary not found in PATH",
            )

        self._endpoint = endpoint

        # Try to connect if endpoint looks like host:port
        if ":" in endpoint:
            rc, _, _ = await self._adb("connect", endpoint, timeout=10.0)
            if rc != 0:
                return PreflightResult(
                    ok=False,
                    failure_hint=f"adb connect {endpoint} failed",
                )

        # Check device is responsive
        rc, stdout, stderr = await self._adb("shell", "echo", "ping", timeout=10.0)
        if rc != 0 or "ping" not in stdout:
            return PreflightResult(
                ok=False,
                failure_hint=f"adb shell echo failed: {stderr or stdout}",
            )

        # Try uiautomator dump
        rc, xml_text, stderr = await self._uiautomator_dump()
        if rc != 0 or not xml_text.strip():
            return PreflightResult(
                ok=False,
                failure_hint=f"uiautomator dump failed: {stderr}",
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
        if self._last_tree is not None:
            return SemanticTree(kind="uiautomator", payload=self._last_tree)
        rc, xml_text, _ = await self._uiautomator_dump()
        if rc != 0 or not xml_text.strip():
            return SemanticTree(kind="uiautomator", payload={"error": "dump_failed"})
        tree = self._xml_to_tree(xml_text)
        self._last_tree = tree
        self._last_xml = xml_text
        return SemanticTree(kind="uiautomator", payload=tree)

    async def act(self, step: str) -> ActResult:
        step = step.strip()

        m = _ACT_TAP_RE.match(step)
        if m:
            return await self._act_tap(m.group("name"))

        m = _ACT_TYPE_RE.match(step)
        if m:
            return await self._act_type(m.group("name"), m.group("text"))

        return ActResult(ok=False, failure_hint=f"unrecognised ADB step: {step!r}")

    async def _act_tap(self, name: str) -> ActResult:
        tree = await self._ensure_tree()
        if tree is None:
            return ActResult(ok=False, failure_hint="view tree not available for tap")
        node = self._find_node(tree, name)
        if node is None:
            return ActResult(ok=False, failure_hint=f"element '{name}' not found in view tree")
        bounds_str = node.get("bounds", "")
        bounds = _parse_bounds(bounds_str)
        if bounds is None:
            return ActResult(ok=False, failure_hint=f"element '{name}' has no parseable bounds")
        x, y = _center(bounds)
        rc, _, stderr = await self._adb("shell", "input", "tap", str(x), str(y))
        if rc != 0:
            return ActResult(ok=False, failure_hint=f"tap ({x},{y}) failed: {stderr}")
        return ActResult(ok=True)

    async def _act_type(self, name: str, text: str) -> ActResult:
        # First tap the field to focus it
        tap_result = await self._act_tap(name)
        if not tap_result.ok:
            return ActResult(ok=False, failure_hint=f"focus field '{name}' failed: {tap_result.failure_hint}")
        # Then type the text
        rc, _, stderr = await self._adb("shell", "input", "text", text)
        if rc != 0:
            return ActResult(ok=False, failure_hint=f"type '{text}' failed: {stderr}")
        return ActResult(ok=True)

    async def assert_(self, step: str) -> AssertResult:
        step = step.strip()

        m = _ASSERT_ELEMENT_VISIBLE_RE.match(step)
        if m:
            return await self._assert_element_visible(m.group("name"))

        m = _ASSERT_ELEMENT_CONTAINS_RE.match(step)
        if m:
            return await self._assert_element_contains(m.group("name"), m.group("text"))

        m = _ASSERT_SCREEN_CONTAINS_RE.match(step)
        if m:
            return await self._assert_screen_contains(m.group("text"))

        return AssertResult(ok=False, failure_hint=f"unrecognised assert step: {step!r}")

    async def _assert_element_visible(self, name: str) -> AssertResult:
        tree = await self._ensure_tree()
        if tree is None:
            return AssertResult(ok=False, failure_hint="view tree not available for assert")
        node = self._find_node(tree, name)
        if node is None:
            return AssertResult(ok=False, failure_hint=f"element '{name}' not found")
        # In uiautomator, if a node is present it's considered visible unless explicitly hidden
        return AssertResult(ok=True)

    async def _assert_element_contains(self, name: str, text: str) -> AssertResult:
        tree = await self._ensure_tree()
        if tree is None:
            return AssertResult(ok=False, failure_hint="view tree not available for assert")
        node = self._find_node(tree, name)
        if node is None:
            return AssertResult(ok=False, failure_hint=f"element '{name}' not found")
        text_lower = text.lower()
        for attr in ("text", "content-desc"):
            val = node.get(attr, "")
            if val and text_lower in val.lower():
                return AssertResult(ok=True)
        return AssertResult(
            ok=False,
            failure_hint=f"element '{name}' does not contain '{text}'",
        )

    async def _assert_screen_contains(self, text: str) -> AssertResult:
        tree = await self._ensure_tree()
        if tree is None:
            return AssertResult(ok=False, failure_hint="view tree not available for assert")
        if self._any_node_contains(tree, text):
            return AssertResult(ok=True)
        return AssertResult(
            ok=False,
            failure_hint=f"screen does not contain '{text}'",
        )

    async def capture_evidence(self) -> Evidence:
        screenshot_b64: str | None = None
        dom: str | None = None

        # Screenshot
        rc, png_bytes_b64, _stderr = await self._adb(
            "exec-out", "screencap", "-p", timeout=15.0
        )
        if rc == 0:
            try:
                # adb exec-out returns raw PNG bytes; base64 encode them
                screenshot_b64 = base64.b64encode(
                    png_bytes_b64.encode("latin-1")
                ).decode("ascii")
            except Exception:
                pass

        # UI dump
        rc2, xml_text, _ = await self._uiautomator_dump()
        if rc2 == 0 and xml_text.strip():
            dom = xml_text
            self._last_xml = xml_text
            self._last_tree = self._xml_to_tree(xml_text)

        return Evidence(screenshot=screenshot_b64, dom=dom)

    # ── internal helpers ──────────────────────────────────────────────────

    async def _ensure_tree(self) -> dict[str, Any] | None:
        if self._last_tree is not None:
            return self._last_tree
        rc, xml_text, _ = await self._uiautomator_dump()
        if rc != 0 or not xml_text.strip():
            return None
        self._last_xml = xml_text
        self._last_tree = self._xml_to_tree(xml_text)
        return self._last_tree
