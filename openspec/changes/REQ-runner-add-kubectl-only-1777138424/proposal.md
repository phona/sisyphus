# Proposal: add kubectl to runner image

## Problem

Runner pods have `KUBECONFIG=/root/.kube/config` injected (via `runner-secrets`), which gives access to the K3s cluster. However, the `kubectl` binary is not present in the runner image (`runner/Dockerfile` and `runner/go.Dockerfile`). Any agent step that calls `kubectl` inside the runner pod fails with `kubectl: not found`.

This blocks:
- accept-stage agents running `kubectl` to inspect or manage cluster resources
- staging-test agents that need to verify pod/service state during tests

## Solution

Add a `kubectl` download step to both runner Dockerfiles, pinned to `v1.31.4` via `ARG KUBECTL_VERSION`. Fetch the binary from `dl.k8s.io` (official release endpoint), place it at `/usr/local/bin/kubectl`, and verify with `kubectl version --client` at build time.

No changes to orchestrator code, helm values, or runtime scripts.

## Scope

- `runner/Dockerfile` — full Flutter runner
- `runner/go.Dockerfile` — Go-only lightweight runner

Both images receive the same `kubectl` version.
