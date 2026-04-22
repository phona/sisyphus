"""M4/M9 故障分级路由：按失败类型 + 轮次返回 RetryDecision。

纯函数，不碰 IO。方便单测每种组合。

M4（decide）— checker/admission fail 分级决策：
| fail_kind                | 处理                          |
|--------------------------|-------------------------------|
| schema / lint / typecheck| follow_up 同 agent（外科手术）|
| test（round < diag_thr） | follow_up 同 agent            |
| test（round ≥ diag_thr） | diagnose（分流 spec/env/code）|
| prompt_too_long          | fresh_start（cancel + 新开）  |
| flaky                    | skip_check_retry（sisyphus 自重）|
| 任意（round ≥ max_rounds）| escalate                     |
| 未知 fail_kind            | escalate（保守兜底）          |

M9（decide_action_fail）— engine action handler 抛异常分级：
| 条件                              | 处理                             |
|-----------------------------------|----------------------------------|
| 非幂等 action                     | escalate（重试会重复副作用）     |
| transient exception + 未超 max    | retry with backoff               |
| transient + 超 max_rounds         | escalate                         |
| 非 transient exception            | escalate（bug, 不自动重试）      |
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx
from kubernetes.client.exceptions import ApiException as K8sApiException

RetryAction = Literal[
    "follow_up",
    "fresh_start",
    "diagnose",
    "skip_check_retry",
    "escalate",
]

ActionFailAction = Literal["retry", "escalate"]

# 网络 / K8s API / 异步超时类异常：重试可能救活。
# 注：RuntimeError (比如 k8s pod 拒启动后 phase=Failed) 不在此列，直接 escalate。
TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,            # 含 asyncio.TimeoutError（3.11+ 起是同一个）
    asyncio.TimeoutError,    # 显式列出兼容更老 traceback
    K8sApiException,         # k8s API 抖动（5xx/超时 + 4xx 某些情况）
    httpx.HTTPError,         # httpx 根异常类（含 TimeoutException / ConnectError 等）
    ConnectionError,         # 网络层兜底
)

SURGICAL_KINDS: frozenset[str] = frozenset({"schema", "lint", "typecheck"})
FAIL_KIND_TEST = "test"
FAIL_KIND_PROMPT_TOO_LONG = "prompt_too_long"
FAIL_KIND_FLAKY = "flaky"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    prompt: str | None   # follow-up 时传给 agent（decide 留空，executor 渲模板填）
    reason: str


def decide(
    stage: str,
    fail_kind: str,
    round: int,
    *,
    max_rounds: int = 5,
    diagnose_threshold: int = 3,
) -> RetryDecision:
    """按 (stage, fail_kind, round) 决策重试行为。

    Args:
        stage: checker 标识（"staging-test" / "pr-ci" / ...）只用于 reason/log
        fail_kind: 失败类别（见常量）。未知值默认 escalate
        round: 当前失败轮次（1-based；第 N 次失败传 N）
        max_rounds: 到/超过即升级人工
        diagnose_threshold: 测试失败第 N 轮起改走 diagnose agent

    Returns:
        RetryDecision（action + reason；prompt 始终 None，由 executor 渲染）
    """
    if round >= max_rounds:
        return RetryDecision(
            "escalate", None,
            f"round {round} ≥ max_rounds {max_rounds}",
        )

    if fail_kind == FAIL_KIND_PROMPT_TOO_LONG:
        return RetryDecision(
            "fresh_start", None,
            "prompt_too_long → cancel + fresh agent + 摘要",
        )

    if fail_kind == FAIL_KIND_FLAKY:
        return RetryDecision(
            "skip_check_retry", None,
            "flaky → sisyphus 自己重跑 check，不烦 agent",
        )

    if fail_kind in SURGICAL_KINDS:
        return RetryDecision(
            "follow_up", None,
            f"{fail_kind} 外科手术类 → follow-up 同 agent（round {round}）",
        )

    if fail_kind == FAIL_KIND_TEST:
        if round >= diagnose_threshold:
            return RetryDecision(
                "diagnose", None,
                f"test fail round {round} ≥ {diagnose_threshold} → diagnose agent",
            )
        return RetryDecision(
            "follow_up", None,
            f"test fail round {round} < {diagnose_threshold} → follow-up 同 agent",
        )

    # 未识别的 fail_kind：保守 escalate（宁可误升级也不做未定义重试）
    return RetryDecision(
        "escalate", None,
        f"unknown fail_kind {fail_kind!r} → escalate",
    )


# ── M9: engine action-fail 决策 ─────────────────────────────────────────


@dataclass(frozen=True)
class ActionFailDecision:
    """engine.step 捕到 action handler 异常后的处理决策。"""
    action: ActionFailAction
    backoff_sec: float
    reason: str


def decide_action_fail(
    action: str,
    *,
    exc: BaseException,
    round: int,
    idempotent: bool,
    max_rounds: int = 3,
) -> ActionFailDecision:
    """action handler 抛异常 → 决定 retry / escalate。

    Args:
        action: 触发异常的 action 名（仅用于 reason/log）
        exc: handler 抛出的异常
        round: 已重试轮次（0-based；第一次失败 round=0）
        idempotent: 重试该 action 是否安全（由 ACTION_META[action]["idempotent"] 传入）
        max_rounds: 最多重试轮次（不含首次；round == max_rounds 就 escalate）

    Returns:
        ActionFailDecision（action="retry"|"escalate" + backoff + reason）

    决策规则（顺序即优先级）：
        1. 非幂等 action → escalate（重试 create_dev 会重复建 BKD issue）
        2. transient 异常 + round < max_rounds → retry（退避 30/60/90/120s）
        3. 其他（非 transient / 超轮） → escalate
    """
    if not idempotent:
        return ActionFailDecision(
            action="escalate", backoff_sec=0.0,
            reason=f"non-idempotent action {action}; retry would duplicate side effects",
        )

    is_transient = isinstance(exc, TRANSIENT_EXCEPTIONS)
    exc_name = type(exc).__name__

    if is_transient and round < max_rounds:
        # 30, 60, 90, 120（上限），递增退避
        backoff = min(30.0 * (round + 1), 120.0)
        return ActionFailDecision(
            action="retry", backoff_sec=backoff,
            reason=f"transient {exc_name} round {round + 1}/{max_rounds}",
        )

    if is_transient:
        return ActionFailDecision(
            action="escalate", backoff_sec=0.0,
            reason=f"transient {exc_name} exceeded max_rounds {max_rounds}",
        )

    return ActionFailDecision(
        action="escalate", backoff_sec=0.0,
        reason=f"non-transient {exc_name}: {str(exc)[:200]}",
    )
