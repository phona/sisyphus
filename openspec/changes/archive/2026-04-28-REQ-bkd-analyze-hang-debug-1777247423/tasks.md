# Tasks — REQ-bkd-analyze-hang-debug-1777247423

## Stage: investigate
- [x] Read 60-min watchdog escalations from `artifact_checks` to confirm symptom
- [x] Identify SQL prefilter floor in `watchdog.py:_tick` as the structural cause
- [x] Confirm `still_running → skip` in `_check_and_escalate` already protects running sessions
- [x] Confirm escalate.py auto-resume on `body.event=='watchdog.stuck'` causes 3× compounding

## Stage: spec
- [x] Author proposal.md with concrete prod-DB evidence (3-hit / 7250s spans)
- [x] Author design.md with behavioral matrix + rollback plan
- [x] Author specs/watchdog-fast-detection/spec.md (openspec delta format)
- [x] Author specs/watchdog-fast-detection/contract.spec.yaml (config + behavior contract)

## Stage: implementation
- [x] `orchestrator/src/orchestrator/config.py`: add `watchdog_session_ended_threshold_sec: int = 300`
- [x] `orchestrator/src/orchestrator/watchdog.py`: SQL prefilter uses `min(fast, slow)`; behavior matrix per design.md
- [x] `orchestrator/tests/test_watchdog.py`: new tests for fast threshold path
  - dead session at 305s → escalates immediately
  - SQL receives the smaller threshold of the two
  - existing running-skip behaviour preserved at high stuck_sec
- [x] No DB schema / migration / state transition changes (verified)

## Stage: PR
- [x] git push feat/REQ-bkd-analyze-hang-debug-1777247423
- [x] gh pr create --label sisyphus
