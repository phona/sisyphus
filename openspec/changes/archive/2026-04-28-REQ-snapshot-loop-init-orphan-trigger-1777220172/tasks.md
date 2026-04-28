# tasks: REQ-snapshot-loop-init-orphan-trigger-1777220172

## Stage: contract / spec

- [x] author `specs/snapshot-orphan-trigger/spec.md` with delta
      `## ADDED Requirements`
- [x] write 5 scenarios `SNAP-ORPHAN-S{1..5}` covering the trigger
      condition, the four skip conditions, and the obs-less startup gate

## Stage: implementation

- [x] `orchestrator/src/orchestrator/snapshot.py`:
      - extract `_trigger_orphan_intent_analyze(main_pool, project_id, issues)`
      - call it inside `sync_once` per project before the `bkd_snapshot`
        UPSERT
      - make the obs UPSERT conditional on `obs_pool is not None`
      - change `sync_once` return to
        `{"projects": int, "snapshot_rows": int, "orphans_triggered": int}`
- [x] `orchestrator/src/orchestrator/main.py`: drop `obs_pg_dsn` from the
      `snapshot.run_loop` startup gate so orphan recovery still runs when
      observability is off
- [x] `orchestrator/tests/test_snapshot.py`:
      - update existing `test_sync_once_*` cases to read the dict return
      - add `test_orphan_intent_analyze_triggers_when_missing_req_state`
      - add `test_orphan_intent_analyze_skipped_when_already_in_req_state`
      - add `test_orphan_intent_analyze_skipped_when_analyze_tag_present`
      - add `test_orphan_intent_analyze_skipped_when_status_done`
      - add `test_sync_once_runs_orphan_pass_without_obs_pool`

## Stage: PR

- [x] git push `feat/REQ-snapshot-loop-init-orphan-trigger-1777220172`
- [x] gh pr create
