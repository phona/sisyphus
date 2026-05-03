"""Robust verifier decision JSON parser with multi-format support and preprocessing.

任务 1/2：扩展输入格式覆盖 + schema 校验前预处理。
覆盖 tag base64（urlsafe/standard）、json codeblock、plain codeblock、
bare JSON、JSON 嵌在 markdown 文本中、base64 内联。
预处理：去除 markdown formatting、修复单引号、尾随逗号。
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

log = __import__("structlog").get_logger(__name__)


@dataclass
class ParseAttempt:
    """单次解析尝试记录（用于 metrics）。"""
    source: str          # e.g. "tag_base64", "json_codeblock", "preprocessed"
    success: bool
    detail: str          # 成功=空/简述；失败=error message
    raw: str | None = None


@dataclass
class ParseResult:
    """解析结果。"""
    decision: dict | None = None
    attempts: list[ParseAttempt] = field(default_factory=list)
    # retry_worthy=True：找到了疑似 decision 但 schema invalid，值得 follow-up 重试
    retry_worthy: bool = False


# ─── 预处理 helpers ──────────────────────────────────────────────────────

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"[_\*](.+?)[_\*](?![_\*])")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """去除常见 markdown formatting，保留文本内容。"""
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_HEADER_RE.sub(r"\1", text)
    return text


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# 匹配 JSON key 外的单引号（简单启发式：冒号前的单引号包裹的词）
_SINGLE_QUOTE_KEY_RE = re.compile(r"(?<=[{\s,])'([^']+?)'\s*:")
_SINGLE_QUOTE_STR_RE = re.compile(r"(:\s*)'([^']*?)'(?=\s*[,}\]])")


def _fix_common_json_syntax(text: str) -> str:
    """修复常见的 JSON 语法错误。

    - 单引号变双引号（key 和 string value）
    - 尾随逗号去除
    - Python None → JSON null
    - 注意：不处理无引号 key（如 {action: "pass"}），那个在 _preprocess_json 里做
    """
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    text = _SINGLE_QUOTE_KEY_RE.sub(r'"\1":', text)
    text = _SINGLE_QUOTE_STR_RE.sub(r'\1"\2"', text)
    # 独立出现的 None（JSON value 位置）→ null
    text = re.sub(r'\bNone\b', 'null', text)
    return text


def _preprocess_json(text: str) -> str:
    """完整预处理：strip markdown + fix JSON syntax。"""
    text = _strip_markdown(text)
    text = _fix_common_json_syntax(text)
    return text


# ─── 平衡大括号提取 ───────────────────────────────────────────────────────

def _extract_balanced_braces(text: str) -> list[str]:
    """从 text 中提取所有 {...} 平衡块（支持嵌套）。"""
    results: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            start = i
            depth = 1
            in_string = False
            escape = False
            i += 1
            while i < len(text) and depth > 0:
                c = text[i]
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                i += 1
            if depth == 0:
                results.append(text[start:i])
        else:
            i += 1
    return results


# ─── tag 层提取 ──────────────────────────────────────────────────────────

def _try_base64_decode(raw: str) -> str | None:
    """尝试 base64 解码（兼容 urlsafe 和 standard，自动补 padding）。"""
    # 先按原始长度补 padding
    padding_needed = (4 - len(raw) % 4) % 4
    padded = raw + "=" * padding_needed
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except Exception:
        pass
    try:
        return base64.b64decode(padded).decode("utf-8")
    except Exception:
        pass
    return None


def _extract_from_tags(tags: Iterable[str] | None) -> tuple[dict | None, list[ParseAttempt]]:
    """从 tags 里找 decision:<base64>。"""
    attempts: list[ParseAttempt] = []
    for t in tags or []:
        if not t.startswith("decision:"):
            continue
        raw = t.removeprefix("decision:")
        decoded = _try_base64_decode(raw)
        if decoded is None:
            attempts.append(ParseAttempt(
                source="tag_base64", success=False,
                detail="base64 decode failed", raw=raw[:80],
            ))
            continue
        try:
            data = json.loads(decoded)
            attempts.append(ParseAttempt(
                source="tag_base64", success=True,
                detail="parsed", raw=decoded[:200],
            ))
            return data, attempts
        except Exception as e:
            attempts.append(ParseAttempt(
                source="tag_base64", success=False,
                detail=f"json parse: {e}", raw=decoded[:200],
            ))
            break  # tag 层失败了，继续试 text 层
    return None, attempts


# ─── text 层提取 ─────────────────────────────────────────────────────────

def _extract_from_text(text: str | None) -> tuple[dict | None, list[ParseAttempt]]:
    """从 description/last_assistant_message 里提取 decision JSON。"""
    attempts: list[ParseAttempt] = []
    if not text:
        return None, attempts

    # 1. ```json ... ``` 代码块（推荐，最稳）
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            data = json.loads(blk)
            attempts.append(ParseAttempt(
                source="json_codeblock", success=True, detail="parsed", raw=blk[:200],
            ))
            return data, attempts
        except Exception as e:
            attempts.append(ParseAttempt(
                source="json_codeblock", success=False,
                detail=f"json parse: {e}", raw=blk[:200],
            ))

    # 2. 纯 ``` ... ``` 无 lang 标（兼容 agent 漏写 json 标签）
    blocks = re.findall(r"```\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for blk in reversed(blocks):
        try:
            data = json.loads(blk)
            if isinstance(data, dict) and "action" in data:
                attempts.append(ParseAttempt(
                    source="plain_codeblock", success=True, detail="parsed", raw=blk[:200],
                ))
                return data, attempts
        except Exception as e:
            attempts.append(ParseAttempt(
                source="plain_codeblock", success=False,
                detail=f"json parse: {e}", raw=blk[:200],
            ))

    # 3. bare JSON 嵌在 markdown 文本中（agent 忘加 code fence）
    # 先用平衡大括号提取所有 {...}，再找含 "action" 的
    candidates = _extract_balanced_braces(text)
    for blk in reversed(candidates):
        if '"action"' not in blk and "'action'" not in blk and "action:" not in blk:
            continue
        # 先直接试
        try:
            data = json.loads(blk)
            if isinstance(data, dict) and "action" in data:
                attempts.append(ParseAttempt(
                    source="bare_braces", success=True, detail="parsed", raw=blk[:200],
                ))
                return data, attempts
        except Exception as e:
            # 预处理后重试
            preprocessed = _preprocess_json(blk)
            if preprocessed != blk:
                try:
                    data = json.loads(preprocessed)
                    if isinstance(data, dict) and "action" in data:
                        attempts.append(ParseAttempt(
                            source="preprocessed", success=True,
                            detail="parsed after preprocessing", raw=preprocessed[:200],
                        ))
                        return data, attempts
                except Exception as e2:
                    attempts.append(ParseAttempt(
                        source="preprocessed", success=False,
                        detail=f"preprocessed json parse: {e2}", raw=preprocessed[:200],
                    ))
            else:
                attempts.append(ParseAttempt(
                    source="bare_braces", success=False,
                    detail=f"json parse: {e}", raw=blk[:200],
                ))

    return None, attempts


# ─── 兜底：plain `decision:<action>[-<fixer>]` tag ────────────────────────

# REQ-fix-verifier-decision-tag-1777812498：当 tag base64 + text JSON 双双失败时，
# 接受 verifier-agent PATCH 上来的纯文本 tag 作为兜底信号。confidence=low 标记，
# 让 dashboard 区分"agent 写齐了 JSON"vs"靠 tag 兜底推进"。
_PLAIN_TAG_TO_DECISION: dict[str, dict] = {
    "decision:pass":     {"action": "pass",     "fixer": None},
    "decision:escalate": {"action": "escalate", "fixer": None},
    "decision:retry":    {"action": "retry",    "fixer": None},
    "decision:fix-dev":  {"action": "fix",      "fixer": "dev"},
    "decision:fix-spec": {"action": "fix",      "fixer": "spec"},
}


def _extract_from_plain_decision_tag(
    tags: Iterable[str] | None,
) -> tuple[dict | None, list[ParseAttempt]]:
    """从 tags 里识别 plain `decision:<action>[-<fixer>]`。

    `decision:fix` 不带 fixer 后缀**不识别**（无法判 dev/spec，宁可让上层 escalate
    也不胡猜）；这条规则在 spec VDTF-S3 锁住。
    """
    attempts: list[ParseAttempt] = []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        spec = _PLAIN_TAG_TO_DECISION.get(t)
        if spec is None:
            continue
        synthesized = {
            **spec,
            "scope": None,
            "reason": f"orch-fallback: inferred from {t} tag",
            "confidence": "low",
        }
        attempts.append(ParseAttempt(
            source="plain_decision_tag",
            success=True,
            detail=f"synthesized from {t}",
            raw=t,
        ))
        return synthesized, attempts
    return None, attempts


# ─── 主入口 ──────────────────────────────────────────────────────────────

def extract_decision_robust(
    description: str | None, tags: Iterable[str] | None,
) -> ParseResult:
    """从 BKD verifier issue 提取 decision JSON（全格式覆盖 + 预处理）。

    返回 ParseResult，包含：
    - decision：解析到的 dict（可能 schema invalid）
    - attempts：每次尝试的记录（用于 metrics）
    - retry_worthy：True 当且仅当找到了疑似 decision 但解析/预处理仍失败
                     （webhook 据此决定是否 follow-up 重试）
    """
    result = ParseResult()

    # 1. tag 层（base64 编码的完整 JSON）
    decision, tag_attempts = _extract_from_tags(tags)
    result.attempts.extend(tag_attempts)
    if decision is not None:
        result.decision = decision
        return result

    # 2. text 层（assistant message 里的 JSON 块）
    decision, text_attempts = _extract_from_text(description)
    result.attempts.extend(text_attempts)
    if decision is not None:
        result.decision = decision
        return result

    # 3. 兜底：plain `decision:<action>[-<fixer>]` tag
    #    （REQ-fix-verifier-decision-tag-1777812498 / closes phona/sisyphus#356）
    decision, plain_tag_attempts = _extract_from_plain_decision_tag(tags)
    result.attempts.extend(plain_tag_attempts)
    if decision is not None:
        result.decision = decision
        return result

    # 4. 都没找到 → retry_worthy 看有没有"接近成功"的尝试
    # 只要有任何 attempt 的 raw 里含 action 就算 retry_worthy
    for att in result.attempts:
        if att.raw and ("action" in att.raw or "action" in (att.detail or "")):
            result.retry_worthy = True
            break

    return result
