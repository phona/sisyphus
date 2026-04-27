"""Tests for thanatos.skill.load_skill."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from thanatos.skill import Skill, SkillLoadError, load_skill


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
