# Tasks: REQ-watchdog-intake-no-result-1777078182

## Stage: spec

- [x] `openspec/changes/REQ-watchdog-intake-no-result-1777078182/proposal.md`
- [x] `openspec/changes/REQ-watchdog-intake-no-result-1777078182/tasks.md`
- [x] `openspec/changes/REQ-watchdog-intake-no-result-1777078182/specs/watchdog/spec.md`
- [x] `openspec/changes/REQ-watchdog-intake-no-result-1777078182/specs/watchdog/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/watchdog.py`：
  - 加 `INTAKING → intent_issue_id` 到 `_STATE_ISSUE_KEY`
  - 加纯函数 `_is_intake_no_result_tag(state, issue)`
  - `_check_and_escalate` 区分 intake-no-result-tag vs 通用 stuck，写专属
    artifact_checks stage/reason，预先 PATCH ctx.escalated_reason，体设 body.event
- [x] `orchestrator/src/orchestrator/actions/escalate.py`：
  - 加 `_SESSION_END_SIGNALS`（含 `watchdog.intake_no_result_tag`），
    `is_session_failed_path` 改用此集合，确保 cleanup CAS + runner 清理仍跑

## Stage: tests

- [x] `orchestrator/tests/test_watchdog.py`：
  - 纯函数 grid case (`_is_intake_no_result_tag`)
  - 集成 case：INTAKING + completed + 无 result → 专属 path
  - 集成 case：INTAKING + completed + result:pass → fall through 通用 path
  - 集成 case：INTAKING + running → skip
  - 集成 case：BKD lookup 失败 → fall through 通用 path
- [x] `orchestrator/tests/test_actions_smoke.py`：
  - 新增 escalate.py 处理 body.event=watchdog.intake_no_result_tag 的 case
    （直接真 escalate + reason 透传 ctx + cleanup 跑）

## Stage: PR

- [x] git push feat/REQ-watchdog-intake-no-result-1777078182
- [x] gh pr create
