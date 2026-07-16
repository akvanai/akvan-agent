"""Logging configuration from ~/.akvan/config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    max_size_mb: int = 5
    backup_count: int = 3
    console: bool = False


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


def load_logging_config(*, project_root: Path | None = None) -> LoggingConfig:
    data = _load_yaml(project_root=project_root)
    log_cfg = data.get("logging")
    cfg = log_cfg if isinstance(log_cfg, dict) else {}

    level = str(cfg.get("level", "INFO")).upper()
    if level not in _VALID_LEVELS:
        level = "INFO"

    env_level = os.getenv("AKVAN_LOG_LEVEL", "").strip().upper()
    if env_level in _VALID_LEVELS:
        level = env_level

    max_size_mb = int(cfg.get("max_size_mb", 5))
    if max_size_mb < 1:
        max_size_mb = 1

    backup_count = int(cfg.get("backup_count", 3))
    if backup_count < 1:
        backup_count = 1

    return LoggingConfig(
        level=level,
        max_size_mb=max_size_mb,
        backup_count=backup_count,
        console=bool(cfg.get("console", False)),
    )
