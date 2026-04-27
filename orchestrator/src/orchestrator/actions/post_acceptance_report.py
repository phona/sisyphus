"""post_acceptance_report (BAFL Case 2)

Run on `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` to advance the REQ into
`PENDING_USER_ACCEPT` with a user-facing notice on the BKD intent issue.

Behavior:
1. Render `prompts/_pending_user_accept.md.j2` — a system note describing
   the acceptance summary + the three resolution tags (`acceptance:approve`
   / `acceptance:request-changes` / `acceptance:reject`).
2. Best-effort `follow_up_issue` to drop the rendered note into the intent
   issue chat. The note opens with "do not respond — system note" so even
   if BKD wakes the analyze-agent, it should remain a no-op.
3. Merge `acceptance:pending` into the intent issue tags and flip
   `statusId="review"` so it surfaces on the BKD board's review column.
4. Persist the rendered note + acceptance summary into ctx so a later
   fixer round (started via `ACCEPT_USER_REQUEST_CHANGES`) can include
   them in the bugfix prompt.

Failure modes:
- The REQ has already advanced to PENDING_USER_ACCEPT via the engine's
  CAS by the time this action runs. Any BKD REST failure here MUST NOT
  unwind that — log a warning and return. Operators can retry by
  re-emitting the action via admin if needed.
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


# Marker comments that delimit the sisyphus-managed acceptance block in
# the rendered note. Future re-invocations can locate-and-replace using
# these markers (Phase 2: when scenarios are re-run after a fixer round
# we want to overwrite, not append, this block).
_BLOCK_OPEN = "<!-- sisyphus:acceptance-report -->"
_BLOCK_CLOSE = "<!-- /sisyphus:acceptance-report -->"


@register("post_acceptance_report", idempotent=True)
async def post_acceptance_report(*, body, req_id, tags, ctx):
    proj = body.projectId
    ctx = ctx or {}
    intent_issue_id = ctx.get("intent_issue_id") or body.issueId
    accept_issue_id = ctx.get("accept_issue_id")
    pr_urls = ctx.get("pr_urls") or {}

    accept_summary = ""
    # Best-effort fetch the accept-agent's last assistant message so the
    # note has a real summary instead of pointing the user back at chat.
    if accept_issue_id:
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                if hasattr(bkd, "get_last_assistant_message"):
                    msg = await bkd.get_last_assistant_message(
                        proj, accept_issue_id,
                    )
                    if msg:
                        accept_summary = (msg or "").strip()[:4000]
        except Exception as e:
            log.warning("post_acceptance_report.fetch_summary_failed",
                        req_id=req_id, accept_issue=accept_issue_id, error=str(e))

    block_body = render(
        "_pending_user_accept.md.j2",
        req_id=req_id,
        intent_issue_id=intent_issue_id,
        accept_issue_id=accept_issue_id,
        accept_summary=accept_summary,
        pr_urls=pr_urls,
    )
    block = f"{_BLOCK_OPEN}\n{block_body}\n{_BLOCK_CLOSE}"

    # 1) persist into ctx — durable record + survives BKD REST failures.
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "acceptance_report": block,
        "acceptance_summary": accept_summary,
    })

    # 2) best-effort BKD-side: tag, statusId, follow-up.
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.merge_tags_and_update(
                proj, intent_issue_id,
                add=["acceptance:pending"],
                remove=["accept", "result:pass", "result:fail"],
                status_id="review",
            )
    except Exception as e:
        log.warning("post_acceptance_report.tag_update_failed",
                    req_id=req_id, intent_issue=intent_issue_id, error=str(e))

    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.follow_up_issue(
                project_id=proj, issue_id=intent_issue_id, prompt=block,
            )
    except Exception as e:
        log.warning("post_acceptance_report.follow_up_failed",
                    req_id=req_id, intent_issue=intent_issue_id, error=str(e))

    log.info("post_acceptance_report.done",
             req_id=req_id, intent_issue=intent_issue_id,
             accept_issue=accept_issue_id,
             summary_len=len(accept_summary))
    return {"intent_issue_id": intent_issue_id, "summary_len": len(accept_summary)}
