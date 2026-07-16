"""Configuration for Akvan-managed local SearXNG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.config import akvan_home
from agent.tools.web.config import load_web_yaml

DEFAULT_SEARXNG_IMAGE = "searxng/searxng:latest"
DEFAULT_CONTAINER_NAME = "akvan-agent-searxng"
DEFAULT_SEARXNG_HOST = "127.0.0.1"
DEFAULT_SEARXNG_PORT = 8090
DEFAULT_INSTANCE_NAME = "Local Search"

BUNDLE_DIR = Path(__file__).resolve().parent


def is_managed_searxng(*, project_root: Path | None = None) -> bool:
    return searxng_runtime_config(project_root=project_root).get("mode") == "managed"


def runtime_config_dir(*, project_root: Path | None = None) -> Path:
    root = akvan_home() if project_root is None else project_root
    return root / "searxng"


def searxng_runtime_config(*, project_root: Path | None = None) -> dict[str, Any]:
    web_cfg = load_web_yaml(project_root=project_root)
    section = web_cfg.get("searxng")
    cfg = section if isinstance(section, dict) else {}
    mode = str(cfg.get("mode") or "").lower().strip()
    if mode not in {"managed", "external"}:
        mode = ""
    port = _int(cfg.get("port"), DEFAULT_SEARXNG_PORT)
    return {
        "mode": mode,
        "port": port if 1 <= port <= 65535 else DEFAULT_SEARXNG_PORT,
        "host": str(cfg.get("host") or DEFAULT_SEARXNG_HOST).strip() or DEFAULT_SEARXNG_HOST,
        "image": str(cfg.get("image") or DEFAULT_SEARXNG_IMAGE),
        "container_name": str(cfg.get("container_name") or DEFAULT_CONTAINER_NAME),
    }


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
