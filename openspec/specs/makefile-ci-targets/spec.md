# makefile-ci-targets Specification

## Purpose
TBD - created by archiving change REQ-makefile-ci-targets-1777110320. Update Purpose after archive.
## Requirements
### Requirement: 顶层 Makefile MUST 提供 ttpos-ci 标准 ci-lint target

The repo-root `Makefile` SHALL define a `ci-lint` target that conforms to the
ttpos-ci contract documented in `docs/integration-contracts.md §2.1`. The target
MUST honor the `BASE_REV` environment variable: when `BASE_REV` is non-empty,
the target MUST scope ruff to Python files changed between `BASE_REV..HEAD`
under `orchestrator/src` or `orchestrator/tests`; when `BASE_REV` is empty or
the scoped change set contains zero Python files, the target MUST fall back to
a full `cd orchestrator && uv run ruff check src/ tests/`. The target MUST exit
with code 0 on success and a non-zero code on lint failure, so that
`orchestrator/src/orchestrator/checkers/dev_cross_check.py` can use exit code
as the sole pass/fail signal.

#### Scenario: MFCT-S1 ci-lint with empty BASE_REV runs full ruff scan

- **GIVEN** `BASE_REV` env unset (or empty string)
- **WHEN** developer / dev_cross_check runs `make ci-lint` from repo root
- **THEN** the recipe invokes `cd orchestrator && uv run ruff check src/ tests/`
  and propagates ruff's exit code; on a clean tree exit code is 0

#### Scenario: MFCT-S2 ci-lint with non-empty BASE_REV scopes to changed Python files

- **GIVEN** `BASE_REV=<sha>` and `git diff --name-only $BASE_REV...HEAD` lists
  `orchestrator/src/orchestrator/state.py` and `README.md`
- **WHEN** `make ci-lint` runs
- **THEN** ruff is invoked only on `src/orchestrator/state.py` (relative to
  `orchestrator/`), and `README.md` is NOT passed to ruff

#### Scenario: MFCT-S3 ci-lint with non-empty BASE_REV but zero in-scope Python changes exits 0

- **GIVEN** `BASE_REV=<sha>` and the diff contains only `Makefile` and
  `docs/architecture.md`
- **WHEN** `make ci-lint` runs
- **THEN** the target prints a "no Python files changed in scope" message and
  exits 0 without invoking ruff (ttpos-ci 仅 lint 变更文件 语义)

### Requirement: 顶层 Makefile MUST 提供 ttpos-ci 标准 ci-unit-test target

The repo-root `Makefile` SHALL define a `ci-unit-test` target. The target MUST
invoke `cd orchestrator && uv run pytest -m "not integration"` so that any test
carrying `@pytest.mark.integration` is excluded from this target's run. The
target MUST exit with the underlying pytest exit code, so that
`orchestrator/src/orchestrator/checkers/staging_test.py` can use exit code as
the sole pass/fail signal.

#### Scenario: MFCT-S4 ci-unit-test runs full pytest suite excluding integration marker

- **GIVEN** the orchestrator test suite contains 500 unit tests and 0 tests
  marked `@pytest.mark.integration`
- **WHEN** `make ci-unit-test` runs
- **THEN** all 500 tests are collected and executed, exit code 0

#### Scenario: MFCT-S5 ci-unit-test skips a test marked @pytest.mark.integration

- **GIVEN** a hypothetical `@pytest.mark.integration` test exists in
  `orchestrator/tests/`
- **WHEN** `make ci-unit-test` runs
- **THEN** the integration-marked test is NOT collected (deselected by `-m "not integration"`)

### Requirement: 顶层 Makefile MUST 提供 ttpos-ci 标准 ci-integration-test target

The repo-root `Makefile` SHALL define a `ci-integration-test` target. The
target MUST invoke `cd orchestrator && uv run pytest -m integration`. Because
sisyphus today has zero tests carrying the `integration` marker, the target
MUST treat pytest exit code 5 (no tests collected) as pass — i.e. exit 0. Any
other non-zero pytest exit code MUST propagate as failure. The target's
existence is required by `staging_test._build_cmd` which skips the entire repo
when either `ci-unit-test:` or `ci-integration-test:` is missing from the
Makefile.

#### Scenario: MFCT-S6 ci-integration-test passes when zero integration tests collected

- **GIVEN** zero tests in `orchestrator/tests/` carry `@pytest.mark.integration`
- **WHEN** `make ci-integration-test` runs
- **THEN** pytest exits with code 5; the Makefile recipe maps this to exit 0
  (placeholder semantics for the bootstrap state)

#### Scenario: MFCT-S7 ci-integration-test surfaces real test failure

- **GIVEN** at least one `@pytest.mark.integration` test exists and that test
  fails with assertion error
- **WHEN** `make ci-integration-test` runs
- **THEN** pytest exits with code 1; the Makefile recipe MUST exit non-zero
  (NOT swallow the failure)

#### Scenario: MFCT-S8 ci-integration-test runs collected integration tests

- **GIVEN** at least one `@pytest.mark.integration` test exists and passes
- **WHEN** `make ci-integration-test` runs
- **THEN** that test is collected and executed; pytest exits 0; recipe exits 0

### Requirement: pyproject.toml MUST register the integration pytest marker

The file `orchestrator/pyproject.toml` SHALL declare the `integration` marker
under `[tool.pytest.ini_options].markers` so that pytest does not emit
`PytestUnknownMarkWarning` when a test is decorated `@pytest.mark.integration`.
The declaration MUST include a human-readable description so future developers
understand the marker's role in `ci-unit-test` / `ci-integration-test` split.

#### Scenario: MFCT-S9 integration marker is registered in pyproject.toml

- **GIVEN** `orchestrator/pyproject.toml` is parsed
- **WHEN** the `[tool.pytest.ini_options]` section is read
- **THEN** the `markers` array contains an entry starting with the literal
  `integration:` followed by a description

### Requirement: 历史 dev-cross-check / ci-test target MUST 被移除

The repo-root `Makefile` SHALL NOT define `dev-cross-check` or `ci-test` after
this change. These targets pre-dated the ttpos-ci contract and are superseded
by `ci-lint` and `ci-unit-test` respectively; `grep -r 'make dev-cross-check\|make ci-test'`
across the repo returns zero hits, confirming there are no callers to break.
Removing them prevents double-tracked targets that drift over time.

#### Scenario: MFCT-S10 dev-cross-check target is gone

- **GIVEN** the repo at the head of feat/REQ-makefile-ci-targets-1777110320
- **WHEN** `make -n dev-cross-check` runs
- **THEN** make exits non-zero with "No rule to make target 'dev-cross-check'"

#### Scenario: MFCT-S11 ci-test target is gone

- **GIVEN** the repo at the head of feat/REQ-makefile-ci-targets-1777110320
- **WHEN** `make -n ci-test` runs
- **THEN** make exits non-zero with "No rule to make target 'ci-test'"

