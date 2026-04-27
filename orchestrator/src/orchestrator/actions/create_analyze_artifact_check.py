"""create_analyze_artifact_check：analyze done → 跑机械产物结构校验
（REQ-analyze-artifact-check-1777254586）。

夹在 ANALYZING → SPEC_LINT_RUNNING 之间，机械校 analyze BKD agent 真的写出了
proposal.md / tasks.md / spec.md，而非"自报 pass 实际无产物"。

退出码语义跟 spec_lint 对齐：0 = 全过；非 0 / timeout / 异常 → emit fail。
"""
from __future__ import annotations

import structlog

from ..checkers import analyze_artifact_check as checker
from ..checkers._types import CheckResult
from ..state import Event
from ..store import artifact_checks, db
from . import register

log = structlog.get_logger(__name__)

_STAGE = "analyze-artifact-check"


@register("create_analyze_artifact_check", idempotent=False)
async def create_analyze_artifact_check(*, body, req_id, tags, ctx):
    """下发 analyze 产物校验任务到 runner pod（proposal/tasks/spec.md 存在 + 非空）。"""
    log.info("create_analyze_artifact_check.start", req_id=req_id)

    pool = db.get_pool()

    try:
        result = await checker.run_analyze_artifact_check(req_id, timeout_sec=120)
    except TimeoutError:
        log.error("create_analyze_artifact_check.timeout", req_id=req_id)
        # 超时也写一行 artifact_checks，让仪表盘看见（与 staging_test 同语义）
        try:
            await artifact_checks.insert_check(
                pool, req_id, _STAGE,
                CheckResult(
                    passed=False, exit_code=-1,
                    stdout_tail="", stderr_tail="timeout",
                    duration_sec=0.0, cmd="(timeout)", reason="timeout",
                ),
            )
        except Exception as e:
            log.warning("create_analyze_artifact_check.timeout_insert_failed",
                        req_id=req_id, error=str(e))
        return {
            "emit": Event.ANALYZE_ARTIFACT_CHECK_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": "timeout",
        }
    except Exception as e:
        log.exception("create_analyze_artifact_check.error", req_id=req_id, error=str(e))
        return {
            "emit": Event.ANALYZE_ARTIFACT_CHECK_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": str(e)[:200],
        }

    await artifact_checks.insert_check(pool, req_id, _STAGE, result)

    if result.passed:
        log.info("create_analyze_artifact_check.done", req_id=req_id,
                 passed=True, exit_code=result.exit_code,
                 duration_sec=round(result.duration_sec, 1))
        return {
            "emit": Event.ANALYZE_ARTIFACT_CHECK_PASS.value,
            "passed": True,
            "exit_code": result.exit_code,
            "cmd": result.cmd,
            "duration_sec": result.duration_sec,
        }

    log.info("create_analyze_artifact_check.done", req_id=req_id,
             passed=False, exit_code=result.exit_code,
             duration_sec=round(result.duration_sec, 1))
    return {
        "emit": Event.ANALYZE_ARTIFACT_CHECK_FAIL.value,
        "passed": False,
        "exit_code": result.exit_code,
        "cmd": result.cmd,
        "duration_sec": result.duration_sec,
    }
