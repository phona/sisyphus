# Proposal: Add helm to runner image

## Background

sisyphus runner pods need `helm` for the `accept` stage, where `make accept-env-up` / `make accept-env-down`
deploy lab environments via Helm charts on K3s. The runner image currently has `KUBECONFIG` wired to the
K3s cluster but lacks the `helm` binary, causing accept-stage failures when business repos use Helm.

This was identified in PR #79 and split into this focused fix.

## Scope

One repo: `phona/sisyphus`.

Two Dockerfiles:
- `runner/Dockerfile` — Flutter full runner (~5 GB)
- `runner/go.Dockerfile` — Go-only lightweight runner (~1 GB)

Both images must have `helm` installed so that any repo's accept stage can run `helm install/upgrade/uninstall`.

## Approach

Install helm from the official release tarball (pinned version, deterministic):

```dockerfile
ARG HELM_VERSION=3.17.3
RUN curl -fsSL https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz \
      | tar -xz --strip-components=1 -C /usr/local/bin linux-amd64/helm \
    && helm version
```

No apt repository needed; the tarball approach is the same pattern we already use for Go.

## Risk

Low. Helm is a standalone static binary; adding it does not conflict with any existing tooling.
Build time increase: ~10 s per image (tarball download + unpack).
