"""start_analyze_with_finalized_intent：intake 完成后起独立 analyze-agent issue。

intake-agent 写完 finalized intent JSON（落 ctx.intake_finalized_intent）后，
这里创建新 BKD issue（不复用 intake issue），把 finalized intent 嵌入 analyze prompt。

REQ-clone-and-pr-ci-fallback-1777115925：在 ensure_runner 之后、create
analyze issue 之前，按 ctx.intake_finalized_intent.involved_repos
server-side clone 业务仓到 /workspace/source/<basename>/。clone 失败 →
emit VERIFY_ESCALATE，不打 agent 进空 PVC。

行为：
1. 检查 ctx.intake_finalized_intent 是否存在（缺失 → emit VERIFY_ESCALATE 不阻断）
2. ensure_runner（analyze-agent 要 clone 仓写代码）
3. server-side clone involved_repos（失败 → emit VERIFY_ESCALATE）
4. create 新 BKD issue（title=[REQ-xxx] [ANALYZE]）
5. follow-up 发 analyze prompt（带 intake_summary）
6. update statusId=working 触发 agent
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from . import register, short_title
from ._clone import clone_involved_repos_into_runner

log = structlog.get_logger(__name__)


@register("start_analyze_with_finalized_intent", idempotent=False)
async def start_analyze_with_finalized_intent(*, body, req_id, tags, ctx):
    finalized = (ctx or {}).get("intake_finalized_intent")
    if not finalized:
        log.warning("start_analyze_with_finalized_intent.missing_finalized_intent", req_id=req_id)
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": "intake_finalized_intent missing in ctx",
        }

    proj = body.projectId

    # 1. 拉 K8s Pod + PVC
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("start_analyze_with_finalized_intent.no_runner_controller",
                    req_id=req_id, error=str(e))
    else:
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("start_analyze_with_finalized_intent.runner_ready", req_id=req_id, pod=pod_name)

    # 2. server-side clone（intake 路径必有 involved_repos；失败 → escalate）。
    # tags + settings.default_involved_repos 是 REQ-clone-fallback-direct-analyze
    # 的 multi-layer 兜底，intake 路径正常用不上但传进去保持调用形状一致。
    cloned_repos, clone_rc = await clone_involved_repos_into_runner(
        req_id, ctx, tags=tags, default_repos=settings.default_involved_repos,
    )
    if clone_rc is not None:
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": f"clone failed (rc={clone_rc}) for repos={cloned_repos}"[:200],
        }

    # 3-5. 创建新 BKD analyze issue（不复用 intake issue）
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ANALYZE]{short_title(ctx)}",
            tags=["analyze", req_id],
            status_id="todo",
            use_worktree=True,
            model=settings.agent_model,
        )
        prompt = render(
            "analyze.md.j2",
            req_id=req_id,
            aissh_server_id=settings.aissh_server_id,
            project_id=proj,
            project_alias=proj,
            issue_id=issue.id,
            intake_summary=finalized,
            cloned_repos=cloned_repos,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    log.info("start_analyze_with_finalized_intent.done", req_id=req_id,
             analyze_issue_id=issue.id, cloned_repos=cloned_repos)
    return {"analyze_issue_id": issue.id, "cloned_repos": cloned_repos}
