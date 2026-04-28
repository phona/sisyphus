# REQ-test-verifier-loop-1777267725: 补 verifier 子链 + SESSION_FAILED 兜底 mock test

## 问题

`orchestrator/src/orchestrator/state.py` 的 transition 表里有两块"事故响应路径"：

1. **verifier 子链**（M14b/c）
   - 任一上游 stage（analyze_artifact_check / spec_lint / challenger / dev_cross_check
     / staging_test / pr_ci / accept-teardown）失败 → `*_FAIL` 事件 → `REVIEW_RUNNING` +
     `invoke_verifier_for_<stage>_fail` action
   - `REVIEW_RUNNING` 出口 3 路：`VERIFY_PASS`(self-loop, action 内 CAS) /
     `VERIFY_FIX_NEEDED` → `FIXER_RUNNING` / `VERIFY_ESCALATE` → `ESCALATED`
   - `FIXER_RUNNING` 出口 2 路：`FIXER_DONE` 回 `REVIEW_RUNNING` /
     `VERIFY_ESCALATE` → `ESCALATED`（round cap 触顶）
   - 人工 resume 路径：`ESCALATED` + 3 个 verify 事件分别接到 `apply_verify_pass`
     / `start_fixer` / 自循环 no-op

2. **SESSION_FAILED 兜底**
   - 13 个 `*_RUNNING` state 都有 `(st, SESSION_FAILED) → (st, "escalate")` self-loop
     transition（dict-comprehension 写法），让 BKD agent 异常或 watchdog 兜底信号都能
     进 escalate action 自决 auto-resume / 真 ESCALATE
   - `INIT` / `GH_INCIDENT_OPEN` / `DONE` / `ESCALATED` **不在**列表 → 应当 skip 而不是
     误进 escalate
   - `escalate` action 内部对 `SESSION_FAILED` 类信号要手动 CAS 把 self-loop 推到
     `ESCALATED`，配合 `VERIFY_ESCALATE` 类信号本身就是表里直跳

现有 `test_engine.py` + `test_engine_adversarial.py` 只散落覆盖了:
- spec-lint.pass → start_challenger 主链 happy path
- (REVIEW_RUNNING, VERIFY_PASS) self-loop close orphan stage_run
- 一条 SESSION_FAILED on REVIEW_RUNNING 不动 stage_run
- DONE + SESSION_FAILED → skip (EAT-S11)
- DONE + 任意 Event → skip (EAT-S12)

**没有**对 verifier 子链的进入 / 出口 / 人工 resume / FIXER 路径，**也没有**对 13 个
running state 的 SESSION_FAILED self-loop 做系统性 mock test 覆盖。一旦谁动了
transition 表（漏挂某条 `*_FAIL` → verifier，或漏挂某 running state 的 SESSION_FAILED）
单测照样绿，实测就会卡死或误路。

## 方案

新增 `orchestrator/tests/test_engine_verifier_loop.py`：16 条 scenario
（VLT-S1..VLT-S16），全部用 in-process FakePool + stub action 隔离副作用，**不打 BKD
不打 Postgres 不打 K8s**，复用 `test_engine.py` 已有的 `FakePool` / `FakeReq` / 同包
import 模式（跟 `test_engine_adversarial.py` 一致），保证两边漂移成本接近 0。

| ID | 场景 | 期望 |
|---|---|---|
| VLT-S1 | `(SPEC_LINT_RUNNING, SPEC_LINT_FAIL)` | dispatch `invoke_verifier_for_spec_lint_fail`，state → `REVIEW_RUNNING` |
| VLT-S2 | `(DEV_CROSS_CHECK_RUNNING, DEV_CROSS_CHECK_FAIL)` | dispatch `invoke_verifier_for_dev_cross_check_fail`，state → `REVIEW_RUNNING` |
| VLT-S3 | `(STAGING_TEST_RUNNING, STAGING_TEST_FAIL)` | dispatch `invoke_verifier_for_staging_test_fail`，state → `REVIEW_RUNNING` |
| VLT-S4 | `(PR_CI_RUNNING, PR_CI_FAIL)` | dispatch `invoke_verifier_for_pr_ci_fail`，state → `REVIEW_RUNNING` |
| VLT-S5 | `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL)` | dispatch `invoke_verifier_for_accept_fail`，state → `REVIEW_RUNNING` |
| VLT-S6 | `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_FAIL)` | dispatch `invoke_verifier_for_analyze_artifact_check_fail`，state → `REVIEW_RUNNING` |
| VLT-S7 | `(CHALLENGER_RUNNING, CHALLENGER_FAIL)` | dispatch `invoke_verifier_for_challenger_fail`，state → `REVIEW_RUNNING` |
| VLT-S8 | `(REVIEW_RUNNING, VERIFY_FIX_NEEDED)` | dispatch `start_fixer`，state → `FIXER_RUNNING`，verifier stage_run close outcome=`fix`，fixer stage_run open |
| VLT-S9 | `(REVIEW_RUNNING, VERIFY_ESCALATE)` | dispatch `escalate`，state → `ESCALATED`，触发 cleanup runner（fire-and-forget） |
| VLT-S10 | `(FIXER_RUNNING, FIXER_DONE)` | dispatch `invoke_verifier_after_fix`，state → `REVIEW_RUNNING`，fixer outcome=`pass` |
| VLT-S11 | `(FIXER_RUNNING, VERIFY_ESCALATE)` | dispatch `escalate`，state → `ESCALATED`（round cap 击顶逃生路径） |
| VLT-S12 | `(ESCALATED, VERIFY_PASS)` resume | dispatch `apply_verify_pass`，state 仍 `ESCALATED`（self-loop；action 内自决推下一 stage） |
| VLT-S13 | `(ESCALATED, VERIFY_FIX_NEEDED)` resume | dispatch `start_fixer`，state → `FIXER_RUNNING` |
| VLT-S14 | `(ESCALATED, VERIFY_ESCALATE)` resume | action=None no-op，state 仍 `ESCALATED`，不重复 cleanup |
| VLT-S15 | 13 个 `*_RUNNING` state 各 `+ SESSION_FAILED` | dispatch `escalate` self-loop（参数化覆盖） |
| VLT-S16 | `(INIT, SESSION_FAILED)` | 无 transition → skip，escalate 不被调 |

边界：
- 用 stub `escalate` / `apply_verify_pass` / `invoke_verifier_*` —— 不验它们内部行为
  （那是 `test_verifier.py` 的活），只验 engine.step 的 dispatch 决策跟 state CAS
- VLT-S15 的 13 个 state 用 `pytest.parametrize` 拆成 13 条独立 case，便于失败定位
- VLT-S9 + VLT-S14 都涉及 ESCALATED + cleanup 行为：S9 是 cur 非 terminal，应触发
  cleanup；S14 是 cur 已 terminal self-loop，应**不**触发（已在 EAT-S7 覆盖，本测复测
  保护回归）

## 不做

- **不验** action handler 内部行为（fan out / BKD issue 创建 / fixer round counter）—
  那是 `test_verifier.py` 的范围，本测只看 engine 的 transition routing
- **不验** stage_runs DB schema / outcome 标签的真实 SQL —— `test_store_stage_runs.py`
  覆盖
- **不验** webhook 解析 decision JSON 的逻辑 —— `test_router.py` /
  `test_verifier.py` 已覆盖
- **不为** 多语言 / runner pod / openspec validate 加新依赖 —— pure mock test
- **不修** transition 表 / `_verifier.py` / `engine.py` —— 纯加测，不改产线
