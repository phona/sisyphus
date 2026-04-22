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
    manifest = 0 合法 / 1 schema 不符 / 2 yaml 解析挂 / 3 open_questions 待人答 / -1 读 PVC 超时；
    openspec = openspec CLI 退出码），engine 只看 passed。

    reason：可选语义标签，下游用来区分"该失败要不要走常规 fail 路径"。
    例：manifest 的 ``open_questions_pending`` 要路由到 pending-human，
    schema 不符才走常规 admission fail。老 checker 不设即 None，兼容。
    """
    passed: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_sec: float
    cmd: str
    reason: str | None = None
