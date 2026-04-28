"""Challenger contract tests for REQ-pg-table-ttl-cleanup-1777344801.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-pg-table-ttl-cleanup-1777344801/specs/table-ttl/spec.md
  openspec/changes/REQ-pg-table-ttl-cleanup-1777344801/specs/table-ttl/contract.spec.yaml

Scenarios covered:
  TTL-S1  event_seen: rows older than ttl_event_seen_days deleted, recent retained  [integration]
  TTL-S2  dispatch_slugs: DELETE SQL targets created_at < cutoff
  TTL-S3  verifier_decisions: DELETE SQL targets made_at < cutoff
  TTL-S4  run_ttl_cleanup returns summary dict with before/after/deleted per table
  TTL-S5  open stage_runs (ended_at IS NULL) are never deleted  [integration]
  TTL-S6  closed stage_runs: old deleted, recent retained  [integration]
  TTL-S7  stage_runs DELETE SQL includes ended_at IS NOT NULL AND ended_at < cutoff
  TTL-S8  ttl_cleanup_enabled=False causes run_loop to return immediately
  TTL-S9  startup references table_ttl.run_loop as a background task

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

_MODULE = "orchestrator.maintenance.table_ttl"
_MAIN_MODULE = "orchestrator.main"


# ─── Mock pool ────────────────────────────────────────────────────────────────


class _CapturePool:
    """Minimal asyncpg pool stand-in that records every SQL call."""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return "DELETE 0"

    async def fetchval(self, sql: str, *args: Any) -> int:
        self.fetchval_calls.append((sql, args))
        return 0

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        self.fetchrow_calls.append((sql, args))
        return {"count": 0}

    def all_sql(self) -> str:
        """Concatenate all SQL statements seen across every call type."""
        all_calls = self.execute_calls + self.fetchval_calls + self.fetchrow_calls
        return " ".join(sql for sql, _ in all_calls)


# ─── TTL-S2 ───────────────────────────────────────────────────────────────────


async def test_ttl_s2_dispatch_slugs_delete_targets_created_at() -> None:
    """TTL-S2: run_ttl_cleanup DELETE for dispatch_slugs MUST reference created_at."""
    mod = importlib.import_module(_MODULE)
    pool = _CapturePool()
    await mod.run_ttl_cleanup(pool)
    sql = pool.all_sql().lower()
    assert "dispatch_slugs" in sql, (
        "TTL-S2: run_ttl_cleanup MUST issue SQL that references the dispatch_slugs table"
    )
    # Find the section of SQL that references dispatch_slugs
    dispatch_sqls = [
        s for s, _ in (pool.execute_calls + pool.fetchval_calls + pool.fetchrow_calls)
        if "dispatch_slugs" in s.lower()
    ]
    assert dispatch_sqls, "TTL-S2: expected at least one SQL statement for dispatch_slugs"
    combined = " ".join(dispatch_sqls).lower()
    assert "created_at" in combined, (
        "TTL-S2: dispatch_slugs SQL MUST reference created_at column as the TTL cutoff"
    )


# ─── TTL-S3 ───────────────────────────────────────────────────────────────────


async def test_ttl_s3_verifier_decisions_delete_targets_made_at() -> None:
    """TTL-S3: run_ttl_cleanup DELETE for verifier_decisions MUST reference made_at."""
    mod = importlib.import_module(_MODULE)
    pool = _CapturePool()
    await mod.run_ttl_cleanup(pool)
    vd_sqls = [
        s for s, _ in (pool.execute_calls + pool.fetchval_calls + pool.fetchrow_calls)
        if "verifier_decisions" in s.lower()
    ]
    assert vd_sqls, (
        "TTL-S3: run_ttl_cleanup MUST issue SQL that references the verifier_decisions table"
    )
    combined = " ".join(vd_sqls).lower()
    assert "made_at" in combined, (
        "TTL-S3: verifier_decisions SQL MUST reference made_at column as the TTL cutoff"
    )


# ─── TTL-S4 ───────────────────────────────────────────────────────────────────


async def test_ttl_s4_returns_summary_dict_per_table() -> None:
    """TTL-S4: run_ttl_cleanup MUST return a dict with 4 table keys, each containing
    before (int), after (int), deleted (int = before - after).
    """
    mod = importlib.import_module(_MODULE)
    pool = _CapturePool()
    result = await mod.run_ttl_cleanup(pool)

    assert isinstance(result, dict), (
        f"TTL-S4: run_ttl_cleanup MUST return a dict, got {type(result).__name__}"
    )
    expected_tables = {"event_seen", "dispatch_slugs", "verifier_decisions", "stage_runs"}
    assert expected_tables == set(result.keys()), (
        f"TTL-S4: result keys MUST be exactly {sorted(expected_tables)!r}, "
        f"got {sorted(result.keys())!r}"
    )
    for tbl, summary in result.items():
        assert isinstance(summary, dict), (
            f"TTL-S4: result[{tbl!r}] MUST be a dict, got {type(summary).__name__}"
        )
        for sub in ("before", "after", "deleted"):
            assert sub in summary, (
                f"TTL-S4: result[{tbl!r}] MUST contain key {sub!r}"
            )
            assert isinstance(summary[sub], int), (
                f"TTL-S4: result[{tbl!r}][{sub!r}] MUST be int, "
                f"got {type(summary[sub]).__name__}"
            )
        assert summary["deleted"] == summary["before"] - summary["after"], (
            f"TTL-S4: result[{tbl!r}]['deleted'] MUST equal before - after "
            f"({summary['before']} - {summary['after']} ≠ {summary['deleted']})"
        )


# ─── TTL-S7 ───────────────────────────────────────────────────────────────────


async def test_ttl_s7_stage_runs_delete_has_ended_at_not_null_guard() -> None:
    """TTL-S7: the SQL for stage_runs MUST contain both 'ended_at IS NOT NULL'
    and an ended_at < cutoff condition so open runs are never deleted.
    """
    mod = importlib.import_module(_MODULE)
    pool = _CapturePool()
    await mod.run_ttl_cleanup(pool)

    sr_sqls = [
        s for s, _ in (pool.execute_calls + pool.fetchval_calls + pool.fetchrow_calls)
        if "stage_runs" in s.lower()
    ]
    assert sr_sqls, (
        "TTL-S7: run_ttl_cleanup MUST issue SQL that references the stage_runs table"
    )
    combined = " ".join(sr_sqls).lower()
    assert "ended_at is not null" in combined, (
        "TTL-S7: stage_runs SQL MUST contain 'ended_at IS NOT NULL' guard "
        "to prevent deletion of open (in-flight) rows"
    )
    assert "ended_at" in combined and ("<" in combined or "$" in combined), (
        "TTL-S7: stage_runs SQL MUST contain an ended_at < cutoff condition"
    )


# ─── TTL-S8 ───────────────────────────────────────────────────────────────────


async def test_ttl_s8_disabled_run_loop_returns_immediately(monkeypatch) -> None:
    """TTL-S8: when SISYPHUS_TTL_CLEANUP_ENABLED=false, run_loop() MUST return
    immediately without entering the while loop.
    """
    monkeypatch.setenv("SISYPHUS_TTL_CLEANUP_ENABLED", "false")
    # Reload so Settings picks up the env change
    if "orchestrator.config" in importlib.sys.modules:
        importlib.reload(importlib.sys.modules["orchestrator.config"])
    mod = importlib.import_module(_MODULE)
    importlib.reload(mod)

    try:
        await asyncio.wait_for(mod.run_loop(), timeout=2.0)
    except TimeoutError:
        pytest.fail(
            "TTL-S8: run_loop() MUST return in <2 s when ttl_cleanup_enabled=False; "
            "it appears to be blocking (did not return)"
        )


# ─── TTL-S9 ───────────────────────────────────────────────────────────────────


def test_ttl_s9_module_exposes_async_run_loop() -> None:
    """TTL-S9a: orchestrator.maintenance.table_ttl MUST expose run_loop as an
    async coroutine function — prerequisite for asyncio.create_task().
    """
    mod = importlib.import_module(_MODULE)
    assert hasattr(mod, "run_loop"), (
        "TTL-S9: orchestrator.maintenance.table_ttl MUST expose run_loop"
    )
    assert asyncio.iscoroutinefunction(mod.run_loop), (
        "TTL-S9: table_ttl.run_loop MUST be defined with 'async def' "
        "so it can be wrapped in asyncio.create_task()"
    )


def test_ttl_s9_main_startup_references_table_ttl() -> None:
    """TTL-S9b: orchestrator.main MUST reference 'table_ttl' in its startup
    logic so the background task is spawned when the orchestrator starts.
    """
    main_mod = importlib.import_module(_MAIN_MODULE)
    src = inspect.getsource(main_mod)
    assert "table_ttl" in src, (
        "TTL-S9: orchestrator.main MUST reference 'table_ttl' — it is responsible "
        "for spawning the background cleanup task during startup"
    )


# ─── Integration fixtures ─────────────────────────────────────────────────────


@pytest.fixture
async def live_pool():
    """Real asyncpg pool against SISYPHUS_PG_DSN (integration tests only)."""
    import asyncpg

    dsn = os.environ.get("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    yield pool
    await pool.close()


# ─── TTL-S1 ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_ttl_s1_event_seen_old_deleted_recent_retained(
    live_pool, monkeypatch
) -> None:
    """TTL-S1: a 40-day-old event_seen row is deleted and a 1-day-old row is retained
    when ttl_event_seen_days=30.
    """
    monkeypatch.setenv("SISYPHUS_TTL_EVENT_SEEN_DAYS", "30")
    monkeypatch.setenv("SISYPHUS_TTL_CLEANUP_ENABLED", "true")
    if "orchestrator.config" in importlib.sys.modules:
        importlib.reload(importlib.sys.modules["orchestrator.config"])
    mod = importlib.import_module(_MODULE)
    importlib.reload(mod)

    now = datetime.now(UTC)
    ts = now.timestamp()
    old_key = f"__contract_s1_old_{ts}"
    recent_key = f"__contract_s1_recent_{ts}"

    await live_pool.execute(
        "INSERT INTO event_seen (event_key, seen_at) VALUES ($1, $2) "
        "ON CONFLICT (event_key) DO NOTHING",
        old_key,
        now - timedelta(days=40),
    )
    await live_pool.execute(
        "INSERT INTO event_seen (event_key, seen_at) VALUES ($1, $2) "
        "ON CONFLICT (event_key) DO NOTHING",
        recent_key,
        now - timedelta(days=1),
    )

    await mod.run_ttl_cleanup(live_pool)

    old_row = await live_pool.fetchrow(
        "SELECT 1 FROM event_seen WHERE event_key = $1", old_key
    )
    recent_row = await live_pool.fetchrow(
        "SELECT 1 FROM event_seen WHERE event_key = $1", recent_key
    )

    # cleanup regardless of assertion outcome
    await live_pool.execute(
        "DELETE FROM event_seen WHERE event_key LIKE '__contract\\_s1\\_%' ESCAPE '\\'"
    )

    assert old_row is None, (
        "TTL-S1: event_seen row with seen_at 40 days ago MUST be deleted "
        "when ttl_event_seen_days=30"
    )
    assert recent_row is not None, (
        "TTL-S1: event_seen row with seen_at 1 day ago MUST be retained "
        "when ttl_event_seen_days=30"
    )


# ─── TTL-S5 ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_ttl_s5_open_stage_runs_never_deleted(live_pool, monkeypatch) -> None:
    """TTL-S5: a stage_runs row with started_at 180 days ago and ended_at IS NULL
    MUST NOT be deleted even when ttl_stage_runs_closed_days=1.
    """
    monkeypatch.setenv("SISYPHUS_TTL_STAGE_RUNS_CLOSED_DAYS", "1")
    monkeypatch.setenv("SISYPHUS_TTL_CLEANUP_ENABLED", "true")
    if "orchestrator.config" in importlib.sys.modules:
        importlib.reload(importlib.sys.modules["orchestrator.config"])
    mod = importlib.import_module(_MODULE)
    importlib.reload(mod)

    now = datetime.now(UTC)
    req_id = f"__contract_s5_{now.timestamp()}"
    started_at = now - timedelta(days=180)

    row_id = await live_pool.fetchval(
        "INSERT INTO stage_runs (req_id, stage, started_at, ended_at) "
        "VALUES ($1, 'dev', $2, NULL) RETURNING id",
        req_id,
        started_at,
    )

    await mod.run_ttl_cleanup(live_pool)

    still_there = await live_pool.fetchrow(
        "SELECT 1 FROM stage_runs WHERE id = $1", row_id
    )

    # cleanup
    await live_pool.execute(
        "DELETE FROM stage_runs WHERE req_id LIKE '__contract\\_s5\\_%' ESCAPE '\\'"
    )

    assert still_there is not None, (
        "TTL-S5: open stage_runs row (ended_at IS NULL, 180 days old) MUST NOT be "
        "deleted regardless of ttl_stage_runs_closed_days"
    )


# ─── TTL-S6 ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_ttl_s6_closed_stage_runs_old_deleted_recent_retained(
    live_pool, monkeypatch
) -> None:
    """TTL-S6: a closed stage_runs row with ended_at 95 days ago is deleted and
    one with ended_at 1 day ago is retained when ttl_stage_runs_closed_days=90.
    """
    monkeypatch.setenv("SISYPHUS_TTL_STAGE_RUNS_CLOSED_DAYS", "90")
    monkeypatch.setenv("SISYPHUS_TTL_CLEANUP_ENABLED", "true")
    if "orchestrator.config" in importlib.sys.modules:
        importlib.reload(importlib.sys.modules["orchestrator.config"])
    mod = importlib.import_module(_MODULE)
    importlib.reload(mod)

    now = datetime.now(UTC)
    ts = now.timestamp()
    old_req = f"__contract_s6_old_{ts}"
    recent_req = f"__contract_s6_recent_{ts}"
    old_ended = now - timedelta(days=95)
    recent_ended = now - timedelta(days=1)

    old_id = await live_pool.fetchval(
        "INSERT INTO stage_runs (req_id, stage, started_at, ended_at) "
        "VALUES ($1, 'dev', $2, $3) RETURNING id",
        old_req,
        old_ended - timedelta(hours=1),
        old_ended,
    )
    recent_id = await live_pool.fetchval(
        "INSERT INTO stage_runs (req_id, stage, started_at, ended_at) "
        "VALUES ($1, 'dev', $2, $3) RETURNING id",
        recent_req,
        recent_ended - timedelta(hours=1),
        recent_ended,
    )

    await mod.run_ttl_cleanup(live_pool)

    old_row = await live_pool.fetchrow(
        "SELECT 1 FROM stage_runs WHERE id = $1", old_id
    )
    recent_row = await live_pool.fetchrow(
        "SELECT 1 FROM stage_runs WHERE id = $1", recent_id
    )

    # cleanup
    await live_pool.execute(
        "DELETE FROM stage_runs WHERE req_id LIKE '__contract\\_s6\\_%' ESCAPE '\\'"
    )

    assert old_row is None, (
        "TTL-S6: closed stage_runs row with ended_at 95 days ago MUST be deleted "
        "when ttl_stage_runs_closed_days=90"
    )
    assert recent_row is not None, (
        "TTL-S6: closed stage_runs row with ended_at 1 day ago MUST be retained "
        "when ttl_stage_runs_closed_days=90"
    )
