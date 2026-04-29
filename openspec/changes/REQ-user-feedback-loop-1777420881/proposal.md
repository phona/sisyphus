# REQ-user-feedback-loop-1777420881: 用户反馈回路——PENDING_USER_PR_REVIEW + GH PR review webhook

## 问题

当前流水线到 accept teardown 后进入 PENDING_USER_REVIEW 状态，等用户在 BKD intent issue
上改 statusId 表态（done=approve / review=fix）。这导致：

- PR 被 review 人打了 reject → sisyphus 不知道，不会自动修复
- PR 被 review 人提了修改建议 → 需要人手动创建新 REQ
- 无法形成"accept → PR review → fix → 再 review"的闭环
- 用户在 GitHub 上 review PR，BKD 上无感知；两个系统割裂

## 方案

用 GitHub PR review webhook 替代 BKD statusId 作为 accept 后的用户反馈入口：

| 维度 | 旧（BKD statusId） | 新（GitHub PR review） |
|---|---|---|
| 触发源 | BKD intent issue statusId 变更 | GitHub `pull_request_review.submitted` / `pull_request_review_comment.created` |
| approved | `statusId=done` → USER_REVIEW_PASS → ARCHIVING | `review.state=approved` → GH_PR_REVIEW_APPROVED → ARCHIVING |
| changes_requested | `statusId=review` → USER_REVIEW_FIX → ESCALATED | `review.state=changes_requested` → GH_PR_REVIEW_CHANGES_REQUESTED → REVIEW_RUNNING → verifier → fixer |
| commented | 不支持 | `review.state=commented` → 内容 NLP 判（LGTM=pass / fix:=fix / 其他=escalate） |
| 状态名 | PENDING_USER_REVIEW | PENDING_USER_PR_REVIEW |

BKD statusId 路径保留但不作为新 REQ 的主流程（向后兼容）。

### 实现要点

1. **state.py**：
   - 新增 `PENDING_USER_PR_REVIEW` 状态
   - 新增 3 个 Event：`GH_PR_REVIEW_APPROVED` / `GH_PR_REVIEW_CHANGES_REQUESTED` / `GH_PR_REVIEW_COMMENTED`
   - TEARDOWN_DONE_PASS 目标改为 PENDING_USER_PR_REVIEW
   - PENDING_USER_PR_REVIEW 的 transitions：approved→ARCHIVING, changes_requested→REVIEW_RUNNING+verifier, commented→REVIEW_RUNNING+verifier
   - PR_MERGED 在 PENDING_USER_PR_REVIEW 也有效（兜底）

2. **webhook.py —— `/github-events` endpoint**：
   - HMAC-SHA256 签名验证（`github_webhook_secret` 配置）
   - 解析 `pull_request_review.submitted`：按 `review.state` 直接判
   - 解析 `pull_request_review_comment.created`：按 `comment.body` 内容判
   - NLP 轻量规则："LGTM"/"looks good" → approved；"fix:" → changes_requested
   - 从 PR branch (`feat/REQ-xxx`) 解析 REQ ID，fallback 查 req_state 表
   - 只有 `state == PENDING_USER_PR_REVIEW` 才处理
   - dedup：同一 review/comment 只处理一次

3. **actions/_verifier.py**：
   - 新增 `pr_review` stage
   - `_PASS_ROUTING`：pr_review → (ARCHIVING, ARCHIVE_DONE)（verifier 判 pass 后直接归档）
   - 3 个 action handler：`invoke_verifier_for_pr_review_fail/success/comment`

4. **verifier prompts**：
   - `pr_review_success.md.j2`：approved 时做最终确认（夹带私货 / 藏条件）
   - `pr_review_fail.md.j2`：changes_requested/commented 时判 fix/escalate

5. **配套更新**：
   - `engine.py` — STATE_TO_STAGE 加入 pending_user_pr_review
   - `watchdog.py` — 加入 _SKIP_STATES / _NO_WATCHDOG_STATES（human-in-loop 不杀）
   - `admin.py` — PR_MERGED_VALID_STATES 加入 PENDING_USER_PR_REVIEW
   - `config.py` — 新增 `github_webhook_secret`
   - `post_acceptance_report.py` — 更新文案说明 GitHub PR review 拍板方式

6. **测试**：
   - 更新 `test_contract_user_acceptance_gate.py`（适应新 TEARDOWN_DONE_PASS 目标）
   - 新增 `test_contract_pr_review_feedback_loop.py`（PR-WH-S1~S10）

### 与现有系统的关系

```
accept pass → teardown_accept_env → PENDING_USER_PR_REVIEW
  ↓ 等 GitHub PR review
  ├─ approved → ARCHIVING → done
  ├─ changes_requested → REVIEW_RUNNING → verifier → fixer → 再推 PR → 再 review
  ├─ commented (LGTM) → ARCHIVING
  ├─ commented (fix:) → REVIEW_RUNNING → fixer
  └─ PR merged (兜底) → ARCHIVING
```

PR review 闭环跟 BKD statusId 闭环并存：新 REQ 走 PR review 路径，旧 REQ（如果还有卡在
PENDING_USER_REVIEW 的）仍可由 statusId 驱动。

## 取舍

- **为什么 approved 不走 verifier 而是直接 archive** —— approved 是最短路径，verifier
  只是确认性检查。保留 `invoke_verifier_for_pr_review_success` action 供 manual/admin 调用。
- **为什么 comment 也进 verifier 而不是直接 escalate** —— 很多 review 的 comment 是
  "LGTM with nits" 或 "approve but please fix X"，需要 verifier 做语义判断。
- **为什么不替换 PENDING_USER_REVIEW 而是新增 PENDING_USER_PR_REVIEW** —— 向后兼容。
  旧 REQ 如果还在 PENDING_USER_REVIEW，statusId 驱动仍然有效。
- **为什么 GitHub webhook 不走 `/bkd-events`** —— payload 格式完全不同（BKD webhook vs
  GitHub webhook），独立 endpoint 更干净。签名验证机制也不同（Bearer vs HMAC-SHA256）。

## 影响面

- 改 `orchestrator/src/orchestrator/state.py`：新增状态 + 事件 + transitions
- 改 `orchestrator/src/orchestrator/webhook.py`：新增 `/github-events` endpoint
- 改 `orchestrator/src/orchestrator/actions/_verifier.py`：新增 pr_review stage + handlers
- 改 `orchestrator/src/orchestrator/engine.py`、`watchdog.py`、`admin.py`、`config.py`：配套注册
- 改 `orchestrator/src/orchestrator/actions/post_acceptance_report.py`：文案更新
- 新增 prompts：`pr_review_success.md.j2`、`pr_review_fail.md.j2`
- 新增/更新测试文件
- 不动数据库 migrations（无 schema 变更）
