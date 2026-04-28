# feat: runner add android emulator + KVM

## Why

Mobile / Flutter business repos that consume the **full Flutter runner image** need to run
Android instrumentation tests (and accept-stage acceptance scenarios that drive an Android
app) inside the per-REQ runner Pod. Today the image ships the Android SDK & platform tools
(via `cirruslabs/flutter:stable`) but is missing two pieces:

1. The Android **emulator** binary + a bootable **system image**, so `make ci-integration-test`
   targets that need an emulator fail with `emulator: command not found` before any test runs.
2. **Hardware acceleration access** (`/dev/kvm`). Without KVM the emulator falls back to the
   software CPU, which on a 4-CPU pod limit takes 5–10 min just to boot — well past the
   staging-test timeout. With KVM, cold boot is ~30–60 s.

This REQ closes both gaps in the **full** runner only (Go runner stays lean), and adds an
**opt-in operator switch** (`runner.kvmEnabled`) so the orchestrator only mounts `/dev/kvm`
on clusters whose nodes actually expose it. Default off keeps existing single-node K3s
deployments working unchanged.

## What Changes

- **runner/Dockerfile (full flavor only)**: install KVM userland (`qemu-kvm`,
  `cpu-checker`), install the Android `emulator` package + `system-images;android-34;
  google_apis;x86_64` via the SDK manager already present in the base image, pre-create
  a default AVD `sisyphus-default`. **Go Dockerfile is not touched** — the Go-only flavor
  stays ~1 GB.
- **scripts/sisyphus-android-emulator.sh**: idempotent helper to boot the default AVD
  headless and block until `sys.boot_completed=1`. Shipped into `/opt/sisyphus/scripts/`
  in both runner images (callers in the Go flavor get a clear "emulator: not found" error
  by design — only the full flavor has it).
- **orchestrator runner Pod spec** (`k8s_runner.py`): when the new orchestrator setting
  `runner_kvm_enabled=True`, mount the host's `/dev/kvm` character device read-write into
  the runner Pod (mirroring the existing `/dev/fuse` pattern). Default `False` keeps
  current behavior bit-identical — no `/dev/kvm` mount, no extra host requirement.
- **orchestrator config + helm chart**: new `Settings.runner_kvm_enabled` (default `False`),
  exposed as helm value `runner.kvmEnabled` and wired through the deployment env.
- **runner README + CLAUDE.md** mentions Android emulator usage from the full-flavor pod.

## Impact

- **Affected specs**: new capability `runner-android-emulator` (this is purely additive).
- **Affected code**:
  - `runner/Dockerfile` (extend full flavor only)
  - `scripts/sisyphus-android-emulator.sh` (new)
  - `orchestrator/src/orchestrator/k8s_runner.py` (`__init__`, `build_pod`)
  - `orchestrator/src/orchestrator/config.py` (new `runner_kvm_enabled`)
  - `orchestrator/src/orchestrator/main.py` (pass new param)
  - `orchestrator/helm/values.yaml` + `templates/deployment.yaml` (new env)
  - `orchestrator/tests/test_k8s_runner.py` (mount toggle assertions)
  - `runner/README.md`
- **Deployment / migration**:
  - **Default off** → existing deployments unaffected. No node prerequisites change.
  - To opt in, operator: (a) confirm `kvm-ok` on each runner-eligible node, (b) set
    `runner.kvmEnabled=true` in helm values, (c) `helm upgrade`. The orchestrator picks
    up the new env on rollout-restart and subsequent Pods get `/dev/kvm` mounted.
- **Image size**: full runner grows ~+1.5 GB (system image + emulator packages). Go
  runner unchanged.
- **Risk**: low. The mount is gated; the AVD/system image are passive payload until a
  REQ runs `emulator -avd sisyphus-default`.
