# tasks: REQ-bkd-cleanup-historical-review-1777222384

## Stage: spec
- [x] 写 proposal.md（动机 / 方案 / 取舍 / 影响面）
- [x] 写 specs/bkd-status-backfill/contract.spec.yaml（black-box 契约）
- [x] 写 specs/bkd-status-backfill/spec.md（ADDED Requirements + Scenarios BBR-S1..S6）

## Stage: implementation
- [x] orchestrator/src/orchestrator/maintenance/__init__.py 占位 package
- [x] orchestrator/src/orchestrator/maintenance/backfill_bkd_review_stuck.py 主体（is_safe_target / select_targets / run / main）
- [x] 走 httpx 直连 BKD REST（不依赖 settings / DI），最小依赖

## Stage: tests
- [x] orchestrator/tests/test_backfill_bkd_review_stuck.py 新文件，覆盖 BBR-S1..S6
- [x] 跑 `make ci-unit-test`（新 test 全过 + 不破坏现有套件）
- [x] 跑 `make ci-lint`（ruff 全过）

## Stage: backfill 实跑
- [x] `--dry-run` 列出 candidates 落 PR description（46 条，分布 40 verifier / 3 fixer / 2 analyze / 1 challenger）
- [ ] `--apply` 实跑（**等 user 显式授权后跑**，本次 sandbox 拦了 mass-write；script 已就绪，跑法在 PR description）

## Stage: PR
- [x] git push origin feat/REQ-bkd-cleanup-historical-review-1777222384
- [x] gh pr create
