"""create_accept (v0.2): PR CI 通过后拉 lab + 派 accept-agent。

v0.2 三段：
1. env-up：sisyphus 直调 k8s_runner.exec_in_runner 跑 `make accept-env-up`，
   工作目录由 `_integration_resolver.resolve_integration_dir` 决策（优先
   /workspace/integration/<name>，回退到 /workspace/source/<name> 单仓 self-host），
   拿 stdout 尾行 JSON 的 endpoint
2. 发 accept-agent BKD issue，注入 endpoint + image_tags + FEATURES
3. agent 跑 FEATURE-A* scenarios → session.completed 进 teardown_accept_env

env-up 失败 → emit accept-env-up.fail → state ESCALATED（lab 起不来，人介入）
"""
from __future__ import annotations

import json

import structlog

from .. import k8s_runner, pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, dispatch_slugs, req_state
from . import register, short_title
from ._integration_resolver import resolve_integration_dir
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("create_accept", idempotent=False)  # 创建新 accept issue + env-up 副作用
async def create_accept(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("accept", Event.ACCEPT_PASS, req_id=req_id):
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"accept_skipped": True})
        return rv

    proj = body.projectId
    source_issue_id = body.issueId   # 触发的 pr-ci-watch issue
    namespace = f"accept-{req_id.lower()}"

    # Phase 1: env-up via sisyphus (aissh 代理)
    accept_env: dict | None = None
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("create_accept.no_runner_controller", req_id=req_id, error=str(e))
        # 没 runner controller → 直接走 skip（等同 dev 环境）
        return {"emit": Event.ACCEPT_PASS.value, "note": "no runner controller, skipped env-up"}

    # 解析 integration dir：integration 优先 / 单仓 source self-host 回退
    resolved = await resolve_integration_dir(rc, req_id)
    if resolved.dir is None:
        log.warning("create_accept.no_integration_dir", req_id=req_id, reason=resolved.reason)
        await req_state.update_context(db.get_pool(), req_id, {"escalated_reason": "accept-env-up-failed"})
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": resolved.reason,
        }
    integration_dir = resolved.dir

    # 由 accept-agent 在 Makefile 里自己读 image_tags
    exec_env = {
        "SISYPHUS_REQ_ID": req_id,
        "SISYPHUS_STAGE": "accept-env-up",
        "SISYPHUS_NAMESPACE": namespace,
    }
    try:
        result = await rc.exec_in_runner(
            req_id,
            command=f"cd {integration_dir} && make accept-env-up",
            env=exec_env,
            timeout_sec=600,   # 10 min 应该够 helm install + wait ready
        )
    except Exception as e:
        log.exception("create_accept.env_up_crashed", req_id=req_id, error=str(e))
        await req_state.update_context(db.get_pool(), req_id, {"escalated_reason": "accept-env-up-failed"})
        return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "error": str(e)[:200]}

    if result.exit_code != 0:
        log.warning("create_accept.env_up_failed", req_id=req_id,
                    exit_code=result.exit_code, stderr_tail=result.stderr[-500:])
        await req_state.update_context(db.get_pool(), req_id, {"escalated_reason": "accept-env-up-failed"})
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "exit_code": result.exit_code,
            "stderr_tail": result.stderr[-500:],
        }

    # 从 stdout 最后一行解 JSON
    last_line = ""
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line:
            last_line = line
            break
    try:
        accept_env = json.loads(last_line)
    except json.JSONDecodeError:
        log.warning("create_accept.env_up_bad_json", req_id=req_id,
                    last_line_preview=last_line[:200])
        await req_state.update_context(db.get_pool(), req_id, {"escalated_reason": "accept-env-up-failed"})
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up stdout tail is not JSON",
        }

    endpoint = accept_env.get("endpoint") if isinstance(accept_env, dict) else None
    if not endpoint:
        log.warning("create_accept.env_up_no_endpoint", req_id=req_id, accept_env=accept_env)
        await req_state.update_context(db.get_pool(), req_id, {"escalated_reason": "accept-env-up-failed"})
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up JSON missing endpoint",
        }

    # Phase 2: dispatch accept-agent
    # PR-link tag 注入（REQ-issue-link-pr-quality-base-1777218242）
    branch_for_links = (ctx or {}).get("branch") or f"feat/{req_id}"
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch_for_links, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(links)

    pool = db.get_pool()
    slug = f"accept|{req_id}|{getattr(body, 'executionId', None) or ''}"
    if hit := await dispatch_slugs.get(pool, slug):
        log.info("create_accept.slug_hit", req_id=req_id, issue_id=hit)
        await req_state.update_context(pool, req_id, {"accept_issue_id": hit})
        return {"accept_issue_id": hit, "endpoint": endpoint, "namespace": namespace}
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ACCEPT] AI-QA{short_title(ctx)}",
            tags=["accept", req_id, f"parent-id:{source_issue_id}", *extra_tags],
            status_id="todo",
            model=settings.agent_model,
        )
        prompt = render(
            "accept.md.j2",
            req_id=req_id,
            endpoint=endpoint,
            namespace=namespace,
            source_issue_id=source_issue_id,
            accept_env=accept_env,
            project_id=proj,
            project_alias=proj,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    await dispatch_slugs.put(pool, slug, issue.id)
    await req_state.update_context(pool, req_id, {
        "accept_issue_id": issue.id,
        "accept_endpoint": endpoint,
        "accept_namespace": namespace,
    })

    log.info("create_accept.done", req_id=req_id, accept_issue=issue.id,
             endpoint=endpoint, namespace=namespace)
    return {
        "accept_issue_id": issue.id,
        "endpoint": endpoint,
        "namespace": namespace,
    }
