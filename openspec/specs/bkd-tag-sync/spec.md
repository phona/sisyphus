# bkd-tag-sync Specification

## Purpose
TBD - created by archiving change REQ-fix-bkd-tag-sync-race-1777427340. Update Purpose after archive.
## Requirements
### Requirement: merge_tags_and_update eliminates concurrent tag overwrite

`BKDRestClient.merge_tags_and_update` and `BKDMcpClient.merge_tags_and_update` SHALL use an optimistic-lock retry loop to prevent lost updates when multiple concurrent callers modify the same issue's tags. The method MUST read current tags via `get_issue`, compute the merged tag list locally, write via `update_issue`, then verify the write succeeded. If the returned tags do not match the expected list, the method MUST re-read the issue and retry the merge-write cycle. The retry loop MUST be capped at 3 attempts; on exhaustion it MUST log a warning and return the last result.

#### Scenario: BKD-S1 no race — single caller updates tags successfully
- **GIVEN** an issue with tags `["existing", "REQ-9"]`
- **WHEN** `merge_tags_and_update(add=["ci-passed"], remove=["existing"])` is called
- **THEN** `update_issue` is invoked with tags `["REQ-9", "ci-passed"]` and the method returns successfully on the first attempt

#### Scenario: BKD-S2 race detected and resolved by retry
- **GIVEN** two concurrent callers both read initial tags `["a"]`
- **WHEN** caller A adds `["b"]` and caller B adds `["c"]` concurrently
- **THEN** the slower caller detects its write was overwritten, re-reads the latest tags `["a", "b"]` or `["a", "c"]` (whichever won), and retries to produce the final merged list `["a", "b", "c"]`

### Requirement: admission rejection surfaces reason to user on BKD intent issue

When `start_analyze` rejects a REQ via `check_admission`, it MUST surface the rejection reason to the user on the BKD intent issue. The action SHALL call `merge_tags_and_update` to append the tag `reason:rate-limit` to the intent issue, and SHALL call `follow_up_issue` with a human-readable message explaining the rejection. BKD sync failures MUST be fail-open (log warning only) and MUST NOT block the escalation state-machine transition.

#### Scenario: BKD-S3 inflight-cap exceeded triggers visible feedback
- **GIVEN** admission returns `admit=False` with reason `inflight-cap-exceeded:10/10`
- **WHEN** `start_analyze` processes the denial
- **THEN** the intent issue receives tag `reason:rate-limit` and a follow-up message containing the full reason string

#### Scenario: BKD-S4 disk-pressure exceeded triggers visible feedback
- **GIVEN** admission returns `admit=False` with reason `disk-pressure:0.85/0.75`
- **WHEN** `start_analyze` processes the denial
- **THEN** the intent issue receives tag `reason:rate-limit` and a follow-up message containing the full reason string

#### Scenario: BKD-S5 BKD sync failure does not block escalation
- **GIVEN** admission returns `admit=False` and the subsequent BKD merge_tags_and_update raises an exception
- **WHEN** `start_analyze` processes the denial
- **THEN** the method still returns `{"emit": "verify.escalate"}` and the REQ proceeds to ESCALATED state

