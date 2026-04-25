"""Contract tests for runner Dockerfile uv binary (REQ-runner-uv-dockerfile-only-1777136147).

Black-box structural contracts derived from:
  openspec/changes/REQ-runner-uv-dockerfile-only-1777136147/specs/runner-uv/spec.md

Scenarios covered:
  RUNNER-UV-ONLY-S1  Dockerfile declares uv COPY instruction (exact string match)
  RUNNER-UV-ONLY-S2  uv COPY instruction appears before openspec npm install in layer order
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "runner" / "Dockerfile"

UV_COPY_INSTRUCTION = "COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/"


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_runner_uv_only_s1_dockerfile_contains_uv_copy_instruction() -> None:
    """
    RUNNER-UV-ONLY-S1: runner/Dockerfile SHALL contain the exact instruction
    `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/`
    so that the built image provides uv and uvx at /usr/local/bin/.
    """
    text = _dockerfile_text()
    assert UV_COPY_INSTRUCTION in text, (
        f"RUNNER-UV-ONLY-S1 contract: runner/Dockerfile MUST contain\n"
        f"  {UV_COPY_INSTRUCTION!r}\n"
        f"but it was not found. "
        f"uv is required for business repos that use `uv run` in ci-lint/ci-unit-test targets."
    )


def test_runner_uv_only_s2_uv_copy_before_openspec_npm_install() -> None:
    """
    RUNNER-UV-ONLY-S2: the uv COPY instruction SHALL appear before the openspec
    npm install instruction in Dockerfile layer order.
    Earlier layers build before later ones; uv must be available before openspec
    npm install runs (in case openspec toolchain ever requires uv).
    """
    text = _dockerfile_text()

    uv_pos = text.find(UV_COPY_INSTRUCTION)
    assert uv_pos != -1, (
        f"RUNNER-UV-ONLY-S2 prerequisite: uv COPY instruction not found in runner/Dockerfile. "
        f"Cannot verify layer ordering. (S1 should have caught this first.)"
    )

    npm_install_pos = text.find("npm install")
    assert npm_install_pos != -1, (
        "RUNNER-UV-ONLY-S2: expected 'npm install' (openspec installation) in runner/Dockerfile "
        "but it was not found — cannot verify uv precedes openspec layer."
    )

    assert uv_pos < npm_install_pos, (
        f"RUNNER-UV-ONLY-S2 contract: uv COPY instruction (pos={uv_pos}) MUST appear before "
        f"'npm install' (pos={npm_install_pos}) in runner/Dockerfile. "
        f"Current ordering violates the spec requirement."
    )
