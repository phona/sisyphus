"""Tests for the BKD-native PENDING_USER_REVIEW gate.

REQ-bkd-acceptance-feedback-loop-1777278984 — covers spec scenarios USER-S5..S8
in openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md

The unit under test is ``webhook._maybe_derive_user_review_event`` —
a self-contained helper that:

1. fetches REQ row + checks state == PENDING_USER_REVIEW
2. checks body.issueId == ctx.intent_issue_id
3. GETs the issue's current statusId via the (mocked) BKD client
4. returns USER_REVIEW_PASS / USER_REVIEW_FIX / None according to statusId
5. when returning USER_REVIEW_FIX, writes ``ctx.escalated_reason = "user-requested-fix"``
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import webhook
from orchestrator.state import Event, ReqState


@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "working"
    tags: list = field(default_factory=list)
    session_status: str | None = None
    description: str | None = None


@dataclass
class FakeRow:
    state: ReqState = ReqState.PENDING_USER_REVIEW
    context: dict = field(default_factory=lambda: {"intent_issue_id": "intent-1"})


def _patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr(webhook, "BKDClient", _ctx)


def _patch_req_state(monkeypatch, *, row: FakeRow | None, ctx_writes: list):
    async def fake_get(_pool, _req_id):
        return row

    async def fake_update_context(_pool, _req_id, patch):
        ctx_writes.append(patch)

    monkeypatch.setattr(webhook.req_state, "get", fake_get)
    monkeypatch.setattr(webhook.req_state, "update_context", fake_update_context)


def _make_bkd(status_id: str = "done") -> AsyncMock:
    issue = FakeIssue(id="intent-1", status_id=status_id)
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=issue)
    return bkd


# ── USER-S5: statusId=done emits USER_REVIEW_PASS ──────────────────────────


@pytest.mark.asyncio
async def test_USER_S5_status_done_emits_user_review_pass(monkeypatch):
    bkd = _make_bkd("done")
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=FakeRow(), ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev == Event.USER_REVIEW_PASS
    # PASS path does not need to mutate ctx.escalated_reason
    assert ctx_writes == []


# ── USER-S6: statusId=review/blocked emits USER_REVIEW_FIX + ctx ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status_id", ["review", "blocked"])
async def test_USER_S6_status_review_or_blocked_emits_fix_with_ctx(
    monkeypatch, status_id,
):
    bkd = _make_bkd(status_id)
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=FakeRow(), ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev == Event.USER_REVIEW_FIX
    assert ctx_writes == [{"escalated_reason": "user-requested-fix"}]


# ── USER-S7: unknown statusId does not emit ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status_id", ["working", "todo", "", "unknown-state", "DONE"])
async def test_USER_S7_unknown_status_id_skips(monkeypatch, status_id):
    bkd = _make_bkd(status_id)
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=FakeRow(), ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    if status_id.lower() == "done":
        assert ev == Event.USER_REVIEW_PASS  # case-insensitive match
    else:
        assert ev is None
    if ev != Event.USER_REVIEW_FIX:
        assert ctx_writes == []


# ── USER-S8: sub-issue id (≠ intent_issue_id) is ignored ──────────────────


@pytest.mark.asyncio
async def test_USER_S8_sub_issue_id_does_not_emit_user_review(monkeypatch):
    bkd = _make_bkd("done")
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=FakeRow(), ctx_writes=ctx_writes)

    # incoming issue.updated is on a sub-issue (not the intent)
    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="some-sub-issue",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev is None
    bkd.get_issue.assert_not_called()
    assert ctx_writes == []


# ── State guard: only fires when state == PENDING_USER_REVIEW ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_pending_state",
    [
        ReqState.ANALYZING,
        ReqState.STAGING_TEST_RUNNING,
        ReqState.ARCHIVING,
        ReqState.DONE,
        ReqState.ESCALATED,
    ],
)
async def test_state_guard_skips_when_not_pending_user_review(
    monkeypatch, non_pending_state,
):
    bkd = _make_bkd("done")
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(
        monkeypatch,
        row=FakeRow(state=non_pending_state),
        ctx_writes=ctx_writes,
    )

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev is None
    bkd.get_issue.assert_not_called()


# ── Missing row (REQ never indexed) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_req_row_skips(monkeypatch):
    bkd = _make_bkd("done")
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=None, ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev is None
    bkd.get_issue.assert_not_called()


# ── No REQ tag in payload → noop ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_req_tag_skips(monkeypatch):
    bkd = _make_bkd("done")
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    # row should never be queried; pass None so test fails loudly if it is
    _patch_req_state(monkeypatch, row=None, ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["random-tag"],  # no REQ-* tag
        issue_number=None,
    )

    assert ev is None


# ── BKD GET failure → graceful skip (no raise) ────────────────────────────


@pytest.mark.asyncio
async def test_bkd_fetch_failure_returns_none(monkeypatch):
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(side_effect=RuntimeError("BKD 5xx"))
    _patch_bkd(monkeypatch, bkd)
    ctx_writes: list = []
    _patch_req_state(monkeypatch, row=FakeRow(), ctx_writes=ctx_writes)

    ev = await webhook._maybe_derive_user_review_event(
        pool=None, project_id="p", issue_id="intent-1",
        tags=["REQ-x"], issue_number=None,
    )

    assert ev is None
    assert ctx_writes == []
