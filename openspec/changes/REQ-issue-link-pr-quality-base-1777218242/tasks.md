# tasks: REQ-issue-link-pr-quality-base-1777218242

## Stage: spec
- [x] 写 proposal.md（动机 / 方案 / 取舍 / 影响面）
- [x] 写 specs/issue-pr-link/contract.spec.yaml（black-box 契约）
- [x] 写 specs/issue-pr-link/spec.md（ADDED Requirements + Scenarios LP-S1..S7）

## Stage: implementation
- [x] orchestrator/src/orchestrator/pr_links.py 新文件：
      `PrLink` dataclass、`from_ctx` / `pr_link_tags` helpers、
      `_discover_repos_via_runner` / `discover_pr_links` /
      `ensure_pr_links_in_ctx`（缓存 + 持久化 + 回填 ctx 里已知 issue id 的 tag）
- [x] orchestrator/src/orchestrator/actions/start_analyze.py：
      stash `analyze_issue_id = body.issueId` 到 ctx（backfill 用）
- [x] orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py：
      stash `analyze_issue_id = issue.id`（同上）
- [x] orchestrator/src/orchestrator/actions/_verifier.py：
      `invoke_verifier` / `start_fixer` create_issue 前调 helper 注入 `pr:*` tag
- [x] orchestrator/src/orchestrator/actions/create_staging_test.py：
      `_dispatch_bkd_agent` 注入 tag
- [x] orchestrator/src/orchestrator/actions/create_pr_ci_watch.py：
      `_dispatch_bkd_agent` 注入 tag
- [x] orchestrator/src/orchestrator/actions/create_accept.py：注入 tag
- [x] orchestrator/src/orchestrator/actions/done_archive.py：注入 tag
- [x] orchestrator/src/orchestrator/actions/start_challenger.py：注入 tag

## Stage: tests
- [x] orchestrator/tests/test_pr_links.py 新文件，覆盖 LP-S1..S7（+ 额外补充用例）
- [x] 跑 `make ci-unit-test`（943 测试全过，新加 18 条 / 改造 2 条 fixture）
- [x] 跑 `make ci-lint`（ruff 通过）
- [x] 跑 `openspec validate REQ-issue-link-pr-quality-base-1777218242 --strict` 通过

## Stage: PR
- [ ] git push origin feat/REQ-issue-link-pr-quality-base-1777218242
- [ ] gh pr create
