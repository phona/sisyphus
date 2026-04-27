"""状态机：REQ 全生命周期 transition table（纯编排引擎，不执行工作）。

架构变化（通用编排引擎）：
- 砍 SPECS_RUNNING / DEV_RUNNING：分别为并行 issue 聚合的 fanout 阶段
- 砍 fanout_specs / fanout_dev / mark_spec_reviewed_and_check / mark_dev_reviewed_and_check
- 砍 SPEC_DONE / SPEC_ALL_PASSED / DEV_DONE / DEV_ALL_PASSED 事件（动态聚合逻辑）
- 加 SPEC_LINT_RUNNING / DEV_CROSS_CHECK_RUNNING：sisyphus 下发的客观 checker 任务
- sisyphus 不执行工作，只根据 webhook 事件推进状态；所有工作由 BKD agent 或 runner pod 执行
- 支持通过 init:STATE BKD tag 在任意状态初始化 REQ（中流注入其他工作流）
- stage_runs / verifier_decisions 表驱动指标优化：高通过率的 stage 可砍掉

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
    INIT = "init"                               # 还没 analyze / 待初始化
    INTAKING = "intaking"                       # intake-agent 在跑（多轮 BKD chat 澄清 + 写 finalized intent）
    ANALYZING = "analyzing"                     # analyze-agent 在跑
    ANALYZE_ARTIFACT_CHECKING = "analyze-artifact-checking"  # 机械校 analyze 产物（proposal/tasks/spec.md 存在 + 非空）
    SPEC_LINT_RUNNING = "spec-lint-running"     # openspec validate 检查（sisyphus 下发 runner 任务）
    CHALLENGER_RUNNING = "challenger-running"   # M18：challenger-agent 读 spec 写 contract test（黑盒，不看 dev 代码）
    DEV_CROSS_CHECK_RUNNING = "dev-cross-check-running"  # 开发交叉验证（sisyphus 下发 runner 任务）
    STAGING_TEST_RUNNING = "staging-test-running"  # 调试环境 build + unit + int test
    PR_CI_RUNNING = "pr-ci-running"             # PR 已开，等 GHA 全套绿
    ACCEPT_RUNNING = "accept-running"           # env-up 完，accept-agent 跑 FEATURE-A*
    ACCEPT_TEARING_DOWN = "accept-tearing-down" # env-down 清 lab，后续按 accept_result 分流
    GH_INCIDENT_OPEN = "gh-incident-open"       # GitHub issue 已开，等人
    ARCHIVING = "archiving"                     # done-archive agent（合 PR 等）
    # verifier-agent 框架
    REVIEW_RUNNING = "review-running"           # verifier-agent 在跑（success / fail 两触发统一入口）
    FIXER_RUNNING = "fixer-running"             # verifier decision=fix → 起对应 fixer agent（dev/spec）
    DONE = "done"                               # REQ 完成
    ESCALATED = "escalated"                     # 熔断 / session-failed / 人工止损


class Event(StrEnum):
    INTENT_INTAKE = "intent.intake"                 # 人在 BKD 打 intent:intake tag → 起 intake-agent 澄清需求
    INTAKE_PASS = "intake.pass"                     # intake-agent 完 + finalized intent JSON ok
    INTAKE_FAIL = "intake.fail"                     # intake-agent 异常 / 用户放弃
    INTENT_ANALYZE = "intent.analyze"               # 人在 BKD 打 intent:analyze tag（旧入口，现支持 init:STATE）
    ANALYZE_DONE = "analyze.done"                   # analyze-agent 完成
    ANALYZE_ARTIFACT_CHECK_PASS = "analyze-artifact-check.pass"   # 机械校 analyze 产物（proposal/tasks/spec.md）通过
    ANALYZE_ARTIFACT_CHECK_FAIL = "analyze-artifact-check.fail"   # 机械校 analyze 产物失败 → verifier
    SPEC_LINT_PASS = "spec-lint.pass"               # openspec validate 通过
    SPEC_LINT_FAIL = "spec-lint.fail"               # openspec validate 失败 → verifier
    CHALLENGER_PASS = "challenger.pass"             # M18：challenger 写完 contract test 推 feat 分支
    CHALLENGER_FAIL = "challenger.fail"             # M18：challenger 写失败 / 拒绝（spec 自相矛盾等）→ verifier
    DEV_CROSS_CHECK_PASS = "dev-cross-check.pass"   # 开发交叉验证通过
    DEV_CROSS_CHECK_FAIL = "dev-cross-check.fail"   # 开发交叉验证失败 → verifier
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
    # verifier-agent 决策事件（webhook.py 从 verifier issue 的 decision JSON 派发）
    # 3 路决策：pass / fix / escalate（retry_checker 已砍 —— flaky/外部抖动直接 escalate）
    VERIFY_PASS = "verify.pass"                     # decision.action = pass → 推下一 stage
    VERIFY_FIX_NEEDED = "verify.fix-needed"         # decision.action = fix → 起 fixer agent
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
    # intake → analyze 两阶段物理隔离：intent:intake tag 走 INTAKING，跳过直接用 intent:analyze
    (ReqState.INIT, Event.INTENT_INTAKE):
        Transition(ReqState.INTAKING, "start_intake",
                   "intent:intake → 启动澄清 agent，brainstorm + finalize intent"),

    (ReqState.INTAKING, Event.INTAKE_PASS):
        Transition(ReqState.ANALYZING, "start_analyze_with_finalized_intent",
                   "intake done → analyze 接力（新 BKD issue，嵌入 finalized intent）"),

    (ReqState.INTAKING, Event.INTAKE_FAIL):
        Transition(ReqState.ESCALATED, "escalate", "intake failed / 用户放弃"),

    (ReqState.INIT, Event.INTENT_ANALYZE):
        Transition(ReqState.ANALYZING, "start_analyze", "kick off"),

    # start_analyze 内部判 escalate（如 clone_involved_repos 失败 → emit VERIFY_ESCALATE）
    # 没这条 transition 会被 engine.illegal_transition 吞掉，REQ 卡 ANALYZING 60min
    # 才靠 watchdog auto_resume，浪费一轮 BKD agent token；实证 2026-04-26 REQ-ttpos-pat-validate。
    (ReqState.ANALYZING, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, "escalate",
                   "start_analyze 内部判 escalate（clone failed 等）"),

    # 同理 start_analyze_with_finalized_intent (INTAKING → ANALYZING via INTAKE_PASS) 内部
    # 也可能 emit VERIFY_ESCALATE（intent 缺字段 / clone failed）。补 INTAKING 那条避免漏。
    (ReqState.INTAKING, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, "escalate",
                   "start_analyze_with_finalized_intent 内部判 escalate"),

    # REQ-analyze-artifact-check-1777254586：analyze done 后先机械校 proposal/tasks/spec.md
    # 是否真存在 + 非空，再放进 spec_lint。防 agent 自报 pass 但产物全空。
    (ReqState.ANALYZING, Event.ANALYZE_DONE):
        Transition(ReqState.ANALYZE_ARTIFACT_CHECKING, "create_analyze_artifact_check",
                   "下发 analyze 产物结构性检查（proposal.md / tasks.md / spec.md 存在 + 非空）"),

    (ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_PASS):
        Transition(ReqState.SPEC_LINT_RUNNING, "create_spec_lint",
                   "analyze 产物齐 → 下发 openspec validate 任务"),

    (ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_analyze_artifact_check_fail",
                   "analyze 产物不全 → verifier"),

    (ReqState.SPEC_LINT_RUNNING, Event.SPEC_LINT_PASS):
        Transition(ReqState.CHALLENGER_RUNNING, "start_challenger",
                   "spec lint 通过 → 起 challenger 写 contract test (M18)"),

    (ReqState.SPEC_LINT_RUNNING, Event.SPEC_LINT_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_spec_lint_fail",
                   "spec lint 失败 → verifier"),

    (ReqState.CHALLENGER_RUNNING, Event.CHALLENGER_PASS):
        Transition(ReqState.DEV_CROSS_CHECK_RUNNING, "create_dev_cross_check",
                   "challenger 写完 contract test → 开发交叉验证"),

    (ReqState.CHALLENGER_RUNNING, Event.CHALLENGER_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_challenger_fail",
                   "challenger 失败（spec 自相矛盾 / 写不出 test 等）→ verifier 判"),

    (ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_PASS):
        Transition(ReqState.STAGING_TEST_RUNNING, "create_staging_test",
                   "开发交叉验证通过 → 调试环境测试"),

    (ReqState.DEV_CROSS_CHECK_RUNNING, Event.DEV_CROSS_CHECK_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_dev_cross_check_fail",
                   "开发交叉验证失败 → verifier"),

    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_PASS):
        Transition(ReqState.PR_CI_RUNNING, "create_pr_ci_watch", "staging 绿 → 开 PR 等 CI"),

    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_staging_test_fail",
                   "staging fail → verifier"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_PASS):
        Transition(ReqState.ACCEPT_RUNNING, "create_accept", "CI 全绿 → 转测"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_FAIL):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_pr_ci_fail",
                   "pr-ci fail → verifier"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_TIMEOUT):
        Transition(ReqState.ESCALATED, "escalate", "PR CI 未触发（repo 可能没配模板）"),

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
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_for_accept_fail",
                   "accept fail + teardown 完 → verifier"),

    # ─── verifier 子链 ─────────────────────────────────────────────────
    # verifier-agent 完成 → webhook 解 decision JSON → emit 对应事件。
    # 注意：VERIFY_PASS 的目标 stage 由 ctx.verifier_stage 决定 —— transition 表无法静态表达
    # next_state 随 stage 变化，所以 apply_verify_pass action 内部手工 CAS 到对应 stage_running
    # 再链式 emit 该 stage 的 done/pass 事件（走原主链 transition）。
    # 3 路决策：pass / fix / escalate（retry_checker 已砍 —— 基础设施 flaky 由 verifier 自己判
    # escalate 给人，sisyphus 不再机制性兜 retry，避免假阳性 retry 死循环）。
    (ReqState.REVIEW_RUNNING, Event.VERIFY_PASS):
        Transition(ReqState.REVIEW_RUNNING, "apply_verify_pass",
                   "decision=pass → action 读 stage 手动推进"),
    (ReqState.REVIEW_RUNNING, Event.VERIFY_FIX_NEEDED):
        Transition(ReqState.FIXER_RUNNING, "start_fixer",
                   "decision=fix → 起对应 fixer（dev/spec）"),
    (ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, "escalate",
                   "verifier decision=escalate 或 schema invalid"),

    (ReqState.FIXER_RUNNING, Event.FIXER_DONE):
        Transition(ReqState.REVIEW_RUNNING, "invoke_verifier_after_fix",
                   "fixer 完 → 再跑 verifier 复查"),

    # start_fixer 自检 fixer_round 超 cap → 走主链 escalate（不跑新 fixer）。
    # 复用 escalate action 把 reason / tag / runner 清理收口，避免 start_fixer 内
    # 自己开第二条 escalate 实现。
    (ReqState.FIXER_RUNNING, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, "escalate",
                   "fixer round 触顶 / start_fixer 自判 escalate"),

    # ─── 终态 ───────────────────────────────────────────────────────────
    (ReqState.ARCHIVING, Event.ARCHIVE_DONE):
        Transition(ReqState.DONE, None, "REQ complete"),

    # ─── 人工恢复（escalate ≠ 死终态）─────────────────────────────────────
    # 用户在 BKD UI follow-up 那个 escalate 的 verifier issue → BKD wake agent →
    # 写新 decision JSON → session.completed 走原来 verifier 同一套链路 → 命中下面 3 条。
    # 复用 apply_verify_pass / start_fixer，它们的 CAS source 同时接 REVIEW_RUNNING + ESCALATED。
    # 配套：webhook.py 把 verifier-decision=escalate 的 issue PATCH 到 BKD statusId="review"
    # （而非 done），用户在 BKD 看板"待审查"列就能定位到该 follow-up 哪条。
    (ReqState.ESCALATED, Event.VERIFY_PASS):
        Transition(ReqState.ESCALATED, "apply_verify_pass",
                   "用户续 escalate 的 verifier issue → 新 decision=pass → action 推下一 stage"),
    (ReqState.ESCALATED, Event.VERIFY_FIX_NEEDED):
        Transition(ReqState.FIXER_RUNNING, "start_fixer",
                   "用户续 escalate 的 verifier issue → 新 decision=fix → 起 fixer"),
    (ReqState.ESCALATED, Event.VERIFY_ESCALATE):
        Transition(ReqState.ESCALATED, None,
                   "用户续了但 verifier 还是判 escalate → 留原地等下一次 follow-up"),

    # ─── 通用错误 ───────────────────────────────────────────────────────
    # session crash 在任何 running state 走 escalate action（self-loop, action 内部决定是否真 escalate）
    # escalate action 现支持 auto-resume：
    #   transient + retry < 2 → BKD follow-up "continue"，state 不动等 BKD wake agent 续
    #   retry 用完 / non-transient → action 内部手 CAS 推到 ESCALATED
    # next_state 写当前 state 是因为"action 自己决定是否真 escalate"，跟 apply_verify_pass 同模式
    **{
        (st, Event.SESSION_FAILED): Transition(st, "escalate", "session crash → auto-resume or escalate")
        for st in [
            ReqState.INTAKING, ReqState.ANALYZING,
            ReqState.ANALYZE_ARTIFACT_CHECKING,
            ReqState.SPEC_LINT_RUNNING, ReqState.CHALLENGER_RUNNING,
            ReqState.DEV_CROSS_CHECK_RUNNING,
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
