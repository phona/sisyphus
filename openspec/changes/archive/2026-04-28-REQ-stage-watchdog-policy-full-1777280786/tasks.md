# tasks — REQ-stage-watchdog-policy-full-1777280786

## Stage: contract / spec
- [x] author specs/watchdog-stage-policy-full/spec.md（ADDED Requirements + 8 个 scenarios WSPF-S1..S8）
- [x] proposal.md 写清动机、方案、取舍、影响范围

## Stage: implementation
- [x] orchestrator/src/orchestrator/watchdog.py：新增 `_StagePolicy` frozen dataclass
- [x] orchestrator/src/orchestrator/watchdog.py：新增 `_STAGE_POLICY: dict[ReqState, _StagePolicy | None]` 表（13 条）
- [x] orchestrator/src/orchestrator/watchdog.py：`_NO_WATCHDOG_STATES` 改派生（policy is None）
- [x] orchestrator/src/orchestrator/watchdog.py：`_tick()` SQL threshold 取 `min(所有 policy + 全局 fallback)`
- [x] orchestrator/src/orchestrator/watchdog.py：`_check_and_escalate()` 按 per-stage `_StagePolicy` 判定 escalate / skip
- [x] orchestrator/src/orchestrator/watchdog.py：unmapped state fallback 到全局 `watchdog_session_ended_threshold_sec` + `watchdog_stuck_threshold_sec`

## Stage: test
- [x] orchestrator/tests/test_contract_watchdog_stage_policy_full.py：新建合同测试，覆盖 WSPF-S1..S8
- [x] orchestrator/tests/test_watchdog.py：现有 case 跑通（无回归）
- [x] make ci-lint 全绿
- [x] make ci-unit-test 全绿

## Stage: PR
- [x] git push feat/REQ-stage-watchdog-policy-full-1777280786
- [x] gh pr create --label sisyphus + sisyphus footer
