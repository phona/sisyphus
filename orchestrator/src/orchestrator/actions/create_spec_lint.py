"""create_spec_lint：analyze done → 跑 spec linting 检查（openspec validate + scenario refs）。

M1 checker 框架：sisyphus 在 runner pod 执行客观任务（spec linting），
根据退出码 emit SPEC_LINT_PASS / SPEC_LINT_FAIL。

Spec linting 包括：
1. openspec validate：openspec 文件格式和结构检查
2. check-scenario-refs.sh：场景引用完整性检查
"""
from __future__ import annotations

import structlog

from ..checkers import spec_lint as checker
from ..config import settings
from ..state import Event
from ..store import artifact_checks, db, req_state
from . import register

log = structlog.get_logger(__name__)

_STAGE = "spec-lint"


@register("create_spec_lint", idempotent=False)
async def create_spec_lint(*, body, req_id, tags, ctx):
    """下发 spec linting 任务到 runner pod（openspec validate + scenario refs 检查）。"""
    ctx = ctx or {}
    leader_repo_path = ctx.get("leader_repo_path")
    if not leader_repo_path:
        log.error("create_spec_lint.no_leader_repo", req_id=req_id)
        return {
            "emit": Event.SPEC_LINT_FAIL.value,
            "passed": False,
            "reason": "leader_repo_path not found in context",
        }

    log.info("create_spec_lint.start", req_id=req_id, leader_repo_path=leader_repo_path)

    try:
        result = await checker.run_spec_lint(
            req_id,
            leader_repo_path=leader_repo_path,
            timeout_sec=120,
        )
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
