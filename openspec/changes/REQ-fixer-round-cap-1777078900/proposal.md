# REQ-fixer-round-cap-1777078900: feat(engine+watchdog): hard cap fixer rounds at N (default 5)

## 问题

verifier-agent decision=fix → `start_fixer` 起 fixer-agent 跑一轮 → 跑完回 `invoke_verifier_after_fix` 复查 → 新 verifier 又判 fix → 再起一轮 fixer …… 这条 verifier↔fixer 子链没有任何机制性出口。

实证场景：spec 自相矛盾、fixer-agent 改不动根因、scope 描述含糊导致 fixer 总是改错位置 —— sisyphus 会无限制起 fixer 烧 BKD session quota / GHCR rate / human attention。

`actions/_verifier.py:start_fixer` 已经渲染了 `round_n=ctx.get("fixer_round", 1)` 给 bugfix prompt，但**没有任何代码增加 `ctx.fixer_round`**，这条字段恒为 1，prompt 显示的 round 也永远是 1。

## 根因

- 计数从未实现：start_fixer 读 ctx.fixer_round 但不写。
- 没有 cap 阈值与逃生事件 —— 状态机里 FIXER_RUNNING 唯一出口是 FIXER_DONE → REVIEW_RUNNING（继续验），无 escalate 出口。
- escalate 路径自 #50 引入 auto-resume 后，watchdog 兜底场景下若 reason 被解读为 `watchdog-stuck`/`session-failed` 就会 BKD follow-up "continue"，无意中把已超 cap 的 fixer 又续上去。

## 方案

### 计数 + 硬 cap（engine 路径）

`actions/_verifier.py:start_fixer` 在创建新 fixer issue 之前：

1. 读 `current = int(ctx.get("fixer_round") or 0)`
2. 算 `next_round = current + 1`
3. 若 `next_round > settings.fixer_round_cap`（默认 5）：
   - `req_state.update_context(escalated_reason="fixer-round-cap", fixer_round_cap_hit=cap)`
   - 不创 fixer issue，return `{"emit": "verify.escalate", "reason": "fixer-round-cap", "fixer_round": current, "cap": cap}`
4. 否则：用 `next_round` 创 issue（带 `round:N` tag、bugfix prompt 渲 `ROUND=N`），跑完 `req_state.update_context(fixer_round=next_round, ...)`

### 状态机：补 FIXER_RUNNING → ESCALATED transition

新增 `(ReqState.FIXER_RUNNING, Event.VERIFY_ESCALATE) → Transition(ESCALATED, "escalate", "fixer round 触顶 / start_fixer 自判 escalate")`。

start_fixer chained-emit `verify.escalate` 后由 engine 复用既有 `escalate` action 收口（reason、tag、runner cleanup、CAS 推 ESCALATED 全在 escalate 内做）。

### config: fixer_round_cap 新 setting

`Settings` 新增 `fixer_round_cap: int = 5`（环境变量 `SISYPHUS_FIXER_ROUND_CAP`）。运维可通过 helm values 覆盖：调高给 fixer 更多机会，调低更早叫人。

### escalate.py: fixer-round-cap 是 hard reason

新增 `_HARD_REASONS = {"fixer-round-cap"}`：

- reason 解析：`ctx.escalated_reason ∈ _HARD_REASONS` 时**不被 `body.event ∈ _CANONICAL_SIGNALS` 覆盖** —— 否则 watchdog.stuck 事件会把已设的 fixer-round-cap 重写为 watchdog-stuck（误归 transient → auto-resume → 续上去 → 死循环回归）
- `_is_transient`：`reason ∈ _HARD_REASONS` 永远返回 False，跳过 auto-resume 直接真 escalate

### watchdog.py: defense in depth

`_check_and_escalate` 在 emit SESSION_FAILED 之前，若 `state == FIXER_RUNNING and ctx.fixer_round >= settings.fixer_round_cap`，写 `escalated_reason="fixer-round-cap"` 到 ctx。

正常路径下 start_fixer 自检 cap 已早一步 escalate；本 hook 兜 start_fixer 写 ctx 后 emit 失败 / 中途崩溃留下的孤儿 FIXER_RUNNING（30 min 后被 watchdog 扫到），保证最终 reason 仍是 fixer-round-cap、不被 auto-resume。

## 取舍

- **counter 含义**：`ctx.fixer_round` 表示"已成功起过的 fixer 轮数"。第一次 start_fixer 写 1，第 N 次写 N。第 N+1 次（`next_round > cap`）被拒。cap=5 = "允许 5 轮"，第 6 次起 escalate。
- **不做 per-stage cap**：不区分 spec_lint / staging_test / pr_ci 等 stage 的 fixer round 各自计数 —— 同一 REQ 全程共享一个 round 计数。理由：跨 stage 反复修往往是同根因，分开计数反而模糊问题信号。
- **不引新 BKD agent**：不开 escalate-helper agent 或 fixer-coordinator，全靠状态机 + 既有 escalate action。
- **不重置 round 计数**：用户 follow-up escalated REQ 续上来时不会自动清零 fixer_round。理由：如果用户判断"还有救"想继续修，应该自己 PATCH ctx 或把 cap 调高 —— 否则等于绕过 cap 没意义。
- **watchdog 不缩短阈值**：fixer round 超 cap 不立刻 escalate（要等 30 min 卡死阈值）。理由：cap 检查在 start_fixer 入口已硬挡，watchdog 是兜底；若入口正常工作，永远走不到 watchdog 路径。

## 兼容性

- 既有 REQ：ctx 没 fixer_round 字段 → 视为 0，第一次 start_fixer 写 1，正常推进。无 migration。
- 老的 `(REVIEW_RUNNING, VERIFY_ESCALATE) → ESCALATED + escalate` transition 不动；新增 `(FIXER_RUNNING, VERIFY_ESCALATE) → ESCALATED + escalate` 是叠加，老 verifier→escalate 路径走原来的。
- bugfix.md.j2 渲染参数 `round_n` 老的恒为 1，新值 1..N 单调递增；模板不需要改。
