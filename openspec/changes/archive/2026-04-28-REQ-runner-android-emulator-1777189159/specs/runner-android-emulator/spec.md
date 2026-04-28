## ADDED Requirements

### Requirement: full runner image ships an Android emulator + bootable system image

The full sisyphus runner image (`runner/Dockerfile`) SHALL include the Android `emulator` binary, the `system-images;android-34;google_apis;x86_64` system image, and a pre-created AVD named `sisyphus-default`. The image MUST allow `emulator -list-avds` to print `sisyphus-default` without further setup. The Go-only runner image (`runner/go.Dockerfile`) is intentionally out of scope and MUST NOT include these payloads.

#### Scenario: RUNNER-AE-S1 emulator binary and default AVD present in full image

- **GIVEN** the full runner image is built from `runner/Dockerfile`
- **WHEN** the container runs `emulator -list-avds`
- **THEN** the command exits 0 and stdout contains `sisyphus-default`

### Requirement: orchestrator mounts /dev/kvm into runner Pod when opt-in flag is set

The sisyphus orchestrator's runner controller SHALL accept a `kvm_enabled` boolean
configuration (sourced from `Settings.runner_kvm_enabled`, default `False`). When
`kvm_enabled` is `True`, every Pod returned by `build_pod(req_id)` MUST include a
`hostPath` volume named `kvm` pointing at the host path `/dev/kvm` with type `CharDevice`,
and the runner container MUST mount it at `/dev/kvm`. When `kvm_enabled` is `False`
(default), the Pod spec MUST NOT contain any `/dev/kvm` volume or mount, preserving
backward compatibility on clusters whose nodes do not expose `/dev/kvm`.

#### Scenario: RUNNER-AE-S2 default off — no /dev/kvm mount

- **GIVEN** a `RunnerController` constructed with default settings (`kvm_enabled` not set)
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod's container `volume_mounts` list contains no entry whose
  `mount_path` equals `/dev/kvm`, and `pod.spec.volumes` contains no volume named `kvm`

#### Scenario: RUNNER-AE-S3 opt-in on — /dev/kvm mounted as CharDevice

- **GIVEN** a `RunnerController` constructed with `kvm_enabled=True`
- **WHEN** the controller calls `build_pod("REQ-1")`
- **THEN** the resulting Pod has a hostPath volume named `kvm` with `path=/dev/kvm` and
  `type=CharDevice`, and the runner container has a corresponding `volume_mount` at
  `/dev/kvm`. Existing mounts (`/workspace`, `/dev/fuse`, `/root/.kube`) are unchanged.

### Requirement: emulator boot helper script ships in /opt/sisyphus/scripts

The full runner image SHALL ship `/opt/sisyphus/scripts/sisyphus-android-emulator.sh`
on `$PATH`. The script MUST support a `boot` sub-command that starts `sisyphus-default`
in headless mode (`-no-window -no-audio -no-snapshot -gpu swiftshader_indirect`) and
blocks until `adb shell getprop sys.boot_completed` returns `1` or a configurable
timeout (default 300 s) elapses. On timeout the script MUST exit non-zero so calling
Makefile targets fail loudly rather than silently proceeding against a half-booted
emulator.

#### Scenario: RUNNER-AE-S4 boot helper exits 0 on completed boot signal

- **GIVEN** the full runner pod is running with `/dev/kvm` mounted
- **WHEN** the agent invokes `sisyphus-android-emulator.sh boot --timeout 300`
- **THEN** the script starts the emulator headless, polls `getprop sys.boot_completed`
  until it reads `1`, prints `[android-emulator] boot completed in <N>s`, and exits 0
