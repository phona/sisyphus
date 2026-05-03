"""Tests for thanatos.skill.load_skill."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from thanatos.skill import Skill, SkillLoadError, load_skill, resolve_skill_path


def _write(tmp: Path, body: str) -> Path:
    p = tmp / "skill.yaml"
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return p


def test_valid_skill_loads(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        name: pytoya-web
        driver: playwright
        entry: $ENDPOINT
        fixtures:
          admin_login:
            user: admin
            pass: admin
        preflight:
          - assert: "a11y_node_count > 5"
        """,
    )
    skill = load_skill(p)
    assert isinstance(skill, Skill)
    assert skill.name == "pytoya-web"
    assert skill.driver == "playwright"
    assert skill.entry == "$ENDPOINT"
    assert skill.fixtures["admin_login"]["user"] == "admin"
    assert skill.preflight[0].assert_ == "a11y_node_count > 5"


def test_missing_driver_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        name: foo
        entry: $ENDPOINT
        """,
    )
    with pytest.raises(SkillLoadError, match="driver"):
        load_skill(p)


def test_unknown_driver_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        name: foo
        driver: ios
        entry: $ENDPOINT
        """,
    )
    with pytest.raises(SkillLoadError):
        load_skill(p)


def test_missing_entry_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        name: foo
        driver: http
        """,
    )
    with pytest.raises(SkillLoadError, match="entry"):
        load_skill(p)


# ---------------------------------------------------------------------------
# resolve_skill_path — R9 .sisyphus/scenarios/ → .thanatos/ fallback
# (REQ-impl-thanatos-scenarios-fallback-1777807813, scenarios CREO-S32..S35)
# ---------------------------------------------------------------------------


def _populate(dir_: Path, *names: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dir_ / n).write_text("placeholder", encoding="utf-8")


def test_resolve_sisyphus_scenarios_takes_priority(tmp_path: Path) -> None:
    """CREO-S32: both dirs exist → use .sisyphus/scenarios/."""
    _populate(tmp_path / ".sisyphus" / "scenarios", "skill.yaml")
    _populate(tmp_path / ".thanatos", "skill.yaml")

    resolved = resolve_skill_path(tmp_path)

    assert resolved == tmp_path / ".sisyphus" / "scenarios" / "skill.yaml"


def test_resolve_falls_back_to_thanatos_when_sisyphus_absent(tmp_path: Path) -> None:
    """CREO-S33: only .thanatos/ exists → use it."""
    _populate(tmp_path / ".thanatos", "skill.yaml")

    resolved = resolve_skill_path(tmp_path)

    assert resolved == tmp_path / ".thanatos" / "skill.yaml"


def test_resolve_falls_back_when_sisyphus_dir_empty(tmp_path: Path) -> None:
    """CREO-S34: empty .sisyphus/scenarios/ + .thanatos/ → use .thanatos/."""
    (tmp_path / ".sisyphus" / "scenarios").mkdir(parents=True)
    _populate(tmp_path / ".thanatos", "skill.yaml")

    resolved = resolve_skill_path(tmp_path)

    assert resolved == tmp_path / ".thanatos" / "skill.yaml"


def test_resolve_neither_path_raises(tmp_path: Path) -> None:
    """CREO-S35: neither dir present → SkillLoadError."""
    with pytest.raises(SkillLoadError, match="no scenario path found"):
        resolve_skill_path(tmp_path)


def test_resolve_custom_filename(tmp_path: Path) -> None:
    """resolve_skill_path joins ``filename`` onto the chosen dir."""
    _populate(tmp_path / ".sisyphus" / "scenarios", "feature.yaml")

    resolved = resolve_skill_path(tmp_path, filename="feature.yaml")

    assert resolved == tmp_path / ".sisyphus" / "scenarios" / "feature.yaml"


def test_resolve_then_load_full_pipeline(tmp_path: Path) -> None:
    """End-to-end: resolve_skill_path output is loadable by load_skill."""
    sisyphus_dir = tmp_path / ".sisyphus" / "scenarios"
    sisyphus_dir.mkdir(parents=True)
    (sisyphus_dir / "skill.yaml").write_text(
        textwrap.dedent(
            """
            name: from-sisyphus-dir
            driver: http
            entry: $ENDPOINT
            """,
        ).lstrip("\n"),
        encoding="utf-8",
    )

    skill = load_skill(resolve_skill_path(tmp_path))

    assert skill.name == "from-sisyphus-dir"
