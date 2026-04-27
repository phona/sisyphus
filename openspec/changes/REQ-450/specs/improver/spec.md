## ADDED Requirements

### Requirement: improver daemon background loop

The orchestrator SHALL include a background async daemon `improver.run_loop()`
that wakes every `improver_interval_sec` seconds (default 86400), calls
`_tick()` once per cycle, and handles `asyncio.CancelledError` cleanly on
shutdown. The daemon MUST only be started when both `improver_enabled` is
`True` and `improver_interval_sec` is positive.

#### Scenario: IMPR-S1 daemon starts when enabled

- **GIVEN** `settings.improver_enabled = True` and `settings.improver_interval_sec = 3600`
- **WHEN** `startup()` is called
- **THEN** an asyncio task named `"improver"` MUST be added to `_bg_tasks`

### Requirement: latency-guard rule triggers when P95 exceeds 75% of watchdog threshold

The improver MUST evaluate stage P95 durations from `stage_runs` over the past
7 days. When the maximum P95 across any stage equals or exceeds 75% of
`watchdog_stuck_threshold_sec`, the rule MUST produce a signal proposing to
raise `watchdog_stuck_threshold_sec` by 25%, capped at 14400 seconds. The rule
MUST NOT produce a signal when no `stage_runs` data exists for the window or
when the current threshold is already at the 14400-second cap.

#### Scenario: IMPR-S2 latency-guard triggers when P95 at 80% of threshold

- **GIVEN** `watchdog_stuck_threshold_sec = 3600`
- **AND** `stage_runs` has rows where stage P95 = 2880 (80% of 3600)
- **WHEN** `_eval_latency_guard(pool)` is called
- **THEN** a `_Signal` with `rule_type = "latency-guard"` MUST be returned
- **AND** `proposed_change["new_value"]` MUST be `4500` (3600 * 1.25)

### Requirement: loop-cap rule raises or lowers fixer_round_cap based on observed rates

The improver MUST evaluate fixer cap-hit escalation rate from `req_state` over
the past 7 days. When the cap-hit rate is ≥ 30% (and total sample ≥
`improver_min_sample_count`), the rule MUST propose raising `fixer_round_cap`
by 1, with a maximum of 10. When the maximum observed fixer rounds over 14 days
is less than `fixer_round_cap - 2`, the rule MUST propose lowering
`fixer_round_cap` by 1, with a minimum of 3. The rule MUST NOT produce a
signal when sample count is below `improver_min_sample_count`.

#### Scenario: IMPR-S3 loop-cap raises when cap-hit rate exceeds 30%

- **GIVEN** `fixer_round_cap = 5` and `improver_min_sample_count = 20`
- **AND** `req_state` shows 7 cap-hit escalations out of 20 total in the last 7 days
- **WHEN** `_eval_loop_cap(pool)` is called
- **THEN** a `_Signal` with `rule_type = "loop-cap"` MUST be returned
- **AND** `proposed_change["new_value"]` MUST be `6`
- **AND** `proposed_change["direction"]` MUST be `"up"`

### Requirement: flake-tolerance rule adjusts checker_infra_flake_retry_max based on 7d/14d rates

The improver MUST evaluate `artifact_checks` infra flake rates. When the 7-day
flake rate is > 25% and `checker_infra_flake_retry_max` is below its maximum
(3), the rule MUST propose raising it by 1. When the 14-day flake rate is < 3%
and `checker_infra_flake_retry_max` is above its minimum (0), the rule MUST
propose lowering it by 1. UP direction takes priority over DOWN when both
conditions are met simultaneously.

#### Scenario: IMPR-S4 flake-tolerance raises when 7d flake rate exceeds 25%

- **GIVEN** `checker_infra_flake_retry_max = 1`
- **AND** `artifact_checks` shows 30 flakes out of 100 checks in the last 7 days
- **WHEN** `_eval_flake_tolerance(pool)` is called
- **THEN** a `_Signal` with `rule_type = "flake-tolerance"` MUST be returned
- **AND** `proposed_change["new_value"]` MUST be `2`

### Requirement: throughput rule raises inflight_req_cap when inflight-cap escalations >= 3

The improver MUST query `req_state` for rows where `escalated_reason LIKE
'inflight-cap-exceeded:%'` within the past 7 days. When this count is ≥ 3 and
`inflight_req_cap` is below its maximum (20), the rule MUST propose raising
`inflight_req_cap` by 2.

#### Scenario: IMPR-S5 throughput raises inflight cap when escalation count >= 3

- **GIVEN** `inflight_req_cap = 8`
- **AND** `req_state` has 3 rows with `escalated_reason = 'inflight-cap-exceeded:...'` in last 7 days
- **WHEN** `_eval_throughput(pool)` is called
- **THEN** a `_Signal` with `rule_type = "throughput"` MUST be returned
- **AND** `proposed_change["new_value"]` MUST be `10`

### Requirement: budget cap and cooldown prevent runaway issue creation

The improver MUST enforce a per-ISO-week budget of `improver_budget_per_window`
non-skipped runs (default 2). A run MUST be marked `skipped` with
`skip_reason = "budget-exceeded"` when the weekly non-skipped count would
exceed the budget. The improver MUST also enforce a per-rule cooldown of
`improver_cooldown_per_rule_days` days (default 7): if the same rule had a
non-skipped run within the cooldown window, the new run MUST be marked
`skipped` with `skip_reason = "cooldown"`. Every triggered signal (skipped or
submitted) MUST be recorded in `improver_runs`.

#### Scenario: IMPR-S6 run is skipped when weekly budget is exhausted

- **GIVEN** `improver_budget_per_window = 2`
- **AND** `improver_runs` already has 2 non-skipped rows in the current ISO week
- **WHEN** a signal is detected and `_check_budget_and_cooldown` is called
- **THEN** the function MUST return `"budget-exceeded"`
- **AND** the run MUST be inserted into `improver_runs` with `status = "skipped"`

#### Scenario: IMPR-S7 run is skipped when rule is in cooldown

- **GIVEN** `improver_cooldown_per_rule_days = 7`
- **AND** the same rule had a non-skipped run 3 days ago
- **WHEN** a signal is detected and `_check_budget_and_cooldown` is called
- **THEN** the function MUST return `"cooldown"`
- **AND** the run MUST be inserted into `improver_runs` with `status = "skipped"`

### Requirement: detect-only mode writes pending runs without calling BKD

When `improver_bkd_project_id` is empty, the improver MUST operate in
detect-only mode: signals that pass budget and cooldown checks MUST be written
to `improver_runs` with `status = "pending"` but MUST NOT call the BKD API or
create any issues.

#### Scenario: IMPR-S8 detect-only tick writes pending without BKD call

- **GIVEN** `improver_bkd_project_id = ""` (empty string)
- **AND** a latency-guard signal is detected that passes budget/cooldown
- **WHEN** `_tick()` runs
- **THEN** a row MUST be inserted into `improver_runs` with `status = "pending"`
- **AND** the BKD `create_issue` endpoint MUST NOT be called

### Requirement: autopilot mode submits BKD intent:analyze issue and records issue ID

When `improver_bkd_project_id` is non-empty, signals that pass budget and
cooldown checks MUST be submitted to BKD as `intent:analyze` issues tagged
with `"improver-autopilot"`. The resulting `bkd_issue_id` MUST be written back
to the `improver_runs` row and the row status MUST be updated to `"submitted"`.

#### Scenario: IMPR-S9 autopilot tick submits BKD issue and marks submitted

- **GIVEN** `improver_bkd_project_id = "proj-123"` (non-empty)
- **AND** a latency-guard signal is detected that passes budget/cooldown
- **WHEN** `_tick()` runs
- **THEN** `BKDClient.create_issue` MUST be called with `tags` containing `"intent:analyze"` and `"improver-autopilot"`
- **AND** the `improver_runs` row MUST be updated to `status = "submitted"` with the returned `bkd_issue_id`
