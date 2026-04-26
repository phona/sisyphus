"""Contract tests for REQ-runner-android-emulator-1777189159.

Capability: runner-android-emulator
Author: challenger-agent (black-box, written from spec only)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is genuinely wrong, escalate to spec_fixer to correct the spec.

Scenarios covered:
  RUNNER-AE-S1  full runner image ships emulator binary + system image +
                pre-created AVD 'sisyphus-default'; Go image excludes them
  RUNNER-AE-S2  RunnerController default off — no /dev/kvm volume or mount
                in build_pod output
  RUNNER-AE-S3  RunnerController opt-in on (kvm_enabled=True) — /dev/kvm
                hostPath CharDevice mounted; existing mounts intact
  RUNNER-AE-S4  sisyphus-android-emulator.sh boot sub-command uses headless
                flags, polls sys.boot_completed, prints success message,
                exits non-zero on timeout
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FULL_DOCKERFILE = REPO_ROOT / "runner" / "Dockerfile"
GO_DOCKERFILE = REPO_ROOT / "runner" / "go.Dockerfile"
EMULATOR_SCRIPT = REPO_ROOT / "scripts" / "sisyphus-android-emulator.sh"


def _read(path: Path) -> str:
    assert path.exists(), f"File not found at expected path: {path}"
    return path.read_text()


def _make_controller(kvm_enabled: bool = False):
    from orchestrator.k8s_runner import RunnerController

    return RunnerController(
        core_v1=MagicMock(),
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner-sa",
        runner_secret_name="sisyphus-runner-secrets",
        storage_class="local-path",
        workspace_size="10Gi",
        kvm_enabled=kvm_enabled,
    )


# ── RUNNER-AE-S1: full image ships emulator + system image + AVD ─────────────


def test_runner_ae_s1_full_dockerfile_installs_emulator_package():
    """S1: runner/Dockerfile MUST install the Android 'emulator' package."""
    content = _read(FULL_DOCKERFILE)
    emulator_lines = [ln for ln in content.splitlines() if "emulator" in ln]
    assert any("emulator" in ln for ln in emulator_lines), (
        "runner/Dockerfile must install the Android 'emulator' package via sdkmanager "
        "(spec RUNNER-AE-S1: `emulator -list-avds` must work inside the container).\n"
        "Emulator-related lines found:\n" + "\n".join(emulator_lines)
    )


def test_runner_ae_s1_full_dockerfile_installs_system_image():
    """S1: runner/Dockerfile MUST install system-images;android-34;google_apis;x86_64."""
    content = _read(FULL_DOCKERFILE)
    assert "system-images;android-34;google_apis;x86_64" in content, (
        "runner/Dockerfile must install 'system-images;android-34;google_apis;x86_64' "
        "via sdkmanager (spec RUNNER-AE-S1).\n"
        "System-image-related lines:\n"
        + "\n".join(
            ln for ln in content.splitlines() if "system-image" in ln or "android-34" in ln
        )
    )


def test_runner_ae_s1_full_dockerfile_precreates_sisyphus_default_avd():
    """S1: runner/Dockerfile MUST pre-create an AVD named 'sisyphus-default' at build time."""
    content = _read(FULL_DOCKERFILE)
    assert "sisyphus-default" in content, (
        "runner/Dockerfile must pre-create an AVD named 'sisyphus-default' at image "
        "build time so that 'emulator -list-avds' prints 'sisyphus-default' without "
        "further setup (spec RUNNER-AE-S1).\n"
        "AVD-related lines:\n"
        + "\n".join(
            ln for ln in content.splitlines() if "avd" in ln.lower() or "emulator" in ln.lower()
        )
    )


def test_runner_ae_s1_go_dockerfile_does_not_install_system_image():
    """S1: runner/go.Dockerfile MUST NOT include the Android system image (Go flavor stays lean)."""
    content = _read(GO_DOCKERFILE)
    assert "system-images;android-34" not in content, (
        "runner/go.Dockerfile must NOT install Android system images — "
        "the Go-only runner flavor is intentionally kept lean (spec RUNNER-AE-S1). "
        "Only the full Flutter runner carries the emulator payloads."
    )


def test_runner_ae_s1_go_dockerfile_does_not_precreate_avd():
    """S1: runner/go.Dockerfile MUST NOT pre-create any AVD."""
    content = _read(GO_DOCKERFILE)
    avd_create_lines = [
        ln for ln in content.splitlines()
        if "avdmanager" in ln.lower() and "create" in ln.lower()
    ]
    assert len(avd_create_lines) == 0, (
        "runner/go.Dockerfile must NOT pre-create any AVD — the Android emulator "
        "is intentionally out of scope for the Go-only flavor (spec RUNNER-AE-S1).\n"
        "Offending lines: " + "\n".join(avd_create_lines)
    )


# ── RUNNER-AE-S2: default off — no /dev/kvm mount ───────────────────────────


def test_runner_ae_s2_default_off_no_kvm_volume_mount():
    """S2: build_pod with default kvm_enabled must NOT mount /dev/kvm."""
    controller = _make_controller(kvm_enabled=False)
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    mounts = containers[0].volume_mounts or []
    kvm_mounts = [m for m in mounts if getattr(m, "mount_path", None) == "/dev/kvm"]
    assert len(kvm_mounts) == 0, (
        "Pod built with default kvm_enabled=False must NOT contain a volume_mount "
        f"at /dev/kvm (spec RUNNER-AE-S2). Found: {kvm_mounts}"
    )


def test_runner_ae_s2_default_off_no_kvm_volume():
    """S2: build_pod with default kvm_enabled must NOT include a volume named 'kvm'."""
    controller = _make_controller(kvm_enabled=False)
    pod = controller.build_pod("REQ-1")

    volumes = pod.spec.volumes or []
    kvm_volumes = [v for v in volumes if getattr(v, "name", None) == "kvm"]
    assert len(kvm_volumes) == 0, (
        "Pod built with default kvm_enabled=False must NOT include a volume named 'kvm' "
        f"(spec RUNNER-AE-S2). Found: {kvm_volumes}"
    )


# ── RUNNER-AE-S3: opt-in on — /dev/kvm mounted as CharDevice ─────────────────


def test_runner_ae_s3_kvm_enabled_has_hostpath_volume_at_kvm():
    """S3: build_pod with kvm_enabled=True must include hostPath volume 'kvm' at /dev/kvm."""
    controller = _make_controller(kvm_enabled=True)
    pod = controller.build_pod("REQ-1")

    volumes = pod.spec.volumes or []
    kvm_volumes = [v for v in volumes if getattr(v, "name", None) == "kvm"]
    assert len(kvm_volumes) == 1, (
        "Pod built with kvm_enabled=True must include exactly one volume named 'kvm' "
        f"(spec RUNNER-AE-S3). Volume names: {[getattr(v, 'name', None) for v in volumes]}"
    )
    host_path = getattr(kvm_volumes[0], "host_path", None)
    assert host_path is not None, (
        "Volume 'kvm' must be a hostPath volume (spec RUNNER-AE-S3). "
        f"Got: {kvm_volumes[0]}"
    )
    assert getattr(host_path, "path", None) == "/dev/kvm", (
        f"hostPath volume 'kvm' must have path='/dev/kvm' (spec RUNNER-AE-S3). "
        f"Got path={getattr(host_path, 'path', None)!r}"
    )
    assert getattr(host_path, "type", None) == "CharDevice", (
        f"hostPath volume 'kvm' must have type='CharDevice' (spec RUNNER-AE-S3). "
        f"Got type={getattr(host_path, 'type', None)!r}"
    )


def test_runner_ae_s3_kvm_enabled_has_kvm_volume_mount():
    """S3: build_pod with kvm_enabled=True must mount the 'kvm' volume at /dev/kvm."""
    controller = _make_controller(kvm_enabled=True)
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    mounts = containers[0].volume_mounts or []
    kvm_mounts = [m for m in mounts if getattr(m, "mount_path", None) == "/dev/kvm"]
    assert len(kvm_mounts) == 1, (
        "Pod built with kvm_enabled=True must have exactly one volume_mount at /dev/kvm "
        f"(spec RUNNER-AE-S3). Mount paths: {[getattr(m, 'mount_path', None) for m in mounts]}"
    )
    assert getattr(kvm_mounts[0], "name", None) == "kvm", (
        "The /dev/kvm volume_mount must reference volume named 'kvm' "
        f"(spec RUNNER-AE-S3). Got name={getattr(kvm_mounts[0], 'name', None)!r}"
    )


def test_runner_ae_s3_existing_mounts_unchanged_with_kvm():
    """S3: Enabling kvm_enabled=True must NOT remove /workspace, /dev/fuse, /root/.kube mounts."""
    controller = _make_controller(kvm_enabled=True)
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    mounts = containers[0].volume_mounts or []
    mount_paths = {getattr(m, "mount_path", None) for m in mounts}

    for required_path in ["/workspace", "/dev/fuse", "/root/.kube"]:
        assert required_path in mount_paths, (
            f"Enabling kvm_enabled=True must NOT remove the existing mount at {required_path} "
            f"(spec RUNNER-AE-S3). Present mount paths: {sorted(p for p in mount_paths if p)}"
        )


# ── RUNNER-AE-S4: emulator boot helper script ────────────────────────────────


def test_runner_ae_s4_script_exists():
    """S4: scripts/sisyphus-android-emulator.sh must exist."""
    assert EMULATOR_SCRIPT.exists(), (
        f"scripts/sisyphus-android-emulator.sh not found at {EMULATOR_SCRIPT}. "
        "Spec RUNNER-AE-S4 requires this helper to ship in /opt/sisyphus/scripts/ "
        "inside the runner image."
    )


def test_runner_ae_s4_script_supports_boot_subcommand():
    """S4: The script MUST implement a 'boot' sub-command."""
    content = _read(EMULATOR_SCRIPT)
    assert "boot" in content, (
        "sisyphus-android-emulator.sh must implement a 'boot' sub-command "
        "(spec RUNNER-AE-S4: `sisyphus-android-emulator.sh boot --timeout 300`)."
    )


def test_runner_ae_s4_script_headless_no_window():
    """S4: boot sub-command MUST pass -no-window to emulator for headless operation."""
    content = _read(EMULATOR_SCRIPT)
    assert "-no-window" in content, (
        "sisyphus-android-emulator.sh must pass '-no-window' to the emulator "
        "(spec RUNNER-AE-S4: headless boot inside a privileged pod without X server)."
    )


def test_runner_ae_s4_script_headless_no_audio():
    """S4: boot sub-command MUST pass -no-audio to emulator."""
    content = _read(EMULATOR_SCRIPT)
    assert "-no-audio" in content, (
        "sisyphus-android-emulator.sh must pass '-no-audio' to the emulator "
        "(spec RUNNER-AE-S4: headless mode flags)."
    )


def test_runner_ae_s4_script_headless_no_snapshot():
    """S4: boot sub-command MUST pass -no-snapshot to emulator."""
    content = _read(EMULATOR_SCRIPT)
    assert "-no-snapshot" in content, (
        "sisyphus-android-emulator.sh must pass '-no-snapshot' to the emulator "
        "(spec RUNNER-AE-S4: headless mode flags)."
    )


def test_runner_ae_s4_script_uses_swiftshader_gpu():
    """S4: boot sub-command MUST use -gpu swiftshader_indirect for headless GPU rendering."""
    content = _read(EMULATOR_SCRIPT)
    assert "swiftshader_indirect" in content, (
        "sisyphus-android-emulator.sh must pass '-gpu swiftshader_indirect' to the emulator "
        "(spec RUNNER-AE-S4: required for headless rendering without a GPU)."
    )


def test_runner_ae_s4_script_polls_boot_completed():
    """S4: boot sub-command MUST poll 'adb shell getprop sys.boot_completed' for '1'."""
    content = _read(EMULATOR_SCRIPT)
    assert "sys.boot_completed" in content, (
        "sisyphus-android-emulator.sh must poll 'adb shell getprop sys.boot_completed' "
        "to detect when the Android emulator has fully booted (spec RUNNER-AE-S4)."
    )


def test_runner_ae_s4_script_prints_completion_message():
    """S4: On successful boot, the script MUST print '[android-emulator] boot completed in <N>s'."""
    content = _read(EMULATOR_SCRIPT)
    assert "[android-emulator] boot completed in" in content, (
        "sisyphus-android-emulator.sh must print '[android-emulator] boot completed in <N>s' "
        "on successful boot (spec RUNNER-AE-S4)."
    )


def test_runner_ae_s4_script_has_configurable_timeout():
    """S4: boot sub-command MUST support a configurable timeout (default 300s)."""
    content = _read(EMULATOR_SCRIPT)
    has_timeout = any(
        kw in content for kw in ["--timeout", "TIMEOUT", "timeout", "300"]
    )
    assert has_timeout, (
        "sisyphus-android-emulator.sh must support a configurable timeout with "
        "default 300s (spec RUNNER-AE-S4: `--timeout 300` flag)."
    )


def test_runner_ae_s4_script_exits_nonzero_on_timeout():
    """S4: On timeout, the script MUST exit non-zero so calling Makefile targets fail loudly."""
    content = _read(EMULATOR_SCRIPT)
    lines = content.splitlines()
    nonzero_exit_re = re.compile(r"exit\s+[1-9]")
    has_nonzero_exit = any(nonzero_exit_re.search(ln) for ln in lines)
    assert has_nonzero_exit, (
        "sisyphus-android-emulator.sh must exit with a non-zero code when the boot "
        "timeout elapses, so calling Makefile targets fail loudly rather than silently "
        "proceeding against a half-booted emulator (spec RUNNER-AE-S4)."
    )
