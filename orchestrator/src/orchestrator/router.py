"""Webhook payload → Event 推断。

输入：BKD webhook payload（issue.updated / session.completed / session.failed）
输出：Event 枚举值 或 None（无映射，skip）

只做"标签 → 事件名"的翻译。不做状态判断（state.py 干），不做 action（actions/ 干）。

M14b：verifier-agent 触发后本模块负责把 decision JSON（tag 或 description 里）
翻成 VERIFY_* 事件；decision schema 校验也在这里（非法 → VERIFY_ESCALATE）。
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable

from .state import Event

log = __import__("structlog").get_logger(__name__)

REQ_ID_RE = re.compile(r"^REQ-[\w-]+$")
# M16：砍 spec 双 fanout，单 tag=spec；analyze-agent 想要多 spec 自己再开 issue
SPEC_TAGS = {"spec"}
# v0.2 新增：stage tag 用于区分 agent role
# staging-test / pr-ci / accept 都走 result:* tag 判 pass/fail

# ─── M14b verifier decision schema 校验 + 映射 ─────────────────────────────
# 3 路决策：pass / fix / escalate。砍 retry_checker（基础设施 flaky 直接 escalate
# 给人介入；sisyphus 不再机制性兜 retry —— "薄编排，不抢 AI 决定权" + 减少假阳性 retry 死循环风险）
_VALID_ACTIONS = {"pass", "fix", "escalate"}
_VALID_FIXERS = {"dev", "spec", None}
_VALID_CONFIDENCE = {"high", "low"}
_VALID_VERDICTS = {"legitimate", "test-hack", "code-lobotomy", "spec-drift", "unclear"}


def validate_decision(decision: object) -> tuple[bool, str]:
    """校验 verifier-agent 输出的 decision JSON 是否合规。

    返回 (ok, reason)。ok=False 时 reason 写入日志 + 上层按 VERIFY_ESCALATE 走。
    """
    if not isinstance(decision, dict):
        return False, "decision must be dict"
    action = decision.get("action")
    if action not in _VALID_ACTIONS:
        return False, f"invalid action: {action!r}"
    fixer = decision.get("fixer")
    if fixer not in _VALID_FIXERS:
        return False, f"invalid fixer: {fixer!r}"
    if action == "fix" and fixer is None:
        return False, "action=fix requires non-null fixer"
    if action != "fix" and fixer is not None:
        return False, f"action={action} must have null fixer"
    conf = decision.get("confidence")
    if conf not in _VALID_CONFIDENCE:
        return False, f"invalid confidence: {conf!r}"
    if not isinstance(decision.get("reason", ""), str):
        return False, "reason must be string"
    return True, ""


def validate_audit_soft(audit: dict | None) -> str | None:
    """软验证 audit 字段（M-fixer-audit）。

    返回 None 表示 OK；否则返回 warning message。
    只 log.warning，不影响 action 决策，不改 validate_decision 本体。
    """
    if audit is None:
        return None
    if not isinstance(audit, dict):
        return "audit must be dict"
    verdict = audit.get("verdict")
    if verdict not in _VALID_VERDICTS:
        return f"invalid audit verdict: {verdict!r}"
    if not isinstance(audit.get("red_flags", []), list):
        return "audit.red_flags must be list"
    if not isinstance(audit.get("files_by_category", {}), dict):
        return "audit.files_by_category must be dict"
    return None


def decision_to_event(decision: dict) -> Event:
    """合规 decision → Event。调用前必须先跑 validate_decision。"""
    action = decision["action"]
    if action == "pass":
        return Event.VERIFY_PASS
    if action == "fix":
        return Event.VERIFY_FIX_NEEDED
    return Event.VERIFY_ESCALATE


def derive_verifier_event(
    description: str | None, tags: Iterable[str] | None,
) -> tuple[Event, dict | None, str]:
    """verifier issue session.completed → (Event, decision | None, reason)。

    decision 拿不到或 schema 不合规 → 返回 (VERIFY_ESCALATE, None, reason)。
    合规 → 返回 (VERIFY_*, decision_dict, "")。

    reason 只在 escalate 时非空，供 obs / log 用。
    """
    decision = extract_decision_from_issue(description, tags)
    if decision is None:
        return Event.VERIFY_ESCALATE, None, "no decision JSON found in tag or description"
    ok, why = validate_decision(decision)
    if not ok:
        return Event.VERIFY_ESCALATE, decision, f"invalid decision: {why}"
    return decision_to_event(decision), decision, ""


def extract_decision_from_issue(
    description: str | None, tags: Iterable[str] | None,
) -> dict | None:
    """从 BKD verifier issue 提取 decision JSON。

    顺序：
    1. tags 里的 `decision:<urlsafe-base64-json>`（机器写最稳）
    2. description 里最后一个 ```json ... ``` 代码块
    3. 都拿不到 → None（webhook 按 VERIFY_ESCALATE 走）
    """
    for t in tags or []:
        if t.startswith("decision:"):
            raw = t.removeprefix("decision:")
            try:
                data = base64.urlsafe_b64decode(raw + "==").decode("utf-8")
                return json.loads(data)
            except Exception as e:
                log.warning("verifier.tag_decision_parse_failed", error=str(e))
                break   # 继续试 description
    if not description:
        return None
    # 优先：```json ... ``` 代码块（推荐）
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", description, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            return json.loads(blk)
        except Exception:
            continue
    # Fallback：纯 ``` ... ``` 无 lang 标的代码块（兼容 agent 漏写 json 标签）
    blocks = re.findall(r"```\s*(\{.*?\})\s*```", description, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            data = json.loads(blk)
            if isinstance(data, dict) and "action" in data:
                return data
        except Exception:
            continue
    # Fallback：扫所有 {...} 大括号块，找最后一个含 "action" 字段的合法 JSON
    # （兼容 agent 用 markdown bullet/bold 没用 codeblock）
    candidates = re.findall(r"\{[^{}]*\"action\"[^{}]*\}", description, flags=re.DOTALL)
    for blk in reversed(candidates):
        try:
            data = json.loads(blk)
            if isinstance(data, dict) and "action" in data:
                return data
        except Exception:
            continue
    return None


_REQUIRED_INTAKE_FIELDS = frozenset({
    "involved_repos", "business_behavior", "data_constraints",
    "edge_cases", "do_not_touch", "acceptance",
})


def extract_intake_finalized_intent(text: str | None) -> dict | None:
    """从 intake-agent 最后一条 message 提取 finalized intent JSON。

    3 层 fallback：json codeblock / plain codeblock / bare braces with key field。
    必须含 6 个 required 字段，否则返 None。
    """
    if not text:
        return None

    def _valid(data: object) -> bool:
        return isinstance(data, dict) and _REQUIRED_INTAKE_FIELDS.issubset(data.keys())

    # 1. ```json ... ``` 代码块（推荐，最稳）
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            data = json.loads(blk)
            if _valid(data):
                return data
        except Exception:
            continue

    # 2. ``` ... ``` 无 lang 标代码块
    blocks = re.findall(r"```\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            data = json.loads(blk)
            if _valid(data):
                return data
        except Exception:
            continue

    # 3. bare braces 含 "involved_repos" 关键字
    candidates = re.findall(r"\{[^{}]*\"involved_repos\"[^{}]*\}", text, flags=re.DOTALL)
    for blk in reversed(candidates):
        try:
            data = json.loads(blk)
            if _valid(data):
                return data
        except Exception:
            continue

    return None


def derive_event(event_type: str, tags: Iterable[str], result_tags_only: bool = False) -> Event | None:
    """根据 (event_type, tags) 推 Event 枚举。

    event_type: BKD webhook 的 event 字段（issue.updated / session.completed / session.failed）
    tags:       issue.tags
    """
    tagset = set(tags)

    # ─── L0: intent 入口 ──────────────────────────────────────────────────
    if event_type == "issue.updated":
        if "intent:intake" in tagset and "intake" not in tagset:
            return Event.INTENT_INTAKE
        if "intent:analyze" in tagset and "analyze" not in tagset:
            return Event.INTENT_ANALYZE
        # ─── race fallback ────────────────────────────────────────────────
        # BKD 实证：agent 有时在 session.completed 之后才 PATCH result tag，
        # 那次 session.completed 的 tags 不含 result:* → router 漏 fire 主链事件。
        # 兜底：issue.updated 看到 stage tag + result tag 的组合时也 fire 对应事件。
        # CAS 天然抗重复：状态已过 N+1 时第二次 fire 会 cas_failed skip，无副作用。
        if "intake" in tagset:
            if "result:pass" in tagset:
                return Event.INTAKE_PASS
            if "result:fail" in tagset:
                return Event.INTAKE_FAIL
        if "challenger" in tagset:
            if "result:pass" in tagset:
                return Event.CHALLENGER_PASS
            if "result:fail" in tagset:
                return Event.CHALLENGER_FAIL
        if "staging-test" in tagset:
            if "result:pass" in tagset:
                return Event.STAGING_TEST_PASS
            if "result:fail" in tagset:
                return Event.STAGING_TEST_FAIL
        if "accept" in tagset:
            if "result:pass" in tagset:
                return Event.ACCEPT_PASS
            if "result:fail" in tagset:
                return Event.ACCEPT_FAIL
        if "done-archive" in tagset and "result:pass" in tagset:
            return Event.ARCHIVE_DONE
        if "fixer" in tagset and (
            "result:pass" in tagset or "result:fail" in tagset
        ):
            return Event.FIXER_DONE
        # 其他 issue.updated 一律忽略（避免自指 loop）
        return None

    # ─── session.failed ────────────────────────────────────────────────────
    if event_type == "session.failed":
        return Event.SESSION_FAILED

    # ─── session.completed: 按主 stage tag 分流 ───────────────────────────
    if event_type != "session.completed":
        return None  # 未知 event type

    # intake-agent：result:pass / result:fail 派发；中间轮（仅 intake tag，无 result）→ None
    if "intake" in tagset:
        if "result:pass" in tagset:
            return Event.INTAKE_PASS
        if "result:fail" in tagset:
            return Event.INTAKE_FAIL
        return None

    # M14b verifier-agent：优先匹配。decision 解析需要 description，router.derive_event
    # 只能看 tag；真正解析走 webhook.py 那层的 extract_decision_from_issue。
    # 这里返 None 让 webhook fall through 到 _derive_verifier_event。
    if "verifier" in tagset:
        return None

    # M14b fixer-agent：fixer 完成 → FIXER_DONE
    if "fixer" in tagset:
        return Event.FIXER_DONE

    # M18：challenger-agent 写完 contract test → result:pass / result:fail
    if "challenger" in tagset:
        if "result:pass" in tagset:
            return Event.CHALLENGER_PASS
        if "result:fail" in tagset:
            return Event.CHALLENGER_FAIL
        return None

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
