# bkd-status-sync

## ADDED Requirements

### Requirement: webhook._push_upstream_status SHALL retry with exponential backoff on BKD PATCH failure

The `_push_upstream_status` function in `webhook.py` SHALL retry the BKD `update_issue` PATCH up to 3 times with exponential backoff (1s, 2s) when the call raises an exception. The retry MUST occur within the same `BKDClient` async context to avoid redundant MCP initialization. After all retries are exhausted, the function MUST log a warning and return without raising, preserving the existing "do not block state machine" semantics.

#### Scenario: BSS-S1 transient BKD error succeeds on retry

- **GIVEN** BKD `update_issue` fails on the first 2 attempts with `RuntimeError("timeout")`
- **WHEN** `_push_upstream_status` is called with `project_id="proj-1"`, `issue_id="iss-a"`, `status_id="done"`
- **THEN** the function MUST attempt `update_issue` exactly 3 times
- **AND** the second attempt MUST sleep for 1.0 seconds before retrying
- **AND** the third attempt MUST sleep for 2.0 seconds before retrying
- **AND** the function MUST return successfully without raising

#### Scenario: BSS-S2 persistent BKD failure is swallowed after 3 attempts

- **GIVEN** BKD `update_issue` fails on all 3 attempts with `RuntimeError("500")`
- **WHEN** `_push_upstream_status` is called
- **THEN** the function MUST attempt `update_issue` exactly 3 times
- **AND** the function MUST log a warning with `webhook.upstream_status_failed`
- **AND** the function MUST return without raising an exception

### Requirement: watchdog SHALL periodically compensate stale sub-agent issues stuck in review

The watchdog `run_loop` SHALL invoke a compensation task every 5 ticks (approximately 5 minutes with default 60s interval). The compensation task MUST scan all active BKD projects, list their issues, and PATCH `statusId` to `"done"` for any issue matching ALL of the following criteria:

1. `statusId` is `"review"`
2. `sessionStatus` is `"completed"`
3. Contains a tag starting with `"REQ-"` (belongs to a sisyphus workflow)
4. Contains a sub-agent role tag: `"analyze"`, `"challenger"`, `"fixer"`, `"accept"`, or `"done-archive"`

The task MUST exclude issues tagged `"verifier"` because a verifier issue in `"review"` may be an intentional escalate state that requires human follow-up, and the BKD side cannot distinguish between pass/fix/escalate verdicts. The task MUST tolerate failures on individual issues and continue processing the rest.

#### Scenario: BSS-S3 completed analyze issue stuck in review is patched to done

- **GIVEN** a BKD issue with `statusId="review"`, `sessionStatus="completed"`, tags `["analyze", "REQ-1"]`
- **WHEN** the watchdog compensation task runs
- **THEN** the issue MUST be PATCHed to `statusId="done"`
- **AND** the patched count MUST be 1

#### Scenario: BSS-S4 verifier issue is skipped to preserve escalate resume path

- **GIVEN** a BKD issue with `statusId="review"`, `sessionStatus="completed"`, tags `["verifier", "REQ-1"]`
- **WHEN** the watchdog compensation task runs
- **THEN** the issue MUST NOT be PATCHed
- **AND** the patched count MUST be 0

#### Scenario: BSS-S5 running session is skipped

- **GIVEN** a BKD issue with `statusId="review"`, `sessionStatus="running"`, tags `["analyze", "REQ-1"]`
- **WHEN** the watchdog compensation task runs
- **THEN** the issue MUST NOT be PATCHed
- **AND** the patched count MUST be 0

#### Scenario: BSS-S6 non-review status is skipped

- **GIVEN** a BKD issue with `statusId="done"`, `sessionStatus="completed"`, tags `["analyze", "REQ-1"]`
- **WHEN** the watchdog compensation task runs
- **THEN** the issue MUST NOT be PATCHed
- **AND** the patched count MUST be 0

#### Scenario: BSS-S7 issue without REQ tag is skipped

- **GIVEN** a BKD issue with `statusId="review"`, `sessionStatus="completed"`, tags `["analyze"]` (no REQ tag)
- **WHEN** the watchdog compensation task runs
- **THEN** the issue MUST NOT be PATCHed
- **AND** the patched count MUST be 0

#### Scenario: BSS-S8 individual PATCH failure does not abort batch

- **GIVEN** two BKD issues both matching the criteria, but the first PATCH fails with HTTP 500
- **WHEN** the watchdog compensation task runs
- **THEN** the second issue MUST still be PATCHed to `statusId="done"`
- **AND** the result MUST report `patched=1, failed=1`

#### Scenario: BSS-S9 multiple projects are scanned

- **GIVEN** active REQ rows exist in `req_state` for both `project_id="proj-a"` and `project_id="proj-b"`
- **AND** each project has one matching stuck sub-agent issue
- **WHEN** the watchdog compensation task runs
- **THEN** both issues MUST be PATCHed to `statusId="done"`
- **AND** the result MUST report `patched=2, failed=0`
