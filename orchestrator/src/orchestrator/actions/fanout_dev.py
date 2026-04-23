"""fanout_dev（M15）：SPG 通过后起单个 dev agent。

M15：删掉 manifest.parallelism 读取，退化成纯单 dev 模式。
多 dev 任务决策权交给 analyze agent，它创建多个 tag=dev issue。

fanout_dev 只起一个 dev agent issue；聚合逻辑 = 数 BKD tag=dev + REQ-xxx 的 issue。
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


@register("fanout_dev", idempotent=False)
async def fanout_dev(*, body, req_id, tags, ctx):
    """起一个 dev agent issue。"""
    if rv := skip_if_enabled("dev", Event.DEV_ALL_PASSED, req_id=req_id):
        return rv

    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [DEV]{short_title(ctx)}",
            tags=["dev", req_id],
            status_id="todo",
        )
        prompt = render(
            "dev.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "dev_issue_id": issue.id,
        "branch": branch,
        "workdir": workdir,
    })
    log.info("fanout_dev.done", req_id=req_id, dev_issue=issue.id)
    return {"dev_issue_id": issue.id}
