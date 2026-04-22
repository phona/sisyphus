"""M4 故障分级路由：按失败类型 + 轮次返回 RetryDecision。

纯函数，不碰 IO。方便单测每种组合。

决策表（见 #11 设计）：
| fail_kind                | 处理                          |
|--------------------------|-------------------------------|
| schema / lint / typecheck| follow_up 同 agent（外科手术）|
| test（round < diag_thr） | follow_up 同 agent            |
| test（round ≥ diag_thr） | diagnose（分流 spec/env/code）|
| prompt_too_long          | fresh_start（cancel + 新开）  |
| flaky                    | skip_check_retry（sisyphus 自重）|
| 任意（round ≥ max_rounds）| escalate                     |
| 未知 fail_kind            | escalate（保守兜底）          |
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RetryAction = Literal[
    "follow_up",
    "fresh_start",
    "diagnose",
    "skip_check_retry",
    "escalate",
]

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
