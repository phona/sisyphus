## ADDED Requirements

### Requirement: runner Dockerfile includes uv binary via multi-stage copy

The runner Dockerfile SHALL include a `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/`
instruction so that the built image MUST provide the `uv` and `uvx` executables at
`/usr/local/bin/uv` and `/usr/local/bin/uvx` respectively.

#### Scenario: RUNNER-UV-ONLY-S1 Dockerfile declares uv copy instruction
- **GIVEN** the file `runner/Dockerfile` in the sisyphus repository
- **WHEN** the file content is inspected
- **THEN** it contains `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/`

#### Scenario: RUNNER-UV-ONLY-S2 uv appears before openspec in Dockerfile layer order
- **GIVEN** the file `runner/Dockerfile` in the sisyphus repository
- **WHEN** the file content is parsed for layer ordering
- **THEN** the uv COPY instruction appears before the openspec npm install instruction
