"""状态机表驱动测试：每个 (state, event) 期望 transition。"""
from __future__ import annotations

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, decide, dump_transitions

# 反向声明：列出 happy path 全链 + 关键分支，验 next_state 和 action。
EXPECTED = [
    # state, event, next_state, action
    (ReqState.INIT,             Event.INTENT_ANALYZE,    ReqState.ANALYZING,        "start_analyze"),
    (ReqState.ANALYZING,        Event.ANALYZE_DONE,      ReqState.SPECS_RUNNING,    "fanout_specs"),
    (ReqState.SPECS_RUNNING,    Event.SPEC_DONE,         ReqState.SPECS_RUNNING,    "mark_spec_reviewed_and_check"),
    (ReqState.SPECS_RUNNING,    Event.SPEC_ALL_PASSED,   ReqState.DEV_RUNNING,      "create_dev"),
    (ReqState.DEV_RUNNING,      Event.DEV_DONE,          ReqState.CI_UNIT_RUNNING,  "create_ci_runner_unit"),
    (ReqState.CI_UNIT_RUNNING,  Event.CI_UNIT_PASS,      ReqState.CI_INT_RUNNING,   "create_ci_runner_integration"),
    (ReqState.CI_UNIT_RUNNING,  Event.CI_UNIT_FAIL,      ReqState.DEV_RUNNING,      "comment_back_dev"),
    (ReqState.CI_INT_RUNNING,   Event.CI_INT_PASS,       ReqState.ACCEPT_RUNNING,   "create_accept"),
    (ReqState.CI_INT_RUNNING,   Event.CI_INT_FAIL,       ReqState.BUGFIX_RUNNING,   "open_gh_and_bugfix"),
    (ReqState.ACCEPT_RUNNING,   Event.ACCEPT_PASS,       ReqState.ARCHIVING,        "done_archive"),
    (ReqState.ACCEPT_RUNNING,   Event.ACCEPT_FAIL,       ReqState.BUGFIX_RUNNING,   "open_gh_and_bugfix"),
    (ReqState.BUGFIX_RUNNING,   Event.BUGFIX_DONE,       ReqState.TEST_FIX_RUNNING, "create_test_fix"),
    (ReqState.BUGFIX_RUNNING,   Event.BUGFIX_SPEC_BUG,   ReqState.ESCALATED,        "escalate"),
    (ReqState.TEST_FIX_RUNNING, Event.TEST_FIX_DONE,     ReqState.REVIEWER_RUNNING, "create_reviewer"),
    # 关键死锁修复：reviewer.pass → 重跑 ci-int（不是 dev）
    (ReqState.REVIEWER_RUNNING, Event.REVIEWER_PASS,     ReqState.CI_INT_RUNNING,   "create_ci_runner_integration"),
    (ReqState.REVIEWER_RUNNING, Event.REVIEWER_FAIL,     ReqState.ESCALATED,        "escalate"),
    (ReqState.ARCHIVING,        Event.ARCHIVE_DONE,      ReqState.DONE,             None),
]


@pytest.mark.parametrize("st,ev,next_st,action", EXPECTED)
def test_transition(st, ev, next_st, action):
    t = decide(st, ev)
    assert t is not None, f"missing transition {st.value}+{ev.value}"
    assert t.next_state == next_st
    assert t.action == action


def test_session_failed_escalates_all_running_states():
    running = [
        ReqState.ANALYZING, ReqState.SPECS_RUNNING, ReqState.DEV_RUNNING,
        ReqState.CI_UNIT_RUNNING, ReqState.CI_INT_RUNNING, ReqState.ACCEPT_RUNNING,
        ReqState.BUGFIX_RUNNING, ReqState.TEST_FIX_RUNNING, ReqState.REVIEWER_RUNNING,
        ReqState.ARCHIVING,
    ]
    for st in running:
        t = decide(st, Event.SESSION_FAILED)
        assert t is not None and t.next_state == ReqState.ESCALATED, st


def test_terminal_states_have_no_outgoing():
    """DONE / ESCALATED 不应再被任何 event 推动。"""
    for st in (ReqState.DONE, ReqState.ESCALATED):
        for ev in Event:
            assert decide(st, ev) is None, f"terminal {st.value} should not move on {ev.value}"


def test_no_orphan_actions():
    """transition.action 必须能在 actions REGISTRY 找到（导入触发注册）。"""
    from orchestrator.actions import REGISTRY  # 触发 import-side-effect 注册
    for (st, ev), t in TRANSITIONS.items():
        if t.action is None:
            continue
        assert t.action in REGISTRY, (
            f"({st.value}, {ev.value}) → action '{t.action}' 没在 REGISTRY 注册"
        )


def test_dump_transitions_renders():
    md = dump_transitions()
    assert "| state |" in md
    assert "init" in md and "done" in md
