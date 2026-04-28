# REQ-bkd-hitl-end-to-end-loop-1777273753: BKD-only HITL end-to-end loop + intent statusId sync

## Why

sisyphus 的 HITL（Human-In-The-Loop）UX 全部走 BKD 看板：用户在 intent issue
上聊 intake、看 escalate follow-up、确认 done 收尾 —— 不需要回 GitHub 翻 PR 评论。
这条"BKD-only"的设计依赖**两件事必须扎实**：

1. **watchdog 不能把人在思考的 intake 当卡死杀掉**
   —— 已由 `REQ-watchdog-stage-policy-1777269909` (PR #161) 解决：
   `_NO_WATCHDOG_STATES = {INTAKING}` 让 SQL 预滤直接 skip INTAKING 行，
   永远不发 SESSION_FAILED → escalate 误判。本 REQ 只**引用**该工作，不重复实现。

2. **REQ 终态必须把 BKD intent issue 的 `statusId` 推到正确列**
   —— 否则用户看到的画面：sisyphus 已经 `state='done'`，但 BKD 看板 intent
   issue 还停在 `working`/`todo`，看板"完成"列空空，"待审查"列堆满已经处理过的
   REQ。用户不知道哪条要看、哪条已收尾。

第 2 件目前**不闭环**。现有 statusId PATCH 只在三个零散点：

- `actions/escalate.py:198-204` PR-merged shortcut → intent statusId="done" ✅
- `webhook.py:_push_upstream_status` 在 session.completed 时推**当前 issue**（不是
  intent issue）的 statusId；verifier-decision=escalate 推 verifier issue 到
  "review"，其他推 "done" ✅（但跟 intent issue 不是一回事）
- 主链 `ARCHIVING + ARCHIVE_DONE → DONE` 这条 happy-path 终态：**没有**任何代码
  去 PATCH intent issue 的 statusId ❌
- `escalate.py` SESSION_FAILED self-loop CAS 推到 ESCALATED：**没有**去 PATCH
  intent issue 的 statusId ❌
- 通过 state.py transition 表声明 `next_state=ESCALATED` 进入终态的路径
  （INTAKE_FAIL / ACCEPT_ENV_UP_FAIL / PR_CI_TIMEOUT / VERIFY_ESCALATE 等）：
  engine.step 做 cas_transition 但**没有**去 PATCH intent issue 的 statusId ❌

结果：BKD 看板上"完成 / 待审查"列长期跟 sisyphus `req_state.state` 失同步，HITL
loop 闭不上。本 REQ 在终态 transition 处补一道 PATCH。

## What Changes

在 `engine.py` 的 terminal-state CAS 成功点（即把 cleanup_runner 那段）旁边，
加一道**幂等**的 BKD intent issue statusId PATCH：

| sisyphus terminal state | BKD intent statusId |
|---|---|
| `DONE` | `done` |
| `ESCALATED` | `review` |

`escalate.py` 内部的 SESSION_FAILED self-loop CAS（line ~437）也要补同一道 PATCH，
因为这条路径 engine.step 表面看是 self-loop，外层 CAS 没动到 terminal，cleanup
+ statusId sync 都得 escalate 自己手动调。

`ESCALATED → review` 选用 "review" 而非 "done" 跟 `webhook._push_upstream_status`
的 verifier-escalate 分支（line 246）保持一致：BKD 看板"待审查"列只剩用户能
follow-up 续作业的 issue（resume 路径）。

### 行为契约（高层）

```
任何 transition into terminal (cur_state != terminal, next_state in {DONE, ESCALATED})
  ↓ engine.step CAS 成功
  ↓ schedule cleanup_runner (existing)
  ↓ ★ schedule sync_intent_status_on_terminal(project_id, intent_issue_id, terminal_state)
  ↓
escalate.py SESSION_FAILED self-loop 内部 CAS to ESCALATED（外层 transition 是 self-loop）
  ↓ CAS advanced
  ↓ cleanup_runner (existing)
  ↓ ★ sync_intent_status_on_terminal(... ESCALATED)

PR-merged shortcut（escalate.py:_apply_pr_merged_done_override）
  ↓ 已经直接 merge_tags_and_update(... status_id="done") (existing — 保留不动)
```

★ = 本 REQ 新增。helper 是幂等的（PATCH 就是替换语义；多次调用不副作用），调用失败
log warning 不阻塞状态机。

## Tradeoffs

- **为什么不在 webhook 层 push intent issue 的 statusId** —— webhook 拿到的是**当前**
  完成 session 的 issue（archive issue / verifier issue / accept issue 等），不是
  intent issue。`_push_upstream_status` 的语义是"把刚结束的 issue 推目标 statusId"，
  跟"REQ 终态把 intent issue 推目标 statusId"是两件事。混在一处会打架（archive
  session.completed → archive issue 推 done，但同一刻 intent issue 也该推 done，
  两边职责一掺，回头排查 bug 要溯两条路径）。
- **为什么 ESCALATED → review 而非 done** —— ESCALATED 不是真完工，是"sisyphus
  无法自动推进了，等人决定"。BKD 看板用 "review" 列做"人 inbox"，跟 verifier
  escalate 的 statusId 推送对齐。如果用 done，看板"完成"列污染，"待审查"列空，
  人无从知道哪些 REQ 等他看。
- **为什么 helper 放 engine.py 而非 actions 层** —— 这是终态 side-effect 跟
  cleanup_runner 同质（"transition into terminal 触发的世界状态同步"）。让它跟
  cleanup 共享一个调用点，避免日后加新终态 side-effect 时多个动作分散在不同地方。
- **为什么不引入新事件 / 新 transition** —— 现有 transition 表足够。本 REQ 是
  side-effect 的补全，不是状态机变更。
- **为什么不写专门 PATCH 的 retry / queue** —— BKD localhost REST 5xx 极罕见，
  有 `webhook._push_upstream_status` 的同模式 best-effort 失败兜底（log warning）
  做参考。下游 dashboard / 人看到 statusId 落后一拍可以人工 PATCH 修。引入 retry
  queue = 多一个故障面。
- **为什么不在主链 transition 中拆 INTAKING bypass watchdog 也合并到本 REQ** ——
  watchdog 改动有自己独立的 spec / 测试 / PR 审查（PR #161 538 lines），合并会
  撑大 PR 范围 + 跨 reviewer。"3 件合一"的"合"是**逻辑合一**（同一个 HITL UX 闭环
  支柱），不是**代码 PR 合一**。本 REQ 的 PR 把它当**前置依赖**引用，不重复实现。

## Impact

- 改 `orchestrator/src/orchestrator/engine.py`：
  - 加 `_TERMINAL_STATE_TO_BKD_STATUS_ID: dict[ReqState, str]` 常量
  - 加 `_sync_intent_status_on_terminal(project_id, intent_issue_id, terminal_state) -> None` helper
  - 在 step() terminal-cleanup 块（line ~206）旁边 schedule 它
- 改 `orchestrator/src/orchestrator/actions/escalate.py`：
  - 在 SESSION_FAILED self-loop 的 cleanup_runner 块（line ~441）旁边调 helper
- 改 `docs/architecture.md`：在已有"角色 / stage 流"段落后追加"§HITL end-to-end
  loop"，按本提案描述用户 / sisyphus / BKD intent issue statusId 三方在每个
  stage 的同步关系
- 测试 `orchestrator/tests/test_intent_status_sync.py` (新文件)：
  - HITL-S1 transition into DONE 触发 intent issue statusId="done" PATCH
  - HITL-S2 transition into ESCALATED 触发 intent issue statusId="review" PATCH
  - HITL-S3 escalate SESSION_FAILED self-loop CAS 触发 intent issue statusId="review"
  - HITL-S4 BKD PATCH 失败仅 log warning, 不阻塞 state.transition / cleanup
  - HITL-S5 PR-merged shortcut 已自带 statusId="done"，不重复 sync（避免 double PATCH）
  - HITL-S6 self-loop transition (cur==next, 非进 terminal) 不触发 sync
- 不动 `state.py`、postgres migrations、`watchdog.py`、`router.py`
- 不改 BKD `update_issue` 签名（已支持 `status_id` 字段）
- 不引入新 BKD tag

## 跟 PR #161 (REQ-watchdog-stage-policy-1777269909) 的关系

PR #161 解决 HITL loop 入口侧问题（watchdog 不杀正在跟用户聊的 intake），本 REQ
解决 HITL loop 出口侧问题（终态把 intent issue 推到对的 BKD 列）。两条 PR 独立
合并，无代码冲突。本 PR 不需要 rebase 等 PR #161，可以并行 review。

合并后的 BKD-only HITL 完整 loop:

```
人 创 BKD intent issue (intent:intake / intent:analyze)
  ↓
INTAKING — watchdog skip (PR #161)，人多轮聊澄清 → 打 result:pass tag
  ↓ INTAKE_PASS
ANALYZING / SPEC_LINT / DEV_CROSS_CHECK / STAGING_TEST / PR_CI / ACCEPT / ARCHIVING
  ↓ 主链推进；fail 走 verifier 3 路（pass/fix/escalate）
  ↓
DONE → BKD intent statusId='done'（本 REQ ★）
ESCALATED → BKD intent statusId='review'（本 REQ ★）
  ↓
人在 BKD 看板看到 intent issue 自动落到对应列；
"待审查"列每个 issue 都是真要人看的；
"完成"列每个都是 sisyphus 自动收的尾。

HITL 闭环。
```
