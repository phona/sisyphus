"""Contract tests for #247 Phase 1: PENDING_USER_REVIEW resume via stage-issue follow-up.

Black-box contracts derived from issue #247 + 2026-05-01 clarification comments:

  PUR-S1  (PENDING_USER_REVIEW, EXECUTE_DONE) → EXECUTE_ARTIFACT_CHECKING
                                              + create_execute_artifact_check
  PUR-S2  (PENDING_USER_REVIEW, CHALLENGER_PASS) → DEV_CROSS_CHECK_RUNNING
                                                + create_dev_cross_check
  PUR-S3  (PENDING_USER_REVIEW, STAGING_TEST_PASS) → PR_CI_RUNNING
                                                  + create_pr_ci_watch
  PUR-S4  (PENDING_USER_REVIEW, PR_CI_PASS) → ACCEPT_RUNNING + create_accept
  PUR-S5  (PENDING_USER_REVIEW, ACCEPT_PASS) → ACCEPT_TEARING_DOWN
                                            + teardown_accept_env
  PUR-S6  parametric sweep: each declared PENDING_USER_REVIEW resume entry
          shares the *same* (next_state, action) as the corresponding main-chain
          (src_state, ev) — proves the dict-reference design (zero drift)
  PUR-S7  *_FAIL events MUST NOT have PENDING_USER_REVIEW resume transitions
          (boundary: failures route through ESCALATED, not PENDING_USER_REVIEW)
  PUR-S8  PENDING_USER_REVIEW happy-path exits remain unchanged
          (USER_REVIEW_PASS → DONE; USER_REVIEW_FIX → ESCALATED; PR_MERGED → DONE)

Module contract under test: orchestrator.engine.step + orchestrator.state.TRANSITIONS
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

_POOL = object()
_REQ_ID = "REQ-247-pending-user-review-resume"
_PROJECT = "proj-pur"


class _Body:
    issueId = "bkd-pur-test"
    projectId = _PROJECT


def _patch_io(monkeypatch, *, cas_return: bool = True) -> AsyncMock:
    from orchestrator import engine

    cas = AsyncMock(return_value=cas_return)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)
    monkeypatch.setattr(engine.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
    monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())
    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())
    return cas


@pytest.fixture(autouse=True)
def _restore_registry():
    from orchestrator.actions import ACTION_META, REGISTRY

    snap_reg = dict(REGISTRY)
    snap_meta = dict(ACTION_META)
    yield
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(snap_reg)
    ACTION_META.update(snap_meta)


async def _step(**overrides):
    from orchestrator.engine import step
    from orchestrator.state import Event, ReqState

    defaults: dict = dict(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=[_REQ_ID],
        cur_state=ReqState.PENDING_USER_REVIEW,
        ctx={},
        event=Event.EXECUTE_DONE,
        depth=0,
    )
    defaults.update(overrides)
    return await step(**defaults)


_RESUME_CASES = [
    pytest.param(
        "EXECUTE_DONE", "create_execute_artifact_check",
        "EXECUTE_ARTIFACT_CHECKING",
        id="PUR-S1-analyze_done",
    ),
    pytest.param(
        "CHALLENGER_PASS", "create_dev_cross_check",
        "DEV_CROSS_CHECK_RUNNING",
        id="PUR-S2-challenger_pass",
    ),
    pytest.param(
        "STAGING_TEST_PASS", "create_pr_ci_watch",
        "PR_CI_RUNNING",
        id="PUR-S3-staging_test_pass",
    ),
    pytest.param(
        "PR_CI_PASS", "create_accept",
        "ACCEPT_RUNNING",
        id="PUR-S4-pr_ci_pass",
    ),
    pytest.param(
        "ACCEPT_PASS", "teardown_accept_env",
        "ACCEPT_TEARING_DOWN",
        id="PUR-S5-accept_pass",
    ),
]


@pytest.mark.parametrize("event_name,expected_action,expected_next", _RESUME_CASES)
async def test_pur_s1_to_s5_main_chain_pass_resumes_from_pending_user_review(
    monkeypatch, event_name, expected_action, expected_next,
) -> None:
    """PUR-S1..S5: 在 PENDING_USER_REVIEW 收到主链 *_PASS 事件，必须复用主链
    transition 推到对应 next_state + dispatch 同一份主链 action。

    用户场景：accept 完进 PENDING_USER_REVIEW，看到 PR 觉得"再调一下"，在 analyze /
    challenger / accept BKD issue 续 follow-up，agent 重跑出 result:pass tag → router
    派 *_PASS 主链事件。复用主链 transition = 零新概念，主链改了恢复路径自动跟。
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)
    calls: list[str] = []

    async def _stub(_a=expected_action, **_kw):
        calls.append(_a)
        return {}

    REGISTRY[expected_action] = _stub

    evt = Event[event_name]
    target_state = ReqState[expected_next]

    result = await _step(
        cur_state=ReqState.PENDING_USER_REVIEW, event=evt,
        tags=[_REQ_ID],
    )
    await asyncio.sleep(0)

    tag = f"[PENDING_USER_REVIEW+{event_name}]"
    assert result.get("action") == expected_action, (
        f"{tag}: action MUST be {expected_action!r}; got {result!r}"
    )
    assert result.get("next_state") == target_state.value, (
        f"{tag}: next_state MUST be {target_state.value!r}; got {result!r}"
    )
    assert len(calls) == 1, (
        f"{tag}: action MUST be dispatched exactly once; got {len(calls)}"
    )
    assert cas.called, f"{tag}: cas_transition MUST have been called"
    cas_next = cas.call_args.args[3]
    assert cas_next == target_state, (
        f"{tag}: CAS MUST advance to {target_state!r}; got {cas_next!r}"
    )


def test_pur_s6_resume_transitions_share_main_chain_target() -> None:
    """PUR-S6: 每条 PENDING_USER_REVIEW resume entry 必须跟主链 (src_state, ev) 是
    *同一个 Transition object reference*（不是 value-equal copy）。这证明
    `_PENDING_USER_REVIEW_RESUME_EVENT_SOURCES` 用 `TRANSITIONS[(src, ev)]` 复用而
    非二次定义；主链改了恢复路径自动跟，无漂移可能。
    """
    from orchestrator.state import (
        _PENDING_USER_REVIEW_RESUME_EVENT_SOURCES,
        TRANSITIONS,
        ReqState,
    )

    drift: list[str] = []
    for ev, src in _PENDING_USER_REVIEW_RESUME_EVENT_SOURCES:
        main = TRANSITIONS.get((src, ev))
        resume = TRANSITIONS.get((ReqState.PENDING_USER_REVIEW, ev))
        assert main is not None, (
            f"PUR-S6: main-chain transition ({src.value}, {ev.value}) missing — "
            "resume source list out of sync with state.py"
        )
        assert resume is not None, (
            f"PUR-S6: resume transition (pending_user_review, {ev.value}) not registered"
        )
        if resume is not main:
            drift.append(
                f"({ev.value}): resume Transition is not the same object as "
                f"main-chain ({src.value}, {ev.value})"
            )

    assert not drift, (
        "PUR-S6: detected dict-reference drift — resume transitions MUST "
        "reuse TRANSITIONS[(src, ev)] not redefine. drifted:\n  " + "\n  ".join(drift)
    )


def test_pur_s7_fail_events_have_no_pending_user_review_resume() -> None:
    """PUR-S7: *_FAIL 事件**故意不在** PENDING_USER_REVIEW resume 列表里。
    边界：失败信号走 USER_REVIEW_FIX → ESCALATED，复用 ESCALATED resume（含 *_FAIL
    完整集）。让 PENDING_USER_REVIEW 保持"happy-path 微调等待态"的纯净语义；
    ESCALATED 是"人接管态"。两个稳定态各司其职，不互相污染。

    防回归：避免后续 PR 误把 *_FAIL 加进 PENDING_USER_REVIEW resume 列表。
    """
    from orchestrator.state import TRANSITIONS, Event, ReqState

    fail_events = [
        Event.SPEC_LINT_FAIL, Event.CHALLENGER_FAIL,
        Event.DEV_CROSS_CHECK_FAIL, Event.STAGING_TEST_FAIL,
        Event.PR_CI_FAIL, Event.ACCEPT_FAIL,
        Event.EXECUTE_ARTIFACT_CHECK_FAIL, Event.TEARDOWN_DONE_FAIL,
        Event.SESSION_FAILED, Event.PR_CI_TIMEOUT, Event.ACCEPT_ENV_UP_FAIL,
        Event.VERIFY_ESCALATE, Event.INTAKE_FAIL,
    ]
    leaked: list[str] = []
    for ev in fail_events:
        if (ReqState.PENDING_USER_REVIEW, ev) in TRANSITIONS:
            leaked.append(ev.value)

    assert not leaked, (
        "PUR-S7: fail events leaked into PENDING_USER_REVIEW resume table "
        "— failures MUST go through USER_REVIEW_FIX → ESCALATED → ESCALATED "
        f"resume, not directly. leaked: {leaked!r}"
    )


def test_pur_s8_existing_pending_user_review_exits_unchanged() -> None:
    """PUR-S8: PENDING_USER_REVIEW 原有 4 条出口必须保留语义不变。新增 resume 通道
    不能污染既有的 statusId / PR_MERGED 路径。"""
    from orchestrator.state import TRANSITIONS, Event, ReqState

    expected = {
        Event.USER_REVIEW_PASS: ReqState.DONE,
        Event.USER_REVIEW_FIX: ReqState.ESCALATED,
        Event.PR_MERGED: ReqState.DONE,
    }
    for ev, expected_next in expected.items():
        t = TRANSITIONS.get((ReqState.PENDING_USER_REVIEW, ev))
        assert t is not None, (
            f"PUR-S8: existing exit (pending_user_review, {ev.value}) MUST remain"
        )
        assert t.next_state == expected_next, (
            f"PUR-S8: (pending_user_review, {ev.value}) MUST go to {expected_next.value}; "
            f"got {t.next_state.value}"
        )
