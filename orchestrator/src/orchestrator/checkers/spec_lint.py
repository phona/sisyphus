"""spec 完整性检查（M1）：openspec validate + scenario refs linter。

sisyphus 在 runner pod 执行两个检查：
1. openspec validate：openspec 文件结构和格式校验
2. check-scenario-refs.sh：场景引用完整性校验（task.md / reports 引用的场景必须在 specs 中定义）

两个检查都通过才算 PASS。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from .. import k8s_runner
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048


def _build_cmd(req_id: str, leader_repo_path: str) -> str:
    """并行跑两个 spec 检查，都通过才算 pass。

    1. openspec validate openspec/changes/<REQ>
    2. check-scenario-refs.sh <repo_root>

    合并两个命令的输出和退出码。
    """
    return (
        f"set -e; cd \"{leader_repo_path}\" && "
        f"echo '=== Running openspec validate ===' && "
        f"openspec validate openspec/changes/{req_id} && "
        f"echo '=== Running check-scenario-refs ===' && "
        f"check-scenario-refs.sh ."
    )


async def run_spec_lint(
    req_id: str,
    *,
    leader_repo_path: str,
    timeout_sec: int = 120,
) -> CheckResult:
    """kubectl exec runner -- <openspec validate + scenario refs checks>，收 stdout/stderr/exit。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id, leader_repo_path)
    log.info(
        "checker.spec_lint.start",
        req_id=req_id, timeout=timeout_sec,
    )
    started = time.monotonic()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError:
        log.error(
            "checker.spec_lint.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"spec lint 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.spec_lint.done",
        req_id=req_id,
        passed=passed, exit_code=exec_result.exit_code,
        duration_sec=round(exec_result.duration_sec, 2),
    )
    return CheckResult(
        passed=passed,
        exit_code=exec_result.exit_code,
        stdout_tail=exec_result.stdout[-_TAIL:],
        stderr_tail=exec_result.stderr[-_TAIL:],
        duration_sec=exec_result.duration_sec,
        cmd=cmd,
    )
