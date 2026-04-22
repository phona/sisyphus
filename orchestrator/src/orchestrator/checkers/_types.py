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
    pr-ci-watch = 0 全绿 / 1 任一失败 / 124 超时），engine 只看 passed。
    """
    passed: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_sec: float
    cmd: str
