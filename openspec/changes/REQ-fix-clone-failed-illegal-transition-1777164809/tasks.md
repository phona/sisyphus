# Tasks: REQ-fix-clone-failed-illegal-transition-1777164809

## Stage: state machine

- [x] `orchestrator/src/orchestrator/state.py`：TRANSITIONS 表新增 `(ANALYZING, VERIFY_ESCALATE) → Transition(ESCALATED, "escalate", ...)` 与 `(INTAKING, VERIFY_ESCALATE) → Transition(ESCALATED, "escalate", ...)` 两条，让 `start_analyze` / `start_analyze_with_finalized_intent` 在 clone 失败 / finalized intent 缺失时 emit `verify.escalate` 能链式推进到 escalate action

## Stage: tests

- [x] `orchestrator/tests/test_state.py`：EXPECTED 表加两行 `(ANALYZING / INTAKING, VERIFY_ESCALATE) → ESCALATED + escalate`
- [x] `orchestrator/tests/test_engine.py`：
  - `test_start_analyze_clone_failed_chains_to_escalate` —— 验 INIT → ANALYZING (start_analyze emit verify.escalate) → ESCALATED (escalate) 全链路通；最终 state=ESCALATED；chained.action=="escalate"
  - `test_start_analyze_with_finalized_intent_clone_failed_chains_to_escalate` —— INTAKING → ANALYZING → ESCALATED 链路通

## Stage: spec

- [x] `openspec/changes/REQ-fix-clone-failed-illegal-transition-1777164809/proposal.md`
- [x] `openspec/changes/REQ-fix-clone-failed-illegal-transition-1777164809/tasks.md`
- [x] `openspec/changes/REQ-fix-clone-failed-illegal-transition-1777164809/specs/clone-failed-escalate-route/contract.spec.yaml`
- [x] `openspec/changes/REQ-fix-clone-failed-illegal-transition-1777164809/specs/clone-failed-escalate-route/spec.md`

## Stage: PR

- [x] git push feat/REQ-fix-clone-failed-illegal-transition-1777164809
- [x] gh pr create
