"""Mock tests for engine.step on verifier sub-chain + SESSION_FAILED fallback
(REQ-test-verifier-loop-1777267725).

Goal: pin down the routing decisions in `state.TRANSITIONS` for the two
"事故响应路径" — verifier loop (entry / decision out / fixer back-edge /
ESCALATED resume) and SESSION_FAILED self-loop on every in-flight state —
so that a regression in the transition table is caught by mock tests
**without** booting BKD / Postgres / K8s.

Stubs replace every action with a recorder; the engine's own
`_record_stage_transitions` and terminal-cleanup branches still run because
they only depend on FakePool / k8s_runner.set_controller.

VLT-S1..S16 一一对应 spec scenario, see
`openspec/changes/REQ-test-verifier-loop-1777267725/specs/engine-verifier-loop-tests/spec.md`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# 复用 test_engine.py 的 FakePool / FakeReq / _drain_tasks，
# 跟 test_engine_adversarial.py 同套路（pytest rootdir 把 tests/ 拍平到 sys.path）。
from test_engine import FakePool, FakeReq, _drain_tasks  # type: ignore[import-not-found]

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState

# ─── 共享 fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def stub_actions():
    """Save+clear REGISTRY / ACTION_META; restore on teardown."""
    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


@pytest.fixture
def mock_runner_controller():
    """Inject a fake k8s controller; assert cleanup_runner calls."""
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _body(**attrs):
    return type("B", (), attrs)()


def _make_recorder(name: str, calls: list):
    async def _rec(*, body, req_id, tags, ctx):
        calls.append({"action": name, "req_id": req_id, "tags": list(tags or [])})
        return {"ok": True}
    return _rec


# ───────────────────────────────────────────────────────────────────────
# VLT-S1..S7: 上游 *_FAIL → REVIEW_RUNNING + invoke_verifier_for_<stage>_fail
# ───────────────────────────────────────────────────────────────────────


_ENTRY_CASES = [
    pytest.param(
        ReqState.SPEC_LINT_RUNNING, Event.SPEC_LINT_FAIL,
        "invoke_verifier_for_spec_lint_fail",
        id="VLT-S1-spec_lint_fail",
    ),
    pytest.param(
        ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_FAIL,
        "invoke_verifier_for_dev_cross_check_fail",
        id="VLT-S2-dev_cross_check_fail",
    ),
    pytest.param(
        ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL,
        "invoke_verifier_for_staging_test_fail",
        id="VLT-S3-staging_test_fail",
    ),
    pytest.param(
        ReqState.PR_CI_RUNNING, Event.PR_CI_FAIL,
        "invoke_verifier_for_pr_ci_fail",
        id="VLT-S4-pr_ci_fail",
    ),
    pytest.param(
        ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_FAIL,
        "invoke_verifier_for_accept_fail",
        id="VLT-S5-accept_teardown_fail",
    ),
    pytest.param(
        ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_FAIL,
        "invoke_verifier_for_analyze_artifact_check_fail",
        id="VLT-S6-analyze_artifact_check_fail",
    ),
    pytest.param(
        ReqState.CHALLENGER_RUNNING, Event.CHALLENGER_FAIL,
        "invoke_verifier_for_challenger_fail",
        id="VLT-S7-challenger_fail",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cur_state,event,expected_action", _ENTRY_CASES)
async def test_vlt_s1_to_s7_upstream_fail_enters_review_running(
    stub_actions, cur_state, event, expected_action,
):
    """Spec VLT-S1..S7: 每个上游 *_FAIL 都从对应 *_RUNNING 路由到 REVIEW_RUNNING +
    dispatch 该 stage 专用 invoke_verifier_for_<stage>_fail action。"""
    calls: list = []
    stub_actions[expected_action] = _make_recorder(expected_action, calls)

    pool = FakePool({"REQ-1": FakeReq(state=cur_state.value)})
    body = _body(issueId="src-1", projectId="p", event="check.failed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=cur_state, ctx={}, event=event,
    )

    assert result["action"] == expected_action
    assert result["next_state"] == ReqState.REVIEW_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert len(calls) == 1, f"{expected_action} should be awaited exactly once"
    assert calls[0]["action"] == expected_action


# ───────────────────────────────────────────────────────────────────────
# VLT-S8: REVIEW_RUNNING + VERIFY_FIX_NEEDED → FIXER_RUNNING + stage_runs roll
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s8_verify_fix_needed_enters_fixer_running(stub_actions):
    """Spec VLT-S8: REVIEW_RUNNING + VERIFY_FIX_NEEDED → FIXER_RUNNING via start_fixer。
    跨 *_RUNNING state → engine 必须 close verifier(outcome=fix) + open fixer。"""
    calls: list = []
    stub_actions["start_fixer"] = _make_recorder("start_fixer", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = _body(issueId="vfy-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:dev_cross_check"],
        cur_state=ReqState.REVIEW_RUNNING,
        ctx={"verifier_stage": "dev_cross_check"},
        event=Event.VERIFY_FIX_NEEDED,
    )

    assert result["action"] == "start_fixer"
    assert result["next_state"] == ReqState.FIXER_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.FIXER_RUNNING.value

    closes = [c for c in pool.stage_runs_calls if c[0] == "close"]
    inserts = [c for c in pool.stage_runs_calls if c[0] == "insert"]
    assert len(closes) == 1, f"expected exactly 1 close, got {pool.stage_runs_calls!r}"
    close_args = closes[0][2]
    # close_latest_stage_run signature: (req_id, stage, outcome, fail_reason)
    assert close_args[1] == "verifier"
    assert close_args[2] == "fix"
    assert len(inserts) == 1, f"expected exactly 1 insert, got {pool.stage_runs_calls!r}"
    insert_args = inserts[0][2]
    # insert_stage_run signature: (req_id, stage, agent_type, ...)
    assert insert_args[1] == "fixer"


# ───────────────────────────────────────────────────────────────────────
# VLT-S9: REVIEW_RUNNING + VERIFY_ESCALATE → ESCALATED + cleanup
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s9_verify_escalate_enters_escalated_with_cleanup(
    stub_actions, mock_runner_controller,
):
    """Spec VLT-S9: REVIEW_RUNNING + VERIFY_ESCALATE → ESCALATED via escalate；
    进 terminal state（cur 非 terminal）→ engine 触发 fire-and-forget cleanup_runner。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = _body(issueId="vfy-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1"],
        cur_state=ReqState.REVIEW_RUNNING, ctx={},
        event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# VLT-S10: FIXER_RUNNING + FIXER_DONE → REVIEW_RUNNING (re-verify back-edge)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s10_fixer_done_returns_to_review_running(stub_actions):
    """Spec VLT-S10: FIXER_RUNNING + FIXER_DONE → REVIEW_RUNNING via
    invoke_verifier_after_fix。stage_runs：fixer close outcome=pass + verifier insert。"""
    calls: list = []
    stub_actions["invoke_verifier_after_fix"] = _make_recorder(
        "invoke_verifier_after_fix", calls,
    )

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.FIXER_RUNNING.value)})
    body = _body(issueId="fix-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["fixer", "REQ-1", "parent-stage:dev_cross_check"],
        cur_state=ReqState.FIXER_RUNNING,
        ctx={"fixer_role": "dev"},
        event=Event.FIXER_DONE,
    )

    assert result["action"] == "invoke_verifier_after_fix"
    assert result["next_state"] == ReqState.REVIEW_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert len(calls) == 1

    closes = [c for c in pool.stage_runs_calls if c[0] == "close"]
    inserts = [c for c in pool.stage_runs_calls if c[0] == "insert"]
    assert len(closes) == 1, pool.stage_runs_calls
    close_args = closes[0][2]
    assert close_args[1] == "fixer"
    assert close_args[2] == "pass"
    assert len(inserts) == 1
    assert inserts[0][2][1] == "verifier"


# ───────────────────────────────────────────────────────────────────────
# VLT-S11: FIXER_RUNNING + VERIFY_ESCALATE → ESCALATED (round cap escape)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s11_fixer_round_cap_escapes_to_escalated(
    stub_actions, mock_runner_controller,
):
    """Spec VLT-S11: FIXER_RUNNING + VERIFY_ESCALATE → ESCALATED via escalate。
    用于 start_fixer 自检 round cap 击顶时 emit VERIFY_ESCALATE 的兜底 transition。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.FIXER_RUNNING.value)})
    body = _body(issueId="fix-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["fixer", "REQ-1"],
        cur_state=ReqState.FIXER_RUNNING, ctx={"fixer_round": 5},
        event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# VLT-S12: ESCALATED + VERIFY_PASS resume (apply_verify_pass self-loop)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s12_escalated_verify_pass_dispatches_apply(
    stub_actions, mock_runner_controller,
):
    """Spec VLT-S12: ESCALATED + VERIFY_PASS → ESCALATED self-loop, action=apply_verify_pass。
    用户在 BKD UI follow-up escalate 的 verifier issue 写新 decision=pass 触发；
    engine 仅 dispatch action（实际 CAS 推下游 state 是 action 内部职责）。
    自循环且 cur 已 terminal → engine 不应再触发 cleanup。"""
    calls: list = []
    stub_actions["apply_verify_pass"] = _make_recorder("apply_verify_pass", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    body = _body(issueId="vfy-resume", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:pr_ci"],
        cur_state=ReqState.ESCALATED, ctx={"verifier_stage": "pr_ci"},
        event=Event.VERIFY_PASS,
    )
    await _drain_tasks()

    assert result["action"] == "apply_verify_pass"
    # transition 表声明的 self-loop：state 不动（stub 不内部 CAS）
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    # cur 已 terminal → engine 跳过 cleanup
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# VLT-S13: ESCALATED + VERIFY_FIX_NEEDED resume
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s13_escalated_verify_fix_needed_enters_fixer_running(stub_actions):
    """Spec VLT-S13: ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING via start_fixer。
    人工 resume 路径：从 ESCALATED 起 fixer，复用主链 start_fixer。"""
    calls: list = []
    stub_actions["start_fixer"] = _make_recorder("start_fixer", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    body = _body(issueId="vfy-resume", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:staging_test"],
        cur_state=ReqState.ESCALATED,
        ctx={"verifier_stage": "staging_test", "verifier_fixer": "dev"},
        event=Event.VERIFY_FIX_NEEDED,
    )

    assert result["action"] == "start_fixer"
    assert result["next_state"] == ReqState.FIXER_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.FIXER_RUNNING.value
    assert len(calls) == 1


# ───────────────────────────────────────────────────────────────────────
# VLT-S14: ESCALATED + VERIFY_ESCALATE no-op self-loop
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s14_escalated_verify_escalate_is_no_op(
    stub_actions, mock_runner_controller,
):
    """Spec VLT-S14: ESCALATED + VERIFY_ESCALATE → ESCALATED self-loop, action=None。
    用户续了 verifier 但仍判 escalate → 留原地。terminal self-loop **不**重复 cleanup。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    body = _body(issueId="vfy-resume", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1"],
        cur_state=ReqState.ESCALATED, ctx={},
        event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "no-op"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# VLT-S15: SESSION_FAILED self-loop on every *_RUNNING state
# ───────────────────────────────────────────────────────────────────────


_SESSION_FAILED_STATES = [
    pytest.param(s, id=f"VLT-S15-{s.value}")
    for s in [
        ReqState.INTAKING, ReqState.ANALYZING,
        ReqState.ANALYZE_ARTIFACT_CHECKING,
        ReqState.SPEC_LINT_RUNNING, ReqState.CHALLENGER_RUNNING,
        ReqState.DEV_CROSS_CHECK_RUNNING,
        ReqState.STAGING_TEST_RUNNING, ReqState.PR_CI_RUNNING,
        ReqState.ACCEPT_RUNNING, ReqState.ACCEPT_TEARING_DOWN,
        ReqState.REVIEW_RUNNING, ReqState.FIXER_RUNNING,
        ReqState.ARCHIVING,
    ]
]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _SESSION_FAILED_STATES)
async def test_vlt_s15_session_failed_self_loops_to_escalate(
    stub_actions, mock_runner_controller, state,
):
    """Spec VLT-S15: 13 个 *_RUNNING state 每个 + SESSION_FAILED → 自循环 + dispatch
    escalate action。state 不动（self-loop；stub 不 CAS），escalate 内部决定真 ESCALATE。
    自循环 cur=next 都非 terminal → engine 也不触发 cleanup。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=state.value)})
    body = _body(issueId="x", projectId="p", event="session.failed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=state, ctx={}, event=Event.SESSION_FAILED,
    )
    await _drain_tasks()

    assert result["action"] == "escalate", f"{state.value} should dispatch escalate"
    assert result["next_state"] == state.value, "SESSION_FAILED is a self-loop"
    assert pool.rows["REQ-1"].state == state.value
    assert len(calls) == 1
    # cur=next 非 terminal → 不 cleanup
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# VLT-S16: INIT + SESSION_FAILED → skip (INIT not in SESSION_FAILED set)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vlt_s16_session_failed_on_init_is_dropped(stub_actions):
    """Spec VLT-S16: INIT 不在 SESSION_FAILED transition 集合（只有 *_RUNNING + INTAKING
    + ARCHIVING 在）→ engine 应返 skip 而不是误进 escalate。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = _body(issueId="x", projectId="p", event="session.failed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.INIT, ctx={}, event=Event.SESSION_FAILED,
    )

    assert result["action"] == "skip"
    assert "no transition init+session.failed" in result["reason"]
    assert pool.rows["REQ-1"].state == ReqState.INIT.value
    assert calls == [], "escalate must NOT be dispatched on INIT + SESSION_FAILED"


# ───────────────────────────────────────────────────────────────────────
# VFR-S1 (REQ-428): REVIEW_RUNNING + VERIFY_INFRA_RETRY → self-loop + apply_verify_infra_retry
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vfr_s1_verify_infra_retry_dispatches_apply(stub_actions):
    """Spec VFR-S1: REVIEW_RUNNING + VERIFY_INFRA_RETRY → REVIEW_RUNNING self-loop,
    action=apply_verify_infra_retry。infra-flake 路径，action 内部决定是否真 CAS 推 stage。"""
    calls: list = []
    stub_actions["apply_verify_infra_retry"] = _make_recorder("apply_verify_infra_retry", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = _body(issueId="vfy-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:staging_test"],
        cur_state=ReqState.REVIEW_RUNNING,
        ctx={"verifier_stage": "staging_test", "infra_retry_count": 0},
        event=Event.VERIFY_INFRA_RETRY,
    )

    assert result["action"] == "apply_verify_infra_retry"
    # transition 表声明 self-loop（stub 不 CAS；action 内部实际会 CAS 到 staging_test_running）
    assert result["next_state"] == ReqState.REVIEW_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert len(calls) == 1, "apply_verify_infra_retry must be dispatched once"
