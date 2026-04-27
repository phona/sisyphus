"""Contract tests for REQ-watchdog-stage-policy-1777269909.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-watchdog-stage-policy-1777269909/specs/watchdog-stage-policy/spec.md

Scenarios:
  WSP-S1  INTAKING + session ended + no result tag → no escalation (SQL pre-filter)
  WSP-S2  INTAKING + session running → no escalation (SQL pre-filter)
  WSP-S3  INTAKING + session ended + result:pass → no escalation (SQL pre-filter)
  WSP-S4  ANALYZING stuck + session=failed → still escalates
  WSP-S5  SQL pre-filter args include "intaking"
  WSP-S6  _SESSION_END_SIGNALS no longer lists watchdog.intake_no_result_tag
"""
from __future__ import annotations

from unittest.mock import AsyncMock


class _RecordingPool:
    """Minimal pool fake: records fetch() calls, returns configured rows."""

    def __init__(self, fetch_return=()):
        self._fetch_return = list(fetch_return)
        self.fetch_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        return self._fetch_return

    async def fetchrow(self, sql: str, *args):
        return None

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


def _make_row(
    state: str,
    req_id: str = "REQ-test",
    project_id: str = "proj-test",
    context: dict | None = None,
    stuck_sec: int = 7200,
) -> dict:
    """Create a mock asyncpg row dict for watchdog SQL result."""
    return {
        "req_id": req_id,
        "project_id": project_id,
        "state": state,
        "context": context or {},
        "stuck_sec": stuck_sec,
    }


# ─── WSP-S5: SQL pre-filter MUST contain "intaking" ─────────────────────────


async def test_s5_sql_prefilter_includes_intaking(monkeypatch):
    """WSP-S5: watchdog._tick() MUST pass "intaking" in the state <> ALL($1) arg.

    The first SQL parameter to pool.fetch() is the excluded-states array. It MUST
    contain the literal string "intaking" so INTAKING rows are never returned to
    the per-row _check_and_escalate logic.
    """
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _RecordingPool(fetch_return=[])
    monkeypatch.setattr(db, "get_pool", lambda: pool)

    await watchdog._tick()

    assert pool.fetch_calls, (
        "watchdog._tick() MUST issue at least one pool.fetch() SQL call"
    )
    _sql, args = pool.fetch_calls[0]
    skip_arr = args[0]
    assert "intaking" in skip_arr, (
        "WSP-S5: SQL pre-filter ($1 array) MUST contain 'intaking' to exclude "
        f"INTAKING rows. Got skip_arr={skip_arr!r}"
    )


# ─── WSP-S1: INTAKING + session ended + no result tag → no escalation ────────


async def test_s1_intaking_session_ended_no_tag_no_escalation(monkeypatch):
    """WSP-S1: INTAKING + session=completed + no result tag → SQL pre-filter excludes row.

    No artifact_checks insert, no engine.step, no req_state.update_context.
    The protection is the SQL pre-filter (not a per-row check), so "intaking" MUST
    appear in the excluded-states array passed to pool.fetch().
    """
    from orchestrator import engine, watchdog
    from orchestrator.store import artifact_checks, db

    pool = _RecordingPool(fetch_return=[])
    step_calls: list = []
    artifact_calls: list = []

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {}),
    )
    monkeypatch.setattr(
        artifact_checks,
        "insert_check",
        AsyncMock(side_effect=lambda *a, **kw: artifact_calls.append(a)),
    )

    result = await watchdog._tick()

    skip_arr = pool.fetch_calls[0][1][0]
    assert "intaking" in skip_arr, (
        "WSP-S1: SQL pre-filter must exclude INTAKING "
        f"(session ended + no-result-tag scenario). Got: {skip_arr!r}"
    )
    assert not step_calls, (
        "WSP-S1: engine.step MUST NOT be called — INTAKING rows excluded by SQL. "
        f"Calls: {step_calls}"
    )
    assert not artifact_calls, (
        "WSP-S1: artifact_checks.insert_check MUST NOT be called for INTAKING. "
        f"Calls: {artifact_calls}"
    )
    assert result["escalated"] == 0, (
        f"WSP-S1: escalated count must be 0; got {result}"
    )


# ─── WSP-S2: INTAKING + session running → no escalation ──────────────────────


async def test_s2_intaking_session_running_no_escalation(monkeypatch):
    """WSP-S2: INTAKING + session=running → SQL pre-filter excludes row.

    Prior to this REQ, running-session protection was per-row (still_running flag).
    The new contract is: INTAKING rows never reach per-row logic at all — they are
    excluded by the SQL pre-filter. "intaking" must appear in the SQL skip array.
    """
    from orchestrator import engine, watchdog
    from orchestrator.store import db

    pool = _RecordingPool(fetch_return=[])
    step_calls: list = []

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {}),
    )

    await watchdog._tick()

    skip_arr = pool.fetch_calls[0][1][0]
    assert "intaking" in skip_arr, (
        "WSP-S2: INTAKING must be in SQL pre-filter even when session is running. "
        f"Got: {skip_arr!r}"
    )
    assert not step_calls, (
        "WSP-S2: engine.step MUST NOT be called for INTAKING rows (session=running)"
    )


# ─── WSP-S3: INTAKING + session ended + result:pass → no escalation ──────────


async def test_s3_intaking_session_ended_result_pass_no_escalation(monkeypatch):
    """WSP-S3: INTAKING + session=completed + result:pass → no watchdog escalation.

    Even if the router missed firing INTAKE_PASS, watchdog MUST NOT escalate this row.
    The SQL pre-filter is the enforcement mechanism: "intaking" in the skip array
    ensures the row never enters _check_and_escalate, regardless of BKD tag state.
    """
    from orchestrator import engine, watchdog
    from orchestrator.store import db

    pool = _RecordingPool(fetch_return=[])
    step_calls: list = []

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(
        engine,
        "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {}),
    )

    await watchdog._tick()

    skip_arr = pool.fetch_calls[0][1][0]
    assert "intaking" in skip_arr, (
        "WSP-S3: INTAKING must be in SQL pre-filter even when result:pass tag present. "
        f"Got: {skip_arr!r}"
    )
    assert not step_calls, (
        "WSP-S3: engine.step MUST NOT fire for INTAKING rows (result:pass scenario)"
    )


# ─── WSP-S4: ANALYZING + session=failed → still escalates ────────────────────


async def test_s4_analyzing_stuck_session_failed_still_escalates(monkeypatch):
    """WSP-S4: ANALYZING past threshold with session_status=failed MUST escalate.

    ANALYZING is not in _NO_WATCHDOG_STATES, so it passes the SQL pre-filter.
    engine.step MUST be called with event=SESSION_FAILED, cur_state=ANALYZING,
    and body.event="watchdog.stuck".
    """
    from orchestrator import engine, watchdog
    from orchestrator.state import Event, ReqState
    from orchestrator.store import artifact_checks, db

    analyzing_row = _make_row(
        state="analyzing",
        req_id="REQ-s4",
        project_id="proj-s4",
        context={"intent_issue_id": "issue-s4"},
        stuck_sec=7200,
    )
    pool = _RecordingPool(fetch_return=[analyzing_row])
    step_calls: list = []

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(artifact_checks, "insert_check", AsyncMock())

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, project_id, issue_id):
            class _Issue:
                session_status = "failed"

            return _Issue()

    monkeypatch.setattr(watchdog, "BKDClient", _FakeBKD)

    async def _capture_step(*args, **kwargs):
        step_calls.append(kwargs)
        return {}

    monkeypatch.setattr(engine, "step", _capture_step)

    result = await watchdog._tick()

    assert step_calls, (
        "WSP-S4: engine.step MUST be called for ANALYZING + session=failed. "
        "ANALYZING is not in _NO_WATCHDOG_STATES and must remain watchdog-eligible."
    )
    call = step_calls[0]
    assert call.get("event") == Event.SESSION_FAILED, (
        f"WSP-S4: engine.step event MUST be SESSION_FAILED; got {call.get('event')!r}"
    )
    assert call.get("cur_state") == ReqState.ANALYZING, (
        f"WSP-S4: cur_state MUST be ANALYZING; got {call.get('cur_state')!r}"
    )
    body = call.get("body")
    assert body is not None, "WSP-S4: body must be passed to engine.step"
    assert body.event == "watchdog.stuck", (
        f"WSP-S4: body.event MUST be 'watchdog.stuck'; got {body.event!r}"
    )
    assert result["escalated"] == 1, (
        f"WSP-S4: escalated count must be 1; got {result}"
    )


# ─── WSP-S6: _SESSION_END_SIGNALS must exclude watchdog.intake_no_result_tag ─


def test_s6_session_end_signals_excludes_intake_no_result_tag():
    """WSP-S6: _SESSION_END_SIGNALS MUST NOT contain "watchdog.intake_no_result_tag".

    The watchdog no longer emits this body.event after the WSP change — keeping
    it in the signal allowlist would be dead code. The other canonical signals
    (session.failed, watchdog.stuck, archive.failed) MUST remain present so their
    respective escalate handlers continue to fire.
    """
    from orchestrator.actions.escalate import _SESSION_END_SIGNALS

    assert "watchdog.intake_no_result_tag" not in _SESSION_END_SIGNALS, (
        "_SESSION_END_SIGNALS MUST NOT list 'watchdog.intake_no_result_tag'; "
        "watchdog no longer emits this event. "
        f"Current set: {_SESSION_END_SIGNALS!r}"
    )
    for required in ("session.failed", "watchdog.stuck", "archive.failed"):
        assert required in _SESSION_END_SIGNALS, (
            f"_SESSION_END_SIGNALS MUST still contain '{required}'. "
            f"Current set: {_SESSION_END_SIGNALS!r}"
        )
