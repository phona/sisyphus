# tasks: REQ-bkd-hitl-end-to-end-loop-1777273753

## Stage: spec

- [x] 写 proposal.md（HITL UX 动机 + 终态 statusId sync 缺口 / 设计 / 跟 PR #161 关系）
- [x] 写 specs/intent-status-sync/contract.spec.yaml（black-box 契约）
- [x] 写 specs/intent-status-sync/spec.md（ADDED Requirements + Scenarios HITL-S1..S6）

## Stage: implementation

- [x] orchestrator/src/orchestrator/engine.py: 加 `_TERMINAL_STATE_TO_BKD_STATUS_ID` 常量 + `_sync_intent_status_on_terminal` helper（fire-and-forget; BKD 失败仅 log warning）
- [x] orchestrator/src/orchestrator/engine.py: 在 step() terminal-state CAS 成功块（cleanup_runner 旁边）schedule sync helper（不为 PR-merged shortcut 双调用，那条路径已自带 status_id="done"）
- [x] orchestrator/src/orchestrator/actions/escalate.py: 在 SESSION_FAILED self-loop CAS advanced=True 后调用 sync helper（同步 PATCH 即可，已经 await cleanup_runner，不需要再 fire-and-forget）

## Stage: docs

- [x] docs/architecture.md: 追加 §"HITL end-to-end loop" 描述用户 / sisyphus state / BKD intent statusId 三方在每个 stage 的同步关系（含 mermaid lifecycle 图）

## Stage: tests

- [x] orchestrator/tests/test_intent_status_sync.py（新文件）：覆盖 HITL-S1..S6 六条 scenarios
- [x] 跑 `make ci-unit-test`（新 test 全过 + 不破坏现有 engine / escalate 测试）
- [x] 跑 `make ci-lint`（full scan）

## Stage: PR

- [x] git push origin feat/REQ-bkd-hitl-end-to-end-loop-1777273753
- [x] gh pr create --label sisyphus（含 sisyphus:cross-link footer）
