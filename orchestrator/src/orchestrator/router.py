"""Webhook payload → Event 推断。

输入：BKD webhook payload（issue.updated / session.completed / session.failed）
输出：Event 枚举值 或 None（无映射，skip）

只做"标签 → 事件名"的翻译。不做状态判断（state.py 干），不做 action（actions/ 干）。

M14b：verifier-agent 触发后本模块负责把 decision JSON（tag 或 description 里）
翻成 VERIFY_* 事件；decision schema 校验也在这里（非法 → VERIFY_ESCALATE）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

from .state import Event
from .verifier_parser import extract_decision_robust

log = __import__("structlog").get_logger(__name__)

REQ_ID_RE = re.compile(r"^REQ-[\w-]+$")
# M16：砍 spec 双 fanout，单 tag=spec；execute-agent 想要多 spec 自己再开 issue
SPEC_TAGS = {"spec"}
# v0.2 新增：stage tag 用于区分 agent role
# staging-test / pr-ci / accept 都走 result:* tag 判 pass/fail

# ─── M14b verifier decision schema 校验 + 映射 ─────────────────────────────
# 4 路决策：pass / fix / escalate / retry（infra-flake 有界重跑）
_VALID_ACTIONS = {"pass", "fix", "escalate", "retry"}
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
    if action in ("pass", "escalate", "retry") and fixer is not None:
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


# verifier decision=pass 时 stage → 对应主链 pass 事件
#（REQ-refactor-verify-pass-transition-1777727230：把 apply_verify_pass 自循环拆成
# 显式 transition，router 负责在 decision 解析层做事件映射）。
_VERIFY_PASS_ROUTING: dict[str, Event] = {
    "execute":                  Event.EXECUTE_DONE,
    "execute_artifact_check":   Event.EXECUTE_ARTIFACT_CHECK_PASS,
    # REQ-refactor-analyze-execute-392 read-compat：历史 verifier issue 仍用
    # 老 stage 名 `analyze` / `analyze_artifact_check`，1-2 周后清理。
    "analyze":                  Event.EXECUTE_DONE,
    "analyze_artifact_check":   Event.EXECUTE_ARTIFACT_CHECK_PASS,
    "spec_lint":                Event.SPEC_LINT_PASS,
    "challenger":               Event.CHALLENGER_PASS,
    "dev_cross_check":          Event.DEV_CROSS_CHECK_PASS,
    "staging_test":             Event.STAGING_TEST_PASS,
    "pr_ci":                    Event.PR_CI_PASS,
    "accept":                   Event.ACCEPT_PASS,
}


def pass_event_for_stage(stage: str | None) -> Event | None:
    """verifier decision=pass 时，按 stage 返回对应主链 pass 事件。

    None 表示 stage 不在已知路由表（应 escalate）。
    """
    return _VERIFY_PASS_ROUTING.get(stage) if stage else None


def decision_to_event(decision: dict, stage: str | None = None) -> Event:
    """合规 decision → Event。调用前必须先跑 validate_decision。

    stage 只在 action=pass 时使用；缺省或未知 stage 时回退 VERIFY_PASS
   （调用方应检查 None 并 escalate）。
    """
    action = decision["action"]
    if action == "pass":
        return pass_event_for_stage(stage) or Event.VERIFY_PASS
    if action == "fix":
        return Event.VERIFY_FIX_NEEDED
    if action == "retry":
        return Event.VERIFY_INFRA_RETRY
    return Event.VERIFY_ESCALATE


def derive_verifier_event(
    description: str | None, tags: Iterable[str] | None,
) -> tuple[Event, dict | None, str]:
    """verifier issue session.completed → (Event, decision | None, reason)。

    decision 拿不到或 schema 不合规 → 返回 (VERIFY_ESCALATE, None, reason)。
    合规 → 返回 (VERIFY_*, decision_dict, "")。

    reason 只在 escalate 时非空，供 obs / log 用。
    """
    event, decision, reason, _ = derive_verifier_event_with_retry_info(description, tags)
    return event, decision, reason


def _stage_from_tags(tags: Iterable[str] | None) -> str | None:
    for t in (tags or []):
        if t.startswith("verify:"):
            return t.removeprefix("verify:")
    return None


def derive_verifier_event_with_retry_info(
    description: str | None, tags: Iterable[str] | None,
) -> tuple[Event, dict | None, str, bool]:
    """verifier issue session.completed → (Event, decision, reason, retry_worthy)。

    retry_worthy=True：找到了疑似 decision 但 schema invalid / 无法解析，
    webhook 层据此 follow-up 要求 agent 重新输出标准格式（最多 retry 2 次）。
    """
    decision = extract_decision_from_issue(description, tags)
    if decision is None:
        # 用 robust parser 的 retry_worthy 判断
        result = extract_decision_robust(description, tags)
        if result.retry_worthy:
            return (
                Event.VERIFY_ESCALATE, None,
                "decision-like text found but unparseable", True,
            )
        return (
            Event.VERIFY_ESCALATE, None,
            "no decision JSON found in tag or description", False,
        )
    ok, why = validate_decision(decision)
    if not ok:
        return Event.VERIFY_ESCALATE, decision, f"invalid decision: {why}", True
    stage = _stage_from_tags(tags)
    event = decision_to_event(decision, stage=stage)
    if event == Event.VERIFY_PASS:
        return Event.VERIFY_ESCALATE, decision, f"unknown verifier stage: {stage!r}", False
    return event, decision, "", False


def extract_decision_from_issue(
    description: str | None, tags: Iterable[str] | None,
) -> dict | None:
    """从 BKD verifier issue 提取 decision JSON（全格式覆盖 + 预处理）。

    顺序：
    1. tags 里的 `decision:<base64-json>`（兼容 urlsafe/standard）
    2. description 里 ```json ... ``` / ``` ... ``` 代码块
    3. bare JSON / 嵌在 markdown 文本中的 JSON（含预处理修复）
    """
    result = extract_decision_robust(description, tags)
    return result.decision


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


def derive_event(event_type: str, tags: Iterable[str]) -> Event | None:
    """根据 (event_type, tags) 推 Event 枚举。

    event_type: BKD webhook 的 event 字段（issue.updated / session.completed / session.failed）
    tags:       issue.tags
    """
    tagset = set(tags)

    # ─── L0: intent 入口 ──────────────────────────────────────────────────
    # REQ-refactor-analyze-execute-392 兼容：intent:analyze / analyze 是历史 tag
    # 名（stage 改名 execute 之前已存在的 BKD issue 上）。读路径双识别 1-2 周后
    # 清理；写路径只用新 intent:execute / execute / verify:execute。
    if event_type == "issue.updated":
        if "intent:intake" in tagset and "intake" not in tagset:
            return Event.INTENT_INTAKE
        if (
            ("intent:execute" in tagset or "intent:analyze" in tagset)
            and "execute" not in tagset
            and "analyze" not in tagset
        ):
            return Event.INTENT_EXECUTE
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

    # REQ-refactor-analyze-execute-392 read-compat：历史 BKD issue 上还可能挂
    # 老 `analyze` tag。新 stage agent issue 由 sisyphus 写 `execute`。
    if "execute" in tagset or "analyze" in tagset:
        return Event.EXECUTE_DONE

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


# ─── base:* tag 解析（REQ-base-branch-override-1777480690）───────────────────

_BASE_TAG_PREFIX = "base:"
# repo basename 规则：字母数字开头，后续可跟字母数字 . _ -
_REPO_BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def extract_base_branches(
    tags: Iterable[str],
    finalized_intent: dict | None = None,
) -> tuple[str | None, dict[str, str]]:
    """解析 BKD intent issue 上的 base:* tag。

    支持两种语法：
    - ``base:<branch>`` — 默认 base 分支（单仓 / 全仓默认）
    - ``base:<repo-basename>:<branch>`` — per-repo override

    额外支持从 ``finalized_intent`` 读取（intake-agent 在 chat 中理解用户意图后写入），
    作为 tag 缺失时的 fallback。优先级：tag > finalized_intent。

    返回 ``(default_branch, repo_overrides)``。
    没任何 ``base:*`` tag 或 finalized_intent 时返 ``(None, {})``（向后兼容）。
    """
    default: str | None = None
    overrides: dict[str, str] = {}

    # 1. 先从 BKD tag 解析（最高优先级）
    for t in tags or []:
        if not isinstance(t, str) or not t.startswith(_BASE_TAG_PREFIX):
            continue
        rest = t[len(_BASE_TAG_PREFIX):]
        if not rest:
            continue
        if ":" in rest:
            parts = rest.split(":", 1)
            maybe_repo = parts[0]
            if _REPO_BASE_RE.match(maybe_repo):
                overrides[maybe_repo] = parts[1]
                continue
        default = rest

    # 2. tag 没命中时 fallback 到 finalized intent（intake-agent 理解产物）
    if finalized_intent:
        default = default or finalized_intent.get("base_branch")
        for repo, branch in (finalized_intent.get("base_branches") or {}).items():
            if repo not in overrides:
                overrides[repo] = branch

    return default, overrides


def resolve_base_branch(
    repo_slug: str,
    default_base: str | None,
    base_overrides: dict[str, str],
) -> str | None:
    """给定 repo slug，解析应使用的 base branch。

    优先顺序：
    1. ``base_overrides[repo_basename]``（per-repo 显式指定）
    2. ``default_base``（全局默认）
    3. ``None``（无显式指定，走 origin/HEAD 兜底）
    """
    basename = repo_slug.rsplit("/", 1)[-1] if "/" in repo_slug else repo_slug
    return base_overrides.get(basename) or default_base or None


def normalize_base_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """把 ``base_overrides`` 的 key 统一归一到 repo basename。

    ``settings.default_base_branches`` 在 helm values 里通常写成
    ``{phona/sisyphus: main}`` —— 用 ``<owner>/<repo>`` 形式，因为这是 ops
    最自然的写法。但 ``resolve_base_branch`` 跟下游的 ``sisyphus-clone-repos.sh``
    都按 basename 查找，混用 owner/repo 跟 basename 形式会让 per-repo override
    永远不命中（GitHub issue #345）。

    本函数把所有 key 归一到 basename，并去掉 ``.git`` 后缀。冲突时（同一 basename
    有多个 owner/repo 写法）取最后写入者，这跟 dict 自身的覆盖语义一致。
    """
    out: dict[str, str] = {}
    for key, value in overrides.items():
        basename = key.rsplit("/", 1)[-1] if "/" in key else key
        if basename.endswith(".git"):
            basename = basename[:-4]
        if basename:
            out[basename] = value
    return out
