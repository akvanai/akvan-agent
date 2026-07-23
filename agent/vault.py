"""Agent media vault under Akvan home (files/images only, not secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home

DEFAULT_VAULT_NAME = "vault"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _configured_root() -> Path | None:
    section = _read_yaml(akvan_home() / "config.yaml").get("vault")
    if not isinstance(section, dict):
        return None
    text = str(section.get("root_dir") or "").strip()
    if not text:
        return None
    return Path(os.path.expandvars(text)).expanduser()


def vault_dir() -> Path:
    """Return the agent media vault directory (default ``~/.akvan/vault``)."""

    configured = _configured_root()
    if configured is not None:
        return configured
    return akvan_home() / DEFAULT_VAULT_NAME


def ensure_vault() -> Path:
    """Create the vault directory with owner-only permissions."""

    from agent.storage.permissions import ensure_private_dir

    return ensure_private_dir(vault_dir())


def is_under_vault(path: Path) -> bool:
    """Return True when path is inside the agent media vault."""

    try:
        path.resolve(strict=False).relative_to(vault_dir().resolve())
    except ValueError:
        return False
    return True
