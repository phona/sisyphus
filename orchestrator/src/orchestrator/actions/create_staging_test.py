"""create_staging_test（v0.2）：dev.done 后下发 staging-test BKD agent。

staging-test 在调试环境（sisyphus-runners Pod）跑 unit + integration test。
按 manifest.sources 遍历，每个 source repo 跑 make ci-lint / ci-unit-test /
ci-integration-test。全绿 → result:pass；任一红 → result:fail + bug:pre-release。
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


@register("create_staging_test")
async def create_staging_test(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("staging-test", Event.STAGING_TEST_PASS, req_id=req_id):
        return rv

    proj = body.projectId
    source_issue_id = body.issueId   # 上游 dev issue（只读参考）

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [staging-test]{short_title(ctx)}",
            tags=["staging-test", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "staging_test.md.j2",
            req_id=req_id,
            source_issue_id=source_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"staging_test_issue_id": issue.id})

    log.info("create_staging_test.done", req_id=req_id, staging_issue=issue.id)
    return {"staging_test_issue_id": issue.id}
