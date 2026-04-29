## ADDED Requirements

### Requirement: Multi-repo end-to-end contract test coverage

The system SHALL provide black-box contract tests verifying that the sisyphus
orchestrator correctly handles requests spanning 2+ repositories across clone,
checker, PR-link, stage-run, artifact-check, and accept phases.

#### Scenario: MREPO-CLONE-S1 L1 returns all repos
- **GIVEN** ctx has intake_finalized_intent.involved_repos=["phona/repo-a","phona/repo-b"] plus fallback layers
- **WHEN** resolve_repos is called
- **THEN** repos=["phona/repo-a","phona/repo-b"] and source label="ctx.intake_finalized_intent.involved_repos"

#### Scenario: MREPO-CLONE-S2 L2 fallback with multi-repo
- **GIVEN** ctx has involved_repos=["phona/repo-a","phona/repo-b","phona/repo-c"] and L1 empty
- **WHEN** resolve_repos is called
- **THEN** repos=["phona/repo-a","phona/repo-b","phona/repo-c"] and source label="ctx.involved_repos"

#### Scenario: MREPO-CLONE-S3 L3 tag fallback with multi-repo
- **GIVEN** tags=["repo:phona/repo-a","repo:phona/repo-b","sisyphus"] and L1/L2 empty
- **WHEN** resolve_repos is called
- **THEN** repos=["phona/repo-a","phona/repo-b"] and source label="tags.repo"

#### Scenario: MREPO-CLONE-S4 L4 default fallback with multi-repo
- **GIVEN** default_repos=["phona/repo-a","phona/repo-b"] and L1-L3 empty
- **WHEN** resolve_repos is called
- **THEN** repos=["phona/repo-a","phona/repo-b"] and source label="settings.default_involved_repos"

#### Scenario: MREPO-CLONE-S5 all layers empty
- **GIVEN** all layers empty
- **WHEN** resolve_repos is called
- **THEN** repos=[] and source label="none"

#### Scenario: MREPO-CLONE-S6 clone helper success with 2+ repos
- **GIVEN** 2 repos in resolve_repos result
- **WHEN** clone_involved_repos_into_runner executes
- **THEN** helper cmd contains both repo slugs, returns (repos, None)

#### Scenario: MREPO-CLONE-S7 clone helper nonzero exit
- **GIVEN** helper returns exit code 128
- **WHEN** clone_involved_repos_into_runner executes
- **THEN** returns (repos, 128) so caller can escalate

#### Scenario: MREPO-CLONE-S8 no controller dev fallback
- **GIVEN** k8s_runner.get_controller raises RuntimeError
- **WHEN** clone_involved_repos_into_runner executes
- **THEN** returns (None, None) so agent self-clones

#### Scenario: MREPO-CLONE-S9 start_execute passes tags+default to helper
- **GIVEN** start_execute called with tags and settings.default_involved_repos
- **WHEN** clone helper is invoked
- **THEN** both tags= and default_repos= are forwarded

#### Scenario: MREPO-CHKR-S1 dev_cross_check traverses all repos
- **GIVEN** _build_cmd for REQ-test
- **WHEN** command runs in runner pod
- **THEN** for-loop over /workspace/source/*/ with per-repo fetch+checkout+ci-lint

#### Scenario: MREPO-CHKR-S2 staging_test parallel per repo
- **GIVEN** _build_cmd for REQ-test
- **WHEN** command runs in runner pod
- **THEN** background jobs (&) + wait, per-repo unit/int log files

#### Scenario: MREPO-CHKR-S3 baseline also traverses
- **GIVEN** _build_baseline_cmd
- **WHEN** command runs
- **THEN** for-loop over /workspace/source/*/ checkout origin/main + ci-*

#### Scenario: MREPO-CHKR-S4 parse per-repo results
- **GIVEN** stdout has PASS markers and stderr has FAIL markers for 3 repos
- **WHEN** _parse_repo_results runs
- **THEN** dict with correct True/False per repo

#### Scenario: MREPO-CHKR-S5 diff isolates introduced failures
- **GIVEN** baseline failures and PR failures for 3 repos
- **WHEN** _compute_diff runs
- **THEN** introduced = pr_failures - baseline_failures only

#### Scenario: MREPO-LINK-S1 discover multi-repo PR links
- **GIVEN** runner has repo-a and repo-b, both have open PRs
- **WHEN** discover_pr_links runs
- **THEN** returns PrLink for both repos

#### Scenario: MREPO-LINK-S2 partial failure isolated
- **GIVEN** runner has repo-a (no PR) and repo-b (has PR)
- **WHEN** discover_pr_links runs
- **THEN** returns only repo-b link, repo-a silently skipped

#### Scenario: MREPO-LINK-S3 cache prevents redundant GH calls
- **GIVEN** ctx.pr_links already populated
- **WHEN** ensure_pr_links_in_ctx runs
- **THEN** returns cached links without calling discover_pr_links

#### Scenario: MREPO-LINK-S4 pr_link_tags format
- **GIVEN** 2 PrLink objects
- **WHEN** pr_link_tags runs
- **THEN** returns ["pr:owner/repo#N", ...]

#### Scenario: MREPO-LINK-S5 create_pr_ci_watch captures multi-repo URLs
- **GIVEN** runner has repo-a and repo-b with PRs
- **WHEN** _capture_pr_urls runs
- **THEN** ctx.pr_urls dict persisted with both repos

#### Scenario: MREPO-RUN-S1 insert_stage_run binds req_id
- **GIVEN** pool stub
- **WHEN** insert_stage_run(pool, "REQ-a", "execute")
- **THEN** SQL references req_id and stage, returns run id

#### Scenario: MREPO-RUN-S2 insert_stage_run supports parallel_id
- **GIVEN** pool stub
- **WHEN** insert_stage_run with parallel_id="repo-a"
- **THEN** parallel_id is bound as 3rd arg

#### Scenario: MREPO-RUN-S3 close_latest targets stage
- **GIVEN** pool stub
- **WHEN** close_latest_stage_run(pool, "REQ-x", "execute", outcome="pass")
- **THEN** SQL WHERE req_id=$1 AND stage=$2

#### Scenario: MREPO-RUN-S4 stamp only open rows
- **GIVEN** pool stub
- **WHEN** stamp_bkd_session_id runs
- **THEN** SQL WHERE ended_at IS NULL AND bkd_session_id IS NULL

#### Scenario: MREPO-ART-S1 insert_check binds req+stage
- **GIVEN** CheckResult and pool stub
- **WHEN** insert_check(pool, "REQ-x", "dev_cross_check", result)
- **THEN** SQL binds req_id and stage

#### Scenario: MREPO-ART-S2 CheckResult carries attempts+reason
- **GIVEN** CheckResult with attempts=3, reason="flake-retry-recovered:dns"
- **WHEN** fields are accessed
- **THEN** attempts==3 and reason matches

#### Scenario: MREPO-ART-S3 insert_check persists attempts
- **GIVEN** CheckResult with attempts=2, reason="flake-retry-exhausted:timeout"
- **WHEN** insert_check executes
- **THEN** args[8]==2 and args[9]==reason string

#### Scenario: MREPO-ACC-S1 accept script traverses all repos
- **GIVEN** _build_accept_script for REQ-x
- **WHEN** script is generated
- **THEN** contains for-loop over /workspace/source/*/ with env-up, sleep, smoke, env-down

#### Scenario: MREPO-ACC-S2 per-repo failure isolation
- **GIVEN** _build_accept_script
- **WHEN** one repo fails env-up or smoke
- **THEN** fail=1 accumulates but script continues; env-down is || true

#### Scenario: MREPO-ACC-S3 missing target skip
- **GIVEN** a repo without accept-env-up target
- **WHEN** script runs make -n check
- **THEN** skip that repo with warning, continue others

#### Scenario: MREPO-ACC-S4 no repos vacuous pass
- **GIVEN** cloned_repos empty
- **WHEN** create_accept action runs
- **THEN** returns ACCEPT_PASS immediately

#### Scenario: MREPO-PR-S1 watch_pr_ci accepts repos
- **GIVEN** inspect.signature of watch_pr_ci
- **WHEN** params are listed
- **THEN** "repos" parameter exists

#### Scenario: MREPO-PR-S2 discover multi-repo from runner
- **GIVEN** runner has repo-a and repo-b with git remotes
- **WHEN** _discover_repos_from_runner runs
- **THEN** returns ["phona/repo-a", "phona/repo-b"]

#### Scenario: MREPO-ESCALATE-S1 iterate all involved repos
- **GIVEN** escalate with involved_repos=["repo-a","repo-b","repo-c"]
- **WHEN** action runs
- **THEN** open_incident called exactly 3 times, once per repo

#### Scenario: MREPO-STATE-S1..S3 ctx supports multi-repo fields
- **GIVEN** source code of create_accept, start_execute, create_pr_ci_watch, escalate
- **WHEN** inspected
- **THEN** each references cloned_repos, involved_repos, and/or pr_urls

#### Scenario: MREPO-SUPERSEDE-S1 supersede per repo
- **GIVEN** _supersede_stale_openspec_changes source
- **WHEN** inspected
- **THEN** iterates over repos list with per-repo basename computation

#### Scenario: MREPO-INTAKE-S1 intake path clones multi-repo
- **GIVEN** start_execute_with_finalized_intent with 2 involved repos
- **WHEN** action runs
- **THEN** clone helper called with both repos, result contains cloned_repos
