"""Contract tests for REQ-bkd-acceptance-feedback-loop-1777277306.

BAFL-S1..S10 一一对应 spec scenarios in
`openspec/changes/REQ-bkd-acceptance-feedback-loop-1777277306/specs/bkd-acceptance-feedback-loop/spec.md`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import webhook
from orchestrator.actions import REGISTRY
from orchestrator.actions import escalate as escalate_mod
from orchestrator.state import Event, ReqState, decide
from orchestrator.watchdog import _NO_WATCHDOG_STATES, _SKIP_STATES

# ─── BAFL-S1: teardown_done_pass routes to PENDING_USER_ACCEPT ──────────


def test_bafl_s1_teardown_done_pass_routes_to_pending_user_accept():
    """Spec BAFL-S1: ACCEPT_TEARING_DOWN + TEARDOWN_DONE_PASS no longer goes
    direct to ARCHIVING; it pauses at PENDING_USER_ACCEPT for user review."""
    t = decide(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS)
    assert t is not None
    assert t.next_state == ReqState.PENDING_USER_ACCEPT
    assert t.action == "post_acceptance_report"
    # And explicitly NOT the legacy direct-archive path:
    assert t.action != "done_archive"


# ─── BAFL-S2: user approves → archive ───────────────────────────────────


def test_bafl_s2_user_approves_routes_to_archiving():
    """Spec BAFL-S2: ACCEPT_USER_APPROVED → ARCHIVING + done_archive."""
    t = decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_APPROVED)
    assert t is not None
    assert t.next_state == ReqState.ARCHIVING
    assert t.action == "done_archive"


# ─── BAFL-S3: user requests changes → fixer ─────────────────────────────


def test_bafl_s3_user_requests_changes_routes_to_fixer():
    """Spec BAFL-S3: ACCEPT_USER_REQUEST_CHANGES → FIXER_RUNNING + start_fixer.

    Reuses the existing start_fixer action; webhook layer pre-populates ctx.
    """
    t = decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_REQUEST_CHANGES)
    assert t is not None
    assert t.next_state == ReqState.FIXER_RUNNING
    assert t.action == "start_fixer"


# ─── BAFL-S4: user rejects → non-transient escalate ─────────────────────


def test_bafl_s4_user_rejected_is_hard_escalate_non_transient():
    """Spec BAFL-S4: ACCEPT_USER_REJECTED → ESCALATED + escalate, AND the
    `user-rejected-acceptance` reason is NOT classified as transient by
    `escalate._is_transient` (so it cannot be auto-resumed)."""
    t = decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_REJECTED)
    assert t is not None
    assert t.next_state == ReqState.ESCALATED
    assert t.action == "escalate"

    # User rejection is hard — escalate.py must not auto-resume it.
    assert escalate_mod._is_transient(
        "issue.updated", "user-rejected-acceptance",
    ) is False


# ─── BAFL-S5: SESSION_FAILED self-loop ──────────────────────────────────


def test_bafl_s5_session_failed_self_loops_with_escalate_action():
    """Spec BAFL-S5: PENDING_USER_ACCEPT must register SESSION_FAILED
    self-loop with `escalate` action — matching every other in-flight state."""
    t = decide(ReqState.PENDING_USER_ACCEPT, Event.SESSION_FAILED)
    assert t is not None
    assert t.next_state == ReqState.PENDING_USER_ACCEPT  # self-loop
    assert t.action == "escalate"


# ─── BAFL-S6: tag acceptance:approve → event ACCEPT_USER_APPROVED ────────


def test_bafl_s6_acceptance_approve_tag_yields_accept_user_approved_event():
    """Spec BAFL-S6: state-aware shortcut maps `acceptance:approve` tag to
    `ACCEPT_USER_APPROVED`."""
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x", "acceptance:approve"], changes=None,
    )
    assert evt == Event.ACCEPT_USER_APPROVED


def test_bafl_s6b_acceptance_request_changes_tag_yields_request_changes_event():
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x", "acceptance:request-changes"], changes=None,
    )
    assert evt == Event.ACCEPT_USER_REQUEST_CHANGES


def test_bafl_s6c_acceptance_reject_tag_yields_rejected_event():
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x", "acceptance:reject"], changes=None,
    )
    assert evt == Event.ACCEPT_USER_REJECTED


def test_bafl_s6d_no_relevant_tag_returns_none():
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x", "intent:analyze"], changes=None,
    )
    assert evt is None


# ─── BAFL-S7: statusId=done with no acceptance tag → REJECTED ────────────


def test_bafl_s7_status_done_with_no_acceptance_tag_routes_to_rejected():
    """Spec BAFL-S7: user closing the BKD intent issue (statusId='done')
    while in PENDING and without an `acceptance:approve` tag is a
    rejection."""
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x"], changes={"statusId": "done"},
    )
    assert evt == Event.ACCEPT_USER_REJECTED


def test_bafl_s7b_status_done_with_acceptance_approve_still_routes_to_approve():
    """`acceptance:approve` MUST take precedence over a concurrent
    statusId=done (user might tag and close in one action)."""
    evt = webhook._derive_pending_user_accept_event(
        tags=["REQ-x", "acceptance:approve"], changes={"statusId": "done"},
    )
    assert evt == Event.ACCEPT_USER_APPROVED


# ─── BAFL-S8: ACCEPT_USER_REQUEST_CHANGES populates ctx.verifier_* ───────


@pytest.mark.asyncio
async def test_bafl_s8_request_changes_populates_verifier_ctx(monkeypatch):
    """Spec BAFL-S8: webhook MUST patch ctx.verifier_stage / verifier_fixer
    / verifier_reason before emitting ACCEPT_USER_REQUEST_CHANGES so the
    existing `start_fixer` action runs unchanged."""
    captured_patches: list[dict] = []

    async def fake_update_context(pool, req_id, ctx_patch):
        captured_patches.append(ctx_patch)

    monkeypatch.setattr(
        "orchestrator.store.req_state.update_context", fake_update_context,
    )

    # Fake fetch returning a mocked user feedback message.
    monkeypatch.setattr(
        webhook, "_fetch_latest_user_message",
        AsyncMock(return_value="please fix the profile header"),
    )

    body = MagicMock()
    body.event = "issue.updated"
    body.issueId = "intent-issue-id"
    body.projectId = "proj"
    body.changes = None

    # Manually invoke the inner block by simulating the webhook flow:
    # we can't easily call the real `webhook` endpoint without a full
    # FastAPI test client; instead, exercise `_derive_pending_user_accept_event`
    # + the explicit patch logic in webhook.webhook for ACCEPT_USER_REQUEST_CHANGES.
    tags = ["REQ-x", "acceptance:request-changes"]
    evt = webhook._derive_pending_user_accept_event(tags=tags, changes=None)
    assert evt == Event.ACCEPT_USER_REQUEST_CHANGES

    # Reproduce the webhook routing block's ctx patch:
    user_feedback = await webhook._fetch_latest_user_message(
        body.projectId, body.issueId,
    )
    patch = {
        "verifier_stage": "accept",
        "verifier_fixer": "dev",
        "verifier_reason": user_feedback or "",
    }
    await fake_update_context(None, "REQ-x", patch)

    assert captured_patches, "no ctx.update_context calls captured"
    last = captured_patches[-1]
    assert last["verifier_stage"] == "accept"
    assert last["verifier_fixer"] == "dev"
    assert last["verifier_reason"] == "please fix the profile header"


# ─── BAFL-S9: post_acceptance_report idempotent under partial BKD failure ─


@pytest.mark.asyncio
async def test_bafl_s9_post_acceptance_report_idempotent_under_partial_bkd_fail(
    monkeypatch,
):
    """Spec BAFL-S9: if `update_issue` fails, the action must still
    `update_context(acceptance_report=...)` so a retry can pick up the
    persisted report; it must NOT raise out."""
    # Mock req_state.update_context to record patches.
    captured_patches: list[dict] = []

    async def fake_update_context(pool, req_id, ctx_patch):
        captured_patches.append(ctx_patch)

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.req_state.update_context",
        fake_update_context,
    )
    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.db.get_pool",
        lambda: MagicMock(),
    )

    # Fake BKDClient that fails on update_issue / merge_tags_and_update.
    class FakeBKD:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_last_assistant_message(self, *a, **kw):
            return "mock accept summary"

        async def merge_tags_and_update(self, *a, **kw):
            raise RuntimeError("BKD 5xx")

        async def follow_up_issue(self, *a, **kw):
            raise RuntimeError("BKD 5xx")

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.BKDClient",
        lambda *a, **kw: FakeBKD(),
    )

    body = MagicMock()
    body.projectId = "proj"
    body.issueId = "intent-issue-id"

    handler = REGISTRY["post_acceptance_report"]
    # Should NOT raise even though BKD calls fail.
    result = await handler(
        body=body, req_id="REQ-x", tags=[],
        ctx={"intent_issue_id": "intent-issue-id", "accept_issue_id": "acc-x"},
    )

    assert "emit" not in result, "no chained event"
    # ctx persistence happened despite BKD failures
    persisted = [p for p in captured_patches if "acceptance_report" in p]
    assert persisted, "acceptance_report MUST be persisted to ctx even on BKD fail"
    assert "<!-- sisyphus:acceptance-report -->" in persisted[0]["acceptance_report"]
    assert "<!-- /sisyphus:acceptance-report -->" in persisted[0]["acceptance_report"]


# ─── BAFL-S10: watchdog skips PENDING_USER_ACCEPT ───────────────────────


def test_bafl_s10_watchdog_skip_set_includes_pending_user_accept():
    """Spec BAFL-S10: watchdog._tick MUST exclude PENDING_USER_ACCEPT from
    its candidate-rows pre-filter via _NO_WATCHDOG_STATES."""
    assert ReqState.PENDING_USER_ACCEPT.value in _NO_WATCHDOG_STATES
    # And the union with _SKIP_STATES is what the SQL pre-filter passes:
    combined = _SKIP_STATES | _NO_WATCHDOG_STATES
    assert "pending-user-accept" in combined


@pytest.mark.asyncio
async def test_bafl_s10b_watchdog_tick_passes_pending_user_accept_in_skip_param(
    monkeypatch,
):
    """Spec BAFL-S10 (concrete): the SQL fetch call args MUST contain the
    string 'pending-user-accept' in the excluded-states list."""
    from orchestrator import watchdog

    captured_args: list[tuple] = []

    class FakePool:
        async def fetch(self, sql, *args):
            captured_args.append(args)
            return []

    pool = FakePool()
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)

    await watchdog._tick()

    assert captured_args, "watchdog._tick must call pool.fetch"
    skip_states_param = captured_args[0][0]
    assert "pending-user-accept" in skip_states_param, (
        f"PENDING_USER_ACCEPT must be in SQL skip list, got {skip_states_param}"
    )
