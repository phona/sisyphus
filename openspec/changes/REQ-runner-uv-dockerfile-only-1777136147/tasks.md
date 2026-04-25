# Tasks: REQ-runner-uv-dockerfile-only-1777136147

## Stage: contract / spec
- [x] author specs/runner-uv/spec.md with ADDED Requirements + scenarios

## Stage: implementation
- [x] add `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/` to runner/Dockerfile

## Stage: PR
- [x] git push feat/REQ-runner-uv-dockerfile-only-1777136147
- [x] gh pr create
