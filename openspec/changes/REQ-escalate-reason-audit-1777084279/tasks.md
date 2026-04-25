# Tasks: REQ-escalate-reason-audit-1777084279

## Stage: spec

- [x] `openspec/changes/REQ-escalate-reason-audit-1777084279/proposal.md`
- [x] `openspec/changes/REQ-escalate-reason-audit-1777084279/tasks.md`
- [x] `openspec/changes/REQ-escalate-reason-audit-1777084279/specs/escalate-reason-audit/spec.md`
- [x] `openspec/changes/REQ-escalate-reason-audit-1777084279/specs/escalate-reason-audit/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/engine.py`：新增 `_EVENT_TO_ESCALATE_REASON` 映射 + `step()` 中 dispatch escalate action 前预填 ctx.escalated_reason（保留 action-error 前缀）
- [x] `orchestrator/src/orchestrator/actions/escalate.py`：注释更新（说明 engine 已经为非 SESSION_FAILED 路径预填 ctx.escalated_reason；`_is_transient` 里 `"verifier-decision-escalate"` 特判从死代码变成真生效）

## Stage: tests

- [x] `orchestrator/tests/test_engine.py`：新增 6 个 case 覆盖 INTAKE_FAIL / PR_CI_TIMEOUT / ACCEPT_ENV_UP_FAIL / VERIFY_ESCALATE 预填 + SESSION_FAILED 不预填 + action-error 不被覆盖

## Stage: PR

- [x] git push feat/REQ-escalate-reason-audit-1777084279
- [x] gh pr create
