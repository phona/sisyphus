"""Contract tests for REQ-bkd-acceptance-feedback-loop-1777278984.

Black-box behavioural contracts derived from:
  openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md

Scenarios:
  USER-S1  TEARDOWN_DONE_PASS routes ACCEPT_TEARING_DOWN → PENDING_USER_REVIEW + post_acceptance_report
  USER-S2  PENDING_USER_REVIEW + USER_REVIEW_PASS → ARCHIVING + done_archive
  USER-S3  PENDING_USER_REVIEW + USER_REVIEW_FIX → ESCALATED + escalate
  USER-S4  Illegal events from PENDING_USER_REVIEW return None
  USER-S5  webhook helper: statusId=done emits USER_REVIEW_PASS
  USER-S6  webhook helper: statusId=review/blocked emits USER_REVIEW_FIX + sets ctx.escalated_reason
  USER-S7  webhook helper: unknown statusId skips (no event)
  USER-S8  webhook helper: sub-issue id ≠ intent_issue_id skips
  USER-S9  post_acceptance_report adds tag + posts message + does NOT touch statusId/title
  USER-S10 post_acceptance_report rerun is idempotent on tag side
  USER-S11 post_acceptance_report missing intent_issue_id is a graceful noop
  USER-S12 watchdog._SKIP_STATES (combined with _NO_WATCHDOG_STATES) covers PENDING_USER_REVIEW
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import watchdog, webhook
from orchestrator.state import Event, ReqState, decide

# ── USER-S1..S4: pure state-machine contracts ────────────────────────────


def test_USER_S1_teardown_pass_to_pending_user_review():
    t = decide(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS)
    assert t is not None
    assert t.next_state == ReqState.PENDING_USER_REVIEW
    assert t.action == "post_acceptance_report"


def test_USER_S2_pending_pass_to_archiving():
    t = decide(ReqState.PENDING_USER_REVIEW, Event.USER_REVIEW_PASS)
    assert t is not None
    assert t.next_state == ReqState.ARCHIVING
    assert t.action == "done_archive"


def test_USER_S3_pending_fix_to_escalated():
    t = decide(ReqState.PENDING_USER_REVIEW, Event.USER_REVIEW_FIX)
    assert t is not None
    assert t.next_state == ReqState.ESCALATED
    assert t.action == "escalate"


@pytest.mark.parametrize(
    "illegal_event",
    [
        Event.ARCHIVE_DONE,
        Event.SESSION_FAILED,
        Event.VERIFY_PASS,
        Event.STAGING_TEST_PASS,
        Event.ACCEPT_PASS,
        Event.INTAKE_PASS,
    ],
)
def test_USER_S4_pending_illegal_events_return_none(illegal_event):
    assert decide(ReqState.PENDING_USER_REVIEW, illegal_event) is None


# ── USER-S5..S8: webhook helper contracts ────────────────────────────────


@dataclass
class _FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "working"
    tags: list = field(default_factory=list)
    session_status: str | None = None
    description: str | None = None


@dataclass
class _FakeRow:
    state: ReqState = ReqState.PENDING_USER_REVIEW
    context: dict = field(default_factory=lambda: {"intent_issue_id": "intent-1"})


def _patch_webhook_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr(webhook, "BKDClient", _ctx)


def _patch_webhook_req_state(
    monkeypatch, *, row: _FakeRow | None, ctx_writes: list,
):
    async def fake_get(_pool, _req_id):
        return row

    async def fake_update_context(_pool, _req_id, patch):
        ctx_writes.append(patch)

    monkeypatch.setattr(webhook.req_state, "get", fake_get)
    monkeypatch.setattr(webhook.req_state, "update_context", fake_update_context)


def _make_bkd_returning(status_id: str) -> AsyncMock:
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(
        return_value=_FakeIssue(id="intent-1", status_id=status_id),
    )
    return bkd


@pytest.mark.asyncio
async def test_USER_S5_done_emits_pass(monkeypatch):
    bkd = _make_bkd_returning("done")
    _patch_webhook_bkd(monkeypatch, bkd)
    writes: list = []
    _patch_webhook_req_state(monkeypatch, row=_FakeRow(), ctx_writes=writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )
    assert ev == Event.USER_REVIEW_PASS
    assert writes == []  # PASS path does not need ctx mutation


@pytest.mark.asyncio
@pytest.mark.parametrize("status_id", ["review", "blocked"])
async def test_USER_S6_review_or_blocked_emits_fix_with_ctx(monkeypatch, status_id):
    bkd = _make_bkd_returning(status_id)
    _patch_webhook_bkd(monkeypatch, bkd)
    writes: list = []
    _patch_webhook_req_state(monkeypatch, row=_FakeRow(), ctx_writes=writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )
    assert ev == Event.USER_REVIEW_FIX
    assert writes == [{"escalated_reason": "user-requested-fix"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_id", ["working", "todo", "", "weird-state"])
async def test_USER_S7_unknown_statusid_skips(monkeypatch, status_id):
    bkd = _make_bkd_returning(status_id)
    _patch_webhook_bkd(monkeypatch, bkd)
    writes: list = []
    _patch_webhook_req_state(monkeypatch, row=_FakeRow(), ctx_writes=writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )
    assert ev is None
    assert writes == []


@pytest.mark.asyncio
async def test_USER_S8_sub_issue_id_skips(monkeypatch):
    bkd = _make_bkd_returning("done")
    _patch_webhook_bkd(monkeypatch, bkd)
    writes: list = []
    _patch_webhook_req_state(monkeypatch, row=_FakeRow(), ctx_writes=writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="some-sub-issue",  # ≠ intent-1
        tags=["REQ-x"], issue_number=None,
    )
    assert ev is None
    bkd.get_issue.assert_not_called()


# ── USER-S9..S11: post_acceptance_report action contracts ────────────────


def _patch_action_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.BKDClient", _ctx,
    )


def _patch_action_db(monkeypatch):
    class P:
        async def execute(self, *_a, **_kw):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.db.get_pool",
        lambda: P(),
    )


def _make_action_bkd():
    issue = _FakeIssue(id="intent-1")
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=issue)
    bkd.update_issue = AsyncMock(return_value=issue)
    bkd.merge_tags_and_update = AsyncMock(return_value=issue)
    bkd.follow_up_issue = AsyncMock(return_value={})
    return bkd


def _make_action_body():
    return type("B", (), {
        "issueId": "any", "projectId": "p", "event": "issue.updated",
        "title": "", "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_USER_S9_first_run_tags_and_messages_no_status_change(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_action_bkd()
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    out = await mod.post_acceptance_report(
        body=_make_action_body(),
        req_id="REQ-x",
        tags=["REQ-x"],
        ctx={
            "intent_issue_id": "intent-1",
            "pr_urls": {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/1"},
        },
    )
    assert out["acceptance_reported"] is True
    bkd.merge_tags_and_update.assert_awaited_once()
    _, kw = bkd.merge_tags_and_update.call_args
    assert kw["add"] == [mod.ACCEPTANCE_PENDING_TAG]
    assert kw.get("status_id") is None  # statusId left to user

    bkd.follow_up_issue.assert_awaited_once()
    _, fkw = bkd.follow_up_issue.call_args
    assert "statusId" in fkw["prompt"]
    assert "https://github.com/phona/sisyphus/pull/1" in fkw["prompt"]

    bkd.update_issue.assert_not_called()  # no title / direct PATCH


@pytest.mark.asyncio
async def test_USER_S10_rerun_is_idempotent_on_tag(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_action_bkd()
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    body = _make_action_body()
    ctx = {"intent_issue_id": "intent-1", "pr_urls": {}}
    await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )
    await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )
    # 2 invocations → 2 merge_tags_and_update calls; bkd.merge dedupes the tag itself.
    assert bkd.merge_tags_and_update.await_count == 2
    for call in bkd.merge_tags_and_update.call_args_list:
        _, kw = call
        assert kw["add"] == [mod.ACCEPTANCE_PENDING_TAG]


@pytest.mark.asyncio
async def test_USER_S11_missing_intent_issue_id_noop(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_action_bkd()
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    out = await mod.post_acceptance_report(
        body=_make_action_body(),
        req_id="REQ-x",
        tags=["REQ-x"],
        ctx={},  # missing intent_issue_id
    )
    assert out["acceptance_reported"] is False
    bkd.merge_tags_and_update.assert_not_called()
    bkd.follow_up_issue.assert_not_called()


# ── USER-S12: watchdog skip-state coverage ───────────────────────────────


def test_USER_S12_pending_user_review_in_combined_skip_set():
    """SQL pre-filter passes the union of _SKIP_STATES + _NO_WATCHDOG_STATES; the
    watchdog must never tick a row whose state is `pending-user-review`."""
    combined = watchdog._SKIP_STATES | {
        s.value for s in watchdog._NO_WATCHDOG_STATES
    }
    assert ReqState.PENDING_USER_REVIEW.value in combined
    # still must not regress legacy entries
    for legacy in (
        ReqState.DONE.value, ReqState.ESCALATED.value,
        ReqState.GH_INCIDENT_OPEN.value, ReqState.INIT.value,
        ReqState.INTAKING.value,
    ):
        assert legacy in combined
