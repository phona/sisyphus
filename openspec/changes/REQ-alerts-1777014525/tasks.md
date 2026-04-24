# Tasks: REQ-alerts-1777014525

## Stage: migration

- [x] 创建 `orchestrator/migrations/0008_create_alerts.sql`（CREATE TABLE alerts + partial index）
- [x] 创建 `orchestrator/migrations/0008_create_alerts.rollback.sql`（DROP TABLE alerts）

## Stage: implementation

- [x] 创建 `orchestrator/src/orchestrator/store/alerts.py`（insert_alert / mark_sent_to_tg）
- [x] 创建 `orchestrator/src/orchestrator/alerts/__init__.py`（insert wrapper，自动取 pool）
- [x] 创建 `orchestrator/src/orchestrator/alerts/tg.py`（send_critical，无配置静默跳过）
- [x] 修改 `orchestrator/src/orchestrator/config.py`（tg_bot_token / tg_chat_id）
- [x] 改写 `orchestrator/src/orchestrator/actions/escalate.py`（ctx reason 优先级 + alerts + TG）
- [x] 修改 `orchestrator/src/orchestrator/k8s_runner.py`（_diagnose_pod + delete_pvc）
- [x] 修改 `orchestrator/src/orchestrator/actions/_verifier.py`（apply_verify_pass timeout 诊断 + invoke_verifier_after_fix 循环检测）
- [x] 改写 `orchestrator/src/orchestrator/watchdog.py`（5min warn 两阶段 + _WARN_THRESHOLD_SEC）
- [x] 改写 `orchestrator/src/orchestrator/runner_gc.py`（gc_pvcs + _disk_pressure）

## Stage: observability

- [x] 创建 `observability/queries/sisyphus/18-active-alerts.sql`（Q18 未 ack 活跃告警）
- [x] 创建 `observability/queries/sisyphus/19-alerts-trend.sql`（Q19 24h 趋势按小时/severity）
- [x] 创建 `observability/queries/sisyphus/20-escalate-reasons.sql`（Q20 30d critical reason 分布）
- [x] 修改 `observability/sisyphus-dashboard.md`（添加 Q17–Q20 条目）

## Stage: tests

- [x] 创建 `orchestrator/tests/test_alerts.py`（alerts insert/tg/escalate/diagnose/fixer-loop 共 14+ 用例）
- [x] 修改 `orchestrator/tests/test_watchdog.py`（两阶段 warn/escalate 测试 + _WARN_THRESHOLD_SEC 验证）
- [x] 修改 `orchestrator/tests/test_runner_gc.py`（gc_pvcs 5 个用例 + disk pressure）
- [x] 修改 `orchestrator/tests/test_migrate.py`（0008 forward/rollback 验证）

## Stage: spec

- [x] `openspec/changes/REQ-alerts-1777014525/proposal.md`
- [x] `openspec/changes/REQ-alerts-1777014525/tasks.md`
- [x] `openspec/changes/REQ-alerts-1777014525/specs/alerts-observability/spec.md`
- [x] `openspec/changes/REQ-alerts-1777014525/specs/alerts-observability/contract.spec.yaml`

## Stage: PR

- [x] git push feat/REQ-alerts-1777014525
- [x] gh pr create（PR #53）
