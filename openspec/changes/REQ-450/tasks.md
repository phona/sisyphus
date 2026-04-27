# Tasks: REQ-450

## Stage: contract / spec

- [x] `openspec/changes/REQ-450/proposal.md`
- [x] `openspec/changes/REQ-450/design.md`
- [x] `openspec/changes/REQ-450/specs/improver/spec.md`
- [x] `openspec/changes/REQ-450/specs/improver/contract.spec.yaml`
- [x] `openspec/changes/REQ-450/tasks.md`

## Stage: implementation

- [x] `orchestrator/migrations/0010_improver_runs.sql`: new `improver_runs` table
- [x] `orchestrator/migrations/0010_improver_runs.rollback.sql`: rollback
- [x] `orchestrator/src/orchestrator/store/improver_runs.py`:
  - `insert_run`, `count_in_budget_window`, `last_non_skipped_at`, `update_status`
  - `_budget_window(ts)` ISO-week Monday helper
- [x] `orchestrator/src/orchestrator/config.py`:
  - 6 new fields: `improver_enabled`, `improver_interval_sec`, `improver_budget_per_window`,
    `improver_cooldown_per_rule_days`, `improver_min_sample_count`, `improver_bkd_project_id`
- [x] `orchestrator/src/orchestrator/improver.py`:
  - `_eval_latency_guard`, `_eval_loop_cap`, `_eval_flake_tolerance`, `_eval_throughput`
  - `_check_budget_and_cooldown`, `_submit_to_bkd`
  - `_tick() -> dict`, `run_loop()`
- [x] `orchestrator/src/orchestrator/main.py`:
  - Import `improver`
  - Step 7: `if settings.improver_enabled` → create task
  - `startup.ok` log includes `improver_enabled`

## Stage: docs

- [x] `CLAUDE.md`: migration list updated to include `0010_improver_runs`
- [x] `README.md`: migration range updated to `0010_improver_runs`
- [x] `docs/observability.md`: table count 7→8, range 0009→0010

## Stage: tests

- [x] `orchestrator/tests/test_improver.py`:
  - `test_budget_window_is_monday`
  - `test_insert_run_returns_id`, `test_count_in_budget_window`, `test_last_non_skipped_at_none`
  - latency-guard: triggers / no-trigger / empty / capped
  - loop-cap: raises / lowers / insufficient-data
  - flake-tolerance: raises / lowers / no-trigger
  - throughput: raises / no-trigger
  - `test_check_budget_returns_budget_when_full`, `test_check_budget_returns_cooldown`, `test_check_budget_returns_none_when_ok`
  - `test_tick_detect_only_writes_pending`, `test_tick_skips_when_budget_exceeded`
- [x] `orchestrator/tests/test_contract_docs_drift_audit.py`:
  - Migration count updated 9→10, regex updated to match 0010

## Stage: PR

- [x] git push feat/REQ-450
- [x] gh pr create --label sisyphus
