"""Challenger contract tests for REQ-flip-integration-resolver-source-1777195860.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-flip-integration-resolver-source-1777195860/specs/self-accept-stage/spec.md

Scenarios covered:
  SDA-S4   single source repo with accept-env-up: wins over explicit integration dir
  SDA-S5   single source repo with accept-env-up: and empty integration → use source
  SDA-S6   source repo without accept-env-up: target and empty integration → returns None
  SDA-S7   multiple source repos with accept-env-up: and no integration → returns None
  SDA-S10  multiple sources with accept-env-up: + explicit integration dir → integration breaks tie
  SDA-M0   module _integration_resolver is importable and exposes _resolve_integration_dir
  SDA-M1   matcher uses ^accept-env-up: pattern (not ci-accept-env-up:)
  SDA-M2   resolver does not raise when /workspace/integration/ is absent (shell-glob safety)
"""
from __future__ import annotations

from pathlib import Path

# ── helpers ─────────────────────────────────────────────────────────────────


def _write_makefile(path: Path, *, has_accept_env_up: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if has_accept_env_up:
        (path / "Makefile").write_text("accept-env-up:\n\techo up\n\naccept-env-down:\n\techo down\n")
    else:
        (path / "Makefile").write_text("ci-lint:\n\truff check .\n")


def _resolver():
    """Import and return _resolve_integration_dir from the resolver module."""
    import orchestrator.actions._integration_resolver as m

    fn = getattr(m, "_resolve_integration_dir", None)
    assert fn is not None, (
        "orchestrator.actions._integration_resolver must expose _resolve_integration_dir"
    )
    return fn


def _call(workspace: Path) -> Path | None:
    """Call the resolver with an injected workspace root."""
    fn = _resolver()
    result = fn(workspace_root=workspace)
    return Path(result) if result is not None else None


# ── SDA-M0: module + symbol existence ───────────────────────────────────────


def test_SDA_M0_module_is_importable() -> None:
    """orchestrator.actions._integration_resolver must be importable with no side effects."""
    import orchestrator.actions._integration_resolver  # noqa: F401


def test_SDA_M0_resolver_function_exposed() -> None:
    """Module must expose _resolve_integration_dir callable."""
    _resolver()


# ── SDA-M1: grep pattern contract ───────────────────────────────────────────


def test_SDA_M1_ci_prefixed_target_not_matched(tmp_path: Path) -> None:
    """Makefile with only ci-accept-env-up: must NOT be matched (spec: grep ^accept-env-up:)."""
    (tmp_path / "integration").mkdir(parents=True)
    # source has 'ci-accept-env-up:' (wrong prefix) — must not count
    src = tmp_path / "source" / "foo"
    src.mkdir(parents=True)
    (src / "Makefile").write_text("ci-accept-env-up:\n\techo up\n")

    result = _call(tmp_path)
    assert result is None, (
        f"Expected None: 'ci-accept-env-up:' must not satisfy the '^accept-env-up:' pattern, "
        f"got {result!r}"
    )


# ── SDA-M2: missing integration dir safety ──────────────────────────────────


def test_SDA_M2_no_exception_when_integration_dir_absent(tmp_path: Path) -> None:
    """Resolver must not raise when /workspace/integration/ does not exist at all."""
    # no integration dir; source also lacks the target
    _write_makefile(tmp_path / "source" / "foo", has_accept_env_up=False)
    # should return None, not raise
    result = _call(tmp_path)
    assert result is None, f"Expected None, got {result!r}"


# ── SDA-S4 ──────────────────────────────────────────────────────────────────


def test_SDA_S4_single_source_wins_over_explicit_integration(tmp_path: Path) -> None:
    """Source-first: single unambiguous source wins even when integration also has accept-env-up:.

    GIVEN /workspace/integration/lab/Makefile contains accept-env-up:
      AND /workspace/source/sisyphus/Makefile contains accept-env-up: (only source)
    WHEN _resolve_integration_dir() is called
    THEN it returns /workspace/source/sisyphus
    """
    _write_makefile(tmp_path / "integration" / "lab", has_accept_env_up=True)
    _write_makefile(tmp_path / "source" / "sisyphus", has_accept_env_up=True)

    result = _call(tmp_path)
    assert result == tmp_path / "source" / "sisyphus", (
        f"Expected source dir to win over integration dir (SDA-S4), got {result!r}"
    )


# ── SDA-S5 ──────────────────────────────────────────────────────────────────


def test_SDA_S5_single_source_primary_path_empty_integration(tmp_path: Path) -> None:
    """Primary path: single source with target + empty integration dir → source wins.

    GIVEN /workspace/integration/ is empty
      AND /workspace/source/sisyphus/Makefile contains accept-env-up: (only source)
    WHEN _resolve_integration_dir() is called
    THEN it returns /workspace/source/sisyphus
    """
    (tmp_path / "integration").mkdir(parents=True)
    _write_makefile(tmp_path / "source" / "sisyphus", has_accept_env_up=True)

    result = _call(tmp_path)
    assert result == tmp_path / "source" / "sisyphus", (
        f"Expected source dir to be returned (SDA-S5 primary path), got {result!r}"
    )


def test_SDA_S5_source_wins_when_integration_dir_missing(tmp_path: Path) -> None:
    """Source wins even when /workspace/integration/ doesn't exist (same as empty)."""
    _write_makefile(tmp_path / "source" / "sisyphus", has_accept_env_up=True)
    # intentionally no integration directory at all

    result = _call(tmp_path)
    assert result == tmp_path / "source" / "sisyphus", (
        f"Expected source dir without integration dir (SDA-S5 variant), got {result!r}"
    )


# ── SDA-S6 ──────────────────────────────────────────────────────────────────


def test_SDA_S6_no_target_anywhere_returns_none(tmp_path: Path) -> None:
    """No accept-env-up: in source + empty integration → returns None.

    GIVEN /workspace/integration/ is empty
      AND /workspace/source/foo/Makefile has no accept-env-up: target
    WHEN _resolve_integration_dir() is called
    THEN it returns None
    """
    (tmp_path / "integration").mkdir(parents=True)
    _write_makefile(tmp_path / "source" / "foo", has_accept_env_up=False)

    result = _call(tmp_path)
    assert result is None, (
        f"Expected None when no repo has accept-env-up: target (SDA-S6), got {result!r}"
    )


# ── SDA-S7 ──────────────────────────────────────────────────────────────────


def test_SDA_S7_multiple_sources_no_integration_returns_none(tmp_path: Path) -> None:
    """Multiple sources with target + no integration → refuses to pick, returns None.

    GIVEN /workspace/integration/ is empty
      AND two source repos (/workspace/source/a + /workspace/source/b) both carry accept-env-up:
    WHEN _resolve_integration_dir() is called
    THEN it returns None (refuses to silently pick one)
    """
    (tmp_path / "integration").mkdir(parents=True)
    _write_makefile(tmp_path / "source" / "a", has_accept_env_up=True)
    _write_makefile(tmp_path / "source" / "b", has_accept_env_up=True)

    result = _call(tmp_path)
    assert result is None, (
        f"Expected None when multiple sources carry accept-env-up: (SDA-S7 ambiguous), got {result!r}"
    )


# ── SDA-S10 ─────────────────────────────────────────────────────────────────


def test_SDA_S10_multiple_sources_integration_breaks_tie(tmp_path: Path) -> None:
    """Multiple ambiguous sources + explicit integration dir → integration is tiebreaker.

    GIVEN two source repos (/workspace/source/a + /workspace/source/b) both carry accept-env-up:
      AND /workspace/integration/lab/Makefile also carries accept-env-up:
    WHEN _resolve_integration_dir() is called
    THEN it returns /workspace/integration/lab
    """
    _write_makefile(tmp_path / "source" / "a", has_accept_env_up=True)
    _write_makefile(tmp_path / "source" / "b", has_accept_env_up=True)
    _write_makefile(tmp_path / "integration" / "lab", has_accept_env_up=True)

    result = _call(tmp_path)
    assert result == tmp_path / "integration" / "lab", (
        f"Expected integration/lab to break the tie (SDA-S10), got {result!r}"
    )
