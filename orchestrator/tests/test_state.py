"""状态机表驱动测试：每个 (state, event) 期望 transition（M14c）。"""
from __future__ import annotations

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, decide, dump_transitions

# 反向声明：列出 happy path 全链 + 关键分支，验 next_state 和 action。
EXPECTED = [
    # state, event, next_state, action
    # INTAKING 路径：intent:intake → INTAKING → ANALYZING（新建 analyze issue）
    (ReqState.INIT,                 Event.INTENT_INTAKE,       ReqState.INTAKING,            "start_intake"),
    (ReqState.INTAKING,             Event.INTAKE_PASS,         ReqState.ANALYZING,           "start_analyze_with_finalized_intent"),
    (ReqState.INTAKING,             Event.INTAKE_FAIL,         ReqState.ESCALATED,           "escalate"),
    (ReqState.INIT,                 Event.INTENT_ANALYZE,      ReqState.ANALYZING,           "start_analyze"),
    # 内部 emit verify.escalate 路径（clone_involved_repos 失败等）
    (ReqState.ANALYZING,            Event.VERIFY_ESCALATE,     ReqState.ESCALATED,           "escalate"),
    (ReqState.INTAKING,             Event.VERIFY_ESCALATE,     ReqState.ESCALATED,           "escalate"),
    # REQ-analyze-artifact-check-1777254586：analyze done 走 artifact check 再到 spec_lint
    (ReqState.ANALYZING,            Event.ANALYZE_DONE,                 ReqState.ANALYZE_ARTIFACT_CHECKING, "create_analyze_artifact_check"),
    (ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_PASS, ReqState.SPEC_LINT_RUNNING, "create_spec_lint"),
    (ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_FAIL, ReqState.REVIEW_RUNNING,    "invoke_verifier_for_analyze_artifact_check_fail"),
    (ReqState.SPEC_LINT_RUNNING,    Event.SPEC_LINT_PASS,      ReqState.CHALLENGER_RUNNING,  "start_challenger"),
    (ReqState.SPEC_LINT_RUNNING,    Event.SPEC_LINT_FAIL,      ReqState.REVIEW_RUNNING,      "invoke_verifier_for_spec_lint_fail"),
    # M18: challenger between spec_lint and dev_cross_check
    (ReqState.CHALLENGER_RUNNING,   Event.CHALLENGER_PASS,     ReqState.DEV_CROSS_CHECK_RUNNING, "create_dev_cross_check"),
    (ReqState.CHALLENGER_RUNNING,   Event.CHALLENGER_FAIL,     ReqState.REVIEW_RUNNING,      "invoke_verifier_for_challenger_fail"),
    (ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_PASS, ReqState.STAGING_TEST_RUNNING, "create_staging_test"),
    (ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_FAIL, ReqState.REVIEW_RUNNING, "invoke_verifier_for_dev_cross_check_fail"),
    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_PASS,   ReqState.PR_CI_RUNNING,       "create_pr_ci_watch"),
    # M14c：fail 全部走 verifier（B2：3 个专门 action 替代旧统一路由）
    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL,   ReqState.REVIEW_RUNNING,      "invoke_verifier_for_staging_test_fail"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_PASS,          ReqState.ACCEPT_RUNNING,      "create_accept"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_FAIL,          ReqState.REVIEW_RUNNING,      "invoke_verifier_for_pr_ci_fail"),
    (ReqState.PR_CI_RUNNING,        Event.PR_CI_TIMEOUT,       ReqState.ESCALATED,           "escalate"),
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_ENV_UP_FAIL,  ReqState.ESCALATED,           "escalate"),
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_PASS,         ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env"),
    (ReqState.ACCEPT_RUNNING,       Event.ACCEPT_FAIL,         ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env"),
    # REQ-bkd-acceptance-feedback-loop-1777278984：teardown 通过后改去 PENDING_USER_REVIEW
    # 等用户验收。新增 PENDING_USER_REVIEW 出口 2 条。
    (ReqState.ACCEPT_TEARING_DOWN,  Event.TEARDOWN_DONE_PASS,  ReqState.PENDING_USER_REVIEW, "post_acceptance_report"),
    (ReqState.PENDING_USER_REVIEW,  Event.USER_REVIEW_PASS,    ReqState.DONE,                None),
    (ReqState.PENDING_USER_REVIEW,  Event.USER_REVIEW_FIX,     ReqState.ESCALATED,           "escalate"),
    (ReqState.ACCEPT_TEARING_DOWN,  Event.TEARDOWN_DONE_FAIL,  ReqState.REVIEW_RUNNING,      "invoke_verifier_for_accept_fail"),
    # verifier 子链（pass 拆成 N 条显式 transition，REQ-refactor-verify-pass-transition-1777727230）
    (ReqState.REVIEW_RUNNING,       Event.ANALYZE_DONE,                 ReqState.ANALYZE_ARTIFACT_CHECKING, "create_analyze_artifact_check"),
    (ReqState.REVIEW_RUNNING,       Event.ANALYZE_ARTIFACT_CHECK_PASS,  ReqState.SPEC_LINT_RUNNING,         "create_spec_lint"),
    (ReqState.REVIEW_RUNNING,       Event.SPEC_LINT_PASS,               ReqState.CHALLENGER_RUNNING,        "start_challenger"),
    (ReqState.REVIEW_RUNNING,       Event.CHALLENGER_PASS,              ReqState.DEV_CROSS_CHECK_RUNNING,   "create_dev_cross_check"),
    (ReqState.REVIEW_RUNNING,       Event.DEV_CROSS_CHECK_PASS,         ReqState.STAGING_TEST_RUNNING,      "create_staging_test"),
    (ReqState.REVIEW_RUNNING,       Event.STAGING_TEST_PASS,            ReqState.PR_CI_RUNNING,             "create_pr_ci_watch"),
    (ReqState.REVIEW_RUNNING,       Event.PR_CI_PASS,                   ReqState.ACCEPT_RUNNING,            "create_accept"),
    (ReqState.REVIEW_RUNNING,       Event.ACCEPT_PASS,                  ReqState.ACCEPT_TEARING_DOWN,       "teardown_accept_env"),
    (ReqState.REVIEW_RUNNING,       Event.VERIFY_FIX_NEEDED,   ReqState.FIXER_RUNNING,       "start_fixer"),
    (ReqState.REVIEW_RUNNING,       Event.VERIFY_ESCALATE,     ReqState.ESCALATED,           "escalate"),
    (ReqState.FIXER_RUNNING,        Event.FIXER_DONE,          ReqState.REVIEW_RUNNING,      "invoke_verifier_after_fix"),
    # fixer round cap：start_fixer 自检超 cap → 链 emit verify.escalate 走 escalate
    (ReqState.FIXER_RUNNING,        Event.VERIFY_ESCALATE,     ReqState.ESCALATED,           "escalate"),
    # PR merged hook：人手合 PR 后 GHA 触发，跳 gate 直达 DONE
    (ReqState.PENDING_USER_REVIEW,  Event.PR_MERGED,           ReqState.DONE,                None),
    (ReqState.REVIEW_RUNNING,       Event.PR_MERGED,           ReqState.DONE,                None),
    (ReqState.PR_CI_RUNNING,        Event.PR_MERGED,           ReqState.DONE,                None),
    # verifier infra-flake 有界重试
    (ReqState.REVIEW_RUNNING,       Event.VERIFY_INFRA_RETRY,  ReqState.REVIEW_RUNNING,      "apply_verify_infra_retry"),
]


@pytest.mark.parametrize("st,ev,next_st,action", EXPECTED)
def test_transition(st, ev, next_st, action):
    t = decide(st, ev)
    assert t is not None, f"missing transition {st.value}+{ev.value}"
    assert t.next_state == next_st
    assert t.action == action


def test_session_failed_routes_to_escalate_action_all_running_states():
    """SESSION_FAILED 在所有 running state 都触发 escalate action。

    新行为（auto-resume 后）：transition 是 self-loop，escalate action 内部决定
    auto-resume（state 不动）还是真 escalate（手动 CAS 推 ESCALATED）。
    所以这里只验 action 名 + transition 存在，不再要求 next_state == ESCALATED。
    """
    running = [
        ReqState.INTAKING, ReqState.ANALYZING,
        ReqState.ANALYZE_ARTIFACT_CHECKING,
        ReqState.SPEC_LINT_RUNNING, ReqState.DEV_CROSS_CHECK_RUNNING,
        ReqState.STAGING_TEST_RUNNING, ReqState.PR_CI_RUNNING,
        ReqState.ACCEPT_RUNNING, ReqState.ACCEPT_TEARING_DOWN,
        # M14b：verifier / fixer running state 也必须 escalate
        ReqState.REVIEW_RUNNING, ReqState.FIXER_RUNNING,
    ]
    for st in running:
        t = decide(st, Event.SESSION_FAILED)
        assert t is not None and t.action == "escalate", st
        # transition 是 self-loop（escalate action 自决是否真 ESCALATED）
        assert t.next_state == st, f"{st} should self-loop, got {t.next_state}"


def test_m14b_verifier_states_present():
    """M14b：新引入的 REVIEW_RUNNING / FIXER_RUNNING 应出现在 ReqState 枚举。"""
    values = {s.value for s in ReqState}
    assert "review-running" in values
    assert "fixer-running" in values


def test_m14b_verifier_events_present():
    """M14b：3 路决策事件定义齐全（retry_checker 已砍）。"""
    values = {e.value for e in Event}
    for ev in [
        "verify.pass", "verify.fix-needed", "verify.escalate", "fixer.done",
    ]:
        assert ev in values, f"M14b 缺 event: {ev}"
    assert "verify.retry-checker" not in values, "retry_checker 已砍，event 不应再存在"


def test_new_checker_events_and_states():
    """新架构：spec-lint 和 dev-cross-check 为客观 checker stages。"""
    values = {e.value for e in Event}
    assert "spec-lint.pass" in values
    assert "spec-lint.fail" in values
    assert "dev-cross-check.pass" in values
    assert "dev-cross-check.fail" in values

    states = {s.value for s in ReqState}
    assert "spec-lint-running" in states
    assert "dev-cross-check-running" in states

    # M18: SPEC_LINT_PASS 推进到 challenger（再 challenger.pass → dev-cross-check）
    t = decide(ReqState.SPEC_LINT_RUNNING, Event.SPEC_LINT_PASS)
    assert t is not None
    assert t.next_state == ReqState.CHALLENGER_RUNNING
    assert t.action == "start_challenger"

    t = decide(ReqState.CHALLENGER_RUNNING, Event.CHALLENGER_PASS)
    assert t is not None
    assert t.next_state == ReqState.DEV_CROSS_CHECK_RUNNING
    assert t.action == "create_dev_cross_check"

    # DEV_CROSS_CHECK_PASS 推进到 staging-test
    t = decide(ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_PASS)
    assert t is not None
    assert t.next_state == ReqState.STAGING_TEST_RUNNING
    assert t.action == "create_staging_test"


def test_done_terminal_has_no_outgoing():
    """DONE 是死终态。"""
    for ev in Event:
        assert decide(ReqState.DONE, ev) is None, f"DONE should not move on {ev.value}"


def test_escalated_resumable_via_verifier_followup():
    """ESCALATED 不是死终态：用户续 verifier issue → BKD 新 decision → 走原 verifier 同链。

    decision=pass 时 router 译成对应主链 pass 事件（如 STAGING_TEST_PASS），
    ESCALATED 反激活 transition 处理；VERIFY_FIX_NEEDED / VERIFY_ESCALATE 仍走 verifier 子链。
    """
    for ev in (Event.STAGING_TEST_PASS, Event.VERIFY_FIX_NEEDED, Event.VERIFY_ESCALATE):
        assert decide(ReqState.ESCALATED, ev) is not None, ev


def test_escalated_resumable_via_stage_issue_followup():
    """REQ-escalated-stage-resume：用户续 stage agent issue（非 verifier issue）也能恢复。

    BKD agent 重跑并贴 result tag → router 派出主链事件 → ESCALATED 复用主链 transition
    （next_state / action 跟主链同一份），不需要新 action。
    """
    expected = [
        # event,                                next_state,                          action
        (Event.INTAKE_PASS,                     ReqState.ANALYZING,                  "start_analyze_with_finalized_intent"),
        (Event.ANALYZE_DONE,                    ReqState.ANALYZE_ARTIFACT_CHECKING,  "create_analyze_artifact_check"),
        (Event.ANALYZE_ARTIFACT_CHECK_PASS,     ReqState.SPEC_LINT_RUNNING,          "create_spec_lint"),
        (Event.ANALYZE_ARTIFACT_CHECK_FAIL,     ReqState.REVIEW_RUNNING,             "invoke_verifier_for_analyze_artifact_check_fail"),
        (Event.SPEC_LINT_PASS,                  ReqState.CHALLENGER_RUNNING,         "start_challenger"),
        (Event.SPEC_LINT_FAIL,                  ReqState.REVIEW_RUNNING,             "invoke_verifier_for_spec_lint_fail"),
        (Event.CHALLENGER_PASS,                 ReqState.DEV_CROSS_CHECK_RUNNING,    "create_dev_cross_check"),
        (Event.CHALLENGER_FAIL,                 ReqState.REVIEW_RUNNING,             "invoke_verifier_for_challenger_fail"),
        (Event.DEV_CROSS_CHECK_PASS,            ReqState.STAGING_TEST_RUNNING,       "create_staging_test"),
        (Event.DEV_CROSS_CHECK_FAIL,            ReqState.REVIEW_RUNNING,             "invoke_verifier_for_dev_cross_check_fail"),
        (Event.STAGING_TEST_PASS,               ReqState.PR_CI_RUNNING,              "create_pr_ci_watch"),
        (Event.STAGING_TEST_FAIL,               ReqState.REVIEW_RUNNING,             "invoke_verifier_for_staging_test_fail"),
        (Event.PR_CI_PASS,                      ReqState.ACCEPT_RUNNING,             "create_accept"),
        (Event.PR_CI_FAIL,                      ReqState.REVIEW_RUNNING,             "invoke_verifier_for_pr_ci_fail"),
        (Event.ACCEPT_PASS,                     ReqState.ACCEPT_TEARING_DOWN,        "teardown_accept_env"),
        (Event.ACCEPT_FAIL,                     ReqState.ACCEPT_TEARING_DOWN,        "teardown_accept_env"),
        (Event.TEARDOWN_DONE_PASS,              ReqState.PENDING_USER_REVIEW,        "post_acceptance_report"),
        (Event.TEARDOWN_DONE_FAIL,              ReqState.REVIEW_RUNNING,             "invoke_verifier_for_accept_fail"),
        (Event.FIXER_DONE,                      ReqState.REVIEW_RUNNING,             "invoke_verifier_after_fix"),
    ]
    for ev, next_st, action in expected:
        t = decide(ReqState.ESCALATED, ev)
        assert t is not None, f"{ev.value} should be resumable from ESCALATED"
        assert t.next_state == next_st, f"{ev.value}: expected {next_st}, got {t.next_state}"
        assert t.action == action, f"{ev.value}: expected action {action}, got {t.action}"


def test_escalated_non_resume_events_still_blocked():
    """不该被复活的事件：直接进 ESCALATED 类、SESSION_FAILED、PENDING_USER_REVIEW 出口、PR_MERGED。

    PR_MERGED 由 escalate.py 入口的 PR-merged shortcut 处理，不在 transition 表里出现。
    """
    blocked = {
        Event.SESSION_FAILED,        # 已在 ESCALATED 再挂还是 escalate，无意义
        Event.INTAKE_FAIL,           # 直接进 ESCALATED 的事件，不是恢复信号
        Event.PR_CI_TIMEOUT,
        Event.ACCEPT_ENV_UP_FAIL,
        Event.USER_REVIEW_FIX,       # PENDING_USER_REVIEW 出口
        Event.USER_REVIEW_PASS,
        Event.PR_MERGED,             # escalate.py 入口的 PR-merged shortcut 处理
        Event.INTENT_INTAKE,         # 入口事件，REQ 已经存在不该重新入口
        Event.INTENT_ANALYZE,
    }
    for ev in blocked:
        assert decide(ReqState.ESCALATED, ev) is None, f"{ev.value} should NOT move ESCALATED"


def test_m5_dropped_test_fix_reviewer_states():
    """M5：确认 test-fix-running / reviewer-running 这两个老 state 彻底删。"""
    values = {s.value for s in ReqState}
    assert "test-fix-running" not in values
    assert "reviewer-running" not in values


# ── REQ-bkd-acceptance-feedback-loop-1777278984 -----------------------------


def test_user_review_state_and_events_present():
    """USR-T0: 新 state / event 枚举齐全（防 typo 改坏 webhook 派发）。"""
    states = {s.value for s in ReqState}
    assert "pending-user-review" in states
    events = {e.value for e in Event}
    assert "user-review.pass" in events
    assert "user-review.fix" in events


def test_user_review_pending_has_no_session_failed_self_loop():
    """USR-T4: PENDING_USER_REVIEW 没 BKD agent 在跑，不应有 SESSION_FAILED 入口。"""
    assert decide(ReqState.PENDING_USER_REVIEW, Event.SESSION_FAILED) is None


def test_user_review_pending_illegal_events_return_none():
    """USR-T4 续：PENDING_USER_REVIEW 4 类合法事件 = exits + #247 PASS resume。

    - 出口：USER_REVIEW_PASS / USER_REVIEW_FIX / PR_MERGED
    - resume（#247 Phase 1）：ANALYZE_DONE / SPEC_LINT_PASS / CHALLENGER_PASS /
      DEV_CROSS_CHECK_PASS / STAGING_TEST_PASS / PR_CI_PASS / ACCEPT_PASS
    其他全非法。
    """
    legal = {
        Event.USER_REVIEW_PASS, Event.USER_REVIEW_FIX, Event.PR_MERGED,
        # #247 Phase 1 stage-issue follow-up resume (PASS-only)
        Event.ANALYZE_DONE, Event.SPEC_LINT_PASS, Event.CHALLENGER_PASS,
        Event.DEV_CROSS_CHECK_PASS, Event.STAGING_TEST_PASS,
        Event.PR_CI_PASS, Event.ACCEPT_PASS,
    }
    for ev in Event:
        if ev in legal:
            continue
        assert decide(ReqState.PENDING_USER_REVIEW, ev) is None, ev


def test_acceptance_teardown_pass_routes_through_pending_not_archiving():
    """USR-T1: TEARDOWN_DONE_PASS 现在指向 PENDING_USER_REVIEW + post_acceptance_report
    （archive 改为 transition 到 DONE 时后台自动触发）。"""
    t = decide(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS)
    assert t is not None
    assert t.next_state == ReqState.PENDING_USER_REVIEW
    assert t.action == "post_acceptance_report"


def test_m5_dropped_test_fix_reviewer_events():
    """M5：test-fix.done / reviewer.pass / reviewer.fail 也彻底删。"""
    legacy = {"test-fix.done", "reviewer.pass", "reviewer.fail"}
    for e in Event:
        assert e.value not in legacy, f"M5 应彻底删 {e.value}"


def test_v02_removed_ci_states():
    """v0.2 砍 CI_UNIT_RUNNING / CI_INT_RUNNING 作为独立 state（event 留作 legacy 兼容）。"""
    values = {s.value for s in ReqState}
    assert "ci-unit-running" not in values
    assert "ci-int-running" not in values


def test_v02_no_legacy_ci_events():
    """v0.2 完全删了 CI_UNIT_PASS/FAIL/CI_INT_PASS/FAIL。"""
    legacy_values = {"ci-unit.pass", "ci-unit.fail", "ci-int.pass", "ci-int.fail"}
    for e in Event:
        assert e.value not in legacy_values, f"v0.2 应彻底删 {e.value}"


def test_m12_dropped_pending_human_state_and_event():
    """M12：砍 M6 admission → ANALYZING_PENDING_HUMAN state / ANALYZE_PENDING_HUMAN event 彻底删。

    sisyphus 不再卡 analyze 阶段歧义；agent 自己在 BKD chat 里跟 user 谈。
    """
    state_values = {s.value for s in ReqState}
    assert "analyzing-pending-human" not in state_values

    event_values = {e.value for e in Event}
    assert "analyze.pending-human" not in event_values


def test_m14c_dropped_bugfix_diagnose_states():
    """M14c：BUGFIX_RUNNING / DIAGNOSE_RUNNING 彻底删。"""
    values = {s.value for s in ReqState}
    assert "bugfix-running" not in values
    assert "diagnose-running" not in values


def test_m14c_dropped_bugfix_diagnose_events():
    """M14c：BUGFIX_* / DIAGNOSE_* / SPEC_REWORK 老事件全删。"""
    legacy = {
        "bugfix.done", "bugfix.spec-bug", "bugfix.env-bug",
        "bugfix.retry", "diagnose.needed", "spec.rework",
    }
    for e in Event:
        assert e.value not in legacy, f"M14c 应彻底删 {e.value}"


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
    # v0.2 / M14c 新 state 出现
    assert "staging-test-running" in md
    assert "pr-ci-running" in md
    assert "accept-tearing-down" in md
    assert "review-running" in md
