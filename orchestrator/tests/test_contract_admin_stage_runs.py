"""Contract tests for REQ-admin-esc-close-stagerun-1777338501:
fix(admin): force_escalate closes in-flight stage_run to prevent long-tail metrics pollution

Black-box behavioral contracts derived from:
  openspec/changes/REQ-admin-esc-close-stagerun-1777338501/specs/admin-stage-runs/spec.md
  openspec/changes/REQ-admin-esc-close-stagerun-1777338501/specs/admin-stage-runs/contract.spec.yaml

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  FESC-S1  ANALYZING state closes analyze stage_run before escalate
  FESC-S2  INIT state skips stage_run close
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class _FakeRow:
    req_id: str
    project_id: str
    state: object
    context: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _OrderTrackingPool:
    """Fake asyncpg pool that records execute call order tokens."""

    def __init__(self):
        self.call_log: list[str] = []

    async def execute(self, sql: str, *args):
        self.call_log.append("sql_update")
        return "UPDATE 1"

    async def fetchrow(self, sql: str, *args):
        return None

    async def fetch(self, sql: str, *args):
        return []

    async def fetchval(self, sql: str, *args):
        return None


# ─── FESC-S1: ANALYZING state closes analyze stage_run BEFORE raw SQL UPDATE ──


async def test_fesc_s1_analyzing_closes_analyze_stage_run_before_sql_update(monkeypatch):
    """
    FESC-S1: When POST /admin/req/{req_id}/escalate is called on a REQ in ANALYZING state,
    the implementation MUST:
    1. call close_latest_stage_run(req_id, "analyze", outcome="escalated",
       fail_reason="admin-force-escalate")
    2. call it BEFORE the raw SQL UPDATE that sets state='escalated' in req_state

    Contract: orphaned stage_run rows (ended_at IS NULL) MUST be closed before state change
    to prevent NULL duration_sec / NULL outcome rows from accumulating in stage_stats.
    """
    from orchestrator import admin as admin_mod
    from orchestrator.state import ReqState

    monkeypatch.setattr(admin_mod, "_verify_token", lambda _: None)

    row = _FakeRow(req_id="REQ-fesc-s1-test", project_id="proj-test", state=ReqState.ANALYZING)

    async def _fake_get(pool, req_id):
        return row

    monkeypatch.setattr("orchestrator.admin.req_state.get", _fake_get)

    call_order: list[str] = []
    close_calls: list[dict] = []
    pool = _OrderTrackingPool()

    original_execute = pool.execute

    async def _tracked_execute(sql: str, *args):
        call_order.append("sql_update")
        return await original_execute(sql, *args)

    pool.execute = _tracked_execute
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    async def _fake_close(p, req_id, stage, *, outcome, fail_reason=None):
        call_order.append("stage_run_close")
        close_calls.append(
            {"req_id": req_id, "stage": stage, "outcome": outcome, "fail_reason": fail_reason}
        )

    monkeypatch.setattr(
        "orchestrator.admin.stage_runs.close_latest_stage_run", _fake_close
    )

    async def _noop_cleanup(*_args, **_kw):
        pass

    monkeypatch.setattr("orchestrator.engine._cleanup_runner_on_terminal", _noop_cleanup)

    result = await admin_mod.force_escalate(
        "REQ-fesc-s1-test", authorization="Bearer test-token"
    )

    await asyncio.sleep(0)  # drain any fire-and-forget tasks

    # Contract 1: escalate MUST succeed for ANALYZING state
    assert result.get("action") == "force_escalated", (
        f"FESC-S1: force_escalate on ANALYZING state MUST return action='force_escalated'; "
        f"got {result!r}"
    )

    # Contract 2: close_latest_stage_run MUST be called with stage='analyze'
    analyze_closes = [c for c in close_calls if c["stage"] == "analyze"]
    assert len(analyze_closes) >= 1, (
        f"FESC-S1: close_latest_stage_run MUST be called with stage='analyze' for ANALYZING state; "
        f"all close_calls: {close_calls}"
    )

    # Contract 3: req_id MUST be forwarded correctly
    assert analyze_closes[0]["req_id"] == "REQ-fesc-s1-test", (
        f"FESC-S1: req_id MUST be forwarded to close_latest_stage_run; "
        f"got {analyze_closes[0]['req_id']!r}"
    )

    # Contract 4: outcome MUST be 'escalated'
    assert analyze_closes[0]["outcome"] == "escalated", (
        f"FESC-S1: outcome MUST be 'escalated'; got {analyze_closes[0]['outcome']!r}. "
        f"Contract: close_latest_stage_run(pool, req_id, 'analyze', outcome='escalated', ...)"
    )

    # Contract 5: fail_reason MUST be 'admin-force-escalate'
    assert analyze_closes[0]["fail_reason"] == "admin-force-escalate", (
        f"FESC-S1: fail_reason MUST be 'admin-force-escalate'; "
        f"got {analyze_closes[0]['fail_reason']!r}"
    )

    # Contract 6: close MUST happen BEFORE the raw SQL UPDATE (ordering invariant)
    assert "stage_run_close" in call_order, (
        f"FESC-S1: stage_run_close MUST appear in call_order; got {call_order}"
    )
    assert "sql_update" in call_order, (
        f"FESC-S1: sql_update MUST appear in call_order; got {call_order}"
    )
    close_idx = call_order.index("stage_run_close")
    update_idx = call_order.index("sql_update")
    assert close_idx < update_idx, (
        f"FESC-S1: close_latest_stage_run MUST be called BEFORE the raw SQL UPDATE; "
        f"call_order={call_order}. Orphaned stage_run must be closed before state transition "
        f"so duration_sec is computed correctly."
    )


# ─── FESC-S2: INIT state skips stage_run close ────────────────────────────────


async def test_fesc_s2_init_state_skips_stage_run_close(monkeypatch):
    """
    FESC-S2: When POST /admin/req/{req_id}/escalate is called on a REQ in INIT state
    (no corresponding entry in STATE_TO_STAGE), close_latest_stage_run MUST NOT be
    called. The REQ state MUST still be set to escalated successfully.

    Contract: INIT has no running stage_run to close — calling close for non-running
    states is incorrect and must be avoided.
    """
    from orchestrator import admin as admin_mod
    from orchestrator.state import ReqState

    monkeypatch.setattr(admin_mod, "_verify_token", lambda _: None)

    row = _FakeRow(req_id="REQ-fesc-s2-test", project_id="proj-test", state=ReqState.INIT)

    async def _fake_get(pool, req_id):
        return row

    monkeypatch.setattr("orchestrator.admin.req_state.get", _fake_get)

    close_calls: list[dict] = []

    async def _fake_close(p, req_id, stage, *, outcome, fail_reason=None):
        close_calls.append({"req_id": req_id, "stage": stage, "outcome": outcome})

    monkeypatch.setattr(
        "orchestrator.admin.stage_runs.close_latest_stage_run", _fake_close
    )

    pool = _OrderTrackingPool()
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    async def _noop_cleanup(*_args, **_kw):
        pass

    monkeypatch.setattr("orchestrator.engine._cleanup_runner_on_terminal", _noop_cleanup)

    result = await admin_mod.force_escalate(
        "REQ-fesc-s2-test", authorization="Bearer test-token"
    )

    await asyncio.sleep(0)  # drain any fire-and-forget tasks

    # Contract 1: escalate MUST succeed (INIT is a valid state to escalate from)
    assert result.get("action") == "force_escalated", (
        f"FESC-S2: force_escalate on INIT state MUST return action='force_escalated'; "
        f"got {result!r}"
    )

    # Contract 2: close_latest_stage_run MUST NOT be called for INIT state
    assert close_calls == [], (
        f"FESC-S2: close_latest_stage_run MUST NOT be called when state is INIT "
        f"(no corresponding stage in STATE_TO_STAGE — no running stage_run to close); "
        f"got {len(close_calls)} unexpected call(s): {close_calls}"
    )

    # Contract 3: the SQL UPDATE to escalated MUST still happen
    sql_updates = [t for t in pool.call_log if t == "sql_update"]
    assert len(sql_updates) >= 1, (
        f"FESC-S2: raw SQL UPDATE MUST be called to set state='escalated'; "
        f"call_log={pool.call_log}. Skipping close MUST NOT prevent the state change."
    )
