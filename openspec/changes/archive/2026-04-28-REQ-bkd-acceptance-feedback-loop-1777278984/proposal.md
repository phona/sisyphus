# REQ-bkd-acceptance-feedback-loop-1777278984: BKD acceptance feedback loop — 0 黑话纯原语 (Case 2)

## 问题

`docs/user-feedback-loop.md`（PR #162，2026-04-27）定义了 accept → review → fix → done
的用户反馈回路：sisyphus 跑完 accept stage 之后，**用户没机会判 PR 满不满足要求**就被
强行推到 `ARCHIVING → DONE`。

当前状态机里：

```
ACCEPT_TEARING_DOWN
  ↓ TEARDOWN_DONE_PASS  →  ARCHIVING (action=done_archive)
                                ↓ ARCHIVE_DONE
                              DONE
```

无论用户在 PR 评论里说 "looks great" 还是 "this is wrong, redo X"，sisyphus 都会
direct 进 archive，把 PR 合掉。**用户验收信号丢失**。

## 方案 (Case 2 — 0 黑话纯原语)

加一个 **PENDING_USER_REVIEW** state 把 `TEARDOWN_DONE_PASS → ARCHIVING` 的直通
关系切开。用户验收信号走 BKD intent issue 的 **statusId 原语**（不引入新 webhook
event 类型 / 不引入新 BKD agent role）：

| 原语 | 信号 |
|---|---|
| 用户把 BKD intent issue statusId 改成 `done` | "approve, ship it" → ARCHIVING |
| 用户把 statusId 改成 `review` 或 `blocked` | "needs changes / I'm not satisfied" → ESCALATED reason=user-requested-fix |

statusId 是 BKD 已有的字段，sisyphus 已经在多处读写它（`_push_upstream_status` /
`escalate.merge_tags_and_update`）—— **没引入任何新抽象**。webhook 收到 BKD 既有的
`issue.updated` 事件后判一下当前 REQ 在不在 PENDING_USER_REVIEW，是的话再读一下
issue 当前 statusId 派事件就完了。

### 流程图

```
ACCEPT_TEARING_DOWN
  ↓ TEARDOWN_DONE_PASS  → PENDING_USER_REVIEW (action=post_acceptance_report)
                                ↓
                      ┌─────────┼─────────┐
                      ↓         ↓         ↓
                USER_REVIEW   USER_REVIEW   (静默等)
                _PASS         _FIX          ↑ no timer，watchdog skip
                  ↓             ↓
              ARCHIVING      ESCALATED
              (done_archive) (escalate, reason=user-requested-fix)
                  ↓
                DONE
```

### 不在本 REQ 范围

- **GH `pull_request_review` webhook**（design doc Case 1）—— 后续单独 REQ 加 GH
  webhook handler 时再补；本 REQ 只做 BKD-native 闭环
- **artifact commit to `.acceptance/<REQ>/`** —— 设计文档列在 M0
  `REQ-acceptance-evidence-commit`，独立 REQ
- **comment 文本解析（"approve" 关键字 / 自由文本路由）** —— 0 黑话哲学要求纯原语，
  不解释自由文本；用户用 statusId 表态。后续 REQ 如确需自由文本路由，独立加
- **fixer 自动接力**（design doc §2.4 verifier-after-user-feedback）—— 当前仅
  ESCALATED 兜底；用户在 BKD escalated REQ 上 follow-up 走现有 admin/resume 路径
  即可（`docs/user-feedback-loop.md` §3 resume design 是另一个 REQ）

## 影响

### 状态机（state.py）

- 新枚举值 `ReqState.PENDING_USER_REVIEW = "pending-user-review"`
- 新 events `Event.USER_REVIEW_PASS = "user-review.pass"` / `Event.USER_REVIEW_FIX = "user-review.fix"`
- 改 `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)`：next_state 从 `ARCHIVING` 改 `PENDING_USER_REVIEW`，action 从 `done_archive` 改 `post_acceptance_report`
- 加 `(PENDING_USER_REVIEW, USER_REVIEW_PASS) → ARCHIVING action=done_archive`
- 加 `(PENDING_USER_REVIEW, USER_REVIEW_FIX) → ESCALATED action=escalate`
- 不加 `(PENDING_USER_REVIEW, SESSION_FAILED)` —— 该 state 没 BKD agent 在跑，不会收到 session 事件

### Webhook (webhook.py + router.py)

- router.py: derive_event 不动（统一入口）
- webhook.py: `derive_event` 返 None 且 `body.event=="issue.updated"` 时新增分支
  - 取 `req_state.get(req_id)`
  - 若 `cur_state == PENDING_USER_REVIEW` 且 `body.issueId == ctx.intent_issue_id`
    - 通过 `BKDClient.get_issue` 读 issue 当前 statusId（BKD `issue.updated` payload
      里 `changes` 字段格式无契约，直接 GET 拿 ground truth 最稳）
    - statusId == "done" → emit USER_REVIEW_PASS
    - statusId in {"review", "blocked"} → 设 ctx.escalated_reason="user-requested-fix" 后 emit USER_REVIEW_FIX
    - 其他 statusId（"working" / "todo" / 未知）→ skip（原语没语义，让用户重试）

### Action 新增 `post_acceptance_report`

- 输入：body / req_id / tags / ctx
- 行为：
  1. PATCH BKD intent issue body，追加 sisyphus-managed status block：
     ```
     <!-- sisyphus:acceptance-status -->
     ## sisyphus 验收结果（accept stage 已通过）
     - PR: <从 ctx.pr_urls 渲染>
     - 期待你做：把本 issue statusId 改 done = approve；改 review/blocked = 要返工
     ```
     用 `ensure_managed_block` 模式（marker + 替换/追加），**不动** statusId（让
     用户驱动 statusId 变更）
  2. 不创建任何 BKD sub-issue / 不起 BKD agent —— "0 黑话" 要求保持纯原语
- 副作用：写 `ctx.acceptance_reported_at`（ISO timestamp，给 dashboard）
- 幂等：`idempotent=True`（管理块用 marker 替换，重复跑只覆盖 block 不重复粘）

### Watchdog (watchdog.py)

- 把 `ReqState.PENDING_USER_REVIEW.value` 加进 `_SKIP_STATES` —— 等用户的状态不卡 timer
  （`docs/user-feedback-loop.md` §1 stage-type taxonomy: human-loop-conversation）

### 文档

- `docs/state-machine.md`：trans table 新增 3 条 + 改 1 条
- `docs/user-feedback-loop.md` §5 M0 落地路线图标注 Case 2 已落地（指向本 REQ）

## 不影响

- 现有 `done_archive` action 不动（仍是 ARCHIVING 入口的 action，只是触发 event 改了）
- `escalate` action 不动（reuse，按 ctx.escalated_reason 走）
- `teardown_accept_env` action 不动（仍 emit TEARDOWN_DONE_PASS，但下游路由改了）
- accept stage / pr-ci stage / fixer stage 行为完全不变

## 风险

1. **breaking 主链 happy path**：所有进 accept stage 的 REQ 走完后都会被卡在
   PENDING_USER_REVIEW 等用户表态。
   - 缓解：sisyphus 在 PENDING 入口写清"用户期望操作"到 intent issue body；
     watchdog 不杀 → 静默等可接受
   - 复用：dashboard 已有的 stuck-state alerting 会捞 PENDING_USER_REVIEW > X
     小时的 REQ（仪表盘 SQL 不需要新增）
2. **statusId 误判**：用户改 statusId 到 `working` / `todo` 是合法的 "我还在想"
   信号，sisyphus skip；但 BKD UI 偶尔有自动状态机会改 statusId，可能误触
   - 缓解：只识别 `done` / `review` / `blocked` 三个明确表态值，其他全 skip
3. **重复 webhook**：sisyphus PATCH intent issue body（post_acceptance_report）会
   触发自指 issue.updated；那次 statusId 没变化 → router 不 fire
   USER_REVIEW_*。CAS 也会兜底（重复 emit 同事件第二次 cas_failed skip）

## 测试

- 单元测试：transition 表 3 条新增 + 1 条改写
- 单元测试：webhook 在 PENDING_USER_REVIEW 收到 issue.updated 时按 statusId 派事件
- 单元测试：post_acceptance_report 写 status block + 不动 statusId
- 单元测试：watchdog skip PENDING_USER_REVIEW
- contract 测试：USER-S1..S5 端到端覆盖 happy / fix / skip / 双触发幂等 / no-issue-id
