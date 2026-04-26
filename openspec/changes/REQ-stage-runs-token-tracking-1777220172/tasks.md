# tasks: REQ-stage-runs-token-tracking-1777220172

## Stage: contract / spec

- [x] author `specs/stage-runs-token-tracking/spec.md` with delta
      `## ADDED Requirements` (3 requirements, scenarios STR-S1..STR-S8)

## Stage: implementation

- [x] `orchestrator/migrations/0008_stage_runs_bkd_session_id.sql`: ADD COLUMN
      `bkd_session_id TEXT` + partial index `idx_stage_runs_bkd_session`
- [x] `orchestrator/migrations/0008_stage_runs_bkd_session_id.rollback.sql`:
      DROP INDEX + DROP COLUMN
- [x] `orchestrator/src/orchestrator/bkd.py`: add `external_session_id` field
      to `Issue`; `_to_issue` populates from `d.get("externalSessionId")`
- [x] `orchestrator/src/orchestrator/store/stage_runs.py`: new
      `stamp_bkd_session_id(pool, req_id, stage, bkd_session_id)` helper
      (idempotent, target ended_at IS NULL AND bkd_session_id IS NULL)
- [x] `orchestrator/src/orchestrator/engine.py`: promote `_STATE_TO_STAGE` →
      public `STATE_TO_STAGE`; export `AGENT_STAGES` frozenset
- [x] `orchestrator/src/orchestrator/webhook.py`: extend BKD fetch to cover
      `session.failed`; capture `issue.external_session_id`; before
      `engine.step`, stamp on the open agent-stage row (best-effort,
      try/except so observability never blocks webhook)

## Stage: unit test

- [x] `orchestrator/tests/test_store_stage_runs.py`: 4 new tests covering
      stamp helper happy path, no-row-matched, empty-token no-op, and that
      `insert_stage_run` does not inline `bkd_session_id`
- [x] `orchestrator/tests/test_bkd_rest.py`: 3 new tests covering `_to_issue`
      extraction, default-None, and end-to-end via REST `get_issue`
- [x] `orchestrator/tests/test_contract_stage_runs_token_tracking.py`: 4
      contract scenarios STR-S1..S4 covering stamp ordering, mechanical-stage
      skip, missing-token skip, and session.failed stamping

## Stage: PR

- [x] `make ci-lint` clean
- [x] `make ci-unit-test` (936 passed)
- [x] `make ci-integration-test` (no integration tests collected → pass)
- [x] git push `feat/REQ-stage-runs-token-tracking-1777220172`
- [x] gh pr create
