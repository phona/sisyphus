"""verifier_parser.py 单元测试。

覆盖：
1. tag base64 解码（urlsafe / standard / 带 padding / 不带 padding）
2. json codeblock / plain codeblock
3. bare JSON / 嵌套在 markdown 文本中的 JSON
4. 预处理：markdown strip / 单引号修复 / 尾随逗号修复
5. retry_worthy 标记
"""
from __future__ import annotations

import base64
import json

from orchestrator.verifier_parser import (
    _extract_balanced_braces,
    _fix_common_json_syntax,
    _preprocess_json,
    _strip_markdown,
    extract_decision_robust,
)

# ─── 1. tag base64 解码 ──────────────────────────────────────────────────

def test_tag_urlsafe_base64_without_padding():
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    result = extract_decision_robust(None, [f"decision:{b64}"])
    assert result.decision == d


def test_tag_standard_base64_with_padding():
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    b64 = base64.b64encode(json.dumps(d).encode()).decode()
    result = extract_decision_robust(None, [f"decision:{b64}"])
    assert result.decision == d


def test_tag_standard_base64_without_padding():
    """标准 base64 但去掉 padding（常见 agent 输出格式）。"""
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    b64 = base64.b64encode(json.dumps(d).encode()).decode().rstrip("=")
    result = extract_decision_robust(None, [f"decision:{b64}"])
    assert result.decision == d


def test_tag_invalid_base64_falls_back():
    """tag base64 损坏时 fallback 到 description。"""
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    desc = f"```json\n{json.dumps(d)}\n```"
    result = extract_decision_robust(desc, ["decision:!!!not-base64!!!"])
    assert result.decision == d


# ─── 2. codeblock 提取 ───────────────────────────────────────────────────

def test_json_codeblock():
    d = {"action": "fix", "fixer": "dev", "scope": "src/", "reason": "bug", "confidence": "high"}
    desc = f"some text\n```json\n{json.dumps(d)}\n```\nfooter"
    result = extract_decision_robust(desc, [])
    assert result.decision == d


def test_plain_codeblock():
    d = {"action": "pass", "fixer": None}
    desc = f"```\n{json.dumps(d)}\n```"
    result = extract_decision_robust(desc, [])
    assert result.decision == d


def test_prefers_last_codeblock():
    d1 = {"action": "pass", "fixer": None}
    d2 = {"action": "escalate", "fixer": None}
    desc = f"```json\n{json.dumps(d1)}\n```\n```json\n{json.dumps(d2)}\n```"
    result = extract_decision_robust(desc, [])
    assert result.decision == d2


# ─── 3. bare JSON / markdown 文本嵌入 ────────────────────────────────────

def test_bare_json_in_text():
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    desc = f"My decision is {json.dumps(d)} because everything looks good."
    result = extract_decision_robust(desc, [])
    assert result.decision == d


def test_json_with_markdown_formatting():
    """agent 用 **bold** 包裹 JSON。"""
    d = {"action": "pass", "fixer": None}
    raw = json.dumps(d)
    desc = f"Decision: **{raw}**"
    result = extract_decision_robust(desc, [])
    assert result.decision == d


def test_json_in_markdown_bullet():
    """JSON 嵌在 markdown bullet 列表中。"""
    d = {"action": "fix", "fixer": "dev"}
    desc = f"- item 1\n- decision: {json.dumps(d)}\n- item 3"
    result = extract_decision_robust(desc, [])
    assert result.decision == d


# ─── 4. 预处理：单引号 / 尾随逗号 ────────────────────────────────────────

def test_fix_single_quotes():
    text = "{'action': 'pass', 'fixer': None}"
    fixed = _fix_common_json_syntax(text)
    assert '"action"' in fixed
    assert '"pass"' in fixed
    data = json.loads(fixed)
    assert data["action"] == "pass"


def test_fix_trailing_comma():
    text = '{"action": "pass", "fixer": None,}'
    fixed = _fix_common_json_syntax(text)
    data = json.loads(fixed)
    assert data["action"] == "pass"


def test_fix_single_quotes_and_trailing_comma():
    text = "{'action': 'pass', 'fixer': None,}"
    fixed = _fix_common_json_syntax(text)
    data = json.loads(fixed)
    assert data["action"] == "pass"


def test_preprocess_json_full():
    text = '**Decision**: `{\'action\': \'pass\', \'fixer\': None,}`'
    pre = _preprocess_json(text)
    # 预处理后大括号里的内容应该能被平衡大括号提取器找到并解析
    braces = _extract_balanced_braces(pre)
    assert len(braces) == 1
    data = json.loads(braces[0])
    assert data["action"] == "pass"


def test_extract_with_preprocessing():
    """bare JSON 含单引号和尾随逗号 → 预处理后成功解析。"""
    desc = "The result is {'action': 'pass', 'fixer': None, 'reason': 'ok', 'confidence': 'high',}"
    result = extract_decision_robust(desc, [])
    assert result.decision is not None
    assert result.decision["action"] == "pass"
    # 验证确实走了 preprocessed 路径
    sources = [a.source for a in result.attempts]
    assert "preprocessed" in sources


# ─── 5. retry_worthy ─────────────────────────────────────────────────────

def test_retry_worthy_when_action_found_but_unparseable():
    """找到了含 action 的大括号但 JSON 语法错误严重到预处理也修不好。"""
    desc = "My decision is {action: pass, fixer: None} because..."
    result = extract_decision_robust(desc, [])
    assert result.decision is None
    assert result.retry_worthy is True


def test_not_retry_worthy_when_no_action_at_all():
    """完全找不到 action 相关文本。"""
    result = extract_decision_robust("no json here", [])
    assert result.decision is None
    assert result.retry_worthy is False


def test_not_retry_worthy_when_valid_decision():
    d = {"action": "pass", "fixer": None}
    result = extract_decision_robust(json.dumps(d), [])
    assert result.decision == d
    assert result.retry_worthy is False


# ─── 6. 平衡大括号 ───────────────────────────────────────────────────────

def test_balanced_braces_nested():
    text = '{"outer": {"inner": 1}}'
    braces = _extract_balanced_braces(text)
    assert len(braces) == 1
    assert json.loads(braces[0]) == {"outer": {"inner": 1}}


def test_balanced_braces_with_string_braces():
    text = '{"code": "if (x) { return 1; }"}'
    braces = _extract_balanced_braces(text)
    assert len(braces) == 1
    assert json.loads(braces[0]) == {"code": "if (x) { return 1; }"}


def test_balanced_braces_multiple():
    text = '{"a": 1} some text {"b": 2}'
    braces = _extract_balanced_braces(text)
    assert len(braces) == 2
    assert json.loads(braces[0]) == {"a": 1}
    assert json.loads(braces[1]) == {"b": 2}


# ─── 7. attempts 记录 ────────────────────────────────────────────────────

def test_attempts_recorded():
    d = {"action": "pass", "fixer": None}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    result = extract_decision_robust("no json", [f"decision:{b64}"])
    assert result.decision == d
    assert len(result.attempts) == 1
    assert result.attempts[0].source == "tag_base64"
    assert result.attempts[0].success is True


def test_attempts_recorded_on_failure():
    result = extract_decision_robust("no json", ["decision:!!!bad!!!"])
    assert result.decision is None
    assert len(result.attempts) == 1
    assert result.attempts[0].source == "tag_base64"
    assert result.attempts[0].success is False


# ─── 8. 空输入 ───────────────────────────────────────────────────────────

def test_none_description_and_tags():
    result = extract_decision_robust(None, None)
    assert result.decision is None
    assert result.retry_worthy is False


def test_empty_description_and_tags():
    result = extract_decision_robust("", [])
    assert result.decision is None
    assert result.retry_worthy is False


# ─── 9. strip markdown ───────────────────────────────────────────────────

def test_strip_markdown_bold():
    assert _strip_markdown("**bold**") == "bold"


def test_strip_markdown_italic():
    assert _strip_markdown("_italic_") == "italic"
    assert _strip_markdown("*italic*") == "italic"


def test_strip_markdown_code():
    assert _strip_markdown("`code`") == "code"


def test_strip_markdown_header():
    assert _strip_markdown("# header") == "header"
    assert _strip_markdown("## sub") == "sub"
