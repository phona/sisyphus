"""Challenger contract tests for REQ-bkd-intent-statusid-sync-1777280751.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-bkd-intent-statusid-sync-1777280751/specs/bkd-intent-status-sync/spec.md
  openspec/changes/REQ-bkd-intent-statusid-sync-1777280751/specs/bkd-intent-status-sync/contract.spec.yaml

Scenarios covered:
  BIS-S1   status_id_for(DONE) == "done"
  BIS-S2   status_id_for(ESCALATED) == "review"
  BIS-S3   status_id_for(non-terminal states) returns None
  BIS-S4   patch_terminal_status DONE → update_issue(status_id="done"), return truthy
  BIS-S5   patch_terminal_status ESCALATED → update_issue(status_id="review")
  BIS-S6   missing/empty intent_issue_id → skip PATCH, return False
  BIS-S7   non-terminal state → skip PATCH, return False
  BIS-S8   BKD raises → no exception propagates, warning logged
  BIS-S9   engine.step ARCHIVING+ARCHIVE_DONE→DONE: patch_terminal_status called (intent_issue_id, DONE)
  BIS-S10  engine.step PR_CI_RUNNING+PR_CI_TIMEOUT→ESCALATED: patch_terminal_status called (intent_issue_id, ESCALATED)
  BIS-S11  escalate SESSION_FAILED retry exhausted → patch_terminal_status(ESCALATED) called once
  BIS-S12  escalate PR-merged override → merge_tags_and_update(status_id="done"), no extra patch_terminal_status for DONE CAS

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.state import Event, ReqState

# ─── Common test fixtures ────────────────────────────────────────────────────

_REQ_ID = "REQ-bkd-intent-statusid-sync-1777280751"
_PROJECT = "proj-bis"
_POOL = object()  # sentinel; DB calls are patched away


class _Body:
    issueId = "bkd-bis-test"
    projectId = _PROJECT


def _make_body(issue_id: str = "src-1", project_id: str = _PROJECT, event: str = "session.failed"):
    return type("Body", (), {
        "issueId": issue_id,
        "projectId": project_id,
        "event": event,
        "title": "T",
        "tags": [],
        "issueNumber": None,
    })()


@pytest.fixture(autouse=True)
def _restore_registry():
    from orchestrator.actions import REGISTRY
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _make_bkd_fake() -> AsyncMock:
    bkd = AsyncMock()
    bkd.update_issue = AsyncMock(return_value=MagicMock(id="intent-1"))
    bkd.merge_tags_and_update = AsyncMock(return_value=MagicMock(id="intent-1"))
    bkd.create_issue = AsyncMock(return_value=MagicMock(id="new-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.list_issues = AsyncMock(return_value=[])
    bkd.get_issue = AsyncMock(return_value=MagicMock(id="intent-1", tags=[]))
    return bkd


def _patch_intent_status_bkd(monkeypatch, fake: AsyncMock) -> None:
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.intent_status.BKDClient", _ctx)


def _patch_escalate_bkd(monkeypatch, fake: AsyncMock) -> None:
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _ctx)


def _patch_escalate_db(monkeypatch) -> None:
    class _Pool:
        async def execute(self, sql, *args): pass
        async def fetchrow(self, sql, *args): return None

    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: _Pool())


def _patch_engine_io(monkeypatch) -> AsyncMock:
    from orchestrator import engine

    cas = AsyncMock(return_value=True)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)
    monkeypatch.setattr(engine.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
    monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())
    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())
    return cas


class _FakeController:
    def __init__(self):
        self.cleanup_calls: list[tuple] = []

    async def cleanup_runner(self, req_id, *, retain_pvc=False):
        self.cleanup_calls.append((req_id, retain_pvc))


# ─── BIS-S1: status_id_for(DONE) returns "done" ─────────────────────────────


def test_bis_s1_status_id_for_done_returns_done():
    """BIS-S1: status_id_for(ReqState.DONE) MUST return the literal string "done"."""
    from orchestrator import intent_status

    result = intent_status.status_id_for(ReqState.DONE)

    assert result == "done", (
        f"BIS-S1 contract: status_id_for(DONE) must return 'done', got {result!r}"
    )


# ─── BIS-S2: status_id_for(ESCALATED) returns "review" ──────────────────────


def test_bis_s2_status_id_for_escalated_returns_review():
    """BIS-S2: status_id_for(ReqState.ESCALATED) MUST return the literal string "review"."""
    from orchestrator import intent_status

    result = intent_status.status_id_for(ReqState.ESCALATED)

    assert result == "review", (
        f"BIS-S2 contract: status_id_for(ESCALATED) must return 'review', got {result!r}"
    )


# ─── BIS-S3: non-terminal states return None ────────────────────────────────


@pytest.mark.parametrize("state", [
    ReqState.INTAKING,
    ReqState.ANALYZING,
    ReqState.SPEC_LINT_RUNNING,
    ReqState.REVIEW_RUNNING,
])
def test_bis_s3_non_terminal_returns_none(state: ReqState):
    """BIS-S3: status_id_for of any non-terminal state MUST return None."""
    from orchestrator import intent_status

    result = intent_status.status_id_for(state)

    assert result is None, (
        f"BIS-S3 contract: status_id_for({state!r}) must return None, got {result!r}"
    )


# ─── BIS-S4: patch_terminal_status DONE → update_issue with status_id="done" ──


async def test_bis_s4_patch_terminal_status_done_calls_bkd_done(monkeypatch):
    """
    BIS-S4: patch_terminal_status(DONE) MUST call BKDClient.update_issue exactly once
    with project_id, issue_id=intent_issue_id, status_id="done", and return truthy.
    """
    from orchestrator import intent_status

    fake = _make_bkd_fake()
    _patch_intent_status_bkd(monkeypatch, fake)

    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.DONE,
        source="test.bis_s4",
    )

    assert result, (
        f"BIS-S4 contract: patch_terminal_status(DONE) must return truthy, got {result!r}"
    )
    fake.update_issue.assert_awaited_once()
    call_kwargs = fake.update_issue.await_args
    assert call_kwargs.kwargs.get("project_id") == "proj-x" or (
        len(call_kwargs.args) > 0 and call_kwargs.args[0] == "proj-x"
    ), (
        f"BIS-S4 contract: update_issue must receive project_id='proj-x'; "
        f"call args: {call_kwargs!r}"
    )
    # status_id must be "done" — verify via call args inspection
    all_args = str(call_kwargs)
    assert "done" in all_args, (
        f"BIS-S4 contract: update_issue must be called with status_id='done'; "
        f"call args: {call_kwargs!r}"
    )
    assert "intent-1" in all_args, (
        f"BIS-S4 contract: update_issue must be called with issue_id='intent-1'; "
        f"call args: {call_kwargs!r}"
    )


# ─── BIS-S5: patch_terminal_status ESCALATED → update_issue with "review" ───


async def test_bis_s5_patch_terminal_status_escalated_calls_bkd_review(monkeypatch):
    """
    BIS-S5: patch_terminal_status(ESCALATED) MUST call BKDClient.update_issue exactly
    once with status_id="review".
    """
    from orchestrator import intent_status

    fake = _make_bkd_fake()
    _patch_intent_status_bkd(monkeypatch, fake)

    await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.ESCALATED,
        source="test.bis_s5",
    )

    fake.update_issue.assert_awaited_once()
    call_args_str = str(fake.update_issue.await_args)
    assert "review" in call_args_str, (
        f"BIS-S5 contract: update_issue must be called with status_id='review'; "
        f"call args: {fake.update_issue.await_args!r}"
    )


# ─── BIS-S6: missing intent_issue_id → skip PATCH, return False ──────────────


@pytest.mark.parametrize("empty_id", [None, "", "  "])
async def test_bis_s6_missing_intent_issue_id_skips_patch(monkeypatch, empty_id):
    """
    BIS-S6: patch_terminal_status with missing/empty intent_issue_id MUST NOT call
    BKDClient.update_issue and MUST return False without raising.
    """
    from orchestrator import intent_status

    fake = _make_bkd_fake()
    _patch_intent_status_bkd(monkeypatch, fake)

    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id=empty_id,
        terminal_state=ReqState.DONE,
        source="test.bis_s6",
    )

    assert result is False, (
        f"BIS-S6 contract: must return False when intent_issue_id={empty_id!r}, got {result!r}"
    )
    fake.update_issue.assert_not_awaited()


# ─── BIS-S7: non-terminal state → skip PATCH, return False ──────────────────


async def test_bis_s7_non_terminal_state_skips_patch(monkeypatch):
    """
    BIS-S7: patch_terminal_status with non-terminal state MUST NOT call
    BKDClient.update_issue and MUST return False.
    """
    from orchestrator import intent_status

    fake = _make_bkd_fake()
    _patch_intent_status_bkd(monkeypatch, fake)

    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.INTAKING,
        source="test.bis_s7",
    )

    assert result is False, (
        f"BIS-S7 contract: must return False for non-terminal state, got {result!r}"
    )
    fake.update_issue.assert_not_awaited()


# ─── BIS-S8: BKD raises → log warning, swallow exception ────────────────────


async def test_bis_s8_bkd_raises_swallowed_warning_logged(monkeypatch, caplog):
    """
    BIS-S8: When BKDClient.update_issue raises, patch_terminal_status MUST NOT
    re-raise and MUST log a warning with event key 'intent_status.patch_failed'.
    """
    from orchestrator import intent_status

    fake = _make_bkd_fake()
    fake.update_issue = AsyncMock(side_effect=RuntimeError("BKD 503"))
    _patch_intent_status_bkd(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        result = await intent_status.patch_terminal_status(
            project_id="proj-x",
            intent_issue_id="intent-1",
            terminal_state=ReqState.DONE,
            source="test.bis_s8",
        )

    # No exception must propagate — if we reach here, the test passes that part
    assert "intent_status.patch_failed" in caplog.text, (
        f"BIS-S8 contract: warning with key 'intent_status.patch_failed' must be logged; "
        f"captured log: {caplog.text!r}"
    )


# ─── BIS-S9: engine.step ARCHIVING+ARCHIVE_DONE→DONE calls patch_terminal_status ──


async def test_bis_s9_engine_step_done_transition_calls_patch_terminal_status(monkeypatch):
    """
    BIS-S9: engine.step with ARCHIVING+ARCHIVE_DONE → DONE MUST call
    intent_status.patch_terminal_status with intent_issue_id and terminal_state=DONE.
    The call MUST be awaited (synchronous) before step returns.
    """
    from orchestrator import engine, intent_status

    _patch_engine_io(monkeypatch)
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    patch_calls: list[dict] = []

    async def _fake_patch(*, project_id, intent_issue_id, terminal_state, source):
        patch_calls.append({
            "project_id": project_id,
            "intent_issue_id": intent_issue_id,
            "terminal_state": terminal_state,
            "source": source,
        })
        return True

    monkeypatch.setattr(intent_status, "patch_terminal_status", _fake_patch)

    result = await engine.step(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=[_REQ_ID],
        cur_state=ReqState.ARCHIVING,
        ctx={"intent_issue_id": "intent-1"},
        event=Event.ARCHIVE_DONE,
        depth=0,
    )

    await asyncio.sleep(0)  # drain any fire-and-forget tasks

    assert len(patch_calls) >= 1, (
        f"BIS-S9 contract: intent_status.patch_terminal_status MUST be called on "
        f"ARCHIVING+ARCHIVE_DONE→DONE transition; got {len(patch_calls)} call(s)"
    )
    done_calls = [c for c in patch_calls if c["terminal_state"] == ReqState.DONE]
    assert done_calls, (
        f"BIS-S9 contract: patch_terminal_status MUST be called with terminal_state=DONE; "
        f"got calls: {patch_calls!r}"
    )
    assert done_calls[0]["intent_issue_id"] == "intent-1", (
        f"BIS-S9 contract: intent_issue_id must be 'intent-1' from ctx; "
        f"got {done_calls[0]['intent_issue_id']!r}"
    )


# ─── BIS-S10: engine.step PR_CI_RUNNING+PR_CI_TIMEOUT→ESCALATED calls patch_terminal_status ──


async def test_bis_s10_engine_step_escalated_transition_calls_patch_terminal_status(
    monkeypatch,
):
    """
    BIS-S10: engine.step with PR_CI_RUNNING+PR_CI_TIMEOUT → ESCALATED MUST call
    intent_status.patch_terminal_status with intent_issue_id and terminal_state=ESCALATED.
    """
    from orchestrator import engine, intent_status
    from orchestrator.actions import REGISTRY

    _patch_engine_io(monkeypatch)
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    patch_calls: list[dict] = []

    async def _fake_patch(*, project_id, intent_issue_id, terminal_state, source):
        patch_calls.append({
            "project_id": project_id,
            "intent_issue_id": intent_issue_id,
            "terminal_state": terminal_state,
            "source": source,
        })
        return True

    monkeypatch.setattr(intent_status, "patch_terminal_status", _fake_patch)

    async def _noop_escalate(**_kw):
        return {"escalated": True}

    REGISTRY["escalate"] = _noop_escalate

    result = await engine.step(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=[_REQ_ID],
        cur_state=ReqState.PR_CI_RUNNING,
        ctx={"intent_issue_id": "intent-2"},
        event=Event.PR_CI_TIMEOUT,
        depth=0,
    )

    await asyncio.sleep(0)

    escalated_calls = [c for c in patch_calls if c["terminal_state"] == ReqState.ESCALATED]
    assert escalated_calls, (
        f"BIS-S10 contract: patch_terminal_status MUST be called with terminal_state=ESCALATED "
        f"on PR_CI_RUNNING+PR_CI_TIMEOUT→ESCALATED; got calls: {patch_calls!r}"
    )
    assert escalated_calls[0]["intent_issue_id"] == "intent-2", (
        f"BIS-S10 contract: intent_issue_id must be 'intent-2' from ctx; "
        f"got {escalated_calls[0]['intent_issue_id']!r}"
    )


# ─── BIS-S11: escalate SESSION_FAILED retry exhausted → patch_terminal_status(ESCALATED) ──


async def test_bis_s11_session_failed_exhausted_calls_patch_terminal_status(monkeypatch):
    """
    BIS-S11: escalate invoked with session.failed + retry exhausted (auto_retry_count=2)
    MUST call intent_status.patch_terminal_status exactly once with terminal_state=ESCALATED
    and the resolved intent_issue_id. Any PATCH failure MUST NOT prevent escalate completing.
    """
    from orchestrator import intent_status
    from orchestrator import k8s_runner as krunner
    from orchestrator.actions import escalate as mod
    from orchestrator.store import req_state as rs

    fake_bkd = _make_bkd_fake()
    _patch_escalate_bkd(monkeypatch, fake_bkd)
    _patch_escalate_db(monkeypatch)

    class _Row:
        state = ReqState.REVIEW_RUNNING

    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        krunner,
        "get_controller",
        lambda: type("Ctrl", (), {"cleanup_runner": AsyncMock()})(),
    )

    patch_calls: list[dict] = []

    async def _fake_patch(*, project_id, intent_issue_id, terminal_state, source):
        patch_calls.append({
            "project_id": project_id,
            "intent_issue_id": intent_issue_id,
            "terminal_state": terminal_state,
            "source": source,
        })
        return True

    monkeypatch.setattr(intent_status, "patch_terminal_status", _fake_patch)

    body = _make_body(issue_id="verifier-1", event="session.failed")
    out = await mod.escalate(
        body=body,
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-s11",
            "auto_retry_count": 2,
        },
    )

    assert out.get("escalated") is True, (
        f"BIS-S11 contract: escalate with retry exhausted must set escalated=True; got {out!r}"
    )

    escalated_calls = [c for c in patch_calls if c["terminal_state"] == ReqState.ESCALATED]
    assert len(escalated_calls) == 1, (
        f"BIS-S11 contract: patch_terminal_status(ESCALATED) MUST be called exactly once; "
        f"got {len(escalated_calls)} call(s): {patch_calls!r}"
    )
    assert escalated_calls[0]["intent_issue_id"] == "intent-s11", (
        f"BIS-S11 contract: intent_issue_id must match ctx.intent_issue_id='intent-s11'; "
        f"got {escalated_calls[0]['intent_issue_id']!r}"
    )


# ─── BIS-S12: PR-merged override keeps single merge_tags_and_update, no extra patch_terminal_status ──


async def test_bis_s12_pr_merged_override_single_merge_no_extra_patch_terminal_status(
    monkeypatch,
):
    """
    BIS-S12: When escalate triggers _apply_pr_merged_done_override (all involved-repo PRs
    merged), MUST call bkd.merge_tags_and_update exactly once with add containing both
    "done" and "via:pr-merge" AND status_id="done".
    The override path MUST NOT additionally invoke intent_status.patch_terminal_status
    for the DONE inner CAS (intent_status may be called for the ESCALATED CAS, but NOT
    for the DONE override CAS — the override's merge_tags_and_update is the single DONE PATCH).
    """
    from orchestrator import intent_status
    from orchestrator import k8s_runner as krunner
    from orchestrator.actions import escalate as mod
    from orchestrator.store import req_state as rs

    fake_bkd = _make_bkd_fake()
    _patch_escalate_bkd(monkeypatch, fake_bkd)
    _patch_escalate_db(monkeypatch)

    class _Row:
        state = ReqState.REVIEW_RUNNING

    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        krunner,
        "get_controller",
        lambda: type("Ctrl", (), {"cleanup_runner": AsyncMock()})(),
    )

    # Track patch_terminal_status calls to verify DONE is NOT invoked via intent_status
    patch_calls: list[dict] = []

    async def _fake_patch(*, project_id, intent_issue_id, terminal_state, source):
        patch_calls.append({"terminal_state": terminal_state, "source": source})
        return True

    monkeypatch.setattr(intent_status, "patch_terminal_status", _fake_patch)

    # Mock GitHub PR API to return a merged PR (triggers _apply_pr_merged_done_override)
    import httpx

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    merged_pr = [{"number": 1, "head": {"sha": "abc123"}, "merged_at": "2026-04-27T00:00:00Z"}]

    class _FakeHttpxClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def get(self, url, **kw): return _FakeResponse(merged_pr)

    monkeypatch.setattr("orchestrator.actions.escalate.httpx.AsyncClient", _FakeHttpxClient)

    body = _make_body(issue_id="src-pr-1", event="session.completed")
    out = await mod.escalate(
        body=body,
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-s12",
            "auto_retry_count": 2,
            "involved_repos": [
                {"full_name": "owner/repo-a", "pr_number": 1}
            ],
        },
    )

    # merge_tags_and_update MUST be called exactly once with status_id="done"
    fake_bkd.merge_tags_and_update.assert_awaited_once()
    mtu_args_str = str(fake_bkd.merge_tags_and_update.await_args)
    assert "done" in mtu_args_str, (
        f"BIS-S12 contract: merge_tags_and_update must be called with status_id='done'; "
        f"args: {fake_bkd.merge_tags_and_update.await_args!r}"
    )
    assert "via:pr-merge" in mtu_args_str, (
        f"BIS-S12 contract: merge_tags_and_update must include 'via:pr-merge' in add; "
        f"args: {fake_bkd.merge_tags_and_update.await_args!r}"
    )

    # The override CAS to DONE MUST NOT additionally call patch_terminal_status(DONE)
    done_patch_calls = [c for c in patch_calls if c["terminal_state"] == ReqState.DONE]
    assert len(done_patch_calls) == 0, (
        f"BIS-S12 contract: override path MUST NOT invoke patch_terminal_status for DONE CAS "
        f"(merge_tags_and_update is the single DONE PATCH); "
        f"got {len(done_patch_calls)} extra call(s): {done_patch_calls!r}"
    )
