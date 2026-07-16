"""Configuration for context-window protection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home


@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    context_length: int | None = None
    max_output_tokens: int = 8192
    compression_enabled: bool = True
    compression_threshold: float = 0.50
    protect_first_messages: int = 3
    protect_recent_ratio: float = 0.20
    summary_max_chars: int = 24_000
    persist_oversized_results: bool = True
    max_result_chars: int = 100_000
    max_turn_chars: int = 200_000
    result_preview_chars: int = 1_500
    result_retention_days: int = 7
    tool_search_enabled: str = "auto"
    tool_schema_threshold: float = 0.10
    skill_warn_main_chars: int = 50_000
    skill_warn_file_count: int = 50
    skill_warn_total_bytes: int = 1_048_576


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return _mapping(value)


def _positive_int(value: object, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _ratio(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0.05 <= parsed <= 0.95 else default


def _boolean(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    return default


def load_context_config(*, project_root: Path | None = None) -> ContextConfig:
    """Load global config, then overlay project config when present."""

    data = _read_yaml(akvan_home() / "config.yaml")
    if project_root is not None:
        project_path = project_root / "config.yaml"
        if project_path.resolve() != (akvan_home() / "config.yaml").resolve():
            project = _read_yaml(project_path)
            if project:
                merged = dict(data)
                merged.update(project)
                data = merged

    context = _mapping(data.get("context"))
    compression = _mapping(context.get("compression"))
    results = _mapping(context.get("tool_results"))
    tool_search = _mapping(context.get("tool_search"))
    skills = _mapping(context.get("skills"))
    configured_length = context.get("context_length")
    context_length = (
        _positive_int(configured_length, 0, minimum=4096)
        if configured_length is not None
        else None
    )
    if context_length == 0:
        context_length = None
    search_mode = str(tool_search.get("enabled", "auto")).strip().lower()
    if search_mode not in {"auto", "on", "off"}:
        search_mode = "auto"

    return ContextConfig(
        enabled=_boolean(context.get("enabled"), True),
        context_length=context_length,
        max_output_tokens=_positive_int(
            context.get("max_output_tokens"), 8192, minimum=0
        ),
        compression_enabled=_boolean(compression.get("enabled"), True),
        compression_threshold=_ratio(compression.get("threshold"), 0.50),
        protect_first_messages=_positive_int(
            compression.get("protect_first_messages"), 3, minimum=0
        ),
        protect_recent_ratio=_ratio(
            compression.get("protect_recent_ratio"), 0.20
        ),
        summary_max_chars=_positive_int(
            compression.get("summary_max_chars"), 24_000, minimum=2_000
        ),
        persist_oversized_results=_boolean(
            results.get("persist_oversized"), True
        ),
        max_result_chars=_positive_int(
            results.get("max_result_chars"), 100_000, minimum=8_000
        ),
        max_turn_chars=_positive_int(
            results.get("max_turn_chars"), 200_000, minimum=16_000
        ),
        result_preview_chars=_positive_int(
            results.get("preview_chars"), 1_500, minimum=200
        ),
        result_retention_days=_positive_int(
            results.get("retention_days"), 7, minimum=1
        ),
        tool_search_enabled=search_mode,
        tool_schema_threshold=_ratio(
            tool_search.get("schema_threshold"), 0.10
        ),
        skill_warn_main_chars=_positive_int(
            skills.get("warn_main_chars"), 50_000, minimum=1_000
        ),
        skill_warn_file_count=_positive_int(
            skills.get("warn_file_count"), 50, minimum=1
        ),
        skill_warn_total_bytes=_positive_int(
            skills.get("warn_total_bytes"), 1_048_576, minimum=16_000
        ),
    )
