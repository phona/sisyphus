"""create_staging_test（v0.2 + M1 checker + M11 manifest-driven + M14c）：
dev.done 后验 staging 测试。

feature flag checker_staging_test_enabled:
  False（默认）: 创建 BKD agent issue（老路，保证老行为不破）
  True: sisyphus 自己在 runner pod 执行测试，根据退出码 emit STAGING_TEST_PASS/FAIL

M14c：移除 retry_enabled 分支。checker fail 直 emit STAGING_TEST_FAIL，
状态机会路由到 verifier 做主观判断。

M11：test_cmd / cwd / timeout 不再硬编码，checker 自己从 PVC manifest.yaml 读。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..checkers import manifest_io
from ..checkers import staging_test as checker
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import artifact_checks, db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_STAGE = "staging-test"


@register("create_staging_test", idempotent=False)  # 老路创 BKD issue；checker 模式安全但保守 False
async def create_staging_test(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("staging-test", Event.STAGING_TEST_PASS, req_id=req_id):
        return rv

    if settings.checker_staging_test_enabled:
        return await _run_checker(req_id=req_id, ctx=ctx or {})

    return await _dispatch_bkd_agent(body=body, req_id=req_id, ctx=ctx)


# ── 新路：sisyphus 自检 ────────────────────────────────────────────────────

async def _run_checker(*, req_id: str, ctx: dict) -> dict:
    log.info("create_staging_test.checker_path", req_id=req_id)

    try:
        result = await checker.run_staging_test(req_id)
    except TimeoutError:
        log.error("create_staging_test.checker_timeout", req_id=req_id)
        return {
            "emit": Event.STAGING_TEST_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": "timeout",
        }
    except manifest_io.ManifestReadError as e:
        log.error("create_staging_test.manifest_read_failed", req_id=req_id, error=str(e))
        return {
            "emit": Event.STAGING_TEST_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": f"manifest read failed: {e}"[:200],
        }
    except Exception as e:
        log.exception("create_staging_test.checker_error", req_id=req_id, error=str(e))
        return {
            "emit": Event.STAGING_TEST_FAIL.value,
            "passed": False,
            "exit_code": -1,
            "reason": str(e)[:200],
        }

    pool = db.get_pool()
    await artifact_checks.insert_check(pool, req_id, _STAGE, result)

    if result.passed:
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
    return {
        "emit": Event.STAGING_TEST_FAIL.value,
        "passed": False,
        "exit_code": result.exit_code,
        "cmd": result.cmd,
        "duration_sec": result.duration_sec,
    }


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
