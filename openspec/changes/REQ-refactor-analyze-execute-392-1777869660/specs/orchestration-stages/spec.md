# Spec — orchestration stage `execute` (formerly `analyze`)

## ADDED Requirements

### Requirement: stage `execute` replaces stage `analyze` end-to-end

The orchestrator SHALL identify the full-delivery stage (the agent that writes
spec + business code + opens PR) by the name `execute`, not `analyze`. All
state-machine identifiers, event identifiers, action handler names, prompt
file paths, BKD tag values, stage_runs `stage` column values, observability
SQL stage-name literals, and human-facing documentation MUST use `execute`
consistently. The system MUST NOT introduce any new `analyze` reference for
this stage; only legacy read-compat paths are permitted to recognize
`analyze`-prefixed values.

#### Scenario: EXEC-S1 ReqState enum exposes EXECUTING with value "executing"
- **GIVEN** the orchestrator state module is imported
- **WHEN** test inspects `ReqState.EXECUTING.value`
- **THEN** value equals `"executing"`
- **AND** legacy member `ReqState.ANALYZING` is absent (raises `AttributeError`)

#### Scenario: EXEC-S2 ReqState exposes EXECUTE_ARTIFACT_CHECKING with value "execute-artifact-checking"
- **GIVEN** the orchestrator state module is imported
- **WHEN** test inspects `ReqState.EXECUTE_ARTIFACT_CHECKING.value`
- **THEN** value equals `"execute-artifact-checking"`
- **AND** legacy `ReqState.ANALYZE_ARTIFACT_CHECKING` is absent

#### Scenario: EXEC-S3 Event enum exposes EXECUTE_DONE / EXECUTE_ARTIFACT_CHECK_PASS / EXECUTE_ARTIFACT_CHECK_FAIL / INTENT_EXECUTE
- **GIVEN** the orchestrator state module is imported
- **WHEN** test inspects Event members and string values
- **THEN** `Event.EXECUTE_DONE.value == "execute.done"`
- **AND** `Event.EXECUTE_ARTIFACT_CHECK_PASS.value == "execute-artifact-check.pass"`
- **AND** `Event.EXECUTE_ARTIFACT_CHECK_FAIL.value == "execute-artifact-check.fail"`
- **AND** `Event.INTENT_EXECUTE.value == "intent.execute"`
- **AND** legacy members `ANALYZE_DONE` / `ANALYZE_ARTIFACT_CHECK_PASS` / `ANALYZE_ARTIFACT_CHECK_FAIL` / `INTENT_ANALYZE` are absent

#### Scenario: EXEC-S4 main-chain transitions use EXECUTING / EXECUTE_*
- **GIVEN** TRANSITIONS table is loaded
- **WHEN** lookup `(ReqState.INIT, Event.INTENT_EXECUTE)`
- **THEN** transition.next_state is `ReqState.EXECUTING`
- **AND** transition.action equals `"start_execute"`
- **AND** lookup `(ReqState.EXECUTING, Event.EXECUTE_DONE)` returns transition with action `"create_execute_artifact_check"` and next_state `ReqState.EXECUTE_ARTIFACT_CHECKING`

### Requirement: action handler registry uses execute names

The action registry SHALL register the rename-target action handlers under
their new names. Each renamed handler MUST appear in `actions.REGISTRY` keyed
by its new name, and the corresponding old name MUST NOT be registered.

#### Scenario: EXEC-S5 actions registry contains start_execute and create_execute_artifact_check
- **GIVEN** the actions package is imported
- **WHEN** test inspects `orchestrator.actions.REGISTRY`
- **THEN** keys `"start_execute"`, `"start_execute_with_finalized_intent"`, `"create_execute_artifact_check"`, `"invoke_verifier_for_execute_artifact_check_fail"` are all present
- **AND** none of `"start_analyze"`, `"start_analyze_with_finalized_intent"`, `"create_analyze_artifact_check"`, `"invoke_verifier_for_analyze_artifact_check_fail"` are present

### Requirement: router writes execute tags but reads both legacy and new tags

The router MUST emit `Event.INTENT_EXECUTE` for either `intent:analyze` or
`intent:execute` BKD tag, and MUST emit `Event.EXECUTE_DONE` for either
`analyze` or `execute` stage tag in `session.completed`. New code paths
(action handlers, sub-issue creation) MUST only write the new `execute` /
`intent:execute` / `verify:execute` tags.

#### Scenario: EXEC-S6 router maps intent:execute tag to INTENT_EXECUTE
- **GIVEN** webhook payload with `event_type="issue.updated"` and `tags=["intent:execute"]`
- **WHEN** `derive_event(event_type, tags)` is called
- **THEN** result equals `Event.INTENT_EXECUTE`

#### Scenario: EXEC-S7 router maps legacy intent:analyze tag to INTENT_EXECUTE (read-compat)
- **GIVEN** webhook payload with `event_type="issue.updated"` and `tags=["intent:analyze"]`
- **WHEN** `derive_event(event_type, tags)` is called
- **THEN** result equals `Event.INTENT_EXECUTE`

#### Scenario: EXEC-S8 session.completed with execute tag emits EXECUTE_DONE
- **GIVEN** webhook payload with `event_type="session.completed"` and `tags=["execute"]`
- **WHEN** `derive_event(event_type, tags)` is called
- **THEN** result equals `Event.EXECUTE_DONE`

#### Scenario: EXEC-S9 session.completed with legacy analyze tag emits EXECUTE_DONE (read-compat)
- **GIVEN** webhook payload with `event_type="session.completed"` and `tags=["analyze"]`
- **WHEN** `derive_event(event_type, tags)` is called
- **THEN** result equals `Event.EXECUTE_DONE`

#### Scenario: EXEC-S10 verifier pass routing accepts both stage keys
- **GIVEN** verifier decision with `stage="analyze"` (legacy) returns `pass`
- **WHEN** `pass_event_for_stage("analyze")` is called
- **THEN** result equals `Event.EXECUTE_DONE`
- **AND** `pass_event_for_stage("execute")` also returns `Event.EXECUTE_DONE`
- **AND** `pass_event_for_stage("analyze_artifact_check")` and `pass_event_for_stage("execute_artifact_check")` both return `Event.EXECUTE_ARTIFACT_CHECK_PASS`

### Requirement: prompts directory exposes execute templates

Renamed prompt files MUST exist at the new path under
`orchestrator/src/orchestrator/prompts/`. The legacy `analyze.md.j2`
file MUST NOT exist after the rename; verifier subdirectory MUST contain
`execute_success.md.j2`, `execute_fail.md.j2`,
`execute_artifact_check_success.md.j2`, `execute_artifact_check_fail.md.j2`
and MUST NOT contain `analyze_*.md.j2` siblings.

#### Scenario: EXEC-S11 prompts/execute.md.j2 exists and analyze.md.j2 does not
- **GIVEN** the orchestrator package source tree
- **WHEN** test inspects file presence under `orchestrator/src/orchestrator/prompts/`
- **THEN** `execute.md.j2` exists
- **AND** `analyze.md.j2` does not exist
- **AND** `verifier/execute_success.md.j2`, `verifier/execute_fail.md.j2`, `verifier/execute_artifact_check_success.md.j2`, `verifier/execute_artifact_check_fail.md.j2` all exist
- **AND** none of `verifier/analyze_*.md.j2` exist

### Requirement: stage_runs and observability emit execute as stage label

The system SHALL refer to the rename-target stage as `execute` /
`execute_artifact_check` — not `analyze` / `analyze_artifact_check` — in
`engine.STATE_TO_STAGE`, `engine.AGENT_STAGES`, the `observability` stage
list, and all Metabase / sisyphus SQL queries.

#### Scenario: EXEC-S12 STATE_TO_STAGE maps EXECUTING to "execute"
- **GIVEN** the engine module is imported
- **WHEN** test inspects `engine.STATE_TO_STAGE[ReqState.EXECUTING]`
- **THEN** value equals `"execute"`
- **AND** `engine.STATE_TO_STAGE[ReqState.EXECUTE_ARTIFACT_CHECKING] == "execute_artifact_check"`
- **AND** `"execute" in engine.AGENT_STAGES` is True
- **AND** `"analyze" in engine.AGENT_STAGES` is False

### Requirement: DB migration 0017 renames stored string values

Migration `0017_rename_analyze_to_execute.sql` MUST exist and MUST UPDATE
all stored string values from the `analyze*` family to the `execute*` family
in tables `req_state`, `event_log`, `stage_runs`, `verifier_decisions`, and
`artifact_checks`. A matching `.rollback.sql` MUST reverse the change.

#### Scenario: EXEC-S13 migration 0017 file exists with rename UPDATE statements
- **GIVEN** the migrations directory
- **WHEN** test reads `orchestrator/migrations/0017_rename_analyze_to_execute.sql`
- **THEN** file content contains `UPDATE req_state SET cur_state='executing' WHERE cur_state='analyzing'`
- **AND** content contains `UPDATE stage_runs SET stage='execute' WHERE stage='analyze'`
- **AND** rollback file `0017_rename_analyze_to_execute.rollback.sql` exists with reverse UPDATEs

### Requirement: intent_tags treats execute as sisyphus-managed

`intent_tags.SISYPHUS_MANAGED_EXACT` MUST contain `"execute"` to keep the new
stage tag from being propagated as a user hint. The legacy `"analyze"` value
SHOULD remain in the set during the read-compat window.

#### Scenario: EXEC-S14 SISYPHUS_MANAGED_EXACT contains "execute" and "analyze"
- **GIVEN** the intent_tags module is imported
- **WHEN** test reads `intent_tags.SISYPHUS_MANAGED_EXACT`
- **THEN** `"execute"` is in the set
- **AND** `"analyze"` is in the set (for read-compat, removed in follow-up REQ)
