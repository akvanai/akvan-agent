"""Platform-neutral gateway runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.config import akvan_home


@dataclass(frozen=True)
class GatewayRuntimeConfig:
    """Platform-neutral delivery tuning consumed by the shared service."""

    stream_edit_interval: float = 0.8
    stream_transport: str = "auto"


def gateway_env_path(project_root: Path | None = None) -> Path:
    """Return the shared environment file used by gateway integrations."""
    return (project_root or akvan_home()) / ".env"
