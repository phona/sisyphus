"""fanout_specs: analyze done → 创建 contract-spec + acceptance-spec 两个 spec issue。

把 analyze issue 推 done（防 BKD 重投递再触发 routeAnalyze）。

M6：开 spec issue 前先跑 manifest_validate 检查 open_questions；非空 → emit
ANALYZE_PENDING_HUMAN 回 analyzing-pending-human 态等人答，不创建 spec issue。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..checkers import manifest_validate
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

SPEC_STAGES = ("contract-spec", "acceptance-spec")


@register("fanout_specs")
async def fanout_specs(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("spec", Event.SPEC_ALL_PASSED, req_id=req_id):
        return rv

    # M6 admission：检 manifest 里的 open_questions 是否清零。flag off 时完全跳过。
    if settings.admission_analyze_pending_questions:
        try:
            check = await manifest_validate.run_manifest_validate(req_id)
        except Exception as e:
            # 读 PVC/kubectl 挂了不是 ambiguity 问题，不走 pending_human；记录后继续让
            # 下游 admission（M3 schema fail）或 skip 逻辑兜底。
            log.warning("fanout_specs.admission_error", req_id=req_id, error=str(e))
        else:
            if (
                not check.passed
                and check.reason == manifest_validate.REASON_OPEN_QUESTIONS_PENDING
            ):
                log.info(
                    "fanout_specs.pending_human",
                    req_id=req_id, stderr_tail=check.stderr_tail[:500],
                )
                return {
                    "emit": Event.ANALYZE_PENDING_HUMAN.value,
                    "reason": check.reason,
                    "stderr_tail": check.stderr_tail,
                }

    proj = body.projectId
    workdir = f"{settings.workdir_root}/feat-{req_id}"
    spec_issue_ids = {}

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        # 1. 把 analyze issue 推 done（幂等闸）
        await bkd.update_issue(project_id=proj, issue_id=body.issueId, status_id="done")

        # 2. 创建 2 个 spec issue
        for stage in SPEC_STAGES:
            issue = await bkd.create_issue(
                project_id=proj,
                title=f"[{req_id}] [{stage}]{short_title(ctx)}",
                tags=[stage, req_id],
                status_id="todo",
            )
            spec_issue_ids[stage] = issue.id

            # 发 prompt
            prompt = render("spec.md.j2",
                            spec_stage=stage, req_id=req_id, workdir=workdir)
            await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)

            # 推 working
            await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    # 把 spec issue id 写进 ctx，下游 mark_spec_reviewed 会用
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "spec_issues": spec_issue_ids,
        "expected_spec_count": len(SPEC_STAGES),
    })

    log.info("fanout_specs.done", req_id=req_id, specs=spec_issue_ids)
    return {"specs_created": list(SPEC_STAGES), "spec_issue_ids": spec_issue_ids}
