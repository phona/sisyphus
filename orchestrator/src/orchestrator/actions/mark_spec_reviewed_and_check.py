"""mark_spec_reviewed_and_check: spec issue 完成后标 ci-passed + 检查是否齐了。

齐了 emit `spec.all-passed` 让 engine 接着推进到 DEV_RUNNING。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..state import Event
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)

SPEC_TAGS = ("contract-spec", "acceptance-spec")


@register("mark_spec_reviewed_and_check", idempotent=True)
async def mark_spec_reviewed_and_check(*, body, req_id, tags, ctx):
    proj = body.projectId
    triggering_issue = body.issueId
    # 推 done + 给本 spec issue 加 ci-passed
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.merge_tags_and_update(
            proj, triggering_issue,
            add=["ci-passed"],
            status_id="done",
        )

        # 全量 list 看本 REQ 下所有 spec issues 是否都 ci-passed
        all_issues = await bkd.list_issues(proj, limit=200)

    expected = (ctx or {}).get("expected_spec_count") or len(SPEC_TAGS)
    spec_issues = [
        i for i in all_issues
        if req_id in i.tags and any(s in i.tags for s in SPEC_TAGS)
    ]
    passed = [i for i in spec_issues if "ci-passed" in i.tags]

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
