# Design: BKD acceptance feedback loop (Case 2 — 0 黑话纯原语)

> 实现 [docs/user-feedback-loop.md](../../../docs/user-feedback-loop.md) §2 描述的
> PENDING_USER_REVIEW 状态，但**只走 BKD-native 原语**（没有 GH `pull_request_review`
> webhook、没有自由文本评论解析、没有专属 user-review BKD agent）。

## "0 黑话纯原语" 约束的具体含义

设计 doc 在 §2.3 列了 7 类事件源（GH PR review state / GH PR comment / GH PR closed /
BKD comment.created / BKD issue.updated / BKD session.failed），每个都需要新 webhook
handler 或新抽象。**Case 2 的特征是只用 BKD 已有的 `issue.updated` + `statusId`**：

| 设计 doc 抽象 | Case 2 的纯原语映射 |
|---|---|
| `pull_request_review` event | ❌ 不接 GH webhook |
| `pull_request_review_comment` queue | ❌ 不解释自由文本 |
| BKD `comment.created` | ❌ BKD ≥0.0.65 没该 event；不引入 |
| BKD `issue.updated` statusId | ✅ **唯一信号通道** |
| 专属 user-review BKD agent | ❌ 不起新 agent role |
| `ctx.user_pr_review_comments[]` queue | ❌ 不维护 comment 队列 |

**用户期望的操作是改 BKD issue statusId**：

- statusId → `done` = "我满意了，发车"
- statusId → `review` 或 `blocked` = "我不满意，要返工"
- 其他 statusId 修改 = sisyphus 不识别（`working` / `todo` 等是 BKD 自己也会改的
  状态，不当作用户表态）

## 状态机增量

### 1 个新 state

```python
class ReqState(StrEnum):
    ...
    PENDING_USER_REVIEW = "pending-user-review"
    ...
```

**watchdog policy**：`human-loop-conversation`（同 INTAKING） —— 不 timer，仅
事件驱动出口。落地：`watchdog._SKIP_STATES` 加这一项。

### 2 个新 events

```python
class Event(StrEnum):
    ...
    USER_REVIEW_PASS = "user-review.pass"   # statusId → done
    USER_REVIEW_FIX = "user-review.fix"     # statusId → review/blocked
    ...
```

### 3 条新 + 1 条改的 transition

```python
# 改：原本是 (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING via done_archive
(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS):
    Transition(ReqState.PENDING_USER_REVIEW, "post_acceptance_report",
               "teardown 完 → 等用户 BKD intent issue 表态"),

# 新：用户 approve
(ReqState.PENDING_USER_REVIEW, Event.USER_REVIEW_PASS):
    Transition(ReqState.ARCHIVING, "done_archive",
               "用户把 BKD intent statusId 改 done → 发车归档"),

# 新：用户 reject / want fix
(ReqState.PENDING_USER_REVIEW, Event.USER_REVIEW_FIX):
    Transition(ReqState.ESCALATED, "escalate",
               "用户把 BKD intent statusId 改 review/blocked → 标 user-requested-fix 入 escalated"),
```

为什么 fix 路径走 ESCALATED 而不是 FIXER_RUNNING：

- "0 黑话" 要求不引入新 agent role / 不解析自由文本 → 没有"用户具体想 fix 啥"的
  context 喂给 fixer-agent
- design doc §2.4 的 fixer 接力路径假设 `ctx.user_pr_review_comments[]` 队列存在；
  Case 2 不维护该队列
- ESCALATED + reason=user-requested-fix → 用户在 BKD escalated REQ 上 follow-up
  描述具体要改啥 → 走 [resume design](../../../docs/user-feedback-loop.md#3-interrupt--resume-设计)
  路径（**该 resume 流程是另一个 REQ**，本 REQ 不实现 resume，只把信号正确
  打到 ESCALATED）

## Webhook 改动

router.py **不动**（保持"标签 → 事件名"翻译职责单一；statusId 是 issue 字段
不是 tag，不归 router 翻）。

webhook.py 在 `derive_event` 返 None 且 `body.event == "issue.updated"` 时新增分支：

```python
# 现有逻辑：derive_event(body.event, tags) -> Event | None
event = router_lib.derive_event(body.event, tags)

# 新增：fallback 看 PENDING_USER_REVIEW
if event is None and body.event == "issue.updated":
    row = await req_state.get(pool, req_id)
    if row and row.state == ReqState.PENDING_USER_REVIEW:
        intent_issue_id = (row.context or {}).get("intent_issue_id")
        if intent_issue_id and intent_issue_id == body.issueId:
            async with BKDClient(...) as bkd:
                issue = await bkd.get_issue(body.projectId, body.issueId)
            new_status = (issue.status_id or "").lower()
            if new_status == "done":
                event = Event.USER_REVIEW_PASS
            elif new_status in {"review", "blocked"}:
                event = Event.USER_REVIEW_FIX
                # 让 escalate.py 拿正确 reason（避免 fall back 到 "issue-updated"）
                await req_state.update_context(
                    pool, req_id, {"escalated_reason": "user-requested-fix"},
                )
```

> 为什么读 issue 当前 statusId 而不是 `body.changes.statusId`：BKD ≥0.0.65 webhook
> payload 的 `changes` 字段没有契约保证，shape 可能是 `{"statusId": "done"}` 也
> 可能是 `{"statusId": {"old": "...", "new": "..."}}`；GET issue 拿当前快照是
> 唯一稳定的 source of truth。

## post_acceptance_report action

文件：`orchestrator/src/orchestrator/actions/post_acceptance_report.py`

```python
@register("post_acceptance_report", idempotent=True)
async def post_acceptance_report(*, body, req_id, tags, ctx):
    """accept teardown 通过后：把验收报告 PATCH 进 BKD intent issue body。

    不动 statusId（用户驱动）；不开新 BKD issue / agent；只改 body 的 sisyphus-managed
    block。idempotent：用 HTML marker 标识 block，重复跑覆盖不重复粘。
    """
```

行为：

1. 取 `intent_issue_id = ctx.intent_issue_id` —— 缺则 noop + log warning
2. 渲染 status block（用 Jinja2 模板 `acceptance_status_block.md.j2`）：

   ```markdown
   <!-- sisyphus:acceptance-status -->
   ## sisyphus 验收已通过 — 等你拍板

   - PR: {{ pr_urls 渲染成列表 }}
   - 状态：所有 acceptance scenarios 通过

   ### 拍板方式（改本 BKD issue 的 statusId 即可）

   - **`done`**: approve，sisyphus 立即合 PR 归档
   - **`review`** / **`blocked`**: 不满意，sisyphus 进 escalated 等你 follow-up
     描述具体要改啥
   - 其他 statusId（`working` / `todo` 等）: sisyphus 继续等

   sisyphus 不解释你写在 chat 里的自由文本 —— statusId 是唯一信号。
   ```

3. PATCH `BKD /issues/{intent_issue_id}` 只 update `description`（**不传 tags 也
   不传 statusId**，避免 tag 替换语义抹历史 tag + 不抢用户 statusId 决定权）
4. 写 `ctx.acceptance_reported_at = utcnow().isoformat()`
5. 不 emit 事件（state 已经 CAS 进 PENDING_USER_REVIEW；下一个事件由用户驱动）

幂等性：用 marker `<!-- sisyphus:acceptance-status -->` 包裹 block；重复跑用
`merge_managed_block(existing_body, marker, new_block)` 替换原 block 而非追加。

## Watchdog 改动

watchdog.py：

```python
_SKIP_STATES = {
    ReqState.DONE.value,
    ReqState.ESCALATED.value,
    ReqState.GH_INCIDENT_OPEN.value,
    ReqState.INIT.value,
    ReqState.INTAKING.value,            # 既有：human-loop
    ReqState.PENDING_USER_REVIEW.value, # 新增：human-loop
}
```

PENDING_USER_REVIEW 没有 BKD agent 在跑，没 session 可查，watchdog 强行 escalate
没意义；用户能等多久等多久。如未来需要 staleness 报警，单独跑 dashboard SQL 即可。

## STATE_TO_STAGE 映射 (engine.py)

PENDING_USER_REVIEW **不**加进 STATE_TO_STAGE —— 它不是 stage（没 timing 语义、没
agent invocation），不该写 stage_runs 行。

## 测试矩阵

### Unit (state.py)

| ID | 输入 | 期望 |
|---|---|---|
| USR-T1 | `decide(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` | `next=PENDING_USER_REVIEW`, action=`post_acceptance_report` |
| USR-T2 | `decide(PENDING_USER_REVIEW, USER_REVIEW_PASS)` | `next=ARCHIVING`, action=`done_archive` |
| USR-T3 | `decide(PENDING_USER_REVIEW, USER_REVIEW_FIX)` | `next=ESCALATED`, action=`escalate` |
| USR-T4 | `decide(PENDING_USER_REVIEW, ARCHIVE_DONE)` | `None`（非法 transition） |

### Unit (webhook + BKD client mock)

| ID | 场景 | 期望 |
|---|---|---|
| USR-W1 | PENDING_USER_REVIEW + intent issue.updated + issue.status_id="done" | emit USER_REVIEW_PASS → CAS to ARCHIVING |
| USR-W2 | PENDING_USER_REVIEW + intent issue.updated + issue.status_id="review" | emit USER_REVIEW_FIX + ctx.escalated_reason="user-requested-fix" → CAS to ESCALATED |
| USR-W3 | PENDING_USER_REVIEW + intent issue.updated + issue.status_id="blocked" | 同 USR-W2 |
| USR-W4 | PENDING_USER_REVIEW + intent issue.updated + issue.status_id="working" | skip（其他 statusId 不识别） |
| USR-W5 | PENDING_USER_REVIEW + sub-issue id ≠ intent_issue_id 的 issue.updated | skip（只看 intent issue） |
| USR-W6 | ANALYZING（非 PENDING）+ intent issue.updated + statusId=done | skip（state 错） |

### Unit (post_acceptance_report)

| ID | 场景 | 期望 |
|---|---|---|
| USR-A1 | ctx 含 intent_issue_id + pr_urls | BKD update_issue called with description containing marker block + pr links + 不传 status_id / tags |
| USR-A2 | ctx 缺 intent_issue_id | noop + log warning + return {} |
| USR-A3 | 二次跑（intent body 已含 block） | 替换原 block 不追加（marker 唯一） |

### Unit (watchdog)

| ID | 场景 | 期望 |
|---|---|---|
| USR-WD1 | scan 发现 state=PENDING_USER_REVIEW 的 row | skip（不调 BKD session 检查） |

### Contract (test_contract_user_acceptance_gate.py)

USER-S1..S6 = 上面 unit 测试矩阵的合并版，按 spec.md 的 Scenario 引用走。
