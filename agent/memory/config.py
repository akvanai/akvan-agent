"""Memory configuration from ~/.akvan/config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from agent.config import akvan_home

MemoryNotifications = Literal["off", "on", "verbose"]


@dataclass(frozen=True)
class MemoryConfig:
    memory_enabled: bool = True
    user_profile_enabled: bool = True
    memory_char_limit: int = 2200
    user_char_limit: int = 1375
    nudge_interval: int = 10
    memory_notifications: MemoryNotifications = "on"


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


def load_memory_config(*, project_root: Path | None = None) -> MemoryConfig:
    data = _load_yaml(project_root=project_root)
    memory = data.get("memory")
    display = data.get("display")
    mem_cfg = memory if isinstance(memory, dict) else {}
    display_cfg = display if isinstance(display, dict) else {}

    notifications = str(
        display_cfg.get(
            "review_notifications",
            display_cfg.get("memory_notifications", "on"),
        )
    ).lower()
    if notifications not in {"off", "on", "verbose"}:
        notifications = "on"

    return MemoryConfig(
        memory_enabled=bool(mem_cfg.get("memory_enabled", True)),
        user_profile_enabled=bool(mem_cfg.get("user_profile_enabled", True)),
        memory_char_limit=int(mem_cfg.get("memory_char_limit", 2200)),
        user_char_limit=int(mem_cfg.get("user_char_limit", 1375)),
        nudge_interval=int(mem_cfg.get("nudge_interval", 10)),
        memory_notifications=notifications,  # type: ignore[arg-type]
    )


def is_memory_enabled(*, project_root: Path | None = None) -> bool:
    cfg = load_memory_config(project_root=project_root)
    return cfg.memory_enabled or cfg.user_profile_enabled
