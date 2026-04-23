"""mark_dev_reviewed_and_check: 单个 dev-agent 完成后标 ci-passed + gate 聚合。

仿 mark_spec_reviewed_and_check（M14d）：
- 打 ci-passed tag + 推 done（幂等）
- 全量扫本 REQ 下所有 dev issues，数 ci-passed 是否达到总数
- 齐了 emit `dev.all-passed` → 进 staging-test
- 不齐等下次 DEV_DONE

dev issue 总数由查询 tag=dev+REQ 的 issue 数量动态决定。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..state import Event
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("mark_dev_reviewed_and_check", idempotent=True)
async def mark_dev_reviewed_and_check(*, body, req_id, tags, ctx):
    proj = body.projectId
    triggering_issue = body.issueId

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.merge_tags_and_update(
            proj, triggering_issue,
            add=["ci-passed"],
            status_id="done",
        )
        all_issues = await bkd.list_issues(proj, limit=200)

    dev_issues = [
        i for i in all_issues
        if req_id in i.tags and "dev" in i.tags
    ]
    passed = [i for i in dev_issues if "ci-passed" in i.tags]
    expected = len(dev_issues)

    log.info(
        "mark_dev_reviewed.gate",
        req_id=req_id,
        passed=len(passed),
        expected=expected,
        triggered_by=triggering_issue,
    )

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "devs_passed": [i.id for i in passed],
    })

    if len(passed) >= expected:
        return {
            "dev_marked": triggering_issue,
            "passed_count": len(passed),
            "expected": expected,
            "emit": Event.DEV_ALL_PASSED.value,
        }
    return {
        "dev_marked": triggering_issue,
        "passed_count": len(passed),
        "expected": expected,
        "gate": "wait",
    }
