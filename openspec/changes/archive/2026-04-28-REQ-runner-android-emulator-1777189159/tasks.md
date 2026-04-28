# tasks: REQ-runner-android-emulator-1777189159

## Stage: contract / spec

- [x] author `specs/runner-android-emulator/spec.md` with delta `## ADDED Requirements`
- [x] write 4 scenarios `RUNNER-AE-S{1..4}` covering: emulator binary present in full
      image, KVM device mount toggle (default off, opt-in on), helper script boot

## Stage: implementation

- [x] `runner/Dockerfile`: install `qemu-kvm cpu-checker`, run
      `sdkmanager "emulator" "system-images;android-34;google_apis;x86_64"`,
      pre-create AVD `sisyphus-default` via `avdmanager`
- [x] `runner/go.Dockerfile`: **not modified** (decision §1)
- [x] new `scripts/sisyphus-android-emulator.sh` (boot/wait/halt sub-commands)
- [x] `scripts/sisyphus-android-emulator.sh` shipped via `COPY` in both Dockerfiles
      (Go flavor will fail fast with clear error if invoked — by design)
- [x] `orchestrator/src/orchestrator/k8s_runner.py`:
      add `kvm_enabled: bool = False` ctor param; in `build_pod` conditionally append
      `/dev/kvm` hostPath volume + mount
- [x] `orchestrator/src/orchestrator/config.py`: add `runner_kvm_enabled: bool = False`
- [x] `orchestrator/src/orchestrator/main.py`: pass `kvm_enabled=settings.runner_kvm_enabled`
      to `RunnerController(...)`
- [x] `orchestrator/helm/values.yaml`: add `runner.kvmEnabled: false` with operator
      prerequisite comment
- [x] `orchestrator/helm/templates/deployment.yaml`: add `SISYPHUS_RUNNER_KVM_ENABLED`
      env entry
- [x] unit tests in `orchestrator/tests/test_k8s_runner.py`:
      - `test_build_pod_no_kvm_mount_by_default`
      - `test_build_pod_mounts_kvm_when_enabled`
      - `test_build_pod_existing_mounts_unchanged_with_kvm_enabled`
- [x] `runner/README.md`: add Android emulator + KVM section

## Stage: PR

- [x] git push `feat/REQ-runner-android-emulator-1777189159`
- [x] gh pr create
