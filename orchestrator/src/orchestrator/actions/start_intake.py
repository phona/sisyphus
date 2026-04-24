"""start_intake：intent:intake 入口 —— 物理隔离 brainstorm 阶段。

行为：
1. ensure_runner（intake-agent 要 clone 仓读代码）
2. update-issue 把 intent issue 改名 [REQ-xxx] [INTAKE] — <title> + tags=[intake, REQ-xxx]
3. follow-up-issue 发 intake prompt（只能读代码 + 问问题，不能写实现）
4. update-issue statusId=working 触发 agent
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from . import register, short_title

log = structlog.get_logger(__name__)


@register("start_intake", idempotent=True)
async def start_intake(*, body, req_id, tags, ctx):
    proj = body.projectId
    issue_id = body.issueId

    # 1. 拉 K8s Pod + PVC（幂等；已存在就跳）
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("start_intake.no_runner_controller", req_id=req_id, error=str(e))
    else:
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("start_intake.runner_ready", req_id=req_id, pod=pod_name)

    # 2-4. BKD 调度 intake-agent
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.update_issue(
            project_id=proj,
            issue_id=issue_id,
            title=f"[{req_id}] [INTAKE]{short_title(ctx)}",
            tags=["intake", req_id],
        )
        prompt = render(
            "intake.md.j2",
            req_id=req_id,
            aissh_server_id=settings.aissh_server_id,
            project_id=proj,
            project_alias=proj,
            issue_id=issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue_id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue_id, status_id="working")

    log.info("start_intake.done", req_id=req_id, issue_id=issue_id)
    return {"issue_id": issue_id, "req_id": req_id}
