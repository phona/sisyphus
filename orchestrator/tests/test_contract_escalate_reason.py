"""Contract tests for escalated_reason on verifier decisions (REQ-escalate-reason-verifier-1777076876).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-escalate-reason-verifier-1777076876/specs/escalate-reason/spec.md

Scenarios:
  ERV-S1  verifier escalate sets ctx.escalated_reason="verifier-decision" before engine.step
  ERV-S2  _is_transient("verifier-decision") returns False — no auto-resume
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest


class _FakePool:
    """Minimal asyncpg pool fake: ordered fetchrow returns + recorded execute calls."""

    def __init__(self, fetchrow_returns=()):
        self._returns = list(fetchrow_returns)
        self._pos = 0
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *args):
        if self._pos < len(self._returns):
            v = self._returns[self._pos]
            self._pos += 1
            return v
        return None

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


# ─── ERV-S1: verifier escalate → ctx.escalated_reason = "verifier-decision" ─


async def test_s1_verifier_escalate_sets_reason_verifier_decision(monkeypatch):
    """
    ERV-S1: When a verifier session.completed results in VERIFY_ESCALATE, the webhook MUST
    set ctx.escalated_reason = "verifier-decision" (via update_context) before engine.step.
    """
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import Event, ReqState
    from orchestrator.store import db, dedup
    from orchestrator.store import req_state as rs_mod

    context_updates: list[dict] = []
    step_snapshots: list[list[dict]] = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class R:
                tags: ClassVar = ["REQ-erv1", "verifier", "result:escalate"]

            return R()

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(webhook, "BKDClient", _BKD)
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: "REQ-erv1")
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: Event.VERIFY_ESCALATE)

    class _Row:
        state = ReqState.REVIEW_RUNNING
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())

    async def _capture_update_context(*args, **kwargs):
        # args: (pool, req_id, patch_dict) or similar — capture whatever patch dict is passed
        for a in args:
            if isinstance(a, dict):
                context_updates.append(dict(a))
        for v in kwargs.values():
            if isinstance(v, dict):
                context_updates.append(dict(v))

    monkeypatch.setattr(rs_mod, "update_context", _capture_update_context)

    async def _capture_step(*args, **kwargs):
        step_snapshots.append(list(context_updates))
        return {"action": "ok"}

    monkeypatch.setattr(engine, "step", _capture_step)

    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self):
            return {
                "event": "session.completed",
                "issueId": "issue-erv1",
                "projectId": "proj-erv1",
                "executionId": "exec-erv1",
                "tags": ["REQ-erv1", "verifier", "result:escalate"],
            }

    await webhook.webhook(_Req())

    # Contract 1: update_context must be called with escalated_reason = "verifier-decision"
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, (
        "webhook MUST call update_context with escalated_reason='verifier-decision' "
        f"for VERIFY_ESCALATE. All context updates captured: {context_updates}"
    )
    assert reason_updates[-1]["escalated_reason"] == "verifier-decision", (
        "escalated_reason MUST be 'verifier-decision', "
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )

    # Contract 2: the reason must be set BEFORE engine.step is called
    assert step_snapshots, "engine.step must be called after VERIFY_ESCALATE"
    updates_at_step_time = step_snapshots[0]
    assert any("escalated_reason" in u for u in updates_at_step_time), (
        "ctx.escalated_reason MUST be set via update_context BEFORE engine.step is called. "
        f"context_updates at engine.step call time: {updates_at_step_time}"
    )


# ─── ERV-S2: verifier-decision is not transient → no auto-resume ─────────────


async def test_s2_verifier_decision_not_transient():
    """
    ERV-S2: _is_transient("verifier-decision") MUST return False.
    A verifier escalation is a deliberate AI decision — auto-resume MUST NOT be triggered.
    """
    from orchestrator.actions.escalate import _is_transient

    # body_event="session.completed" simulates normal verifier session completion;
    # reason="verifier-decision" is what webhook sets when VERIFY_ESCALATE is derived.
    result = _is_transient("session.completed", "verifier-decision")

    assert result is False, (
        f"_is_transient('session.completed', 'verifier-decision') MUST return False (non-transient); "
        f"got {result!r}. verifier-decision is an intentional escalation — no auto-resume follow-up."
    )
