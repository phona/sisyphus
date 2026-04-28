# Design: runner android emulator + KVM

## Decision tree

### 1. Which runner flavor gets the emulator?

**Decision**: full Flutter flavor only (`runner/Dockerfile`).

Rationale: the Go-only flavor (`runner/go.Dockerfile`) is ~1 GB by design and serves Go
business repos that have no use for an Android device. Adding the system image + emulator
to it would balloon it to ~2.5 GB for zero benefit. Mobile REQs already opt in to the full
flavor via `runner_image: ghcr.io/phona/sisyphus-runner:main`.

The `sisyphus-android-emulator.sh` helper script is shipped to `/opt/sisyphus/scripts/`
in both flavors so the path is uniform; on the Go flavor the script will fail fast with
`emulator: command not found`, which is the desired clear error.

### 2. Which Android API level + system image variant?

**Decision**: `system-images;android-34;google_apis;x86_64` (Android 14, no Google Play).

- API 34 matches what current Flutter `stable` channel is built against; older API levels
  don't expose the same `androidx.activity` APIs that recent ttpos-flutter REQs already
  rely on.
- `google_apis` (without `playstore`) is sufficient for instrumentation tests (most apps
  need Google Maps / Location stubs but not the Play Store gate). The `playstore` variant
  is locked to Google-signed userdata that complicates `adb root` and is licensed
  differently.
- `x86_64` (not `arm64-v8a`): vm-node04 is amd64; running an arm64 image under QEMU TCG
  is an order of magnitude slower than x86_64 with KVM. ARM hosts are not in scope.

### 3. KVM access path: device plugin vs hostPath?

**Decision**: hostPath mount at `/dev/kvm` (same shape as existing `/dev/fuse`).

- The runner Pod is already `privileged: true` (DinD prerequisite), so adding a
  character-device hostPath mount is straightforward and consistent with the `/dev/fuse`
  precedent in the same file.
- A K8s device plugin (e.g. `kubevirt/kvm-device-plugin`) would be cleaner architecturally
  but adds a cluster-wide DaemonSet just for this REQ. Sisyphus runs on a single-node K3s
  on vm-node04; the cost/benefit of a device plugin doesn't pencil out.
- **The mount is opt-in via a new orchestrator setting** (`runner_kvm_enabled`, default
  `false`). On nodes that don't expose `/dev/kvm` (e.g. nested virt without host KVM),
  enabling the flag would make Pod creation fail with `MountVolume.SetUp failed`. Keeping
  it off by default preserves the current "single-node K3s" zero-config experience.

### 4. AVD pre-creation in the image?

**Decision**: pre-create one AVD `sisyphus-default` at build time (no userdata snapshot).

- Pre-creating shaves ~10 s off each REQ's first emulator boot (the SDK has to lay out
  ~200 MB of system image either way; pre-doing it puts the cost in image build, not test
  runtime).
- We do **not** pre-create a "snapshot" of a booted state — snapshots are tightly bound
  to the kernel/QEMU versions and break across image rebuilds in subtle ways. Cold boot
  with KVM is fast enough.
- REQs that need a custom AVD (different screen size, locale, etc.) can still
  `avdmanager create avd` at runtime — `sisyphus-default` is just a known-good baseline.

### 5. Helper script vs document `emulator` flags in prose?

**Decision**: ship `scripts/sisyphus-android-emulator.sh` with the canonical "headless
boot + wait until ready" flags.

The flag set that actually works in a privileged container without an X server is a
3-line incantation that every REQ would otherwise re-derive:

```
emulator -avd sisyphus-default \
  -no-window -no-audio -no-snapshot \
  -gpu swiftshader_indirect \
  -no-boot-anim -accel on &
adb wait-for-device
adb shell 'while [[ "$(getprop sys.boot_completed)" != "1" ]]; do sleep 1; done'
```

Encoding this once in a helper script keeps every business-repo Makefile a one-liner
(`/opt/sisyphus/scripts/sisyphus-android-emulator.sh boot`). Mirrors the existing
`sisyphus-clone-repos.sh` pattern.

## Why an opt-in flag instead of "always on"

Three reasons:

1. **Backward compat**: existing dev / staging deployments don't necessarily have
   `/dev/kvm` exposed (some K8s distros run inside containers themselves). Defaulting on
   would break those deployments at next Pod rollover.
2. **Trust boundary**: `/dev/kvm` is a host device. We pass it into a privileged container
   anyway, but the *flag* documents intent — every operator opting in has to acknowledge
   that the runner Pod can spawn KVM-accelerated VMs.
3. **Symmetry with `kvmEnabled` upstream conventions**: KubeVirt, Tekton's Android
   pipelines, and `actions/runner-images` all gate KVM access behind a flag, not behind
   privileged context alone.

## Tested invariants (covered by unit tests)

- `build_pod(req)` with default `kvm_enabled=False` produces a Pod whose `volume_mounts`
  list does **not** contain `/dev/kvm`. Backward-compat assertion.
- `RunnerController(..., kvm_enabled=True).build_pod(req)` produces a Pod with a
  `kvm` hostPath volume of type `CharDevice`, mounted at `/dev/kvm`.
- Existing `/dev/fuse`, `/workspace`, `/root/.kube` mounts are untouched in both modes.

## Out of scope

- Changing the default `runner_image` from `:go` to `:main`. That decision is made
  per-REQ via the prompt template; this REQ only adds the *capability* to the full
  image, not the routing of which image to pick.
- iOS simulators (macOS-only; not on Linux runner pod).
- KVM nested-virtualization tuning on the host (`kvm-intel.nested=1` kernel parameter).
  Documented as an operator prerequisite in the helm comment, not enforced.
