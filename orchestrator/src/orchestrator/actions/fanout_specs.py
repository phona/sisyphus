"""fanout_specs: analyze done → 创建 contract-spec + acceptance-spec 两个 spec issue。

把 analyze issue 推 done（防 BKD 重投递再触发 routeAnalyze）。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

SPEC_STAGES = ("contract-spec", "acceptance-spec")


@register("fanout_specs", idempotent=False)  # 创建两个新 spec issue
async def fanout_specs(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("spec", Event.SPEC_ALL_PASSED, req_id=req_id):
        return rv

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
