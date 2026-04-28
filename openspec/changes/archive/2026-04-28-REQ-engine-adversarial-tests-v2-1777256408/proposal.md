# REQ-engine-adversarial-tests-v2-1777256408: test(engine): adversarial mock tests for state machine

## 问题

`orchestrator/src/orchestrator/engine.py` 的 `engine.step` 是状态机推进器：
查 transition → CAS → record stage_runs → dispatch action → 链式 emit。
任一环节挂掉都直接影响所有 REQ 的状态推进。

现有 `test_engine.py`（14 条）只覆盖了 happy-path emit chain + 几个常规 fail
（cleanup 失败 / illegal transition / 简单 CAS race）。**真要把 engine.step 当裁判**，
我们需要一组系统性的 adversarial 单元测试，专门挑奇形输入 / 反常返回值 / 死路径
往里灌，看 engine 会不会：

1. 把脏数据当 emit 链下去（如 handler 返回 `{"emit": "garbage"}` 时尝试推进）
2. 在 chain 中段 row 消失时崩溃（`req_state.get` 返回 None）
3. 把 None / list / int 当 emit dict 解构 (`result.get("emit")`)
4. 在已是 terminal state 时多次触发 cleanup（self-loop 在 ESCALATED 上）
5. 因 stage_runs DB 写挂导致主流程异常（违反 best-effort 契约）
6. action 不在 REGISTRY 时触发崩溃而非返回 error
7. 在 chained 路径里命中 illegal transition 时丢失 base_result

## 方案

新增 `orchestrator/tests/test_engine_adversarial.py`：12 条 adversarial 用例
（EAT-S1..S12），全部用 in-process FakePool + stub action 隔离副作用，**不打 BKD
不打 Postgres 不打 K8s**，复用已有 `test_engine.py` 的 `FakePool` + `stub_actions`
fixture 模式（直接从 conftest / 跨 test 引用，不抄一份）。

每个 case 一句话：

| ID | 反常输入 | 期望 |
|---|---|---|
| EAT-S1 | handler 返回 `{"emit": "garbage-event"}` | 不抛错；engine 记 `engine.invalid_emit` log；返回 base_result，无 `chained` 字段 |
| EAT-S2 | handler 返回 `None` | 不抛错；engine 把 result 当空 dict 处理；无 `chained` 字段 |
| EAT-S3 | handler 返回 `[1,2,3]`（非 dict） | 同 S2：被规整成空 dict，无 `chained` |
| EAT-S4 | chain 中段 `req_state.get` 返回 None（行被删 / 库重置） | engine 早返 base_result，不抛 AttributeError |
| EAT-S5 | chained emit 推到一个该 state 没注册的 transition | 子 step 返回 skip+`no transition`；父 base_result 含 `chained.action == "skip"` |
| EAT-S6 | action 名在 transition 表但不在 REGISTRY | 返回 `{"action":"error","reason":"action <X> not registered"}`，CAS 已经成功但不副作用 |
| EAT-S7 | ESCALATED + VERIFY_ESCALATE（self-loop, action=None） | 返回 `no-op`，**不**再次触发 cleanup_runner（cur 已 terminal） |
| EAT-S8 | stage_runs INSERT 抛异常 | engine.step 不抛；transition 仍成功 |
| EAT-S9 | body 缺 issueId 属性（getattr 默认 None） | 不抛 AttributeError |
| EAT-S10 | depth=12 边界（合法）+ depth=13（触红） | depth=12 走完最后一次 dispatch；depth=13 立刻返 recursion error |
| EAT-S11 | SESSION_FAILED 在 terminal state（DONE） | 无 transition → skip，escalate action 不被调 |
| EAT-S12 | DONE 终态对所有 Event 都返 skip | 全枚举遍历 Event：terminal DONE 不接任何事件 |

S1-S6 验"垃圾输入不崩 engine"。
S7-S9 验"边界 / 内部异常不传染主链"。
S10-S12 验"终态语义"。

测试**只触 engine.py**，不触 actions/checkers/k8s_runner —— 这是状态机层的契约
测试，不应混入 stage 业务逻辑（否则跟 `test_engine_chain_*` / `test_actions_*`
重叠）。

## 不做

- ❌ 不增加新 ReqState / Event：纯测试增量
- ❌ 不改 `engine.py`：全部用现有 API + 伪输入；如果某条 case 挂了
  说明 engine 真的有 bug，需要 follow-up REQ 修
- ❌ 不写 integration test（M18 challenger 写）

## 影响

- `orchestrator/tests/test_engine_adversarial.py` 新增 1 个文件 ≈ 350 行
- 不动 prod 代码。不动 schema。不影响 runtime
