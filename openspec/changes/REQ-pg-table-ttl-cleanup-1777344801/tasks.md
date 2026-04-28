# Tasks: REQ-pg-table-ttl-cleanup-1777344801

## Stage: spec

- [x] Author `specs/table-ttl/contract.spec.yaml` — background task data contract
- [x] Author `specs/table-ttl/spec.md` — scenarios (TTL-S1 through TTL-S9)

## Stage: implementation

- [x] `orchestrator/src/orchestrator/maintenance/table_ttl.py` — new module:
  `run_ttl_cleanup(pool, cfg)` single cleanup pass + `run_loop()` background loop
- [x] `orchestrator/src/orchestrator/config.py` — 6 new settings:
  `ttl_cleanup_enabled`, `ttl_cleanup_interval_sec`, `ttl_event_seen_days`,
  `ttl_dispatch_slugs_days`, `ttl_verifier_decisions_days`, `ttl_stage_runs_closed_days`
- [x] `orchestrator/src/orchestrator/main.py` — startup step 7: spawn `table_ttl.run_loop`
  as background task when `ttl_cleanup_enabled=True` and `ttl_cleanup_interval_sec > 0`

## Stage: tests

- [x] `orchestrator/tests/test_table_ttl.py` — 6 unit tests (mock pool):
  - SQL structure and cutoff parameter for each table
  - `stage_runs` DELETE condition includes `ended_at IS NOT NULL`
  - summary dict structure
  - `ttl_cleanup_enabled=False` causes `run_loop` to exit immediately
- [x] 3 integration tests (`@pytest.mark.integration`, real PG):
  - `event_seen`: old row deleted, recent row kept
  - `stage_runs` open (ended_at IS NULL): never deleted regardless of age
  - `stage_runs` closed: old deleted, recent kept

## Stage: PR

- [x] `git push origin feat/REQ-pg-table-ttl-cleanup-1777344801`
- [x] `gh pr create --label sisyphus`
