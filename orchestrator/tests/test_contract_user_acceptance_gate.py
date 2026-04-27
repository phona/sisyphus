"""Contract tests for REQ-bkd-acceptance-feedback-loop-1777278984.

Black-box behavioural contracts derived from:
  openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md

Scenarios:
  USER-S1  TEARDOWN_DONE_PASS routes ACCEPT_TEARING_DOWN → PENDING_USER_REVIEW + post_acceptance_report
  USER-S2  PENDING_USER_REVIEW + USER_REVIEW_PASS → ARCHIVING + done_archive
  USER-S3  PENDING_USER_REVIEW + USER_REVIEW_FIX → ESCALATED + escalate
  USER-S4  Illegal events from PENDING_USER_REVIEW return None
  USER-S5  webhook: statusId=done emits USER_REVIEW_PASS (only when state is pending-user-review)
  USER-S6  webhook: statusId=review/blocked emits USER_REVIEW_FIX + sets ctx.escalated_reason
  USER-S7  webhook: unknown statusId emits no event
  USER-S8  webhook: sub-issue id != intent_issue_id emits no event
  USER-S9  post_acceptance_report PATCHes description with managed block + does NOT patch tags/statusId
  USER-S10 post_acceptance_report second invocation replaces block (exactly one marker occurrence)
  USER-S11 post_acceptance_report missing intent_issue_id is a graceful noop
  USER-S12 watchdog._SKIP_STATES must contain PENDING_USER_REVIEW (canonical mechanism per spec)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import watchdog, webhook
from orchestrator.state import Event, ReqState, decide

# ── shared helpers ───────────────────────────────────────────────────────────

ACCEPTANCE_MARKER = "<!-- sisyphus:acceptance-status -->"


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


def _extract_description(call_args) -> str | None:
    """Extract 'description' from a mock call regardless of positional/keyword convention."""
    if call_args is None:
        return None
    if "description" in call_args.kwargs:
        return call_args.kwargs["description"]
    for arg in call_args.args:
        if isinstance(arg, dict) and "description" in arg:
            return arg["description"]
    return None


def _has_tags_in_call(call_args) -> bool:
    """True if the call passes a non-None 'tags' argument."""
    if call_args is None:
        return False
    tags_val = call_args.kwargs.get("tags")
    if tags_val is not None:
        return True
    for arg in call_args.args:
        if isinstance(arg, dict) and arg.get("tags") is not None:
            return True
    return False


def _has_status_id_in_call(call_args) -> bool:
    """True if the call passes a non-None 'status_id' / 'statusId' argument."""
    if call_args is None:
        return False
    for key in ("status_id", "statusId"):
        if call_args.kwargs.get(key) is not None:
            return True
    for arg in call_args.args:
        if isinstance(arg, dict):
            if arg.get("status_id") is not None or arg.get("statusId") is not None:
                return True
    return False


# ── USER-S1..S4: pure state-machine contracts ────────────────────────────────


def test_USER_S1_teardown_pass_to_pending_user_review():
    """Scenario USER-S1: TEARDOWN_DONE_PASS routes to PENDING_USER_REVIEW via post_acceptance_report."""
    t = decide(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS)
    assert t is not None
    assert t.next_state == ReqState.PENDING_USER_REVIEW
    assert t.action == "post_acceptance_report"


def test_USER_S2_pending_pass_to_archiving():
    """Scenario USER-S2: USER_REVIEW_PASS from PENDING_USER_REVIEW goes to ARCHIVING via done_archive."""
    t = decide(ReqState.PENDING_USER_REVIEW, Event.USER_REVIEW_PASS)
    assert t is not None
    assert t.next_state == ReqState.ARCHIVING
    assert t.action == "done_archive"


def test_USER_S3_pending_fix_to_escalated():
    """Scenario USER-S3: USER_REVIEW_FIX from PENDING_USER_REVIEW goes to ESCALATED via escalate."""
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
    """Scenario USER-S4: any event other than USER_REVIEW_PASS/FIX returns None from PENDING_USER_REVIEW."""
    assert decide(ReqState.PENDING_USER_REVIEW, illegal_event) is None


# ── USER-S5..S8: webhook routing contracts ───────────────────────────────────


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
    """Scenario USER-S5: statusId=done in PENDING_USER_REVIEW + matching issueId emits USER_REVIEW_PASS."""
    bkd = _make_bkd_returning("done")
    _patch_webhook_bkd(monkeypatch, bkd)
    writes: list = []
    _patch_webhook_req_state(monkeypatch, row=_FakeRow(), ctx_writes=writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )
    assert ev == Event.USER_REVIEW_PASS
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_id", ["review", "blocked"])
async def test_USER_S6_review_or_blocked_emits_fix_with_ctx(monkeypatch, status_id):
    """Scenario USER-S6: statusId=review/blocked emits USER_REVIEW_FIX + sets ctx.escalated_reason."""
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
    """Scenario USER-S7: unknown statusId values emit no event."""
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
    """Scenario USER-S8: issueId != intent_issue_id emits no event regardless of statusId."""
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


# ── USER-S9..S11: post_acceptance_report action contracts ────────────────────
#
# Spec contract (user-acceptance-gate/spec.md USER-S9..S11):
#   The action MUST call BKDClient.update_issue to PATCH `description` only.
#   The patched description MUST embed a managed block delimited by
#   <!-- sisyphus:acceptance-status -->.
#   The PATCH MUST NOT include `tags` or `statusId`.
#   ctx.acceptance_reported_at MUST be set to a non-empty ISO8601 timestamp.
#   When intent_issue_id is missing, the action MUST noop without raising.


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

        async def fetchrow(self, *_a, **_kw):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.db.get_pool",
        lambda: P(),
    )


def _make_action_bkd(description: str | None = None) -> AsyncMock:
    issue = _FakeIssue(id="intent-1", description=description)
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
async def test_USER_S9_patches_description_with_marker_not_tags_or_status(monkeypatch):
    """Scenario USER-S9: first invocation PATCHes description with managed block.

    Spec contract:
      - BKD update_issue MUST be called
      - The patched description MUST contain <!-- sisyphus:acceptance-status -->
      - The patched description MUST contain the PR URL
      - The PATCH MUST NOT include tags or statusId
      - ctx.acceptance_reported_at MUST be set
    """
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_action_bkd(description="existing body without block")
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    ctx = {
        "intent_issue_id": "intent-1",
        "pr_urls": {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/200"},
    }

    await mod.post_acceptance_report(
        body=_make_action_body(),
        req_id="REQ-x",
        tags=["REQ-x"],
        ctx=ctx,
    )

    # Spec: update_issue MUST be called
    bkd.update_issue.assert_awaited()

    # Spec: description must contain the managed block marker
    desc = _extract_description(bkd.update_issue.call_args)
    assert desc is not None, (
        "update_issue must receive a 'description' argument; "
        "spec requires PATCHing description with the acceptance-status block"
    )
    assert ACCEPTANCE_MARKER in desc, (
        f"description must contain the managed block marker {ACCEPTANCE_MARKER!r}"
    )

    # Spec: description must include PR URL so user can navigate
    assert "https://github.com/phona/sisyphus/pull/200" in desc, (
        "description block must include the PR URL(s)"
    )

    # Spec: PATCH must NOT include tags
    assert not _has_tags_in_call(bkd.update_issue.call_args), (
        "update_issue MUST NOT include tags to avoid tag-replacement side effects"
    )

    # Spec: PATCH must NOT include statusId (user owns that field)
    assert not _has_status_id_in_call(bkd.update_issue.call_args), (
        "update_issue MUST NOT include statusId; user drives that field"
    )

    # Spec: ctx.acceptance_reported_at must be set to a non-empty ISO8601 timestamp
    assert ctx.get("acceptance_reported_at"), (
        "ctx.acceptance_reported_at must be set after action completes"
    )


@pytest.mark.asyncio
async def test_USER_S10_second_invocation_replaces_block_not_appends(monkeypatch):
    """Scenario USER-S10: second invocation replaces the managed block; exactly one marker occurs.

    Spec contract:
      - After second invocation, the resulting description MUST contain
        exactly one occurrence of <!-- sisyphus:acceptance-status -->
      - The block content MUST be the freshly rendered version (not old + new concatenated)
    """
    from orchestrator.actions import post_acceptance_report as mod

    # Simulate issue body already containing the block from a prior run
    prior_body = (
        "Some existing body text.\n"
        f"{ACCEPTANCE_MARKER}\n"
        "## sisyphus 验收已通过 — 等你拍板\n"
        "- PR: https://github.com/phona/sisyphus/pull/100\n"
        "\n"
    )
    bkd = _make_action_bkd(description=prior_body)
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    ctx = {
        "intent_issue_id": "intent-1",
        "pr_urls": {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/200"},
    }

    await mod.post_acceptance_report(
        body=_make_action_body(),
        req_id="REQ-x",
        tags=["REQ-x"],
        ctx=ctx,
    )

    bkd.update_issue.assert_awaited()
    desc = _extract_description(bkd.update_issue.call_args)
    assert desc is not None, "update_issue must receive a description argument"

    marker_count = desc.count(ACCEPTANCE_MARKER)
    assert marker_count == 1, (
        f"description must contain exactly one marker occurrence after re-run; "
        f"got {marker_count}. Block was appended rather than replaced."
    )


@pytest.mark.asyncio
async def test_USER_S11_missing_intent_issue_id_noop(monkeypatch):
    """Scenario USER-S11: when ctx.intent_issue_id is absent, action noops without raising.

    Spec contract:
      - No BKD update_issue call is made
      - Action returns without raising
    """
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_action_bkd()
    _patch_action_bkd(monkeypatch, bkd)
    _patch_action_db(monkeypatch)

    # Should not raise
    result = await mod.post_acceptance_report(
        body=_make_action_body(),
        req_id="REQ-x",
        tags=["REQ-x"],
        ctx={},  # missing intent_issue_id
    )

    # Spec: no update_issue call when intent_issue_id is missing
    bkd.update_issue.assert_not_called()

    # Spec: action returns a non-error result (dict or any non-exception)
    assert result is not None


# ── USER-S12: watchdog skip-state coverage ───────────────────────────────────


def test_USER_S12_pending_user_review_in_skip_states():
    """Scenario USER-S12: watchdog._SKIP_STATES must contain PENDING_USER_REVIEW.

    Spec contract (canonical mechanism per spec):
      Adding PENDING_USER_REVIEW.value to watchdog._SKIP_STATES is the canonical
      mechanism. The watchdog MUST NOT emit SESSION_FAILED or watchdog.stuck for
      REQs in this state regardless of duration.

    Note: the spec explicitly calls out _SKIP_STATES as the canonical container.
    """
    assert ReqState.PENDING_USER_REVIEW.value in watchdog._SKIP_STATES, (
        "spec requires PENDING_USER_REVIEW.value to be in watchdog._SKIP_STATES "
        "(human-loop-conversation state; no BKD agent to crash-check)"
    )
    # Legacy entries must still be present (regression guard)
    for legacy in (
        ReqState.DONE.value,
        ReqState.ESCALATED.value,
        ReqState.GH_INCIDENT_OPEN.value,
        ReqState.INIT.value,
        ReqState.INTAKING.value,
    ):
        assert legacy in watchdog._SKIP_STATES, (
            f"watchdog._SKIP_STATES must still contain legacy entry {legacy!r}"
        )
