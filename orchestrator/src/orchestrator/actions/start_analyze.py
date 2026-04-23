"""start_analyze (v0.2)：intent:analyze 入口。

v0.2 变化：agent 跑前先 ensure_runner 拉起 K8s Pod + PVC，保证 analyze-agent
kubectl exec 进去能立刻用。Pod 生命周期绑本 REQ 直到 done/escalate。

行为：
1. ensure_runner（K8s：建 PVC + Pod，等 Ready）
2. update-issue 把 intent issue 改名 [REQ-xxx] [ANALYZE] — <title> + tags=[analyze, REQ-xxx]
3. follow-up-issue 发 analyze prompt
4. update-issue statusId=working 触发 agent
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("start_analyze", idempotent=True)
async def start_analyze(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("analyze", Event.ANALYZE_DONE, req_id=req_id):
        return rv
    proj = body.projectId
    issue_id = body.issueId

    # 1. 拉 K8s Pod + PVC（幂等；已存在就跳）
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        # dev 环境可能没 K8s；降级警告，后续 agent kubectl exec 会自己报错
        log.warning("start_analyze.no_runner_controller", req_id=req_id, error=str(e))
    else:
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("start_analyze.runner_ready", req_id=req_id, pod=pod_name)

    # 2-4. BKD 调度 analyze-agent
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.update_issue(
            project_id=proj,
            issue_id=issue_id,
            title=f"[{req_id}] [ANALYZE]{short_title(ctx)}",
            tags=["analyze", req_id],
        )
        prompt = render(
            "analyze.md.j2",
            req_id=req_id,
            aissh_server_id=settings.aissh_server_id,
            project_id=proj,
            project_alias=proj,   # BKD REST 接 id 也接 alias，二者等价
            issue_id=issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue_id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue_id, status_id="working")

    log.info("start_analyze.done", req_id=req_id, issue_id=issue_id)
    return {"issue_id": issue_id, "req_id": req_id}
