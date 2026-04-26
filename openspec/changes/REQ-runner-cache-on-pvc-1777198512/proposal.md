# fix(runner): redirect Go/npm/uv cache to PVC

## Why

The runner Pod's toolchain caches default to paths that live on the container's
writable layer (ephemeral storage), not on the per-REQ PVC mounted at
`/workspace`:

- Go modules: `$GOPATH/pkg/mod` — `/root/go/pkg/mod` (image baked `GOPATH=/root/go`).
- Go build cache: `~/.cache/go-build`.
- npm: `~/.npm`.
- uv: `~/.cache/uv`.

Two operational pains follow:

1. **Cache lost on Pod restart.** A K8s OOMKill / node migration / `runner.pause()`
   destroys the container; the PVC at `/workspace` survives but the caches don't.
   Every subsequent `dev_cross_check` / `staging_test` re-downloads modules and
   re-compiles from cold, padding minutes onto each retry.
2. **Node ephemeral-storage pressure.** Caches accumulate on `/var/lib/kubelet`
   (or the docker overlay) and aren't visible to `runner_gc` because they're not
   in any PVC. On vm-node04 (single-node K3s, ~50 GB ephemeral) several
   concurrent runners can push the node to disk-pressure eviction.

Redirecting the four big toolchain caches into `/workspace/.cache/...` makes
them PVC-resident: they survive Pod restarts within the same REQ, get GCd along
with the PVC at REQ done/escalate, and stop bleeding into shared node storage.

## What Changes

- **`orchestrator/src/orchestrator/k8s_runner.py`** — `build_pod()` injects four
  new env vars on every runner container:
  - `GOMODCACHE=/workspace/.cache/go/mod`
  - `GOCACHE=/workspace/.cache/go/build`
  - `npm_config_cache=/workspace/.cache/npm`
  - `UV_CACHE_DIR=/workspace/.cache/uv`

  No volume / mount changes — `/workspace` is already mounted from the per-REQ
  PVC.
- **`orchestrator/tests/test_k8s_runner.py`** — new
  `test_build_pod_redirects_toolchain_caches_to_pvc` assertion locking the four
  env values down so a future refactor can't silently revert them.

Image, Dockerfile, helm chart, CLI tools, and `sisyphus-clone-repos.sh` are
**not** touched: the convention lives entirely in the Pod spec.

## Impact

- **Affected specs**: new capability `runner-cache-on-pvc` (purely additive).
- **Affected code**: `orchestrator/src/orchestrator/k8s_runner.py`,
  `orchestrator/tests/test_k8s_runner.py`.
- **Deployment / migration**: zero ops — orchestrator rollout-restart picks up
  the new Pod template; existing in-flight Pods keep running with the old
  (ephemeral) cache layout until their REQ ends. PVC size requests are
  unchanged (default `10Gi`); cache footprint for a typical REQ is well under
  1 GB across all four toolchains, so existing PVCs comfortably absorb it.
- **Risk**: low. Env vars are honored by every supported version of `go`,
  `npm`, and `uv`; they take precedence over default paths, so the change is
  purely "where do bytes land" with no behavioral side-effects on the tools
  themselves.
- **Out of scope**: cross-REQ cache sharing (would need a separate RWX
  PV / something like a node-local hostPath cache) — different design, larger
  blast radius, deferred.
