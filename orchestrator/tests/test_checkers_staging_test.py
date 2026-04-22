"""checkers/staging_test.py 单测：mock RunnerController，验 CheckResult 字段。"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers.staging_test import CheckResult, run_staging_test
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    return FakeRC()


# ── pass ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_pass(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=3.5),
    )
    result = await run_staging_test("REQ-1", "make test")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    assert result.stderr_tail == ""
    assert result.duration_sec == 3.5
    assert result.cmd == "make test"


# ── fail ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_fail(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: make_fake_controller(exit_code=1, stdout="FAIL\n", stderr="panic: nil ptr\n", duration=2.0),
    )
    result = await run_staging_test("REQ-2", "make test")

    assert result.passed is False
    assert result.exit_code == 1
    assert result.stdout_tail == "FAIL\n"
    assert result.stderr_tail == "panic: nil ptr\n"


# ── stdout/stderr tail 截尾 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_truncates_tails(monkeypatch):
    big_out = "x" * 5000
    big_err = "e" * 4000
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: make_fake_controller(exit_code=0, stdout=big_out, stderr=big_err),
    )
    result = await run_staging_test("REQ-3", "make test")

    assert len(result.stdout_tail) == 2048
    assert len(result.stderr_tail) == 2048
    assert result.stdout_tail == big_out[-2048:]
    assert result.stderr_tail == big_err[-2048:]


# ── timeout ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_timeout(monkeypatch):
    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)  # 永远不返回
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: SlowRC(),
    )
    with pytest.raises(TimeoutError):
        # 为让测试不卡，patch asyncio.wait_for 直接模拟超时
        async def fast_wait_for(coro, timeout):
            task = asyncio.ensure_future(coro)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                raise TimeoutError() from None

        monkeypatch.setattr("orchestrator.checkers.staging_test.asyncio.wait_for", fast_wait_for)
        await run_staging_test("REQ-4", "make test", timeout_sec=1)
