# Tasks for REQ-engine-adversarial-tests-v2-1777256408

## Stage: contract / spec

- [x] author `proposal.md` —— 描述 12 条 adversarial scenario 范围 + 不做清单
- [x] author `specs/engine-adversarial-tests/spec.md` ADDED delta：
      列出 EAT-S1..EAT-S12 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] 新增 `orchestrator/tests/test_engine_adversarial.py`：
  - 复用 `test_engine.py` 已有的 `FakePool` + `FakeReq` + `stub_actions` 设计
    （直接 import 它们的私有定义，避免抄一份让两边漂移）
  - 12 条 case 一一对应 EAT-S1..S12 scenario
  - 所有 case 用 `pytest.mark.asyncio`，不依赖真 DB / BKD / K8s
- [x] EAT-S1：emit garbage → invalid_emit log，无 chained
- [x] EAT-S2：handler 返 None → 视作空 dict
- [x] EAT-S3：handler 返 list → 视作空 dict
- [x] EAT-S4：chain 中段 row 消失（FakePool 删 row）→ 安全早返
- [x] EAT-S5：chained emit 推到无 transition 的 state → skip
- [x] EAT-S6：action 不在 REGISTRY → action 'X' not registered
- [x] EAT-S7：terminal self-loop（ESCALATED + VERIFY_ESCALATE）不再 cleanup
- [x] EAT-S8：stage_runs INSERT 抛异常 → 主流程不挂
- [x] EAT-S9：body 缺 issueId → 不抛
- [x] EAT-S10：recursion 边界（depth=12 vs depth=13）
- [x] EAT-S11：SESSION_FAILED 在 DONE → skip（terminal 不接 SESSION_FAILED）
- [x] EAT-S12：DONE 终态对所有 Event → skip（穷举）

## Stage: PR

- [x] git push feat/REQ-engine-adversarial-tests-v2-1777256408
- [x] gh pr create --label sisyphus（PR body 写动机 + 测试方案）
