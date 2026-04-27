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
from orchestrator.admission import AdmissionDecision
from orchestrator.state import Event


@pytest.fixture(autouse=True)
def _admit_by_default(monkeypatch):
    """Admission gate 默认 admit=True 通过；个别 case 要 deny 自己再 patch。"""
    monkeypatch.setattr(
        start_analyze, "check_admission",
        AsyncMock(return_value=AdmissionDecision(admit=True)),
    )
    monkeypatch.setattr(start_analyze.db, "get_pool", lambda: object())
    # REQ-issue-link-pr-quality-base-1777218242: success path stashes
    # analyze_issue_id via update_context. Fixture pool is dummy object();
    # patch update_context to no-op so existing tests don't crash.
    # Tests that want to inspect the call can re-patch.
    noop = AsyncMock()
    monkeypatch.setattr(start_analyze.req_state, "update_context", noop)
    # REQ-427: dispatch_slugs slug check — no hit by default so create_issue proceeds.
    monkeypatch.setattr(
        start_analyze_with_finalized_intent.dispatch_slugs, "get", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        start_analyze_with_finalized_intent.dispatch_slugs, "put", AsyncMock()
    )


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


# ── REQ-clone-fallback-direct-analyze-1777119520: multi-layer fallback ────

@pytest.mark.asyncio
async def test_clone_helper_uses_repo_tags_when_ctx_empty(monkeypatch):
    """ctx 没 involved_repos，但 BKD tags 含 `repo:<org>/<name>` → 解析后 clone。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())

    repos, rc = await _clone.clone_involved_repos_into_runner(
        "REQ-X",
        ctx={"intent_title": "direct analyze entry"},
        tags=["analyze", "REQ-X", "repo:phona/foo", "repo:ZonEaseTech/bar"],
    )
    assert repos == ["phona/foo", "ZonEaseTech/bar"]
    assert rc is None
    cmd = exec_fn.await_args.args[1]
    assert "phona/foo" in cmd
    assert "ZonEaseTech/bar" in cmd


@pytest.mark.asyncio
async def test_clone_helper_uses_default_repos_when_ctx_and_tags_empty(monkeypatch):
    """ctx + tags 都没 involved_repos → settings.default_involved_repos 兜底。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())

    repos, rc = await _clone.clone_involved_repos_into_runner(
        "REQ-X",
        ctx={"intent_title": "direct analyze, no repo tag"},
        tags=["analyze", "REQ-X"],
        default_repos=["phona/sisyphus"],
    )
    assert repos == ["phona/sisyphus"]
    assert rc is None


@pytest.mark.asyncio
async def test_clone_helper_returns_none_when_all_layers_empty(monkeypatch):
    """ctx + tags + default_repos 全空 → (None, None)，caller 跳过。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())

    repos, rc = await _clone.clone_involved_repos_into_runner(
        "REQ-X", ctx={"intent_title": "no repos anywhere"},
        tags=["analyze"], default_repos=[],
    )
    assert repos is None
    assert rc is None
    exec_fn.assert_not_awaited()


def test_resolve_repos_priority_order():
    """L1 ctx.intake_finalized_intent > L2 ctx.involved_repos > L3 tags.repo > L4 default。"""
    # L1 wins
    repos, src = _clone.resolve_repos(
        {"intake_finalized_intent": {"involved_repos": ["L1/x"]},
         "involved_repos": ["L2/x"]},
        tags=["repo:L3/x"],
        default_repos=["L4/x"],
    )
    assert repos == ["L1/x"]
    assert src == "ctx.intake_finalized_intent.involved_repos"

    # L1 missing → L2 wins
    repos, src = _clone.resolve_repos(
        {"involved_repos": ["L2/x"]},
        tags=["repo:L3/x"],
        default_repos=["L4/x"],
    )
    assert repos == ["L2/x"]
    assert src == "ctx.involved_repos"

    # L1+L2 missing → L3 (tags) wins
    repos, src = _clone.resolve_repos(
        {"intent_title": "no involved"},
        tags=["repo:L3/x", "analyze"],
        default_repos=["L4/x"],
    )
    assert repos == ["L3/x"]
    assert src == "tags.repo"

    # L1+L2+L3 missing → L4 (settings.default) wins
    repos, src = _clone.resolve_repos(
        {}, tags=["analyze"], default_repos=["L4/x", "L4/y"],
    )
    assert repos == ["L4/x", "L4/y"]
    assert src == "settings.default_involved_repos"

    # all empty → ([], "none")
    repos, src = _clone.resolve_repos({}, tags=[], default_repos=[])
    assert repos == []
    assert src == "none"


def test_resolve_repos_skips_empty_layers():
    """空 list / 非 list / 含非字符串项的 layer 视作 miss，继续往下找。"""
    # L1 是空 list → fall through to L2
    repos, src = _clone.resolve_repos(
        {"intake_finalized_intent": {"involved_repos": []},
         "involved_repos": ["fallback/wins"]},
    )
    assert repos == ["fallback/wins"]
    assert src == "ctx.involved_repos"

    # L1 非 list（agent 写错 schema） → fall through
    repos, src = _clone.resolve_repos(
        {"intake_finalized_intent": {"involved_repos": "not-a-list"},
         "involved_repos": ["fallback/wins"]},
    )
    assert repos == ["fallback/wins"]
    assert src == "ctx.involved_repos"


def test_extract_repo_tags_validates_slug():
    """`repo:<org>/<name>` 形式合法的 tag 才算数；非法 slug + 非 repo: 前缀全过滤。"""
    tags = [
        "analyze", "REQ-X", "round-2",                # 不是 repo: 前缀
        "repo:phona/sisyphus",                        # OK
        "repo:Zone-Ease_Tech/foo",                    # OK（org/repo 字符集）
        "repo:invalid org/name",                      # 非法 slug（含空格）
        "repo:/missing-org",                          # 非法 slug
        "repo:no-slash-here",                         # 非法 slug
        "repo:phona/sisyphus",                        # 重复，去重
        "repo:phona/repo-with.dots_and-dash",         # OK
    ]
    out = _clone._extract_repo_tags(tags)
    assert out == [
        "phona/sisyphus",
        "phona/repo-with.dots_and-dash",
    ]


def test_extract_repo_tags_handles_none_and_non_string():
    assert _clone._extract_repo_tags(None) == []
    assert _clone._extract_repo_tags([]) == []
    # 非字符串项跳过，不抛
    assert _clone._extract_repo_tags(["repo:phona/x", 42, None, "repo:phona/y"]) == [
        "phona/x", "phona/y",
    ]


@pytest.mark.asyncio
async def test_start_analyze_passes_repo_tags_and_default_to_clone(monkeypatch):
    """直接 analyze 入口：ctx 没 involved，但 tags 带 `repo:`，sisyphus 替 clone。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, _ = _patch_bkd_client(monkeypatch, target_module=start_analyze)

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze", "repo:phona/sisyphus"],
        ctx={"intent_title": "direct analyze entry"},
    )
    exec_fn.assert_awaited_once()
    cmd = exec_fn.await_args.args[1]
    assert "phona/sisyphus" in cmd
    follow_up.assert_awaited_once()
    assert rv["cloned_repos"] == ["phona/sisyphus"]
    assert "emit" not in rv


@pytest.mark.asyncio
async def test_start_analyze_uses_settings_default_when_no_ctx_no_tags(monkeypatch):
    """直接 analyze 入口：ctx 空 + tags 没 repo:，settings.default_involved_repos 兜底。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    _patch_bkd_client(monkeypatch, target_module=start_analyze)
    monkeypatch.setattr(start_analyze.settings, "default_involved_repos",
                        ["phona/sisyphus"])

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze"],
        ctx={"intent_title": "single-repo dogfood"},
    )
    exec_fn.assert_awaited_once()
    cmd = exec_fn.await_args.args[1]
    assert "phona/sisyphus" in cmd
    assert rv["cloned_repos"] == ["phona/sisyphus"]


@pytest.mark.asyncio
async def test_start_analyze_skip_remains_when_all_layers_empty(monkeypatch):
    """ctx + tags + settings.default 全空 → 沿用旧 fallback，不调 helper，agent 自己 clone。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, _ = _patch_bkd_client(monkeypatch, target_module=start_analyze)
    monkeypatch.setattr(start_analyze.settings, "default_involved_repos", [])

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze"],
        ctx={"intent_title": "no repos anywhere"},
    )
    exec_fn.assert_not_awaited()
    follow_up.assert_awaited_once()
    assert rv["cloned_repos"] is None


# ── REQ-ux-tags-injection-1777257283: hint tag 转发 ───────────────────────


@pytest.mark.asyncio
async def test_start_analyze_forwards_user_hint_tags(monkeypatch):
    """tags 含 repo: + ux: → PATCH 的 tags kwarg 把它们追加到 ['analyze', req_id]。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    _, update_issue, _ = _patch_bkd_client(
        monkeypatch, target_module=start_analyze,
    )

    await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze", "repo:phona/sisyphus", "ux:fast-track"],
        ctx={"intent_title": "with hint tags"},
    )

    # 第一次 update_issue = rename + tags（第二次是 status_id=working）
    _, kwargs = update_issue.call_args_list[0]
    tags = kwargs["tags"]
    assert tags == ["analyze", "REQ-X", "repo:phona/sisyphus", "ux:fast-track"]


@pytest.mark.asyncio
async def test_start_analyze_strips_sisyphus_managed_tags(monkeypatch):
    """stale intent:* / result:* / pr:* 不被转发到 PATCH tags。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    _, update_issue, _ = _patch_bkd_client(
        monkeypatch, target_module=start_analyze,
    )

    await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=[
            "intent:analyze", "REQ-X", "analyze", "result:pass",
            "pr:phona/foo#1", "repo:phona/foo", "ux:fast-track",
        ],
        ctx={},
    )
    _, kwargs = update_issue.call_args_list[0]
    tags = kwargs["tags"]
    assert tags == ["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"]
    # 不重复 / 不混入 managed
    assert tags.count("analyze") == 1
    assert tags.count("REQ-X") == 1
    assert "intent:analyze" not in tags
    assert "result:pass" not in tags
    assert "pr:phona/foo#1" not in tags


@pytest.mark.asyncio
async def test_start_analyze_no_hint_tags_keeps_base_only(monkeypatch):
    """tags 全是 sisyphus-managed → PATCH 的 tags 只剩 ['analyze', req_id]，向后兼容。"""
    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    _, update_issue, _ = _patch_bkd_client(
        monkeypatch, target_module=start_analyze,
    )

    await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze"],
        ctx={},
    )
    _, kwargs = update_issue.call_args_list[0]
    assert kwargs["tags"] == ["analyze", "REQ-X"]


# ── REQ-orch-rate-limit-1777202974: admission gate ────────────────────────


@pytest.mark.asyncio
async def test_start_analyze_admission_denied_emits_escalate(monkeypatch):
    """admission deny → emit VERIFY_ESCALATE，不调 ensure_runner / clone / BKD。"""
    monkeypatch.setattr(
        start_analyze, "check_admission",
        AsyncMock(return_value=AdmissionDecision(
            admit=False, reason="inflight-cap-exceeded:10/10",
        )),
    )
    update_ctx = AsyncMock()
    monkeypatch.setattr(start_analyze.req_state, "update_context", update_ctx)

    exec_fn = AsyncMock(return_value=FakeExec(exit_code=0))
    fake_rc = _patch_runner(monkeypatch, exec_fn=exec_fn)
    follow_up, _, _ = _patch_bkd_client(monkeypatch, target_module=start_analyze)

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X",
        tags=["intent:analyze"], ctx={},
    )

    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    assert "admission denied" in rv["reason"]
    assert "inflight-cap-exceeded" in rv["reason"]
    # ctx.escalated_reason 必须落 ctx
    update_ctx.assert_awaited_once()
    patch = update_ctx.await_args.args[2]
    assert patch["escalated_reason"] == "rate-limit:inflight-cap-exceeded:10/10"
    # 不能花 runner / clone / agent 的成本
    fake_rc.ensure_runner.assert_not_awaited()
    exec_fn.assert_not_awaited()
    follow_up.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_analyze_admission_disk_pressure_escalates(monkeypatch):
    """disk-pressure 拒绝同样 escalate；reason 标 disk-pressure。"""
    monkeypatch.setattr(
        start_analyze, "check_admission",
        AsyncMock(return_value=AdmissionDecision(
            admit=False, reason="disk-pressure:0.85/0.75",
        )),
    )
    update_ctx = AsyncMock()
    monkeypatch.setattr(start_analyze.req_state, "update_context", update_ctx)
    exec_fn = AsyncMock()
    _patch_runner(monkeypatch, exec_fn=exec_fn)
    _patch_bkd_client(monkeypatch, target_module=start_analyze)

    rv = await start_analyze.start_analyze(
        body=_make_body(), req_id="REQ-X", tags=[], ctx={},
    )

    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    assert "disk-pressure" in rv["reason"]
    patch = update_ctx.await_args.args[2]
    assert "disk-pressure" in patch["escalated_reason"]
