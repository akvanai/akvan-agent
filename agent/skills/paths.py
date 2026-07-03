"""Filesystem locations for bundled and runtime skill packages."""

from __future__ import annotations

import os
from pathlib import Path

from agent.config import akvan_home

NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"
BUNDLED_MANIFEST = ".bundled_manifest"
SKILL_SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))


def user_skills_dir(user_root: Path | None = None) -> Path:
    if user_root is not None:
        return user_root / ".akvan" / "skills"
    return akvan_home() / "skills"


def project_skills_dir(project_root: Path) -> Path:
    return project_root / ".akvan" / "skills"


def bundled_skills_dir() -> Path:
    override = os.getenv("AKVAN_BUNDLED_SKILLS", "").strip()
    if override:
        return Path(override).expanduser()
    installed = akvan_home() / "app" / "skills"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[2] / "skills"
