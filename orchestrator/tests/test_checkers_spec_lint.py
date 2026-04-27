"""checkers/spec_lint.py 单测：mock RunnerController，验 CheckResult 字段 +
empty-source guard（REQ-checker-empty-source-1777113775）的 cmd 形状。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.spec_lint import _build_cmd, run_spec_lint
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


def _assert_for_each_repo_cmd(cmd: str) -> None:
    """验证 cmd 是 for-each-repo 模板，跑 openspec validate + scenario refs。"""
    assert "/workspace/source/*/" in cmd
    assert "openspec validate" in cmd
    assert "check-scenario-refs.sh" in cmd
    assert "fail=0" in cmd
    assert "fail=1" in cmd
    assert "[ $fail -eq 0 ]" in cmd


# ── pass ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_spec_lint_pass(monkeypatch):
    FakeRC = make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=2.0)
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_spec_lint("REQ-1")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    _assert_for_each_repo_cmd(result.cmd)
    assert FakeRC.last_cmd == result.cmd


# ── fail ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_spec_lint_fail(monkeypatch):
    FakeRC = make_fake_controller(
        exit_code=1, stdout="",
        stderr="=== FAIL: repo-a ===\n", duration=3.1,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_spec_lint("REQ-2")

    assert result.passed is False
    assert result.exit_code == 1
    assert "FAIL" in result.stderr_tail


# ── timeout ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_spec_lint_timeout(monkeypatch):
    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr("orchestrator.checkers.spec_lint.asyncio.wait_for", fast_wait_for)

    result = await run_spec_lint("REQ-3", timeout_sec=1)
    assert result.passed is False
    assert result.exit_code == -1
    assert "超时" in result.stderr_tail


# ── empty-source guard（REQ-checker-empty-source-1777113775）─────────────


def test_build_cmd_emits_workspace_source_existence_guard():
    """`/workspace/source` 不存在直接 exit 1，不能让 for 循环 0 次默认 pass。"""
    cmd = _build_cmd("REQ-X")
    assert "[ ! -d /workspace/source ]" in cmd
    assert "FAIL spec_lint: /workspace/source missing" in cmd


def test_build_cmd_emits_repo_count_zero_guard():
    """`/workspace/source` 是空目录（0 个 cloned repo）也直接 exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "find /workspace/source -mindepth 1 -maxdepth 1 -type d" in cmd
    assert '"$repo_count" -eq 0' in cmd
    assert "FAIL spec_lint: /workspace/source empty" in cmd


def test_build_cmd_emits_zero_eligible_guard():
    """所有仓都被 skip（feat 分支不存在 / 没 openspec/changes/<REQ>/）→ ran=0 → exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "ran=0" in cmd
    assert "ran=$((ran+1))" in cmd
    assert '"$ran" -eq 0' in cmd
    assert "0 source repos eligible" in cmd


# ── CIFR-S10 infra-flake retry wiring (REQ-checker-infra-flake-retry-1777247423)


def _make_seq_controller(*results: ExecResult):
    seq = list(results)

    class FakeRC:
        calls = 0

        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.calls += 1
            return seq.pop(0)

    return FakeRC


@pytest.mark.asyncio
async def test_run_spec_lint_recovers_from_dns_flake(monkeypatch):
    """CIFR-S10 (spec_lint): DNS flake 一次 → retry pass → attempts=2 reason recovered."""
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_enabled", True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_max", 1,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_backoff_sec", 0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="openspec ok\n", stderr="", duration_sec=2.5),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_spec_lint("REQ-X")
    assert result.passed is True
    assert result.attempts == 2
    assert result.reason is not None
    assert "flake-retry-recovered" in result.reason
    assert FakeRC.calls == 2


@pytest.mark.asyncio
async def test_run_spec_lint_does_not_retry_validate_failure(monkeypatch):
    """openspec validate 真出错 → 不重试."""
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_enabled", True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_max", 2,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.settings.checker_infra_flake_retry_backoff_sec", 0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=1,
            stdout="",
            stderr="x [ERROR] capability/spec.md: ADDED 'X' must contain SHALL or MUST\n",
            duration_sec=0.5,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.spec_lint.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_spec_lint("REQ-X")
    assert result.passed is False
    assert result.attempts == 1
    assert result.reason is None
    assert FakeRC.calls == 1


# ── CIFR-S12: pr_ci_watch is unchanged ─────────────────────────────────


def test_pr_ci_watch_does_not_import_flake_module():
    """CIFR-S12: pr_ci_watch 自有 HTTP retry，不引 _flake / run_with_flake_retry."""
    from pathlib import Path
    pr_ci = Path(__file__).parent.parent / "src" / "orchestrator" / "checkers" / "pr_ci_watch.py"
    src = pr_ci.read_text()
    assert "from ._flake" not in src
    assert "import _flake" not in src
    assert "run_with_flake_retry" not in src
