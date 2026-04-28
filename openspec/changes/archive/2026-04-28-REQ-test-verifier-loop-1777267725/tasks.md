# Tasks for REQ-test-verifier-loop-1777267725

## Stage: contract / spec

- [x] author `proposal.md` —— 描述 16 条 verifier 子链 + SESSION_FAILED scenario 范围 + 不做清单
- [x] author `specs/engine-verifier-loop-tests/spec.md` ADDED delta：
      列出 VLT-S1..VLT-S16 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] 新增 `orchestrator/tests/test_engine_verifier_loop.py`：
  - 复用 `test_engine.py` 的 `FakePool` / `FakeReq` / `_drain_tasks`
    （直接 `from test_engine import ...`，跟 `test_engine_adversarial.py` 同模式）
  - 16 条 case 一一对应 VLT-S1..VLT-S16 scenario
  - 所有 case 用 `pytest.mark.asyncio`，不依赖真 DB / BKD / K8s
- [x] VLT-S1：spec_lint fail → REVIEW_RUNNING (invoke_verifier_for_spec_lint_fail)
- [x] VLT-S2：dev_cross_check fail → REVIEW_RUNNING
- [x] VLT-S3：staging_test fail → REVIEW_RUNNING
- [x] VLT-S4：pr_ci fail → REVIEW_RUNNING
- [x] VLT-S5：accept teardown fail → REVIEW_RUNNING
- [x] VLT-S6：analyze_artifact_check fail → REVIEW_RUNNING
- [x] VLT-S7：challenger fail → REVIEW_RUNNING
- [x] VLT-S8：REVIEW_RUNNING + VERIFY_FIX_NEEDED → FIXER_RUNNING + stage_runs close/open
- [x] VLT-S9：REVIEW_RUNNING + VERIFY_ESCALATE → ESCALATED + cleanup runner
- [x] VLT-S10：FIXER_RUNNING + FIXER_DONE → REVIEW_RUNNING
- [x] VLT-S11：FIXER_RUNNING + VERIFY_ESCALATE → ESCALATED (round cap escape)
- [x] VLT-S12：ESCALATED + VERIFY_PASS resume → apply_verify_pass dispatched
- [x] VLT-S13：ESCALATED + VERIFY_FIX_NEEDED resume → FIXER_RUNNING
- [x] VLT-S14：ESCALATED + VERIFY_ESCALATE resume → no-op，no extra cleanup
- [x] VLT-S15：每个 *_RUNNING state + SESSION_FAILED → escalate self-loop（参数化 13 状态）
- [x] VLT-S16：INIT + SESSION_FAILED → skip（INIT 不在 SESSION_FAILED 集合）

## Stage: PR

- [x] git push feat/REQ-test-verifier-loop-1777267725
- [x] gh pr create --label sisyphus（PR body 写动机 + 测试方案）
