# tasks: REQ-archive-state-cleanup-1777195098

## Stage: spec
- [x] 写 proposal.md（动机 / 方案 / 取舍 / 影响面）
- [x] 写 specs/escalate-pr-merged-override/contract.spec.yaml（black-box 契约）
- [x] 写 specs/escalate-pr-merged-override/spec.md（ADDED Requirements + Scenarios PMO-S1..S8）

## Stage: implementation
- [x] orchestrator/src/orchestrator/actions/escalate.py: 加 `_all_prs_merged_for_req(repos, branch)` helper（GH REST 探测，HTTP error / 0 PR 都 return False）
- [x] orchestrator/src/orchestrator/actions/escalate.py: 加 `_apply_pr_merged_done_override(...)` helper（CAS state→DONE + ctx + BKD tags=done/via:pr-merge + cleanup_runner retain_pvc=False）
- [x] orchestrator/src/orchestrator/actions/escalate.py: 在 `escalate()` 入口（auto-resume 之前）插 PR-merged shortcut

## Stage: docs
- [x] docs/state-machine.md: 新增一节 "PR-merged shortcut (escalate 入口)"，指明 escalate 入口先做一次 GH PR 检查

## Stage: tests
- [x] orchestrator/tests/test_contract_escalate_pr_merged_override.py 新文件，覆盖 PMO-S1..S8 八条 scenarios
- [x] 跑 `make ci-unit-test`（新 test 全过 + 不破坏现有 escalate 测试）

## Stage: PR
- [x] git push origin feat/REQ-archive-state-cleanup-1777195098
- [x] gh pr create
