"""create_staging_test（v0.2 + M1 checker + M4 retry）：dev.done 后验 staging 测试。

feature flag checker_staging_test_enabled:
  False（默认）: 创建 BKD agent issue（老路，保证老行为不破）
  True: sisyphus 自己在 runner pod 执行测试，根据退出码 emit STAGING_TEST_PASS/FAIL

feature flag retry_enabled（仅 checker 路径生效）:
  False（默认）: checker fail 直 emit STAGING_TEST_FAIL（老行为，进 bugfix 链）
  True: checker fail 走 retry.executor 分级决策；follow_up/diagnose 不 emit fail，
        状态留在 STAGING_TEST_RUNNING 等 dev agent 修完再触发重跑
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..checkers import staging_test as checker
from ..config import settings
from ..prompts import render
from ..retry import executor as retry_exec
from ..retry.executor import RetryContext
from ..state import Event
from ..store import artifact_checks, db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_TEST_CMD = "make test"   # M1 硬编码；M3 改成读 PVC manifest.yaml
_STAGE = "staging-test"


@register("create_staging_test", idempotent=False)  # 老路创 BKD issue；checker 模式安全但保守 False
async def create_staging_test(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("staging-test", Event.STAGING_TEST_PASS, req_id=req_id):
        return rv

    if settings.checker_staging_test_enabled:
        return await _run_checker(body=body, req_id=req_id, ctx=ctx or {})

    return await _dispatch_bkd_agent(body=body, req_id=req_id, ctx=ctx)


# ── 新路：sisyphus 自检 ────────────────────────────────────────────────────

async def _run_checker(*, body, req_id: str, ctx: dict) -> dict:
    log.info("create_staging_test.checker_path", req_id=req_id, cmd=_TEST_CMD)

    try:
        result = await checker.run_staging_test(req_id, _TEST_CMD)
    except TimeoutError:
        log.error("create_staging_test.checker_timeout", req_id=req_id)
        return await _handle_fail(
            body=body, req_id=req_id, ctx=ctx,
            fail_kind="flaky",   # timeout 归 flaky；不烦 agent，sisyphus 自己重跑
            details={"reason": "timeout", "exit_code": -1, "cmd": _TEST_CMD},
        )
    except Exception as e:
        log.exception("create_staging_test.checker_error", req_id=req_id, error=str(e))
        return await _handle_fail(
            body=body, req_id=req_id, ctx=ctx,
            fail_kind="test",
            details={"reason": str(e)[:200], "exit_code": -1, "cmd": _TEST_CMD},
        )

    pool = db.get_pool()
    await artifact_checks.insert_check(pool, req_id, _STAGE, result)

    if result.passed:
        # admission pass：清零 round 计数，保下一阶段 / 后续 REQ 干净
        if settings.retry_enabled:
            await retry_exec.reset_stage(req_id, _STAGE)
        log.info("create_staging_test.checker_done", req_id=req_id,
                 passed=True, exit_code=result.exit_code,
                 duration_sec=round(result.duration_sec, 1))
        return {
            "emit": Event.STAGING_TEST_PASS.value,
            "passed": True,
            "exit_code": result.exit_code,
            "cmd": result.cmd,
            "duration_sec": result.duration_sec,
        }

    log.info("create_staging_test.checker_done", req_id=req_id,
             passed=False, exit_code=result.exit_code,
             duration_sec=round(result.duration_sec, 1))
    return await _handle_fail(
        body=body, req_id=req_id, ctx=ctx,
        fail_kind="test",
        details={
            "cmd": result.cmd,
            "exit_code": result.exit_code,
            "stdout_tail": result.stdout_tail,
            "stderr_tail": result.stderr_tail,
            "duration_sec": result.duration_sec,
        },
    )


async def _handle_fail(*, body, req_id: str, ctx: dict, fail_kind: str, details: dict) -> dict:
    """checker fail 统一出口：按 retry_enabled 决定走 retry.executor 还是直接 emit FAIL。

    retry_enabled=False 时返老 shape（`passed`/`exit_code`/`cmd`/`emit`），保
    既有 test_create_staging_test_checker_fail 兼容。
    """
    if not settings.retry_enabled:
        out = {
            "emit": Event.STAGING_TEST_FAIL.value,
            "passed": False,
            "exit_code": details.get("exit_code", -1),
            "cmd": details.get("cmd", _TEST_CMD),
        }
        if "reason" in details:
            out["reason"] = details["reason"]
        return out

    retry_result = await retry_exec.run(RetryContext(
        req_id=req_id,
        project_id=body.projectId,
        stage=_STAGE,
        fail_kind=fail_kind,
        issue_id=(ctx or {}).get("dev_issue_id"),   # follow_up / fresh_start 的目标
        details=details,
    ))
    log.info("create_staging_test.retry_dispatched",
             req_id=req_id, retry_action=retry_result.get("retry_action"))
    return retry_result


# ── 老路：BKD agent（flag off 时走这里）────────────────────────────────────

async def _dispatch_bkd_agent(*, body, req_id: str, ctx: dict) -> dict:
    proj = body.projectId
    source_issue_id = body.issueId

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [staging-test]{short_title(ctx)}",
            tags=["staging-test", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "staging_test.md.j2",
            req_id=req_id,
            source_issue_id=source_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"staging_test_issue_id": issue.id})

    log.info("create_staging_test.bkd_agent_dispatched", req_id=req_id, staging_issue=issue.id)
    return {"staging_test_issue_id": issue.id}
