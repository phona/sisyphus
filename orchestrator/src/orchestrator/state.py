"""状态机：REQ 全生命周期 transition table。

设计要点：
- ReqState 枚举每个 stage（"REQ 在哪一步"）
- Event 枚举每种触发（webhook → derive_event → 这）
- TRANSITIONS 是 (state, event) → Transition 的映射，唯一真相
- transition 可选触发 action（一个动词，actions/ 下有对应 handler）

核心 invariant: 同一 REQ 同时只能在一个 state；CAS 更新（store/req_state.py）
保证并发事件不会重复推进。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReqState(StrEnum):
    INIT = "init"                         # 还没 analyze
    ANALYZING = "analyzing"               # analyze-agent 在跑
    SPECS_RUNNING = "specs-running"       # 2 个 spec-agent 在跑（部分完成也在此 state）
    DEV_RUNNING = "dev-running"           # SPG gate 通过，dev-agent 在跑
    CI_UNIT_RUNNING = "ci-unit-running"   # ci-runner unit 在跑
    CI_INT_RUNNING = "ci-int-running"     # ci-runner integration 在跑
    ACCEPT_RUNNING = "accept-running"     # accept-agent 在跑
    BUGFIX_RUNNING = "bugfix-running"     # bugfix round-N 在跑
    TEST_FIX_RUNNING = "test-fix-running" # test-fix round-N 在跑
    REVIEWER_RUNNING = "reviewer-running" # reviewer round-N 在跑
    GH_INCIDENT_OPEN = "gh-incident-open" # GitHub issue 已开，等人（accept-fail / ci-int-fail 都到这）
    ARCHIVING = "archiving"               # done-archive agent 在跑
    DONE = "done"                         # PR 已开 + openspec apply 完成
    ESCALATED = "escalated"               # 熔断 / reviewer-fail / session-failed


class Event(StrEnum):
    INTENT_ANALYZE = "intent.analyze"           # 人在 BKD 打 intent:analyze tag
    ANALYZE_DONE = "analyze.done"               # analyze-agent 完成
    SPEC_DONE = "spec.done"                     # 单个 spec-agent 完成（router 不区分 contract/accept）
    SPEC_ALL_PASSED = "spec.all-passed"         # 聚合事件：actions 检测到 N/N ci-passed 后 dispatch
    DEV_DONE = "dev.done"                       # dev-agent 完成
    CI_UNIT_PASS = "ci-unit.pass"
    CI_UNIT_FAIL = "ci-unit.fail"
    CI_INT_PASS = "ci-int.pass"
    CI_INT_FAIL = "ci-int.fail"
    ACCEPT_PASS = "accept.pass"
    ACCEPT_FAIL = "accept.fail"
    BUGFIX_DONE = "bugfix.done"
    BUGFIX_SPEC_BUG = "bugfix.spec-bug"         # diagnosis:spec-bug → 直接 escalate
    TEST_FIX_DONE = "test-fix.done"
    REVIEWER_PASS = "reviewer.pass"
    REVIEWER_FAIL = "reviewer.fail"
    ARCHIVE_DONE = "archive.done"
    SESSION_FAILED = "session.failed"


@dataclass(frozen=True)
class Transition:
    """从 cur_state 收到 event 后的下一步。"""
    next_state: ReqState
    action: str | None = None  # action handler key（actions/ 下文件名同名）；None = 纯状态推进
    reason: str | None = None  # 给 logs 的可读说明


# (cur_state, event) → Transition
# 没列出的组合 = 非法 transition（webhook 收到时 skip + log）
TRANSITIONS: dict[tuple[ReqState, Event], Transition] = {
    # ─── 主链 happy path ─────────────────────────────────────────────────
    (ReqState.INIT, Event.INTENT_ANALYZE):
        Transition(ReqState.ANALYZING, "start_analyze", "kick off"),

    (ReqState.ANALYZING, Event.ANALYZE_DONE):
        Transition(ReqState.SPECS_RUNNING, "fanout_specs", "create 2 spec issues"),

    # spec-agent 完成时进入此 transition：mark_spec_reviewed 给 spec issue 加 ci-passed tag，
    # 然后 list-issues 看是否 N/N，如果是会派发 SPEC_ALL_PASSED 事件接着推进。
    (ReqState.SPECS_RUNNING, Event.SPEC_DONE):
        Transition(ReqState.SPECS_RUNNING, "mark_spec_reviewed_and_check", "tag + maybe gate"),

    (ReqState.SPECS_RUNNING, Event.SPEC_ALL_PASSED):
        Transition(ReqState.DEV_RUNNING, "create_dev", "SPG gate open"),

    (ReqState.DEV_RUNNING, Event.DEV_DONE):
        Transition(ReqState.CI_UNIT_RUNNING, "create_ci_runner_unit"),

    (ReqState.CI_UNIT_RUNNING, Event.CI_UNIT_PASS):
        Transition(ReqState.CI_INT_RUNNING, "create_ci_runner_integration"),

    (ReqState.CI_UNIT_RUNNING, Event.CI_UNIT_FAIL):
        # 轻量回炉：不算 bugfix round，回 dev issue 评论让 agent 自修
        Transition(ReqState.DEV_RUNNING, "comment_back_dev", "lightweight retry"),

    (ReqState.CI_INT_RUNNING, Event.CI_INT_PASS):
        Transition(ReqState.ACCEPT_RUNNING, "create_accept"),

    (ReqState.CI_INT_RUNNING, Event.CI_INT_FAIL):
        # 契约/集成测试失败：策略 A——一律 GH issue + BKD bugfix 双路
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix", "ci-int fail → human + AI"),

    (ReqState.ACCEPT_RUNNING, Event.ACCEPT_PASS):
        Transition(ReqState.ARCHIVING, "done_archive"),

    (ReqState.ACCEPT_RUNNING, Event.ACCEPT_FAIL):
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix", "accept fail → human + AI"),

    # ─── bugfix 子链 ─────────────────────────────────────────────────────
    (ReqState.BUGFIX_RUNNING, Event.BUGFIX_DONE):
        Transition(ReqState.TEST_FIX_RUNNING, "create_test_fix"),

    (ReqState.BUGFIX_RUNNING, Event.BUGFIX_SPEC_BUG):
        # bugfix-agent 自判 spec-bug：AI 不能动 spec，escalate 给人
        Transition(ReqState.ESCALATED, "escalate", "spec-bug needs human"),

    (ReqState.TEST_FIX_RUNNING, Event.TEST_FIX_DONE):
        Transition(ReqState.REVIEWER_RUNNING, "create_reviewer"),

    (ReqState.REVIEWER_RUNNING, Event.REVIEWER_PASS):
        # 关键 transition：reviewer 选了胜者 merge → 重跑 ci-integration（不是新 dev！）
        # 状态机让"重跑"成为合法，没有 Gate "stale" 阻塞问题
        Transition(ReqState.CI_INT_RUNNING, "create_ci_runner_integration", "rerun after merge"),

    (ReqState.REVIEWER_RUNNING, Event.REVIEWER_FAIL):
        Transition(ReqState.ESCALATED, "escalate", "reviewer abstained"),

    # ─── 终态 ───────────────────────────────────────────────────────────
    (ReqState.ARCHIVING, Event.ARCHIVE_DONE):
        Transition(ReqState.DONE, None, "PR opened, REQ complete"),

    # ─── 通用错误 ───────────────────────────────────────────────────────
    # session crash 在任何 running state 都直接 escalate（人工查 BKD logs）
    **{
        (st, Event.SESSION_FAILED): Transition(ReqState.ESCALATED, "escalate", "agent session crashed")
        for st in [
            ReqState.ANALYZING, ReqState.SPECS_RUNNING, ReqState.DEV_RUNNING,
            ReqState.CI_UNIT_RUNNING, ReqState.CI_INT_RUNNING, ReqState.ACCEPT_RUNNING,
            ReqState.BUGFIX_RUNNING, ReqState.TEST_FIX_RUNNING, ReqState.REVIEWER_RUNNING,
            ReqState.ARCHIVING,
        ]
    },
}


def decide(cur_state: ReqState, event: Event) -> Transition | None:
    """主 API：给 (state, event) 查 transition。None 表示非法/忽略。"""
    return TRANSITIONS.get((cur_state, event))


# ─── 调试用：打印整张表 ───────────────────────────────────────────────────
def dump_transitions() -> str:
    """For docs / debugging — render full transition table as markdown."""
    rows = ["| state | event | next | action | reason |", "|---|---|---|---|---|"]
    for (st, ev), t in sorted(TRANSITIONS.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value)):
        rows.append(f"| {st.value} | {ev.value} | {t.next_state.value} | {t.action or '—'} | {t.reason or ''} |")
    return "\n".join(rows)
