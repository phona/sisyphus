"""retry.policy.decide / decide_action_fail 单测。"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from kubernetes.client.exceptions import ApiException as K8sApiException

from orchestrator.retry.policy import (
    FAIL_KIND_FLAKY,
    FAIL_KIND_PROMPT_TOO_LONG,
    FAIL_KIND_TEST,
    SURGICAL_KINDS,
    ActionFailDecision,
    RetryDecision,
    decide,
    decide_action_fail,
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


# ═══════════════════════════════════════════════════════════════════════
# M9: decide_action_fail — engine action handler 异常分级
# ═══════════════════════════════════════════════════════════════════════


# ─── 非幂等 action：永远 escalate（无论异常类型/round） ───────────────────
@pytest.mark.parametrize("exc", [
    TimeoutError("pod not ready"),
    K8sApiException(status=500, reason="server error"),
    httpx.ConnectError("connect failed"),
    ValueError("bad input"),
])
def test_non_idempotent_always_escalates(exc):
    d = decide_action_fail("create_dev", exc=exc, round=0, idempotent=False)
    assert d.action == "escalate"
    assert "non-idempotent" in d.reason
    assert d.backoff_sec == 0.0


def test_non_idempotent_ignores_round():
    d = decide_action_fail("fanout_specs", exc=TimeoutError(), round=10, idempotent=False)
    assert d.action == "escalate"


# ─── 幂等 + transient 异常 + 未超轮：retry + 递增 backoff ────────────────
@pytest.mark.parametrize("exc_factory", [
    lambda: TimeoutError("pod not ready in 120s"),
    # asyncio.TimeoutError 在 3.11+ 就是 TimeoutError 别名，显式覆盖确保老代码
    # 里用旧名字的 raise asyncio.TimeoutError 也被认成 transient
    lambda: asyncio.TimeoutError(),  # noqa: UP041
    lambda: K8sApiException(status=500, reason="server error"),
    lambda: K8sApiException(status=503, reason="service unavailable"),
    lambda: httpx.ConnectError("conn reset"),
    lambda: httpx.TimeoutException("read timeout"),
    lambda: ConnectionError("broken pipe"),
])
def test_idempotent_transient_retries(exc_factory):
    d = decide_action_fail("start_analyze", exc=exc_factory(),
                           round=0, idempotent=True, max_rounds=3)
    assert d.action == "retry"
    assert d.backoff_sec == 30.0   # round 0 → 30s


def test_transient_backoff_grows_per_round():
    exc = TimeoutError("x")
    b0 = decide_action_fail("a", exc=exc, round=0, idempotent=True, max_rounds=5).backoff_sec
    b1 = decide_action_fail("a", exc=exc, round=1, idempotent=True, max_rounds=5).backoff_sec
    b2 = decide_action_fail("a", exc=exc, round=2, idempotent=True, max_rounds=5).backoff_sec
    b3 = decide_action_fail("a", exc=exc, round=3, idempotent=True, max_rounds=5).backoff_sec
    assert (b0, b1, b2, b3) == (30.0, 60.0, 90.0, 120.0)


def test_transient_backoff_caps_at_120():
    """round 超过 3 也不让 backoff 无限涨。"""
    d = decide_action_fail("a", exc=TimeoutError(), round=10,
                           idempotent=True, max_rounds=20)
    assert d.action == "retry"
    assert d.backoff_sec == 120.0


# ─── 幂等 + transient + 超轮：escalate ─────────────────────────────────
def test_idempotent_transient_exceeds_max_rounds_escalates():
    d = decide_action_fail("start_analyze", exc=TimeoutError("pod"),
                           round=3, idempotent=True, max_rounds=3)
    assert d.action == "escalate"
    assert "exceeded max_rounds" in d.reason


def test_idempotent_transient_beyond_max_rounds_escalates():
    d = decide_action_fail("start_analyze", exc=K8sApiException(status=500),
                           round=5, idempotent=True, max_rounds=3)
    assert d.action == "escalate"


# ─── 幂等 + 非 transient 异常：直接 escalate（不重试 bug） ──────────────
@pytest.mark.parametrize("exc", [
    ValueError("bad ctx field"),
    KeyError("issue_id"),
    RuntimeError("Pod failed: CrashLoopBackOff"),
    TypeError("NoneType has no attribute"),
])
def test_idempotent_non_transient_escalates(exc):
    d = decide_action_fail("start_analyze", exc=exc,
                           round=0, idempotent=True, max_rounds=3)
    assert d.action == "escalate"
    assert "non-transient" in d.reason
    assert d.backoff_sec == 0.0


def test_decision_is_frozen_dataclass():
    d = decide_action_fail("a", exc=TimeoutError(), round=0, idempotent=True)
    assert isinstance(d, ActionFailDecision)
    with pytest.raises(AttributeError):
        d.action = "nope"   # type: ignore[misc]
