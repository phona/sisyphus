# bkd-status-backfill

## ADDED Requirements

### Requirement: maintenance CLI MUST select only role-tagged review-stuck sub-issues

The `orchestrator.maintenance.backfill_bkd_review_stuck` CLI SHALL list every
BKD issue in the target project and select for backfill **only** those issues
whose `statusId == "review"` AND whose `sessionStatus` is not `"running"` AND
whose tag set contains exactly one of the recognised role tags
(`verifier`, `fixer`, `analyze`, `challenger`, `accept-agent`, `done-archive`)
AND whose tag set contains at least one tag matching the `REQ-*` prefix. Any
issue missing a role tag MUST NOT be patched — this protects user-created
intent issues from being silently archived. Any issue without a `REQ-*` tag
MUST NOT be patched — this protects orphan BKD entries unrelated to the
sisyphus workflow.

#### Scenario: BBR-S1 verifier issue at review with completed session is selected

- **GIVEN** a BKD issue with `statusId="review"`,
  `tags=["verifier","REQ-foo-1234","verify:staging_test","decision:escalate"]`,
  and `sessionStatus="completed"`
- **WHEN** `select_targets([issue])` is invoked
- **THEN** the issue MUST appear in the returned target list
- **AND** the decision_reason MUST start with `role=verifier;session=completed`

#### Scenario: BBR-S2 intent issue without role tag is rejected

- **GIVEN** a BKD issue with `statusId="review"`,
  `tags=["REQ-foo-1234"]` only (no role tag), and any sessionStatus
- **WHEN** `select_targets([issue])` is invoked
- **THEN** the issue MUST NOT appear in the returned target list
- **AND** the per-issue decision_reason MUST equal `"no-role-tag"`

#### Scenario: BBR-S3 running session is rejected even with role tag

- **GIVEN** a BKD issue with `statusId="review"`, `tags=["fixer","REQ-foo-1234"]`,
  and `sessionStatus="running"`
- **WHEN** `select_targets([issue])` is invoked
- **THEN** the issue MUST NOT appear in the returned target list
- **AND** the per-issue decision_reason MUST equal `"session-running"`

### Requirement: dry-run mode MUST emit decisions without any BKD write

When invoked **without** the `--apply` flag, the CLI SHALL list every candidate
and print one machine-readable JSON object per issue to stdout, but MUST NOT
issue any HTTP PATCH against the BKD REST API. The CLI MUST exit 0 even if
the candidate list is empty. Operators rely on this to preview before
committing.

#### Scenario: BBR-S4 dry-run prints candidates and makes zero PATCH calls

- **GIVEN** a BKD list response containing 2 candidate issues meeting BBR-S1
- **WHEN** `run(project_id="p", apply=False, ...)` is invoked
- **THEN** `httpx.AsyncClient.patch` MUST NOT be called
- **AND** stdout MUST contain exactly 2 JSON lines, each with `action="skipped"`
  and a non-empty `reason` field

### Requirement: --apply mode MUST PATCH each target to statusId='done' best-effort

When invoked with `--apply`, the CLI SHALL iterate the target list and call
`PATCH /api/projects/{project_id}/issues/{issue_id}` with body
`{"statusId": "done"}` for each. Tags MUST NOT be sent in the PATCH body
(BKD treats tags as full-replace, omitting the field preserves them). If a
single PATCH raises an HTTP error, the CLI MUST log a warning to stderr and
continue with the next target. The CLI exits 0 if at least one PATCH
succeeded; exits non-zero only if every PATCH attempt failed.

#### Scenario: BBR-S5 apply patches each candidate with statusId-only body

- **GIVEN** 3 candidate issues meeting BBR-S1
- **WHEN** `run(project_id="p", apply=True, ...)` is invoked and BKD returns
  HTTP 200 for each PATCH
- **THEN** `httpx.AsyncClient.patch` MUST be called exactly 3 times
- **AND** each call MUST target `/api/projects/p/issues/<issue_id>`
- **AND** each call body MUST be exactly `{"statusId": "done"}` — no `tags` key
- **AND** stdout MUST contain 3 JSON lines with `action="patched"`

#### Scenario: BBR-S6 partial PATCH failures continue and exit zero

- **GIVEN** 3 candidate issues; the second PATCH returns HTTP 503 while the
  first and third return 200
- **WHEN** `run(project_id="p", apply=True, ...)` is invoked
- **THEN** the run MUST attempt 3 PATCH calls (loop does not abort on the 503)
- **AND** the first and third stdout entries MUST report `action="patched"`
- **AND** the second stdout entry MUST report `action="failed"` with a non-empty
  `reason` mentioning the HTTP error
- **AND** the CLI exit code MUST be 0 (≥1 success)
