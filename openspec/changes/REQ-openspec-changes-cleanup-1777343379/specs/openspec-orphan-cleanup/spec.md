## ADDED Requirements

### Requirement: escalate action cleans up openspec/changes/REQ-XXX/ from runner repos

When the `escalate` action transitions a REQ to the ESCALATED terminal state via the real
escalate path (not auto-resume, not PR-merged shortcut), the system SHALL attempt to remove
`openspec/changes/<req_id>/` from each involved repo in the runner pod and commit the removal.
The cleanup MUST be fail-open: any exec failure or runner unavailability MUST NOT block the
escalate transition from completing and returning `{"escalated": True}`.

#### Scenario: OSC-S1 real escalate path calls exec_in_runner with rm + commit command
- **GIVEN** a REQ with verifier-decision-escalate event and two involved_repos
- **WHEN** the escalate action runs (non-transient reason, retry_count=0)
- **THEN** exec_in_runner is called once per repo with a command containing `rm -rf openspec/changes/<req_id>/` and `git commit`

#### Scenario: OSC-S2 cleanup failure does not block escalate
- **GIVEN** exec_in_runner raises RuntimeError for every call
- **WHEN** the escalate action runs
- **THEN** escalate still returns `{"escalated": True}` without raising

#### Scenario: OSC-S3 no involved repos skips cleanup
- **GIVEN** no involved_repos resolved (no ctx, no tags, no default)
- **WHEN** the escalate action runs
- **THEN** exec_in_runner is never called for cleanup

#### Scenario: OSC-S4 no runner controller is fail-open
- **GIVEN** k8s_runner.get_controller() raises RuntimeError
- **WHEN** the escalate action runs
- **THEN** escalate still returns `{"escalated": True}` without raising

### Requirement: start_analyze supersedes stale same-slug openspec/changes dirs

When `start_analyze` dispatches a new analyze run for REQ-XXX-vN (a redispatch), the system
SHALL scan each cloned repo for `openspec/changes/` directories whose base slug (after stripping
the `-vN` suffix) matches the current REQ's base slug but is not the current REQ itself. Any
such stale directory MUST be moved to `openspec/changes/_superseded/<old-dir>/` and committed.
The supersede step MUST be fail-open: any failure MUST NOT prevent BKD dispatch from proceeding.

#### Scenario: SUPR-S1 vN redispatch triggers supersede mv and commit
- **GIVEN** REQ-foo-1234-v2 is being dispatched with cloned_repos
- **WHEN** start_analyze calls _supersede_stale_openspec_changes
- **THEN** exec_in_runner receives a command containing `_superseded` and the current req_id

#### Scenario: SUPR-S2 no stale dirs when base_slug equals current
- **GIVEN** REQ-foo-1234 (no -vN suffix) is dispatched
- **WHEN** start_analyze calls _supersede_stale_openspec_changes
- **THEN** no mv is performed (current dir is excluded by the loop)

#### Scenario: SUPR-S3 supersede exec failure does not block dispatch
- **GIVEN** exec_in_runner raises RuntimeError during supersede
- **WHEN** start_analyze runs
- **THEN** BKD dispatch still proceeds (no emit=VERIFY_ESCALATE in result)

### Requirement: cleanup script classifies orphan openspec/changes dirs by PG state

The `orchestrator/scripts/cleanup_orphan_openspec_changes.py` script SHALL scan
`openspec/changes/REQ-*/` directories, query the PG `req_state` table for each REQ id,
and classify: state ∈ {done, escalated} or PG no record → action=delete; any other state
→ action=keep. The `archive` and `_superseded` subdirectories MUST be excluded from the scan.
With `--apply`, the script MUST run `git rm -rf` and `git commit` per directory (no push).

#### Scenario: COP-S1 done state maps to delete action
- **GIVEN** a REQ dir with PG state=done
- **WHEN** _classify is called
- **THEN** DirStatus.action == "delete"

#### Scenario: COP-S2 escalated state maps to delete action
- **GIVEN** a REQ dir with PG state=escalated
- **WHEN** _classify is called
- **THEN** DirStatus.action == "delete"

#### Scenario: COP-S3 in-flight state maps to keep action
- **GIVEN** a REQ dir with PG state=analyzing (in-flight)
- **WHEN** _classify is called
- **THEN** DirStatus.action == "keep"

#### Scenario: COP-S4 PG no record maps to delete action
- **GIVEN** a REQ dir with no matching row in PG req_state
- **WHEN** _classify is called
- **THEN** DirStatus.action == "delete" and DirStatus.state == "not_found"

#### Scenario: COP-S5 archive and _superseded dirs are excluded from scan
- **GIVEN** openspec/changes/ contains REQ-real-1234, archive/, and _superseded/
- **WHEN** _collect_req_dirs is called
- **THEN** only REQ-real-1234 appears; archive and _superseded are absent
