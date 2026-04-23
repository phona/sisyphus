"""dev_cross_check：开发交叉验证（M1 checker）。

sisyphus 在 runner pod 直接跑开发交叉验证，吃退出码定 pass/fail。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from .. import k8s_runner
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048


def _build_cmd(req_id: str, feature_repo_path: str) -> str:
    """跑开发交叉验证。

    对标 staging-test，开发交叉验证是 dev agent 完成后的客观检查。
    """
    return (
        f"set -e; cd \"{feature_repo_path}\" && "
        f"make dev-cross-check"
    )


async def run_dev_cross_check(
    req_id: str,
    *,
    feature_repo_path: str,
    timeout_sec: int = 300,
) -> CheckResult:
    """kubectl exec runner -- make dev-cross-check ...，收 stdout/stderr/exit。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id, feature_repo_path)
    log.info(
        "checker.dev_cross_check.start",
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
            "checker.dev_cross_check.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"dev cross-check 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.dev_cross_check.done",
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
