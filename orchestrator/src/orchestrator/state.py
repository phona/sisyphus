"""状态机：REQ 全生命周期 transition table（M5：bugfix 子链简化）。

M5 变化：
- 砍 test-fix + reviewer 双链（实测 agent_quality.first_pass 最差那俩，AI Review 标"过度设计"）
- bugfix 完成回 staging-test 自验（M4 retry policy 在 counter 达阈值时 emit diagnose.needed）
- 新增 DIAGNOSE_RUNNING：轻 agent 读 bugfix 历史 + 失败栈分流
  - diagnosis:code-bug → BUGFIX_RETRY → 再起 dev-fix
  - diagnosis:spec-bug → SPEC_REWORK → escalate（spec-fix agent 本期不做）
  - diagnosis:env-bug  → BUGFIX_ENV_BUG → escalate（复用旧事件）

保留自 v0.2：
- 加 STAGING_TEST_RUNNING：dev 之后 agent 在调试环境跑 unit + integration test
- 加 PR_CI_RUNNING：PR 开了，等 GHA 全套（lint/unit/int/sonar/image-publish）全绿
- 加 ACCEPT_TEARING_DOWN：accept 完成后必跑 env-down 清 lab，保证不漏资源
- paused：req_state 表上 BOOLEAN flag，不进状态机

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
    # M6：manifest admission 发现 open_questions 非空 → 卡这里等人答
    ANALYZING_PENDING_HUMAN = "analyzing-pending-human"
    SPECS_RUNNING = "specs-running"             # contract + acceptance spec-agent
    DEV_RUNNING = "dev-running"                 # SPG gate 通过，dev-agent 只写代码
    STAGING_TEST_RUNNING = "staging-test-running"  # 调试环境 build + unit + int test
    PR_CI_RUNNING = "pr-ci-running"             # PR 已开，等 GHA 全套绿
    ACCEPT_RUNNING = "accept-running"           # env-up 完，accept-agent 跑 FEATURE-A*
    ACCEPT_TEARING_DOWN = "accept-tearing-down" # env-down 清 lab，后续按 accept_result 分流
    BUGFIX_RUNNING = "bugfix-running"           # bugfix round-N（单 dev-fix agent）
    DIAGNOSE_RUNNING = "diagnose-running"       # bugfix 反复失败 → 轻 agent 分流
    GH_INCIDENT_OPEN = "gh-incident-open"       # GitHub issue 已开，等人
    ARCHIVING = "archiving"                     # done-archive agent（合 PR 等）
    DONE = "done"                               # REQ 完成
    ESCALATED = "escalated"                     # 熔断 / session-failed / 人工止损


class Event(StrEnum):
    INTENT_ANALYZE = "intent.analyze"               # 人在 BKD 打 intent:analyze tag
    ANALYZE_DONE = "analyze.done"                   # analyze-agent 完成
    # M6：analyze manifest admission 发现 open_questions 非空 → fanout_specs 不进，回挂起
    ANALYZE_PENDING_HUMAN = "analyze.pending-human"
    SPEC_DONE = "spec.done"                         # 单个 spec-agent 完成
    SPEC_ALL_PASSED = "spec.all-passed"             # 聚合事件：N/N ci-passed
    DEV_DONE = "dev.done"                           # dev-agent push 完毕
    STAGING_TEST_PASS = "staging-test.pass"         # 调试环境测试全绿
    STAGING_TEST_FAIL = "staging-test.fail"         # 调试环境测试任一红 → bug:pre-release
    PR_CI_PASS = "pr-ci.pass"                       # GHA 全套绿（含 image-publish）
    PR_CI_FAIL = "pr-ci.fail"                       # GHA 任一红 → bug:ci
    PR_CI_TIMEOUT = "pr-ci.timeout"                 # 没收到 CI 结果（可能 repo 没配）
    ACCEPT_ENV_UP_FAIL = "accept-env-up.fail"       # lab 起不来（内部事件，create_accept 发）
    ACCEPT_PASS = "accept.pass"                     # accept-agent 跑完 FEATURE-A* 全 pass
    ACCEPT_FAIL = "accept.fail"                     # accept-agent 发现 bug → bug:post-release
    TEARDOWN_DONE_PASS = "teardown-done.pass"       # env-down 完（上一个是 accept.pass）
    TEARDOWN_DONE_FAIL = "teardown-done.fail"       # env-down 完（上一个是 accept.fail）
    BUGFIX_DONE = "bugfix.done"                     # dev-fix 改完 → 回 staging-test 重验
    BUGFIX_SPEC_BUG = "bugfix.spec-bug"             # 老 bugfix prompt 自判 spec-bug → escalate
    BUGFIX_ENV_BUG = "bugfix.env-bug"               # 老 bugfix prompt 自判 env-bug / 或 diagnose → escalate
    DIAGNOSE_NEEDED = "diagnose.needed"             # M4 retry policy：round ≥ 阈值，上 diagnose agent
    BUGFIX_RETRY = "bugfix.retry"                   # diagnose:code-bug → 再起 dev-fix
    SPEC_REWORK = "spec.rework"                     # diagnose:spec-bug → escalate（spec-fix 本期不做）
    ARCHIVE_DONE = "archive.done"
    SESSION_FAILED = "session.failed"


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

    # M6：fanout_specs 跑 admission 发现 open_questions 非空 → chained emit 挂起等人
    # (两步走：先按 ANALYZE_DONE 转到 SPECS_RUNNING 启动 fanout_specs，fanout_specs
    # 检出歧义回 emit ANALYZE_PENDING_HUMAN，SPECS_RUNNING 在此再跳挂起态)
    (ReqState.SPECS_RUNNING, Event.ANALYZE_PENDING_HUMAN):
        Transition(ReqState.ANALYZING_PENDING_HUMAN, None,
                   "open_questions pending human answer"),

    # M6：人打 resume:analyze / 重加 intent:analyze → 重跑 analyze（带新答案）
    (ReqState.ANALYZING_PENDING_HUMAN, Event.INTENT_ANALYZE):
        Transition(ReqState.ANALYZING, "start_analyze",
                   "human answered open_questions, re-kick analyze"),

    (ReqState.SPECS_RUNNING, Event.SPEC_DONE):
        Transition(ReqState.SPECS_RUNNING, "mark_spec_reviewed_and_check", "tag + maybe gate"),

    (ReqState.SPECS_RUNNING, Event.SPEC_ALL_PASSED):
        Transition(ReqState.DEV_RUNNING, "create_dev", "SPG gate open"),

    (ReqState.DEV_RUNNING, Event.DEV_DONE):
        Transition(ReqState.STAGING_TEST_RUNNING, "create_staging_test",
                   "dev 推完，调试环境跑 unit+int"),

    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_PASS):
        Transition(ReqState.PR_CI_RUNNING, "create_pr_ci_watch", "staging 绿 → 开 PR 等 CI"),

    (ReqState.STAGING_TEST_RUNNING, Event.STAGING_TEST_FAIL):
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix", "bug:pre-release"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_PASS):
        Transition(ReqState.ACCEPT_RUNNING, "create_accept", "CI 全绿 → 转测"),

    (ReqState.PR_CI_RUNNING, Event.PR_CI_FAIL):
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix", "bug:ci"),

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
                   "accept fail → 清 lab 再走 bugfix"),

    (ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS):
        Transition(ReqState.ARCHIVING, "done_archive", "teardown 完 → 归档"),

    (ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_FAIL):
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix", "bug:post-release"),

    # ─── bugfix 子链（M5 简化：单修 + 失败阈值触发 diagnose 分流）──────────
    # bugfix 改完 → 回 staging-test 自验（不再 fanout test-fix + reviewer）
    (ReqState.BUGFIX_RUNNING, Event.BUGFIX_DONE):
        Transition(ReqState.STAGING_TEST_RUNNING, "create_staging_test",
                   "bugfix 改完 → staging 重验"),

    # 老 prompt 自判 spec-bug / env-bug 直 escalate（prompt 未改，保留路径）
    (ReqState.BUGFIX_RUNNING, Event.BUGFIX_SPEC_BUG):
        Transition(ReqState.ESCALATED, "escalate", "spec-bug needs human"),
    (ReqState.BUGFIX_RUNNING, Event.BUGFIX_ENV_BUG):
        Transition(ReqState.ESCALATED, "escalate", "env-bug needs sisyphus runner fix"),

    # M4 retry policy：round ≥ 阈值时 emit diagnose.needed → 起 diagnose agent
    (ReqState.BUGFIX_RUNNING, Event.DIAGNOSE_NEEDED):
        Transition(ReqState.DIAGNOSE_RUNNING, "spawn_diagnose",
                   "多次修复失败 → 上诊断 agent 分流"),

    # diagnose 分流：
    (ReqState.DIAGNOSE_RUNNING, Event.BUGFIX_RETRY):
        Transition(ReqState.BUGFIX_RUNNING, "open_gh_and_bugfix",
                   "diagnosis:code-bug → 再起 dev-fix"),
    (ReqState.DIAGNOSE_RUNNING, Event.SPEC_REWORK):
        Transition(ReqState.ESCALATED, "escalate",
                   "diagnosis:spec-bug → escalate（spec-fix 本期不做）"),
    (ReqState.DIAGNOSE_RUNNING, Event.BUGFIX_ENV_BUG):
        Transition(ReqState.ESCALATED, "escalate",
                   "diagnosis:env-bug / unknown → escalate"),

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
            ReqState.BUGFIX_RUNNING, ReqState.DIAGNOSE_RUNNING,
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
