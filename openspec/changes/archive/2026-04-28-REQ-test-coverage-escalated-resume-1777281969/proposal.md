# REQ-test-coverage-escalated-resume-1777281969: 收口 47/47 transition 的 engine.step mock 覆盖 + 加深 ESCALATED resume 路径

## 问题

最近三个 PR 把 engine.step mock 测试覆盖推进到了 41/47：

- `test_engine_main_chain.py` (#159)：MCT-S1..MCT-S11，11 条 happy-path 主链
- `test_engine_accept_phase.py` (#158)：APT-S1..APT-S7，7 条 accept 阶段
- `test_engine_verifier_loop.py` (#160)：VLT-S1..VLT-S16，16 条 verifier 子链 +
  SESSION_FAILED 兜底（含 13 个 *_RUNNING state 的 self-loop 参数化）

从 `state.TRANSITIONS` 静态扫描 47 条 transition 减去三家覆盖之后，剩 6 条**没有**
任何 engine.step 级 mock 测试覆盖：

| # | transition | next_state | action |
|---|---|---|---|
| 1 | `(INIT, INTENT_INTAKE)` | INTAKING | `start_intake` |
| 2 | `(INTAKING, INTAKE_PASS)` | ANALYZING | `start_analyze_with_finalized_intent` |
| 3 | `(INTAKING, INTAKE_FAIL)` | ESCALATED | `escalate` |
| 4 | `(INTAKING, VERIFY_ESCALATE)` | ESCALATED | `escalate` |
| 5 | `(ANALYZING, VERIFY_ESCALATE)` | ESCALATED | `escalate` |
| 6 | `(PR_CI_RUNNING, PR_CI_TIMEOUT)` | ESCALATED | `escalate` |

而 `(ESCALATED, VERIFY_PASS)` / `(ESCALATED, VERIFY_FIX_NEEDED)` 这两条**人工
resume 路径**虽然在 VLT-S12 / VLT-S13 已被 dispatch 级覆盖，但只验"action 名 +
state 不变"——并没验真正"resume from ESCALATED 把 REQ 推到下一 stage"的端到端
路由（apply_verify_pass action 内部会 CAS `ESCALATED → STAGING_TEST_RUNNING`
然后 chain emit `STAGING_TEST_PASS`，engine 链式 dispatch `create_pr_ci_watch`）。
这条 chain 是 sisyphus 唯一让人手把"卡死"REQ 续起来的路径，回归一次代价就是
所有 escalate 的 REQ 全部失活——必须有专门的端到端 mock 测试钉死它。

## 方案

新增 `orchestrator/tests/test_engine_escalated_resume.py`，9 条 scenario
（ERT-S1..ERT-S9），全部用 in-process FakePool + stub action 隔离副作用，**不打
BKD / Postgres / K8s**。复用 `test_engine.py` 的 `FakePool` / `FakeReq` /
`_drain_tasks`（直接 `from test_engine import ...`，跟 `test_engine_adversarial.py`
/ `_main_chain` / `_verifier_loop` 同模式）。

| ID | 场景 | 期望 |
|---|---|---|
| ERT-S1 | `(INIT, INTENT_INTAKE)` | dispatch `start_intake`，state → `INTAKING` |
| ERT-S2 | `(INTAKING, INTAKE_PASS)` | dispatch `start_analyze_with_finalized_intent`，state → `ANALYZING` |
| ERT-S3 | `(INTAKING, INTAKE_FAIL)` | dispatch `escalate`，state → `ESCALATED`，cleanup_runner(retain_pvc=True) 被 await 一次 |
| ERT-S4 | `(INTAKING, VERIFY_ESCALATE)` | dispatch `escalate`，state → `ESCALATED`，cleanup_runner(retain_pvc=True) 被 await 一次 |
| ERT-S5 | `(ANALYZING, VERIFY_ESCALATE)` | dispatch `escalate`，state → `ESCALATED`，cleanup_runner(retain_pvc=True) 被 await 一次 |
| ERT-S6 | `(PR_CI_RUNNING, PR_CI_TIMEOUT)` | dispatch `escalate`，state → `ESCALATED`，cleanup_runner(retain_pvc=True) 被 await 一次 |
| ERT-S7 | ESCALATED + VERIFY_PASS **端到端 resume** | apply_verify_pass stub 内部 CAS `ESCALATED → STAGING_TEST_RUNNING` 并 `emit STAGING_TEST_PASS`；engine 链式调 `create_pr_ci_watch`；最终 row state = `PR_CI_RUNNING` |
| ERT-S8 | ESCALATED + VERIFY_FIX_NEEDED **tag 透传** | start_fixer 收到的 tags 含 `verify:staging_test`、ctx 含 `verifier_stage=staging_test`，state → `FIXER_RUNNING` |
| ERT-S9 | 47/47 静态 sweep | 参数化遍历 `state.TRANSITIONS` 全 47 项，每项构造 stub action，`engine.step` 必须返 `action == transition.action`（或 `no-op` if `transition.action is None`）+ `next_state == transition.next_state.value` |

## 边界 & 不做

- **不验** action handler 内部行为 —— 已被 `test_verifier.py` /
  `test_actions_start_analyze.py` / `test_intake.py` 覆盖
- **不修** `state.TRANSITIONS` / engine.py / actions —— 纯加测，不改产线代码
- **不为** sweep 测试加入 stage_runs 副作用断言 —— 那是 `test_store_stage_runs.py`
  的范围；本 sweep 只校"engine.step 把表里声明的每条 transition 都跑得通"
- ERT-S9 是 **defense-in-depth** —— 即便单粒度 ERT-S1..S6 / VLT / MCT / APT
  漏了某条 case，sweep 也会 fail
