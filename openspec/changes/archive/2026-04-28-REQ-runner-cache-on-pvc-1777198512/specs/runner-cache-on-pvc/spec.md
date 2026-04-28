## ADDED Requirements

### Requirement: runner Pod redirects Go / npm / uv toolchain caches onto the per-REQ PVC

The sisyphus orchestrator's `RunnerController.build_pod(req_id)` SHALL inject
four cache-location environment variables into every runner container, and
each value MUST point inside `/workspace/.cache/` so the cache is written to
the PVC mounted at `/workspace` rather than the container's writable layer.
Future cache lookups by `go`, `npm`, and `uv` MUST therefore land on
PVC-backed storage that survives Pod restarts within the same REQ. The exact
mapping is:

| Env var            | Value                          | Tool covered           |
|--------------------|--------------------------------|------------------------|
| `GOMODCACHE`       | `/workspace/.cache/go/mod`     | Go module download cache |
| `GOCACHE`          | `/workspace/.cache/go/build`   | Go build cache           |
| `npm_config_cache` | `/workspace/.cache/npm`        | npm package cache        |
| `UV_CACHE_DIR`     | `/workspace/.cache/uv`         | uv package cache         |

The runner container's existing `/workspace` PVC mount MUST remain the only
volume backing these paths — no new volume, mount, or hostPath is introduced.
Cross-REQ cache sharing is explicitly out of scope: each REQ's PVC is its
own cache scope and cleanup happens implicitly when the PVC is deleted at
REQ done / escalate.

#### Scenario: RUNNER-CACHE-S1 GOMODCACHE points into /workspace/.cache

- **GIVEN** a `RunnerController` constructed with default settings
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod's container `env` list contains an entry whose
  `name` is `GOMODCACHE` and whose `value` is exactly `/workspace/.cache/go/mod`

#### Scenario: RUNNER-CACHE-S2 GOCACHE points into /workspace/.cache

- **GIVEN** a `RunnerController` constructed with default settings
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod's container `env` list contains an entry whose
  `name` is `GOCACHE` and whose `value` is exactly `/workspace/.cache/go/build`

#### Scenario: RUNNER-CACHE-S3 npm_config_cache points into /workspace/.cache

- **GIVEN** a `RunnerController` constructed with default settings
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod's container `env` list contains an entry whose
  `name` is `npm_config_cache` and whose `value` is exactly
  `/workspace/.cache/npm`

#### Scenario: RUNNER-CACHE-S4 UV_CACHE_DIR points into /workspace/.cache

- **GIVEN** a `RunnerController` constructed with default settings
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod's container `env` list contains an entry whose
  `name` is `UV_CACHE_DIR` and whose `value` is exactly `/workspace/.cache/uv`
