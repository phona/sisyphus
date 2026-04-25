# REQ-admin-complete-endpoint-1777117709: feat(admin): add /req/<id>/complete endpoint to clean up stale escalated REQ

## 问题

ESCALATED 不是死终态——人在 BKD UI 给 verifier issue follow-up 还能续起来
（`state.py` 的 ESCALATED + VERIFY_PASS / VERIFY_FIX_NEEDED 三条 transition）。
为此 `runner_gc` 给 ESCALATED 留 `pvc_retain_on_escalate_days` 的保留期：

```python
# runner_gc.py L52-58
if state == "escalated" and not ignore_retention:
    updated_at = r["updated_at"]
    if updated_at and (now - updated_at) < retention:
        active.add(r["req_id"])
```

但**有些 escalated 不会续**：

- 误开的 REQ（intent issue 写错被 user 直接放弃）
- 上游需求被砍 / 被合并到别的 REQ
- 老仓的 escalate（仓退役了，没人想再翻 PVC debug）
- 流程跑到一半发现是 spike / 实验性 REQ

这些 stale escalated 占的资源：

- runner Pod 已经被 `_cleanup_runner_on_terminal(retain_pvc=True)` 清掉，**Pod 是 0**
- 但 PVC 整段 retention 期都在挂磁盘（默认 1 天，prod 调到 7 天的 cluster 就更长）
- `req_state` 行也一直在表里"working set"，metrics dashboard / `failure_mode` view 都
  把它们当现役 escalate 计入 reason 频次（污染 Top-N）

人现在能做的：

1. **等 retention 过**——磁盘压力 / dashboard 污染 retention 期内挡不住
2. **手动调 `runner_gc.gc_once(ignore_retention=True)`**——但那是磁盘紧急疏散开关，
   会把所有 escalated 全清，**不能精确单点**
3. **直接 `psql` 改 `req_state.state='done'`**——能跑但绕过审计、不触发 runner cleanup、
   操作员需要 DB 凭证

缺一条**精确、有审计、立即触发 runner cleanup** 的 admin path。

## 方案

加 `POST /admin/req/{req_id}/complete`，给 admin 一条明确"我看了，这个不会续，
按 done 处理"的快捷键。

### 行为契约

```
POST /admin/req/{req_id}/complete
Authorization: Bearer <webhook_token>
Body (optional): {"reason": "字符串，可选"}

→ 200  {"action": "completed", "from_state": "escalated", "reason": "..."}
→ 200  {"action": "noop", "state": "already done"}        # 幂等
→ 404  REQ not found
→ 409  REQ 不在 ESCALATED（防误用：in-flight REQ 想 force done 应当先 escalate
        再 complete，两步走，避免一个 endpoint 把任意 stage 截断）
```

### 实现要点

1. **state 转移**：直接 SQL `UPDATE req_state SET state='done', context = context || $2`
   走（mirror 现有 `force_escalate` 的反向）。**不**走 `engine.step` / state machine
   transition——admin override 不进 transition table，避免污染合法 transition 集合。
2. **context 写入** `{"completed_reason": "admin", "completed_from_state": "escalated", ...}`
   保留审计痕迹；如果 body 有 `reason` 字段也写进 ctx。
3. **runner cleanup** 立即触发：fire-and-forget 调
   `engine._cleanup_runner_on_terminal(req_id, ReqState.DONE)`，**不等 runner_gc 下一轮**。
   这一步关键——光改 DB 不清 PVC 等于没省资源。注意 `retain_pvc=False`（done
   语义就是不留）。
4. **history 行**：CAS transition 那条 SQL 已经追加 history，但我们走直接 UPDATE 跳过了
   `cas_transition` helper。**手工补一条** history entry（from=escalated, to=done,
   event=admin.complete, action=null）以便 `req_summary` view 看得到这次操作。
5. **拒绝 non-ESCALATED**：`state != ReqState.ESCALATED` → HTTPException(409)。设计取舍：
   "complete 任意 state" 听起来更通用，但实际只在 escalated 后才有意义；放开 in-flight
   REQ 的 complete 入口会让 admin 误把 ANALYZING 直接 done 而 runner 还在跑 prompt。
   想要"任意 state → terminal"的人应当先 `escalate` 再 `complete`，两步操作意图明确。

### 与现有 endpoints 的对比

| endpoint | from_state | to_state | retain PVC | 主用例 |
|---|---|---|---|---|
| `force_escalate` | * | escalated | yes | 卡死 / 进度永远推不动 |
| `complete` | escalated | done | no | 已经 escalated 但确认不会续，立即收资源 |
| `pause` | * | (state 不动) | yes | 临时让出资源，准备 resume |

**complete 不替代 escalate**——escalate 是"踢进死信队列"，complete 是"把死信队列里
确认作废的最终清理"。配合 `force_escalate` 形成"escalate（保 PVC 等人）→ 人决定
→ resume（VERIFY_PASS / VERIFY_FIX_NEEDED）或 complete（done + 清 PVC）"两叉。

## 取舍

- **为什么不直接砍 ESCALATED PVC retention** —— 保留期是给"人在 BKD UI 给 verifier
  issue 续 follow-up"的窗口（`state.py` 那 3 条 ESCALATED → ... transition 用得着
  workspace + git 状态）。砍掉 retention 等于砍掉续命路径。本 REQ 留 retention，加
  显式"放弃续"按钮。
- **为什么 409 不是 200 noop** —— 用 409 比 noop 更醒目；admin 误调 complete 的非
  escalated REQ 应该看到错误，不该被静默忽略。但 already-done 是 200 noop（真·幂等）。
- **为什么不接 BKD issue 标记** —— complete 只动 sisyphus state + runner 资源；BKD
  issue 该不该归档是另一件事（`done_archive` agent 负责真·归档）。admin complete
  是"宣告 REQ 不会续"，不是"REQ 完成了交付"——两者语义不一样。BKD intent issue
  的状态由 admin 自己在 BKD UI 标，sisyphus 不代劳。
- **为什么 fire-and-forget 而非 await 清完** —— mirror `engine._cleanup_runner_on_terminal`
  用法：`asyncio.create_task` 让 endpoint 立即返回；cleanup 失败 runner_gc 兜底
  （已经有日志 + retry 机制）。HTTP 调用方拿到 200 时表示"DB 改完了 + cleanup 已
  排队"，不是"PVC 已删"。
- **为什么 reason body 是 optional** —— 80% 调用是 stale 清扫批量做，写不出有意义的
  reason；强制要求会让操作员胡填或者塞一坨重复的 "stale"。可选字段对真有 context
  时（"REQ-X 被 REQ-Y 取代"）有用。

## 影响面

- 改 `orchestrator/src/orchestrator/admin.py`：加 `complete_req` endpoint + `CompleteBody`
  pydantic model + 模块 docstring 增一行。
- 改 `orchestrator/src/orchestrator/engine.py`：把 `_cleanup_runner_on_terminal`
  从 module-private（前缀 `_`）改成 module-level 可导入（保持 `_` 名但 admin 直接
  import；Python 没真"private"，admin 跨模块用是可接受的——或重命名去掉 `_`）。
  **选用**：不改名，admin.py 里 `from .engine import _cleanup_runner_on_terminal`，
  显式注释这是受管 cross-module import。
- 测试：`orchestrator/tests/test_admin.py` 加 4 个 case：
  - `test_complete_404_when_not_found`
  - `test_complete_noop_when_already_done`
  - `test_complete_409_when_not_escalated`（验证 in-flight 状态被拒）
  - `test_complete_marks_done_and_triggers_cleanup`（验证 SQL UPDATE + cleanup task 起）
- 不动 `state.py` / `engine.py` transition table / Postgres migrations.
- 不动 BKD 集成层 / runner_gc（DB 层 done state 已经不在 keep set，gc 会顺手再扫一遍
  作为兜底）。
