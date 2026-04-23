"""create_dev_cross_check：spec lint 通过 → 开发交叉验证。

M1 checker 框架：sisyphus 在 runner pod 执行 make dev-cross-check，
根据退出码 emit DEV_CROSS_CHECK_PASS / DEV_CROSS_CHECK_FAIL。

多仓重构后：checker 自行遍历 /workspace/source/*，含 Makefile 的仓逐一跑
`make dev-cross-check`，任一失败整体红。
"""
from __future__ import annotations

import structlog

from ..checkers import dev_cross_check as checker
from ..state import Event
from ..store import artifact_checks, db
from . import register

log = structlog.get_logger(__name__)

_STAGE = "dev-cross-check"


@register("create_dev_cross_check", idempotent=False)
async def create_dev_cross_check(*, body, req_id, tags, ctx):
    """下发开发交叉验证任务到 runner pod。"""
    log.info("create_dev_cross_check.start", req_id=req_id)

    try:
        result = await checker.run_dev_cross_check(req_id, timeout_sec=300)
    except TimeoutError:
        log.error("create_dev_cross_check.timeout", req_id=req_id)
        return {
            "emit": Event.DEV_CROSS_CHECK_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": "timeout",
        }
    except Exception as e:
        log.exception("create_dev_cross_check.error", req_id=req_id, error=str(e))
        return {
            "emit": Event.DEV_CROSS_CHECK_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": str(e)[:200],
        }

    pool = db.get_pool()
    await artifact_checks.insert_check(pool, req_id, _STAGE, result)

    if result.passed:
        log.info("create_dev_cross_check.done", req_id=req_id,
                 passed=True, exit_code=result.exit_code,
                 duration_sec=round(result.duration_sec, 1))
        return {
            "emit": Event.DEV_CROSS_CHECK_PASS.value,
            "passed": True,
            "exit_code": result.exit_code,
            "cmd": result.cmd,
            "duration_sec": result.duration_sec,
        }

    log.info("create_dev_cross_check.done", req_id=req_id,
             passed=False, exit_code=result.exit_code,
             duration_sec=round(result.duration_sec, 1))
    return {
        "emit": Event.DEV_CROSS_CHECK_FAIL.value,
        "passed": False,
        "exit_code": result.exit_code,
        "cmd": result.cmd,
        "duration_sec": result.duration_sec,
    }
