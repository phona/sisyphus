"""create_spec_lint：analyze done → 跑 spec linting 检查（openspec validate + scenario refs）。

M1 checker 框架：sisyphus 在 runner pod 执行客观任务（spec linting），
根据退出码 emit SPEC_LINT_PASS / SPEC_LINT_FAIL。

多仓重构后：checker 自行遍历 /workspace/source/*，找到含 openspec/changes/<REQ>/ 的
仓逐一跑 openspec validate + check-scenario-refs.sh，任一失败整体红。
"""
from __future__ import annotations

import structlog

from ..checkers import spec_lint as checker
from ..state import Event
from ..store import artifact_checks, db
from . import register

log = structlog.get_logger(__name__)

_STAGE = "spec-lint"


@register("create_spec_lint", idempotent=False)
async def create_spec_lint(*, body, req_id, tags, ctx):
    """下发 spec linting 任务到 runner pod（openspec validate + scenario refs 检查）。"""
    log.info("create_spec_lint.start", req_id=req_id)

    try:
        result = await checker.run_spec_lint(req_id, timeout_sec=120)
    except TimeoutError:
        log.error("create_spec_lint.timeout", req_id=req_id)
        return {
            "emit": Event.SPEC_LINT_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": "timeout",
        }
    except Exception as e:
        log.exception("create_spec_lint.error", req_id=req_id, error=str(e))
        return {
            "emit": Event.SPEC_LINT_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": str(e)[:200],
        }

    pool = db.get_pool()
    await artifact_checks.insert_check(pool, req_id, _STAGE, result)

    if result.passed:
        log.info("create_spec_lint.done", req_id=req_id,
                 passed=True, exit_code=result.exit_code,
                 duration_sec=round(result.duration_sec, 1))
        return {
            "emit": Event.SPEC_LINT_PASS.value,
            "passed": True,
            "exit_code": result.exit_code,
            "cmd": result.cmd,
            "duration_sec": result.duration_sec,
        }

    log.info("create_spec_lint.done", req_id=req_id,
             passed=False, exit_code=result.exit_code,
             duration_sec=round(result.duration_sec, 1))
    return {
        "emit": Event.SPEC_LINT_FAIL.value,
        "passed": False,
        "exit_code": result.exit_code,
        "cmd": result.cmd,
        "duration_sec": result.duration_sec,
    }
