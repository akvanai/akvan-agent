"""Web tool configuration from ~/.akvan/.env and ~/.akvan/config.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, set_key

from agent.config import akvan_home

SUPPORTED_SEARCH_BACKENDS = frozenset({"searxng", "ddgs", "firecrawl"})
SUPPORTED_EXTRACT_BACKENDS = frozenset({"firecrawl"})
DEFAULT_FIRECRAWL_URL = "http://localhost:3002"


def config_yaml_path(*, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / "config.yaml"


def env_path(*, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / ".env"


def _load_dotenv(*, project_root: Path | None = None) -> dict[str, str | None]:
    root = project_root or Path.cwd()
    global_root = akvan_home()
    values: dict[str, str | None] = {}
    for key, value in os.environ.items():
        values[key] = value
    for path in (global_root / ".env", root / ".env"):
        if path.exists():
            values.update(dotenv_values(path))
    return values


def _env(dotenv: dict[str, str | None], key: str, default: str = "") -> str:
    return os.getenv(key, dotenv.get(key) or default).strip()


def get_env_value(key: str, *, project_root: Path | None = None, default: str = "") -> str:
    """Resolve a web-tool env var from the process env or ~/.akvan/.env."""

    dotenv = _load_dotenv(project_root=project_root)
    return _env(dotenv, key, default)


def load_web_yaml(*, project_root: Path | None = None) -> dict[str, Any]:
    path = config_yaml_path(project_root=project_root)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    web = data.get("web")
    return web if isinstance(web, dict) else {}


def save_web_yaml(
    *,
    search_backend: str | None = None,
    extract_backend: str | None = None,
    backend: str | None = None,
    project_root: Path | None = None,
) -> Path:
    path = config_yaml_path(project_root=project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, yaml.YAMLError):
            pass
    web = data.setdefault("web", {})
    if not isinstance(web, dict):
        web = {}
        data["web"] = web
    if search_backend is not None:
        web["search_backend"] = search_backend
    if extract_backend is not None:
        web["extract_backend"] = extract_backend
    if backend is not None:
        web["backend"] = backend
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    return path


def save_web_env(
    values: dict[str, str],
    *,
    project_root: Path | None = None,
) -> Path:
    path = env_path(project_root=project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    for key, value in values.items():
        if value:
            set_key(str(path), key, value, quote_mode="never")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _ddgs_importable() -> bool:
    try:
        import ddgs  # noqa: F401

        return True
    except ImportError:
        return False


def is_backend_available(backend: str, *, project_root: Path | None = None) -> bool:
    dotenv = _load_dotenv(project_root=project_root)
    if backend == "searxng":
        return bool(_env(dotenv, "SEARXNG_URL"))
    if backend == "ddgs":
        return _ddgs_importable()
    if backend == "firecrawl":
        return bool(
            _env(dotenv, "FIRECRAWL_API_KEY") or _env(dotenv, "FIRECRAWL_API_URL")
        )
    return False


def _shared_backend(web_cfg: dict[str, Any], dotenv: dict[str, str | None]) -> str:
    configured = str(web_cfg.get("backend") or "").lower().strip()
    if configured and is_backend_available(configured):
        return configured
    env_backend = _env(dotenv, "AKVAN_WEB_BACKEND").lower()
    if env_backend and is_backend_available(env_backend):
        return env_backend
    for candidate in ("searxng", "ddgs", "firecrawl"):
        if is_backend_available(candidate):
            return candidate
    return ""


def get_search_backend(*, project_root: Path | None = None) -> str:
    web_cfg = load_web_yaml(project_root=project_root)
    dotenv = _load_dotenv(project_root=project_root)
    specific = str(web_cfg.get("search_backend") or "").lower().strip()
    if not specific:
        specific = _env(dotenv, "AKVAN_WEB_SEARCH_BACKEND").lower()
    if specific and is_backend_available(specific):
        return specific
    return _shared_backend(web_cfg, dotenv)


def get_extract_backend(*, project_root: Path | None = None) -> str:
    web_cfg = load_web_yaml(project_root=project_root)
    dotenv = _load_dotenv(project_root=project_root)
    specific = str(web_cfg.get("extract_backend") or "").lower().strip()
    if not specific:
        specific = _env(dotenv, "AKVAN_WEB_EXTRACT_BACKEND").lower()
    if specific and is_backend_available(specific):
        return specific
    shared = _shared_backend(web_cfg, dotenv)
    if shared and is_backend_available(shared) and shared in SUPPORTED_EXTRACT_BACKENDS:
        return shared
    if is_backend_available("firecrawl", project_root=project_root):
        return "firecrawl"
    return ""


def is_search_configured(*, project_root: Path | None = None) -> bool:
    backend = get_search_backend(project_root=project_root)
    return bool(backend) and is_backend_available(backend, project_root=project_root)


def is_extract_configured(*, project_root: Path | None = None) -> bool:
    backend = get_extract_backend(project_root=project_root)
    return bool(backend) and is_backend_available(backend, project_root=project_root)


def is_web_configured(*, project_root: Path | None = None) -> bool:
    return is_search_configured(project_root=project_root) or is_extract_configured(
        project_root=project_root
    )


def web_env_values(*, project_root: Path | None = None) -> dict[str, str]:
    dotenv = _load_dotenv(project_root=project_root)
    keys = (
        "SEARXNG_URL",
        "FIRECRAWL_API_URL",
        "FIRECRAWL_API_KEY",
        "AKVAN_WEB_SEARCH_BACKEND",
        "AKVAN_WEB_EXTRACT_BACKEND",
        "AKVAN_WEB_EXTRACT_SUMMARY_MODEL",
    )
    return {key: _env(dotenv, key) for key in keys}
