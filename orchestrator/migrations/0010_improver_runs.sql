-- improver_runs: records every detection tick from the improver-autopilot daemon.
--
-- One row per triggered signal. Status lifecycle:
--   pending   -> detected a signal but no BKD project configured (detect-only mode)
--   submitted -> created a BKD intent:analyze issue for autopilot mode
--   skipped   -> signal suppressed by budget cap, cooldown, or insufficient data
--
-- budget_window is the ISO-week Monday (date_trunc('week', triggered_at)).
-- The budget cap counts non-skipped rows per window.

CREATE TABLE IF NOT EXISTS improver_runs (
    id              BIGSERIAL PRIMARY KEY,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    rule_type       TEXT NOT NULL,
      -- latency-guard   (watchdog_stuck_threshold_sec)
      -- loop-cap        (fixer_round_cap)
      -- flake-tolerance (checker_infra_flake_retry_max)
      -- throughput      (inflight_req_cap)
    signal_data     JSONB NOT NULL,       -- metric values that triggered the rule
    proposed_change JSONB NOT NULL,       -- {param, from_value, to_value}
    bkd_issue_id    TEXT,                 -- set after BKD issue created (autopilot mode)
    bkd_project_id  TEXT,                 -- BKD project the issue was created in
    status          TEXT NOT NULL DEFAULT 'pending',
    budget_window   DATE NOT NULL,        -- date_trunc('week', triggered_at)::date
    skip_reason     TEXT                  -- budget / cooldown / insufficient_data / no_bkd_project
);

CREATE INDEX IF NOT EXISTS idx_improver_runs_rule_triggered
    ON improver_runs (rule_type, triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_improver_runs_budget_window
    ON improver_runs (budget_window)
    WHERE status <> 'skipped';
