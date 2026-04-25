"""Contract tests for webhook early noise filter (REQ-router-noise-filter-1777109307).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-router-noise-filter-1777109307/specs/router-noise-filter/spec.md

Scenarios covered:
  RNF-S1  issue.updated 无 REQ tag 无 intent tag → skip (noise filter)
  RNF-S2  issue.updated 含 REQ tag → 走下游 (engine.step 被调用)
  RNF-S3  issue.updated 仅含 intent:intake tag → 走下游 (engine.step 被调用)
  RNF-S4  issue.updated 仅含 intent:analyze tag → 走下游 (engine.step 被调用)
  RNF-S5  session.completed 无 REQ tag → 旧 filter 仍生效 (skip)
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

# ─── Helpers ──────────────────────────────────────────────────────────────────


class _FakePool:
    def __init__(self):
        self.executed: list = []

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _make_request(event: str, tags: list[str], issue_id: str = "issue-rnf") -> object:
    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self_inner):
            return {
                "event": event,
                "issueId": issue_id,
                "projectId": "proj-rnf",
                "executionId": "exec-rnf",
                "tags": tags,
            }

    return _Req()


def _mock_bkd(monkeypatch, webhook_mod, tags: list[str]) -> None:
    """Stub BKDClient so get_issue never hits network."""

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


def _setup_for_skip(monkeypatch) -> dict:
    """
    Minimal mock setup for noise-filter skip scenarios.
    Returns dict with call-tracking lists.
    """
    import orchestrator.observability as obs
    from orchestrator import router as router_lib
    from orchestrator.store import db, dedup

    mark_calls: list = []
    obs_calls: list = []
    derive_calls: list = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock(side_effect=lambda *a, **kw: mark_calls.append(a)))
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock(side_effect=lambda *a, **kw: obs_calls.append(a)))

    orig_derive = router_lib.derive_event

    def _tracked_derive(evt, tags):
        derive_calls.append((evt, tags))
        return orig_derive(evt, tags)

    monkeypatch.setattr(router_lib, "derive_event", _tracked_derive)

    return {
        "mark_calls": mark_calls,
        "obs_calls": obs_calls,
        "derive_calls": derive_calls,
    }


def _setup_for_passthrough(monkeypatch, *, req_id: str | None, derive_event_val) -> dict:
    """
    Full mock setup for pass-through scenarios (noise filter should NOT fire).
    Returns dict with call-tracking lists.
    """
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import ReqState
    from orchestrator.store import db, dedup
    from orchestrator.store import req_state as rs_mod

    step_calls: list = []
    obs_calls: list = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock(side_effect=lambda *a, **kw: obs_calls.append(a)))
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: req_id)
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: derive_event_val)

    class _Row:
        state = ReqState.INIT
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(rs_mod, "update_context", AsyncMock())

    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {"action": "noop"}),
    )

    _mock_bkd(monkeypatch, webhook, [])

    return {
        "step_calls": step_calls,
        "obs_calls": obs_calls,
        "webhook": webhook,
    }


# ─── RNF-S1: issue.updated 无 REQ tag 无 intent tag → skip ──────────────────


async def test_rnf_s1_issue_updated_no_req_no_intent_is_skipped(monkeypatch):
    """
    RNF-S1: webhook 收到 issue.updated，tags 不含 REQ-* 也不含 intent:intake /
    intent:analyze 时，必须命中 noise filter，返回 action=skip + 含 issue.updated 的
    reason；dedup.mark_processed 被调用一次；obs.record_event / derive_event /
    engine.step 均不被调用。
    """
    from orchestrator import engine, webhook

    tracking = _setup_for_skip(monkeypatch)
    _mock_bkd(monkeypatch, webhook, ["bug", "frontend"])

    step_calls: list = []
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {"action": "noop"}),
    )

    req = _make_request("issue.updated", ["bug", "frontend"])
    result = await webhook.webhook(req)

    assert isinstance(result, dict), f"Expected dict response, got {type(result)}"
    assert result.get("action") == "skip", (
        f"RNF-S1: handler must return action='skip' for noise event, "
        f"got action={result.get('action')!r}"
    )

    reason = result.get("reason", "")
    assert "issue.updated" in reason, (
        f"RNF-S1: skip reason must identify 'issue.updated' noise, got reason={reason!r}"
    )

    assert len(tracking["mark_calls"]) == 1, (
        f"RNF-S1: dedup.mark_processed must be called exactly once so BKD at-least-once "
        f"retry also short-circuits; called {len(tracking['mark_calls'])} times"
    )

    assert len(tracking["obs_calls"]) == 0, (
        f"RNF-S1: obs.record_event must NOT be called for noise events "
        f"(must not pollute event log); called {len(tracking['obs_calls'])} times"
    )

    assert len(tracking["derive_calls"]) == 0, (
        f"RNF-S1: router.derive_event must NOT be called for noise events; "
        f"called {len(tracking['derive_calls'])} times"
    )

    assert len(step_calls) == 0, (
        f"RNF-S1: engine.step must NOT be called for noise events; "
        f"called {len(step_calls)} times"
    )


# ─── RNF-S2: issue.updated 含 REQ tag → 走下游 ──────────────────────────────


async def test_rnf_s2_issue_updated_with_req_tag_proceeds(monkeypatch):
    """
    RNF-S2: webhook 收到 issue.updated，tags 含 REQ-* tag 时，
    noise filter 不命中，handler 继续走下游，engine.step 至少被调用一次。
    """
    from orchestrator.state import Event

    tracking = _setup_for_passthrough(
        monkeypatch,
        req_id="REQ-rnf-s2-test",
        derive_event_val=Event.SESSION_FAILED,
    )

    req = _make_request("issue.updated", ["REQ-rnf-s2-test", "analyze"])
    await tracking["webhook"].webhook(req)

    assert len(tracking["step_calls"]) >= 1, (
        f"RNF-S2: engine.step must be called when issue.updated has a REQ-* tag; "
        f"called {len(tracking['step_calls'])} times"
    )


# ─── RNF-S3: issue.updated 仅含 intent:intake tag → 走下游 ──────────────────


async def test_rnf_s3_issue_updated_intent_intake_proceeds(monkeypatch):
    """
    RNF-S3: webhook 收到 issue.updated，tags 含 intent:intake（无 REQ-* tag）时，
    noise filter 不命中（intent 入口必须放行），handler 继续走下游，
    engine.step 至少被调用一次。
    """
    from orchestrator import router as router_lib
    from orchestrator.state import Event

    tracking = _setup_for_passthrough(
        monkeypatch,
        req_id=None,
        derive_event_val=Event.INTENT_INTAKE,
    )
    # No REQ tag in intent:intake event — extract_req_id returns None
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: None)

    req = _make_request("issue.updated", ["intent:intake"], issue_id="issue-rnf-s3")
    await tracking["webhook"].webhook(req)

    assert len(tracking["step_calls"]) >= 1, (
        f"RNF-S3: engine.step must be called for issue.updated with intent:intake; "
        f"called {len(tracking['step_calls'])} times"
    )


# ─── RNF-S4: issue.updated 仅含 intent:analyze tag → 走下游 ─────────────────


async def test_rnf_s4_issue_updated_intent_analyze_proceeds(monkeypatch):
    """
    RNF-S4: webhook 收到 issue.updated，tags 含 intent:analyze（无 REQ-* tag）时，
    noise filter 不命中，handler 继续走下游，engine.step 至少被调用一次。
    """
    from orchestrator import router as router_lib
    from orchestrator.state import Event

    tracking = _setup_for_passthrough(
        monkeypatch,
        req_id=None,
        derive_event_val=Event.INTENT_ANALYZE,
    )
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: None)

    req = _make_request("issue.updated", ["intent:analyze"], issue_id="issue-rnf-s4")
    await tracking["webhook"].webhook(req)

    assert len(tracking["step_calls"]) >= 1, (
        f"RNF-S4: engine.step must be called for issue.updated with intent:analyze; "
        f"called {len(tracking['step_calls'])} times"
    )


# ─── RNF-S5: session.completed 无 REQ tag → 旧 filter 仍生效 ────────────────


async def test_rnf_s5_session_completed_no_req_still_skipped(monkeypatch):
    """
    RNF-S5: 新 issue.updated noise filter 加入后，既有 session.completed filter
    必须仍然生效：session.completed 且 tags 无 REQ-* 时返回 action=skip，
    reason 包含 'session'，mark_processed 被调用，engine.step 不被调用。
    """
    from orchestrator import engine, webhook

    tracking = _setup_for_skip(monkeypatch)
    _mock_bkd(monkeypatch, webhook, ["analyze"])

    step_calls: list = []
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {"action": "noop"}),
    )

    req = _make_request("session.completed", ["analyze"])
    result = await webhook.webhook(req)

    assert isinstance(result, dict), f"Expected dict response, got {type(result)}"
    assert result.get("action") == "skip", (
        f"RNF-S5: session.completed without REQ tag must return action=skip; "
        f"got action={result.get('action')!r}"
    )

    reason = result.get("reason", "")
    assert "session" in reason, (
        f"RNF-S5: skip reason for session.completed must mention 'session'; "
        f"got reason={reason!r}"
    )

    assert len(tracking["mark_calls"]) == 1, (
        f"RNF-S5: mark_processed must be called exactly once; "
        f"called {len(tracking['mark_calls'])} times"
    )

    assert len(step_calls) == 0, (
        f"RNF-S5: engine.step must NOT be called when session.completed is noise; "
        f"called {len(step_calls)} times"
    )
