"""checkers/staging_test.py 单测：mock RunnerController，验 CheckResult 字段。

多仓重构后：cmd 遍历 /workspace/source/*，并行对每个含 `ci-test` target 的仓
跑 `make ci-test`。业务 repo 自己在 Makefile 聚合实际跑啥（unit / integration / lint / ...）。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.staging_test import run_staging_test
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


def _assert_for_each_repo_cmd(cmd: str) -> None:
    """验证 cmd 是 for-each-repo 并行 shell 模板（关键标记即可，别拘束全文）。"""
    assert "/workspace/source/*/" in cmd
    assert "make ci-test" in cmd
    assert "ci-test:" in cmd  # grep Makefile target 过滤
    assert " & " in cmd  # 后台并行
    assert "wait $pid" in cmd
    assert "exit $fail" in cmd


# ── pass：验 cmd 是 for-each-repo 并行版 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_pass(monkeypatch):
    FakeRC = make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=3.5)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-1")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    assert result.stderr_tail == ""
    assert result.duration_sec == 3.5
    _assert_for_each_repo_cmd(result.cmd)
    assert FakeRC.last_cmd == result.cmd


# ── fail ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_fail(monkeypatch):
    FakeRC = make_fake_controller(exit_code=1, stdout="FAIL\n", stderr="panic: nil ptr\n", duration=2.0)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-2")

    assert result.passed is False
    assert result.exit_code == 1
    assert result.stdout_tail == "FAIL\n"
    assert result.stderr_tail == "panic: nil ptr\n"


# ── stdout/stderr tail 截尾 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_truncates_tails(monkeypatch):
    big_out = "x" * 5000
    big_err = "e" * 4000
    FakeRC = make_fake_controller(exit_code=0, stdout=big_out, stderr=big_err)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-3")

    assert len(result.stdout_tail) == 2048
    assert len(result.stderr_tail) == 2048
    assert result.stdout_tail == big_out[-2048:]
    assert result.stderr_tail == big_err[-2048:]


# ── timeout ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_timeout(monkeypatch):
    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr("orchestrator.checkers.staging_test.asyncio.wait_for", fast_wait_for)

    with pytest.raises(TimeoutError):
        await run_staging_test("REQ-4")
