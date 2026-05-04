# state-transition-progress-lint

## ADDED Requirements

### Requirement: Transition dataclass exposes a progress field for static no-progress detection

The `Transition` dataclass in `orchestrator/src/orchestrator/state.py` SHALL
expose a `progress` attribute typed `str | None` that defaults to `None`. The
allowed non-None values MUST be exactly the string literals `"yes"`, `"no"`,
and `"explicit-noop"`. The dataclass MUST remain frozen, and the field MUST be
settable as a keyword-only constructor argument.

`progress="yes"` MUST mean "this transition advances the state machine to a
different ReqState". `progress="no"` MUST mean "this transition does not
advance state and is flagged as a deadlock candidate that needs telemetry or
escalation". `progress="explicit-noop"` MUST mean "this is an intentional
self-loop, acknowledged by the author (e.g. SESSION_FAILED action-decides
self-loop)". `progress=None` SHALL be reserved for transitions whose semantic
is auto-derivable from `(src_state, next_state)` —— specifically only allowed
when `next_state != src_state` (lint will derive `"yes"`).

#### Scenario: STPL-S1 Transition accepts progress kwarg with allowed values

- **GIVEN** the `Transition` dataclass imported from `orchestrator.state`
- **WHEN** constructing `Transition(next_state=ReqState.DONE, progress="yes")`
- **THEN** the resulting instance MUST expose `.progress == "yes"`
- **AND** the same MUST hold for `progress="no"` and `progress="explicit-noop"`
- **AND** omitting `progress` MUST set `.progress` to `None`

#### Scenario: STPL-S2 every existing self-loop transition has explicit progress annotation

- **GIVEN** the current `TRANSITIONS` dict in `orchestrator.state`
- **WHEN** iterating each `(src_state, event) → transition` entry
- **THEN** every entry where `transition.next_state == src_state` MUST have
  `transition.progress` set to one of `"no"` or `"explicit-noop"`
- **AND** in particular `TRANSITIONS[(ReqState.ESCALATED, Event.VERIFY_ESCALATE)].progress`
  MUST equal `"explicit-noop"`
- **AND** `TRANSITIONS[(ReqState.REVIEW_RUNNING, Event.VERIFY_INFRA_RETRY)].progress`
  MUST equal `"explicit-noop"`
- **AND** every `(state, Event.SESSION_FAILED)` entry MUST have
  `progress == "explicit-noop"`

### Requirement: scripts/lint-state-transitions.py validates the TRANSITIONS table

The repository SHALL provide an executable script at
`scripts/lint-state-transitions.py` that, when invoked from the repo root with
no arguments, MUST import `TRANSITIONS` from `orchestrator.state`, iterate every
entry, and apply the following validation rules:

1. **Self-loop annotation rule**: when `transition.next_state == src_state`,
   `transition.progress` MUST be `"no"` or `"explicit-noop"`. If `progress`
   is `None` or `"yes"`, the script MUST exit non-zero and print a line
   identifying the offending `(state, event)` pair.
2. **Advancing-transition consistency rule**: when
   `transition.next_state != src_state`, `transition.progress` MUST NOT be
   `"no"` or `"explicit-noop"`. If it is, the script MUST exit non-zero and
   print the offending `(state, event)` pair.
3. **Allowed value rule**: any non-None `progress` value other than
   `"yes"` / `"no"` / `"explicit-noop"` MUST cause the script to exit non-zero.

When all entries pass validation, the script MUST exit zero and MUST print a
human-readable report containing per-bucket counts (yes / no / explicit-noop)
and a list of every `(state, event) → next_state` pair that has
`progress="no"` or `progress="explicit-noop"` so a human reviewer can audit
intentional vs unacknowledged no-progress transitions in one glance.

#### Scenario: STPL-S3 lint script exits zero on current TRANSITIONS table

- **GIVEN** the `scripts/lint-state-transitions.py` script and the current
  `orchestrator/src/orchestrator/state.py`
- **WHEN** running `python3 scripts/lint-state-transitions.py` from repo root
- **THEN** the process exit code MUST be 0
- **AND** stdout MUST contain a line matching the substring `progress=yes`
- **AND** stdout MUST contain a line matching the substring `progress=explicit-noop`
- **AND** stdout MUST contain a line listing
  `(escalated, verify.escalate) → escalated` under `explicit-noop`

#### Scenario: STPL-S4 lint script flags an unannotated self-loop

- **GIVEN** a synthetic `TRANSITIONS` dict that contains
  `(ReqState.DONE, Event.PR_MERGED) → Transition(ReqState.DONE)` with no
  `progress` field set
- **WHEN** the lint module's validation function is called against that dict
- **THEN** the function MUST return a non-empty list of violations
- **AND** the violation MUST identify the `(done, pr.merged)` pair
- **AND** the violation message MUST mention "self-loop requires progress
  annotation"

#### Scenario: STPL-S5 lint script flags a contradictory progress=yes self-loop

- **GIVEN** a synthetic transition entry
  `(ReqState.INIT, Event.INTENT_ANALYZE) → Transition(ReqState.INIT, progress="yes")`
- **WHEN** the lint validation function is called against the dict
- **THEN** the function MUST return a violation referencing
  "progress=yes contradicts self-loop"

#### Scenario: STPL-S6 lint script flags an unknown progress value

- **GIVEN** a synthetic transition with `progress="maybe"`
- **WHEN** the lint validation function is called
- **THEN** the function MUST return a violation referencing the invalid value

### Requirement: Makefile ci-lint and orchestrator-ci.yml invoke the lint

`Makefile`'s `ci-lint` target SHALL invoke the lint script as part of its
sequence so that `make ci-lint` (the contract used by sisyphus
`dev_cross_check` checker per `docs/integration-contracts.md`) returns
non-zero whenever a TRANSITIONS violation is introduced.

`.github/workflows/orchestrator-ci.yml` SHALL include a step that runs
`python3 scripts/lint-state-transitions.py` after the existing ruff step in
the `lint-test` job, so PR CI catches violations independently of the
sisyphus checker.

#### Scenario: STPL-S7 ci-lint target invokes lint-state-transitions.py

- **GIVEN** the repo root
- **WHEN** running `make -n ci-lint` (dry-run print)
- **THEN** the printed recipe MUST contain the literal substring
  `lint-state-transitions.py`

#### Scenario: STPL-S8 orchestrator-ci.yml runs the lint script

- **GIVEN** the file `.github/workflows/orchestrator-ci.yml`
- **WHEN** parsing it as YAML
- **THEN** the `jobs.lint-test.steps` array MUST contain at least one step
  whose `run` field references `scripts/lint-state-transitions.py`
