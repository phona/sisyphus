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
        # 其他 issue.updated 一律忽略（避免自指 loop）
        return None

    # ─── session.failed ────────────────────────────────────────────────────
    if event_type == "session.failed":
        return Event.SESSION_FAILED

    # ─── session.completed: 按主 stage tag 分流 ───────────────────────────
    if event_type != "session.completed":
        return None  # 未知 event type

    # bugfix 优先（diagnosis tag 决定细分）
    if "bugfix" in tagset:
        if "diagnosis:spec-bug" in tagset:
            return Event.BUGFIX_SPEC_BUG
        if "diagnosis:env-bug" in tagset:
            return Event.BUGFIX_ENV_BUG
        return Event.BUGFIX_DONE

    if "test-fix" in tagset:
        return Event.TEST_FIX_DONE

    if "reviewer" in tagset:
        if "result:pass" in tagset:
            return Event.REVIEWER_PASS
        if "result:fail" in tagset:
            return Event.REVIEWER_FAIL
        return None  # 没结果 tag，忽略（agent 没正常完成）

    if "ci" in tagset:
        target = _get_target(tagset)
        if target == "unit":
            if "ci:pass" in tagset:
                return Event.CI_UNIT_PASS
            if "ci:fail" in tagset:
                return Event.CI_UNIT_FAIL
        elif target == "integration":
            if "ci:pass" in tagset:
                return Event.CI_INT_PASS
            if "ci:fail" in tagset:
                return Event.CI_INT_FAIL
        return None  # ci 没结果 tag

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
