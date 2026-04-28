# REQ-test-accept-phase-1777267654: test(engine): mock tests for the 7 accept-phase transitions

## 问题

`orchestrator/src/orchestrator/state.py` 的状态机定义里，accept 阶段（`ACCEPT_RUNNING` +
`ACCEPT_TEARING_DOWN` 两个 state 出发的 7 条 transition）是 happy-path 链路从 PR-CI
绿到 ARCHIVING 的最后一公里：

```
PR_CI_RUNNING ──pr-ci.pass──▶ ACCEPT_RUNNING
                                  │
                                  ├─accept-env-up.fail─▶ ESCALATED
                                  ├─accept.pass───────▶ ACCEPT_TEARING_DOWN ──teardown-done.pass─▶ ARCHIVING
                                  ├─accept.fail───────▶ ACCEPT_TEARING_DOWN ──teardown-done.fail─▶ REVIEW_RUNNING
                                  └─session.failed────▶ ACCEPT_RUNNING (self-loop)
                                                       ACCEPT_TEARING_DOWN
                                                          └─session.failed─▶ ACCEPT_TEARING_DOWN (self-loop)
```

具体 7 条 transition（state.py L175-L191、L247-L259）：

| # | (cur_state, event) | next_state | action |
|---|---|---|---|
| 1 | (ACCEPT_RUNNING, ACCEPT_PASS) | ACCEPT_TEARING_DOWN | teardown_accept_env |
| 2 | (ACCEPT_RUNNING, ACCEPT_FAIL) | ACCEPT_TEARING_DOWN | teardown_accept_env |
| 3 | (ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) | ESCALATED | escalate |
| 4 | (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) | ARCHIVING | done_archive |
| 5 | (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) | REVIEW_RUNNING | invoke_verifier_for_accept_fail |
| 6 | (ACCEPT_RUNNING, SESSION_FAILED) | ACCEPT_RUNNING (self-loop) | escalate |
| 7 | (ACCEPT_TEARING_DOWN, SESSION_FAILED) | ACCEPT_TEARING_DOWN (self-loop) | escalate |

现有 `orchestrator/tests/test_engine.py` + `test_engine_adversarial.py` 集中验
spec_lint / challenger / staging-test / DONE / ESCALATED 等点，**accept 这 7 条
transition 一条单测都没有**。后果：

- 改 `teardown_accept_env` action 名 / 改 emit 顺序 → CI 不会红
- `(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED` 这条 terminal 边漏了 cleanup_runner
  断言，回归只能靠生产打脸
- `(ACCEPT_TEARING_DOWN, SESSION_FAILED)` self-loop 是 sisyphus
  唯一兜底 BKD session crash 的转移点，没单测保护就是定时炸弹

## 方案

新增 `orchestrator/tests/test_engine_accept_phase.py`：7 条 mock 用例
（APT-S1..APT-S7），逐条验上面 7 条 transition 的：

1. `engine.step` 收到事件后 CAS 成功推到目标 state
2. 转移表声明的 action handler 被调用一次
3. terminal 边（ESCALATED）触发 `cleanup_runner(retain_pvc=True)`，self-loop / 非
   terminal 边不触发
4. handler 主动 emit 下一事件（teardown_accept_env 真实代码会 emit
   `teardown-done.pass` / `teardown-done.fail`）→ 链式 step 推到下一目标 state

复用 `test_engine.py` 已有的 `FakePool` + `FakeReq` + `_drain_tasks` + `stub_actions`
fixture 模式，**不打 BKD 不打 Postgres 不打 K8s**。每条 case 一句话：

| ID | 输入 transition | 期望 |
|---|---|---|
| APT-S1 | (ACCEPT_RUNNING, ACCEPT_PASS) + handler emits `teardown-done.pass` | 状态推到 ACCEPT_TEARING_DOWN，链式再推到 ARCHIVING；done_archive 被调；非 terminal 不 cleanup |
| APT-S2 | (ACCEPT_RUNNING, ACCEPT_FAIL) + handler emits `teardown-done.fail` | 状态推到 ACCEPT_TEARING_DOWN，链式推到 REVIEW_RUNNING；invoke_verifier_for_accept_fail 被调 |
| APT-S3 | (ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) | 状态推到 ESCALATED；escalate 被调；cleanup_runner(retain_pvc=True) 触发一次 |
| APT-S4 | (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) | 状态推到 ARCHIVING；done_archive 被调；不触发 cleanup（ARCHIVING 不是 terminal） |
| APT-S5 | (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) | 状态推到 REVIEW_RUNNING；invoke_verifier_for_accept_fail 被调；不触发 cleanup |
| APT-S6 | (ACCEPT_RUNNING, SESSION_FAILED) self-loop | 状态保持 ACCEPT_RUNNING；escalate 被调；非 terminal 不 cleanup |
| APT-S7 | (ACCEPT_TEARING_DOWN, SESSION_FAILED) self-loop | 状态保持 ACCEPT_TEARING_DOWN；escalate 被调；非 terminal 不 cleanup |

## 不做

- ❌ 不动 `engine.py` / `state.py` —— 全部用现有 API + fake stub；如果 case 挂
  说明状态机或 engine 真的有 bug，需要 follow-up REQ 修
- ❌ 不增加新 ReqState / Event：纯测试增量
- ❌ 不写 integration test（M18 challenger 自己读 spec 黑盒写 contract test）
- ❌ 不验真 `teardown_accept_env` 的 helm uninstall 行为（那是 actions/ 的 unit
  test 范围，不是 engine 状态机层的契约）

## 影响

- `orchestrator/tests/test_engine_accept_phase.py` 新增 1 个文件 ≈ 280 行
- 不动 prod 代码。不动 schema。不影响 runtime
