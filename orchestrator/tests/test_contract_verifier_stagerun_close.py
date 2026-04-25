"""
Contract tests for REQ-verifier-stagerun-close-1777105576:
fix(verifier): close orphan verifier stage_run on VERIFY_PASS path

Black-box behavioral contracts derived from:
  openspec/changes/REQ-verifier-stagerun-close-1777105576/specs/verifier-stagerun-close/spec.md
  openspec/changes/REQ-verifier-stagerun-close-1777105576/specs/verifier-stagerun-close/contract.spec.yaml

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  VSC-S1  VERIFY_PASS self-loop closes verifier stage_run with outcome=pass
  VSC-S2  close 是 best-effort，失败只 log 不抛
  VSC-S3  VERIFY_FIX_NEEDED 仍走原 close+open 路径
  VSC-S4  REVIEW_RUNNING + SESSION_FAILED self-loop 不触发 verifier close

Module under test:
  orchestrator.engine._record_stage_transitions(pool, req_id, cur_state, next_state, event)
Store contract (orchestrator.store.stage_runs):
  close_latest_stage_run(pool, req_id, stage, outcome) -> id | None
  open_stage_run(pool, req_id, stage, ...) -> id | None
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import structlog.testing


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _FakePool:
    """Minimal asyncpg pool stub: accepts calls, captures execute for inspection."""

    def __init__(self):
        self.execute_calls: list = []

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))

    async def fetch(self, sql: str, *args):
        return []

    async def fetchrow(self, sql: str, *args):
        return None

    async def fetchval(self, sql: str, *args):
        return None


class _FakeStageRuns:
    """
    Fake orchestrator.store.stage_runs module stub.

    Captures calls to close_latest_stage_run and open_stage_run.
    All other attributes return an AsyncMock so any awaited call succeeds without
    manually listing every function in the real module.
    """

    def __init__(self, close_side_effect=None):
        self._close_side_effect = close_side_effect
        self._close_calls: list = []
        self._open_calls: list = []

    async def close_latest_stage_run(self, pool, req_id, stage, outcome=None, fail_reason=None, **kw):
        self._close_calls.append({"req_id": req_id, "stage": stage, "outcome": outcome})
        if self._close_side_effect is not None:
            raise self._close_side_effect
        return "fake-run-id-closed"

    async def insert_stage_run(self, pool, req_id, stage, **kw):
        self._open_calls.append({"req_id": req_id, "stage": stage})
        return 42  # fake run_id int

    def __getattr__(self, name: str):
        """Return a fresh AsyncMock for any other stage_runs function the engine calls."""
        return AsyncMock(return_value=None)


def _make_fake_stage_runs(close_side_effect=None) -> _FakeStageRuns:
    return _FakeStageRuns(close_side_effect=close_side_effect)


# ─── VSC-S1: VERIFY_PASS self-loop MUST close verifier stage_run ─────────────


async def test_vsc_s1_verify_pass_closes_verifier_stage_run(monkeypatch):
    """
    VSC-S1: _record_stage_transitions MUST call close_latest_stage_run(pool, req_id,
    "verifier", outcome="pass") exactly once when cur_state=REVIEW_RUNNING and
    event=VERIFY_PASS, even though next_state == cur_state (self-loop in transition table).

    Invariant: close MUST happen regardless of cur_state == next_state equality.
    """
    import orchestrator.engine as engine_mod
    from orchestrator.state import Event, ReqState

    fake_sr = _make_fake_stage_runs()
    monkeypatch.setattr(engine_mod, "stage_runs", fake_sr)

    pool = _FakePool()
    req_id = "REQ-vsc-test-001"

    await engine_mod._record_stage_transitions(
        pool,
        req_id=req_id,
        cur_state=ReqState.REVIEW_RUNNING,
        next_state=ReqState.REVIEW_RUNNING,  # self-loop
        event=Event.VERIFY_PASS,
    )

    verifier_closes = [c for c in fake_sr._close_calls if c["stage"] == "verifier"]

    assert len(verifier_closes) == 1, (
        f"VSC-S1: close_latest_stage_run MUST be called exactly once for stage='verifier' "
        f"on (REVIEW_RUNNING, VERIFY_PASS) self-loop; "
        f"got {len(verifier_closes)} call(s): {verifier_closes}"
    )
    assert verifier_closes[0]["outcome"] == "pass", (
        f"VSC-S1: outcome MUST be 'pass'; got {verifier_closes[0]['outcome']!r}. "
        f"Contract: close_latest_stage_run(pool, req_id, 'verifier', outcome='pass')"
    )
    assert verifier_closes[0]["req_id"] == req_id, (
        f"VSC-S1: req_id '{req_id}' MUST be forwarded to close_latest_stage_run; "
        f"got {verifier_closes[0]['req_id']!r}"
    )

    # Invariant: self-loop means no new stage_run should be inserted
    assert fake_sr._open_calls == [], (
        f"VSC-S1: on (REVIEW_RUNNING→REVIEW_RUNNING) self-loop, insert_stage_run MUST NOT "
        f"be called (no new stage is entered); got: {fake_sr._open_calls}"
    )


# ─── VSC-S2: close failure MUST be logged and MUST NOT propagate ─────────────


async def test_vsc_s2_close_error_logged_not_raised(monkeypatch):
    """
    VSC-S2: When close_latest_stage_run raises an exception, _record_stage_transitions
    MUST catch it and log a warning/error with event 'engine.stage_runs.write_failed'.
    The exception MUST NOT propagate to the caller (best-effort semantic).
    """
    import orchestrator.engine as engine_mod
    from orchestrator.state import Event, ReqState

    db_error = RuntimeError("simulated DB transient failure")
    fake_sr = _make_fake_stage_runs(close_side_effect=db_error)
    monkeypatch.setattr(engine_mod, "stage_runs", fake_sr)

    pool = _FakePool()

    with structlog.testing.capture_logs() as log_records:
        try:
            await engine_mod._record_stage_transitions(
                pool,
                req_id="REQ-vsc-test-002",
                cur_state=ReqState.REVIEW_RUNNING,
                next_state=ReqState.REVIEW_RUNNING,
                event=Event.VERIFY_PASS,
            )
        except Exception as exc:
            pytest.fail(
                f"VSC-S2: _record_stage_transitions MUST NOT raise when "
                f"close_latest_stage_run fails (best-effort contract); "
                f"raised {type(exc).__name__}: {exc}"
            )

    logged_levels = [(r.get("event", ""), r.get("log_level", "")) for r in log_records]

    warning_or_error_events = [
        r["event"]
        for r in log_records
        if r.get("log_level") in ("warning", "error", "warn")
    ]
    assert any("engine.stage_runs.write_failed" in e for e in warning_or_error_events), (
        f"VSC-S2: MUST emit a warning/error log with event 'engine.stage_runs.write_failed' "
        f"when close_latest_stage_run raises; "
        f"actual log events+levels: {logged_levels!r}"
    )


# ─── VSC-S3: VERIFY_FIX_NEEDED uses existing generic close+open path ──────────


async def test_vsc_s3_verify_fix_needed_normal_close_open(monkeypatch):
    """
    VSC-S3: (REVIEW_RUNNING, VERIFY_FIX_NEEDED) → next_state=FIXER_RUNNING (state change).
    _record_stage_transitions MUST:
    - close the verifier stage_run with outcome='fix' via the generic close-on-leave path
    - open a fixer stage_run via the generic open-on-enter path

    This verifies the existing behavior is preserved and not accidentally broken by the
    VERIFY_PASS fix — the explicit close branch MUST only trigger on VERIFY_PASS.
    """
    import orchestrator.engine as engine_mod
    from orchestrator.state import Event, ReqState

    fake_sr = _make_fake_stage_runs()
    monkeypatch.setattr(engine_mod, "stage_runs", fake_sr)

    pool = _FakePool()

    await engine_mod._record_stage_transitions(
        pool,
        req_id="REQ-vsc-test-003",
        cur_state=ReqState.REVIEW_RUNNING,
        next_state=ReqState.FIXER_RUNNING,
        event=Event.VERIFY_FIX_NEEDED,
    )

    verifier_closes = [c for c in fake_sr._close_calls if c["stage"] == "verifier"]
    assert len(verifier_closes) >= 1, (
        f"VSC-S3: verifier stage_run MUST be closed on VERIFY_FIX_NEEDED (leave REVIEW_RUNNING); "
        f"close_calls: {fake_sr._close_calls}"
    )
    assert verifier_closes[0]["outcome"] == "fix", (
        f"VSC-S3: verifier close outcome MUST be 'fix' for VERIFY_FIX_NEEDED; "
        f"got {verifier_closes[0]['outcome']!r}. "
        f"Contract: _EVENT_TO_OUTCOME[VERIFY_FIX_NEEDED] == 'fix'"
    )

    fixer_opens = [o for o in fake_sr._open_calls if o["stage"] == "fixer"]
    assert len(fixer_opens) >= 1, (
        f"VSC-S3: fixer stage_run MUST be opened on enter FIXER_RUNNING; "
        f"open_calls: {fake_sr._open_calls}"
    )


# ─── VSC-S4: SESSION_FAILED self-loop MUST NOT close verifier stage_run ───────


async def test_vsc_s4_session_failed_self_loop_no_verifier_close(monkeypatch):
    """
    VSC-S4: (REVIEW_RUNNING, SESSION_FAILED) is a self-loop (next_state == cur_state).
    _record_stage_transitions MUST NOT call close_latest_stage_run for stage='verifier'.
    Only event == VERIFY_PASS triggers the explicit verifier close; other self-loop
    events (e.g. SESSION_FAILED dispatched by the watchdog) must not.
    """
    import orchestrator.engine as engine_mod
    from orchestrator.state import Event, ReqState

    fake_sr = _make_fake_stage_runs()
    monkeypatch.setattr(engine_mod, "stage_runs", fake_sr)

    pool = _FakePool()

    await engine_mod._record_stage_transitions(
        pool,
        req_id="REQ-vsc-test-004",
        cur_state=ReqState.REVIEW_RUNNING,
        next_state=ReqState.REVIEW_RUNNING,  # self-loop
        event=Event.SESSION_FAILED,
    )

    verifier_closes = [c for c in fake_sr._close_calls if c["stage"] == "verifier"]
    assert verifier_closes == [], (
        f"VSC-S4: close_latest_stage_run MUST NOT be called for stage='verifier' "
        f"on (REVIEW_RUNNING, SESSION_FAILED) self-loop — only VERIFY_PASS triggers "
        f"the explicit verifier close; got {len(verifier_closes)} call(s): {verifier_closes}"
    )
