"""Challenger contract tests for REQ-fix-admission-pur-cap-1777866614.

Black-box contracts derived exclusively from
  openspec/changes/REQ-fix-admission-pur-cap-1777866614/specs/orch-rate-limit/spec.md

Scenario covered:
  ORCH-RATE-S7  pending-user-review excluded from in-flight cap

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _CapturePool:
    """Minimal pool stub: capture fetchrow args, return a fixed count.

    Black-box: the spec says the SQL passed to ``pool.fetchrow`` MUST include
    ``pending-user-review`` in the excluded-states array. We do not assert on
    the SQL string itself (implementation detail) — only on the parameters.
    """

    def __init__(self, count: int) -> None:
        self._count = count
        self.last_args: tuple = ()
        self.fetchrow_calls: int = 0

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls += 1
        self.last_args = args
        return {"n": self._count}


def _install_disk_ok_controller() -> None:
    """Install a controller whose disk usage is well below any threshold,
    so the disk-pressure gate is a no-op for these tests.
    """
    from orchestrator import k8s_runner

    fake = MagicMock()
    fake.node_disk_usage_ratio = AsyncMock(return_value=0.0)
    k8s_runner.set_controller(fake)


@pytest.fixture(autouse=True)
def _reset_controller():
    from orchestrator import k8s_runner, runner_gc

    runner_gc._DISK_CHECK_DISABLED = False
    k8s_runner.set_controller(None)
    yield
    runner_gc._DISK_CHECK_DISABLED = False
    k8s_runner.set_controller(None)


# ─── ORCH-RATE-S7 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_rate_s7_pending_user_review_excluded_from_inflight_cap(
    monkeypatch,
) -> None:
    """ORCH-RATE-S7: PUR REQs MUST NOT count toward the in-flight cap.

    Spec scenario:
      GIVEN settings.inflight_req_cap = 10
      AND   req_state holds 8 REQs in pending-user-review (PUR) plus
            2 REQs in challenger-running
      WHEN  check_admission(pool, req_id="REQ-new") runs
      THEN  the SQL passed to pool.fetchrow MUST include
            'pending-user-review' in the excluded-states array
      AND   the count returned by the SQL is 2 (only the two
            challenger-running REQs), not 10
      AND   the result's admit is True (2 < 10)

    Why this matters: PUR REQs have already had their runner Pod torn down
    and are parked waiting for human follow-up — counting them against the
    cap caused incident #384, where 8 long-parked PUR rows starved every
    fresh REQ.
    """
    from orchestrator import admission

    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(
        admission.settings, "admission_disk_pressure_threshold", 0.95
    )
    _install_disk_ok_controller()

    # The contract: by the time fetchrow is invoked, the SQL has already
    # filtered out the 8 PUR rows, so the row count seen by the caller is 2.
    pool = _CapturePool(count=2)

    decision = await admission.check_admission(pool, req_id="REQ-new")

    # 1. fetchrow was invoked exactly once (admission must consult the pool).
    assert pool.fetchrow_calls == 1, (
        "ORCH-RATE-S7: check_admission MUST call pool.fetchrow exactly once "
        f"to count in-flight REQs (got {pool.fetchrow_calls} calls)"
    )

    # 2. The excluded-states array passed to the SQL MUST contain
    #    'pending-user-review'. We accept any positional arg that is a
    #    list/tuple of strings so we don't lock down the parameter order.
    state_list = _find_state_list(pool.last_args)
    assert state_list is not None, (
        "ORCH-RATE-S7: check_admission MUST pass an excluded-states list to "
        f"pool.fetchrow; saw args={pool.last_args!r}"
    )
    assert "pending-user-review" in state_list, (
        "ORCH-RATE-S7: the excluded-states array passed to pool.fetchrow MUST "
        "include 'pending-user-review' so PUR REQs do not count against the "
        f"in-flight cap. Saw excluded states: {state_list!r}"
    )

    # 3. Admission MUST succeed: count=2 < cap=10.
    assert decision.admit is True, (
        "ORCH-RATE-S7: with 2 challenger-running REQs and cap=10, admission "
        f"MUST admit a fresh REQ. Got admit={decision.admit!r} "
        f"reason={getattr(decision, 'reason', None)!r}"
    )
    assert getattr(decision, "reason", None) is None, (
        "ORCH-RATE-S7: a successful admission MUST report reason=None. "
        f"Got reason={decision.reason!r}"
    )


@pytest.mark.asyncio
async def test_orch_rate_s7_excluded_states_also_contain_legacy_terminals(
    monkeypatch,
) -> None:
    """Companion guard: the existing terminal/parked exclusions MUST stay.

    The MODIFIED requirement explicitly enumerates the excluded-states set as
    ``{init, done, escalated, gh-incident-open, pending-user-review}``. Adding
    PUR MUST NOT silently drop any of the prior four states — otherwise the
    fix would regress the cap behaviour for terminal/incident REQs.
    """
    from orchestrator import admission

    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(
        admission.settings, "admission_disk_pressure_threshold", 0.95
    )
    _install_disk_ok_controller()

    pool = _CapturePool(count=0)

    await admission.check_admission(pool, req_id="REQ-new")

    state_list = _find_state_list(pool.last_args)
    assert state_list is not None, (
        "check_admission MUST pass an excluded-states list to pool.fetchrow; "
        f"saw args={pool.last_args!r}"
    )

    required = {
        "init",
        "done",
        "escalated",
        "gh-incident-open",
        "pending-user-review",
    }
    missing = required - set(state_list)
    assert not missing, (
        "Per the MODIFIED requirement the excluded-states set MUST be the "
        f"superset of {sorted(required)!r}. Missing: {sorted(missing)!r}. "
        f"Saw: {sorted(set(state_list))!r}"
    )


# ─── helpers ─────────────────────────────────────────────────────────────────


def _find_state_list(args: tuple):
    """Locate the excluded-states sequence among ``pool.fetchrow`` positional
    args without assuming a specific parameter order.

    The spec only constrains *what* MUST be in the state list, not where it
    sits in the argument tuple — so we accept the first positional arg that
    looks like a sequence of state-name strings.
    """
    for arg in args:
        if isinstance(arg, (list, tuple)) and arg and all(
            isinstance(x, str) for x in arg
        ):
            return list(arg)
    return None
