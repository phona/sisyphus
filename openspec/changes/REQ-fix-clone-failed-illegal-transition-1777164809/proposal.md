# REQ-fix-clone-failed-illegal-transition-1777164809: fix(state): add (ANALYZING|INTAKING, VERIFY_ESCALATE) → ESCALATED transitions

## 问题

`actions/start_analyze.py` 与 `actions/start_analyze_with_finalized_intent.py` 在 `clone_involved_repos_into_runner` 失败（auth / repo not found / network）或 `ctx.intake_finalized_intent` 缺失时，会 `return {"emit": "verify.escalate", ...}`，让 engine 链式推进到标准 escalate 路径。但 transition 表里**没有** `(ANALYZING, VERIFY_ESCALATE)` 这一行。

链路实际是：

1. webhook 收到 `intent:analyze` → engine.step 拿到 `(INIT, INTENT_ANALYZE)` transition → CAS 把 state 推到 `ANALYZING` → dispatch `start_analyze`
2. `start_analyze` 跑 `_clone` helper → exit_code != 0 → return `{"emit": "verify.escalate"}`
3. engine reload state（现在是 ANALYZING）→ chained step `(ANALYZING, VERIFY_ESCALATE)` → `decide()` 返 None → `engine.illegal_transition` log → return skip

同样 `(INTAKING, INTAKE_PASS)` transition CAS 后 state 已是 ANALYZING，`start_analyze_with_finalized_intent` emit verify.escalate 也撞同一个空洞。

**症状**：clone 失败 / finalized intent 缺失的 REQ 永远卡在 `analyzing`，runner pod / PVC 不被回收，要等 watchdog 60 min 兜 SESSION_FAILED 才能被推到 ESCALATED；intent issue 上的 escalated tag / reason 也写不上。

## 根因

action handler 主动 emit verify.escalate 当作"我决定 escalate"用，跟 verifier-agent 决策时 emit 的语义复用同一个事件，但 transition 表只覆盖 `(REVIEW_RUNNING, VERIFY_ESCALATE)` 跟 REQ-fixer-round-cap 引入的 `(FIXER_RUNNING, VERIFY_ESCALATE)`。pre-verifier 的 in-flight state（这里是 ANALYZING）从来没补 VERIFY_ESCALATE 出口。

## 方案

### 状态机：补 ANALYZING / INTAKING → ESCALATED transition

在 `orchestrator/src/orchestrator/state.py` 的 TRANSITIONS 表新增两条：

```python
(ReqState.ANALYZING, Event.VERIFY_ESCALATE):
    Transition(ReqState.ESCALATED, "escalate",
               "start_analyze 内部失败（clone / 缺 finalized intent）→ escalate"),
(ReqState.INTAKING, Event.VERIFY_ESCALATE):
    Transition(ReqState.ESCALATED, "escalate",
               "intake 阶段 action 主动 emit escalate（防御对称）"),
```

复用既有 `escalate` action 收口 reason / intent issue tag / runner cleanup，不开第二条 escalate 实现。

INTAKING 这条目前没有 action 触达（`start_intake` 不 emit verify.escalate），但跟 ANALYZING 形状对称：未来若 `start_intake` 加 server-side clone 也是同样的失败模式。补上比让状态机静默卡死好。

### 测试

- `orchestrator/tests/test_state.py:EXPECTED` 加两行 (ANALYZING / INTAKING, VERIFY_ESCALATE) → ESCALATED + escalate
- `orchestrator/tests/test_engine.py` 加端到端 chain 测试：
  - `test_start_analyze_clone_failed_chains_to_escalate`：INIT → ANALYZING (start_analyze) → ESCALATED (escalate)
  - `test_start_analyze_with_finalized_intent_clone_failed_chains_to_escalate`：INTAKING → ANALYZING (start_analyze_with_finalized_intent) → ESCALATED (escalate)

## 取舍

- **不改 action emit 形状**：start_analyze 系列保留 `emit verify.escalate` 不改成 `emit session.failed`。理由：verify.escalate 的语义是"决定不修，给人"，跟 clone/auth/repo 不存在的失败模式语义一致；session.failed 的语义是 agent session 崩 / watchdog 超时，跟 action 主动判失败不是一回事。混用反而模糊 escalate.py 里 hard reason / canonical signals 的解析。
- **不补全部 in-flight state**：只加 ANALYZING / INTAKING 两条。其他 stage（spec_lint / dev_cross_check / staging_test / pr_ci / accept）的 action 现状不会主动 emit verify.escalate（fail 走 invoke_verifier_for_*_fail 转 REVIEW_RUNNING，由 verifier 决策）。先补真有 emit 路径的 state，避免噪声 transition。
- **不 reset runner / PVC**：escalate action 内部已处理 cleanup（escalated 时 retain_pvc=True 给人 debug）。本 fix 只让状态机能走通这条路。

## 兼容性

- 既有逻辑：`(REVIEW_RUNNING, VERIFY_ESCALATE)` / `(FIXER_RUNNING, VERIFY_ESCALATE)` / `(ESCALATED, VERIFY_ESCALATE)` 不动。
- ESCALATED 是 self-loop terminal：新加的 ANALYZING / INTAKING transition 把 state 推到 ESCALATED 后由 engine `_TERMINAL_STATES` cleanup 路径接管 —— 跟原 `(PR_CI_RUNNING, PR_CI_TIMEOUT) → ESCALATED + escalate` 完全相同形状。
- 老 REQ 无 migration：当前卡在 ANALYZING 的 REQ 等 watchdog 兜 SESSION_FAILED 推 ESCALATED；新 REQ 直接走新 transition。
