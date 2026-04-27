"""improver_runs table helpers (REQ-improver-autopilot)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import asyncpg


def _budget_window(ts: datetime) -> date:
    """Return the ISO-week Monday for the given timestamp."""
    return ts.date() - timedelta(days=ts.weekday())


async def insert_run(
    pool: asyncpg.Pool,
    rule_type: str,
    signal_data: dict,
    proposed_change: dict,
    *,
    status: str = "pending",
    skip_reason: str | None = None,
    bkd_issue_id: str | None = None,
    bkd_project_id: str | None = None,
    triggered_at: datetime | None = None,
) -> int:
    """Insert one improver_runs row; return the auto-increment id."""
    now = triggered_at or datetime.now(UTC)
    row = await pool.fetchrow(
        """
        INSERT INTO improver_runs
            (triggered_at, rule_type, signal_data, proposed_change,
             bkd_issue_id, bkd_project_id, status, budget_window, skip_reason)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        now,
        rule_type,
        json.dumps(signal_data),
        json.dumps(proposed_change),
        bkd_issue_id,
        bkd_project_id,
        status,
        _budget_window(now),
        skip_reason,
    )
    return int(row["id"])


async def count_in_budget_window(pool: asyncpg.Pool, window_start: date) -> int:
    """Count non-skipped runs in the given budget window."""
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM improver_runs
        WHERE budget_window = $1 AND status <> 'skipped'
        """,
        window_start,
    )
    return int(row["cnt"]) if row else 0


async def last_non_skipped_at(
    pool: asyncpg.Pool, rule_type: str
) -> datetime | None:
    """Return the triggered_at of the most recent non-skipped run for rule_type."""
    row = await pool.fetchrow(
        """
        SELECT triggered_at FROM improver_runs
        WHERE rule_type = $1 AND status <> 'skipped'
        ORDER BY triggered_at DESC
        LIMIT 1
        """,
        rule_type,
    )
    return row["triggered_at"] if row else None


async def update_status(
    pool: asyncpg.Pool,
    run_id: int,
    *,
    status: str,
    bkd_issue_id: str | None = None,
    bkd_project_id: str | None = None,
) -> None:
    """Update run status (e.g. pending → submitted)."""
    await pool.execute(
        """
        UPDATE improver_runs SET
            status         = $2,
            bkd_issue_id   = COALESCE($3, bkd_issue_id),
            bkd_project_id = COALESCE($4, bkd_project_id)
        WHERE id = $1
        """,
        run_id, status, bkd_issue_id, bkd_project_id,
    )
