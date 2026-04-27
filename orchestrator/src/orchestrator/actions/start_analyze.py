"""start_analyze (v0.2)：intent:analyze 入口。

v0.2 变化：agent 跑前先 ensure_runner 拉起 K8s Pod + PVC，保证 analyze-agent
kubectl exec 进去能立刻用。Pod 生命周期绑本 REQ 直到 done/escalate。

REQ-clone-and-pr-ci-fallback-1777115925：在 ensure_runner 之后、follow-up
prompt 之前，把 ctx 里的 involved_repos 替 agent server-side clone 进
/workspace/source/<basename>/。clone 失败 → 直接 emit VERIFY_ESCALATE，不
让 agent 进空 PVC 干活。

REQ-clone-fallback-direct-analyze-1777119520：直接 analyze 路径（无 intake，
ctx 没 involved_repos）也试 multi-layer fallback —— 把 BKD intent issue tags
里的 `repo:<org>/<name>` 跟 settings.default_involved_repos 喂给 _clone helper。
所有层都拿不到 → 才落到 agent prompt Part A.3 的"自跑 helper"路径。

行为：
1. ensure_runner（K8s：建 PVC + Pod，等 Ready）
2. server-side clone involved_repos（如果 ctx 有；失败 → VERIFY_ESCALATE）
3. update-issue 把 intent issue 改名 [REQ-xxx] [ANALYZE] — <title> + tags=[analyze, REQ-xxx]
4. follow-up-issue 发 analyze prompt
5. update-issue statusId=working 触发 agent
"""
from __future__ import annotations

import structlog

from .. import k8s_runner, links
from ..admission import check_admission
from ..bkd import BKDClient
from ..config import settings
from ..intent_tags import filter_propagatable_intent_tags
from ..prompts import render
from ..prompts.status_block import build_status_block_ctx
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._clone import clone_involved_repos_into_runner
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("start_analyze", idempotent=True)
async def start_analyze(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("analyze", Event.ANALYZE_DONE, req_id=req_id):
        return rv
    proj = body.projectId
    issue_id = body.issueId

    # 0. Admission gate（in-flight cap + disk pressure）。拒了直接 escalate，
    # 不浪费 PVC / Pod 的资源。fail-open by design：DB / disk 探测异常仍 admit。
    decision = await check_admission(db.get_pool(), req_id=req_id)
    if not decision.admit:
        await req_state.update_context(db.get_pool(), req_id, {
            "escalated_reason": f"rate-limit:{decision.reason}",
        })
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": f"admission denied: {decision.reason}",
        }

    # 1. 拉 K8s Pod + PVC（幂等；已存在就跳）
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        # dev 环境可能没 K8s；降级警告，后续 agent kubectl exec 会自己报错
        log.warning("start_analyze.no_runner_controller", req_id=req_id, error=str(e))
    else:
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("start_analyze.runner_ready", req_id=req_id, pod=pod_name)

    # 2. server-side clone（multi-layer fallback：ctx → tags → settings.default
    # 都没拿到 → 无声跳过，让 agent 按 prompt Part A.3 自跑 helper）
    cloned_repos, clone_rc = await clone_involved_repos_into_runner(
        req_id, ctx, tags=tags, default_repos=settings.default_involved_repos,
    )
    if clone_rc is not None:
        # helper 跑过但失败 → 不 dispatch agent，直接 escalate
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": f"clone failed (rc={clone_rc}) for repos={cloned_repos}"[:200],
        }

    # 3-5. BKD 调度 analyze-agent
    # REQ-ux-tags-injection-1777257283: forward user hint tags (`repo:`, `ux:`, ...)
    # so they survive the rename PATCH and stay visible to downstream agents /
    # dashboards / fallback layers.
    forwarded = filter_propagatable_intent_tags(tags)
    bkd_intent_issue_url = links.bkd_issue_url(proj, issue_id) or ""
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.update_issue(
            project_id=proj,
            issue_id=issue_id,
            title=f"[{req_id}] [ANALYZE]{short_title(ctx)}",
            tags=["analyze", req_id, *forwarded],
        )
        prompt = render(
            "analyze.md.j2",
            req_id=req_id,
            aissh_server_id=settings.aissh_server_id,
            project_id=proj,
            project_alias=proj,   # BKD REST 接 id 也接 alias，二者等价
            issue_id=issue_id,
            cloned_repos=cloned_repos,
            # REQ-pr-issue-traceability-1777218612: lets analyze.md.j2 render
            # the PR-body cross-link footer with a clickable BKD link.
            bkd_intent_issue_url=bkd_intent_issue_url,
            # REQ-ux-status-block-1777257283: canonical at-a-glance REQ status
            # block at the top of the analyze prompt. ctx.pr_urls is populated
            # by later stages (pr_ci_watch.discover_pr_urls); on first analyze
            # it is absent so format_pr_links_inline returns "" and the row
            # is omitted by the partial.
            status_block=build_status_block_ctx(
                req_id=req_id,
                stage="analyze",
                bkd_intent_issue_url=bkd_intent_issue_url,
                cloned_repos=cloned_repos,
                pr_urls=(ctx or {}).get("pr_urls"),
            ),
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue_id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue_id, status_id="working")

    # stash analyze_issue_id 进 ctx：让后续 pr_links.ensure_pr_links_in_ctx 第一次
    # discover 成功时能 backfill 这条 analyze issue 的 pr:* tag
    # （REQ-issue-link-pr-quality-base-1777218242）
    await req_state.update_context(db.get_pool(), req_id, {
        "analyze_issue_id": issue_id,
    })

    log.info("start_analyze.done", req_id=req_id, issue_id=issue_id,
             cloned_repos=cloned_repos)
    return {"issue_id": issue_id, "req_id": req_id, "cloned_repos": cloned_repos}
