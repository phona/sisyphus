"""Challenger contract tests for REQ-stage-runs-token-tracking-1777220172.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-stage-runs-token-tracking-1777220172/specs/stage-runs-token-tracking/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  STR-S1  webhook stamps BKD agent token before engine.step (session.completed, agent stage)
  STR-S2  AGENT_STAGES excludes mechanical checkers — spec_lint etc. stay NULL
  STR-S3  webhook with externalSessionId=null skips stamp (no DB write)
  STR-S4  session.failed also stamps for crash diagnostics
  STR-S5  stamp_bkd_session_id SQL targets ended_at IS NULL AND bkd_session_id IS NULL
  STR-S6  stamp_bkd_session_id with empty token returns None with no SQL emitted
  STR-S7  _to_issue extracts externalSessionId from BKD payload into Issue.external_session_id
  STR-S8  _to_issue defaults external_session_id to None when field absent (no KeyError)

Module contracts under test:
  orchestrator.bkd._to_issue / Issue.external_session_id   (STR-S7, STR-S8)
  orchestrator.store.stage_runs.stamp_bkd_session_id       (STR-S5, STR-S6)
  orchestrator.engine.AGENT_STAGES                         (STR-S2 boundary)
  orchestrator.webhook POST /bkd-events                    (STR-S1, STR-S3, STR-S4)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest


# ─── Minimal FakePool (shared by SQL contract tests) ─────────────────────────


class _FakePool:
    """Minimal asyncpg pool stub: captures all SQL calls and returns preset values."""

    def __init__(self, fetchrow_returns: tuple = ()):
        self._returns = list(fetchrow_returns)
        self._pos = 0
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        self.fetchrow_calls.append((sql, args))
        if self._pos < len(self._returns):
            val = self._returns[self._pos]
            self._pos += 1
            return val
        return None

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))

    async def fetch(self, sql: str, *args):
        return []

    async def fetchval(self, sql: str, *args):
        return None


# ─── STR-S7: _to_issue extracts externalSessionId ────────────────────────────


def test_str_s7_to_issue_extracts_external_session_id() -> None:
    """
    STR-S7: _to_issue with a payload containing externalSessionId MUST set
    Issue.external_session_id to that UUID string.
    """
    from orchestrator.bkd import _to_issue

    payload = {
        "id": "issue-str-s7",
        "projectId": "proj-test",
        "title": "test s7",
        "tags": ["REQ-stage-runs-token-tracking-1777220172"],
        "statusId": "working",
        "issueNumber": 1,
        "sessionStatus": "completed",
        "externalSessionId": "a742034b-6fb0-4047-be96-d5431dc1f252",
    }
    issue = _to_issue(payload)

    assert issue.external_session_id == "a742034b-6fb0-4047-be96-d5431dc1f252", (
        "STR-S7: Issue.external_session_id MUST equal the payload's externalSessionId; "
        f"got {issue.external_session_id!r}"
    )


# ─── STR-S8: _to_issue defaults external_session_id to None ──────────────────


def test_str_s8_to_issue_defaults_external_session_id_to_none() -> None:
    """
    STR-S8: _to_issue with payload that omits externalSessionId MUST set
    Issue.external_session_id to None — no KeyError, not an empty string.
    """
    from orchestrator.bkd import _to_issue

    payload = {
        "id": "issue-str-s8",
        "projectId": "proj-test",
        "title": "test s8 no session",
        "tags": [],
        "statusId": "todo",
        "issueNumber": 2,
        "sessionStatus": None,
        # externalSessionId key deliberately absent
    }

    try:
        issue = _to_issue(payload)
    except KeyError as exc:
        pytest.fail(
            "STR-S8: _to_issue MUST NOT raise KeyError when externalSessionId is absent; "
            f"got KeyError({exc})"
        )

    assert issue.external_session_id is None, (
        "STR-S8: Issue.external_session_id MUST default to None when field absent; "
        f"got {issue.external_session_id!r}"
    )


# ─── STR-S5: stamp SQL targets ended_at IS NULL AND bkd_session_id IS NULL ───


async def test_str_s5_stamp_sql_targets_open_and_null_token_row() -> None:
    """
    STR-S5: stamp_bkd_session_id SQL MUST contain 'ended_at IS NULL' AND
    'bkd_session_id IS NULL' in its WHERE clause, ensuring that closed rows
    and already-stamped rows are never mutated.

    The helper uses an UPDATE...RETURNING query via pool.fetchrow, so we
    inspect fetchrow_calls (not execute_calls) for the SQL contract.
    """
    from orchestrator.store.stage_runs import stamp_bkd_session_id

    # Return a row with numeric id (asyncpg pg_bigint → int)
    pool = _FakePool(fetchrow_returns=({"id": 42},))
    row_id = await stamp_bkd_session_id(pool, "REQ-7", "analyze", "new-sess")

    assert len(pool.fetchrow_calls) == 1, (
        f"STR-S5: stamp_bkd_session_id MUST emit exactly 1 SQL call (UPDATE…RETURNING via fetchrow); "
        f"got {len(pool.fetchrow_calls)}"
    )
    sql_lower = pool.fetchrow_calls[0][0].lower()

    assert "ended_at is null" in sql_lower, (
        "STR-S5: SQL MUST contain 'ended_at IS NULL'; "
        f"actual SQL:\n{pool.fetchrow_calls[0][0]}"
    )
    assert "bkd_session_id is null" in sql_lower, (
        "STR-S5: SQL MUST contain 'bkd_session_id IS NULL'; "
        f"actual SQL:\n{pool.fetchrow_calls[0][0]}"
    )


# ─── STR-S6: empty token is a no-op ──────────────────────────────────────────


async def test_str_s6_empty_token_is_noop() -> None:
    """
    STR-S6: stamp_bkd_session_id with an empty string token MUST:
      - return None immediately
      - emit NO SQL (no round-trip to DB)
    """
    from orchestrator.store.stage_runs import stamp_bkd_session_id

    pool = _FakePool()
    result = await stamp_bkd_session_id(pool, "REQ-1", "analyze", "")

    assert result is None, (
        f"STR-S6: stamp_bkd_session_id('') MUST return None; got {result!r}"
    )
    assert pool.execute_calls == [], (
        f"STR-S6: stamp_bkd_session_id('') MUST emit NO SQL; "
        f"got {len(pool.execute_calls)} execute call(s)"
    )


# ─── STR-S2: AGENT_STAGES boundary ───────────────────────────────────────────


def test_agent_stages_contains_all_spec_required_stages() -> None:
    """
    Boundary for STR-S1/S2: engine.AGENT_STAGES MUST be a frozenset containing
    all five agent stages: analyze, verifier, fixer, accept, archive.
    """
    from orchestrator.engine import AGENT_STAGES

    required = {"analyze", "verifier", "fixer", "accept", "archive"}
    assert isinstance(AGENT_STAGES, frozenset), (
        f"AGENT_STAGES MUST be frozenset; got {type(AGENT_STAGES).__name__!r}"
    )
    assert required <= AGENT_STAGES, (
        f"AGENT_STAGES MUST contain {required!r}; missing: {required - AGENT_STAGES!r}"
    )


def test_agent_stages_excludes_mechanical_checkers() -> None:
    """
    STR-S2: mechanical stage names MUST NOT appear in AGENT_STAGES.
    spec_lint, dev_cross_check, staging_test, pr_ci, accept_teardown
    have no BKD agent — their bkd_session_id column MUST stay NULL.
    """
    from orchestrator.engine import AGENT_STAGES

    mechanical = {"spec_lint", "dev_cross_check", "staging_test", "pr_ci", "accept_teardown"}
    overlap = mechanical & AGENT_STAGES
    assert overlap == set(), (
        f"AGENT_STAGES MUST NOT contain mechanical stages; "
        f"found unexpected overlap: {overlap!r}"
    )


# ─── Webhook stamp-ordering tests (STR-S1, STR-S3, STR-S4) ──────────────────
#
# These tests POST to /bkd-events via FastAPI's ASGI transport, monkeypatching
# all I/O (DB pool, BKD HTTP, engine.step) to control inputs and capture outputs.
# The call_order list is the key artifact: it proves stamp precedes step (STR-S1).


@dataclass
class _FakeReqRow:
    state: Any
    context: dict = field(default_factory=dict)


def _make_fake_issue(tags: list[str], external_session_id: str | None):
    """Build a minimal orchestrator.bkd.Issue with all required fields."""
    from orchestrator.bkd import Issue

    return Issue(
        id="bkd-issue-id",
        project_id="proj-test",
        issue_number=99,
        title="test issue",
        status_id="working",
        tags=tags,
        session_status="completed",
        external_session_id=external_session_id,
    )


def _patch_webhook_deps(
    monkeypatch,
    *,
    bkd_tags: list[str],
    bkd_external_session_id: str | None,
    req_state_value: Any,
    derive_event_return: Any,
    call_order: list[str],
    stamp_calls: list[tuple],
    step_calls: list[dict],
) -> _FakePool:
    """Monkeypatch all external I/O in the webhook module for a single test."""
    from orchestrator import webhook
    from orchestrator.state import Event

    pool = _FakePool()

    # Pool
    monkeypatch.setattr(webhook.db, "get_pool", lambda: pool)

    # Dedup: always "new" (first call), then mark_processed is no-op
    monkeypatch.setattr(webhook.dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(webhook.dedup, "mark_processed", AsyncMock(return_value=None))

    # Observability: no-op
    monkeypatch.setattr(webhook.obs, "record_event", AsyncMock(return_value=None))

    # _push_upstream_status: no-op (session.completed upstream push)
    monkeypatch.setattr(webhook, "_push_upstream_status", AsyncMock(return_value=None))

    # BKDClient: return controlled Issue with desired external_session_id
    fake_issue = _make_fake_issue(bkd_tags, bkd_external_session_id)

    class _FakeBKD:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get_issue(self, _project_id, _issue_id):
            return fake_issue

        async def update_issue(self, **_kw):
            pass

        async def get_last_assistant_message(self, *_a):
            return None

    monkeypatch.setattr(webhook, "BKDClient", lambda *_a, **_kw: _FakeBKD())

    # router_lib: controlled event derivation and req_id extraction
    monkeypatch.setattr(
        webhook.router_lib, "derive_event", lambda _event, _tags: derive_event_return
    )
    monkeypatch.setattr(
        webhook.router_lib, "extract_req_id", lambda _tags, _num=None: "REQ-test-stamp-x"
    )

    # req_state: return controlled state row
    fake_row = _FakeReqRow(state=req_state_value)
    monkeypatch.setattr(webhook.req_state, "get", AsyncMock(return_value=fake_row))
    monkeypatch.setattr(webhook.req_state, "update_context", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook.req_state, "insert_init", AsyncMock(return_value=None))

    # verifier_decisions: no-op
    monkeypatch.setattr(
        webhook.verifier_decisions, "insert_decision", AsyncMock(return_value=None)
    )

    # stage_runs: track stamp calls AND call order
    class _TrackingStageRuns:
        async def stamp_bkd_session_id(self, pool_arg, req_id, stage, bkd_session_id):
            call_order.append("stamp")
            stamp_calls.append((req_id, stage, bkd_session_id))
            return "tracked-row-id"

        def __getattr__(self, name):
            return AsyncMock(return_value=None)

    monkeypatch.setattr(webhook, "stage_runs", _TrackingStageRuns())

    # engine.step: track call order
    async def _fake_step(*_a, **kw):
        call_order.append("step")
        step_calls.append(kw)
        return {"action": "ok", "req_id": kw.get("req_id", "?")}

    monkeypatch.setattr(webhook.engine, "step", _fake_step)

    return pool


# STR-S1: session.completed for agent stage → stamp BEFORE step ───────────────


async def test_str_s1_stamp_called_before_engine_step(monkeypatch) -> None:
    """
    STR-S1: When webhook receives session.completed for ANALYZING (analyze stage):
      - stamp_bkd_session_id MUST be called exactly once
      - The call MUST precede engine.step
    """
    from httpx import ASGITransport, AsyncClient
    from orchestrator.main import app
    from orchestrator.state import Event, ReqState

    call_order: list[str] = []
    stamp_calls: list[tuple] = []
    step_calls: list[dict] = []

    _patch_webhook_deps(
        monkeypatch,
        bkd_tags=["REQ-stage-runs-token-tracking-1777220172", "analyze"],
        bkd_external_session_id="sess-analyze-uuid",
        req_state_value=ReqState.ANALYZING,
        derive_event_return=Event.ANALYZE_DONE,
        call_order=call_order,
        stamp_calls=stamp_calls,
        step_calls=step_calls,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/bkd-events",
            json={
                "event": "session.completed",
                "issueId": "analyze-bkd-issue-id",
                "projectId": "proj-test",
                "executionId": "exec-str-s1",
            },
            headers={"Authorization": "Bearer test-webhook-token"},
        )

    assert response.status_code == 200, (
        f"STR-S1: webhook MUST return 200; got {response.status_code}: {response.text}"
    )
    assert len(stamp_calls) == 1, (
        f"STR-S1: stamp_bkd_session_id MUST be called exactly once for agent stage; "
        f"got {len(stamp_calls)} call(s): {stamp_calls}"
    )
    assert stamp_calls[0][2] == "sess-analyze-uuid", (
        f"STR-S1: stamp MUST carry externalSessionId='sess-analyze-uuid'; "
        f"got {stamp_calls[0][2]!r}"
    )

    assert "stamp" in call_order and "step" in call_order, (
        f"STR-S1: both stamp and step MUST appear in call_order; got {call_order!r}"
    )
    stamp_idx = call_order.index("stamp")
    step_idx = call_order.index("step")
    assert stamp_idx < step_idx, (
        f"STR-S1: stamp MUST precede engine.step; "
        f"stamp at index {stamp_idx}, step at index {step_idx} in {call_order!r}"
    )


# STR-S3: null externalSessionId → stamp NOT called ───────────────────────────


async def test_str_s3_null_external_session_id_skips_stamp(monkeypatch) -> None:
    """
    STR-S3: When webhook receives session.completed with externalSessionId=null:
      - stamp_bkd_session_id MUST NOT be called
      (no empty/NULL token should be written to the DB)
    """
    from httpx import ASGITransport, AsyncClient
    from orchestrator.main import app
    from orchestrator.state import Event, ReqState

    call_order: list[str] = []
    stamp_calls: list[tuple] = []
    step_calls: list[dict] = []

    _patch_webhook_deps(
        monkeypatch,
        bkd_tags=["REQ-stage-runs-token-tracking-1777220172", "analyze"],
        bkd_external_session_id=None,  # null — BKD hasn't assigned a session UUID yet
        req_state_value=ReqState.ANALYZING,
        derive_event_return=Event.ANALYZE_DONE,
        call_order=call_order,
        stamp_calls=stamp_calls,
        step_calls=step_calls,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/bkd-events",
            json={
                "event": "session.completed",
                "issueId": "analyze-issue-no-session-id",
                "projectId": "proj-test",
                "executionId": "exec-str-s3",
            },
            headers={"Authorization": "Bearer test-webhook-token"},
        )

    assert response.status_code == 200, (
        f"STR-S3: webhook MUST return 200; got {response.status_code}: {response.text}"
    )
    assert len(stamp_calls) == 0, (
        f"STR-S3: stamp_bkd_session_id MUST NOT be called when externalSessionId is null; "
        f"got {len(stamp_calls)} call(s): {stamp_calls}"
    )


# STR-S4: session.failed also stamps ─────────────────────────────────────────


async def test_str_s4_session_failed_also_stamps(monkeypatch) -> None:
    """
    STR-S4: When webhook receives session.failed for REVIEW_RUNNING (verifier stage):
      - The webhook MUST fetch the BKD issue (extending to session.failed path)
      - stamp_bkd_session_id MUST be called with the crashed session's externalSessionId
    """
    from httpx import ASGITransport, AsyncClient
    from orchestrator.main import app
    from orchestrator.state import Event, ReqState

    call_order: list[str] = []
    stamp_calls: list[tuple] = []
    step_calls: list[dict] = []

    _patch_webhook_deps(
        monkeypatch,
        bkd_tags=["REQ-stage-runs-token-tracking-1777220172", "verifier"],
        bkd_external_session_id="sess-verifier-crashed",
        req_state_value=ReqState.REVIEW_RUNNING,
        derive_event_return=Event.SESSION_FAILED,
        call_order=call_order,
        stamp_calls=stamp_calls,
        step_calls=step_calls,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/bkd-events",
            json={
                "event": "session.failed",
                "issueId": "verifier-bkd-issue-id",
                "projectId": "proj-test",
                "executionId": "exec-str-s4",
            },
            headers={"Authorization": "Bearer test-webhook-token"},
        )

    assert response.status_code == 200, (
        f"STR-S4: webhook MUST return 200; got {response.status_code}: {response.text}"
    )
    assert len(stamp_calls) == 1, (
        f"STR-S4: stamp_bkd_session_id MUST be called once for session.failed on agent stage; "
        f"got {len(stamp_calls)} call(s): {stamp_calls}"
    )
    assert stamp_calls[0][2] == "sess-verifier-crashed", (
        f"STR-S4: stamp MUST carry the crashed session's externalSessionId; "
        f"got {stamp_calls[0][2]!r}"
    )
