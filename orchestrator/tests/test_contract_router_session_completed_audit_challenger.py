"""Contract tests for webhook session.completed escalated_reason audit.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-router-session-completed-audit-1777344435/specs/router-session-completed-audit/spec.md

Scenarios covered:
  RSCA-S1  INTAKE_FAIL session.completed → ctx.escalated_reason = "intake-fail"
  RSCA-S2  PR_CI_TIMEOUT session.completed → ctx.escalated_reason = "pr-ci-timeout"
  RSCA-S3  VERIFY_ESCALATE → ctx.escalated_reason must NOT be overwritten to "session-completed"
  RSCA-S4  challenger without result tag → derive_event returns None (not SESSION_FAILED)
  RSCA-S5  session.completed with no stage tag → derive_event returns None
  RSCA-S6  session.completed with known stage + unrecognized result → derive_event returns None
  RSCA-S7  fixer without result tag → derive_event returns FIXER_DONE
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

# ─── Shared helpers ───────────────────────────────────────────────────────────


class _FakePool:
    def __init__(self):
        self.execute_calls: list = []

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


def _make_session_completed_request(tags: list[str], issue_id: str = "issue-rsca") -> object:
    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self_inner):
            return {
                "event": "session.completed",
                "issueId": issue_id,
                "issueNumber": None,
                "projectId": "proj-rsca",
                "executionId": "exec-rsca",
                "tags": tags,
            }

    return _Req()


def _mock_bkd(monkeypatch, webhook_mod, tags: list[str]) -> None:
    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class _R:
                pass

            _R.tags = tags
            return _R()

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(webhook_mod, "BKDClient", _BKD)


def _setup_webhook_for_escalation(monkeypatch, *, req_id: str, derive_event_val):
    """
    Set up webhook mocks for session.completed escalation path tests.
    Returns a tracking dict with update_ctx_calls and step_calls.
    """
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import ReqState
    from orchestrator.store import db, dedup
    from orchestrator.store import req_state as rs_mod

    update_ctx_calls: list = []
    step_calls: list = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: req_id)
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: derive_event_val)

    class _Row:
        state = ReqState.REVIEW_RUNNING
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(
        rs_mod,
        "update_context",
        AsyncMock(side_effect=lambda *a, **kw: update_ctx_calls.append((list(a), kw))),
    )
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append((list(a), kw)) or {"action": "noop"}),
    )
    _mock_bkd(monkeypatch, webhook, [])

    return {
        "update_ctx_calls": update_ctx_calls,
        "step_calls": step_calls,
        "webhook": webhook,
    }


def _find_escalated_reason(update_ctx_calls: list) -> str | None:
    """
    Inspect all update_context call args for any dict containing "escalated_reason".
    Returns the first value found, or None.
    """
    for args, kwargs in update_ctx_calls:
        # check positional args
        for a in args:
            if isinstance(a, dict) and "escalated_reason" in a:
                return a["escalated_reason"]
        # check keyword args
        for v in kwargs.values():
            if isinstance(v, dict) and "escalated_reason" in v:
                return v["escalated_reason"]
    return None


# ─── RSCA-S1: INTAKE_FAIL → escalated_reason = "intake-fail" ────────────────


async def test_rsca_s1_intake_fail_sets_escalated_reason(monkeypatch):
    """
    RSCA-S1: session.completed with tags ["intake", "REQ-x", "result:fail"] routes to
    INTAKE_FAIL; webhook step 5.8 must pre-set ctx.escalated_reason = "intake-fail"
    before the escalate action fires.
    """
    from orchestrator.state import Event

    tracking = _setup_webhook_for_escalation(
        monkeypatch,
        req_id="REQ-rsca-s1",
        derive_event_val=Event.INTAKE_FAIL,
    )

    req = _make_session_completed_request(["intake", "REQ-rsca-s1", "result:fail"])
    await tracking["webhook"].webhook(req)

    reason = _find_escalated_reason(tracking["update_ctx_calls"])
    assert reason == "intake-fail", (
        f"RSCA-S1: ctx.escalated_reason must be 'intake-fail' for INTAKE_FAIL event; "
        f"got {reason!r}. update_context call args: {tracking['update_ctx_calls']!r}"
    )


# ─── RSCA-S2: PR_CI_TIMEOUT → escalated_reason = "pr-ci-timeout" ────────────


async def test_rsca_s2_pr_ci_timeout_sets_escalated_reason(monkeypatch):
    """
    RSCA-S2: session.completed with tags ["pr-ci", "REQ-x", "pr-ci:timeout"] routes to
    PR_CI_TIMEOUT; webhook step 5.8 must pre-set ctx.escalated_reason = "pr-ci-timeout".
    """
    from orchestrator.state import Event

    tracking = _setup_webhook_for_escalation(
        monkeypatch,
        req_id="REQ-rsca-s2",
        derive_event_val=Event.PR_CI_TIMEOUT,
    )

    req = _make_session_completed_request(["pr-ci", "REQ-rsca-s2", "pr-ci:timeout"])
    await tracking["webhook"].webhook(req)

    reason = _find_escalated_reason(tracking["update_ctx_calls"])
    assert reason == "pr-ci-timeout", (
        f"RSCA-S2: ctx.escalated_reason must be 'pr-ci-timeout' for PR_CI_TIMEOUT event; "
        f"got {reason!r}. update_context call args: {tracking['update_ctx_calls']!r}"
    )


# ─── RSCA-S3: VERIFY_ESCALATE → must NOT overwrite with "session-completed" ──


async def test_rsca_s3_verify_escalate_does_not_produce_session_completed(monkeypatch):
    """
    RSCA-S3: session.completed routed to VERIFY_ESCALATE must retain
    ctx.escalated_reason = "verifier-decision" (or leave it unset).
    It must NOT write "session-completed" as the reason.
    """
    from orchestrator.state import Event

    tracking = _setup_webhook_for_escalation(
        monkeypatch,
        req_id="REQ-rsca-s3",
        derive_event_val=Event.VERIFY_ESCALATE,
    )

    req = _make_session_completed_request(["verifier", "REQ-rsca-s3", "result:pass"])
    await tracking["webhook"].webhook(req)

    # Verify that "session-completed" was never stored as escalated_reason
    for args, kwargs in tracking["update_ctx_calls"]:
        all_dicts = [a for a in args if isinstance(a, dict)]
        all_dicts += [v for v in kwargs.values() if isinstance(v, dict)]
        for d in all_dicts:
            actual = d.get("escalated_reason")
            assert actual != "session-completed", (
                "RSCA-S3: ctx.escalated_reason must NOT be set to 'session-completed' "
                f"for VERIFY_ESCALATE; found {actual!r} in update_context call"
            )

    # engine.step must have been invoked (event was not silently dropped)
    assert len(tracking["step_calls"]) >= 1, (
        "RSCA-S3: engine.step must be called for VERIFY_ESCALATE event; "
        "webhook must not silently drop this event path"
    )


# ─── RSCA-S4: challenger without result → None ───────────────────────────────


def test_rsca_s4_challenger_without_result_returns_none():
    """
    RSCA-S4: derive_event("session.completed", ["challenger", "REQ-x"]) must return
    None, not SESSION_FAILED or any other event.
    """
    from orchestrator.router import derive_event

    result = derive_event("session.completed", ["challenger", "REQ-rsca-s4"])
    assert result is None, (
        "RSCA-S4: derive_event must return None for session.completed with challenger tag "
        f"and no result tag (prevents spurious SESSION_FAILED); got {result!r}"
    )


# ─── RSCA-S5: no stage tag → None ────────────────────────────────────────────


def test_rsca_s5_no_stage_tag_returns_none():
    """
    RSCA-S5: derive_event("session.completed", ["REQ-x"]) — no stage tag at all —
    must return None (silently skip).
    """
    from orchestrator.router import derive_event

    result = derive_event("session.completed", ["REQ-rsca-s5"])
    assert result is None, (
        "RSCA-S5: derive_event must return None for session.completed with no stage tag; "
        f"got {result!r}"
    )


# ─── RSCA-S6: known stage + unrecognized result → None ───────────────────────


def test_rsca_s6_known_stage_unrecognized_result_returns_none():
    """
    RSCA-S6: derive_event("session.completed", ["challenger", "REQ-x", "result:weird"])
    must return None — unknown result variants are silently skipped.
    """
    from orchestrator.router import derive_event

    result = derive_event("session.completed", ["challenger", "REQ-rsca-s6", "result:weird"])
    assert result is None, (
        "RSCA-S6: derive_event must return None for session.completed with known stage tag "
        f"but unrecognized result tag; got {result!r}"
    )


# ─── RSCA-S7: fixer without result → FIXER_DONE ─────────────────────────────


def test_rsca_s7_fixer_without_result_returns_fixer_done():
    """
    RSCA-S7: derive_event("session.completed", ["fixer", "REQ-x"]) must return
    FIXER_DONE — fixer never requires a result tag.
    """
    from orchestrator.router import derive_event
    from orchestrator.state import Event

    result = derive_event("session.completed", ["fixer", "REQ-rsca-s7"])
    assert result == Event.FIXER_DONE, (
        "RSCA-S7: derive_event must return FIXER_DONE for session.completed + fixer tag "
        f"(fixer never needs a result tag); got {result!r}"
    )
