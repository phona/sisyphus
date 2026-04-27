"""checkers/analyze_artifact_check.py 单测：mock RunnerController，验 CheckResult
字段 + empty-source guard / 0-eligible guard / proposal/tasks/spec literals
（REQ-analyze-artifact-check-1777254586）。

跟 test_checkers_spec_lint.py 同结构。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.analyze_artifact_check import (
    _build_cmd,
    run_analyze_artifact_check,
)
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


@pytest.mark.asyncio
async def test_run_pass(monkeypatch):
    FakeRC = make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=2.0)
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_analyze_artifact_check("REQ-1")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    assert "/workspace/source/*/" in result.cmd
    assert "openspec/changes/REQ-1" in result.cmd
    assert FakeRC.last_cmd == result.cmd


@pytest.mark.asyncio
async def test_run_fail(monkeypatch):
    FakeRC = make_fake_controller(
        exit_code=1, stdout="",
        stderr="=== FAIL: repo-a: ... missing or all empty ===\n",
        duration=3.1,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_analyze_artifact_check("REQ-2")

    assert result.passed is False
    assert result.exit_code == 1
    assert "FAIL" in result.stderr_tail


@pytest.mark.asyncio
async def test_run_timeout(monkeypatch):
    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.asyncio.wait_for",
        fast_wait_for,
    )

    result = await run_analyze_artifact_check("REQ-3", timeout_sec=1)
    assert result.passed is False
    assert result.exit_code == -1
    assert "超时" in result.stderr_tail


def test_build_cmd_workspace_source_existence_guard():
    cmd = _build_cmd("REQ-X")
    assert "[ ! -d /workspace/source ]" in cmd
    assert "FAIL analyze-artifact-check: /workspace/source missing" in cmd


def test_build_cmd_repo_count_zero_guard():
    cmd = _build_cmd("REQ-X")
    assert "find /workspace/source -mindepth 1 -maxdepth 1 -type d" in cmd
    assert '"$repo_count" -eq 0' in cmd
    assert "FAIL analyze-artifact-check: /workspace/source empty" in cmd


def test_build_cmd_zero_eligible_guard():
    cmd = _build_cmd("REQ-X")
    assert "ran=0" in cmd
    assert "ran=$((ran+1))" in cmd
    assert '"$ran" -eq 0' in cmd
    assert "0 source repos eligible" in cmd


def test_build_cmd_references_proposal_tasks_spec():
    cmd = _build_cmd("REQ-X")
    assert "openspec/changes/REQ-X/proposal.md" in cmd
    assert "openspec/changes/REQ-X/tasks.md" in cmd
    assert '"$ch/specs"' in cmd and "spec.md" in cmd


def test_build_cmd_has_checkbox_regex():
    cmd = _build_cmd("REQ-X")
    assert r"\[[ xX]\]" in cmd
    assert "grep -E" in cmd


def test_build_cmd_uses_feat_branch():
    cmd = _build_cmd("REQ-X")
    assert 'git fetch origin "feat/REQ-X"' in cmd
    assert 'git checkout -B "feat/REQ-X" "origin/feat/REQ-X"' in cmd


def test_build_cmd_aggregate_proposal_tasks_flags():
    cmd = _build_cmd("REQ-X")
    assert "has_proposal=0" in cmd
    assert "has_tasks=0" in cmd
    assert "has_proposal=1" in cmd
    assert "has_tasks=1" in cmd
    assert '"$has_proposal" -eq 0' in cmd
    assert '"$has_tasks" -eq 0' in cmd


def test_build_cmd_ends_with_aggregate_exit():
    cmd = _build_cmd("REQ-X")
    assert cmd.rstrip().endswith("[ $fail -eq 0 ]")


def _make_seq_controller(*results: ExecResult):
    seq = list(results)

    class FakeRC:
        calls = 0

        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.calls += 1
            return seq.pop(0)

    return FakeRC


@pytest.mark.asyncio
async def test_recovers_from_dns_flake(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_enabled",
        True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_max",
        1,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_backoff_sec",
        0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="ok\n", stderr="", duration_sec=2.5),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_analyze_artifact_check("REQ-X")
    assert result.passed is True
    assert result.attempts == 2
    assert result.reason is not None
    assert "flake-retry-recovered" in result.reason
    assert FakeRC.calls == 2


@pytest.mark.asyncio
async def test_does_not_retry_real_artifact_failure(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_enabled",
        True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_max",
        2,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.settings.checker_infra_flake_retry_backoff_sec",
        0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=1,
            stdout="",
            stderr="=== FAIL analyze-artifact-check: no eligible repo has openspec/changes/REQ-X/tasks.md with at least one Markdown checkbox ===\n",
            duration_sec=0.5,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.analyze_artifact_check.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_analyze_artifact_check("REQ-X")
    assert result.passed is False
    assert result.attempts == 1
    assert result.reason is None
    assert FakeRC.calls == 1
