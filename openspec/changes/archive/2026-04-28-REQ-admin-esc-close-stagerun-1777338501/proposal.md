# REQ-admin-esc-close-stagerun-1777338501 — force_escalate 收尾 stage_run 防长尾

## 问题

`POST /admin/req/{req_id}/escalate`（`force_escalate`）通过 raw SQL `UPDATE req_state SET state='escalated'` 绕过 `engine.step()`，导致：

- `engine._record_stage_transitions` 不会被触发
- 任何进行中的 `stage_runs` 行（`ended_at IS NULL`）在 REQ 被 force-escalate 后永远不会被关闭
- 这些 orphan 行的 `duration_sec = NULL`、`outcome = NULL`，污染 `stage_stats` 视图的 AVG/P50/P95 计算以及 Metabase 指标

## 解决方案

1. `store/stage_runs.py` 新增 `close_open_stage_runs(pool, req_id, *, outcome, fail_reason)` —— 一次性关闭某 req 所有 `ended_at IS NULL` 的 stage_run 行
2. `admin.py` `force_escalate` 在 raw SQL UPDATE 之后立即调用 `close_open_stage_runs(outcome='escalated', fail_reason='admin-force-escalate')`（best-effort，失败只 log warning）
3. 补测试 `test_force_escalate_closes_open_stage_runs`（FRE-S3）

## 影响范围

- 仅涉及 `sisyphus/orchestrator` 仓
- 不改状态机逻辑，不改 DB schema
- 不影响正常 engine.step() 路径（`_record_stage_transitions` 逻辑不变）
