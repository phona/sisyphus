"""Contract tests for REQ-runner-add-helm-only-1777138424.

Capability: runner-helm
Author: challenger-agent (black-box, written from spec only)

Scenarios covered:
  RUNNER-HELM-S1  runner/Dockerfile installs helm; image can run `helm version` (exits 0, v3.x.y)
  RUNNER-HELM-S2  runner/go.Dockerfile installs helm; image can run `helm version` (exits 0, v3.x.y)
  RUNNER-HELM-S3  Both Dockerfiles pin HELM_VERSION via ARG; default matches semver pattern
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FULL_DOCKERFILE = REPO_ROOT / "runner" / "Dockerfile"
GO_DOCKERFILE = REPO_ROOT / "runner" / "go.Dockerfile"

# HELM_VERSION default can be "3.17.3" or "v3.17.3"
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_ARG_HELM_RE = re.compile(r"^\s*ARG\s+HELM_VERSION\s*=\s*(\S+)", re.MULTILINE)
# Install step uses the ARG: e.g. "helm-v${HELM_VERSION}"
_HELM_INSTALL_RE = re.compile(r"helm-v\$\{?HELM_VERSION\}?", re.IGNORECASE)


def _read(path: Path) -> str:
    assert path.exists(), f"Dockerfile not found at expected path: {path}"
    return path.read_text()


# ── RUNNER-HELM-S1: full runner image ───────────────────────────────────────

def test_runner_helm_s1_full_dockerfile_exists():
    """S1: runner/Dockerfile must exist at the canonical path."""
    assert FULL_DOCKERFILE.exists(), (
        f"runner/Dockerfile not found at {FULL_DOCKERFILE}; "
        "both runner images must be present for sisyphus accept-stage to work"
    )


def test_runner_helm_s1_full_dockerfile_declares_helm_version_arg():
    """S1: runner/Dockerfile MUST declare ARG HELM_VERSION with a pinned default."""
    content = _read(FULL_DOCKERFILE)
    match = _ARG_HELM_RE.search(content)
    assert match is not None, (
        "runner/Dockerfile must contain 'ARG HELM_VERSION=<pinned-version>' "
        "so that builds are reproducible.\n"
        "Relevant lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "HELM" in ln or "helm" in ln)
    )


def test_runner_helm_s1_full_dockerfile_install_step_uses_helm_version_arg():
    """S1: runner/Dockerfile RUN step MUST install helm using $HELM_VERSION ARG."""
    content = _read(FULL_DOCKERFILE)
    assert _HELM_INSTALL_RE.search(content) is not None, (
        "runner/Dockerfile must install helm via the HELM_VERSION ARG "
        "(expected 'helm-v${HELM_VERSION}' in a RUN step).\n"
        "Relevant lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "helm" in ln.lower())
    )


def test_runner_helm_s1_full_dockerfile_verifies_helm_binary():
    """S1: runner/Dockerfile MUST call `helm version` after install to verify binary exits 0."""
    content = _read(FULL_DOCKERFILE)
    helm_lines = [ln for ln in content.splitlines() if "helm" in ln.lower()]
    assert any("helm version" in ln for ln in helm_lines), (
        "runner/Dockerfile must run `helm version` after install to verify "
        "the binary is present and functional (spec RUNNER-HELM-S1).\n"
        "Helm-related lines found:\n" + "\n".join(helm_lines)
    )


# ── RUNNER-HELM-S2: Go-only runner image ────────────────────────────────────

def test_runner_helm_s2_go_dockerfile_exists():
    """S2: runner/go.Dockerfile must exist at the canonical path."""
    assert GO_DOCKERFILE.exists(), (
        f"runner/go.Dockerfile not found at {GO_DOCKERFILE}; "
        "the Go-only runner image must also carry helm for accept-stage compatibility"
    )


def test_runner_helm_s2_go_dockerfile_declares_helm_version_arg():
    """S2: runner/go.Dockerfile MUST declare ARG HELM_VERSION with a pinned default."""
    content = _read(GO_DOCKERFILE)
    match = _ARG_HELM_RE.search(content)
    assert match is not None, (
        "runner/go.Dockerfile must contain 'ARG HELM_VERSION=<pinned-version>' "
        "so that builds are reproducible.\n"
        "Relevant lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "HELM" in ln or "helm" in ln)
    )


def test_runner_helm_s2_go_dockerfile_install_step_uses_helm_version_arg():
    """S2: runner/go.Dockerfile RUN step MUST install helm using $HELM_VERSION ARG."""
    content = _read(GO_DOCKERFILE)
    assert _HELM_INSTALL_RE.search(content) is not None, (
        "runner/go.Dockerfile must install helm via the HELM_VERSION ARG "
        "(expected 'helm-v${HELM_VERSION}' in a RUN step).\n"
        "Relevant lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "helm" in ln.lower())
    )


def test_runner_helm_s2_go_dockerfile_verifies_helm_binary():
    """S2: runner/go.Dockerfile MUST call `helm version` after install to verify binary exits 0."""
    content = _read(GO_DOCKERFILE)
    helm_lines = [ln for ln in content.splitlines() if "helm" in ln.lower()]
    assert any("helm version" in ln for ln in helm_lines), (
        "runner/go.Dockerfile must run `helm version` after install to verify "
        "the binary is present and functional (spec RUNNER-HELM-S2).\n"
        "Helm-related lines found:\n" + "\n".join(helm_lines)
    )


# ── RUNNER-HELM-S3: version pinning ─────────────────────────────────────────

def test_runner_helm_s3_full_dockerfile_helm_version_is_pinned_semver():
    """S3: HELM_VERSION default in runner/Dockerfile MUST be a semver matching v3.x.y."""
    content = _read(FULL_DOCKERFILE)
    match = _ARG_HELM_RE.search(content)
    assert match is not None, "ARG HELM_VERSION not found in runner/Dockerfile (see S3)"
    default = match.group(1)
    semver = _SEMVER_RE.match(default)
    assert semver is not None, (
        f"HELM_VERSION default in runner/Dockerfile must be a semver like '3.x.y' or 'v3.x.y', "
        f"got {default!r}"
    )
    major = int(semver.group(1))
    assert major == 3, (
        f"HELM_VERSION must be a v3 release; got major={major} (full value: {default!r})"
    )


def test_runner_helm_s3_go_dockerfile_helm_version_is_pinned_semver():
    """S3: HELM_VERSION default in runner/go.Dockerfile MUST be a semver matching v3.x.y."""
    content = _read(GO_DOCKERFILE)
    match = _ARG_HELM_RE.search(content)
    assert match is not None, "ARG HELM_VERSION not found in runner/go.Dockerfile (see S3)"
    default = match.group(1)
    semver = _SEMVER_RE.match(default)
    assert semver is not None, (
        f"HELM_VERSION default in runner/go.Dockerfile must be a semver like '3.x.y' or 'v3.x.y', "
        f"got {default!r}"
    )
    major = int(semver.group(1))
    assert major == 3, (
        f"HELM_VERSION must be a v3 release; got major={major} (full value: {default!r})"
    )


def test_runner_helm_s3_both_dockerfiles_pin_same_helm_version():
    """S3: Both Dockerfiles MUST pin the same HELM_VERSION for build-to-build consistency."""
    full_content = _read(FULL_DOCKERFILE)
    go_content = _read(GO_DOCKERFILE)

    full_match = _ARG_HELM_RE.search(full_content)
    go_match = _ARG_HELM_RE.search(go_content)

    assert full_match is not None, "ARG HELM_VERSION missing from runner/Dockerfile"
    assert go_match is not None, "ARG HELM_VERSION missing from runner/go.Dockerfile"

    full_ver = full_match.group(1)
    go_ver = go_match.group(1)
    assert full_ver == go_ver, (
        "Both runner Dockerfiles must pin the same HELM_VERSION to avoid version skew "
        "between the full and Go-only runner images.\n"
        f"  runner/Dockerfile:    HELM_VERSION={full_ver!r}\n"
        f"  runner/go.Dockerfile: HELM_VERSION={go_ver!r}"
    )
