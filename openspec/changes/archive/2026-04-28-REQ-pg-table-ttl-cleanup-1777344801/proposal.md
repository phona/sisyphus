# REQ-pg-table-ttl-cleanup-1777344801: Periodic TTL Cleanup for Append-Only Tables

## Problem

Four tables in the sisyphus Postgres instance grow without bound and are never pruned:

| Table | Purpose | Growth rate |
|---|---|---|
| `event_seen` | Webhook dedup | ~4000 rows/week |
| `dispatch_slugs` | BKD issue creation idempotency | ~50 rows/week |
| `verifier_decisions` | Verifier audit trail | ~50 rows/week |
| `stage_runs` | Stage agent timing & token metrics | ~100 rows/week |

Over months, unbounded growth leads to slow sequential scans on dedup lookups,
inflated backup sizes, and degraded dashboard query performance.

## Approach

Add a background asyncio task (`table_ttl.run_loop`) to the orchestrator startup
sequence. The task runs `run_ttl_cleanup(pool)` every `ttl_cleanup_interval_sec`
seconds (default 24 h) and deletes rows older than per-table retention thresholds.

**Key safety constraint**: `stage_runs` rows where `ended_at IS NULL` are _never_
deleted — they represent still-open or abnormally-terminated runs that may be
needed for debugging.

All thresholds are configurable via environment variables (12-factor) with
conservative defaults: 30 days for webhook dedup, 90 days for everything else.

## Non-scope

- `req_state` — permanent business record; never pruned
- `bkd_snapshot` / obs views — managed by separate snapshot loop
- Manual admin endpoint — 24 h periodic is sufficient; no on-demand trigger needed
- Bulk historical backfill of already-accumulated rows — first automatic run handles it
