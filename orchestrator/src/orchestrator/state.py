"""状态机：REQ 全生命周期 transition table（M14c：verifier 接管 fail 路径）。

M14c 变化：
- 砍 BUGFIX_RUNNING / DIAGNOSE_RUNNING 子链（M5 的拆分）
- staging-test / pr-ci / accept / teardown 失败事件全部路由到 REVIEW_RUNNING，
  由 verifier-agent 主观判 pass / fix / retry_checker / escalate
- BUGFIX_* / DIAGNOSE_* 事件全部砍掉

保留自 v0.2 / M14b：
- 加 STAGING_TEST_RUNNING：dev 之后 agent 在调试环境跑 unit + integration test
- 加 PR_CI_RUNNING：PR 开了，等 GHA 全套（lint/unit/int/sonar/image-publish）全绿
- 加 ACCEPT_TEARING_DOWN：accept 完成后必跑 env-down 清 lab，保证不漏资源
- M14b verifier 子链：REVIEW_RUNNING + FIXER_RUNNING

设计要点：
- ReqState 枚举每个 stage（"REQ 在哪一步"）
- Event 枚举每种触发（webhook → derive_event → 这）
- TRANSITIONS 是 (state, event) → Transition 的映射，唯一真相
- transition 可选触发 action（actions/ 下有对应 handler）
- 同 REQ 同时只能在一个 state；CAS 更新（store/req_state.py）保并发
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReqState(StrEnum):
    INIT = "init"                               # 还没 analyze
    ANALYZING = "analyzing"                     # analyze-agent 在跑
    SPECS_RUNNING = "specs-running"             # contract + acceptance spec-agent
    DEV_RUNNING = "dev-running"                 # SPG gate 通过，dev-agent 只写代码
    STAGING_TEST_RUNNING = "staging-test-running"  # 调试环境 build + unit + int test
    PR_CI_RUNNING = "pr-ci-running"             # PR 已开，等 GHA 全套绿
    ACCEPT_RUNNING = "accept-running"           # env-up 完，accept-agent 跑 FEATURE-A*
    ACCEPT_TEARING_DOWN = "accept-tearing-down" # env-down 清 lab，后续按 accept_result 分流
    GH_INCIDENT_OPEN = "gh-incident-open"       # GitHub issue 已开，等人
    ARCHIVING = "archiving"                     # done-archive agent（合 PR 等）
    # M14b：verifier-agent 框架
    REVIEW_RUNNING = "review-running"           # verifier-agent 在跑（success / fail 两触发统一入口）
    FIXER_RUNNING = "fixer-running"             # verifier decision=fix → 起对应 fixer agent（dev/spec/manifest）
    DONE = "done"                               # REQ 完成
    ESCALATED = "escalated"                     # 熔断 / session-failed / 人工止损


class Event(StrEnum):
    INTENT_ANALYZE = "intent.analyze"               # 人在 BKD 打 intent:analyze tag
    ANALYZE_DONE = "analyze.done"                   # analyze-agent 完成
    SPEC_DONE = "spec.done"                         # 单个 spec-agent 完成
    SPEC_ALL_PASSED = "spec.all-passed"             # 聚合事件：N/N ci-passed
    DEV_DONE = "dev.done"                           # dev-agent push 完毕
    STAGING_TEST_PASS = "staging-test.pass"         # 调试环境测试全绿
    STAGING_TEST_FAIL = "staging-test.fail"         # 调试环境测试任一红 → verifier
    PR_CI_PASS = "pr-ci.pass"                       # GHA 全套绿（含 image-publish）
    PR_CI_FAIL = "pr-ci.fail"                       # GHA 任一红 → verifier
    PR_CI_TIMEOUT = "pr-ci.timeout"                 # 没收到 CI 结果（可能 repo 没配）
    ACCEPT_ENV_UP_FAIL = "accept-env-up.fail"       # lab 起不来（内部事件，create_accept 发）
    ACCEPT_PASS = "accept.pass"                     # accept-agent 跑完 FEATURE-A* 全 pass
    ACCEPT_FAIL = "accept.fail"                     # accept-agent 发现 bug → verifier
    TEARDOWN_DONE_PASS = "teardown-done.pass"       # env-down 完（上一个是 accept.pass）
    TEARDOWN_DONE_FAIL = "teardown-done.fail"       # env-down 完（上一个是 accept.fail）→ verifier
    ARCHIVE_DONE = "archive.done"
    SESSION_FAILED = "session.failed"
    # M14b：verifier-agent 决策事件（webhook.py 从 verifier issue 的 decision JSON 派发）
    VERIFY_PASS = "verify.pass"                     # decision.action = pass → 推下一 stage
    VERIFY_FIX_NEEDED = "verify.fix-needed"         # decision.action = fix → 起 fixer agent
    VERIFY_RETRY_CHECKER = "verify.retry-checker"   # decision.action = retry_checker → 重跑当前 checker
    VERIFY_ESCALATE = "verify.escalate"             # decision.action = escalate
    FIXER_DONE = "fixer.done"                       # fixer agent 跑完 → 回对应 stage 重跑 checker


@dataclass(frozen=True)
class Transition:
    """从 cur_state 收到 event 后的下一步。"""
    next_state: ReqState
    action: str | None = None
    reason: str | None = None


# (cur_state, event) → Transition
# 没列出的组合 = 非法 transition（webhook 收到时 skip + log）
TRANSITIONS: dict[tuple[ReqState, Event], Transition] = {
    # ─── 主链 happy path ─────────────────────────────────────────────────
    (ReqState.INIT, Event.INTENT_ANALYZE):
        Transition(ReqState.ANALYZING, "start_analyze", "kick off"),

    (ReqState.ANALYZING, Event.ANALYZE_DONE):
        Transition(ReqState.SPECS_RUNNING, "fanout_specs", "create 2 spec issues"),

    (ReqState.SPECS_RUNNING, Event.SPEC_DONE):
        Transition(ReqState.SPECS_RUNNING, "mark_spec_reviewed_and_check", "tag + maybe gate"),

    (ReqState.SPECS_RUNNING, Event.SPEC_ALL_PASSED):
        Transition(ReqState.DEV_RUNNING, "create_dev", "SPG gate open"),

    (ReqState.DEV_RUNNING, Event.DEV_DONE):
        Transition(ReqState.STAGING_TEST_RUNNING, "create_staging_test",
                   "dev 推完，调试环境跑 unit+int"),

    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_PASS):
        Transition(ReqState.PR_CI_RUNNING, "create_pr_ci_watch", "staging 绿 → 开 PR 等 CI"),

    # M14c：fail 全部走 verifier，trigger=fail
    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_fail", "staging fail → verifier"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_PASS):
        Transition(ReqState.ACCEPT_RUNNING, "create_accept", "CI 全绿 → 转测"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_fail", "pr-ci fail → verifier"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_TIMEOUT):
        Transition(ReqState.ESCALATED, "escalate", "PR CI 未触发（repo 可能没配模板）"),

    # accept 进入前 sisyphus 内部跑 ci-accept-env-up（aissh kubectl exec）；如挂了
    # 由 action 发 ACCEPT_ENV_UP_FAIL 直 escalate
    (ReqState.ACCEPT_RUNNING, Event.ACCEPT_ENV_UP_FAIL):
        Transition(ReqState.ESCALATED, "escalate", "lab env-up 起不来"),

    (ReqState.ACCEPT_RUNNING, Event.ACCEPT_PASS):
        Transition(ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env",
                   "accept pass → 必须先清 lab 再归档"),

    (ReqState.ACCEPT_RUNNING, Event.ACCEPT_FAIL):
        Transition(ReqState.ACCEPT_TEARING_DOWN, "teardown_accept_env",
                   "accept fail → 清 lab 再走 verifier"),

    (ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS):
        Transition(ReqState.ARCHIVING, "done_archive", "teardown 完 → 归档"),

    (ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_fail",
                   "accept fail + teardown 完 → verifier"),

    # ─── M14b verifier 子链 ─────────────────────────────────────────────
    # verifier-agent 完成 → webhook 解 decision JSON → emit 对应事件。
    # 注意：VERIFY_PASS 的目标 stage 由 ctx.verifier_stage 决定 —— transition 表无法静态表达
    # next_state 随 stage 变化，所以 apply_verify_pass action 内部手工 CAS 到对应 stage_running
    # 再链式 emit 该 stage 的 done/pass 事件（走原主链 transition）。VERIFY_RETRY_CHECKER
    # 类似。此处 next_state 声明为 REVIEW_RUNNING（self-loop），实际目标状态由 action 改。
    (ReqState.REVIEW_RUNNING, Event.VERIFY_PASS):
        Transition(ReqState.REVIEW_RUNNING, "apply_verify_pass",
                   "decision=pass → action 读 stage 手动推进"),
    (ReqState.REVIEW_RUNNING, Event.VERIFY_FIX_NEEDED):
        Transition(ReqState.FIXER_RUNNING, "start_fixer",
                   "decision=fix → 起对应 fixer（dev/spec/manifest）"),
    (ReqState.REVIEW_RUNNING, Event.VERIFY_RETRY_CHECKER):
        Transition(ReqState.REVIEW_RUNNING, "apply_verify_retry_checker",
                   "decision=retry_checker → 重跑当前 stage 的 checker"),
    (ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, "escalate",
                   "verifier decision=escalate 或 schema invalid"),

    # fixer agent 完成 → 回 REVIEW_RUNNING 让 verifier 再判一次（pass / 再 fix）
    (ReqState.FIXER_RUNNING, Event.FIXER_DONE):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_after_fix",
                   "fixer 完 → 再跑 verifier 复查"),

    # ─── 终态 ───────────────────────────────────────────────────────────
    (ReqState.ARCHIVING, Event.ARCHIVE_DONE):
        Transition(ReqState.DONE, None, "REQ complete"),

    # ─── 通用错误 ───────────────────────────────────────────────────────
    # session crash 在任何 running state 都直接 escalate
    **{
        (st, Event.SESSION_FAILED): Transition(ReqState.ESCALATED, "escalate", "agent session crashed")
        for st in [
            ReqState.ANALYZING, ReqState.SPECS_RUNNING, ReqState.DEV_RUNNING,
            ReqState.STAGING_TEST_RUNNING, ReqState.PR_CI_RUNNING,
            ReqState.ACCEPT_RUNNING, ReqState.ACCEPT_TEARING_DOWN,
            ReqState.REVIEW_RUNNING, ReqState.FIXER_RUNNING,
            ReqState.ARCHIVING,
        ]
    },
}


def decide(cur_state: ReqState, event: Event) -> Transition | None:
    """主 API：给 (state, event) 查 transition。None 表示非法/忽略。"""
    return TRANSITIONS.get((cur_state, event))


def dump_transitions() -> str:
    """For docs / debugging — render full transition table as markdown."""
    rows = ["| state | event | next | action | reason |", "|---|---|---|---|---|"]
    for (st, ev), t in sorted(TRANSITIONS.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value)):
        rows.append(f"| {st.value} | {ev.value} | {t.next_state.value} | {t.action or '—'} | {t.reason or ''} |")
    return "\n".join(rows)
