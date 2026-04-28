## ADDED Requirements

### Requirement: Multi-repo M16 end-to-end contract test suite

The system SHALL provide contract tests that verify multi-repo behavior across
the full sisyphus pipeline: clone -> checker -> PR link -> stage_run ->
artifact_check -> accept. Tests must be pure test increments (no production code
changes).

#### Scenario: MREPO-CLONE-S1..S9 resolve_repos 5-layer fallback with 2+ repos
- **GIVEN** ctx, tags, and settings with multi-repo values at various layers
- **WHEN** resolve_repos is called
- **THEN** L1 (intake_finalized_intent) > L2 (ctx.involved_repos) > L3 (tags.repo) > L4 (settings.default) > none, each returning all repos in the layer

#### Scenario: MREPO-CHKR-S1..S5 checker scripts traverse all cloned repos
- **GIVEN** dev_cross_check, staging_test, and baseline shell commands
- **WHEN** _build_cmd is called for a multi-repo REQ
- **THEN** each command iterates /workspace/source/*/ with per-repo checkout, run, and isolated log collection

#### Scenario: MREPO-LINK-S1..S5 cross-repo PR link discovery and caching
- **GIVEN** runner pod contains 2+ cloned repos with open PRs
- **WHEN** discover_pr_links / ensure_pr_links_in_ctx / _capture_pr_urls run
- **THEN** each repo is queried independently, partial failures are isolated, and cache prevents redundant GH calls

#### Scenario: MREPO-RUN-S1..S4 stage_run归属 per-REQ per-stage
- **GIVEN** multiple REQs with overlapping stages
- **WHEN** insert_stage_run / close_latest_stage_run / stamp_bkd_session_id execute
- **THEN** SQL binds to (req_id, stage) without cross-REQ leakage

#### Scenario: MREPO-ART-S1..S3 artifact_checks isolation with flake-retry
- **GIVEN** CheckResult with attempts and reason fields
- **WHEN** insert_check writes to artifact_checks table
- **THEN** (req_id, stage) isolation is maintained and attempts/reason are persisted

#### Scenario: MREPO-ACC-S1..S4 accept phase traverses all repos
- **GIVEN** _build_accept_script for a multi-repo REQ
- **WHEN** script runs env-up -> sleep -> smoke -> env-down
- **THEN** each repo is processed independently, missing targets are skipped, and env-down is best-effort
