# REQ-bkd-intent-statusid-sync-1777280751: feat(state): auto-sync BKD intent issue statusId on REQ terminal transition

## Why

REQ 推进到终态（DONE / ESCALATED）时，sisyphus 现在不主动 PATCH BKD intent
issue 的 `statusId`。intent issue 是用户最初下单的那张 BKD kanban 卡，是 dashboard
／ 看板上人能直接看到的工件。结果：

- happy-path 收尾：archive 完成、REQ 进 DONE → 状态机推完，但 BKD 看板上 intent
  卡仍卡在 "working" 列，不会自动挪进 "完成"。用户得手动拖。
- escalate 收尾：retry 用完 / verifier 判 escalate / pr-ci timeout → REQ 进
  ESCALATED，sisyphus 加 `escalated` tag、可能开 GH incident，但 intent 卡在
  BKD 上还是 "working"——人不打开看板细看就漏看故障。

唯一例外是 `_apply_pr_merged_done_override` 路径（REQ-archive-state-cleanup）
里已经 explicit 写 `status_id="done"` 给 intent issue PATCH。这条路径走对了，
但它只覆盖"PR 在 escalate 触发瞬间已合"这一窄场景；正常 happy-path 收尾 + 真
escalate 都还没接上。

`_push_upstream_status`（webhook.py）已经把"刚发 session.completed 的 BKD
issue"自动推到 done / review，**但那 PATCH 的是 agent 自己的 issue**（analyze /
verifier / archive 的 BKD issue），不是 intent issue。"agent issue 进 done"和
"intent 卡进 done"是两件事。

## What Changes

加一道**统一的"REQ 进终态 → BKD intent statusId 跟上"机制**，覆盖所有进
DONE / ESCALATED 的状态机路径。机制集中放在 `intent_status` helper 模块，
两个调用点：`engine.step` 终态分支 + `actions/escalate.py` 的内部 CAS 路径。

### 状态映射

| ReqState | BKD statusId | 看板列 | 备注 |
|---|---|---|---|
| `DONE` | `"done"` | "完成" | happy-path 归档 / pr-merged-override 终点 |
| `ESCALATED` | `"review"` | "待审查" | 与 `webhook._push_upstream_status` 对 verifier-escalate 的处理对齐——故障要送到人眼前 |

`"review"` 而非"escalated/blocked"是因为 BKD 项目本身没"escalated" status 列；
"review" 是已有的"等人看一下"标签位，与现有 verifier-escalate 的 BKD UI 行为一致
（webhook.py 同样把判 escalate 的 verifier issue 推到 "review"）。

### 行为契约

```
engine.step CAS to terminal_state (cur != terminal):
  ↓
  1. await intent_status.patch_terminal_status(...)  ← 同步等 PATCH 落，避免与下游 race
  2. fire-and-forget cleanup_runner（保留原 fire-and-forget 模式，cleanup 慢）
  3. dispatch action handler（如果 transition 有 action）

actions/escalate.py SESSION_FAILED self-loop inner CAS to ESCALATED:
  ↓
  patch BKD intent statusId="review"（在 cleanup_runner 旁，best-effort）
```

`_apply_pr_merged_done_override` 已自带 `merge_tags_and_update(... status_id="done")`——
保留现状不重构（一次 PATCH 同时 add tags + status 更紧凑）。它的 status="done"
比 engine 终态 hook 的 status="review"（如果上游入口是非 SESSION_FAILED 路径）晚
执行，最终 BKD 落到 "done"，符合预期。

### 失败语义

- 所有 PATCH 都 best-effort：BKD 不可达 / 5xx → `log.warning` 后吞掉，不阻塞状态机
- engine.step 终态分支选 **await** 而非 fire-and-forget，是为了消除 race：override 路径
  会在 engine hook 之后再 PATCH 一次，await 保证顺序"engine hook 先 → override 后"，
  最终态总是 "done"。BKD PATCH 是单次 localhost HTTP，时延 ~30ms，await 不影响吞吐。
- 不引入 retry：`_push_upstream_status` 也是单次试错，BKD 漏 tag 不会致 REQ 卡住

### 不动的部分

- 不动 state.py / 不加新 ReqState / 不加新 Event：纯 side effect，跟 transition 表无关
- 不动 watchdog / runner_gc：终态转移触发的瞬时 hook，跟周期 GC 解耦
- 不动 webhook `_push_upstream_status`：那是 BKD-issue-level 收尾，跟 intent issue
  这条独立。`_push_upstream_status` 把 archive agent issue 推 done，跟本 hook 把
  intent issue 推 done 是平行的两件事
- `_apply_pr_merged_done_override` 现有 `status_id="done"` 调用保留——它对 PR-merged
  路径的语义已经齐全，不重构

### 与现有路径并存

| 触发 | 现行行为 | 加入本 hook 后 |
|---|---|---|
| ARCHIVING + ARCHIVE_DONE → DONE | engine 终态 hook：cleanup_runner | + intent statusId="done" |
| 任意 → ESCALATED （非 SESSION_FAILED self-loop） | engine 终态 hook：cleanup_runner | + intent statusId="review" |
| SESSION_FAILED 触发 escalate inner CAS → ESCALATED | escalate 内部：cleanup_runner(retain_pvc=True) | + intent statusId="review" |
| escalate inner CAS → DONE（pr-merged-override） | merge_tags_and_update + status_id="done" + cleanup_runner(retain_pvc=False) | 不变（已正确） |

## Tradeoffs

- **为什么 await 而非 fire-and-forget**：override 路径会在 engine hook 之后再 PATCH
  一次，fire-and-forget 让两次 PATCH 顺序不定，可能"done → review"覆盖错。await
  让 engine hook 先落地，override 后写一次，最终态正确。BKD PATCH ~30ms 不构成
  瓶颈（webhook handler 已经在做多次异步 I/O）。
- **为什么 ESCALATED → review 而非新建一个 BKD status**：BKD 项目没 status 自定义
  端点（实测 `/api/projects/{id}/statuses` 返回 404），只能用看板原生的 todo /
  working / review / done 四列。"review" 与 verifier-escalate 当前行为一致，且语义
  本就是"等人看"，跟 ESCALATED "需要人介入"对齐。"done" 不行——会让人误以为已
  完成；"working" 不行——本来就在 working，没变化等于没标。
- **为什么集中在 helper + 两调用点而非分散到每个 escalate 子路径**：终态 hook 是
  cross-cutting concern，集中放好维护，未来加新终态语义（比如 PAUSED）只需改 helper
  + 加新映射。每个 action 自己 PATCH 容易漏。
- **为什么不读 BKD 当前 statusId 决定要不要 PATCH**：PATCH 同样 statusId 是 no-op，
  BKD REST 接受幂等 PATCH。多读一次 GET 反而增加失败面。
- **为什么不动 `_apply_pr_merged_done_override`**：它的 `merge_tags_and_update(...
  status_id="done")` 在一次 PATCH 里同时改 tags + status，比拆成两个 PATCH 更紧
  凑。重构纯属代码洁癖，无功能收益。
- **为什么不写 webhook 钩子兜底（"如果 engine 漏 patch，session.completed 时补上"）**：
  状态机入终态唯一两条路径都已覆盖（engine.step + escalate inner CAS），不存在
  "REQ 进了终态但没经过 hook"的情况；多兜一道增加调用面、更难审计。

## Impact

### 改动文件

- 新增 `orchestrator/src/orchestrator/intent_status.py`：
  - `STATE_TO_STATUS_ID: dict[ReqState, str]`（两条：DONE→done、ESCALATED→review）
  - `status_id_for(state) -> str | None`
  - `async def patch_terminal_status(*, project_id, intent_issue_id, terminal_state, source)`：
    best-effort PATCH，BKD 异常吞掉只 warning
- 改 `orchestrator/src/orchestrator/engine.py`：
  - 终态分支已有 cleanup_runner fire-and-forget；并列加 `await intent_status.patch_terminal_status(...)`
  - 从 `ctx` 读 `intent_issue_id`（已有字段，webhook.py init 时填）
- 改 `orchestrator/src/orchestrator/actions/escalate.py`：
  - SESSION_FAILED inner CAS to ESCALATED 后，补 `await intent_status.patch_terminal_status(...)`
  - `_apply_pr_merged_done_override` 不动

### 测试

- 新增 `orchestrator/tests/test_intent_status.py`：unit test 覆盖 helper 行为
  - `BIS-S1` DONE → status_id_for == "done"
  - `BIS-S2` ESCALATED → status_id_for == "review"
  - `BIS-S3` 非终态 state（INTAKING / ANALYZING / 等）→ status_id_for == None
  - `BIS-S4` patch_terminal_status DONE 调用 BKD update_issue with status_id="done"
  - `BIS-S5` patch_terminal_status ESCALATED 调用 BKD update_issue with status_id="review"
  - `BIS-S6` intent_issue_id 为空 → 直接返 False，不调 BKD
  - `BIS-S7` 非终态 state → 直接返 False，不调 BKD
  - `BIS-S8` BKD 抛异常 → log warning，不 reraise，返回 True（PATCH 已尝试）
- 改 `orchestrator/tests/test_engine.py`：在 `test_terminal_done_triggers_cleanup_no_retain` /
  `test_terminal_escalated_triggers_cleanup_retain_pvc` 同模式添加 `BIS-S9` /
  `BIS-S10` 断言 `intent_status.patch_terminal_status` 被调用 + 参数正确
- 改 `orchestrator/tests/test_contract_escalate_pr_merged_override.py` 或新增
  `test_contract_intent_status_sync.py`：覆盖
  - `BIS-S11` 真 escalate 路径（非 PR-merged shortcut，SESSION_FAILED 用尽 retry）→
    intent_status.patch_terminal_status 调用一次，statusId="review"
  - `BIS-S12` PR-merged-override 路径不依赖本 hook（它自带 status_id="done"）

### 不动

- 不动 `state.py`、`webhook.py`（`_push_upstream_status` 单独管 agent issue）
- 不动 Postgres migrations / observability schema
- 不动 BKD tag schema / router 匹配规则
