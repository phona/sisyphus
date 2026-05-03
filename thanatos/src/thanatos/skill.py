"""Load and validate ``<repo>/.thanatos/skill.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

DriverName = Literal["playwright", "adb", "http"]


class PreflightCheck(BaseModel):
    assert_: str = Field(alias="assert")

    model_config = {"populate_by_name": True}


class Skill(BaseModel):
    name: str
    driver: DriverName
    entry: str
    fixtures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    preflight: list[PreflightCheck] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("skill.name must be non-empty")
        return v


class SkillLoadError(Exception):
    """Raised when skill.yaml is missing or fails schema validation."""


_SISYPHUS_SCENARIOS_DIR = ".sisyphus/scenarios"
_THANATOS_DIR = ".thanatos"


def resolve_skill_path(repo_root: str | Path, *, filename: str = "skill.yaml") -> Path:
    """Resolve a repository's skill yaml path with fallback.

    Two-step lookup, in order:

    1. ``<repo_root>/.sisyphus/scenarios/`` if the directory exists and contains
       at least one entry — return ``<repo_root>/.sisyphus/scenarios/<filename>``.
    2. Otherwise ``<repo_root>/.thanatos/`` if that directory exists — return
       ``<repo_root>/.thanatos/<filename>``.

    Raises :class:`SkillLoadError` when neither directory is present.

    Note: this function only resolves the *directory* — the returned file
    itself is not opened or validated here. ``load_skill`` is responsible for
    surfacing missing-file / schema errors when the resolved path is read.
    """
    root = Path(repo_root)
    sisyphus_dir = root / _SISYPHUS_SCENARIOS_DIR
    thanatos_dir = root / _THANATOS_DIR

    if sisyphus_dir.is_dir() and any(sisyphus_dir.iterdir()):
        return sisyphus_dir / filename
    if thanatos_dir.is_dir():
        return thanatos_dir / filename
    raise SkillLoadError(
        f"no scenario path found: tried {sisyphus_dir} and {thanatos_dir}"
    )


def load_skill(path: str | Path) -> Skill:
    """Load skill.yaml from a path.

    Raises :class:`SkillLoadError` for missing files, malformed yaml, or schema
    violations (missing ``driver`` / unknown driver / missing ``entry``, etc.).
    """
    p = Path(path)
    if not p.is_file():
        raise SkillLoadError(f"skill file not found: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SkillLoadError(f"invalid yaml in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise SkillLoadError(f"skill yaml must be a mapping, got {type(raw).__name__}")
    try:
        return Skill.model_validate(raw)
    except ValidationError as e:
        raise SkillLoadError(f"skill schema error in {p}:\n{e}") from e
