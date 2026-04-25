"""actions/start_analyze.py + start_analyze_with_finalized_intent.py 单测：

REQ-clone-and-pr-ci-fallback-1777115925：验 server-side clone 派发与失败传播。

不测 BKD REST 主体（在 test_bkd_rest.py），不测 ensure_runner 主体
（在 test_k8s_runner.py），只测 _clone helper 跟 action 串起来的契约。
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.actions import _clone, start_analyze, start_analyze_with_finalized_intent
from orchestrator.state import Event


@dataclass
class FakeExec:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_sec: float = 0.1


def _make_body(*, project_id: str = "nnvxh8wj", issue_id: str = "issue-X"):
    return SimpleNamespace(projectId=project_id, issueId=issue_id, title="t")


def _patch_runner(monkeypatch, *, exec_fn: AsyncMock, ensure_ready_fn: AsyncMock | None = None):
    """同时 patch _clone 跟 start_analyze* 路径上的 k8s_runner.get_controller。"""
    if ensure_ready_fn is None:
        ensure_ready_fn = AsyncMock(return_value="runner-pod-x")

    class FakeRC:
        def __init__(self):
            self.exec_in_runner = exec_fn
            self.ensure_runner = ensure_ready_fn

    fake_rc = FakeRC()
    # _clone helper 调 k8s_runner.get_controller()
    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: fake_rc)
    # start_analyze.py 同样的 namespace
    monkeypatch.setattr(start_analyze.k8s_runner, "get_controller", lambda: fake_rc)
    monkeypatch.setattr(
        start_analyze_with_finalized_intent.k8s_runner, "get_controller",
        lambda: fake_rc,
    )
    return fake_rc


def _patch_bkd_client(monkeypatch, *, target_module, follow_up: AsyncMock | None = None,
                     update_issue: AsyncMock | None = None,
                     create_issue: AsyncMock | None = None):
    """patch target module 的 BKDClient，捕获 follow_up_issue / update_issue / create_issue 调用。"""
    follow_up = follow_up or AsyncMock(return_value=None)
    update_issue = update_issue or AsyncMock(return_value=None)
    create_issue = create_issue or AsyncMock(
        return_value=SimpleNamespace(id="created-issue-X"),
    )

    bkd_instance = MagicMock()
    bkd_instance.follow_up_issue = follow_up
    bkd_instance.update_issue = update_issue
    bkd_instance.create_issue = create_issue
    bkd_instance.__aenter__ = AsyncMock(return_value=bkd_instance)
    bkd_instance.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(target_module, "BKDClient", lambda *a, **kw: bkd_instance)
    return follow_up, update_issue, create_issue


# ── start_analyze: server-side clone happy path ─────────────────────────────


@pytest.mark.asyncio
async def test_start_analyze_server_side_clones_when_involved_repos_present(monkeypatch):
    """ctx 含 involved_repos → exec_in_runner 跑 sisyphus-clone-repos.sh，agent 收到 prompt。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, update_issue, _ = _patch_bkd_client(
        monkeypatch, target_module=start_analyze,
    )

    ctx = {"involved_repos": ["phona/repo-a", "ZonEaseTech/ttpos-server-go"]}
    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X", tags=[], ctx=ctx,
    )

    # 1) clone 跑过：cmd 含 helper 路径 + 两个仓
    exec_fn.assert_awaited_once()
    cmd = exec_fn.await_args.args[1]
    assert "/opt/sisyphus/scripts/sisyphus-clone-repos.sh" in cmd
    assert "phona/repo-a" in cmd
    assert "ZonEaseTech/ttpos-server-go" in cmd

    # 2) agent 收到 prompt（clone 之后）
    follow_up.assert_awaited_once()
    update_issue.assert_awaited()  # 至少 rename + status=working 两次

    # 3) return 包含 cloned_repos
    assert rv["cloned_repos"] == ["phona/repo-a", "ZonEaseTech/ttpos-server-go"]
    assert "emit" not in rv  # 没 escalate


@pytest.mark.asyncio
async def test_start_analyze_skips_clone_when_no_involved_repos(monkeypatch):
    """直接路径：ctx 没 involved_repos → 不调 exec_in_runner，agent 还是被 dispatch。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, _ = _patch_bkd_client(monkeypatch, target_module=start_analyze)

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X", tags=[],
        ctx={"intent_title": "no involved_repos here"},
    )

    exec_fn.assert_not_awaited()  # 跳过 clone
    follow_up.assert_awaited_once()  # 但仍 dispatch agent
    assert rv["cloned_repos"] is None


@pytest.mark.asyncio
async def test_start_analyze_clone_failure_emits_verify_escalate(monkeypatch):
    """clone helper exit 非 0 → return emit=VERIFY_ESCALATE，agent 不被 dispatch。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=5, stderr="auth error"))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, _ = _patch_bkd_client(monkeypatch, target_module=start_analyze)

    ctx = {"intake_finalized_intent": {"involved_repos": ["phona/typo-repo"]}}
    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X", tags=[], ctx=ctx,
    )

    exec_fn.assert_awaited_once()
    follow_up.assert_not_awaited()  # 不打 agent 进空 PVC
    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    assert "clone failed" in rv["reason"]
    assert "5" in rv["reason"]  # exit code 出现在 reason


# ── start_analyze_with_finalized_intent: intake 路径 ──────────────────────


@pytest.mark.asyncio
async def test_start_analyze_with_finalized_intent_clones_involved_repos(monkeypatch):
    """intake 路径必有 finalized intent；server-side clone 拿 involved_repos。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, create_issue = _patch_bkd_client(
        monkeypatch, target_module=start_analyze_with_finalized_intent,
    )

    ctx = {
        "intake_finalized_intent": {
            "involved_repos": ["phona/repo-a", "phona/repo-b", "phona/repo-c"],
            "business_behavior": "x", "data_constraints": "y",
            "edge_cases": "z", "do_not_touch": "w", "acceptance": "v",
        },
    }
    rv = await start_analyze_with_finalized_intent.start_analyze_with_finalized_intent(
        body=_make_body(), req_id="REQ-X", tags=[], ctx=ctx,
    )

    exec_fn.assert_awaited_once()
    cmd = exec_fn.await_args.args[1]
    for r in ("phona/repo-a", "phona/repo-b", "phona/repo-c"):
        assert r in cmd

    create_issue.assert_awaited_once()  # intake 路径要建新 analyze issue
    follow_up.assert_awaited_once()
    assert rv["cloned_repos"] == ["phona/repo-a", "phona/repo-b", "phona/repo-c"]
    assert "emit" not in rv


@pytest.mark.asyncio
async def test_start_analyze_with_finalized_intent_clone_failure_escalates(monkeypatch):
    """intake 路径 clone 失败 → VERIFY_ESCALATE，且不 create analyze issue。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=2, stderr="repo not found"))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, create_issue = _patch_bkd_client(
        monkeypatch, target_module=start_analyze_with_finalized_intent,
    )

    ctx = {"intake_finalized_intent": {"involved_repos": ["phona/typo"]}}
    rv = await start_analyze_with_finalized_intent.start_analyze_with_finalized_intent(
        body=_make_body(), req_id="REQ-X", tags=[], ctx=ctx,
    )

    exec_fn.assert_awaited_once()
    create_issue.assert_not_awaited()
    follow_up.assert_not_awaited()
    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    assert "clone failed" in rv["reason"]
    assert "2" in rv["reason"]


@pytest.mark.asyncio
async def test_start_analyze_with_finalized_intent_missing_finalized_escalates(monkeypatch):
    """intake_finalized_intent 缺失 → VERIFY_ESCALATE（保留旧契约）。"""
    # 不 patch runner / bkd —— 该 case 在它们之前就 return
    rv = await start_analyze_with_finalized_intent.start_analyze_with_finalized_intent(
        body=_make_body(), req_id="REQ-X", tags=[], ctx={},
    )
    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    assert "intake_finalized_intent" in rv["reason"]


# ── _clone helper 行为单测 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_helper_skips_when_no_controller(monkeypatch):
    """k8s_runner.get_controller 抛 RuntimeError → 跳过 clone（dev 环境兼容）。"""
    def raise_no_ctrl():
        raise RuntimeError("controller not initialized")
    monkeypatch.setattr(_clone.k8s_runner, "get_controller", raise_no_ctrl)

    repos, rc = await _clone.clone_involved_repos_into_runner(
        "REQ-X", {"involved_repos": ["phona/repo-a"]},
    )
    assert repos is None
    assert rc is None


@pytest.mark.asyncio
async def test_clone_helper_finalized_intent_takes_priority(monkeypatch):
    """ctx.intake_finalized_intent.involved_repos 优先于 ctx.involved_repos。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())

    ctx = {
        "intake_finalized_intent": {"involved_repos": ["finalized/wins"]},
        "involved_repos": ["fallback/loses"],
    }
    repos, rc = await _clone.clone_involved_repos_into_runner("REQ-X", ctx)
    assert repos == ["finalized/wins"]
    assert rc is None
    cmd = exec_fn.await_args.args[1]
    assert "finalized/wins" in cmd
    assert "fallback/loses" not in cmd


@pytest.mark.asyncio
async def test_clone_helper_filters_non_string_repos(monkeypatch):
    """involved_repos 含非字符串项 → 过滤掉，不传给 helper。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())

    ctx = {"involved_repos": ["phona/repo-a", None, "", 42, "phona/repo-b"]}
    repos, rc = await _clone.clone_involved_repos_into_runner("REQ-X", ctx)
    assert repos == ["phona/repo-a", "phona/repo-b"]
    assert rc is None
