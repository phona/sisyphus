"""Contract tests for REQ-runner-net-tools-1777202552.

Capability: runner-net-tools
Author: challenger-agent (black-box, written from spec only)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is genuinely wrong, escalate to spec_fixer to correct the spec.

Scenarios covered:
  RUNNER-NET-S1  ip command resolves on Flutter runner (iproute2 in Dockerfile §1 apt-get)
  RUNNER-NET-S2  ss command resolves on Go runner (iproute2 in go.Dockerfile §1 apt-get)
  RUNNER-NET-S3  netstat command resolves on both runners (net-tools in both Dockerfiles)
  RUNNER-NET-S4  ifconfig command resolves on both runners (net-tools in both Dockerfiles)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FULL_DOCKERFILE = REPO_ROOT / "runner" / "Dockerfile"
GO_DOCKERFILE = REPO_ROOT / "runner" / "go.Dockerfile"

# Regex: match an apt-get install block and everything up to the next &&-continuation
# that starts a non-package command (curl / rm / echo / chmod / install).
# We use this to extract only the first apt-get install invocation's package list.
_FIRST_APT_BLOCK_RE = re.compile(
    r"apt-get\s+install\s+-y\s+--no-install-recommends\s+(.*?)"
    r"(?=&&\s*(?:curl|rm\s+-rf|echo|chmod|install\s+-m))",
    re.DOTALL,
)


def _read(path: Path) -> str:
    assert path.exists(), f"Dockerfile not found at expected path: {path}"
    return path.read_text()


def _first_apt_install_block(content: str) -> str:
    """Return the package-list text of the first apt-get install invocation."""
    match = _FIRST_APT_BLOCK_RE.search(content)
    return match.group(1) if match else ""


# ── RUNNER-NET-S1: ip command on Flutter runner ──────────────────────────────


def test_runner_net_s1_full_dockerfile_exists():
    """S1: runner/Dockerfile must exist at the canonical path."""
    assert FULL_DOCKERFILE.exists(), (
        f"runner/Dockerfile not found at {FULL_DOCKERFILE}; "
        "the Flutter runner image must be present for sisyphus accept-stage to work"
    )


def test_runner_net_s1_iproute2_present_in_full_dockerfile():
    """S1: runner/Dockerfile MUST include 'iproute2' package (provides ip + ss on PATH)."""
    content = _read(FULL_DOCKERFILE)
    assert "iproute2" in content, (
        "runner/Dockerfile must install 'iproute2' to make the 'ip' command "
        "available on PATH inside the Flutter runner Pod (spec RUNNER-NET-S1). "
        "Add 'iproute2' to the apt-get install list in section §1 of runner/Dockerfile.\n"
        "Current apt-get install lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "apt-get install" in ln or "iproute2" in ln)
    )


def test_runner_net_s1_iproute2_in_section1_full_dockerfile():
    """S1: iproute2 MUST be in the first (§1 base-tools) apt-get install — same layer as ca-certificates."""
    content = _read(FULL_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, (
        "runner/Dockerfile: could not extract the first apt-get install block — "
        "Dockerfile structure may have changed"
    )
    assert "iproute2" in block, (
        "runner/Dockerfile: 'iproute2' must be in the SAME §1 apt-get install invocation "
        "as base tools like 'ca-certificates'/'gnupg'/'curl', so it shares the layer "
        "cache scope and image-tag cadence (spec RUNNER-NET-S1). "
        "Was it accidentally placed in a later RUN block instead?\n"
        "First apt-get install block found:\n" + block[:400]
    )


# ── RUNNER-NET-S2: ss command on Go runner ───────────────────────────────────


def test_runner_net_s2_go_dockerfile_exists():
    """S2: runner/go.Dockerfile must exist at the canonical path."""
    assert GO_DOCKERFILE.exists(), (
        f"runner/go.Dockerfile not found at {GO_DOCKERFILE}; "
        "the Go-only runner image must also be present"
    )


def test_runner_net_s2_iproute2_present_in_go_dockerfile():
    """S2: runner/go.Dockerfile MUST include 'iproute2' package (provides ss + ip on PATH)."""
    content = _read(GO_DOCKERFILE)
    assert "iproute2" in content, (
        "runner/go.Dockerfile must install 'iproute2' to make the 'ss' command "
        "available on PATH inside the Go runner Pod (spec RUNNER-NET-S2). "
        "Add 'iproute2' to the apt-get install list in section §1 of runner/go.Dockerfile.\n"
        "Current apt-get install lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "apt-get install" in ln or "iproute2" in ln)
    )


def test_runner_net_s2_iproute2_in_section1_go_dockerfile():
    """S2: iproute2 MUST be in the first (§1 base-tools) apt-get install of go.Dockerfile."""
    content = _read(GO_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, (
        "runner/go.Dockerfile: could not extract the first apt-get install block — "
        "Dockerfile structure may have changed"
    )
    assert "iproute2" in block, (
        "runner/go.Dockerfile: 'iproute2' must be in the SAME §1 apt-get install invocation "
        "as base tools like 'ca-certificates'/'gnupg'/'curl' (spec RUNNER-NET-S2). "
        "First apt-get install block found:\n" + block[:400]
    )


# ── RUNNER-NET-S3: netstat command on both runners ───────────────────────────


def test_runner_net_s3_net_tools_present_in_full_dockerfile():
    """S3: runner/Dockerfile MUST include 'net-tools' package (provides netstat on PATH)."""
    content = _read(FULL_DOCKERFILE)
    assert "net-tools" in content, (
        "runner/Dockerfile must install 'net-tools' to make 'netstat' available "
        "on PATH inside the Flutter runner Pod (spec RUNNER-NET-S3). "
        "Add 'net-tools' to the apt-get install list in section §1 of runner/Dockerfile."
    )


def test_runner_net_s3_net_tools_in_section1_full_dockerfile():
    """S3: net-tools MUST be in the first (§1 base-tools) apt-get install of Dockerfile."""
    content = _read(FULL_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, "runner/Dockerfile: could not extract the first apt-get install block"
    assert "net-tools" in block, (
        "runner/Dockerfile: 'net-tools' must be in the §1 base-tools apt-get install "
        "so it shares the layer cache scope (spec RUNNER-NET-S3). "
        "First apt-get install block:\n" + block[:400]
    )


def test_runner_net_s3_net_tools_present_in_go_dockerfile():
    """S3: runner/go.Dockerfile MUST include 'net-tools' package (provides netstat on PATH)."""
    content = _read(GO_DOCKERFILE)
    assert "net-tools" in content, (
        "runner/go.Dockerfile must install 'net-tools' to make 'netstat' available "
        "on PATH inside the Go runner Pod (spec RUNNER-NET-S3). "
        "Add 'net-tools' to the apt-get install list in section §1 of runner/go.Dockerfile."
    )


def test_runner_net_s3_net_tools_in_section1_go_dockerfile():
    """S3: net-tools MUST be in the first (§1 base-tools) apt-get install of go.Dockerfile."""
    content = _read(GO_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, "runner/go.Dockerfile: could not extract the first apt-get install block"
    assert "net-tools" in block, (
        "runner/go.Dockerfile: 'net-tools' must be in the §1 base-tools apt-get install "
        "(spec RUNNER-NET-S3). "
        "First apt-get install block:\n" + block[:400]
    )


# ── RUNNER-NET-S4: ifconfig command on both runners ──────────────────────────
# ifconfig is also provided by net-tools; these tests confirm the same net-tools
# package declaration satisfies the ifconfig PATH requirement.


def test_runner_net_s4_ifconfig_provided_by_net_tools_full_dockerfile():
    """S4: runner/Dockerfile must declare net-tools; ifconfig is part of that package."""
    content = _read(FULL_DOCKERFILE)
    assert "net-tools" in content, (
        "runner/Dockerfile must install 'net-tools' to make 'ifconfig' available "
        "on PATH inside the Flutter runner Pod (spec RUNNER-NET-S4). "
        "'ifconfig' is shipped as part of the net-tools Debian package."
    )


def test_runner_net_s4_ifconfig_provided_by_net_tools_go_dockerfile():
    """S4: runner/go.Dockerfile must declare net-tools; ifconfig is part of that package."""
    content = _read(GO_DOCKERFILE)
    assert "net-tools" in content, (
        "runner/go.Dockerfile must install 'net-tools' to make 'ifconfig' available "
        "on PATH inside the Go runner Pod (spec RUNNER-NET-S4). "
        "'ifconfig' is shipped as part of the net-tools Debian package."
    )


# ── cross-cut: both packages co-installed in same §1 block ───────────────────


def test_runner_net_both_packages_colocated_in_section1_full_dockerfile():
    """S1-S4: iproute2 AND net-tools MUST appear together in §1 of runner/Dockerfile."""
    content = _read(FULL_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, "runner/Dockerfile: could not extract the first apt-get install block"
    missing = [pkg for pkg in ("iproute2", "net-tools") if pkg not in block]
    assert not missing, (
        "runner/Dockerfile §1 apt-get install is missing: "
        + ", ".join(f"'{p}'" for p in missing)
        + ". Both packages must be co-installed in the same §1 invocation "
        "to share that layer's cache scope and image-tagging cadence "
        "(spec RUNNER-NET-S1..S4).\n"
        "First apt-get install block:\n" + block[:400]
    )


def test_runner_net_both_packages_colocated_in_section1_go_dockerfile():
    """S1-S4: iproute2 AND net-tools MUST appear together in §1 of runner/go.Dockerfile."""
    content = _read(GO_DOCKERFILE)
    block = _first_apt_install_block(content)
    assert block, "runner/go.Dockerfile: could not extract the first apt-get install block"
    missing = [pkg for pkg in ("iproute2", "net-tools") if pkg not in block]
    assert not missing, (
        "runner/go.Dockerfile §1 apt-get install is missing: "
        + ", ".join(f"'{p}'" for p in missing)
        + ". Both packages must be co-installed in the same §1 invocation "
        "(spec RUNNER-NET-S1..S4).\n"
        "First apt-get install block:\n" + block[:400]
    )
