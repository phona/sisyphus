# Design: admin /resume — state-level resume from ESCALATED

## 状态机视角

ESCALATED 是"等人决策的中转站"，已有 3 条出口 transition：

```
ESCALATED + VERIFY_PASS       → REVIEW_RUNNING (apply_verify_pass)
                                  └→ apply_verify_pass 内 CAS 推到 stage_running
                                  └→ 链式 emit 该 stage 的 done/pass 事件
ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING (start_fixer)
                                  └→ start_fixer 起新 BKD fixer-agent，跑完 emit FIXER_DONE
ESCALATED + VERIFY_ESCALATE   → ESCALATED (None) [self-loop, no-op]
```

新 endpoint 不加 transition、不改状态机表，**仅作为派 Event 的入口**。

```
HTTP POST /admin/req/{id}/resume
   └→ ResumeBody.action 映射成 Event
       └→ engine.step(cur_state=ESCALATED, event=VERIFY_PASS|VERIFY_FIX_NEEDED)
           └→ existing transition + action handler
```

## 与 verifier follow-up 路径并行

```
                       ┌── BKD verifier issue follow-up
                       │     └─ wake agent → 写 decision JSON
                       │         └─ webhook → derive_event → engine.step
                       │             └─ same transitions
                       │
ESCALATED REQ ─────────┤
                       │
                       └── admin POST /resume
                             └─ engine.step(VERIFY_PASS|FIX_NEEDED) directly
```

两条路径殊途同归，命中同一组 transition + action handler。verifier 路径走得通时
（前提：有 verifier issue + agent 能跑）保留它给"agent 判"的能力；admin 路径是
"人已经判完，不必绕 agent"的快捷键。

## 路径冲突处理：rename runner-* prefix

v0.2 引入 admin K8s runner ops（`405193f` commit）时占了 `/pause` `/resume`，
没预留 state-level resume 的位置。本 REQ 加 `runner-` 前缀给 K8s 操作让出 `/resume`。

| 之前 | 现在 |
|---|---|
| `POST /pause`              | `POST /runner-pause`              |
| `POST /resume`             | `POST /runner-resume`             |
| `POST /rebuild-workspace`  | `POST /rebuild-workspace`（不变，已有 workspace 字样足够）|
| `GET  /runners`            | `GET  /runners`（不变）|
| —                          | `POST /resume`（**新**，state-level）|

`runner-` 前缀让 admin endpoint 自描述："runner-*"=K8s 资源、其它=state 操作。

`runner-rebuild-workspace` 没必要 rename（已有 `workspace` 名词，跟 state 没语义冲突）。

## ResumeBody schema 设计

```python
class ResumeBody(BaseModel):
    action: Literal["pass", "fix-needed"]   # 必传，无默认
    stage: str | None = None                # 覆盖 ctx.verifier_stage（pass 路由必需）
    fixer: Literal["dev", "spec"] | None = None  # 覆盖 ctx.verifier_fixer（fix-needed 路由）
    reason: str | None = None               # 写 ctx.resume_reason（审计）
```

为什么 action 不默认：让操作员的意图显式化（见 proposal "取舍"）。

为什么 stage 可选：典型场景是上一轮 verifier 已经把 verifier_stage 写进 ctx，
admin 不必重复传；但来自 pr_ci.timeout / accept-env-up.fail / intake.fail 这些
**非 verifier 路径**的 ESCALATED ctx 没 verifier_stage，必须 body 带。

为什么 fixer 可选：start_fixer 内部对 ctx.verifier_fixer 默认 "dev"。admin 想
强制走 spec fixer 才需要 body 带。

reason 完全可选：跟 `complete` 的 reason 同语义，仅审计用。

## ctx 预置时机

```
1. _verify_token
2. row = req_state.get
3. assert row.state == ESCALATED  # 否则 409
4. effective_stage = body.stage or row.context.get("verifier_stage")
5. if action == "pass" and not effective_stage:
       raise 400 "verifier_stage required for action=pass"
6. ctx_patch = {
       "resumed_by_admin": true,
       "resume_action": body.action,
   }
   if body.stage:    ctx_patch["verifier_stage"] = body.stage
   if body.fixer:    ctx_patch["verifier_fixer"] = body.fixer
   if body.reason:   ctx_patch["resume_reason"] = body.reason
7. await req_state.update_context(pool, req_id, ctx_patch)
8. row = req_state.get（重读拿到 patched ctx）
9. event = VERIFY_PASS if action=="pass" else VERIFY_FIX_NEEDED
10. fake_body = _FakeBody(req_id, project_id)
11. result = await engine.step(pool, body=fake_body, ..., event=event)
12. return {"action": "resumed", "from_state": "escalated",
            "event": event.value, "chained": result}
```

precondition_chain 顺序确保 401 ≠ 404 ≠ 409 ≠ 400 不互相覆盖。

## 错误情形矩阵

| 触发 | HTTP | body |
|---|---|---|
| 无 / 错 token | 401 | 同其它 admin endpoint |
| REQ 不存在 | 404 | `{"detail": "req <id> not found"}` |
| state != ESCALATED | 409 | `{"detail": "req <id> is in state <X>; expected escalated. Hint: POST /admin/req/<id>/escalate first to abort an in-flight REQ."}` |
| body.action ∉ {pass, fix-needed} | 422 | pydantic ValidationError |
| body.action="pass" + 缺 stage（ctx + body 都没）| 400 | `{"detail": "verifier_stage required for action=pass; provide body.stage or use BKD verifier follow-up"}` |
| 成功（推 pass）| 200 | `{"action": "resumed", "event": "verify.pass", ...}` |
| 成功（推 fix-needed）| 200 | `{"action": "resumed", "event": "verify.fix-needed", ...}` |
| engine.step CAS 竞争失败（并发 BKD verifier follow-up 同时推 pass）| 200 | `{"action": "resumed", "chained": {"action": "skip", ...}}`（透传）|

## 不变量

### 不污染 transition table

resume endpoint 不引入新 Event / 新 transition，所有路径都已经在
`state.py TRANSITIONS` 里。状态机看 `(ESCALATED, VERIFY_PASS) → REVIEW_RUNNING`
跟从 BKD verifier follow-up 来的 VERIFY_PASS 完全等价。

### 不绕过 stage_runs / verifier_decisions

走 engine.step 意味着：
- engine `_record_stage_transitions` 会处理 stage_runs（apply_verify_pass 的
  REVIEW_RUNNING → stage_running self-loop close 行为已写好）
- 跟 BKD verifier follow-up 路径不同，admin 路径**不写 verifier_decisions**
  （没真跑 verifier-agent，没 decision 可记）—— 这是预期行为，verifier_decisions
  表只反映"verifier 真做的判断"。审计靠 ctx.resumed_by_admin / resume_action
  / resume_reason 三个 ctx field 区分。

### 跟 force_escalate / complete 不打架

```
in-flight → force_escalate → ESCALATED → resume(pass)    → 主链推下一 stage
                                       → resume(fix-needed) → fixer agent
                                       → complete         → DONE
                                       → BKD verifier follow-up → 同 resume(pass/fix)
```

force_escalate 是 (any → escalated) 的入口，complete / resume 是 escalated → 出口。
三个 endpoint 的 state 前置条件互斥（complete 要求 escalated；resume 要求 escalated；
force_escalate 接受 any non-escalated），不会被误用打架。

## 实现细节：runner endpoint rename

仅改装饰器路径，函数名 `pause_runner` / `resume_runner` 不动（FastAPI 路径解析跟
Python 函数名解耦；测试也按函数名 import 不受影响）。

```python
# 之前
@admin.post("/req/{req_id}/pause")
async def pause_runner(...):

# 之后
@admin.post("/req/{req_id}/runner-pause")
async def pause_runner(...):
```

无 schema 改动 / 无 body 改动 / 无 response 改动 —— 纯路径迁移。

## 测试覆盖

7 个 case（tasks.md 详列），覆盖:
- 401 / 404 / 409 / 400（缺 stage）/ 422（pydantic）四档错误
- pass 成功（验证 emit VERIFY_PASS + ctx patch）
- fix-needed 成功（验证 emit VERIFY_FIX_NEEDED）
- 路径迁移（runner-pause / runner-resume 路径生效，旧路径 404）

mock 走跟 test_admin.py 既有 `_FakePool` / `_FakeRow` 同一套，不引新 fixture。
engine.step 整段 mock 掉，只验它被以正确参数调用一次（admin endpoint 责任仅"派 Event"，
engine 行为已被 test_engine.py / test_state.py 覆盖）。
