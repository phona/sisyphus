# Tasks — REQ-fix-watchdog-liveness-1777809646

## Stage: spec

- [ ] author proposal.md (this REQ)
- [ ] author specs/watchdog-liveness/spec.md (delta format, ADDED Requirements)

## Stage: implementation

- [ ] add `BKDRestClient.last_log_activity_at(project_id, issue_id)` returning latest log entry `createdAt` or `None`
- [ ] add `watchdog_liveness_grace_sec: int = 120` to `config.Settings`
- [ ] in `watchdog._check_and_escalate`, after BKD `get_issue`, call `last_log_activity_at`; if recent activity within grace window → log skip + return False

## Stage: tests

- [ ] unit test: `BKDRestClient.last_log_activity_at` parses `createdAt` from latest log; returns None on empty/error
- [ ] unit test: `watchdog._check_and_escalate` returns False (no escalate) when last activity within grace, even if `session_status != "running"`
- [ ] unit test: `watchdog._check_and_escalate` still escalates when last activity is stale (> grace) and session not running, preserving original behaviour
- [ ] contract test: `escalate` action with transient body (`watchdog.stuck`, retry < cap) does NOT call `merge_tags_and_update` adding `escalated` (locks down #353 invariant)

## Stage: PR (推之前必须全绿)

- [ ] git push feat/REQ-fix-watchdog-liveness-1777809646
- [ ] `make ci-lint` 全绿
- [ ] `make ci-unit-test` 全绿
- [ ] `make ci-integration-test` 全绿（无 PG 环境跳过视为 pass）
- [ ] gh pr create --label sisyphus
