"""Adversarial mock tests for engine.step (REQ-engine-adversarial-tests-v2-1777256408).

Goal: feed engine.step奇形输入 / 反常 handler 返回 / 死路径，确认它不崩 / 不污染状态。
全部用 in-process FakePool + stub action，**不打 BKD / Postgres / K8s**。

EAT-S1..S12 一一对应 spec scenario，见
`openspec/changes/REQ-engine-adversarial-tests-v2-1777256408/specs/engine-adversarial-tests/spec.md`。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# 复用 test_engine.py 里的 FakePool / FakeReq —— 它们就是 engine adversarial 测的
# 标准 stub。抄一份会跟 test_engine 漂移。pytest rootdir-based collection 把
# tests/ 下的模块拍平到 sys.path，绝对 import 即可。
from test_engine import FakePool, FakeReq, _drain_tasks  # type: ignore[import-not-found]

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState


# ─── 共享 stub_actions fixture（独立于 test_engine 的同名 fixture，作用域局部） ──
@pytest.fixture
def stub_actions():
    """clear REGISTRY / ACTION_META，测后还原。"""
    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


def _body(**attrs):
    """构造一个 minimal body 对象。"""
    return type("B", (), attrs)()


# ───────────────────────────────────────────────────────────────────────
# EAT-S1：handler 返 emit garbage
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s1_unknown_emit_string_dropped(stub_actions, caplog):
    """Spec EAT-S1: handler emit 不在 Event 枚举的字符串 → 不抛 + 不 chain。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        return {"emit": "totally-not-an-event"}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    body = _body(issueId="x", projectId="p", event="check.passed")
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )

    assert result["action"] == "start_challenger"
    assert result["next_state"] == ReqState.CHALLENGER_RUNNING.value
    assert "chained" not in result, "无效 emit 不应触发 chain"


# ───────────────────────────────────────────────────────────────────────
# EAT-S2 / S3：handler 返非 dict
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s2_handler_returns_none(stub_actions):
    """Spec EAT-S2: handler 返 None → 视作空 dict。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        return None

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "start_challenger"
    assert result["next_state"] == ReqState.CHALLENGER_RUNNING.value
    assert result["result"] == {}, "None 应被规整成 empty dict"
    assert "chained" not in result


@pytest.mark.asyncio
async def test_eat_s3_handler_returns_list(stub_actions):
    """Spec EAT-S3: handler 返 list → 视作空 dict（不挂在 result.get）。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        return [1, 2, 3]

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "start_challenger"
    assert "chained" not in result


# ───────────────────────────────────────────────────────────────────────
# EAT-S4：chain 中段 row 消失
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s4_row_vanishes_mid_chain(stub_actions):
    """Spec EAT-S4: req_state.get 在 chain 中段返 None → 安全早返，不抛 AttributeError。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ANALYZE_ARTIFACT_CHECKING.value)})

    async def create_spec_lint(*, body, req_id, tags, ctx):
        # 模拟 row 在 dispatch 后被删（管理员清理 / DB reset / 测试夹具）
        pool.rows.pop(req_id, None)
        return {"emit": Event.SPEC_LINT_PASS.value}

    stub_actions["create_spec_lint"] = create_spec_lint

    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ANALYZE_ARTIFACT_CHECKING, ctx={},
        event=Event.ANALYZE_ARTIFACT_CHECK_PASS,
    )
    assert result["action"] == "create_spec_lint"
    assert "chained" not in result, "row 没了，chain 应早返而不是再 step"


# ───────────────────────────────────────────────────────────────────────
# EAT-S5：chained emit 命中 illegal transition
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s5_chained_illegal_transition(stub_actions):
    """Spec EAT-S5: handler emit 一个该 state 没注册的 event → chain 子 step 返 skip。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        # CHALLENGER_RUNNING + ARCHIVE_DONE 没有 transition
        return {"emit": Event.ARCHIVE_DONE.value}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "start_challenger"
    assert "chained" in result
    assert result["chained"]["action"] == "skip"
    assert "no transition challenger-running+archive.done" in result["chained"]["reason"]


# ───────────────────────────────────────────────────────────────────────
# EAT-S6：action 不在 REGISTRY
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s6_unregistered_action_returns_error(stub_actions):
    """Spec EAT-S6: transition.action 名在表里但 REGISTRY 没注册 → return error，不抛。"""
    # stub_actions 已经清空了 REGISTRY；不再注册 start_challenger
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "error"
    assert result["reason"] == "action start_challenger not registered"
    # CAS 已经成功 —— 行已推到 challenger-running（engine 不回滚）
    assert pool.rows["REQ-1"].state == ReqState.CHALLENGER_RUNNING.value


# ───────────────────────────────────────────────────────────────────────
# EAT-S7：terminal self-loop 不再 cleanup
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_runner_controller():
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_eat_s7_terminal_self_loop_no_cleanup(stub_actions, mock_runner_controller):
    """Spec EAT-S7: ESCALATED + VERIFY_ESCALATE 是 self-loop, action=None；不应再次 cleanup。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    body = _body(issueId="x", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ESCALATED, ctx={}, event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "no-op"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    # 关键：cur 已 terminal → engine 跳过 cleanup
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# EAT-S8：stage_runs INSERT 抛异常
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s8_stage_runs_insert_failure_does_not_break_engine(stub_actions):
    """Spec EAT-S8: stage_runs DB 写挂 → engine 仍推进 + 不抛。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        return {"ok": True}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})

    # monkey-patch fetchrow：INSERT INTO stage_runs 抛错
    orig_fetchrow = pool.fetchrow

    async def evil_fetchrow(sql, *args):
        if sql.strip().startswith("INSERT INTO stage_runs"):
            raise RuntimeError("DB down (simulated)")
        return await orig_fetchrow(sql, *args)

    pool.fetchrow = evil_fetchrow  # type: ignore[method-assign]

    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "start_challenger"
    assert pool.rows["REQ-1"].state == ReqState.CHALLENGER_RUNNING.value


# ───────────────────────────────────────────────────────────────────────
# EAT-S9：body 缺 issueId 属性
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s9_body_without_issue_id(stub_actions):
    """Spec EAT-S9: body 没 issueId 属性 → 用 getattr 默认 None，不抛。"""
    async def start_challenger(*, body, req_id, tags, ctx):
        return {"ok": True}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    body = _body()  # 完全空对象

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    assert result["action"] == "start_challenger"
    assert result["next_state"] == ReqState.CHALLENGER_RUNNING.value


# ───────────────────────────────────────────────────────────────────────
# EAT-S10a/b：recursion guard 边界
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s10a_depth_12_still_dispatches(stub_actions):
    """Spec EAT-S10a: depth=12 是合法边界，handler 仍执行。"""
    called = {"n": 0}

    async def start_challenger(*, body, req_id, tags, ctx):
        called["n"] += 1
        return {"ok": True}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
        depth=12,
    )
    assert called["n"] == 1
    assert result["action"] == "start_challenger"


@pytest.mark.asyncio
async def test_eat_s10b_depth_13_recursion_guard(stub_actions):
    """Spec EAT-S10b: depth=13 触发 recursion guard，handler 不被调。"""
    called = {"n": 0}

    async def start_challenger(*, body, req_id, tags, ctx):
        called["n"] += 1
        return {"ok": True}

    stub_actions["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
        depth=13,
    )
    assert called["n"] == 0, "depth>12 不应再 dispatch"
    assert result == {"action": "error", "reason": "engine recursion >12"}


# ───────────────────────────────────────────────────────────────────────
# EAT-S11：SESSION_FAILED 在 terminal state DONE → skip
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s11_session_failed_on_done_skips(stub_actions):
    """Spec EAT-S11: terminal DONE 没 SESSION_FAILED transition → skip。"""
    escalate_called = {"n": 0}

    async def escalate(*, body, req_id, tags, ctx):
        escalate_called["n"] += 1
        return {"ok": True}

    stub_actions["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DONE.value)})
    result = await engine.step(
        pool, body=_body(issueId="x", projectId="p"),
        req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.DONE, ctx={}, event=Event.SESSION_FAILED,
    )
    assert result["action"] == "skip"
    assert result["reason"].startswith("no transition done+")
    assert escalate_called["n"] == 0
    # 状态不动
    assert pool.rows["REQ-1"].state == ReqState.DONE.value


# ───────────────────────────────────────────────────────────────────────
# EAT-S12：DONE 终态对所有 Event 都 skip
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eat_s12_done_skips_every_event(stub_actions):
    """Spec EAT-S12: 穷举 Event 枚举，DONE 状态下每个都 skip 不抛。"""
    handler_calls = {"n": 0}

    async def any_action(*, body, req_id, tags, ctx):
        handler_calls["n"] += 1
        return {"ok": True}

    # 把每个可能的 action 都注册成 any_action（如果有 transition 漏到 DONE）
    for name in [
        "start_intake", "start_analyze", "start_analyze_with_finalized_intent",
        "create_analyze_artifact_check", "create_spec_lint", "start_challenger",
        "create_dev_cross_check", "create_staging_test", "create_pr_ci_watch",
        "create_accept", "teardown_accept_env", "done_archive", "escalate",
        "apply_verify_pass", "start_fixer", "invoke_verifier_after_fix",
        "invoke_verifier_for_analyze_artifact_check_fail",
        "invoke_verifier_for_spec_lint_fail",
        "invoke_verifier_for_challenger_fail",
        "invoke_verifier_for_dev_cross_check_fail",
        "invoke_verifier_for_staging_test_fail",
        "invoke_verifier_for_pr_ci_fail",
        "invoke_verifier_for_accept_fail",
    ]:
        stub_actions[name] = any_action

    for ev in Event:
        pool = FakePool({"REQ-1": FakeReq(state=ReqState.DONE.value)})
        result = await engine.step(
            pool, body=_body(issueId="x", projectId="p"),
            req_id="REQ-1", project_id="p", tags=[],
            cur_state=ReqState.DONE, ctx={}, event=ev,
        )
        assert result["action"] == "skip", f"{ev.value} should skip on DONE"
        assert pool.rows["REQ-1"].state == ReqState.DONE.value

    assert handler_calls["n"] == 0, "DONE 下不应有任何 handler 被调"
