# Tasks: REQ-runner-add-kubectl-only-1777138424

## Stage: spec

- [x] author specs/runner-kubectl/spec.md with Dockerfile-content scenarios

## Stage: implementation

- [x] add kubectl download step to runner/Dockerfile (step 3, pinned KUBECTL_VERSION=v1.31.4)
- [x] add kubectl download step to runner/go.Dockerfile (step 2, same version)
- [x] renumber section headers in both Dockerfiles to keep sequential numbering

## Stage: PR

- [x] git push feat/REQ-runner-add-kubectl-only-1777138424
- [x] gh pr create
