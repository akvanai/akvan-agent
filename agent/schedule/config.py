"""Schedule configuration from ~/.akvan/config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool = True
    tick_interval_seconds: int = 60
    wrap_response: bool = True


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


def load_schedule_config(*, project_root: Path | None = None) -> ScheduleConfig:
    data = _load_yaml(project_root=project_root)
    raw = data.get("schedule")
    cfg = raw if isinstance(raw, dict) else {}
    interval = int(cfg.get("tick_interval_seconds", 60))
    if interval < 5:
        interval = 5
    return ScheduleConfig(
        enabled=bool(cfg.get("enabled", True)),
        tick_interval_seconds=interval,
        wrap_response=bool(cfg.get("wrap_response", True)),
    )


def is_schedule_enabled(*, project_root: Path | None = None) -> bool:
    return load_schedule_config(project_root=project_root).enabled
