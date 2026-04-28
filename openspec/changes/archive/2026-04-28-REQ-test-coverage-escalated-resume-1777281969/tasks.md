# Tasks for REQ-test-coverage-escalated-resume-1777281969

## Stage: contract / spec

- [x] author `proposal.md` —— 列 6 条没人覆盖的 transition + 加深 ESCALATED resume + 47/47 静态 sweep 范围
- [x] author `specs/engine-escalated-resume-tests/spec.md` ADDED delta：
      列出 ERT-S1..ERT-S9 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] 新增 `orchestrator/tests/test_engine_escalated_resume.py`：
  - 复用 `test_engine.py` 的 `FakePool` / `FakeReq` / `_drain_tasks`
    （直接 `from test_engine import ...`，跟 `_main_chain` / `_accept_phase` /
    `_verifier_loop` 同模式）
  - 9 条 case 一一对应 ERT-S1..ERT-S9 scenario
  - 所有 case 用 `pytest.mark.asyncio`，不依赖真 DB / BKD / K8s
- [x] ERT-S1：(INIT, INTENT_INTAKE) → INTAKING + start_intake
- [x] ERT-S2：(INTAKING, INTAKE_PASS) → ANALYZING + start_analyze_with_finalized_intent
- [x] ERT-S3：(INTAKING, INTAKE_FAIL) → ESCALATED + escalate + cleanup_runner
- [x] ERT-S4：(INTAKING, VERIFY_ESCALATE) → ESCALATED + escalate + cleanup_runner
- [x] ERT-S5：(ANALYZING, VERIFY_ESCALATE) → ESCALATED + escalate + cleanup_runner
- [x] ERT-S6：(PR_CI_RUNNING, PR_CI_TIMEOUT) → ESCALATED + escalate + cleanup_runner
- [x] ERT-S7：ESCALATED + VERIFY_PASS 端到端 resume（apply_verify_pass 内部 CAS + chain emit → PR_CI_RUNNING）
- [x] ERT-S8：ESCALATED + VERIFY_FIX_NEEDED tag 透传 → FIXER_RUNNING
- [x] ERT-S9：参数化遍历 47 条 transition，每条 engine.step 必须返
      `transition.action`（or `no-op` if `None`）+ `next_state == transition.next_state`

## Stage: PR

- [x] git push feat/REQ-test-coverage-escalated-resume-1777281969
- [x] gh pr create --label sisyphus（PR body 写动机 + 测试方案 + 引用前 3 条 PR）
