# pr-ready-notify

## ADDED Requirements

### Requirement: engine MUST tag BKD intent issue with pr-ready on REVIEW_RUNNING entry

The orchestrator engine SHALL, upon a successful CAS transition into
`ReqState.REVIEW_RUNNING`, check whether `ctx.pr_urls` is non-empty. When
non-empty, it MUST schedule a fire-and-forget asyncio.Task that calls
`BKDClient.merge_tags_and_update` on the BKD intent issue, adding the tag
`pr-ready` plus one `pr:owner/repo#N` tag per `(repo, url)` entry in
`ctx.pr_urls`. The statusId of the intent issue MUST NOT be changed. When
`ctx.pr_urls` is absent or empty, the helper MUST skip the BKD call entirely.
When `ctx.intent_issue_id` is absent, the helper MUST be a no-op. Any exception
from the BKD call MUST be caught and logged at WARNING level; the state machine
transition MUST NOT be blocked or rolled back.

#### Scenario: PRN-S1 REVIEW_RUNNING with non-empty pr_urls adds pr-ready tag

- **GIVEN** a REQ in state `PR_CI_RUNNING` with `ctx.intent_issue_id="intent-abc"`,
  `ctx.pr_urls={"phona/sisyphus": "https://github.com/phona/sisyphus/pull/42"}`,
  and `project_id="proj-x"`
- **WHEN** `engine.step` processes `Event.PR_CI_FAIL` and the CAS to
  `ReqState.REVIEW_RUNNING` succeeds
- **THEN** a fire-and-forget asyncio.Task MUST be scheduled that calls
  `BKDClient.merge_tags_and_update` with `add` containing `"pr-ready"` and
  `"pr:phona/sisyphus#42"`

#### Scenario: PRN-S2 empty pr_urls dict skips BKD PATCH

- **GIVEN** a REQ transitioning into `REVIEW_RUNNING` with `ctx.pr_urls={}`
- **WHEN** `engine.step` processes the transition and the pr-ready helper runs
- **THEN** `BKDClient.merge_tags_and_update` MUST NOT be called

#### Scenario: PRN-S3 absent pr_urls skips BKD PATCH

- **GIVEN** a REQ in `SPEC_LINT_RUNNING` with no `pr_urls` key in ctx (PR not yet opened)
- **WHEN** `engine.step` processes `Event.SPEC_LINT_FAIL` and transitions to `REVIEW_RUNNING`
- **THEN** `BKDClient.merge_tags_and_update` MUST NOT be called

#### Scenario: PRN-S4 BKD call failure logs warning and does not block transition

- **GIVEN** a REQ transitioning into `REVIEW_RUNNING` with non-empty `pr_urls`
  and BKD responding with HTTP 5xx
- **WHEN** `merge_tags_and_update` raises an exception inside the async Task
- **THEN** the helper MUST log a WARNING and MUST NOT re-raise
- **AND** `req_state.state` MUST be `REVIEW_RUNNING` (no rollback)

#### Scenario: PRN-S5 absent intent_issue_id is a no-op

- **GIVEN** `_tag_intent_pr_ready` is called with `intent_issue_id=None`
  and a non-empty `pr_urls` dict
- **WHEN** the helper runs
- **THEN** `BKDClient.merge_tags_and_update` MUST NOT be called
