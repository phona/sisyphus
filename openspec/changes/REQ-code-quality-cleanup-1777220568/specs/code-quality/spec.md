## ADDED Requirements

### Requirement: orchestrator src tree carries no dead parameters or unused-variable lint findings

The `orchestrator/src/orchestrator/` Python tree SHALL be free of
parameter-shaped dead code as defined by two complementary signals: ruff
under the project's existing `pyproject.toml` selection
(`["E", "F", "I", "B", "UP", "RUF"]` minus the project's ignore list)
MUST report zero findings, and `vulture src/ --min-confidence 100` MUST
report zero findings. In particular, public-facing helpers MUST NOT
declare parameters with default values that are never read inside the
function body and never passed by any caller; protocol slots (such as
`__aexit__(self, *exc, ...)`) that intentionally discard their arguments
MUST use a leading-underscore name (`*_exc`, `*_a`, `*_`) so the intent
is explicit and the linter / vulture do not flag them.

This requirement scopes only `orchestrator/src/`. Test code under
`orchestrator/tests/` is out of scope: pytest fixtures, monkeypatched
lambdas, and mock async-context-manager stubs routinely carry parameters
named for protocol matching that vulture cannot see through.

#### Scenario: CQ-S1 ruff under project config reports zero findings on src + tests

- **GIVEN** a fresh checkout of the repository at the merge of this REQ
- **WHEN** an operator runs `cd orchestrator && uv run ruff check src/ tests/`
- **THEN** the command exits 0 and prints `All checks passed!` with no
  remaining unused-noqa, unused-import, or unused-variable diagnostics

#### Scenario: CQ-S2 vulture at 100 % confidence reports zero findings on src

- **GIVEN** a fresh checkout of the repository at the merge of this REQ
- **WHEN** an operator runs
  `cd orchestrator && uv tool run vulture src/ --min-confidence 100`
- **THEN** the command exits 0 with empty stdout, demonstrating no
  fully-unused parameters or variables remain in the source tree

#### Scenario: CQ-S3 derive_event signature accepts exactly event_type and tags

- **GIVEN** the `orchestrator.router` module imported from a fresh
  checkout at the merge of this REQ
- **WHEN** a caller invokes
  `inspect.signature(orchestrator.router.derive_event).parameters`
- **THEN** the resulting mapping contains exactly two keys, `event_type`
  and `tags`, with no `result_tags_only` parameter present, demonstrating
  the dead parameter has been removed without breaking the two-arg call
  sites used in `webhook.py` and the test suite
