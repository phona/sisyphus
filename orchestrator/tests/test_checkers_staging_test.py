"""checkers/staging_test.py 单测：mock RunnerController，验 CheckResult 字段。

多仓重构后 + ttpos-ci 契约统一：cmd 遍历 /workspace/source/*，**repo 之间并行**
对每个含 `ci-unit-test` + `ci-integration-test` target 的仓跑
`make ci-unit-test && make ci-integration-test`（**单 repo 内串行**）。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.staging_test import _build_cmd, run_staging_test
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


def _assert_for_each_repo_cmd(cmd: str) -> None:
    """验证 cmd 是 for-each-repo 并行 shell 模板（关键标记即可，别拘束全文）。

    ttpos-ci 契约统一后：单 repo 内 unit→integration 串行（&&），repo 之间并行（&）。
    """
    assert "/workspace/source/*/" in cmd
    # ttpos-ci 标准 target（ci-test 已废）
    assert "make ci-unit-test" in cmd
    assert "make ci-integration-test" in cmd
    assert "ci-unit-test:" in cmd  # grep Makefile target 过滤
    assert "ci-integration-test:" in cmd
    # 单 repo 内串行（&&），repo 间并行（&）
    assert "&&" in cmd
    assert " & " in cmd  # 后台并行（每仓子 shell）
    assert "wait $pid" in cmd
    assert "[ $fail -eq 0 ]" in cmd  # 不能用 `exit $fail`：orch 包装的 exit-marker echo 不再跑
    # log 文件名 split unit / int
    assert "$name-unit.log" in cmd
    assert "$name-int.log" in cmd
    # fetch err 暴露（regression：之前 git fetch 2>/dev/null 把 auth/network 错全吞）
    assert "fetch_err=" in cmd
    assert "git fetch stderr:" in cmd
    assert "rev-parse --verify" in cmd
    assert 'git fetch origin "feat/REQ-997" 2>/dev/null' not in cmd


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


# ── empty-source guard（REQ-checker-empty-source-1777113775）─────────────


def test_build_cmd_emits_workspace_source_existence_guard():
    """`/workspace/source` 不存在 → exit 1，不能 for 循环 0 次默认 pass。"""
    cmd = _build_cmd("REQ-X")
    assert "[ ! -d /workspace/source ]" in cmd
    assert "FAIL staging_test: /workspace/source missing" in cmd


def test_build_cmd_emits_repo_count_zero_guard():
    """`/workspace/source` 空目录（0 cloned repo）→ exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "find /workspace/source -mindepth 1 -maxdepth 1 -type d" in cmd
    assert '"$repo_count" -eq 0' in cmd
    assert "FAIL staging_test: /workspace/source empty" in cmd


def test_build_cmd_emits_zero_eligible_guard():
    """所有仓都被 skip（无 feat 分支 / 缺 unit-or-integration target）→ ran=0 → exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "ran=0" in cmd
    assert "ran=$((ran+1))" in cmd
    assert '"$ran" -eq 0' in cmd
    assert "0 source repos eligible" in cmd


# ── CIFR-S10/S11 infra-flake retry wiring (REQ-checker-infra-flake-retry-1777247423)


def _make_seq_controller(*results: ExecResult):
    """造一个 fake controller，按 results 顺序逐次返不同 ExecResult。"""
    seq = list(results)

    class FakeRC:
        calls = 0

        async def exec_in_runner(self, req_id, command, **kw):
            FakeRC.calls += 1
            return seq.pop(0)

    return FakeRC


@pytest.mark.asyncio
async def test_run_staging_test_recovers_from_dns_flake(monkeypatch):
    """CIFR-S10 (staging_test): 第一次 DNS flake → 第二次 pass → attempts=2 reason 含 recovered."""
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_enabled", True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_max", 1,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_backoff_sec", 0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="ci-test ok\n", stderr="", duration_sec=2.5),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-X")
    assert result.passed is True
    assert result.exit_code == 0
    assert result.attempts == 2
    assert result.reason is not None
    assert "flake-retry-recovered" in result.reason
    assert FakeRC.calls == 2


@pytest.mark.asyncio
async def test_run_staging_test_does_not_retry_real_test_failure(monkeypatch):
    """CIFR-S11 (staging_test): make Error / TestFoo fail → 不重试，attempts=1, reason=None."""
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_enabled", True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_max", 2,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_backoff_sec", 0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=2, stdout="--- FAIL: TestFoo (0.10s)\n",
            stderr="make: *** [Makefile:42] Error 1", duration_sec=10.0,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-X")
    assert result.passed is False
    assert result.exit_code == 2
    assert result.attempts == 1
    assert result.reason is None
    assert FakeRC.calls == 1


@pytest.mark.asyncio
async def test_run_staging_test_retry_disabled_when_setting_off(monkeypatch):
    """settings.checker_infra_flake_retry_enabled=False → 即使 DNS flake 也不重试."""
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_enabled", False,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_max", 5,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.settings.checker_infra_flake_retry_backoff_sec", 0,
    )
    FakeRC = _make_seq_controller(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-X")
    assert result.passed is False
    assert result.attempts == 1
    assert result.reason is None
    assert FakeRC.calls == 1
