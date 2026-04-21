"""create_ci_runner: 派 ci-runner-agent 跑 unit 或 integration。

注册两个 key（create_ci_runner_unit / create_ci_runner_integration）走同一函数，
只是 target 参数不同。parent issue 取自当前事件来源（dev 完成 → parent=dev,
ci-int 重跑 → parent=reviewer/test-fix 视情况，统一以 ctx 里"上一个开发产物 issue" 为 parent）。
"""
from __future__ import annotations

import time

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


async def _create(*, body, req_id, tags, ctx, target: str):
    skip_stage = "ci_unit" if target == "unit" else "ci_int"
    emit_event = Event.CI_UNIT_PASS if target == "unit" else Event.CI_INT_PASS
    if rv := skip_if_enabled(skip_stage, emit_event, req_id=req_id):
        return rv
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = f"{settings.workdir_root}/ci-{req_id}-{target}-{int(time.time())}"

    # parent stage：以触发本 transition 的事件源 issue 为父。
    # （dev.done → parent=dev；reviewer.pass → parent=reviewer；CI fail 重跑也是 parent=ci 上次）
    parent_issue_id = body.issueId
    parent_stage = _infer_parent_stage(tags)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [CI {target}] self-check {parent_stage}{short_title(ctx)}",
            tags=[
                "ci", req_id,
                f"target:{target}",
                f"parent:{parent_stage}",
                f"parent-id:{parent_issue_id}",
            ],
            status_id="todo",
        )
        prompt = render(
            "ci_runner.md.j2",
            req_id=req_id, target=target, branch=branch, workdir=workdir,
            parent_issue_id=parent_issue_id, parent_stage=parent_stage,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        f"ci_{target.replace('-','_')}_issue_id": issue.id,
    })

    log.info("create_ci_runner.done", req_id=req_id, target=target, ci_issue=issue.id)
    return {"ci_issue_id": issue.id, "target": target}


@register("create_ci_runner_unit")
async def create_ci_runner_unit(*, body, req_id, tags, ctx):
    return await _create(body=body, req_id=req_id, tags=tags, ctx=ctx, target="unit")


@register("create_ci_runner_integration")
async def create_ci_runner_integration(*, body, req_id, tags, ctx):
    return await _create(body=body, req_id=req_id, tags=tags, ctx=ctx, target="integration")


def _infer_parent_stage(tags: list[str]) -> str:
    """从触发事件 issue tags 推父 stage。

    ci-int 经常由 ci-unit pass 触发（tags=[ci, target:unit, ci:pass]），
    所以 ci 也要识别 — 用 target: 细分 unit / integration。
    """
    for s in ("dev", "reviewer", "test-fix", "bugfix", "accept"):
        if s in tags:
            return s
    if "ci" in tags:
        for t in tags:
            if t.startswith("target:"):
                return f"ci-{t.split(':', 1)[1]}"
        return "ci"
    return "unknown"
