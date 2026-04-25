"""Contract tests for REQ-rename-accept-targets-1777124774.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-rename-accept-targets-1777124774/specs/self-accept-stage/spec.md

Scenarios covered:
  SDA-S1  accept-env-up target exists in Makefile (.PHONY + recipe header, no ci- prefix);
           last stdout line is valid JSON with endpoint + namespace (integration only — needs DinD)
  SDA-S2  accept-env-down target exists (.PHONY + recipe header, no ci- prefix);
           make accept-env-down exits 0 on missing stack (idempotent)
  SDA-S4  _resolve_integration_dir(): integration dir takes priority over source when both have target
  SDA-S5  _resolve_integration_dir(): single source with target → fallback to that source dir
  SDA-S6  _resolve_integration_dir(): source without accept-env-up target → None
  SDA-S7  _resolve_integration_dir(): multiple source repos with target → None (refuse to pick)
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

# orchestrator/tests/../../ = repo root (where Makefile lives)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _make(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["make", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _makefile_text() -> str:
    return (REPO_ROOT / "Makefile").read_text()


# ── SDA-S1: accept-env-up naming ─────────────────────────────────────────────


def test_SDA_S1_accept_env_up_in_phony():
    """accept-env-up MUST appear in .PHONY (not ci-accept-env-up)."""
    phony_lines = [l for l in _makefile_text().splitlines() if l.startswith(".PHONY")]
    phony_content = " ".join(phony_lines)
    assert "accept-env-up" in phony_content, (
        f"accept-env-up must appear in .PHONY; got: {phony_content}"
    )


def test_SDA_S1_accept_env_up_recipe_header_exists():
    """accept-env-up: MUST appear as a recipe header in the Makefile."""
    assert "accept-env-up:" in _makefile_text(), (
        "Makefile must define an 'accept-env-up:' recipe header"
    )


def test_SDA_S1_no_legacy_ci_accept_env_up_recipe():
    """ci-accept-env-up MUST NOT appear as a live recipe header or .PHONY entry."""
    for line in _makefile_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("ci-accept-env-up:"):
            pytest.fail(
                f"Legacy recipe header 'ci-accept-env-up:' found in Makefile: {line!r}"
            )
    phony_lines = [l for l in _makefile_text().splitlines() if l.startswith(".PHONY")]
    phony_content = " ".join(phony_lines)
    # The legacy name MUST NOT be a .PHONY entry either
    assert "ci-accept-env-up" not in phony_content, (
        f"Legacy 'ci-accept-env-up' must not appear in .PHONY; got: {phony_content}"
    )


def test_SDA_S1_accept_env_up_dry_run_exits_0():
    """make -n accept-env-up exits 0 (target is parseable by make)."""
    result = _make("-n", "accept-env-up")
    assert result.returncode == 0, (
        f"make -n accept-env-up exited {result.returncode}:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.integration
def test_SDA_S1_accept_env_up_emits_endpoint_json():
    """make accept-env-up last stdout line is JSON with endpoint + namespace (DinD required)."""
    ns = f"test-{uuid.uuid4().hex[:8]}"
    env_extra = {"SISYPHUS_NAMESPACE": ns}
    result = _make("accept-env-up", env_extra=env_extra)
    # Always teardown
    _make("accept-env-down", env_extra=env_extra)
    assert result.returncode == 0, (
        f"make accept-env-up exited {result.returncode}:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert lines, "make accept-env-up produced no stdout output"
    last_line = lines[-1]
    try:
        data = json.loads(last_line)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"Last stdout line is not valid JSON: {last_line!r}\n{exc}"
        )
    assert "endpoint" in data, f"JSON missing 'endpoint' field: {data}"
    assert "namespace" in data, f"JSON missing 'namespace' field: {data}"


# ── SDA-S2: accept-env-down naming + idempotency ─────────────────────────────


def test_SDA_S2_accept_env_down_in_phony():
    """accept-env-down MUST appear in .PHONY (not ci-accept-env-down)."""
    phony_lines = [l for l in _makefile_text().splitlines() if l.startswith(".PHONY")]
    phony_content = " ".join(phony_lines)
    assert "accept-env-down" in phony_content, (
        f"accept-env-down must appear in .PHONY; got: {phony_content}"
    )


def test_SDA_S2_accept_env_down_recipe_header_exists():
    """accept-env-down: MUST appear as a recipe header in the Makefile."""
    assert "accept-env-down:" in _makefile_text(), (
        "Makefile must define an 'accept-env-down:' recipe header"
    )


def test_SDA_S2_no_legacy_ci_accept_env_down_recipe():
    """ci-accept-env-down MUST NOT appear as a live recipe header or .PHONY entry."""
    for line in _makefile_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("ci-accept-env-down:"):
            pytest.fail(
                f"Legacy recipe header 'ci-accept-env-down:' found in Makefile: {line!r}"
            )
    phony_lines = [l for l in _makefile_text().splitlines() if l.startswith(".PHONY")]
    phony_content = " ".join(phony_lines)
    assert "ci-accept-env-down" not in phony_content, (
        f"Legacy 'ci-accept-env-down' must not appear in .PHONY; got: {phony_content}"
    )


def test_SDA_S2_accept_env_down_idempotent_on_missing_stack():
    """make accept-env-down exits 0 even when the named stack was never started."""
    ns = f"test-nonexistent-{uuid.uuid4().hex[:8]}"
    result = _make("accept-env-down", env_extra={"SISYPHUS_NAMESPACE": ns})
    assert result.returncode == 0, (
        f"make accept-env-down on missing stack exited {result.returncode} (expected 0):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── SDA-S4..S7: _resolve_integration_dir() contract ──────────────────────────
#
# The spec describes _resolve_integration_dir(workspace_root) as a helper that:
#   1. Scans workspace_root/integration/*/Makefile for ^accept-env-up: header
#   2. If found → return that integration dir (priority)
#   3. Else scans workspace_root/source/*/Makefile for the same header
#   4. If exactly one source dir has the target → return it (fallback)
#   5. If zero or multiple → return None


def _resolve(workspace_root: Path):
    from orchestrator.actions._integration_resolver import _resolve_integration_dir

    return _resolve_integration_dir(workspace_root=workspace_root)


def _write_makefile_with_target(path: Path, target: str) -> None:
    path.write_text(f".PHONY: {target}\n{target}:\n\techo done\n")


def _write_makefile_without_target(path: Path) -> None:
    path.write_text(".PHONY: ci-lint\nci-lint:\n\techo lint\n")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "integration").mkdir()
    (tmp_path / "source").mkdir()
    return tmp_path


def test_SDA_S4_integration_dir_takes_priority(workspace: Path):
    """Integration dir is returned when it has accept-env-up: even if source also has it."""
    lab = workspace / "integration" / "lab"
    lab.mkdir()
    _write_makefile_with_target(lab / "Makefile", "accept-env-up")

    sisyphus = workspace / "source" / "sisyphus"
    sisyphus.mkdir()
    _write_makefile_with_target(sisyphus / "Makefile", "accept-env-up")

    result = _resolve(workspace)

    assert result is not None, (
        "_resolve_integration_dir returned None; expected integration/lab"
    )
    assert Path(result) == lab, (
        f"Expected integration/lab ({lab}), got {result}"
    )


def test_SDA_S5_single_source_fallback(workspace: Path):
    """Single source repo with accept-env-up: target → returns that source directory."""
    sisyphus = workspace / "source" / "sisyphus"
    sisyphus.mkdir()
    _write_makefile_with_target(sisyphus / "Makefile", "accept-env-up")

    result = _resolve(workspace)

    assert result is not None, (
        "_resolve_integration_dir returned None; expected source/sisyphus fallback"
    )
    assert Path(result) == sisyphus, (
        f"Expected source/sisyphus ({sisyphus}), got {result}"
    )


def test_SDA_S6_source_without_target_returns_none(workspace: Path):
    """Source repo Makefile without accept-env-up: → returns None."""
    foo = workspace / "source" / "foo"
    foo.mkdir()
    _write_makefile_without_target(foo / "Makefile")

    result = _resolve(workspace)

    assert result is None, (
        f"Expected None when source has no accept-env-up target, got {result}"
    )


def test_SDA_S7_multiple_sources_returns_none(workspace: Path):
    """Multiple source repos with accept-env-up: → returns None (refuses to pick one)."""
    a = workspace / "source" / "a"
    b = workspace / "source" / "b"
    a.mkdir()
    b.mkdir()
    _write_makefile_with_target(a / "Makefile", "accept-env-up")
    _write_makefile_with_target(b / "Makefile", "accept-env-up")

    result = _resolve(workspace)

    assert result is None, (
        f"Expected None for multiple candidate source repos, got {result}"
    )
