## ADDED Requirements

### Requirement: runner Dockerfiles include kubectl binary from dl.k8s.io

Both `runner/Dockerfile` and `runner/go.Dockerfile` SHALL include a `RUN` instruction
that downloads the `kubectl` binary from `https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl`,
installs it to `/usr/local/bin/kubectl`, and marks it executable. The built images
MUST provide a working `kubectl` client so that runner-pod steps that invoke
`kubectl` against the cluster (accept-stage, staging-test) do not fail with
`kubectl: not found`.

#### Scenario: RUNNER-KUBECTL-S1 Dockerfile declares kubectl download instruction

- **GIVEN** the file `runner/Dockerfile` in the sisyphus repository
- **WHEN** the file content is inspected
- **THEN** it contains `https://dl.k8s.io/release/` and `/usr/local/bin/kubectl`

#### Scenario: RUNNER-KUBECTL-S2 go.Dockerfile declares kubectl download instruction

- **GIVEN** the file `runner/go.Dockerfile` in the sisyphus repository
- **WHEN** the file content is inspected
- **THEN** it contains `https://dl.k8s.io/release/` and `/usr/local/bin/kubectl`

#### Scenario: RUNNER-KUBECTL-S3 kubectl step appears before openspec in Dockerfile layer order

- **GIVEN** the file `runner/Dockerfile` in the sisyphus repository
- **WHEN** the file content is parsed for layer ordering
- **THEN** the kubectl download instruction appears before the openspec npm install instruction
