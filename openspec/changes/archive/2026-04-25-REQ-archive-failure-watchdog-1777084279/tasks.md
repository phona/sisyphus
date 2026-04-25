# Tasks: REQ-archive-failure-watchdog-1777084279

## Stage: spec
- [x] `openspec/changes/REQ-archive-failure-watchdog-1777084279/proposal.md`
- [x] `openspec/changes/REQ-archive-failure-watchdog-1777084279/tasks.md`
- [x] `openspec/changes/REQ-archive-failure-watchdog-1777084279/specs/archive-failure/spec.md`
- [x] `openspec/changes/REQ-archive-failure-watchdog-1777084279/specs/archive-failure/contract.spec.yaml`

## Stage: implementation
- [x] 改 `orchestrator/src/orchestrator/watchdog.py`：新增 `_STATE_FAILURE_EVENT` 映射 + `_check_and_escalate` 构造 SyntheticBody 时取 state 对应 event
- [x] 改 `orchestrator/src/orchestrator/actions/escalate.py`：
  - `_CANONICAL_SIGNALS` 加 `"archive.failed"`
  - `_TRANSIENT_REASONS` 加 `"archive-failed"` / `"archive-failed-after-2-retries"`
  - `_is_transient` 识别 `body_event=="archive.failed"`
  - reason 二次 override：`session.failed` webhook + `body.issueId == ctx.archive_issue_id` → `"archive-failed"`
  - final_reason bumping：`reason=="archive-failed"` + retry 用完 → `"archive-failed-after-2-retries"`
  - `is_session_failed_path` 改用 `_CANONICAL_SIGNALS` 集合（包含 archive.failed）

## Stage: tests
- [x] `orchestrator/tests/test_watchdog.py`：
  - 扩 `_patch_engine` 捕获 `body_event`
  - 加 ARCH-S1 case：ARCHIVING 卡死 → body.event="archive.failed"
  - 加 ARCH-S2 case：非 ARCHIVING 仍 body.event="watchdog.stuck"
- [x] `orchestrator/tests/test_actions_smoke.py`：
  - 加 `test_escalate_archive_failed_from_watchdog`（ARCH-S3）
  - 加 `test_escalate_archive_failed_from_bkd_session_failed_webhook`（ARCH-S4）
  - 加 `test_escalate_session_failed_unrelated_issue_unchanged`（ARCH-S5）
  - 加 `test_escalate_archive_failed_real_after_retries_exhausted`（ARCH-S6）

## Stage: PR
- [ ] git push feat/REQ-archive-failure-watchdog-1777084279
- [ ] gh pr create
