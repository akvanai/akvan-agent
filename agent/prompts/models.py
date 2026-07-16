"""Immutable prompt snapshot data structures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.skills import SkillRegistry


@dataclass(frozen=True)
class PromptSource:
    kind: str
    path: Path | None
    content: str


@dataclass(frozen=True)
class PromptSnapshot:
    """The exact system prefix and discovery state frozen for a session."""

    content: str
    sources: tuple[PromptSource, ...]
    fingerprint: str
    skills: SkillRegistry
    project_root: Path
