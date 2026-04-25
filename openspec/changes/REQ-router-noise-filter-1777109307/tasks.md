# Tasks: REQ-router-noise-filter-1777109307

## Stage: implementation (webhook)

- [x] `orchestrator/src/orchestrator/webhook.py`：扩展早期 noise filter，把 `issue.updated`
  也纳入。条件：`body.event == "issue.updated"` 且**没** REQ-N tag 且**没** `intent:intake` /
  `intent:analyze` tag → log.debug + `dedup.mark_processed` + `return skip`，不调
  `obs.record_event` / `derive_event`。
- [x] 现有 session.completed filter 路径行为不变（同条件、同返回）。

## Stage: tests

- [x] `orchestrator/tests/test_contract_router_noise_filter.py`（新文件）：
  - `test_RNF_S1_issue_updated_no_req_no_intent_skipped` —— issue.updated + tags 既无
    REQ 也无 intent 入口 → return skip + mark_processed 调过 + obs.record_event 没调过 +
    engine.step 没调过
  - `test_RNF_S2_issue_updated_with_req_tag_passes` —— issue.updated + tags 含 REQ-x →
    继续走下游（engine.step 被调）
  - `test_RNF_S3_issue_updated_with_intent_intake_passes` —— issue.updated + tags 仅含
    `intent:intake`，无 REQ → 走下游（INTENT_INTAKE 路径仍能 fire）
  - `test_RNF_S4_issue_updated_with_intent_analyze_passes` —— issue.updated + tags 仅含
    `intent:analyze`，无 REQ → 走下游（INTENT_ANALYZE 路径仍能 fire）
  - `test_RNF_S5_session_completed_no_req_still_skipped` —— 现有 session filter 路径回归

## Stage: spec

- [x] `openspec/changes/REQ-router-noise-filter-1777109307/proposal.md`
- [x] `openspec/changes/REQ-router-noise-filter-1777109307/tasks.md`
- [x] `openspec/changes/REQ-router-noise-filter-1777109307/specs/router-noise-filter/contract.spec.yaml`
- [x] `openspec/changes/REQ-router-noise-filter-1777109307/specs/router-noise-filter/spec.md`

## Stage: PR

- [x] git push feat/REQ-router-noise-filter-1777109307
- [x] gh pr create
