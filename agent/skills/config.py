"""Skills configuration from ~/.akvan/config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home


@dataclass(frozen=True)
class SkillsConfig:
    creation_nudge_interval: int = 10


@dataclass(frozen=True)
class CuratorConfig:
    archive_after_days: int = 90
    auto_archive: bool = False


def config_yaml_path(*, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / "config.yaml"


def _load_yaml(*, project_root: Path | None = None) -> dict[str, Any]:
    path = config_yaml_path(project_root=project_root)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def load_skills_config(*, project_root: Path | None = None) -> SkillsConfig:
    data = _load_yaml(project_root=project_root)
    skills = data.get("skills")
    cfg = skills if isinstance(skills, dict) else {}
    return SkillsConfig(
        creation_nudge_interval=int(cfg.get("creation_nudge_interval", 10)),
    )


def load_curator_config(*, project_root: Path | None = None) -> CuratorConfig:
    data = _load_yaml(project_root=project_root)
    curator = data.get("curator")
    cfg = curator if isinstance(curator, dict) else {}
    return CuratorConfig(
        archive_after_days=int(cfg.get("archive_after_days", 90)),
        auto_archive=bool(cfg.get("auto_archive", False)),
    )
