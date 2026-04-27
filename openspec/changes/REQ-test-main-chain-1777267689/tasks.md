# Tasks for REQ-test-main-chain-1777267689

## Stage: contract / spec

- [x] author `proposal.md` —— 描述 11 条主链 happy-path scenario 范围 + 不做清单
- [x] author `specs/engine-main-chain-tests/spec.md` ADDED delta：
      列出 MCT-S1..MCT-S11 + MCT-CHAIN 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] 新增 `orchestrator/tests/test_engine_main_chain.py`：
  - 复用 `test_engine.py` 已有的 `FakePool` + `FakeReq` 设计
    （`from test_engine import FakePool, FakeReq` —— 跟 `test_engine_adversarial.py` 同模式）
  - 局部 `stub_actions` fixture 隔离 `REGISTRY` / `ACTION_META`，测后还原
  - 11 条单 transition mock 用例（MCT-S1..MCT-S11）
  - 1 条端到端 chain 用例（MCT-CHAIN）—— stub 每个 action emit 下一事件，
    验从 INIT 一路推到 DONE
  - 所有 case 用 `pytest.mark.asyncio`，不依赖真 DB / BKD / K8s
- [x] MCT-S1：`(INIT, INTENT_ANALYZE)` → `ANALYZING` + `start_analyze`
- [x] MCT-S2：`(ANALYZING, ANALYZE_DONE)` → `ANALYZE_ARTIFACT_CHECKING` + `create_analyze_artifact_check`
- [x] MCT-S3：`(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)` → `SPEC_LINT_RUNNING` + `create_spec_lint`
- [x] MCT-S4：`(SPEC_LINT_RUNNING, SPEC_LINT_PASS)` → `CHALLENGER_RUNNING` + `start_challenger`
- [x] MCT-S5：`(CHALLENGER_RUNNING, CHALLENGER_PASS)` → `DEV_CROSS_CHECK_RUNNING` + `create_dev_cross_check`
- [x] MCT-S6：`(DEV_CROSS_CHECK_RUNNING, DEV_CROSS_CHECK_PASS)` → `STAGING_TEST_RUNNING` + `create_staging_test`
- [x] MCT-S7：`(STAGING_TEST_RUNNING, STAGING_TEST_PASS)` → `PR_CI_RUNNING` + `create_pr_ci_watch`
- [x] MCT-S8：`(PR_CI_RUNNING, PR_CI_PASS)` → `ACCEPT_RUNNING` + `create_accept`
- [x] MCT-S9：`(ACCEPT_RUNNING, ACCEPT_PASS)` → `ACCEPT_TEARING_DOWN` + `teardown_accept_env`
- [x] MCT-S10：`(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` → `ARCHIVING` + `done_archive`
- [x] MCT-S11：`(ARCHIVING, ARCHIVE_DONE)` → `DONE` + `None` (no-op)
- [x] MCT-CHAIN：从 `(INIT, INTENT_ANALYZE)` 一路 emit 链推到 `DONE`

## Stage: PR

- [x] git push feat/REQ-test-main-chain-1777267689
- [x] gh pr create --label sisyphus（PR body 写动机 + 测试方案）
