"""checker 共享类型。

所有 artifact-driven checker 返回同一个 CheckResult shape，下游
artifact_checks 表写入 / engine emit 决策都不用关心 checker 是哪种。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    """checker 运行结果。

    exit_code 语义按 checker 自定（staging-test = 命令退出码；
    pr-ci-watch = 0 全绿 / 1 任一失败 / 124 超时；
    openspec = openspec CLI 退出码），engine 只看 passed。

    reason：可选语义标签，下游用来区分"该失败要不要走常规 fail 路径"。
    老 checker 不设即 None，兼容。三个 kubectl-exec checker 在 infra-flake retry
    发生时把 "flake-retry-recovered:<tag>" / "flake-retry-exhausted:<tag>"
    写到这里（REQ-checker-infra-flake-retry-1777247423）。

    attempts：总 exec 次数（含首次）。无 retry = 1；retry 触发 ≥ 2。
    """
    passed: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_sec: float
    cmd: str
    reason: str | None = None
    attempts: int = 1
