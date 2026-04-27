"""Unit tests for improver-autopilot daemon (REQ-improver-autopilot).

Tests mock the DB pool and BKD client; no real Postgres or HTTP needed.
Covers: rule evaluation signal detection, budget cap, cooldown, detect-only vs
autopilot mode, _tick result aggregation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orchestrator import improver
from orchestrator.store import improver_runs as ir_store

# ─── Fake pool ────────────────────────────────────────────────────────────────

class FakePool:
    """Minimal asyncpg pool mock: fetch / fetchrow / execute."""

    def __init__(self, rows_by_query: list | None = None):
        # Each call to fetch/fetchrow pops from this queue.
        self._queue: list = list(rows_by_query or [])
        self.executed: list = []

    def _pop(self):
        if not self._queue:
            return None
        return self._queue.pop(0)

    async def fetch(self, sql, *args):
        r = self._pop()
        return r if isinstance(r, list) else ([] if r is None else [r])

    async def fetchrow(self, sql, *args):
        r = self._pop()
        if isinstance(r, list):
            return r[0] if r else None
        return r

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _row(**kw):
    """Return a dict that behaves like an asyncpg Record for key access."""
    return kw


# ─── store/improver_runs helpers ─────────────────────────────────────────────

def test_budget_window_is_monday():
    # 2026-04-27 is a Monday
    monday = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    assert ir_store._budget_window(monday) == monday.date()

    # 2026-04-29 is a Wednesday → window should be Monday 2026-04-27
    wednesday = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
    assert ir_store._budget_window(wednesday).weekday() == 0  # Monday


@pytest.mark.asyncio
async def test_insert_run_returns_id(monkeypatch):
    pool = FakePool([_row(id=42)])
    run_id = await ir_store.insert_run(
        pool,
        "latency-guard",
        {"metric": "p95", "value": 3000},
        {"param": "watchdog_stuck_threshold_sec", "from_value": 3600, "to_value": 4500},
        status="pending",
    )
    assert run_id == 42


@pytest.mark.asyncio
async def test_count_in_budget_window(monkeypatch):
    pool = FakePool([_row(cnt=2)])
    from datetime import date
    count = await ir_store.count_in_budget_window(pool, date(2026, 4, 27))
    assert count == 2


@pytest.mark.asyncio
async def test_last_non_skipped_at_none(monkeypatch):
    pool = FakePool([None])
    result = await ir_store.last_non_skipped_at(pool, "latency-guard")
    assert result is None


# ─── Rule evaluation: latency-guard ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_latency_guard_triggers_when_p95_high(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.watchdog_stuck_threshold_sec", 3600)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # P95 = 2900s → ratio = 2900/3600 ≈ 0.806 > 0.75 → should trigger
    pool = FakePool([
        [_row(stage="ANALYZING", n=20, p95=2900.0)],
    ])
    signal = await improver._eval_latency_guard(pool)
    assert signal is not None
    assert signal.rule_type == "latency-guard"
    assert signal.proposed_change["param"] == "watchdog_stuck_threshold_sec"
    assert signal.proposed_change["from_value"] == 3600
    assert signal.proposed_change["to_value"] == 4500  # 3600 * 1.25


@pytest.mark.asyncio
async def test_latency_guard_no_trigger_when_p95_low(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.watchdog_stuck_threshold_sec", 3600)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # P95 = 1000s → ratio = 0.278 < 0.75 → no trigger
    pool = FakePool([
        [_row(stage="ANALYZING", n=20, p95=1000.0)],
    ])
    signal = await improver._eval_latency_guard(pool)
    assert signal is None


@pytest.mark.asyncio
async def test_latency_guard_no_trigger_no_rows(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    pool = FakePool([[]])  # empty list from fetch
    signal = await improver._eval_latency_guard(pool)
    assert signal is None


@pytest.mark.asyncio
async def test_latency_guard_capped_at_max(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.watchdog_stuck_threshold_sec", 13000)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    pool = FakePool([
        [_row(stage="ANALYZING", n=20, p95=12000.0)],
    ])
    signal = await improver._eval_latency_guard(pool)
    assert signal is not None
    # 13000 * 1.25 = 16250, but cap is 14400
    assert signal.proposed_change["to_value"] == 14400


# ─── Rule evaluation: loop-cap (raise) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_loop_cap_raises_when_hit_rate_high(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.fixer_round_cap", 5)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # 4 cap-hit escalations out of 10 fixable REQs → 40% > 30%
    pool = FakePool([
        _row(cnt=4),   # cap_hit_count
        _row(cnt=10),  # fixable_count
    ])
    signal = await improver._eval_loop_cap(pool)
    assert signal is not None
    assert signal.proposed_change["to_value"] == 6  # 5 + 1


@pytest.mark.asyncio
async def test_loop_cap_lowers_when_rounds_low(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.fixer_round_cap", 8)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # 0 cap-hits out of 10 fixable → rate=0 → no UP trigger
    # max_rounds = 4 → cap - max_rounds = 4 → 4 > _LOOP_CAP_MAX_ROUNDS_DOWN(2) → DOWN trigger
    pool = FakePool([
        _row(cnt=0),    # cap_hit_count
        _row(cnt=10),   # fixable_count
        _row(max_rounds=4),  # max_fixer_rounds
    ])
    signal = await improver._eval_loop_cap(pool)
    assert signal is not None
    assert signal.proposed_change["to_value"] == 7  # 8 - 1


@pytest.mark.asyncio
async def test_loop_cap_no_trigger_insufficient_data(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 20)
    pool = FakePool([
        _row(cnt=5),   # cap_hit_count
        _row(cnt=15),  # fixable_count (< min_sample_count=20)
    ])
    signal = await improver._eval_loop_cap(pool)
    assert signal is None


# ─── Rule evaluation: flake-tolerance ────────────────────────────────────────

@pytest.mark.asyncio
async def test_flake_tolerance_raises_when_rate_high(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.checker_infra_flake_retry_max", 1)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # 30% flake rate (30/100) > 25% threshold
    pool = FakePool([
        _row(flake_count=30, total_count=100),
    ])
    signal = await improver._eval_flake_tolerance(pool)
    assert signal is not None
    assert signal.proposed_change["to_value"] == 2  # 1 + 1


@pytest.mark.asyncio
async def test_flake_tolerance_lowers_when_rate_low(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.checker_infra_flake_retry_max", 2)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # 7d rate OK (not high), 14d rate = 1% < 3% → lower
    pool = FakePool([
        _row(flake_count=5, total_count=100),   # 7d: 5% — below UP threshold
        _row(flake_count=5, total_count=500),   # 14d: 1% < 3% DOWN threshold
    ])
    signal = await improver._eval_flake_tolerance(pool)
    assert signal is not None
    assert signal.proposed_change["to_value"] == 1  # 2 - 1


@pytest.mark.asyncio
async def test_flake_tolerance_no_trigger_middle_range(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.checker_infra_flake_retry_max", 1)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    # 7d: 10% (between 3% and 25%) → no UP; 14d: 5% (above 3%) → no DOWN
    pool = FakePool([
        _row(flake_count=10, total_count=100),
        _row(flake_count=25, total_count=500),
    ])
    signal = await improver._eval_flake_tolerance(pool)
    assert signal is None


# ─── Rule evaluation: throughput ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_throughput_raises_cap_when_escalations_high(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.inflight_req_cap", 10)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    pool = FakePool([
        _row(cnt=4),    # cap_escalation_count_7d (>= 3 threshold)
        _row(cnt=50),   # total_reqs_7d (>= min_sample_count)
    ])
    signal = await improver._eval_throughput(pool)
    assert signal is not None
    assert signal.proposed_change["to_value"] == 12  # 10 + 2


@pytest.mark.asyncio
async def test_throughput_no_trigger_low_escalations(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.inflight_req_cap", 10)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    pool = FakePool([
        _row(cnt=1),    # only 1 escalation < 3 threshold
        _row(cnt=50),
    ])
    signal = await improver._eval_throughput(pool)
    assert signal is None


# ─── Budget & cooldown ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_budget_returns_budget_when_full(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_budget_per_window", 2)
    monkeypatch.setattr("orchestrator.improver.settings.improver_cooldown_per_rule_days", 7)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    signal = improver._Signal(
        rule_type="latency-guard",
        signal_data={},
        proposed_change={"param": "x", "from_value": 1, "to_value": 2},
        bkd_description="",
    )
    pool = FakePool([
        _row(cnt=2),   # budget window already has 2 non-skipped runs
    ])
    reason = await improver._check_budget_and_cooldown(pool, signal, now)
    assert reason == "budget"


@pytest.mark.asyncio
async def test_check_budget_returns_cooldown(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_budget_per_window", 5)
    monkeypatch.setattr("orchestrator.improver.settings.improver_cooldown_per_rule_days", 7)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    recent = now - timedelta(days=3)  # 3 days ago < 7-day cooldown
    signal = improver._Signal(
        rule_type="latency-guard",
        signal_data={},
        proposed_change={"param": "x", "from_value": 1, "to_value": 2},
        bkd_description="",
    )
    pool = FakePool([
        _row(cnt=0),         # budget OK
        _row(triggered_at=recent),  # recent cooldown hit
    ])
    reason = await improver._check_budget_and_cooldown(pool, signal, now)
    assert reason == "cooldown"


@pytest.mark.asyncio
async def test_check_budget_returns_none_when_ok(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_budget_per_window", 5)
    monkeypatch.setattr("orchestrator.improver.settings.improver_cooldown_per_rule_days", 7)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    signal = improver._Signal(
        rule_type="latency-guard",
        signal_data={},
        proposed_change={"param": "x", "from_value": 1, "to_value": 2},
        bkd_description="",
    )
    pool = FakePool([
        _row(cnt=0),   # budget OK
        None,          # no previous run for this rule_type
    ])
    reason = await improver._check_budget_and_cooldown(pool, signal, now)
    assert reason is None


# ─── _tick integration: detect-only mode ─────────────────────────────────────

@pytest.mark.asyncio
async def test_tick_detect_only_writes_pending(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_enabled", True)
    monkeypatch.setattr("orchestrator.improver.settings.improver_bkd_project_id", "")
    monkeypatch.setattr("orchestrator.improver.settings.improver_budget_per_window", 5)
    monkeypatch.setattr("orchestrator.improver.settings.improver_cooldown_per_rule_days", 7)
    monkeypatch.setattr("orchestrator.improver.settings.improver_min_sample_count", 5)
    monkeypatch.setattr("orchestrator.improver.settings.watchdog_stuck_threshold_sec", 3600)
    monkeypatch.setattr("orchestrator.improver.settings.fixer_round_cap", 5)
    monkeypatch.setattr("orchestrator.improver.settings.checker_infra_flake_retry_max", 1)
    monkeypatch.setattr("orchestrator.improver.settings.inflight_req_cap", 10)

    inserted_runs: list[dict] = []

    async def fake_insert_run(pool, rule_type, signal_data, proposed_change, **kw):
        inserted_runs.append({"rule_type": rule_type, "status": kw.get("status", "pending")})
        return len(inserted_runs)

    async def fake_count(pool, window):
        return 0

    async def fake_last(pool, rule_type):
        return None

    monkeypatch.setattr("orchestrator.improver.improver_runs.insert_run", fake_insert_run)
    monkeypatch.setattr("orchestrator.improver.improver_runs.count_in_budget_window", fake_count)
    monkeypatch.setattr("orchestrator.improver.improver_runs.last_non_skipped_at", fake_last)

    # Make latency-guard trigger, all others no-signal
    async def fake_latency(*a):
        return improver._Signal(
            rule_type="latency-guard",
            signal_data={"p95_sec": 3000.0},
            proposed_change={"param": "watchdog_stuck_threshold_sec", "from_value": 3600, "to_value": 4500},
            bkd_description="test",
        )

    async def no_signal(*a):
        return None

    monkeypatch.setattr("orchestrator.improver.db.get_pool", lambda: object())
    monkeypatch.setattr("orchestrator.improver._RULES", [
        fake_latency, no_signal, no_signal, no_signal
    ])

    result = await improver._tick()
    assert result["pending"] == 1
    assert result["submitted"] == 0
    assert result["skipped"] == 0
    assert inserted_runs[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_tick_skips_when_budget_exceeded(monkeypatch):
    monkeypatch.setattr("orchestrator.improver.settings.improver_budget_per_window", 2)
    monkeypatch.setattr("orchestrator.improver.settings.improver_cooldown_per_rule_days", 7)

    inserted_runs: list[dict] = []

    async def fake_insert_run(pool, rule_type, signal_data, proposed_change, **kw):
        inserted_runs.append({"rule_type": rule_type, "status": kw.get("status", "pending")})
        return len(inserted_runs)

    async def fake_count(pool, window):
        return 2  # already at budget limit

    async def fake_last(pool, rule_type):
        return None

    monkeypatch.setattr("orchestrator.improver.improver_runs.insert_run", fake_insert_run)
    monkeypatch.setattr("orchestrator.improver.improver_runs.count_in_budget_window", fake_count)
    monkeypatch.setattr("orchestrator.improver.improver_runs.last_non_skipped_at", fake_last)

    async def fake_signal(*a):
        return improver._Signal(
            rule_type="latency-guard",
            signal_data={},
            proposed_change={"param": "x", "from_value": 1, "to_value": 2},
            bkd_description="",
        )

    monkeypatch.setattr("orchestrator.improver.db.get_pool", lambda: object())
    monkeypatch.setattr("orchestrator.improver._RULES", [fake_signal])

    result = await improver._tick()
    assert result["skipped"] == 1
    assert result["pending"] == 0
    assert inserted_runs[0]["status"] == "skipped"
