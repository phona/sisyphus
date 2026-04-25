# Proposal: Add uv to sisyphus runner image

## Problem

`make ci-lint`, `make ci-unit-test`, and `make ci-integration-test` in the sisyphus repo
all invoke `uv run` (ruff / pytest). The runner Dockerfile does not install `uv`, so
self-dogfood dev_cross_check and staging_test always fail with `uv: not found`.

## Solution

Copy the `uv` binary from the official `ghcr.io/astral-sh/uv:latest` image into the
runner image. This is the recommended Docker pattern from the uv project and adds no
runtime dependencies or layer overhead beyond the two binaries.

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
```

## Scope

Single-file change: `runner/Dockerfile`. No Python deps, no Makefile changes needed —
the existing `uv run ruff` / `uv run pytest` invocations in the Makefile already handle
`uv sync` implicitly via `uv run`.

## Risk

Low. The uv binary is statically linked; copying it into the image cannot break existing
toolchain layers (Flutter / Go / openspec).
