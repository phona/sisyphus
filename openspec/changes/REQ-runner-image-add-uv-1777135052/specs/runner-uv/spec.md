## ADDED Requirements

### Requirement: runner image includes uv binary

The runner image SHALL include the `uv` Python package manager binary at
`/usr/local/bin/uv` so that `uv run ruff check` and `uv run pytest` MUST succeed
when invoked inside the runner container.

#### Scenario: RUNNER-UV-S1 uv binary is present and executable in runner image
- **GIVEN** the sisyphus runner image has been built from runner/Dockerfile
- **WHEN** a process inside the container runs `uv --version`
- **THEN** the command exits 0 and prints a version string matching `uv \d+\.\d+`

#### Scenario: RUNNER-UV-S2 ci-lint target succeeds in runner container
- **GIVEN** the runner container has the sisyphus source repo mounted at /workspace/source/sisyphus
- **WHEN** `make ci-lint` is executed (no BASE_REV, full scan)
- **THEN** the command exits 0 without `uv: not found` errors
