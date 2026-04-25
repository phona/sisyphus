# REQ-verifier-stagerun-close-1777105576: fix(verifier): close orphan verifier stage_run on VERIFY_PASS path

## 问题

verifier-agent 决策 `pass` 走主链 `apply_verify_pass` 时，对应的 `stage_runs` 行 `verifier`
**永远不被 close**：`ended_at IS NULL` 一直挂着。指标看板（M14e Q12/Q13 verifier 决策吞吐 /
平均时长）按 `ended_at` group 都漏算 PASS 路径，verifier 的 `pass` outcome 在 `stage_runs`
上完全不可见，只能从 `verifier_decisions` 倒推。久了 `stage_runs` 表上 stage='verifier' 行
有大量 orphan，运维 `SELECT count(*) WHERE stage='verifier' AND ended_at IS NULL` 看着像
卡死。

## 根因

`engine._record_stage_transitions` 用 transition 表声明的 `cur_state → next_state` 来决定
要不要 close 上一阶段 + open 下一阶段。同 state（`cur == next`）就 early-return 不动 stage_run。

`(REVIEW_RUNNING, VERIFY_PASS)` 在 `state.py` 里就声明成自循环：

```python
(ReqState.REVIEW_RUNNING, Event.VERIFY_PASS):
    Transition(ReqState.REVIEW_RUNNING, "apply_verify_pass", ...)
```

所以 engine.step CAS 完 + 跑 `_record_stage_transitions` 时直接 early-return，verifier 的
stage_run 不被关。

外面看 self-loop 是因为 next_state 由 `ctx.verifier_stage` 动态决定（不同 stage 路向不同
target），transition 表没法静态写。`apply_verify_pass` action 内部手工 `cas_transition`
把 state 推到 target stage_running，再链式 emit 该 stage 的 done/pass 事件。这条手工 CAS
**绕过 `_record_stage_transitions`**，所以 verifier 那条 run 没人收。

对照其他 verifier 决策路径：

- `VERIFY_FIX_NEEDED` → `REVIEW_RUNNING → FIXER_RUNNING`（不同 state）→ `_record_stage_transitions`
  正常 close verifier（outcome=`fix`）+ open fixer。✓
- `VERIFY_ESCALATE` → `REVIEW_RUNNING → ESCALATED`（不同 state）→ 正常 close verifier（outcome=`escalate`）。✓

只有 PASS 是自循环 → 漏 close。

## 方案

`engine._record_stage_transitions` 在 `cur_state == next_state` early-return **之前**先做一次
精准 close：当 `cur_state == REVIEW_RUNNING and event == Event.VERIFY_PASS` 时，调
`stage_runs.close_latest_stage_run(pool, req_id, "verifier", outcome="pass")` 把当前 verifier
那条 run 收掉。

不需要 open 新的 stage_run：apply_verify_pass 内部 CAS 到 target stage（如 `SPEC_LINT_RUNNING`）
是为了"verifier 判 pass 等同于该 stage 已通过"，不是真重跑该 stage。链式 emit 紧接着推到下
一 stage（如 `CHALLENGER_RUNNING`），那一步会通过 `_record_stage_transitions` 正常 open
challenger 的 run。

代码改 1 处（engine.py），新增 1 个分支 + 必要 try/except 包裹（best-effort，跟同函数其他写
入对齐，失败只 log 不抛）。

## 取舍

- **不改 transition 表**：把 `(REVIEW_RUNNING, VERIFY_PASS)` 改成 `→ X_RUNNING` 的"显式" target
  做不到 —— next_state 由 ctx 动态决定，transition 表是静态映射。继续保 self-loop + action
  内部手工 CAS 是最小侵入。
- **只 close、不 open**：apply_verify_pass 的"绕过 stage_running"语义是有意为之（verifier
  代该 stage 出 PASS 决策），不应该再为该 stage 凭空开一条 stage_run。
- **outcome 写死 `pass`**：到达 `(REVIEW_RUNNING, VERIFY_PASS)` 这一条分支必然是 verifier
  的 PASS 决策，没必要再走 `_EVENT_TO_OUTCOME` 查表（虽然查也是 `pass`）。
- **不动 ESCALATED + VERIFY_PASS**：用户续 escalated verifier issue 写新 decision=pass 时
  cur_state 是 `ESCALATED`（不是 `REVIEW_RUNNING`），且彼时 verifier stage_run 已经在前一次
  `REVIEW_RUNNING → ESCALATED` 的 close 路径里收掉了。新 fix 不需要覆盖这条。
- **不补历史 orphan**：不写 migration 回填已有 `ended_at IS NULL` 的老 verifier 行 ——
  那是观测污染，不影响主流程；运维 ad-hoc SQL 自己 backfill 即可。

## 兼容性

- 行为对齐：上线后 verifier `pass` 路径会正常 close stage_run，Metabase 看板自动正确。
- 既有 orphan：留着不补；前文已说不写 migration。
- 数据库 schema：不动。
- `verifier_decisions` 表：不动。
