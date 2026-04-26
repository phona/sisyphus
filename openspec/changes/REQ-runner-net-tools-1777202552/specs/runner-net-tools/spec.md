## ADDED Requirements

### Requirement: runner images guarantee iproute2 + net-tools userland

Both sisyphus runner images SHALL ship with the `iproute2` and `net-tools`
Debian/Ubuntu packages pre-installed in the base image layer, where "both"
means the Flutter-flavored `ghcr.io/phona/sisyphus-runner` built from
`runner/Dockerfile` and the Go-only `ghcr.io/phona/sisyphus-runner-go` built
from `runner/go.Dockerfile`. Consequently, the binaries `ip`, `ss`, `netstat`,
and `ifconfig` MUST be resolvable on the default `PATH` inside any runner Pod
started from either image, without any further apt-get or runtime installation
step. The packages MUST be installed in the same `apt-get install` invocation
as the rest of the section §1 base utilities so they share that layer's cache
scope and image tagging cadence.

#### Scenario: RUNNER-NET-S1 ip command resolves on Flutter runner

- **GIVEN** a Pod started from `ghcr.io/phona/sisyphus-runner:<tag>`
- **WHEN** an operator runs `kubectl exec <pod> -- command -v ip`
- **THEN** the command exits 0 and prints a path under `/usr/sbin/` or
  `/sbin/`, demonstrating `iproute2` is on `PATH`

#### Scenario: RUNNER-NET-S2 ss command resolves on Go runner

- **GIVEN** a Pod started from `ghcr.io/phona/sisyphus-runner-go:<tag>`
- **WHEN** an operator runs `kubectl exec <pod> -- command -v ss`
- **THEN** the command exits 0 and prints a path under `/usr/sbin/` or
  `/usr/bin/`, demonstrating `iproute2` is on `PATH`

#### Scenario: RUNNER-NET-S3 netstat command resolves on both runners

- **GIVEN** a Pod started from either runner image variant
- **WHEN** an operator runs `kubectl exec <pod> -- command -v netstat`
- **THEN** the command exits 0 and prints a path under `/bin/` or
  `/usr/bin/`, demonstrating `net-tools` is on `PATH`

#### Scenario: RUNNER-NET-S4 ifconfig command resolves on both runners

- **GIVEN** a Pod started from either runner image variant
- **WHEN** an operator runs `kubectl exec <pod> -- command -v ifconfig`
- **THEN** the command exits 0 and prints a path under `/sbin/` or
  `/usr/sbin/`, demonstrating `net-tools` is on `PATH`
