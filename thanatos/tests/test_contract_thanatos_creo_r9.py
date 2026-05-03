"""Contract tests for REQ-impl-thanatos-scenarios-fallback-1777807813 — CREO-S32..S35.

Black-box only: exercises the public ``thanatos.skill.resolve_skill_path`` API as
defined by the spec. Does not assert on internal implementation details.
All tests marked ``@pytest.mark.integration``.
"""
from __future__ import annotations

import pathlib

import pytest

from thanatos.skill import SkillLoadError, resolve_skill_path


def _make_dir(parent: pathlib.Path, name: str) -> pathlib.Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=False)
    return d


def _seed_skill_yaml(dirpath: pathlib.Path) -> pathlib.Path:
    yml = dirpath / "skill.yaml"
    yml.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")
    return yml


# ─── CREO-S32: .sisyphus/scenarios/ takes priority over .thanatos/ ───────────


@pytest.mark.integration
def test_creo_s32_sisyphus_scenarios_takes_priority(tmp_path):
    """CREO-S32: when both directories exist, .sisyphus/scenarios/ wins."""
    sisyphus_dir = tmp_path / ".sisyphus" / "scenarios"
    sisyphus_dir.mkdir(parents=True)
    _seed_skill_yaml(sisyphus_dir)

    thanatos_dir = _make_dir(tmp_path, ".thanatos")
    _seed_skill_yaml(thanatos_dir)

    result = resolve_skill_path(tmp_path)

    expected = tmp_path / ".sisyphus" / "scenarios" / "skill.yaml"
    assert pathlib.Path(result) == expected, (
        f"CREO-S32: expected {expected}, got {result}"
    )


# ─── CREO-S33: fallback to .thanatos/ when .sisyphus/scenarios/ absent ───────


@pytest.mark.integration
def test_creo_s33_fallback_when_sisyphus_dir_absent(tmp_path):
    """CREO-S33: only .thanatos/ exists → return .thanatos/skill.yaml."""
    thanatos_dir = _make_dir(tmp_path, ".thanatos")
    _seed_skill_yaml(thanatos_dir)

    assert not (tmp_path / ".sisyphus" / "scenarios").exists(), (
        "test setup invariant violated: .sisyphus/scenarios/ must not exist"
    )

    result = resolve_skill_path(tmp_path)

    expected = tmp_path / ".thanatos" / "skill.yaml"
    assert pathlib.Path(result) == expected, (
        f"CREO-S33: expected {expected}, got {result}"
    )


# ─── CREO-S34: fallback when .sisyphus/scenarios/ is empty ───────────────────


@pytest.mark.integration
def test_creo_s34_fallback_when_sisyphus_dir_empty(tmp_path):
    """CREO-S34: empty .sisyphus/scenarios/ + .thanatos/ → return .thanatos/skill.yaml."""
    sisyphus_dir = tmp_path / ".sisyphus" / "scenarios"
    sisyphus_dir.mkdir(parents=True)
    assert list(sisyphus_dir.iterdir()) == [], (
        "test setup invariant violated: .sisyphus/scenarios/ must be empty"
    )

    thanatos_dir = _make_dir(tmp_path, ".thanatos")
    _seed_skill_yaml(thanatos_dir)

    result = resolve_skill_path(tmp_path)

    expected = tmp_path / ".thanatos" / "skill.yaml"
    assert pathlib.Path(result) == expected, (
        f"CREO-S34: expected {expected}, got {result}"
    )


# ─── CREO-S35: neither path exists raises SkillLoadError ─────────────────────


@pytest.mark.integration
def test_creo_s35_raises_when_neither_dir_exists(tmp_path):
    """CREO-S35: neither directory present → SkillLoadError naming both paths."""
    assert not (tmp_path / ".sisyphus" / "scenarios").exists()
    assert not (tmp_path / ".thanatos").exists()

    with pytest.raises(SkillLoadError) as excinfo:
        resolve_skill_path(tmp_path)

    msg = str(excinfo.value)
    assert ".sisyphus/scenarios" in msg, (
        f"CREO-S35: error message must name .sisyphus/scenarios path; got: {msg!r}"
    )
    assert ".thanatos" in msg, (
        f"CREO-S35: error message must name .thanatos path; got: {msg!r}"
    )
