# Tasks: REQ-runner-image-add-uv-1777135052

## Stage: contract / spec
- [x] author specs/runner-uv/spec.md with ADDED Requirements and scenarios

## Stage: implementation
- [x] add `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/` to runner/Dockerfile (step 3, before openspec CLI)
- [x] renumber subsequent Dockerfile section comments (4, 5, 6)

## Stage: PR
- [x] git push feat/REQ-runner-image-add-uv-1777135052
- [x] gh pr create
