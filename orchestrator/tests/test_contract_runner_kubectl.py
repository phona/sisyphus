"""Contract tests for REQ-runner-add-kubectl-only-1777138424.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-runner-add-kubectl-only-1777138424/specs/runner-kubectl/spec.md

Scenarios covered:
  RUNNER-KUBECTL-S1  runner/Dockerfile declares kubectl download instruction
  RUNNER-KUBECTL-S2  runner/go.Dockerfile declares kubectl download instruction
  RUNNER-KUBECTL-S3  kubectl step appears before openspec npm install in Dockerfile layer order
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = REPO_ROOT / "runner" / "Dockerfile"
GO_DOCKERFILE = REPO_ROOT / "runner" / "go.Dockerfile"

_KUBECTL_DL_URL = "https://dl.k8s.io/release/"
_KUBECTL_BIN = "/usr/local/bin/kubectl"


# ── RUNNER-KUBECTL-S1 ────────────────────────────────────────────────────────


def test_RUNNER_KUBECTL_S1_dockerfile_contains_kubectl_download_url():
    """S1: runner/Dockerfile fetches kubectl from dl.k8s.io."""
    assert DOCKERFILE.exists(), f"runner/Dockerfile not found at {DOCKERFILE}"
    content = DOCKERFILE.read_text()
    assert _KUBECTL_DL_URL in content, (
        f"runner/Dockerfile MUST contain '{_KUBECTL_DL_URL}'; "
        "kubectl binary must be downloaded from the official dl.k8s.io endpoint"
    )


def test_RUNNER_KUBECTL_S1_dockerfile_installs_to_usr_local_bin():
    """S1: runner/Dockerfile places kubectl at /usr/local/bin/kubectl."""
    content = DOCKERFILE.read_text()
    assert _KUBECTL_BIN in content, (
        f"runner/Dockerfile MUST contain '{_KUBECTL_BIN}'; "
        "kubectl binary must be installed to /usr/local/bin/kubectl"
    )


# ── RUNNER-KUBECTL-S2 ────────────────────────────────────────────────────────


def test_RUNNER_KUBECTL_S2_go_dockerfile_contains_kubectl_download_url():
    """S2: runner/go.Dockerfile fetches kubectl from dl.k8s.io."""
    assert GO_DOCKERFILE.exists(), f"runner/go.Dockerfile not found at {GO_DOCKERFILE}"
    content = GO_DOCKERFILE.read_text()
    assert _KUBECTL_DL_URL in content, (
        f"runner/go.Dockerfile MUST contain '{_KUBECTL_DL_URL}'; "
        "kubectl binary must be downloaded from the official dl.k8s.io endpoint"
    )


def test_RUNNER_KUBECTL_S2_go_dockerfile_installs_to_usr_local_bin():
    """S2: runner/go.Dockerfile places kubectl at /usr/local/bin/kubectl."""
    content = GO_DOCKERFILE.read_text()
    assert _KUBECTL_BIN in content, (
        f"runner/go.Dockerfile MUST contain '{_KUBECTL_BIN}'; "
        "kubectl binary must be installed to /usr/local/bin/kubectl"
    )


# ── RUNNER-KUBECTL-S3 ────────────────────────────────────────────────────────


def test_RUNNER_KUBECTL_S3_kubectl_layer_before_openspec_layer():
    """S3: kubectl download RUN appears before openspec npm install RUN in runner/Dockerfile."""
    content = DOCKERFILE.read_text()
    lines = content.splitlines()

    kubectl_line: int | None = None
    openspec_line: int | None = None

    for i, line in enumerate(lines):
        if _KUBECTL_DL_URL in line and kubectl_line is None:
            kubectl_line = i
        # openspec npm install: "npm install -g @fission-ai/openspec"
        if "npm install" in line and "openspec" in line and openspec_line is None:
            openspec_line = i

    assert kubectl_line is not None, (
        "runner/Dockerfile must contain a RUN instruction that downloads kubectl "
        f"from {_KUBECTL_DL_URL}"
    )
    assert openspec_line is not None, (
        "runner/Dockerfile must contain a RUN instruction that installs openspec via npm"
    )
    assert kubectl_line < openspec_line, (
        f"kubectl download (line {kubectl_line + 1}) MUST appear BEFORE "
        f"openspec npm install (line {openspec_line + 1}) in runner/Dockerfile; "
        "layer ordering ensures kubectl is available when openspec is installed"
    )
