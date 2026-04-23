"""staging-test 自检（M1）：sisyphus 在 runner pod 直接跑测试命令，收退出码决定 pass/fail。

不起 BKD agent，不靠 result:pass tag，sisyphus 是唯一裁判。

M15：test_cmd 固定为 `make ci-test`，cwd 默认 /workspace，timeout 1800s。
业务 repo 必须在 Makefile 提供 `ci-test` target。
"""
from __future__ import annotations

import asyncio

import structlog

from .. import k8s_runner
from ._types import CheckResult

__all__ = ["CheckResult", "run_staging_test"]

log = structlog.get_logger(__name__)

_TAIL = 2048
_DEFAULT_TIMEOUT = 1800


async def run_staging_test(req_id: str) -> CheckResult:
    """在 runner pod 执行 make ci-test，收退出码决定 pass/fail。"""
    cmd = "cd /workspace && make ci-test"
    timeout_sec = _DEFAULT_TIMEOUT

    rc = k8s_runner.get_controller()
    log.info(
        "checker.staging_test.start",
        req_id=req_id, cmd=cmd, timeout=timeout_sec,
    )

    result = await asyncio.wait_for(
        rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
        timeout=timeout_sec + 10,
    )

    passed = result.exit_code == 0
    log.info(
        "checker.staging_test.done",
        req_id=req_id, passed=passed, exit_code=result.exit_code,
        duration_sec=round(result.duration_sec, 1),
    )

    return CheckResult(
        passed=passed,
        exit_code=result.exit_code,
        stdout_tail=result.stdout[-_TAIL:],
        stderr_tail=result.stderr[-_TAIL:],
        duration_sec=result.duration_sec,
        cmd=cmd,
    )
