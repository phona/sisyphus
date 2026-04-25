## ADDED Requirements

### Requirement: runner images include the helm CLI

Both sisyphus runner images (full Flutter runner and Go-only runner) SHALL include the `helm` binary
so that accept-stage Makefile targets that invoke `helm install`, `helm upgrade`, or `helm uninstall`
can execute without error. The version MUST be pinned as a build ARG (`HELM_VERSION`) to ensure
reproducible builds.

#### Scenario: RUNNER-HELM-S1 helm binary present in full runner image

- **GIVEN** the full runner image (runner/Dockerfile) is built
- **WHEN** the container runs `helm version`
- **THEN** the command exits 0 and prints a version string matching `v3.x.y`

#### Scenario: RUNNER-HELM-S2 helm binary present in Go runner image

- **GIVEN** the Go-only runner image (runner/go.Dockerfile) is built
- **WHEN** the container runs `helm version`
- **THEN** the command exits 0 and prints a version string matching `v3.x.y`

#### Scenario: RUNNER-HELM-S3 helm version is pinned via build ARG

- **GIVEN** either runner Dockerfile
- **WHEN** built without overriding `HELM_VERSION`
- **THEN** the installed helm matches the default `HELM_VERSION` value in the Dockerfile
