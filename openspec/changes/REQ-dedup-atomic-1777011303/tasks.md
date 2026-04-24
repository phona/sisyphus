# Tasks: REQ-dedup-atomic-1777011303

## Stage: migration

- [x] 创建 `orchestrator/migrations/0007_add_event_seen_processed_at.sql`（ADD COLUMN processed_at TIMESTAMPTZ + index）
- [x] 创建 `orchestrator/migrations/0007_add_event_seen_processed_at.rollback.sql`（DROP COLUMN）

## Stage: implementation

- [x] 改写 `orchestrator/src/orchestrator/store/dedup.py`：`check_and_record` 返回 "new"/"retry"/"skip"，新增 `mark_processed`
- [x] 改写 `orchestrator/src/orchestrator/webhook.py`：处理三值 dedup 状态，各 success path 调 `mark_processed`，exception path 不调

## Stage: tests

- [x] 创建 `orchestrator/tests/test_dedup.py`：dedup 单元测试（new/skip/retry/mark_processed）+ webhook 级别 dedup 行为测试

## Stage: observability

- [x] 创建 `observability/queries/sisyphus/17-dedup-retry-rate.sql`（pending_or_crashed vs done 每小时分布）

## Stage: spec

- [x] `openspec/changes/REQ-dedup-atomic-1777011303/proposal.md`
- [x] `openspec/changes/REQ-dedup-atomic-1777011303/tasks.md`
- [x] `openspec/changes/REQ-dedup-atomic-1777011303/specs/webhook-dedup/spec.md`
- [x] `openspec/changes/REQ-dedup-atomic-1777011303/specs/webhook-dedup/contract.spec.yaml`

## Stage: PR

- [x] git push feat/REQ-dedup-atomic-1777011303
- [x] gh pr create
