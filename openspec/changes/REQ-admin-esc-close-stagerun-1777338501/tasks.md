# Tasks — REQ-admin-esc-close-stagerun-1777338501

## Stage: spec
- [x] author specs/admin-stage-runs/contract.spec.yaml
- [x] author specs/admin-stage-runs/spec.md (FESC-S1, FESC-S2)

## Stage: implementation
- [x] `admin.py`: import `stage_runs`; before raw SQL UPDATE, look up `engine.STATE_TO_STAGE.get(row.state)` and call `close_latest_stage_run` if stage found (best-effort)

## Stage: unit test
- [x] `tests/test_admin.py`: add FRE-S1 mock for `close_latest_stage_run` (state=ANALYZING hits STATE_TO_STAGE)
- [x] `tests/test_admin.py`: add FRE-S3 `test_force_escalate_closes_current_stage_run` — verifies close called with correct args AND before SQL UPDATE
- [x] `tests/test_admin.py`: add FRE-S4 `test_force_escalate_no_close_when_state_has_no_stage` — INIT state → no close call

## Stage: PR
- [x] git push feat/REQ-admin-esc-close-stagerun-1777338501
- [x] gh pr create
