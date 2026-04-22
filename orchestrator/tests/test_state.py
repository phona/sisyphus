"""状态机表驱动测试：每个 (state, event) 期望 transition（v0.2）。"""
from __future__ import annotations

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, decide, dump_transitions

# 反向声明：列出 happy path 全链 + 关键分支，验 next_state 和 action。
EXPECTED = [
    # state, event, next_state, action
    (ReqState.INIT,                 Event.INTENT_ANALYZE,      ReqState.ANALYZING,           "start_analyze"),
    (ReqState.ANALYZING,            Event.ANALYZE_DONE,        ReqState.SPECS_RUNNING,       "fanout_specs"),
    (ReqState.SPECS_RUNNING,        Event.SPEC_DONE,           ReqState.SPECS_RUNNING,       "mark_spec_reviewed_and_check"),
    (ReqState.SPECS_RUNNING,        Event.SPEC_ALL_PASSED,     ReqState.DEV_RUNNING,         "create_dev"),
    # v0.2：dev → staging-test（砍 ci-unit/ci-int 作为独立 stage）
    (ReqState.DEV_RUNNING,          Event.DEV_DONE,            ReqState.STAGING_TEST_RUNNING, "create_staging_test"),
    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_PASS,   ReqState.PR_CI_RUNNING,       "create_pr_ci_watch"),
    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL,   ReqState.BUGFIX_RUNNING,      "open_gh_and_bugfix"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_PASS,          ReqState.ACCEPT_RUNNING,      "create_accept"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_FAIL,          ReqState.BUGFIX_RUNNING,      "open_gh_and_bugfix"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_TIMEOUT,       ReqState.ESCALATED,           "escalate"),
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_ENV_UP_FAIL,  ReqState.ESCALATED,           "escalate"),
    # v0.2：accept 完必跑 teardown 清 lab（不管 pass/fail）
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_PASS,         ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env"),
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_FAIL,         ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env"),
    (ReqState.ACCEPT_TEARING_DOWN,  Event.TEARDOWN_DONE_PASS,  ReqState.ARCHIVING,           "done_archive"),
    (ReqState.ACCEPT_TEARING_DOWN,  Event.TEARDOWN_DONE_FAIL,  ReqState.BUGFIX_RUNNING,      "open_gh_and_bugfix"),
    (ReqState.BUGFIX_RUNNING,       Event.BUGFIX_DONE,         ReqState.TEST_FIX_RUNNING,    "create_test_fix"),
    (ReqState.BUGFIX_RUNNING,       Event.BUGFIX_SPEC_BUG,     ReqState.ESCALATED,           "escalate"),
    (ReqState.BUGFIX_RUNNING,       Event.BUGFIX_ENV_BUG,      ReqState.ESCALATED,           "escalate"),
    (ReqState.TEST_FIX_RUNNING,     Event.TEST_FIX_DONE,       ReqState.REVIEWER_RUNNING,    "create_reviewer"),
    # v0.2 关键：reviewer.pass 回 STAGING_TEST（小步快跑重验）
    (ReqState.REVIEWER_RUNNING,     Event.REVIEWER_PASS,       ReqState.STAGING_TEST_RUNNING, "create_staging_test"),
    (ReqState.REVIEWER_RUNNING,     Event.REVIEWER_FAIL,       ReqState.ESCALATED,           "escalate"),
    (ReqState.ARCHIVING,            Event.ARCHIVE_DONE,        ReqState.DONE,                None),
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
        ReqState.STAGING_TEST_RUNNING, ReqState.PR_CI_RUNNING,
        ReqState.ACCEPT_RUNNING, ReqState.ACCEPT_TEARING_DOWN,
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


def test_v02_removed_ci_states():
    """v0.2 砍 CI_UNIT_RUNNING / CI_INT_RUNNING 作为独立 state（event 留作 legacy 兼容）。"""
    values = {s.value for s in ReqState}
    assert "ci-unit-running" not in values
    assert "ci-int-running" not in values


def test_v02_legacy_ci_events_unused_by_state_machine():
    """ci-unit.* / ci-int.* event 为 v0.1 遗留（event 枚举保留给老 action 引用，
    但 TRANSITIONS 里不再出现，state machine 不再响应。"""
    legacy = {Event.CI_UNIT_PASS, Event.CI_UNIT_FAIL, Event.CI_INT_PASS, Event.CI_INT_FAIL}
    for (_, ev), _ in TRANSITIONS.items():
        assert ev not in legacy, f"v0.2 state machine 不该再用 {ev.value}"


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
    # v0.2 新 state 出现
    assert "staging-test-running" in md
    assert "pr-ci-running" in md
    assert "accept-tearing-down" in md
