# tasks: REQ-bkd-intent-statusid-sync-1777280751

## Stage: spec
- [x] 写 proposal.md（动机 / 状态映射 / 行为契约 / 取舍 / 影响面）
- [x] 写 specs/bkd-intent-status-sync/contract.spec.yaml（black-box 契约）
- [x] 写 specs/bkd-intent-status-sync/spec.md（ADDED Requirements + Scenarios BIS-S1..S12）

## Stage: implementation
- [x] orchestrator/src/orchestrator/intent_status.py 新文件：`STATE_TO_STATUS_ID` 映射 + `status_id_for(state)` + async `patch_terminal_status(*, project_id, intent_issue_id, terminal_state, source)`，best-effort + BKD 异常吞掉
- [x] orchestrator/src/orchestrator/engine.py：终态分支已有 cleanup_runner fire-and-forget，并列 await `intent_status.patch_terminal_status`，从 ctx 读 `intent_issue_id`
- [x] orchestrator/src/orchestrator/actions/escalate.py：SESSION_FAILED inner CAS to ESCALATED 后，await `intent_status.patch_terminal_status` 推 statusId="review"

## Stage: tests
- [x] orchestrator/tests/test_intent_status.py 新文件：BIS-S1..S8 unit test 覆盖 helper 行为（mapping、空 intent_id、BKD 异常）
- [x] orchestrator/tests/test_engine.py：BIS-S9 + BIS-S10，在已有 terminal cleanup 测试旁加断言 `patch_terminal_status` 被调用，参数正确
- [x] orchestrator/tests/test_contract_intent_status_sync.py 新文件：BIS-S11 真 escalate 路径推 review，BIS-S12 PR-merged-override 不依赖本 hook
- [x] 跑 `make ci-unit-test`（新 test 全过 + 不破坏现有 engine / escalate 测试）

## Stage: PR
- [x] git push origin feat/REQ-bkd-intent-statusid-sync-1777280751
- [x] gh pr create
