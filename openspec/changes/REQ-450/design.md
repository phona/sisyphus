# Design: REQ-450 improver-autopilot

## Architecture

`improver.py` is a background async daemon following the same `run_loop()` pattern
as `watchdog.py` and `runner_gc.py`. It runs on a configurable interval
(`improver_interval_sec`, default 86400 = 24h) and performs one `_tick()` per
cycle.

```
main.py startup
  └─ asyncio.create_task(improver.run_loop())
       └─ every improver_interval_sec:
            _tick()
              ├─ _eval_latency_guard(pool)  → _Signal | None
              ├─ _eval_loop_cap(pool)       → _Signal | None
              ├─ _eval_flake_tolerance(pool)→ _Signal | None
              └─ _eval_throughput(pool)     → _Signal | None
                  │
                  ▼ for each non-None signal:
              _check_budget_and_cooldown(pool, signal, now)
                  │  → skip_reason | None
                  ▼
              insert_run(pool, ..., status=pending|skipped)
                  │
                  ▼ if not skipped AND improver_bkd_project_id set:
              _submit_to_bkd(signal) → bkd_issue_id
              update_status(pool, run_id, status="submitted", bkd_issue_id=...)
```

## DB schema

New table `improver_runs` (migration 0010):

```sql
CREATE TABLE improver_runs (
    id              BIGSERIAL PRIMARY KEY,
    rule_type       TEXT NOT NULL,           -- latency-guard|loop-cap|flake-tolerance|throughput
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_data     JSONB NOT NULL,          -- raw metric values that triggered the rule
    proposed_change JSONB NOT NULL,          -- {param, old_value, new_value, direction}
    bkd_issue_id    TEXT,
    bkd_project_id  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|submitted|skipped
    budget_window   DATE NOT NULL,           -- ISO week Monday (for budget counting)
    skip_reason     TEXT                     -- populated when status=skipped
);
```

## Rule signal queries

### latency-guard
```sql
SELECT stage, PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95
FROM stage_runs
WHERE started_at > now() - interval '7 days'
  AND duration_sec IS NOT NULL
GROUP BY stage
```
Trigger when `max(p95) / watchdog_stuck_threshold_sec >= 0.75`.

### loop-cap
```sql
-- cap-hit rate
SELECT COUNT(*) FILTER (WHERE escalated_reason LIKE 'fixer-round-cap-exceeded:%') AS cap_hits,
       COUNT(*) AS total
FROM req_state
WHERE updated_at > now() - interval '7 days'
  AND state IN ('escalated')
```
Up if `cap_hits / total >= 0.30` (when total >= min_sample).
Down via:
```sql
SELECT MAX((context->>'fixer_rounds')::int) AS max_rounds FROM req_state
WHERE updated_at > now() - interval '14 days'
```
Down if `max_rounds < fixer_round_cap - 2`.

### flake-tolerance
```sql
SELECT COUNT(*) FILTER (WHERE flake_reason IS NOT NULL) AS flakes, COUNT(*) AS total
FROM artifact_checks
WHERE checked_at > now() - interval '7 days'   -- for UP
```
14-day window for DOWN direction.

### throughput
```sql
SELECT COUNT(*) AS inflight_escalations
FROM req_state
WHERE escalated_reason LIKE 'inflight-cap-exceeded:%'
  AND updated_at > now() - interval '7 days'
```
Trigger (UP) if `inflight_escalations >= 3`.

## Budget cap logic

```python
window = budget_window(now)  # ISO week Monday
used   = await count_in_budget_window(pool, window)
if used >= settings.improver_budget_per_window:
    return "budget-exceeded"

last   = await last_non_skipped_at(pool, signal.rule_type)
if last and (now - last).days < settings.improver_cooldown_per_rule_days:
    return "cooldown"

return None  # proceed
```

## Config additions (config.py)

```python
improver_enabled: bool = False
improver_interval_sec: int = 86400
improver_budget_per_window: int = 2
improver_cooldown_per_rule_days: int = 7
improver_min_sample_count: int = 20
improver_bkd_project_id: str = ""
```

All threshold constants (0.75, 1.25, 14400, 0.30, etc.) are module-level
constants in `improver.py`, NOT in config. They represent domain invariants that
should only change with intentional code review, not env vars.

## BKD issue creation

When `improver_bkd_project_id` is set, `_submit_to_bkd(signal)` calls
`BKDClient.create_issue()` with:
- `title`: `[improver-autopilot] <rule_type>: <description>`
- `tags`: `["intent:analyze", "improver-autopilot"]`
- `project_id`: `settings.improver_bkd_project_id`
- `body`: dedented multi-line explanation with signal_data + proposed_change

The analyze-agent receiving this issue is responsible for actually modifying
the config value in deployment (e.g., helm values update).
