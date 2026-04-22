"""Webhook payload → Event 推断（取代 router/router.js 的 routeKey/resultKey 逻辑）。

输入：BKD webhook payload（issue.updated / session.completed / session.failed）
输出：Event 枚举值 或 None（无映射，skip）

只做"标签 → 事件名"的翻译。不做状态判断（state.py 干），不做 action（actions/ 干）。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .state import Event

REQ_ID_RE = re.compile(r"^REQ-[\w-]+$")
SPEC_TAGS = {"contract-spec", "acceptance-spec"}
# v0.2 新增：stage tag 用于区分 agent role
# staging-test / pr-ci / accept 都走 result:* tag 判 pass/fail


def derive_event(event_type: str, tags: Iterable[str], result_tags_only: bool = False) -> Event | None:
    """根据 (event_type, tags) 推 Event 枚举。

    event_type: BKD webhook 的 event 字段（issue.updated / session.completed / session.failed）
    tags:       issue.tags
    """
    tagset = set(tags)

    # ─── L0: intent:analyze 入口 ──────────────────────────────────────────
    if event_type == "issue.updated":
        if "intent:analyze" in tagset and "analyze" not in tagset:
            return Event.INTENT_ANALYZE
        # M6：analyzing-pending-human 下人答完 open_questions → 打 resume:analyze
        # 触发 re-kick analyze。走 INTENT_ANALYZE 事件让状态机统一入口。
        if "resume:analyze" in tagset:
            return Event.INTENT_ANALYZE
        # 其他 issue.updated 一律忽略（避免自指 loop）
        return None

    # ─── session.failed ────────────────────────────────────────────────────
    if event_type == "session.failed":
        return Event.SESSION_FAILED

    # ─── session.completed: 按主 stage tag 分流 ───────────────────────────
    if event_type != "session.completed":
        return None  # 未知 event type

    # diagnose agent（M5）：比 bugfix 先查，因为 diagnose issue 可能也带 bugfix 历史 tag
    if "diagnose" in tagset:
        if "diagnosis:code-bug" in tagset:
            return Event.BUGFIX_RETRY
        if "diagnosis:spec-bug" in tagset:
            return Event.SPEC_REWORK
        # env-bug / unknown / 无 tag 都按 env-bug 走 escalate（统一终态）
        return Event.BUGFIX_ENV_BUG

    # bugfix agent：老 prompt 自判 diagnosis:spec-bug / env-bug 直接 escalate
    if "bugfix" in tagset:
        if "diagnosis:spec-bug" in tagset:
            return Event.BUGFIX_SPEC_BUG
        if "diagnosis:env-bug" in tagset:
            return Event.BUGFIX_ENV_BUG
        return Event.BUGFIX_DONE

    # v0.2：staging-test agent 在调试环境跑 unit+int，结果带 result:pass/fail
    if "staging-test" in tagset:
        if "result:pass" in tagset:
            return Event.STAGING_TEST_PASS
        if "result:fail" in tagset:
            return Event.STAGING_TEST_FAIL
        return None

    # v0.2：pr-ci-watch agent 监听 N 个 PR 的 GHA commit statuses
    if "pr-ci" in tagset:
        if "pr-ci:pass" in tagset:
            return Event.PR_CI_PASS
        if "pr-ci:fail" in tagset:
            return Event.PR_CI_FAIL
        if "pr-ci:timeout" in tagset:
            return Event.PR_CI_TIMEOUT
        return None

    if "accept" in tagset:
        if "result:pass" in tagset:
            return Event.ACCEPT_PASS
        if "result:fail" in tagset:
            return Event.ACCEPT_FAIL
        return None

    if "done-archive" in tagset:
        return Event.ARCHIVE_DONE

    if "dev" in tagset:
        return Event.DEV_DONE

    if any(t in tagset for t in SPEC_TAGS):
        return Event.SPEC_DONE

    if "analyze" in tagset:
        return Event.ANALYZE_DONE

    return None


def extract_req_id(tags: Iterable[str], issue_number: int | None = None) -> str | None:
    """从 tags 找 REQ-xxx；找不到用 issueNumber 兜底（intent 入口场景）。"""
    for t in tags:
        if REQ_ID_RE.match(t):
            return t
    if issue_number is not None:
        return f"REQ-{issue_number}"
    return None


def _get_target(tagset: set[str]) -> str | None:
    for t in tagset:
        if t.startswith("target:"):
            return t.split(":", 1)[1]
    return None


def get_round(tags: Iterable[str]) -> int:
    """解析 round-N tag。"""
    for t in tags:
        if t.startswith("round-"):
            try:
                return int(t.removeprefix("round-"))
            except ValueError:
                continue
    return 0


def get_parent_id(tags: Iterable[str]) -> str | None:
    """解析 parent-id:xxx tag。"""
    for t in tags:
        if t.startswith("parent-id:"):
            return t.removeprefix("parent-id:")
    return None


def get_parent_stage(tags: Iterable[str]) -> str | None:
    """解析 parent:xxx tag（不是 parent-id）。"""
    for t in tags:
        if t.startswith("parent:") and not t.startswith("parent-id:"):
            return t.removeprefix("parent:")
    return None
