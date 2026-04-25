# server-side-clone-and-no-env-fallback Specification

## Purpose
TBD - created by archiving change REQ-clone-and-pr-ci-fallback-1777115925. Update Purpose after archive.
## Requirements
### Requirement: start_analyze MUST server-side clone involved_repos before dispatching analyze-agent

The orchestrator SHALL own the clone of every repo listed in
`ctx.intake_finalized_intent.involved_repos` (falling back to
`ctx.involved_repos`) into the runner pod's `/workspace/source/<basename>/`,
and MUST do so server-side from `actions/start_analyze.py::start_analyze`
and `actions/start_analyze_with_finalized_intent.py::start_analyze_with_finalized_intent`
after `k8s_runner.ensure_runner(req_id, wait_ready=True)` returns and
BEFORE the BKD `follow_up_issue(prompt)` call dispatches the
analyze-agent. The clone MUST be performed by invoking
`/opt/sisyphus/scripts/sisyphus-clone-repos.sh <repo1> <repo2> ...` inside
the runner pod via `k8s_runner.exec_in_runner`. The analyze-agent MUST
NOT be the only path that places source code under
`/workspace/source/<basename>/`.

When the runner controller is unavailable (`k8s_runner.get_controller()`
raises `RuntimeError`, e.g. dev environment without K8s), the orchestrator
MUST log a warning and skip server-side clone gracefully, allowing the
agent to fall back to its prompt-driven clone path.

#### Scenario: SCNF-S1 server-side clone runs sisyphus-clone-repos.sh with intake involved_repos

- **GIVEN** `start_analyze_with_finalized_intent` is invoked with
  `ctx={"intake_finalized_intent": {"involved_repos": ["phona/repo-a", "ZonEaseTech/ttpos-server-go"]}}`
- **WHEN** `k8s_runner.ensure_runner` returns Pod ready
- **THEN** `k8s_runner.exec_in_runner` MUST be called with a command that
  invokes `/opt/sisyphus/scripts/sisyphus-clone-repos.sh` and contains both
  `phona/repo-a` and `ZonEaseTech/ttpos-server-go` as positional arguments
- **AND** the call MUST happen before `BKDClient.follow_up_issue`

#### Scenario: SCNF-S2 server-side clone is skipped when no involved_repos in ctx

- **GIVEN** `start_analyze` is invoked on the direct-analyze path with
  `ctx={"intent_title": "..."}` (no `intake_finalized_intent`, no
  `involved_repos`)
- **WHEN** the action runs
- **THEN** `k8s_runner.exec_in_runner` MUST NOT be called
- **AND** the action MUST still dispatch the analyze-agent via
  `BKDClient.follow_up_issue` (preserving the direct-analyze fallback path
  where the agent clones manually per `analyze.md.j2` Part A.3)

### Requirement: start_analyze MUST emit VERIFY_ESCALATE when server-side clone fails

The `start_analyze` / `start_analyze_with_finalized_intent` action SHALL
NOT proceed to dispatch the analyze-agent when the server-side clone
helper exits with a non-zero exit code (e.g., GitHub auth failure, repo
name typo, persistent network error); it MUST instead return a mapping
containing `emit: VERIFY_ESCALATE` and a `reason` string that includes
the literal substring `clone failed` plus the failing helper exit code,
so the engine routes the REQ directly to ESCALATED rather than hand a
broken workspace to the agent.

#### Scenario: SCNF-S3 clone helper exit code 5 → emit VERIFY_ESCALATE

- **GIVEN** `ctx.intake_finalized_intent.involved_repos = ["phona/typo-repo"]`
  and `k8s_runner.exec_in_runner` returns `ExecResult(exit_code=5, stderr="...")`
- **WHEN** `start_analyze_with_finalized_intent` runs
- **THEN** the return value MUST contain `emit == "VERIFY_ESCALATE"` and
  `reason` MUST contain the substring `clone failed` and the literal
  `5` (the helper exit code)
- **AND** `BKDClient.follow_up_issue` MUST NOT have been called (no agent
  dispatched on a broken workspace)

### Requirement: pr_ci_watch MUST raise ValueError when caller passes no repos

`watch_pr_ci` MUST raise `ValueError("no repos provided ...")` immediately
when its `repos` argument is empty / `None`, treating the missing argument
as a configuration error;
`checkers/pr_ci_watch.py::watch_pr_ci(req_id, branch, ..., repos=None)`
SHALL treat an empty / `None` `repos` argument as a configuration error.
The function MUST NOT consult any environment variable (in particular
MUST NOT read `SISYPHUS_BUSINESS_REPO` or any other global env) to
synthesize a fallback repo list. The decision of which repos to monitor
SHALL be made entirely by the caller
(`actions/create_pr_ci_watch.py::_run_checker`) based on per-REQ context
(runner-pod filesystem discovery and / or
`ctx.intake_finalized_intent.involved_repos`).

The deletion of the env-var fallback is intentional: a process-global env
var inevitably gets stale across REQs (the env points at a single repo set
at orchestrator startup, while real REQs span multiple repos and rotate
weekly). The previous fallback caused two failure modes — (i) wrong-repo
PR lookup when the env did not match the current REQ, (ii) silent loss of
non-env-listed repos in multi-repo REQs — both of which appeared as
either spurious PR-CI failures (verifier-fix-dev waste) or false-positive
passes (review-only-bot misclassification on the unwatched repo).

#### Scenario: SCNF-S4 watch_pr_ci raises ValueError when repos=None and no env consulted

- **GIVEN** `SISYPHUS_BUSINESS_REPO` env var is unset
- **WHEN** `watch_pr_ci("REQ-X", "feat/REQ-X")` is awaited (no `repos`
  argument)
- **THEN** `ValueError` MUST be raised with message containing
  `no repos provided`

#### Scenario: SCNF-S5 watch_pr_ci ignores SISYPHUS_BUSINESS_REPO env even when set

- **GIVEN** `SISYPHUS_BUSINESS_REPO=phona/legacy-repo` is set in the
  process environment AND the caller passes `repos=None` (e.g., runner
  discovery returned empty list and ctx had no involved_repos)
- **WHEN** `watch_pr_ci("REQ-X", "feat/REQ-X", repos=None)` is awaited
- **THEN** `ValueError` MUST be raised exactly as in SCNF-S4 — the env
  var MUST NOT be read, MUST NOT be used to synthesize `repos=["phona/legacy-repo"]`,
  MUST NOT log "falling back to env"
- **AND** no GitHub API call MUST be issued (no PR lookup, no check-run
  fetch — the function MUST short-circuit before any HTTP I/O)

### Requirement: create_pr_ci_watch MUST translate ValueError from checker to PR_CI_TIMEOUT

The `actions/create_pr_ci_watch.py::_run_checker` function SHALL catch
`ValueError` raised by `pr_ci_watch.watch_pr_ci` (the only ValueError now
being the "no repos provided" config error) and translate it to
`emit: PR_CI_TIMEOUT` with a `reason` string that contains the literal
substring `config error`. This MUST route the REQ directly to ESCALATED
through the existing PR_CI_TIMEOUT transition, NOT through PR_CI_FAIL +
verifier (because verifier-agent cannot fix the missing per-REQ
configuration — only the human or the upstream intake can).

#### Scenario: SCNF-S6 create_pr_ci_watch returns PR_CI_TIMEOUT when repos cannot be resolved

- **GIVEN** runner-pod discovery returns `[]` (workspace empty),
  `ctx.intake_finalized_intent` is `None`, and `ctx.involved_repos` is also
  `None`
- **WHEN** `create_pr_ci_watch._run_checker(req_id, ctx)` is awaited
- **THEN** the return value MUST be `{"emit": "PR_CI_TIMEOUT", "reason": "config error: no repos provided ...", "exit_code": -1}`
- **AND** `pr_ci_watch.watch_pr_ci` MUST have been called once with
  `repos=None` (not silently skipped — the action MUST defer the
  configuration check to the checker so the rule lives in one place)

### Requirement: SISYPHUS_BUSINESS_REPO env var MUST NOT be referenced in production code paths

The string literal `SISYPHUS_BUSINESS_REPO` SHALL NOT appear in the
production-path source files of `orchestrator/src/orchestrator/` (excluding
test fixtures). Specifically the deletion includes
`orchestrator/src/orchestrator/checkers/pr_ci_watch.py:86` and any other
reference under `orchestrator/src/orchestrator/`. The contract test
`tests/test_contract_clone_and_pr_ci_fallback.py` MUST grep the source
tree to assert this invariant, so future copy-paste reintroduction is
caught by CI rather than by a stale-env-fallback bug at 3am.

The string MAY remain in test files under `orchestrator/tests/` only when
a test explicitly verifies that the env var is ignored (the SCNF-S5
regression guard). Production code references MUST be exactly zero.

#### Scenario: SCNF-S7 grep finds zero SISYPHUS_BUSINESS_REPO references in production source

- **GIVEN** the orchestrator source tree at `orchestrator/src/orchestrator/`
- **WHEN** the contract test runs `grep -rn "SISYPHUS_BUSINESS_REPO" orchestrator/src/orchestrator/`
- **THEN** the result MUST be empty (zero matches) — no Python source file
  under the production path SHALL contain the string `SISYPHUS_BUSINESS_REPO`

