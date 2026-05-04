# Tasks — analyze→execute rename

## Stage: spec
- [x] Author proposal.md
- [x] Author design.md
- [x] Author specs/orchestration-stages/spec.md (delta)

## Stage: implementation — code rename
- [x] state.py — rename `ReqState.ANALYZING` → `EXECUTING`, `ReqState.ANALYZE_ARTIFACT_CHECKING` → `EXECUTE_ARTIFACT_CHECKING`, related Event members + string values
- [x] state.py — update TRANSITIONS table + `_ESCALATED_RESUME_EVENT_SOURCES` + `_PENDING_USER_REVIEW_RESUME_EVENT_SOURCES`
- [x] git mv `actions/start_analyze.py` → `start_execute.py`
- [x] git mv `actions/start_analyze_with_finalized_intent.py` → `start_execute_with_finalized_intent.py`
- [x] git mv `actions/create_analyze_artifact_check.py` → `create_execute_artifact_check.py`
- [x] git mv `checkers/analyze_artifact_check.py` → `checkers/execute_artifact_check.py`
- [x] git mv `prompts/analyze.md.j2` → `prompts/execute.md.j2`
- [x] git mv `prompts/verifier/analyze_*.md.j2` → `prompts/verifier/execute_*.md.j2`
- [x] git mv test files referencing `analyze` directly (start_analyze, create_analyze_artifact_check, checkers_analyze_artifact_check, contract_analyze_artifact_check)
- [x] actions/__init__.py — update imports + register names
- [x] router.py — update event mapping + add read-compat for `intent:analyze` / `analyze` / `verify:analyze`
- [x] webhook.py — update `_VERIFY_PASS_ROUTING` + add legacy stage key compat
- [x] intent_tags.py — add `"execute"` to SISYPHUS_MANAGED_EXACT (keep `"analyze"`)
- [x] engine.py — update `STATE_TO_STAGE`, `AGENT_STAGES`, `_EVENT_TO_OUTCOME`
- [x] snapshot.py — rename `_trigger_orphan_intent_analyze` → `_trigger_orphan_intent_execute`; double-tag detect
- [x] watchdog.py — update `_StagePolicy` map keys + `EXIT_AGENT_TAGS`
- [x] observability.py — update stage list
- [x] config.py — `mcp_capability_providers` map key + `skip_analyze` field
- [x] pr_links.py — update `analyze_issue_id` ctx key (keep alias for compat)
- [x] _verifier.py action helper — update stage→prompt path mapping
- [x] All inner string literals: `"analyze"` / `"analyze_artifact_check"` / `"analyzing"` etc

## Stage: implementation — DB migration
- [x] migrations/0017_rename_analyze_to_execute.sql — UPDATE req_state / event_log / stage_runs / verifier_decisions / artifact_checks
- [x] migrations/0017_rename_analyze_to_execute.rollback.sql — reverse UPDATE

## Stage: implementation — docs
- [x] CLAUDE.md — analyze → execute references
- [x] docs/architecture.md
- [x] docs/state-machine.md
- [x] docs/integration-contracts.md
- [x] docs/api-tag-management-spec.md
- [x] docs/observability.md
- [x] docs/prompts.md
- [x] docs/playbook.md (if any analyze refs)
- [x] docs/user-feedback-loop.md
- [x] README.md
- [x] observability/sisyphus-dashboard.md (if needed)

## Stage: implementation — Metabase / observability SQL
- [x] observability/queries/sisyphus/05-active-req-overview.sql comment
- [x] observability/agent_quality.sql
- [x] observability/queries/alert-stuck-session.sql
- [x] observability/queries/alert-layers-drift.sql
- [x] observability/queries/alert-duplicate-stage.sql
- [x] observability/schema.sql comment

## Stage: tests
- [x] Update `test_state.py` — assert new enum members
- [x] Update `test_router.py` — incl. read-compat (intent:analyze still routes)
- [x] Update `test_engine_main_chain.py` — full chain with EXECUTING
- [x] Update `test_engine_escalated_resume.py`
- [x] Update other tests referencing `ANALYZE_*` / `ANALYZING` / `analyze.*`
- [x] Add `test_router_analyze_compat.py` — read-compat regression
- [x] All renamed test files re-pointed at renamed module imports

## Stage: PR (must all green before push)
- [x] `make ci-lint` green
- [x] `make ci-unit-test` green
- [x] `make ci-integration-test` green (or skipped due to no PG)
- [x] `git push origin feat/REQ-refactor-analyze-execute-392-1777869660`
- [x] `gh pr create --label sisyphus`
