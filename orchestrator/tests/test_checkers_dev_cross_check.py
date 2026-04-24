"""checkers/dev_cross_check.py 单测：mock RunnerController，验 CheckResult 字段。

ttpos-ci 契约统一后：cmd 遍历 /workspace/source/*，串行对每个含 `ci-lint` target 的仓
跑 `BASE_REV=$(git merge-base HEAD origin/main) make ci-lint`。
BASE_REV 缺失（fetch 不到 origin/main / develop / dev）则传空，ci-lint 退化为全量扫描。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.dev_cross_check import run_dev_cross_check
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


def _assert_for_each_repo_cmd(cmd: str) -> None:
    """验证 cmd 是 for-each-repo 串行 shell 模板，跑 ci-lint + BASE_REV。"""
    assert "/workspace/source/*/" in cmd
    assert "make ci-lint" in cmd
    assert "ci-lint:" in cmd  # grep Makefile target 过滤
    # BASE_REV 计算 + 注入
    assert "BASE_REV=" in cmd
    assert "git merge-base HEAD origin/main" in cmd
    assert "git merge-base HEAD origin/develop" in cmd  # fallback
    assert "git merge-base HEAD origin/dev" in cmd  # fallback
    # 累加 fail 标志
    assert "fail=0" in cmd
    assert "fail=1" in cmd
    assert "[ $fail -eq 0 ]" in cmd  # 不能用 `exit $fail`：orch 包装的 exit-marker echo 不再跑


# ── pass ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_dev_cross_check_pass(monkeypatch):
    FakeRC = make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=1.5)
    monkeypatch.setattr(
        "orchestrator.checkers.dev_cross_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_dev_cross_check("REQ-1")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    assert result.stderr_tail == ""
    _assert_for_each_repo_cmd(result.cmd)
    assert FakeRC.last_cmd == result.cmd


# ── fail ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_dev_cross_check_fail(monkeypatch):
    FakeRC = make_fake_controller(
        exit_code=1, stdout="lint warnings...\n",
        stderr="=== FAIL: ttpos-server-go ===\n", duration=8.2,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.dev_cross_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_dev_cross_check("REQ-2")

    assert result.passed is False
    assert result.exit_code == 1
    assert "FAIL" in result.stderr_tail


# ── stdout/stderr tail 截尾 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_dev_cross_check_truncates_tails(monkeypatch):
    big_out = "x" * 5000
    big_err = "e" * 4000
    FakeRC = make_fake_controller(exit_code=0, stdout=big_out, stderr=big_err)
    monkeypatch.setattr(
        "orchestrator.checkers.dev_cross_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_dev_cross_check("REQ-3")

    assert len(result.stdout_tail) == 2048
    assert len(result.stderr_tail) == 2048
    assert result.stdout_tail == big_out[-2048:]
    assert result.stderr_tail == big_err[-2048:]


# ── timeout ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_dev_cross_check_timeout(monkeypatch):
    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.dev_cross_check.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr("orchestrator.checkers.dev_cross_check.asyncio.wait_for", fast_wait_for)

    # timeout 走 internal CheckResult 返回（不抛异常）
    result = await run_dev_cross_check("REQ-4", timeout_sec=1)
    assert result.passed is False
    assert result.exit_code == -1
    assert "超时" in result.stderr_tail
