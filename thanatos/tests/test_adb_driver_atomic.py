"""AdbDriver atomic operations — exercise pure logic without touching adb.

These tests stub ``_adb`` / ``_uiautomator_dump`` to inject canned responses,
so they run in CI without a redroid container.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from thanatos.drivers.adb import AdbDriver

# ── helpers ──────────────────────────────────────────────────────────────


def _xml(*nodes: str) -> str:
    """Wrap node fragments into a uiautomator-style hierarchy XML."""
    body = "".join(nodes)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<hierarchy rotation="0">'
        f'<node bounds="[0,0][1080,1920]">{body}</node>'
        "</hierarchy>"
    )


def _node(
    *,
    text: str = "",
    desc: str = "",
    rid: str = "",
    bounds: str = "[100,200][300,400]",
    cls: str = "android.widget.TextView",
) -> str:
    return (
        f'<node text="{text}" content-desc="{desc}" resource-id="{rid}" '
        f'class="{cls}" bounds="{bounds}" clickable="true" enabled="true" />'
    )


class FakeAdb:
    """Replaces AdbDriver._adb / _uiautomator_dump with scripted responses."""

    def __init__(self, dump_xml: str | None = None, page_focus: str | None = None) -> None:
        self.dump_xml = dump_xml
        self.page_focus = page_focus
        self.calls: list[tuple[str, ...]] = []

    async def adb(self, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
        self.calls.append(args)
        if args[:2] == ("shell", "echo"):
            return 0, "ping", ""
        if args[0] == "connect":
            return 0, "", ""
        if args[:3] == ("shell", "input", "tap"):
            return 0, "", ""
        if args[:3] == ("shell", "input", "text"):
            return 0, "", ""
        if args[:4] == ("shell", "dumpsys", "window", "windows"):
            if self.page_focus is None:
                return 1, "", "no focus"
            return 0, self.page_focus, ""
        if args[0] == "exec-out" and args[1] == "screencap":
            # latin-1 → base64 round-trip yields a deterministic string
            return 0, "PNGFAKE", ""
        return 0, "", ""

    async def dump(self) -> tuple[int, str, str]:
        if self.dump_xml is None:
            return 1, "", "no dump"
        return 0, self.dump_xml, ""


@pytest.fixture
def driver_with_dump() -> tuple[AdbDriver, FakeAdb]:
    """AdbDriver pre-bound to fake adb returning a 2-button page."""
    fake = FakeAdb(
        dump_xml=_xml(
            _node(text="Login", rid="com.app:id/login_btn", bounds="[100,200][300,400]"),
            _node(text="Forgot password", desc="forgot password",
                  rid="com.app:id/forgot_link", bounds="[100,500][500,600]"),
        ),
        page_focus=(
            "  mCurrentFocus=Window{abc1234 u0 com.ttpos.shop/.LoginActivity}\n"
        ),
    )
    drv = AdbDriver()
    drv._endpoint = "localhost:5555"
    # Patch internals
    drv._adb = fake.adb  # type: ignore[method-assign]
    drv._uiautomator_dump = fake.dump  # type: ignore[method-assign]
    return drv, fake


# ── tests ────────────────────────────────────────────────────────────────


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_observe_returns_uiautomator_tree(driver_with_dump: Any) -> None:
    drv, _ = driver_with_dump
    tree = await drv.observe()
    assert tree.kind == "uiautomator"
    assert "children" in tree.payload


@pytest.mark.asyncio
async def test_tap_by_name_finds_button_and_sends_input_tap(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, fake = driver_with_dump
    result = await drv.tap_by_name("login")
    assert result.ok, result.failure_hint
    # Should center of [100,200][300,400] = (200, 300)
    assert ("shell", "input", "tap", "200", "300") in fake.calls


@pytest.mark.asyncio
async def test_tap_by_name_returns_failure_when_element_missing(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    result = await drv.tap_by_name("checkout button")
    assert not result.ok
    assert "not found" in (result.failure_hint or "")


@pytest.mark.asyncio
async def test_type_into_focuses_then_sends_text(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, fake = driver_with_dump
    result = await drv.type_into("forgot", "hello world")
    assert result.ok, result.failure_hint
    assert any(call[:3] == ("shell", "input", "text") for call in fake.calls)


@pytest.mark.asyncio
async def test_wait_sleeps_only_briefly() -> None:
    drv = AdbDriver()
    result = await drv.wait(50)
    assert result.ok


@pytest.mark.asyncio
async def test_wait_rejects_negative_ms() -> None:
    drv = AdbDriver()
    result = await drv.wait(-1)
    assert not result.ok


@pytest.mark.asyncio
async def test_wait_for_text_succeeds_when_text_present(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    result = await drv.wait_for_text("Login", timeout_ms=2000)
    assert result.ok


@pytest.mark.asyncio
async def test_wait_for_text_times_out_when_text_absent(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    result = await drv.wait_for_text("Checkout", timeout_ms=500)
    assert not result.ok
    assert "did not appear" in (result.failure_hint or "")


@pytest.mark.asyncio
async def test_current_page_extracts_package_and_activity(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    page = await drv.current_page()
    assert page["package"] == "com.ttpos.shop"
    assert page["activity"] == ".LoginActivity"


@pytest.mark.asyncio
async def test_current_page_reports_no_focus_when_dumpsys_empty() -> None:
    fake = FakeAdb(page_focus=None)
    drv = AdbDriver()
    drv._adb = fake.adb  # type: ignore[method-assign]
    drv._uiautomator_dump = fake.dump  # type: ignore[method-assign]
    page = await drv.current_page()
    assert page["package"] is None


@pytest.mark.asyncio
async def test_find_element_returns_snapshot_with_center(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    snap = await drv.find_element("forgot")
    assert snap is not None
    assert snap["center"] == [300, 550]
    assert snap["resource-id"] == "com.app:id/forgot_link"


@pytest.mark.asyncio
async def test_find_element_returns_none_for_missing_name(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    snap = await drv.find_element("nonexistent widget")
    assert snap is None


@pytest.mark.asyncio
async def test_screenshot_returns_base64(
    driver_with_dump: tuple[AdbDriver, FakeAdb],
) -> None:
    drv, _ = driver_with_dump
    png = await drv.screenshot()
    assert png is not None
    # base64 of "PNGFAKE" (latin-1 → bytes) is "UE5HRkFLRQ=="
    assert png == "UE5HRkFLRQ=="
