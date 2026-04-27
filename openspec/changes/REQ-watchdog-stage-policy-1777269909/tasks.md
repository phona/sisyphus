# tasks — REQ-watchdog-stage-policy-1777269909

## Stage: contract / spec
- [x] author specs/watchdog-stage-policy/spec.md（ADDED Requirements + scenarios WSP-S1..S6）
- [x] proposal.md 写清动机、方案、取舍、影响范围

## Stage: implementation
- [x] orchestrator/src/orchestrator/watchdog.py：引入 `_NO_WATCHDOG_STATES`，SQL 预过滤合并
- [x] orchestrator/src/orchestrator/watchdog.py：删除 `_INTAKE_RESULT_TAGS` / `_INTAKE_NO_RESULT_EVENT` / `_INTAKE_NO_RESULT_REASON` / `_is_intake_no_result_tag` 死代码
- [x] orchestrator/src/orchestrator/watchdog.py：`_check_and_escalate` 中 intake-no-result-tag 分支删除
- [x] orchestrator/src/orchestrator/watchdog.py：`_STATE_ISSUE_KEY` 中 INTAKING 条目删除（不再 dispatch）
- [x] orchestrator/src/orchestrator/actions/escalate.py：`_SESSION_END_SIGNALS` 移除 `"watchdog.intake_no_result_tag"`
- [x] 删除 openspec/changes/REQ-watchdog-intake-no-result-1777078182/（被本 REQ 取代，未归档）

## Stage: test
- [x] orchestrator/tests/test_watchdog.py：新增 INTAKING-skipped-via-sql case
- [x] orchestrator/tests/test_watchdog.py：删除 9a/9b/9d intake-no-result-tag / intake-fall-through 旧 case
- [x] orchestrator/tests/test_watchdog.py：更新 9c (running 已被 SQL 滤掉) 解释
- [x] 删除 orchestrator/tests/test_contract_watchdog_intake_no_result.py（contract 失效）
- [x] make ci-unit-test 全绿

## Stage: PR
- [x] git push feat/REQ-watchdog-stage-policy-1777269909
- [x] gh pr create --label sisyphus + sisyphus footer
