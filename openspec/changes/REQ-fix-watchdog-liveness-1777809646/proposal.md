# REQ-fix-watchdog-liveness-1777809646

fix(watchdog): liveness check + no tag pollution (closes #352 #353)

## Why

Watchdog 当前唯一的活体信号是 BKD `sessionStatus == "running"`。在 5/3 大 REQ
analyze 实测：analyze-agent 单 turn ~5min 写大量代码，watchdog 1min 一 tick，
检测到 `sessionStatus != "running"`（BKD 在 turn 边界、sub-issue 等待等场景会
短暂离开 `running`）+ stuck >= ended 阈值（300s）→ emit `SESSION_FAILED` →
escalate.py auto_resume 救回；连续两次 stuck → retry 用尽 → 真 escalate，给
**父 intent issue** 加 `[escalated, reason:<X>]` tag。Agent 实际还在干活，retry
是误救，escalated tag 是误污染。

后果：
- 每个长 analyze REQ 都被误标 escalated，UI / 用户误以为崩了
- `verifier_decisions.actual_outcome` 自动回填（PR #337）把"agent 实际跑通但
  父 issue 留着 escalated tag"算成 escalate / silent_pass，污染 Q8 verifier
  准确率
- auto_resume 的"⚠️ Session was interrupted"follow-up 打断 agent 自然节奏，
  agent 被迫从 chat history 重读现场重新规划

根因不是阈值不够长（拉长阈值只是把误判推后），而是**watchdog 用错信号**。BKD
`sessionStatus` 是 session 生命周期标，不是 agent 工作活体；真正能反映"agent
本秒还在产出"的是 BKD `/issues/{id}/logs` 里最新 log entry 的 `createdAt`
（assistant-message / tool-result 都会创建新 entry）。

## What Changes

### 1. 给 BKD client 加 `last_log_activity_at()`（轻量活体探针）

`BKDRestClient.last_log_activity_at(project_id, issue_id) -> datetime | None`
拉 `/issues/{id}/logs?limit=10` 取最新一条 log entry 的 `createdAt`。失败 / 空
日志返回 None。BKDClient (factory) 同步暴露同名方法。

### 2. watchdog 在 escalate 决策前先看活动

`watchdog._check_and_escalate` 拿到 issue 后，**不论 sessionStatus 是什么**，
先调 `last_log_activity_at`。如果距今 < `watchdog_liveness_grace_sec`（默认 120s）
→ 记 debug log 并 `return False`，不进入任何 escalate 路径（既不快车 / 也不
慢车 / 也不 self-heal）。本检查在 `_STAGES_NEEDING_RESULT_TAG` 自愈逻辑、
`fixer-round-cap` 硬终止之外（fixer-round-cap 仍然短路活体检查继续走 escalate，
因为是确定性硬封顶不依赖 agent 是否活着）。

### 3. 新配置 `watchdog_liveness_grace_sec`

`config.py` 加 `watchdog_liveness_grace_sec: int = 120`。120s 给单 turn 写大量
代码留出节奏空间；用户可调。SQL 预过滤阈值不动——row 仍按现有 ended_sec /
stuck_sec min 入选，活体检查只是在选中的 row 上多加一道豁免门。

### 4. 测试钉牢 auto_resume 路径不污染 tag

#353 的核心保证："auto_resume 路径不在父 issue 加 escalated tag"。当前代码已经
是这样（escalate.py auto_resume 早返回，不调 `merge_tags_and_update`），但缺
显式契约测试。新增 contract test 验证：transient + retry < 2 → escalate 返
`auto_resumed=True` + BKD client 没收到 `merge_tags_and_update` add `escalated`
的调用。

## Out of Scope

- 不改 `_STAGE_POLICY` 各 stage 的 ended_sec / stuck_sec 默认值。活体检查是更
  上层的过滤，policy 数值仍然作为硬上限兜底。
- 不做"escalated 后用户手工 resume 成功 → 自动撤 tag"。属于 M14b 后续扩展，
  本 REQ 先把误污染源头堵住。
- 不引入 `agent_turns` 表查询。`agent_turns_collector` 是 5min 批量 + 仅扫
  `ended_at IS NOT NULL` 的 stage_runs，不适合实时活体；直接调 BKD `/logs`
  更轻量、零额外耦合。
