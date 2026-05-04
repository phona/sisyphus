# Proposal: verifier resume webhook fix v2

Refs #380.

## 背景

REQ #808 (test-router-decision-contract) 5/4 03:00 卡 ESCALATED 不前进。
verifier issue #818 收 follow-up resume 后正确 emit `decision:pass`（含合规
` ```json ... ``` ` 块在 last assistant message），BKD `session.completed`
event 触发 webhook —— 但 orchestrator 没消费 transition，REQ 留在
ESCALATED。

事故时间线（详 #380）：

1. 第一次 verifier session: emit `decision:escalate` → BKD fires
   `session.completed` → orch dedup `new` → engine.step → REQ 进 ESCALATED。
2. 用户在 verifier issue chat 续 follow-up（resume 路径）→ BKD 起新 session →
   verifier 重判 `decision:pass`。
3. BKD 第二次 fires `session.completed`。期望：orch 解析 decision JSON →
   emit VERIFY_PASS → REQ 离开 ESCALATED 推进下一 stage。
4. 实际：orch 没消费。事后用户 PATCH BKD issue statusId 强 trigger
   `issue.updated` 也无效（verifier 决策解析仅在 `body.event ==
   "session.completed"` 跑）。

根因猜测（#380 列三条）：

1. **dedup 误命中**：现 dedup key 是 `issueId|event_type|executionId`。BKD
   resume 起新 session 时若复用 executionId（具体行为需 BKD 实测确认），
   第二次 `session.completed` 会撞 dedup 被 skip。
2. **state machine bug**：transition 走了但 action 静默 fail。
3. **webhook 投递**：BKD resume 不重发 `session.completed`。

#380 给出三条修法候选：

- A. resume 路径强制 bypass dedup（is_resume=True）
- B. 显式 admin "retrigger verifier decision" endpoint，人能手戳
- C. dedup key 加 timestamp 区分 resume 的新 execution

## 范围（只动 `phona/sisyphus`）

1. **`orchestrator/src/orchestrator/webhook.py`**：
   - 候选 A 的安全实现：仅当 `session.completed` + `verifier` tag + dedup
     `skip` + 当前 REQ state == `REVIEW_RUNNING` 三条同时成立时 bypass dedup
     重新 process。
     - state==REVIEW_RUNNING 是关键 guard：决策一旦消费 state 立即转出
       REVIEW_RUNNING（VERIFY_PASS / VERIFY_FIX_NEEDED / VERIFY_ESCALATE 三
       transition 都跳出 REVIEW_RUNNING），所以 stale 重发不会触发 bypass。
     - 这避开候选 C（timestamp 入 dedup key）的 redelivery 反向 escalate
       hazard（webhook.py:213-218 注释提到 REQ-final7 实测过）。
   - dedup 命中 / 未命中都 emit 含 `executionId` 的 obs event，让事故再发
     时能从 Metabase 直接定位是哪条 path 走丢。
2. **`orchestrator/src/orchestrator/admin.py`**：
   - 新增 `POST /admin/req/{req_id}/retrigger-verifier`，body
     `{"issue_id": "<bkd_issue_id>"}`（非空必填）。处理 flow：
     1. 抓 BKD issue 的 last assistant message。
     2. 复用 `router.derive_verifier_event_with_retry_info` 解 decision JSON。
     3. 若解出有效 event（VERIFY_PASS / VERIFY_FIX_NEEDED / VERIFY_ESCALATE），
        喂 `engine.step` 走合法 transition。
     4. 落 `verifier_decisions`（best-effort）。
   - 跟 `/admin/req/{req_id}/resume` 区别：那个是 state-level 强推
     event（不读 BKD chat），retrigger-verifier 是 webhook 消费层面的人工
     retry（读 BKD chat 重新解析），失败原因更可查。
3. **`orchestrator/tests/test_webhook_verifier_resume.py`** (新增)：
   - 单测覆盖：模拟 verifier issue session.completed 两次（escalate→pass）。
     - 第一次 dedup `new` → state REVIEW_RUNNING → ESCALATED。
     - 第二次 dedup `skip`（同 executionId 复用模拟）+ state ==
       REVIEW_RUNNING → bypass dedup → process → state 离开 REVIEW_RUNNING。
   - 反向 case：dedup `skip` 但 state 已不是 REVIEW_RUNNING → 维持 skip
     行为（防 stale redelivery 反推）。

## 不在范围

- 不动 `dedup.py`：dedup row schema / 接口不变。webhook.py 自己判 bypass。
- 不动 state machine：transition 表不增不减。
- 不改 BKD 行为：retrigger 是 orch 侧补救，不要求 BKD 重发 webhook。

## Risk

- **redelivery 反向 escalate hazard**：bypass dedup 必须以 state guard 守住。
  若 state guard 写错（例：忘了检查 state），BKD 任意时刻重发同一
  session.completed 都会触发 verifier decision 重处理，而那时 state 可能已
  推到下一 stage，再重新 escalate 会反向打断。所以 bypass 的 if 条件 4 项
  全部成立才 process（event/tag/dedup/state），缺一不可。
- **admin retrigger 滥用**：endpoint 有 `webhook_token` 认证（同 webhook），
  人不会随便戳。脚本里也走同 token。
