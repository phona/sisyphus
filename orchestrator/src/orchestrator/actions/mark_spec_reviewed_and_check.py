"""mark_spec_reviewed_and_check（M16）：单个 spec agent 完成后标 ci-passed + gate 聚合。

仿 mark_dev_reviewed_and_check（M14d/M15）：
- 打 ci-passed tag + 推 done（幂等）
- 全量扫本 REQ 下所有 tag=spec issues，数 ci-passed 是否达到总数
- 齐了 emit `spec.all-passed` → 进 DEV_RUNNING
- 不齐等下次 SPEC_DONE

spec issue 总数由查询 tag=spec+REQ 的 issue 数量动态决定（analyze-agent 可能只开 1 个，
也可能开多个并行）。sisyphus 不维护 expected_spec_count 预期值。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..state import Event
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("mark_spec_reviewed_and_check", idempotent=True)
async def mark_spec_reviewed_and_check(*, body, req_id, tags, ctx):
    proj = body.projectId
    triggering_issue = body.issueId

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.merge_tags_and_update(
            proj, triggering_issue,
            add=["ci-passed"],
            status_id="done",
        )
        all_issues = await bkd.list_issues(proj, limit=200)

    spec_issues = [
        i for i in all_issues
        if req_id in i.tags and "spec" in i.tags
    ]
    passed = [i for i in spec_issues if "ci-passed" in i.tags]
    expected = len(spec_issues)

    log.info(
        "mark_spec_reviewed.gate",
        req_id=req_id,
        passed=len(passed),
        expected=expected,
        triggered_by=triggering_issue,
    )

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "specs_passed": [i.id for i in passed],
    })

    if len(passed) >= expected:
        return {
            "spec_marked": triggering_issue,
            "passed_count": len(passed),
            "expected": expected,
            "emit": Event.SPEC_ALL_PASSED.value,
        }
    return {
        "spec_marked": triggering_issue,
        "passed_count": len(passed),
        "expected": expected,
        "gate": "wait",
    }
