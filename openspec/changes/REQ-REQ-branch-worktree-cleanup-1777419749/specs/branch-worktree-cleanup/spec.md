# branch-worktree-cleanup

## ADDED Requirements

### Requirement: sisyphus-pr-merged-hook SHALL delete the merged feat/REQ-* branch after notifying the orchestrator

The `.github/workflows/sisyphus-pr-merged-hook.yml` workflow SHALL, after successfully notifying the orchestrator of a PR merge, delete the PR's head branch via the GitHub REST API. The deletion MUST only occur when the PR has the `sisyphus` label and a REQ id was successfully extracted from the branch name. The deletion MUST be best-effort: if the branch is already deleted or the API returns an error, the workflow step MUST log the condition and continue without failing the workflow.

#### Scenario: BWC-S1 PR merge deletes the feat branch

- **GIVEN** a PR with head ref `feat/REQ-foo-123` and label `sisyphus` is merged
- **WHEN** the `sisyphus-pr-merged-hook` workflow runs
- **THEN** the orchestrator MUST be notified via `POST /admin/req/REQ-foo-123/pr-merged`
- **AND** the branch `feat/REQ-foo-123` MUST be deleted via `gh api repos/{repo}/git/refs/heads/feat/REQ-foo-123 -X DELETE`
- **AND** the workflow MUST succeed even if the branch was already deleted

#### Scenario: BWC-S2 non-sisyphus PR is ignored

- **GIVEN** a PR without the `sisyphus` label is merged
- **WHEN** the workflow trigger evaluates the `if` condition
- **THEN** the entire job MUST be skipped
- **AND** no branch deletion MUST be attempted

#### Scenario: BWC-S3 branch already deleted is handled gracefully

- **GIVEN** a merged sisyphus PR whose head branch was already deleted manually
- **WHEN** the delete step runs
- **THEN** the `gh api` call MAY return 404
- **AND** the step MUST print a message and exit 0

### Requirement: engine._cleanup_runner_on_terminal SHALL clean bkd/* worktrees and branches when a REQ reaches a terminal state

The orchestrator SHALL, upon a REQ entering a terminal state (`done` or `escalated`), execute a git cleanup command inside the runner pod after cleaning up the K8s Pod and PVC. The cleanup command MUST scan all git worktrees under the `worktrees/` directory, determine the branch checked out in each worktree, remove the worktree with `--force`, and delete the corresponding branch with `-D`. The cleanup MUST be fire-and-forget: any exception or non-zero exit code MUST be logged at debug level and MUST NOT block the state machine transition or raise an exception to the caller.

#### Scenario: BWC-S4 DONE state triggers git cleanup

- **GIVEN** a REQ transitions to `DONE` state
- **WHEN** `_cleanup_runner_on_terminal` is invoked
- **THEN** `cleanup_runner` MUST be called with `retain_pvc=False`
- **AND** `exec_in_runner` MUST be called with a shell command that iterates worktrees matching `/worktrees/`
- **AND** the command MUST remove each matching worktree and delete its branch

#### Scenario: BWC-S5 ESCALATED state triggers git cleanup

- **GIVEN** a REQ transitions to `ESCALATED` state
- **WHEN** `_cleanup_runner_on_terminal` is invoked
- **THEN** `cleanup_runner` MUST be called with `retain_pvc=True`
- **AND** `exec_in_runner` MUST still be called for git cleanup (worktree cleanup is independent of PVC retention)

#### Scenario: BWC-S6 no K8s controller skips both cleanups safely

- **GIVEN** no K8s controller is initialized (dev/test environment)
- **WHEN** `_cleanup_runner_on_terminal` is invoked
- **THEN** the function MUST return immediately without calling either `cleanup_runner` or `exec_in_runner`
- **AND** no exception MUST propagate

#### Scenario: BWC-S7 cleanup_runner failure still attempts git cleanup

- **GIVEN** `cleanup_runner` raises an exception
- **WHEN** `_cleanup_runner_on_terminal` is invoked
- **THEN** the exception MUST be caught and logged
- **AND** `exec_in_runner` MUST still be attempted afterward

#### Scenario: BWC-S8 exec_in_runner failure does not block state machine

- **GIVEN** `exec_in_runner` raises an exception or returns a non-zero exit code
- **WHEN** `_cleanup_runner_on_terminal` is invoked
- **THEN** the exception or non-zero result MUST be caught and logged at debug level
- **AND** no exception MUST propagate to the caller
