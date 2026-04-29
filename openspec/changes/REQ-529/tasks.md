# REQ-529 Tasks

## Stage: execute / spec
- [x] 梳理 state.py 全部 53 条 transition（40 显式 + 13 SESSION_FAILED self-loop）
- [x] 建立未覆盖清单：PR_MERGED 竞态 3 条、VERIFY_INFRA_RETRY 1 条、ESCALATED 恢复态 3 条值未验、CHALLENGER_RUNNING SESSION_FAILED 漏测
- [x] 编写 `test_state_transitions_gap.py`（54 个测试函数）

## Stage: implementation
- [x] ruff lint 通过
- [x] pytest 通过（87/87 状态机测试全绿）
- [x] 全 orchestrator unit tests 通过（1899 passed，9 pre-existing failure 与本次无关）

## Stage: PR
- [x] git push feat/REQ-529
- [x] gh pr create --label sisyphus → https://github.com/phona/sisyphus/pull/208
