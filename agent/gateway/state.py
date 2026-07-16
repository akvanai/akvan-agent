"""Persisted gateway enable/disable state."""

from __future__ import annotations

import json
from pathlib import Path

from agent.config import akvan_home
from agent.storage.permissions import (
    ensure_private_file,
    harden_akvan_home,
    is_under_akvan_home,
    replace_private_file,
)

_STATE_FILE = "gateway-state.json"


def gateway_state_path(project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / _STATE_FILE


def _load_raw(project_root: Path | None = None) -> dict[str, dict[str, object]]:
    path = gateway_state_path(project_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _save_raw(state: dict[str, dict[str, object]], project_root: Path | None = None) -> Path:
    root = project_root or akvan_home()
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    path = gateway_state_path(root)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    ensure_private_file(path)
    return path


def is_gateway_enabled(gateway_id: str, *, project_root: Path | None = None) -> bool:
    entry = _load_raw(project_root).get(gateway_id, {})
    return bool(entry.get("enabled"))


def set_gateway_enabled(
    gateway_id: str,
    enabled: bool,
    *,
    project_root: Path | None = None,
) -> None:
    state = _load_raw(project_root)
    entry = dict(state.get(gateway_id, {}))
    entry["enabled"] = enabled
    state[gateway_id] = entry
    _save_raw(state, project_root)
