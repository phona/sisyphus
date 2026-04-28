## ADDED Requirements

### Requirement: admin /escalate accepts kind parameter for escalation reason

The system SHALL accept an optional `kind` field in the request body of
`POST /admin/req/{req_id}/escalate`. When provided, `kind` MUST be written as
`ctx.escalated_reason` and used as the BKD `reason:<kind>` tag on the intent issue.
When omitted, `kind` SHALL default to `"admin"`.

#### Scenario: EKS-S1 default kind written to context
- **GIVEN** a REQ in ANALYZING state and no request body
- **WHEN** POST /admin/req/{req_id}/escalate is called
- **THEN** the SQL UPDATE sets escalated_reason="admin" in the context JSON
- **AND** the response contains {"kind": "admin"}

#### Scenario: EKS-S2 custom kind written to context and response
- **GIVEN** a REQ in ANALYZING state
- **WHEN** POST /admin/req/{req_id}/escalate is called with body {"kind": "infra-flake"}
- **THEN** the SQL UPDATE sets escalated_reason="infra-flake" in the context JSON
- **AND** the response contains {"kind": "infra-flake"}

### Requirement: admin /escalate syncs BKD intent issue on force escalation

After force-escalating a REQ, the system SHALL sync the BKD intent issue by adding
tags `["escalated", "reason:<kind>"]` and setting `status_id="review"`. This sync
MUST be non-blocking: BKD unavailability SHALL NOT prevent the SQL state update or
runner cleanup from completing. The system SHALL log a warning on sync failure.

#### Scenario: EKS-S3 BKD sync called with escalated tags and review status
- **GIVEN** a REQ in ANALYZING state with ctx.intent_issue_id="intent-123"
- **WHEN** POST /admin/req/{req_id}/escalate is called with body {"kind": "watchdog-stuck"}
- **THEN** BKDClient.merge_tags_and_update is called with add=["escalated", "reason:watchdog-stuck"] and status_id="review"

#### Scenario: EKS-S4 BKD sync uses req_id as fallback when no intent_issue_id
- **GIVEN** a REQ whose context does not contain intent_issue_id
- **WHEN** POST /admin/req/{req_id}/escalate is called
- **THEN** BKD sync targets the req_id itself as the issue_id

#### Scenario: EKS-S5 BKD sync failure does not block escalation
- **GIVEN** BKD is unreachable (raises RuntimeError on connect)
- **WHEN** POST /admin/req/{req_id}/escalate is called
- **THEN** the SQL UPDATE still executes setting state=escalated
- **AND** the runner cleanup task is still scheduled
- **AND** the endpoint returns 200 with action="force_escalated"

#### Scenario: EKS-S6 already-escalated REQ returns noop without BKD sync
- **GIVEN** a REQ already in ESCALATED state
- **WHEN** POST /admin/req/{req_id}/escalate is called
- **THEN** the response is {"action": "noop", "state": "already escalated"}
- **AND** no SQL UPDATE is executed
- **AND** no BKD sync is attempted
