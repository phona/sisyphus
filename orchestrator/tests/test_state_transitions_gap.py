"""状态机 transition 缺口补齐测试（REQ-529）。

覆盖范围：
- PR_MERGED 竞态 3 条 transition
- VERIFY_INFRA_RETRY 从 REVIEW_RUNNING
- ESCALATED 恢复态 3 条 transition 的 next_state + action 实际值
- CHALLENGER_RUNNING 的 SESSION_FAILED self-loop
- 各 state 非法事件返回 None 的负向断言
- 全 transition 枚举验 completeness
"""
from __future__ import annotations

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, Transition, decide

# ── 缺口 1：PR_MERGED 竞态 transition（REQ-pr-merge-archive-hook-1777344443）─────────

PR_MERGED_CASES = [
    # state, event, next_state, action
    (ReqState.PENDING_USER_REVIEW, Event.PR_MERGED, ReqState.DONE, None),
    (ReqState.REVIEW_RUNNING, Event.PR_MERGED, ReqState.DONE, None),
    (ReqState.PR_CI_RUNNING, Event.PR_MERGED, ReqState.DONE, None),
]


@pytest.mark.parametrize("st,ev,next_st,action", PR_MERGED_CASES)
def test_pr_merged_race_transitions(st, ev, next_st, action):
    """PR merged by reviewer → skip remaining gates → archive。CAS 竞态下只有一方赢。"""
    t = decide(st, ev)
    assert t is not None, f"missing PR_MERGED transition from {st.value}"
    assert t.next_state == next_st, f"{st.value}+{ev.value} expected {next_st.value}, got {t.next_state.value}"
    assert t.action == action


def test_pr_merged_only_from_specific_states():
    """PR_MERGED 只允许从 PENDING_USER_REVIEW / REVIEW_RUNNING / PR_CI_RUNNING 触发。

    从其他任何 state 触发 PR_MERGED 都是非法的（不应有 transition）。
    """
    allowed = {ReqState.PENDING_USER_REVIEW, ReqState.REVIEW_RUNNING, ReqState.PR_CI_RUNNING}
    for st in ReqState:
        if st in allowed:
            continue
        assert decide(st, Event.PR_MERGED) is None, (
            f"PR_MERGED should NOT be valid from {st.value}"
        )


# ── 缺口 2：VERIFY_INFRA_RETRY（有界重跑 infra flake checker）──────────────────────────


def test_verify_infra_retry_from_review_running():
    """verifier decision=retry → 有界重跑 stage checker（infra flake；超 cap → escalate）。"""
    t = decide(ReqState.REVIEW_RUNNING, Event.VERIFY_INFRA_RETRY)
    assert t is not None
    assert t.next_state == ReqState.REVIEW_RUNNING
    assert t.action == "apply_verify_infra_retry"


def test_verify_infra_retry_only_from_review_running():
    """VERIFY_INFRA_RETRY 只应从 REVIEW_RUNNING 触发（verifier 决策的上下文）。"""
    for st in ReqState:
        if st == ReqState.REVIEW_RUNNING:
            continue
        assert decide(st, Event.VERIFY_INFRA_RETRY) is None, (
            f"VERIFY_INFRA_RETRY should NOT be valid from {st.value}"
        )


# ── 缺口 3：ESCALATED 恢复态 transition 实际值（此前只测了 existence）──────────────────

ESCALATED_RESUME_CASES = [
    (Event.VERIFY_PASS, ReqState.ESCALATED, "apply_verify_pass"),
    (Event.VERIFY_FIX_NEEDED, ReqState.FIXER_RUNNING, "start_fixer"),
]


@pytest.mark.parametrize("ev,next_st,action", ESCALATED_RESUME_CASES)
def test_escalated_resume_transition_values(ev, next_st, action):
    """ESCALATED 恢复态：用户续 verifier issue → 新 decision → 推进状态。

    此前 test_escalated_resumable_via_verifier_followup 只测了 existence，
    这里补 next_state + action 的精确值。
    """
    t = decide(ReqState.ESCALATED, ev)
    assert t is not None
    assert t.next_state == next_st, f"ESCALATED+{ev.value} expected {next_st.value}, got {t.next_state.value}"
    assert t.action == action


def test_escalated_verify_escalate_self_loop_no_action():
    """ESCALATED + VERIFY_ESCALATE → 留原地（self-loop），无 action（等下一次 follow-up）。"""
    t = decide(ReqState.ESCALATED, Event.VERIFY_ESCALATE)
    assert t is not None
    assert t.next_state == ReqState.ESCALATED
    assert t.action is None


# ── 缺口 4：CHALLENGER_RUNNING 的 SESSION_FAILED self-loop─────────────────────────────


def test_challenger_running_session_failed_self_loop():
    """CHALLENGER_RUNNING 必须有 SESSION_FAILED self-loop（escalate action 自决 auto-resume）。"""
    t = decide(ReqState.CHALLENGER_RUNNING, Event.SESSION_FAILED)
    assert t is not None
    assert t.next_state == ReqState.CHALLENGER_RUNNING
    assert t.action == "escalate"


# ── 缺口 5：负向断言 —— 各 state 的非法事件应返回 None──────────────────────────────────


def test_init_illegal_events():
    """INIT 只接受 INTENT_INTAKE 和 INTENT_ANALYZE。"""
    legal = {Event.INTENT_INTAKE, Event.INTENT_ANALYZE}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.INIT, ev) is None, f"INIT should not accept {ev.value}"


def test_intaking_illegal_events():
    """INTAKING 只接受 INTAKE_PASS / INTAKE_FAIL / VERIFY_ESCALATE / SESSION_FAILED。"""
    legal = {Event.INTAKE_PASS, Event.INTAKE_FAIL, Event.VERIFY_ESCALATE, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.INTAKING, ev) is None, f"INTAKING should not accept {ev.value}"


def test_analyzing_illegal_events():
    """ANALYZING 只接受 ANALYZE_DONE / VERIFY_ESCALATE / SESSION_FAILED。"""
    legal = {Event.ANALYZE_DONE, Event.VERIFY_ESCALATE, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.ANALYZING, ev) is None, f"ANALYZING should not accept {ev.value}"


def test_analyze_artifact_checking_illegal_events():
    """ANALYZE_ARTIFACT_CHECKING 只接受 pass/fail + SESSION_FAILED。"""
    legal = {
        Event.ANALYZE_ARTIFACT_CHECK_PASS, Event.ANALYZE_ARTIFACT_CHECK_FAIL,
        Event.SESSION_FAILED,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.ANALYZE_ARTIFACT_CHECKING, ev) is None, (
            f"ANALYZE_ARTIFACT_CHECKING should not accept {ev.value}"
        )


def test_spec_lint_running_illegal_events():
    """SPEC_LINT_RUNNING 只接受 pass/fail + SESSION_FAILED。"""
    legal = {Event.SPEC_LINT_PASS, Event.SPEC_LINT_FAIL, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.SPEC_LINT_RUNNING, ev) is None, (
            f"SPEC_LINT_RUNNING should not accept {ev.value}"
        )


def test_challenger_running_illegal_events():
    """CHALLENGER_RUNNING 只接受 pass/fail + SESSION_FAILED。"""
    legal = {Event.CHALLENGER_PASS, Event.CHALLENGER_FAIL, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.CHALLENGER_RUNNING, ev) is None, (
            f"CHALLENGER_RUNNING should not accept {ev.value}"
        )


def test_dev_cross_check_running_illegal_events():
    """DEV_CROSS_CHECK_RUNNING 只接受 pass/fail + SESSION_FAILED。"""
    legal = {Event.DEV_CROSS_CHECK_PASS, Event.DEV_CROSS_CHECK_FAIL, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.DEV_CROSS_CHECK_RUNNING, ev) is None, (
            f"DEV_CROSS_CHECK_RUNNING should not accept {ev.value}"
        )


def test_staging_test_running_illegal_events():
    """STAGING_TEST_RUNNING 只接受 pass/fail + SESSION_FAILED。"""
    legal = {Event.STAGING_TEST_PASS, Event.STAGING_TEST_FAIL, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.STAGING_TEST_RUNNING, ev) is None, (
            f"STAGING_TEST_RUNNING should not accept {ev.value}"
        )


def test_pr_ci_running_illegal_events():
    """PR_CI_RUNNING 接受 pass/fail/timeout/merged + SESSION_FAILED。"""
    legal = {
        Event.PR_CI_PASS, Event.PR_CI_FAIL, Event.PR_CI_TIMEOUT,
        Event.PR_MERGED, Event.SESSION_FAILED,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.PR_CI_RUNNING, ev) is None, (
            f"PR_CI_RUNNING should not accept {ev.value}"
        )


def test_accept_running_illegal_events():
    """ACCEPT_RUNNING 接受 pass/fail/env-up-fail + SESSION_FAILED。"""
    legal = {
        Event.ACCEPT_PASS, Event.ACCEPT_FAIL, Event.ACCEPT_ENV_UP_FAIL,
        Event.SESSION_FAILED,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.ACCEPT_RUNNING, ev) is None, (
            f"ACCEPT_RUNNING should not accept {ev.value}"
        )


def test_accept_tearing_down_illegal_events():
    """ACCEPT_TEARING_DOWN 接受 teardown-done pass/fail + SESSION_FAILED。"""
    legal = {
        Event.TEARDOWN_DONE_PASS, Event.TEARDOWN_DONE_FAIL,
        Event.SESSION_FAILED,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.ACCEPT_TEARING_DOWN, ev) is None, (
            f"ACCEPT_TEARING_DOWN should not accept {ev.value}"
        )


def test_pending_user_review_illegal_events():
    """PENDING_USER_REVIEW 接受 user-review pass/fix/merged。无 SESSION_FAILED。"""
    legal = {Event.USER_REVIEW_PASS, Event.USER_REVIEW_FIX, Event.PR_MERGED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.PENDING_USER_REVIEW, ev) is None, (
            f"PENDING_USER_REVIEW should not accept {ev.value}"
        )


def test_review_running_illegal_events():
    """REVIEW_RUNNING 接受 verifier 4 路决策 + merged + SESSION_FAILED。"""
    legal = {
        Event.VERIFY_PASS, Event.VERIFY_FIX_NEEDED, Event.VERIFY_ESCALATE,
        Event.VERIFY_INFRA_RETRY, Event.PR_MERGED, Event.SESSION_FAILED,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.REVIEW_RUNNING, ev) is None, (
            f"REVIEW_RUNNING should not accept {ev.value}"
        )


def test_fixer_running_illegal_events():
    """FIXER_RUNNING 接受 fixer.done / verify.escalate / SESSION_FAILED。"""
    legal = {Event.FIXER_DONE, Event.VERIFY_ESCALATE, Event.SESSION_FAILED}
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.FIXER_RUNNING, ev) is None, (
            f"FIXER_RUNNING should not accept {ev.value}"
        )


def test_gh_incident_open_has_no_transitions():
    """GH_INCIDENT_OPEN 是等人 state，没有任何 outgoing transition。"""
    for ev in Event:
        assert decide(ReqState.GH_INCIDENT_OPEN, ev) is None, (
            f"GH_INCIDENT_OPEN should have no transitions, got {ev.value}"
        )


# ── 缺口 6：全 transition 枚举验 completeness（防止未来新增 transition 漏测）───────


def test_all_transitions_have_next_state_and_action_type():
    """每个 transition 必须有合法的 next_state（ReqState 枚举值）和 str/None action。"""
    for (st, ev), t in TRANSITIONS.items():
        assert isinstance(t, Transition)
        assert isinstance(t.next_state, ReqState), (
            f"({st.value}, {ev.value}) next_state must be ReqState enum, got {type(t.next_state)}"
        )
        assert t.action is None or isinstance(t.action, str), (
            f"({st.value}, {ev.value}) action must be str or None, got {type(t.action)}"
        )


def test_all_transitions_reason_is_str_or_none():
    """每个 transition 的 reason 必须是 str 或 None。"""
    for _kv, t in TRANSITIONS.items():
        assert t.reason is None or isinstance(t.reason, str)


def test_transition_count_sanity():
    """transition 总数 sanity check：防止未来重构误删/误增。

    当前总数 = 39 显式 + 12 SESSION_FAILED self-loop + 19 ESCALATED stage-resume 反激活
    （REQ-escalated-stage-resume）= 70。
    如果数字变了，说明有人增删 transition，本测试会 fail 提醒同步测试。
    """
    assert len(TRANSITIONS) == 70, (
        f"Expected 70 transitions, got {len(TRANSITIONS)}. "
        "If this is intentional, update this assertion and add corresponding tests."
    )


def test_explicit_transition_count():
    """非 SESSION_FAILED 的显式 + ESCALATED 反激活 transition 数量 sanity check。

    包含 39 主链显式 + 19 ESCALATED 主链反激活（复用主链 transition 对象，但 key 是
    (ESCALATED, ev) 独立计数）= 58 条非 SESSION_FAILED transition。
    """
    explicit = [k for k in TRANSITIONS if k[1] != Event.SESSION_FAILED]
    assert len(explicit) == 58, (
        f"Expected 58 explicit transitions, got {len(explicit)}"
    )


def test_session_failed_transition_count():
    """SESSION_FAILED self-loop 数量 sanity check。"""
    session_failed = [k for k in TRANSITIONS if k[1] == Event.SESSION_FAILED]
    assert len(session_failed) == 12, (
        f"Expected 12 SESSION_FAILED transitions, got {len(session_failed)}"
    )


# ── 缺口 7：竞态路径 —— CAS 场景下的 transition 行为────────────────────────────────────


def test_verify_pass_from_review_running_is_self_loop():
    """VERIFY_PASS 从 REVIEW_RUNNING 出发是 self-loop：action 内部手动 CAS 推进。

    这是 M14b 设计要点：next_state 不动，action 自己决定跳到哪个 stage_running。
    """
    t = decide(ReqState.REVIEW_RUNNING, Event.VERIFY_PASS)
    assert t is not None
    assert t.next_state == ReqState.REVIEW_RUNNING
    assert t.action == "apply_verify_pass"


def test_verify_infra_retry_from_review_running_is_self_loop():
    """VERIFY_INFRA_RETRY 同样是 self-loop：action 内部有界重跑 checker。"""
    t = decide(ReqState.REVIEW_RUNNING, Event.VERIFY_INFRA_RETRY)
    assert t is not None
    assert t.next_state == ReqState.REVIEW_RUNNING
    assert t.action == "apply_verify_infra_retry"


# ── 缺口 8：终态安全 —— DONE / ESCALATED 不应有未声明的出口─────────────────────────────


def test_done_no_transitions_at_all():
    """DONE 是完全终态：没有任何 outgoing transition（包括 SESSION_FAILED）。"""
    for ev in Event:
        assert decide(ReqState.DONE, ev) is None, f"DONE must not have any transition on {ev.value}"


def test_escalated_non_resume_events_all_none():
    """ESCALATED 接受 3 类恢复事件，其余非法：

    1. verifier 决策续 follow-up：VERIFY_PASS / VERIFY_FIX_NEEDED / VERIFY_ESCALATE
    2. stage-issue 续 follow-up（REQ-escalated-stage-resume）：19 条主链事件
       —— intake/analyze/spec_lint/challenger/dev_cross_check/staging_test/pr_ci/
       accept/teardown/fixer 各 pass+fail（无 timeout/env-up-fail/intake-fail/
       user-review-fix/PR_MERGED 这类直接进 ESCALATED 或不属于"恢复"语义的事件）
    """
    resume_events = {
        # verifier 决策反激活
        Event.VERIFY_PASS, Event.VERIFY_FIX_NEEDED, Event.VERIFY_ESCALATE,
        # stage-issue 反激活
        Event.INTAKE_PASS,
        Event.ANALYZE_DONE,
        Event.ANALYZE_ARTIFACT_CHECK_PASS, Event.ANALYZE_ARTIFACT_CHECK_FAIL,
        Event.SPEC_LINT_PASS, Event.SPEC_LINT_FAIL,
        Event.CHALLENGER_PASS, Event.CHALLENGER_FAIL,
        Event.DEV_CROSS_CHECK_PASS, Event.DEV_CROSS_CHECK_FAIL,
        Event.STAGING_TEST_PASS, Event.STAGING_TEST_FAIL,
        Event.PR_CI_PASS, Event.PR_CI_FAIL,
        Event.ACCEPT_PASS, Event.ACCEPT_FAIL,
        Event.TEARDOWN_DONE_PASS, Event.TEARDOWN_DONE_FAIL,
        Event.FIXER_DONE,
    }
    for ev in Event:
        if ev in resume_events:
            continue
        assert decide(ReqState.ESCALATED, ev) is None, (
            f"ESCALATED should not accept non-resume event {ev.value}"
        )
