"""Challenger contract tests for REQ-runner-image-add-uv-1777135052.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-runner-image-add-uv-1777135052/specs/runner-uv/spec.md

Scenarios covered:
  RUNNER-UV-S1  uv binary is present and executable in runner image
  RUNNER-UV-S2  ci-lint target succeeds in runner container without uv-not-found errors
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
IMAGE_TAG = "sisyphus-runner-contract-uv-test:latest"


def _build_runner_image() -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "build",
            "-t", IMAGE_TAG,
            "-f", os.path.join(REPO_ROOT, "runner", "Dockerfile"),
            REPO_ROOT,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture(scope="module")
def runner_image():
    result = _build_runner_image()
    if result.returncode != 0:
        pytest.fail(
            f"runner/Dockerfile build failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )
    yield IMAGE_TAG
    subprocess.run(["docker", "rmi", "--force", IMAGE_TAG], capture_output=True)


@pytest.mark.integration
def test_runner_uv_s1_uv_binary_present_and_executable(runner_image):
    """
    Scenario: RUNNER-UV-S1
    GIVEN the sisyphus runner image has been built from runner/Dockerfile
    WHEN a process inside the container runs `uv --version`
    THEN the command exits 0 and prints a version string matching `uv \\d+\\.\\d+`
    """
    result = subprocess.run(
        ["docker", "run", "--rm", runner_image, "uv", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"RUNNER-UV-S1: `uv --version` exited {result.returncode}, expected 0.\n"
        f"Output: {output}"
    )
    assert re.search(r"uv \d+\.\d+", output), (
        f"RUNNER-UV-S1: version string matching 'uv N.N' not found in output: {output!r}"
    )


@pytest.mark.integration
def test_runner_uv_s2_ci_lint_succeeds_without_uv_not_found(runner_image):
    """
    Scenario: RUNNER-UV-S2
    GIVEN the runner container has the sisyphus source repo mounted at /workspace/source/sisyphus
    WHEN `make ci-lint` is executed (no BASE_REV, full scan)
    THEN the command exits 0 without `uv: not found` errors
    """
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--volume", f"{REPO_ROOT}:/workspace/source/sisyphus",
            "--workdir", "/workspace/source/sisyphus",
            runner_image,
            "make", "ci-lint",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = result.stdout + result.stderr
    assert "uv: not found" not in combined, (
        f"RUNNER-UV-S2: 'uv: not found' appeared in ci-lint output:\n{combined}"
    )
    assert result.returncode == 0, (
        f"RUNNER-UV-S2: `make ci-lint` exited {result.returncode}, expected 0.\n"
        f"Output (tail):\n{combined[-3000:]}"
    )
