# Tasks — REQ-ux-status-block-1777257283

## Stage: contract / spec
- [x] author `specs/bkd-intent-status-block/spec.md` (BISB-S1..S7 scenarios)
- [x] author `specs/bkd-intent-status-block/contract.spec.yaml` (partial schema)
- [x] author `proposal.md` + `design.md`

## Stage: implementation
- [x] add `orchestrator/src/orchestrator/prompts/_shared/status_block.md.j2`
      (markdown table, four always-on rows + three optional rows)
- [x] add `orchestrator/src/orchestrator/prompts/status_block.py` exposing
      `build_status_block_ctx(req_id, stage, ...)`
- [x] include status block at the top of `intake.md.j2` (above tools_whitelist)
- [x] include status block at the top of `analyze.md.j2` (above tools_whitelist)
- [x] `start_intake.py`: pass `bkd_intent_issue_url` + `status_block` into render
- [x] `start_analyze.py`: pass `status_block` into render (re-uses existing
      `bkd_intent_issue_url` + `cloned_repos`, adds `pr_urls` from ctx)
- [x] `start_analyze_with_finalized_intent.py`: pass `bkd_intent_issue_url` +
      `status_block` into render (parity with direct path)

## Stage: tests
- [x] add `orchestrator/tests/test_prompts_status_block.py` covering BISB-S1..S7
- [x] verify existing `test_prompts_sisyphus_label.py` and
      `test_prompts_repo_agnostic.py` still pass (no `status_block` kwarg
      required — guarded by `{% if status_block %}`)

## Stage: PR
- [x] `cd /workspace/source/sisyphus && BASE_REV=$(git merge-base HEAD origin/main) make ci-lint`
      (only changed files lint-clean — Makefile ci contract)
- [x] `make ci-unit-test` passes (integration tests excluded — Postgres-required tests skip)
- [x] `git push origin feat/REQ-ux-status-block-1777257283`
- [x] `gh label create sisyphus --force` then `gh pr create --label sisyphus`
      with body containing the `<!-- sisyphus:cross-link -->` footer block
