"""Global knowledge configuration from ``~/.akvan/config.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home


@dataclass(frozen=True)
class KnowledgeConfig:
    enabled: bool = True
    review_interval: int = 15
    auto_save_explicit_facts: bool = True
    max_concept_chars: int = 20_000


def load_knowledge_config(*, project_root: Path | None = None) -> KnowledgeConfig:
    """Load knowledge settings from the global Akvan configuration."""
    _ = project_root  # Reserved for API compatibility; v1 knowledge is global-only.
    path = akvan_home() / "config.yaml"
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        data = {}
    root = data if isinstance(data, dict) else {}
    raw = root.get("knowledge")
    cfg = raw if isinstance(raw, dict) else {}
    return KnowledgeConfig(
        enabled=bool(cfg.get("enabled", True)),
        review_interval=max(0, int(cfg.get("review_interval", 15))),
        auto_save_explicit_facts=bool(cfg.get("auto_save_explicit_facts", True)),
        max_concept_chars=max(1_000, int(cfg.get("max_concept_chars", 20_000))),
    )


def is_knowledge_enabled(*, project_root: Path | None = None) -> bool:
    return load_knowledge_config(project_root=project_root).enabled
