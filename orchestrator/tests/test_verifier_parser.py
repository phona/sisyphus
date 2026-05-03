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


# ─── 8a. VDTF-S1: 渲染后的 verifier prompt 包含 mandate 段 ─────────────────


def _verifier_stage_trigger_pairs() -> list[tuple[str, str]]:
    """枚举所有 verifier/{stage}_{trigger}.md.j2 模板。"""
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts" / "verifier"
    out: list[tuple[str, str]] = []
    for p in sorted(here.glob("*_*.md.j2")):
        if p.name.startswith("_"):
            continue
        # 文件名形如 staging_test_fail.md.j2 / accept_success.md.j2
        stem = p.name.removesuffix(".md.j2")
        # 找最后一个 _success/_fail 切分
        for suffix in ("_success", "_fail"):
            if stem.endswith(suffix):
                out.append((stem.removesuffix(suffix), suffix.lstrip("_")))
                break
    return out


def test_vdtf_s1_every_verifier_prompt_mandates_decision_tag():
    """VDTF-S1: 任何 stage/trigger 渲染后都必须含 decision:<action> tag mandate 段。"""
    from orchestrator.prompts import render

    pairs = _verifier_stage_trigger_pairs()
    assert pairs, "no verifier prompts discovered"

    for stage, trigger in pairs:
        rendered = render(
            f"verifier/{stage}_{trigger}.md.j2",
            req_id="REQ-test-1",
            stage=stage,
            trigger=trigger,
            artifact_paths=[],
            stderr_tail="",
            history=[],
            project_id="proj-x",
            project_alias="proj-x",
            checker_stdout="",
            checker_stderr="",
            checker_exit_code=None,
        )
        # 必须含至少一个 `decision:<action>` 字面 example
        examples = [
            "decision:pass", "decision:fix-dev", "decision:fix-spec",
            "decision:escalate", "decision:retry",
        ]
        assert any(ex in rendered for ex in examples), (
            f"{stage}/{trigger}: rendered prompt lacks any decision:<action> tag example"
        )
        # 必须含 curl PATCH 例子
        assert "curl" in rendered and "PATCH" in rendered, (
            f"{stage}/{trigger}: rendered prompt lacks curl PATCH tag-merge example"
        )
        # 必须含 hard constraint 提示词（区分于纯 UX 旧文本）
        assert "HARD CONSTRAINT" in rendered, (
            f"{stage}/{trigger}: rendered prompt lacks HARD CONSTRAINT marker for decision tag"
        )


# ─── 8b. plain `decision:<action>[-<fixer>]` 兜底 tag ────────────────────
# REQ-fix-verifier-decision-tag-1777812498：spec VDTF-S2 / VDTF-S3。

def test_plain_decision_tag_pass_synthesizes_low_confidence():
    """VDTF-S2: plain `decision:pass` tag + 无 JSON → 兜底合成 low-confidence pass。"""
    from orchestrator.router import validate_decision

    result = extract_decision_robust(
        description="some chatter without any JSON block",
        tags=["verifier", "verify:staging_test", "decision:pass"],
    )
    assert result.decision is not None
    assert result.decision["action"] == "pass"
    assert result.decision["fixer"] is None
    assert result.decision["confidence"] == "low"
    assert result.decision["reason"].startswith("orch-fallback")
    ok, _ = validate_decision(result.decision)
    assert ok is True


def test_plain_decision_tag_fix_dev_sets_fixer():
    from orchestrator.router import validate_decision

    result = extract_decision_robust(
        description=None,
        tags=["verifier", "decision:fix-dev"],
    )
    assert result.decision == {
        "action": "fix",
        "fixer": "dev",
        "scope": None,
        "reason": "orch-fallback: inferred from decision:fix-dev tag",
        "confidence": "low",
    }
    ok, _ = validate_decision(result.decision)
    assert ok is True


def test_plain_decision_tag_fix_spec_sets_fixer():
    from orchestrator.router import validate_decision

    result = extract_decision_robust(
        description=None,
        tags=["decision:fix-spec"],
    )
    assert result.decision["action"] == "fix"
    assert result.decision["fixer"] == "spec"
    ok, _ = validate_decision(result.decision)
    assert ok is True


def test_plain_decision_tag_escalate_and_retry():
    for tag, expected in (("decision:escalate", "escalate"), ("decision:retry", "retry")):
        result = extract_decision_robust(None, [tag])
        assert result.decision is not None, tag
        assert result.decision["action"] == expected
        assert result.decision["fixer"] is None
        assert result.decision["confidence"] == "low"


def test_plain_decision_fix_without_fixer_suffix_is_not_synthesized():
    """VDTF-S3: bare `decision:fix` 不带 -dev/-spec → 不合成（不胡猜 fixer）。"""
    from orchestrator.router import validate_decision

    result = extract_decision_robust(
        description=None,
        tags=["verifier", "decision:fix"],
    )
    assert result.decision is None
    ok, _ = validate_decision(result.decision)
    assert ok is False


def test_json_block_takes_precedence_over_plain_tag():
    """JSON 主路径仍优先；plain tag 只在 JSON 失败时兜底。"""
    d = {"action": "fix", "fixer": "dev", "scope": "src/", "reason": "real",
         "confidence": "high"}
    result = extract_decision_robust(
        description=f"```json\n{json.dumps(d)}\n```",
        tags=["decision:escalate"],   # 故意冲突：tag 想 escalate，JSON 想 fix
    )
    assert result.decision["action"] == "fix"
    assert result.decision["confidence"] == "high"
    assert "orch-fallback" not in result.decision["reason"]


def test_base64_tag_takes_precedence_over_plain_tag():
    """tag base64 仍优先于 plain tag（同一字段位置不破坏老 agent）。"""
    d = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    result = extract_decision_robust(
        description=None,
        tags=[f"decision:{b64}", "decision:escalate"],
    )
    assert result.decision == d


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
