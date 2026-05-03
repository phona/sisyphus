## ADDED Requirements

### Requirement: start_analyze creates independent analyze sub-issue instead of hijacking intent issue

The system SHALL ensure that `start_analyze` does not modify the user's original BKD intent issue title, status, or core tags. Instead, it SHALL create a new BKD issue for the analyze-agent to work on.

#### Scenario: FIX-S1 intent issue keeps original title and status
- **GIVEN** a BKD intent issue exists with title "Add login endpoint" and status "todo"
- **WHEN** `start_analyze` is dispatched for REQ-X
- **THEN** the intent issue title remains "Add login endpoint" and status remains "todo"
- **AND** a new analyze sub-issue is created with title "[REQ-X] [ANALYZE] — Add login endpoint"

#### Scenario: FIX-S2 analyze sub-issue gets analyze tag and working status
- **GIVEN** `start_analyze` is dispatched for REQ-X
- **WHEN** the BKD sub-issue is created
- **THEN** the sub-issue tags include "analyze" and "REQ-X"
- **AND** the sub-issue status is set to "working" to trigger the agent

#### Scenario: FIX-S3 intent issue gets req_id tag for traceability
- **GIVEN** a BKD intent issue exists without REQ-X tag
- **WHEN** `start_analyze` is dispatched for REQ-X
- **THEN** the intent issue gets "REQ-X" tag added via merge_tags_and_update
- **AND** user hint tags (e.g., "repo:phona/foo", "ux:fast-track") are forwarded to both intent and analyze issues

#### Scenario: FIX-S4 idempotency via dispatch_slugs
- **GIVEN** `start_analyze` has already created an analyze sub-issue for REQ-X
- **WHEN** `start_analyze` is dispatched again for the same REQ-X
- **THEN** no new analyze sub-issue is created
- **AND** the existing analyze issue ID is returned from the slug cache

#### Scenario: FIX-S5 backward compatibility with existing intent issue tags
- **GIVEN** an intent issue already has "analyze" tag from a prior run
- **WHEN** `start_analyze` is dispatched
- **THEN** the existing "analyze" tag on the intent issue is preserved
- **AND** the new analyze sub-issue still gets its own "analyze" tag

#### Scenario: FIX-S6 analyze.md.j2 handles direct analyze path without intake summary
- **GIVEN** the direct analyze path (no intake) is triggered
- **WHEN** the analyze prompt is rendered
- **THEN** the prompt renders successfully without UndefinedError on intake_summary
- **AND** the prompt includes guidance for the agent to self-analyze the intent issue
