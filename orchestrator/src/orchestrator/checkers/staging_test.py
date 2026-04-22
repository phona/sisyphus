"""staging-test 自检（M1）：sisyphus 在 runner pod 直接跑测试命令，收退出码决定 pass/fail。

不起 BKD agent，不靠 result:pass tag，sisyphus 是唯一裁判。
"""
from __future__ import annotations

import asyncio

import structlog

from .. import k8s_runner
from ._types import CheckResult

__all__ = ["CheckResult", "run_staging_test"]

log = structlog.get_logger(__name__)

_TAIL = 2048  # stdout/stderr 各截尾 2KB，防 OOM


async def run_staging_test(
    req_id: str,
    test_cmd: str,
    timeout_sec: int = 600,
) -> CheckResult:
    """在 runner pod 里执行 test_cmd，收集结果。

    timeout_sec 超时时抛 asyncio.TimeoutError（由 create_staging_test action 捕获）。
    """
    rc = k8s_runner.get_controller()
    log.info("checker.staging_test.start", req_id=req_id, cmd=test_cmd, timeout=timeout_sec)

    result = await asyncio.wait_for(
        rc.exec_in_runner(req_id, test_cmd, timeout_sec=timeout_sec),
        timeout=timeout_sec + 10,  # 比内部 deadline 多 10s，确保内部先超时返 -1
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
        cmd=test_cmd,
    )
