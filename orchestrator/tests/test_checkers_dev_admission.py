"""checkers/dev_admission.py 单测（M14d）。

三类 case：
- manifest.pr 未填 → fail
- scope 越界 → fail（返回越界文件）
- 全 OK → pass
"""
from __future__ import annotations

import pytest

from orchestrator.checkers import dev_admission
from orchestrator.checkers._types import CheckResult
from orchestrator.k8s_runner import ExecResult


class _FakeRC:
    def __init__(self, changed: list[str]):
        self._changed = changed

    async def exec_in_runner(self, req_id, command, **kw):
        # 模拟 git diff --name-only 输出
        return ExecResult(
            exit_code=0,
            stdout="\n".join(self._changed) + ("\n" if self._changed else ""),
            stderr="",
            duration_sec=0.1,
        )


def _patch_manifest(monkeypatch, manifest: dict):
    async def fake_read(req_id, **kw):
        return manifest
    monkeypatch.setattr(
        "orchestrator.checkers.dev_admission.manifest_io.read_manifest",
        fake_read,
    )


def _patch_git(monkeypatch, changed: list[str]):
    monkeypatch.setattr(
        "orchestrator.checkers.dev_admission.k8s_runner.get_controller",
        lambda: _FakeRC(changed),
    )


# ── check_scope helper：纯函数表驱动 ─────────────────────────────────────

@pytest.mark.parametrize("changed,scope,expected_out_of_scope", [
    # scope 空 → 全 OK
    (["a/b.py"], [], []),
    # 精确匹配
    (["internal/auth/login.go"], ["internal/auth/**"], []),
    # 目录前缀
    (["internal/auth/jwt.go"], ["internal/auth/"], []),
    # 越界
    (["internal/order/pay.go"], ["internal/auth/**"], ["internal/order/pay.go"]),
    # 混合：部分越界
    (
        ["internal/auth/a.go", "internal/auth/b.go", "cmd/server/main.go"],
        ["internal/auth/**"],
        ["cmd/server/main.go"],
    ),
    # 单文件 pattern
    (["internal/model/user.go"], ["internal/model/user.go"], []),
    (["internal/model/order.go"], ["internal/model/user.go"], ["internal/model/order.go"]),
])
def test_check_scope(changed, scope, expected_out_of_scope):
    assert dev_admission.check_scope(changed, scope) == expected_out_of_scope


# ── check_pr_manifest 表驱动 ──────────────────────────────────────────────

@pytest.mark.parametrize("manifest,expected_ok", [
    ({"pr": {"repo": "phona/ubox", "number": 42}}, True),
    ({"pr": {"repo": "phona/ubox"}}, False),   # number 未填
    ({"pr": {"number": 42}}, False),            # repo 未填
    ({"pr": {"repo": "phona/ubox", "number": 0}}, False),  # number 非法
    ({}, False),
])
def test_check_pr_manifest(manifest, expected_ok):
    err = dev_admission.check_pr_manifest(manifest)
    if expected_ok:
        assert err is None
    else:
        assert err is not None


# ── run_dev_admission 端到端 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admission_ok(monkeypatch):
    _patch_manifest(monkeypatch, {"pr": {"repo": "phona/foo", "number": 42}})
    _patch_git(monkeypatch, ["internal/auth/login.go"])
    result = await dev_admission.run_dev_admission(
        "REQ-9", task_scope=["internal/auth/**"], repo_cwd="source/foo",
    )
    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_admission_pr_missing(monkeypatch):
    _patch_manifest(monkeypatch, {"pr": {"repo": "phona/foo"}})  # no number
    result = await dev_admission.run_dev_admission(
        "REQ-9", task_scope=None, repo_cwd="source/foo",
    )
    assert result.passed is False
    assert "number" in result.stderr_tail


@pytest.mark.asyncio
async def test_admission_scope_violation(monkeypatch):
    _patch_manifest(monkeypatch, {"pr": {"repo": "phona/foo", "number": 42}})
    _patch_git(monkeypatch, [
        "internal/auth/login.go",
        "internal/order/pay.go",  # 越界
    ])
    result = await dev_admission.run_dev_admission(
        "REQ-9", task_scope=["internal/auth/**"], repo_cwd="source/foo",
    )
    assert result.passed is False
    assert "越界文件" in result.stderr_tail
    assert "internal/order/pay.go" in result.stderr_tail


@pytest.mark.asyncio
async def test_admission_single_dev_mode_skips_scope(monkeypatch):
    """task_scope=None（单 dev 模式）→ 只查 manifest.pr，不跑 git diff。"""
    _patch_manifest(monkeypatch, {"pr": {"repo": "phona/foo", "number": 42}})

    # 不 patch git diff — 如果 checker 尝试跑会挂
    result = await dev_admission.run_dev_admission(
        "REQ-9", task_scope=None, repo_cwd="source/foo",
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_admission_manifest_read_fail(monkeypatch):
    """读 manifest 挂 → fail with reason None + 语义 error。"""
    from orchestrator.checkers import manifest_io

    async def fail_read(req_id, **kw):
        raise manifest_io.ManifestReadError("PVC unreachable")

    monkeypatch.setattr(
        "orchestrator.checkers.dev_admission.manifest_io.read_manifest",
        fail_read,
    )
    result = await dev_admission.run_dev_admission(
        "REQ-9", task_scope=["x/**"], repo_cwd="source/foo",
    )
    assert result.passed is False
    assert "PVC unreachable" in result.stderr_tail
