# REQ-441 Tasks

## Stage: contract / spec

- [x] author specs/watchdog-stuck-double-check/spec.md with CHALLENGER_RUNNING reconcile scenarios

## Stage: implementation

- [x] Add `ReqState.CHALLENGER_RUNNING: "challenger_issue_id"` to `_STATE_ISSUE_KEY` in watchdog.py

## Stage: tests

- [x] test_challenger_running_session_skips_when_still_running — session=running → skip
- [x] test_challenger_running_session_failed_escalates — session=failed + elapsed > ended_sec → escalate
- [x] test_challenger_running_in_state_issue_key — structural contract test

## Stage: PR

- [x] git push feat/REQ-441
- [x] gh pr create --label sisyphus
