"""Skill metadata and validation errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SkillOrigin = Literal["user", "project"]


class SkillError(ValueError):
    """Raised when a requested skill or resource is unavailable or unsafe."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    category: str
    root: Path
    origin: SkillOrigin

    @property
    def skill_file(self) -> Path:
        return self.root / "SKILL.md"
