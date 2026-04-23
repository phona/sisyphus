"""fanout_specs（M16）：analyze done → 起单个 spec agent issue。

M15 砍掉了 dev 的 hardcoded fanout，parallelism 决策交给 analyze-agent。
M16 同样思路砍掉 spec 的双 fanout：sisyphus 不强制分 contract / acceptance 两个 agent，
由 analyze-agent 在 proposal/design/tasks 里判断是否需要拆，需要时自己再通过 BKD REST
创多个 tag=spec 的 issue（跟 dev 完全对称）。

fanout_specs 只起一个 spec agent issue（默认路径）；聚合逻辑 = 数 BKD tag=spec + REQ-xxx
的 issue 有几个 ci-passed。
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


@register("fanout_specs", idempotent=False)  # 创建新 spec issue
async def fanout_specs(*, body, req_id, tags, ctx):
    """起一个 spec agent issue。analyze-agent 想要并行多 spec 自己再加。"""
    if rv := skip_if_enabled("spec", Event.SPEC_ALL_PASSED, req_id=req_id):
        return rv

    proj = body.projectId
    workdir = f"{settings.workdir_root}/feat-{req_id}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        # 1. 把 analyze issue 推 done（幂等闸）
        await bkd.update_issue(project_id=proj, issue_id=body.issueId, status_id="done")

        # 2. 创建 1 个 spec issue
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [SPEC]{short_title(ctx)}",
            tags=["spec", req_id],
            status_id="todo",
        )
        prompt = render("spec.md.j2", req_id=req_id, workdir=workdir)
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "spec_issue_id": issue.id,
        "workdir": workdir,
    })

    log.info("fanout_specs.done", req_id=req_id, spec_issue=issue.id)
    return {"spec_issue_id": issue.id}
