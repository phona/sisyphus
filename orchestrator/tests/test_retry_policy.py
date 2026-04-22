"""retry.policy.decide 单测：每种 fail_kind × round 组合验路由。"""
from __future__ import annotations

import pytest

from orchestrator.retry.policy import (
    FAIL_KIND_FLAKY,
    FAIL_KIND_PROMPT_TOO_LONG,
    FAIL_KIND_TEST,
    SURGICAL_KINDS,
    RetryDecision,
    decide,
)


# ─── escalate：round ≥ max_rounds，一切压过 ────────────────────────────────
@pytest.mark.parametrize("fail_kind", [
    "test", "schema", "lint", "typecheck", "flaky", "prompt_too_long", "unknown",
])
def test_round_at_or_above_max_always_escalates(fail_kind):
    d = decide("staging-test", fail_kind, round=5, max_rounds=5)
    assert d.action == "escalate"
    assert "max_rounds" in d.reason


def test_round_beyond_max_escalates():
    d = decide("staging-test", "test", round=10, max_rounds=5)
    assert d.action == "escalate"


# ─── 外科手术类：schema / lint / typecheck ────────────────────────────────
@pytest.mark.parametrize("kind", sorted(SURGICAL_KINDS))
def test_surgical_kind_follow_up(kind):
    d = decide("dev", kind, round=1)
    assert d.action == "follow_up"
    assert kind in d.reason


@pytest.mark.parametrize("kind", sorted(SURGICAL_KINDS))
def test_surgical_kind_still_follow_up_at_round_4(kind):
    """外科手术类不走 diagnose 分流，直到 max_rounds 才 escalate。"""
    d = decide("dev", kind, round=4, max_rounds=5, diagnose_threshold=3)
    assert d.action == "follow_up"


# ─── test fail：round 分 diagnose_threshold 前后 ──────────────────────────
def test_test_fail_round_1_follow_up():
    d = decide("staging-test", FAIL_KIND_TEST, round=1, diagnose_threshold=3)
    assert d.action == "follow_up"
    assert "round 1" in d.reason


def test_test_fail_round_2_still_follow_up():
    d = decide("staging-test", FAIL_KIND_TEST, round=2, diagnose_threshold=3)
    assert d.action == "follow_up"


def test_test_fail_at_diagnose_threshold_diagnose():
    d = decide("staging-test", FAIL_KIND_TEST, round=3, diagnose_threshold=3)
    assert d.action == "diagnose"
    assert "diagnose" in d.reason


def test_test_fail_above_diagnose_threshold_still_diagnose_until_max():
    d = decide("staging-test", FAIL_KIND_TEST, round=4, diagnose_threshold=3, max_rounds=5)
    assert d.action == "diagnose"


# ─── prompt_too_long：不管 round 都 fresh_start（直到 max_rounds）─────────
def test_prompt_too_long_fresh_start_round_1():
    d = decide("dev", FAIL_KIND_PROMPT_TOO_LONG, round=1)
    assert d.action == "fresh_start"


def test_prompt_too_long_fresh_start_round_4():
    d = decide("dev", FAIL_KIND_PROMPT_TOO_LONG, round=4, max_rounds=5)
    assert d.action == "fresh_start"


# ─── flaky：skip_check_retry（sisyphus 自重，不烦 agent）──────────────────
def test_flaky_skip_check_retry():
    d = decide("staging-test", FAIL_KIND_FLAKY, round=1)
    assert d.action == "skip_check_retry"


def test_flaky_still_skip_at_round_4():
    d = decide("staging-test", FAIL_KIND_FLAKY, round=4, max_rounds=5)
    assert d.action == "skip_check_retry"


# ─── 未知 fail_kind：保守 escalate ────────────────────────────────────────
def test_unknown_fail_kind_escalates():
    d = decide("staging-test", "kaboom", round=1)
    assert d.action == "escalate"
    assert "unknown fail_kind" in d.reason


def test_empty_fail_kind_escalates():
    d = decide("staging-test", "", round=1)
    assert d.action == "escalate"


# ─── RetryDecision：prompt 始终 None（由 executor 渲染）───────────────────
def test_decision_prompt_is_always_none():
    for kind in ["test", "schema", "prompt_too_long", "flaky", "unknown"]:
        d = decide("s", kind, round=1)
        assert isinstance(d, RetryDecision)
        assert d.prompt is None


# ─── 自定义 max_rounds / diagnose_threshold ──────────────────────────────
def test_custom_max_rounds_1():
    """max_rounds=1 时 round=1 就 escalate（配置极严）。"""
    d = decide("s", "test", round=1, max_rounds=1)
    assert d.action == "escalate"


def test_custom_diagnose_threshold_2():
    """diagnose_threshold=2 时 round=2 即 diagnose。"""
    d = decide("s", "test", round=2, diagnose_threshold=2, max_rounds=5)
    assert d.action == "diagnose"


def test_high_max_allows_more_rounds():
    d = decide("s", "test", round=4, diagnose_threshold=3, max_rounds=10)
    assert d.action == "diagnose"   # 不 escalate，还在 diagnose 窗口
