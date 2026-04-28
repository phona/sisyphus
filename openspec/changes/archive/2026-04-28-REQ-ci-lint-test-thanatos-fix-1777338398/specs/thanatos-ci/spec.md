## ADDED Requirements

### Requirement: ci-integration-test must not fail due to thanatos import errors

The root `Makefile`'s `ci-integration-test` target SHALL NOT invoke pytest in the
`thanatos/` subdirectory. Thanatos integration tests MUST be managed by thanatos'
own CI workflow. The `make ci-integration-test` command MUST exit 0 when no
PostgreSQL is reachable and MUST map pytest exit 5 to exit 0 for the orchestrator.

#### Scenario: TCIF-S1 ci-integration-test exits 0 without PostgreSQL

- **GIVEN** the sisyphus repo on any branch with no PostgreSQL running
- **WHEN** `make ci-integration-test` is executed from the repo root
- **THEN** the command exits with code 0

#### Scenario: TCIF-S3 thanatos-ci workflow provides GHA check-runs for thanatos PRs

The sisyphus project MUST have a `.github/workflows/thanatos-ci.yml` workflow
that triggers on `thanatos/**` changes and MUST produce GitHub Actions check-runs
so that `pr_ci_watch` does not see `no-gha` for thanatos PRs.

#### Scenario: TCIF-S4 uv run pytest succeeds in thanatos directory

- **GIVEN** a fresh checkout with no pre-installed packages
- **WHEN** `cd thanatos && uv run pytest -m "not integration"` is executed
- **THEN** all 15 unit tests pass with exit code 0
