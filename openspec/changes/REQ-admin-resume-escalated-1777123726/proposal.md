# REQ-admin-resume-escalated-1777123726: feat(admin): /req/{id}/resume endpoint to unblock business REQ stuck on verifier escalate

## Why

ESCALATED 不是死终态，但目前唯一的"续命"路径要绕 BKD verifier-agent（5–10s
启动 + 重判 + 解 decision JSON），对操作员已确认是 infra flake / 已知误判的场景
是浪费。`pr_ci.timeout` / `accept-env-up.fail` / `intake.fail` 这些**非 verifier
路径**进 ESCALATED 的 REQ 甚至没 verifier issue 给人 follow-up，只能 raw `/emit`
event 名手动派——绕审计、易出错。需要一条显式、参数受限、走合法 transition
的 admin 出口，跟 `/escalate`（入口）`/complete`（作废出口）配齐。

## What Changes

- **新加** `POST /admin/req/{req_id}/resume`：state-level resume from ESCALATED。
  body `{action: pass|fix-needed, stage?, fixer?, reason?}` → 派 VERIFY_PASS
  / VERIFY_FIX_NEEDED Event 走 engine.step；复用现有 `apply_verify_pass`
  / `start_fixer` action handler。**不引入新 Event / 新 transition**。
- **重命名** v0.2 K8s runner endpoint:
  `/admin/req/{id}/pause` → `/admin/req/{id}/runner-pause`,
  `/admin/req/{id}/resume` → `/admin/req/{id}/runner-resume`。
  让出 `/resume` 给新 state-level endpoint，行为不变，无 caller 影响。
- **审计字段**：成功 resume 时把 `resumed_by_admin=true` / `resume_action`
  / `resume_reason` 落 `req_state.context`，区别 BKD verifier 路径
  （后者写 `verifier_decisions` 表）。

## 问题

ESCALATED 不是死终态——`state.py` 已经写了三条 transition 让人续命：

```python
# state.py L204-212
(ReqState.ESCALATED, Event.VERIFY_PASS):
    Transition(ReqState.ESCALATED, "apply_verify_pass", ...)
(ReqState.ESCALATED, Event.VERIFY_FIX_NEEDED):
    Transition(ReqState.FIXER_RUNNING, "start_fixer", ...)
(ReqState.ESCALATED, Event.VERIFY_ESCALATE):
    Transition(ReqState.ESCALATED, None, "...")
```

设计的"标准续命路径"是：用户在 BKD UI 给那条 escalate 的 verifier issue 写一条
follow-up，BKD wake 同一个 verifier-agent 重写 decision JSON，session.completed
→ webhook 解 decision → 派对应 Event → 命中上面的 transition。

**但这条路径每次都要走 BKD verifier-agent**：

- 起 BKD session（5–10s 启动 + 重读 prompt 上下文）
- 让 agent 重判一遍（一次 prompt request + 模型推理 chain，吃 quota）
- 解 decision JSON 派 Event

**有些场景操作员已经知道答案**，跑 verifier 是浪费：

- verifier 因为 `_HARD_REASONS={"fixer-round-cap"}` 强制 escalate，但操作员看了
  确认 staging-test fail 是 GHA 网络抖动（基础设施 flaky），想直接 pass
- pr_ci.timeout 走的 transition 直接进 ESCALATED（无 verifier 决策路径，没法续）：
  `(PR_CI_RUNNING, PR_CI_TIMEOUT) → ESCALATED, "escalate"`
- accept-env-up.fail 同样直接 ESCALATED：
  `(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED, "escalate"`
- intake.fail 直接 ESCALATED：`(INTAKING, INTAKE_FAIL) → ESCALATED, "escalate"`
- session.failed retry 用完 → ESCALATED；操作员排查后觉得 retry 一次能过

这些场景下没有 verifier issue 给人 follow-up（pr_ci.timeout / accept-env-up.fail
是机械路径），现状只能：

1. **`/admin/req/{id}/emit` + body `{"event": "verify.pass"}`**——能用，但 emit
   是底层调试 API，要操作员熟 Event 枚举名 + 知道 ESCALATED → VERIFY_PASS 的内部
   transition 含义；操作员误打 `verify.escalate` 还会把 REQ 又自循环回 ESCALATED
2. **手动伪造 verifier issue + decision JSON**——更复杂 + 污染 verifier_decisions 表
3. **直接 `psql` 改 `req_state.state`**——绕审计、不带 stage 路由信息、apply_verify_pass
   依赖的 ctx.verifier_stage 也不会被 set

缺一条**显式、有审计、参数受限**的"resume from escalated" admin path。

## 方案

加 `POST /admin/req/{req_id}/resume`，把"决定下一步走 pass / fix"的语义化操作
封装成一条 endpoint，复用现有 ESCALATED → VERIFY_PASS / VERIFY_FIX_NEEDED transition。

**路径冲突解决**：现有 `/admin/req/{id}/pause` 和 `/resume` 是 v0.2 的 K8s runner
pod 操作（删 pod / 重建 pod，不改 state），命名跟新的 state-level resume 撞车。
本 REQ 同步把这两条 runner 操作 endpoint 重命名：

| 旧路径 | 新路径 | 行为 |
|---|---|---|
| `/admin/req/{id}/pause` | `/admin/req/{id}/runner-pause` | 删 Pod 保 PVC（不变） |
| `/admin/req/{id}/resume` | `/admin/req/{id}/runner-resume` | 重建 Pod（不变） |
| —（新） | `/admin/req/{id}/resume` | state-level resume from ESCALATED |

runner pause/resume 是 v0.2 引入的 admin 工具（V0.2-PLAN.md L27），目前只有人工 ops
通过 Bearer token 用，无 CI / 自动化 caller（grep 全仓只有 docs 引用）；rename 影响面
仅限 docs + 模块 docstring。

### 行为契约

```
POST /admin/req/{req_id}/resume
Authorization: Bearer <webhook_token>
Body (required):
{
  "action": "pass" | "fix-needed",
  "stage": "<verifier_stage>" | null,    # optional, 覆盖 ctx.verifier_stage
  "fixer": "dev" | "spec" | null,        # optional, 覆盖 ctx.verifier_fixer (action=fix-needed)
  "reason": "<audit string>" | null      # optional, 写到 ctx.resume_reason
}

→ 200  {"action": "resumed", "from_state": "escalated", "event": "verify.pass", "chained": {...}}
→ 200  {"action": "resumed", "from_state": "escalated", "event": "verify.fix-needed", ...}
→ 400  body.action invalid / "pass" 缺 stage（无 ctx.verifier_stage 兜底）
→ 404  REQ not found
→ 409  state != ESCALATED
→ 401  bad / missing token
```

### 实现要点

1. **复用现有 transition**：
   - `action="pass"` → emit `Event.VERIFY_PASS` → `apply_verify_pass` 已支持
     `src in (REVIEW_RUNNING, ESCALATED)` 的 CAS（`_verifier.py` L171-177）
   - `action="fix-needed"` → emit `Event.VERIFY_FIX_NEEDED` → `(ESCALATED, VERIFY_FIX_NEEDED)
     → FIXER_RUNNING, "start_fixer"` 主链已写

2. **ctx 预置**：
   - `ctx.verifier_stage` 由 `apply_verify_pass` / `start_fixer` 读用于路由；本 admin
     调用的 ctx 可能：
     - 仍含上一轮 verifier 写入的 verifier_stage / verifier_fixer / verifier_scope（典型）
     - 或来自非 verifier 路径的 escalate（pr_ci.timeout / accept-env-up.fail / intake.fail），
       ctx 没 verifier_stage（apply_verify_pass 会返 `unknown verifier_stage` 走死路）
   - body.stage / body.fixer 覆盖 ctx，给操作员显式指定路由的能力
   - body.reason 写 `ctx.resume_reason`；同时打 `ctx.resumed_by_admin=true`

3. **走 engine.step 而非裸 SQL**：跟 `/escalate` `/complete` 不同，本 endpoint 走
   合法 transition，所以走 `engine.step` 派 Event，让 `apply_verify_pass` /
   `start_fixer` 跑（包括它们内部的 ensure_runner、stage_runs.close 等副作用）。
   `_FakeBody`（已有，emit endpoint 复用）当 webhook body 喂 engine。

4. **action="pass" 缺 stage 早 fail**：apply_verify_pass 拿不到 stage 会 emit
   VERIFY_ESCALATE 把 REQ 又推回 ESCALATED，操作员回看是个静默"什么都没发生"——
   admin endpoint 在 dispatch 前 validate ctx.verifier_stage（含 body 覆盖后），
   缺则 400，给清晰错误。

5. **审计**：history 行通过 cas_transition 走 engine.step 自动写
   （from=escalated, to=review-running / fixer-running, event=verify.pass / verify.fix-needed,
   action=apply_verify_pass / start_fixer）—— 不需要手动追加。
   额外把 admin 标记落 ctx：`{"resumed_by_admin": true, "resume_action": "pass", "resume_reason": "..."}`。

### 与现有 endpoints 的对比

| endpoint | from_state | to_state | 走 transition? | 用例 |
|---|---|---|---|---|
| `force_escalate` | * | escalated | 否（裸 SQL） | 卡死 / 进度永远推不动 |
| `complete` | escalated | done | 否（裸 SQL） | escalated 但确认作废 |
| `resume`（新）| escalated | review-running / fixer-running | **是** | escalated 但操作员决定推 pass / fix |
| `runner-pause` | (state 不动) | (state 不动) | 否（K8s only）| 临时让出资源 |
| `runner-resume` | (state 不动) | (state 不动) | 否（K8s only）| 配 runner-pause |

**resume 跟 complete 互不替代**——complete 是"宣告作废清资源"，resume 是"给一次
机会推下一 stage"。两个 endpoint 配合 force_escalate 形成完整的 escalated 出口集：

```
in-flight REQ
   ↓ force_escalate（卡死）
ESCALATED ──→ resume(pass)         → REVIEW_RUNNING → 主链推 stage（apply_verify_pass）
          ──→ resume(fix-needed)   → FIXER_RUNNING（start_fixer 起 fixer agent）
          ──→ complete             → DONE（清 PVC）
          ──→ BKD verifier follow-up（保留路径，不替代）
```

## 取舍

- **为什么不做 `action="retry"`（重跑失败 stage）**——sisyphus 哲学是"escalate 后
  flaky / infra 抖动直接归人决策"（CLAUDE.md "失败先验，再试错"），retry 这种
  机械重试已经被 M14c 砍掉过。如果操作员要重跑 staging-test，应当用 `/emit`
  body `{"event": "staging-test.fail"}` 这种 raw API；本 endpoint 只暴露
  verifier 决策路径（pass / fix-needed），跟 verifier-agent 的语义对齐。

- **为什么 body 必传 action 不默认 "pass"**——操作员用 admin 路径绕过 verifier
  这件事本身要求 explicit。默认 pass 等于鼓励"我也不知道但先 pass 试试"——
  这种语义应当走原本的 BKD verifier follow-up 让 agent 判，而不是 admin 兜底。

- **为什么 rename runner-pause/resume 而不是给新 endpoint 取另一个名（unblock /
  reactivate）**——`/resume` 在 escalate / complete / emit 已建立的"state 操作"
  动词集合里语义最自然（"恢复执行"）。runner-pause/resume 当时 v0.2 命名漏想了
  state /resume 的需求；现在补上 runner- 前缀，跟 runner ops 的 `runner-rebuild-workspace`
  风格也更一致（rebuild-workspace 就是 runner 概念，没 state 含义）。

  Rename 的 cost：admin docstring + V0.2-PLAN.md + sisyphus-integration.md 三处
  docs；无脚本 / CI 调用方（grep 验证：`/admin/req/.*/(pause|resume)` 仅 4 处
  匹配，全是文档）。Sisyphus pre-1.0，不发版本号兼容标签。

- **为什么不在 resume 后加 `cleanup_runner` 反向操作**——apply_verify_pass 内部
  已经会 `ensure_runner(req_id, wait_ready=True)`（_verifier.py L207），会把
  escalate 时清的 pod 拉回来；start_fixer 通过原 transition 路径走的 BKD
  fixer-agent 拉的也是同 pod。不需要 admin endpoint 自己管 runner。

- **为什么 400 缺 verifier_stage 而不是 200 noop**——apply_verify_pass 缺 stage
  的死路径（emit VERIFY_ESCALATE 自循环回 ESCALATED）对调用方完全静默，admin
  会看到 200 带 chained={escalated} 回到原状态，操作员 confused。早 400 + 提示
  "provide body.stage" 比让它走完一遍 transition 安全。

- **为什么不做 `confirm: true` 二次确认**——admin endpoints 全靠 Bearer token
  做权限隔离，已经是 ops-only 操作；force_escalate / complete 没二次确认本 endpoint
  也跟齐。

## 影响面

### 改动文件

- `orchestrator/src/orchestrator/admin.py`：
  - 加 `ResumeBody(BaseModel)` pydantic schema
  - 加 `@admin.post("/req/{req_id}/resume")` async def `resume_req`（新 state-level）
  - 重命名 `@admin.post("/req/{req_id}/pause")` → `/runner-pause`
  - 重命名 `@admin.post("/req/{req_id}/resume")` （runner，旧）→ `/runner-resume`
  - 模块 docstring 更新（runner ops 段落 + 新 endpoint 列表）
- `orchestrator/tests/test_admin.py`：5 个新 case 见 tasks.md
- `orchestrator/docs/V0.2-PLAN.md`：admin 工具表更新 path
- `orchestrator/docs/sisyphus-integration.md`：pause 引用更新 path

### 不动

- `state.py` / TRANSITIONS（已有 ESCALATED → VERIFY_PASS / VERIFY_FIX_NEEDED 三条 transition）
- `engine.py` / `actions/_verifier.py`（apply_verify_pass / start_fixer 现状已支持 ESCALATED CAS source）
- `escalate.py`（_HARD_REASONS / auto-resume 逻辑独立）
- Postgres migrations（无新表）
- BKD 集成层 / runner_gc / k8s_runner（runner 操作 endpoint 仅 path 改名，handler 函数不动）
