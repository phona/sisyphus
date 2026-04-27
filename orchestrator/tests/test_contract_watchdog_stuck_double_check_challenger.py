"""Challenger contract tests: watchdog reconciles BKD sessionStatus for CHALLENGER_RUNNING.

REQ-441 — watchdog 判 stuck 前 reconcile BKD sessionStatus

Black-box contracts derived exclusively from:
  openspec/changes/REQ-441/specs/watchdog-stuck-double-check/spec.md
  openspec/changes/REQ-441/specs/watchdog-stuck-double-check/contract.spec.yaml

Scenarios covered:
  WSD-S1  CHALLENGER_RUNNING + session=running + stuck > ended_sec → no escalation
  WSD-S2  CHALLENGER_RUNNING + session=failed  + stuck > ended_sec → escalate (SESSION_FAILED)
  WSD-S3  _STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING] == "challenger_issue_id"

Invariants checked:
  WSD-INV-2  _STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING] == "challenger_issue_id"
  WSD-INV-3  session=running → escalated=0, no engine.step, no artifact_checks call
  WSD-INV-4  session=failed + stuck >= ended_sec → engine.step(event=SESSION_FAILED)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import watchdog
from orchestrator.state import Event, ReqState

# ─── shared fakes ────────────────────────────────────────────────────────────


@dataclass
class _FakePool:
    rows: list = field(default_factory=list)
    executed: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


@dataclass
class _FakeIssue:
    session_status: str | None = "failed"
    id: str = "ch-issue-1"
    project_id: str = "proj-1"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = field(default_factory=list)


def _make_row(state: str, *, req_id: str = "REQ-441", project_id: str = "proj-1",
              ctx: dict | None = None, stuck_sec: int = 400) -> dict:
    return {
        "req_id": req_id,
        "project_id": project_id,
        "state": state,
        "context": json.dumps(ctx or {}),
        "stuck_sec": stuck_sec,
    }


def _patch_pool(monkeypatch, pool: _FakePool) -> None:
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_bkd(monkeypatch, issue: _FakeIssue | None) -> AsyncMock:
    fake = AsyncMock()
    fake.get_issue = AsyncMock(return_value=issue)

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    return fake


def _patch_engine(monkeypatch) -> list:
    calls: list = []

    async def fake_step(pool, *, body, req_id, project_id, tags, cur_state,
                        ctx, event, depth=0):
        calls.append({
            "req_id": req_id,
            "cur_state": cur_state,
            "event": event,
            "body_issue": getattr(body, "issueId", None),
        })
        return {}

    monkeypatch.setattr("orchestrator.watchdog.engine.step", fake_step)
    return calls


def _patch_artifact(monkeypatch) -> list:
    calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        calls.append({"req_id": req_id, "stage": stage, "result": result})

    monkeypatch.setattr("orchestrator.watchdog.artifact_checks.insert_check", fake_insert)
    return calls


# ─── WSD-S3 / WSD-INV-2: static key-table check ──────────────────────────────


def test_wsd_s3_challenger_running_in_state_issue_key():
    """WSD-S3 / WSD-INV-2: _STATE_ISSUE_KEY MUST contain CHALLENGER_RUNNING → "challenger_issue_id".

    The spec requires that the watchdog can locate the BKD issue for a
    CHALLENGER_RUNNING REQ via ctx["challenger_issue_id"]. This is only possible
    if _STATE_ISSUE_KEY maps that state to the correct context key.
    """
    assert ReqState.CHALLENGER_RUNNING in watchdog._STATE_ISSUE_KEY, (
        "WSD-INV-2: _STATE_ISSUE_KEY MUST contain ReqState.CHALLENGER_RUNNING"
    )
    assert watchdog._STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING] == "challenger_issue_id", (
        "WSD-INV-2: _STATE_ISSUE_KEY[CHALLENGER_RUNNING] MUST equal 'challenger_issue_id', "
        f"got {watchdog._STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING]!r}"
    )


# ─── WSD-S1 / WSD-INV-3: running session → no escalation ────────────────────


@pytest.mark.asyncio
async def test_wsd_s1_challenger_running_session_running_no_escalation(monkeypatch):
    """WSD-S1 / WSD-INV-3: CHALLENGER_RUNNING + session_status=running → escalated=0.

    Even when stuck_sec exceeds watchdog_session_ended_threshold_sec, if BKD
    reports the challenger session as still running, the watchdog MUST skip
    escalation for that REQ (long-tail protection).
    """
    pool = _FakePool(rows=[
        _make_row(
            ReqState.CHALLENGER_RUNNING.value,
            req_id="REQ-441",
            ctx={"challenger_issue_id": "ch-live", "intent_issue_id": "intent-1"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="running", id="ch-live"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}, (
        f"WSD-S1: expected escalated=0 for running session, got {result}"
    )
    assert step_calls == [], (
        "WSD-INV-3: engine.step MUST NOT be called when session_status='running'"
    )
    assert art_calls == [], (
        "WSD-INV-3: artifact_checks.insert_check MUST NOT be called when session_status='running'"
    )


# ─── WSD-S2 / WSD-INV-4: failed session → escalate ──────────────────────────


@pytest.mark.asyncio
async def test_wsd_s2_challenger_running_session_failed_escalates(monkeypatch):
    """WSD-S2 / WSD-INV-4: CHALLENGER_RUNNING + session_status=failed + stuck > ended_sec → escalate.

    When BKD reports the challenger session as failed/ended and the REQ has been
    stuck longer than watchdog_session_ended_threshold_sec, the watchdog MUST
    call engine.step with event=SESSION_FAILED for that REQ.
    """
    pool = _FakePool(rows=[
        _make_row(
            ReqState.CHALLENGER_RUNNING.value,
            req_id="REQ-441",
            ctx={"challenger_issue_id": "ch-dead", "intent_issue_id": "intent-2"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="failed", id="ch-dead"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}, (
        f"WSD-S2: expected escalated=1 for failed session, got {result}"
    )
    assert len(step_calls) == 1, (
        f"WSD-INV-4: engine.step MUST be called exactly once, got {len(step_calls)} calls"
    )
    assert step_calls[0]["event"] == Event.SESSION_FAILED, (
        f"WSD-INV-4: engine.step event MUST be SESSION_FAILED, got {step_calls[0]['event']!r}"
    )
    assert step_calls[0]["cur_state"] == ReqState.CHALLENGER_RUNNING, (
        f"WSD-INV-4: cur_state MUST be CHALLENGER_RUNNING, got {step_calls[0]['cur_state']!r}"
    )
    assert step_calls[0]["body_issue"] == "ch-dead", (
        f"WSD-INV-4: body.issueId MUST be the challenger issue id 'ch-dead', "
        f"got {step_calls[0]['body_issue']!r}"
    )
