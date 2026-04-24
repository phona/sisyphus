"""validate_audit_soft：soft 校验 audit 字段（fixer-audit REQ）。

覆盖：
1. 5 个合法 verdict 枚举全通过
2. 非法枚举 → 返回 warning message（不影响 action 决策，不 escalate）
3. files_by_category 非 dict → 返回 warning message
4. validate_decision 不受 audit 字段影响（5 字段老 decision 仍合法）
5. audit=None → None（first-time verify 兼容）
"""
from __future__ import annotations

import pytest

from orchestrator.router import validate_audit_soft, validate_decision

# ─── 1. 5 个合法 verdict ────────────────────────────────────────────────────

@pytest.mark.parametrize("verdict", [
    "legitimate",
    "test-hack",
    "code-lobotomy",
    "spec-drift",
    "unclear",
])
def test_valid_verdicts_return_none(verdict):
    audit = {
        "diff_summary": "src=+5/-2 tests=+3/-0",
        "verdict": verdict,
        "red_flags": [],
        "files_by_category": {"src": 2, "tests": 1, "spec": 0, "config": 0},
    }
    assert validate_audit_soft(audit) is None


# ─── 2. 非法 verdict → warning message，不 escalate ─────────────────────────

@pytest.mark.parametrize("bad_verdict", [
    "hack",
    "pass",
    "unknown",
    "",
    None,
    123,
])
def test_invalid_verdict_returns_warning(bad_verdict):
    audit = {
        "diff_summary": "src=+1/-0",
        "verdict": bad_verdict,
        "red_flags": [],
        "files_by_category": {"src": 1, "tests": 0, "spec": 0, "config": 0},
    }
    result = validate_audit_soft(audit)
    assert result is not None
    assert "verdict" in result.lower()


# ─── 3. files_by_category 非 dict → warning message ────────────────────────

@pytest.mark.parametrize("bad_fbc", [
    "not-a-dict",
    ["src", "tests"],
    42,
])
def test_files_by_category_non_dict_returns_warning(bad_fbc):
    audit = {
        "diff_summary": "src=+1/-0",
        "verdict": "legitimate",
        "red_flags": [],
        "files_by_category": bad_fbc,
    }
    result = validate_audit_soft(audit)
    assert result is not None
    assert "files_by_category" in result.lower()


# ─── 4. validate_decision 不受 audit 字段影响 ────────────────────────────────

def test_validate_decision_unaffected_by_audit_fields():
    """5 字段老 decision 仍合法；多出 audit 字段也合法（schema 宽松）。"""
    old_decision = {
        "action": "pass",
        "fixer": None,
        "scope": None,
        "reason": "all good",
        "confidence": "high",
    }
    ok, reason = validate_decision(old_decision)
    assert ok is True
    assert reason == ""

    decision_with_audit = {
        **old_decision,
        "diff_summary": "src=+5/-2",
        "verdict": "legitimate",
        "red_flags": [],
        "files_by_category": {"src": 2, "tests": 1, "spec": 0, "config": 0},
    }
    ok2, reason2 = validate_decision(decision_with_audit)
    assert ok2 is True
    assert reason2 == ""


# ─── 5. audit=None → None（first-time verify，backward compat）───────────────

def test_audit_none_returns_none():
    assert validate_audit_soft(None) is None


# ─── 6. audit 非 dict → warning ──────────────────────────────────────────────

def test_audit_not_dict_returns_warning():
    assert validate_audit_soft("string") is not None
    assert validate_audit_soft(42) is not None
    assert validate_audit_soft([]) is not None
