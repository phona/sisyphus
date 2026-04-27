# REQ-test-main-chain-1777267689: test(engine): main-chain happy-path mock tests

## 问题

`orchestrator/src/orchestrator/state.py` 把状态机权威 transition table 写死，
`engine.step` 是唯一的推进器。**主链 happy path** 11 条 transition（INIT →
ANALYZING → ANALYZE_ARTIFACT_CHECKING → SPEC_LINT_RUNNING → CHALLENGER_RUNNING →
DEV_CROSS_CHECK_RUNNING → STAGING_TEST_RUNNING → PR_CI_RUNNING → ACCEPT_RUNNING
→ ACCEPT_TEARING_DOWN → ARCHIVING → DONE）覆盖**整个 REQ 生命周期**：任何一条
推进失败都会让 REQ 卡死或漏走 stage。

现状测试拆得碎：

- `tests/test_state.py` 做静态断言，只检 `decide(state, event)` 的 `next_state`
  / `action` 字段，**不真跑 `engine.step`**——CAS / record_stage_runs / dispatch
  这一整段全 bypass，状态机层有 bug 也看不出来。
- `tests/test_engine.py` 只覆盖了 11 条主链中的 #4（spec-lint.pass→challenger）
  / #5（challenger.pass→dev-cross-check）+ 几条 cleanup / illegal / CAS 异常路
  径。**1, 2, 3, 6, 8, 9, 10 这 7 条 happy-path transition 在 engine.step 层完全
  没断言**——只有静态映射在 test_state，但映射对了 + dispatch 错了 / CAS 错了 /
  stage_runs 写错了，目前没单测拦得住。
- `tests/test_engine_adversarial.py` 走的是 EAT-S1..S12 反常输入，跟 happy path
  相互不重叠、不替代。

举个真实风险：M18 加 challenger stage 时改了 `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
的 `next_state`，如果当时漏了改 `_record_stage_transitions` 里 `STATE_TO_STAGE`
映射，spec_lint 那条 stage_run 会 close 失败但 engine.step 仍 return success，
test_state 的静态断言也通过——在线上要等 Q12/Q13 看板 orphan stage_run 才报。

## 方案

新增 `orchestrator/tests/test_engine_main_chain.py`：11 条 mock 用例
（MCT-S1..MCT-S11），一对一覆盖主链 11 条 happy-path transition。复用
`tests/test_engine.py` 已有的 `FakePool` / `FakeReq` / `stub_actions` fixture
设计（直接 `from test_engine import …`，跟 `test_engine_adversarial.py` 同模式，
避免抄一份让两边漂移）。

每条 case 做 4 个断言：

1. `engine.step` 返回 `action="<expected_action>"`（dispatch 进了正确 handler）
2. `next_state="<expected_next_state>"`（CAS 推到正确目标）
3. `pool.rows[req_id].state == expected_next_state`（FakePool 真把 row 更新了）
4. stub action 被精确调用一次（不重不漏）

| ID | (state, event) | 期望 next_state | action |
|---|---|---|---|
| MCT-S1 | (INIT, INTENT_ANALYZE) | ANALYZING | `start_analyze` |
| MCT-S2 | (ANALYZING, ANALYZE_DONE) | ANALYZE_ARTIFACT_CHECKING | `create_analyze_artifact_check` |
| MCT-S3 | (ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS) | SPEC_LINT_RUNNING | `create_spec_lint` |
| MCT-S4 | (SPEC_LINT_RUNNING, SPEC_LINT_PASS) | CHALLENGER_RUNNING | `start_challenger` |
| MCT-S5 | (CHALLENGER_RUNNING, CHALLENGER_PASS) | DEV_CROSS_CHECK_RUNNING | `create_dev_cross_check` |
| MCT-S6 | (DEV_CROSS_CHECK_RUNNING, DEV_CROSS_CHECK_PASS) | STAGING_TEST_RUNNING | `create_staging_test` |
| MCT-S7 | (STAGING_TEST_RUNNING, STAGING_TEST_PASS) | PR_CI_RUNNING | `create_pr_ci_watch` |
| MCT-S8 | (PR_CI_RUNNING, PR_CI_PASS) | ACCEPT_RUNNING | `create_accept` |
| MCT-S9 | (ACCEPT_RUNNING, ACCEPT_PASS) | ACCEPT_TEARING_DOWN | `teardown_accept_env` |
| MCT-S10 | (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) | ARCHIVING | `done_archive` |
| MCT-S11 | (ARCHIVING, ARCHIVE_DONE) | DONE | `None` (no-op terminal) |

额外加一条 end-to-end 串联用例 MCT-CHAIN：用 stub action 每条都 `return
{"emit": "<next-event>"}`，从 `(INIT, INTENT_ANALYZE)` 进 engine.step，验它能
一路链式推到 DONE，11 个 stub action 各被调用一次，最终 row 状态为 `done`。这
条用例覆盖"emit chain 在主链全长跑得通"——`test_engine.py` 现有 chain 测试只
跨 1 步，深链 chain 没人验证。

测试**只触 engine.py + state.py + actions.REGISTRY 替换**，不触真 BKD / Postgres
/ K8s。在 BKD agent 的工具白名单里，配合 `make ci-unit-test` 在 runner pod
跑过即可。

## 不做

- ❌ 不改 `engine.py` / `state.py` / 任何 prod 代码：纯测试增量
- ❌ 不写 fail-path（fail 路径已由 test_state.py 静态映射覆盖；fail action
  dispatch 由 test_actions_smoke 覆盖；fail → verifier 链由 test_verifier.py 覆盖）
- ❌ 不写 INTAKING 入口（INTAKING → ANALYZING 已在 test_state.py 静态覆盖；
  本 REQ 聚焦"主线 11 步" + 一条端到端 chain）
- ❌ 不写 integration test（M18 challenger 写）

## 影响

- `orchestrator/tests/test_engine_main_chain.py` 新增 1 个文件 ≈ 280 行
- 不动 prod 代码。不动 schema。不影响 runtime
- CI 时长增量：12 个 mock case，全 in-process FakePool，单条 < 50ms，总 < 1s
