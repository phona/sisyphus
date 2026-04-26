"""checkers/dev_cross_check.py 单测：mock RunnerController，验 CheckResult 字段。

ttpos-ci 契约统一后：cmd 遍历 /workspace/source/*，串行对每个含 `ci-lint` target 的仓
跑 `BASE_REV=$(git merge-base HEAD origin/<default_branch>) make ci-lint`。
BASE_REV 计算先读 `git symbolic-ref refs/remotes/origin/HEAD` 拿仓实际 default_branch，
再退到静态链 `main → master → develop → dev → 空`（REQ-fix-base-rev-default-branch-1777214183）。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.dev_cross_check import _build_cmd, run_dev_cross_check
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
    # BASE_REV 计算 + 注入（REQ-fix-base-rev-default-branch-1777214183）
    assert "BASE_REV=" in cmd
    # 优先：origin/HEAD 符号引用（仓真实 default_branch，如 release）
    assert "git symbolic-ref --short refs/remotes/origin/HEAD" in cmd
    assert "default_branch=" in cmd
    assert 'git merge-base HEAD "origin/$default_branch"' in cmd
    # 退化静态链：main → master → develop → dev
    assert "git merge-base HEAD origin/main" in cmd
    assert "git merge-base HEAD origin/master" in cmd  # fallback
    assert "git merge-base HEAD origin/develop" in cmd  # fallback
    assert "git merge-base HEAD origin/dev" in cmd  # fallback
    # 累加 fail 标志
    assert "fail=0" in cmd
    assert "fail=1" in cmd
    assert "[ $fail -eq 0 ]" in cmd  # 不能用 `exit $fail`：orch 包装的 exit-marker echo 不再跑
    # fetch err 暴露（regression：之前 git fetch 2>/dev/null 把 auth/network 错全吞）
    assert "fetch_err=" in cmd  # 捕到 stderr 不再丢
    assert "git fetch stderr:" in cmd  # 失败 message 带真原因
    assert "rev-parse --verify" in cmd  # 单独验 origin ref 在不在
    assert 'git fetch origin "feat/' in cmd
    # 不应再出现把 fetch stderr 全吞的写法
    assert 'git fetch origin "feat/REQ-997" 2>/dev/null' not in cmd


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


# ── empty-source guard（REQ-checker-empty-source-1777113775）─────────────


def test_build_cmd_emits_workspace_source_existence_guard():
    """`/workspace/source` 不存在 → exit 1，不能 for 循环 0 次默认 pass。"""
    cmd = _build_cmd("REQ-X")
    assert "[ ! -d /workspace/source ]" in cmd
    assert "FAIL dev_cross_check: /workspace/source missing" in cmd


def test_build_cmd_emits_repo_count_zero_guard():
    """`/workspace/source` 空目录（0 cloned repo）→ exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "find /workspace/source -mindepth 1 -maxdepth 1 -type d" in cmd
    assert '"$repo_count" -eq 0' in cmd
    assert "FAIL dev_cross_check: /workspace/source empty" in cmd


def test_build_cmd_emits_zero_eligible_guard():
    """所有仓都被 skip（无 feat 分支 / 无 ci-lint target）→ ran=0 → exit 1。"""
    cmd = _build_cmd("REQ-X")
    assert "ran=0" in cmd
    assert "ran=$((ran+1))" in cmd
    assert '"$ran" -eq 0' in cmd
    assert "0 source repos eligible" in cmd


# ── BASE_REV default_branch resolution（REQ-fix-base-rev-default-branch-1777214183）──


def test_build_cmd_resolves_default_branch_from_origin_head_first():
    """BASE_REV 链先 resolve 仓真实 default_branch（origin/HEAD 符号引用），再退静态链。

    修这条前静态链 main → develop → dev 全 miss 时（默认分支 release / master 等）
    BASE_REV 必空 → ci-lint 退化全量扫，增量功能形同虚设。
    """
    cmd = _build_cmd("REQ-X")

    # 步骤 1: 解析 origin/HEAD 符号引用（剥 `origin/` 前缀拿到 branch 名）
    assert "git symbolic-ref --short refs/remotes/origin/HEAD" in cmd
    assert "sed 's@^origin/@@'" in cmd
    assert "default_branch=" in cmd

    # 步骤 2: 优先用 default_branch 跑 merge-base，且必须 gate 在 `[ -n ... ]` 里
    # 防 default_branch 空时调 `origin/` 触发歧义错误
    assert '[ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch"' in cmd

    # 步骤 3: 退化静态链顺序固定（symbolic-ref 失败时兜底）
    head = cmd.find("default_branch=")
    main_idx = cmd.find("git merge-base HEAD origin/main", head)
    master_idx = cmd.find("git merge-base HEAD origin/master", head)
    develop_idx = cmd.find("git merge-base HEAD origin/develop", head)
    dev_idx = cmd.find('git merge-base HEAD origin/dev 2>/dev/null', head)
    assert head < main_idx < master_idx < develop_idx < dev_idx

    # 步骤 4: 全 miss 退空字符串
    assert 'echo ""' in cmd
