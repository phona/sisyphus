# Proposal: Add uv to sisyphus runner Dockerfile

## Problem

`make ci-lint`, `make ci-unit-test`, and `make ci-integration-test` invoke `uv run`
(ruff / pytest). The runner Dockerfile does not install `uv`, so dev_cross_check and
staging_test fail with `uv: not found` when the business repo uses uv.

## Solution

Copy the `uv` and `uvx` binaries from the official `ghcr.io/astral-sh/uv:latest` image
into the runner image using Docker multi-stage copy. This is the recommended pattern from
the uv project and adds no runtime dependencies.

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
```

## Scope

Single-file change: `runner/Dockerfile`. No contract test is written for this REQ
(docker-build/run contract tests are excluded per REQ scope).

## Risk

Low. The uv binary is statically linked; copying it into the image cannot break existing
toolchain layers (Flutter / Go / openspec).
