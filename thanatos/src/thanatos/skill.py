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
