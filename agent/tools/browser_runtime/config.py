"""Configuration for optional browser runtime backed tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from agent.config import akvan_home
from agent.storage.permissions import (
    ensure_private_file,
    harden_akvan_home,
    is_under_akvan_home,
    prepare_akvan_parent,
)

DEFAULT_RUNTIME_MODE = "local"
DEFAULT_RUNTIME_HOST = "127.0.0.1"
DEFAULT_RUNTIME_PORT = 49733
DEFAULT_BANNER_TEMPLATE = "announcement-basic"
DEFAULT_BANNER_SIZE = "x_landscape"
DEFAULT_BANNER_ROOT_NAME = "banners"
DEFAULT_BROWSER_INACTIVITY_TIMEOUT = 900
DEFAULT_PROFILES_DIR_NAME = "browser/profiles"
CONTAINER_PROFILES_DIR = "/akvan-browser-profiles"

BANNER_SIZE_PRESETS: dict[str, dict[str, int]] = {
    "x_landscape": {"width": 1200, "height": 675},
    "square": {"width": 1080, "height": 1080},
    "story": {"width": 1080, "height": 1920},
}


def config_yaml_path(*, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / "config.yaml"


def _read_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_yaml(*, project_root: Path | None = None) -> dict[str, Any]:
    global_path = akvan_home() / "config.yaml"
    root_path = config_yaml_path(project_root=project_root)
    data = _read_yaml_file(global_path)
    if root_path.resolve() == global_path.resolve():
        return data

    for key, value in _read_yaml_file(root_path).items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return data


def _save_yaml(data: dict[str, Any], *, project_root: Path | None = None) -> Path:
    path = config_yaml_path(project_root=project_root)
    prepare_akvan_parent(path)
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    if is_under_akvan_home(path):
        ensure_private_file(path)
    return path


def _section(name: str, *, project_root: Path | None = None) -> dict[str, Any]:
    value = _load_yaml(project_root=project_root).get(name)
    return value if isinstance(value, dict) else {}


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _path(value: Any, default: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return default
    return Path(os.path.expandvars(text)).expanduser()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def browser_runtime_config(*, project_root: Path | None = None) -> dict[str, Any]:
    cfg = _section("browser_runtime", project_root=project_root)
    mode = str(cfg.get("mode") or DEFAULT_RUNTIME_MODE).lower().strip()
    if mode not in {"local", "docker"}:
        mode = DEFAULT_RUNTIME_MODE
    port = _int(cfg.get("port"), DEFAULT_RUNTIME_PORT)
    return {
        "enabled": _bool(cfg.get("enabled"), False),
        "mode": mode,
        "host": str(cfg.get("host") or DEFAULT_RUNTIME_HOST).strip() or DEFAULT_RUNTIME_HOST,
        "port": port if 1 <= port <= 65535 else DEFAULT_RUNTIME_PORT,
        "token": str(cfg.get("token") or os.getenv("AKVAN_BROWSER_RUNTIME_TOKEN") or "").strip(),
        "image": str(cfg.get("image") or "akvan-agent-browser-runtime:playwright-1.52.0"),
        "base_image": str(cfg.get("base_image") or "mcr.microsoft.com/playwright/python:v1.52.0-noble"),
        "container_name": str(cfg.get("container_name") or "akvan-agent-browser-runtime"),
    }


def banner_generation_config(*, project_root: Path | None = None) -> dict[str, Any]:
    cfg = _section("banner_generation", project_root=project_root)
    root_dir = _path(cfg.get("root_dir"), akvan_home() / DEFAULT_BANNER_ROOT_NAME)
    return {
        "enabled": _bool(cfg.get("enabled"), False),
        "root_dir": root_dir,
        "templates_dir": root_dir / "templates",
        "output_dir": root_dir / "renders",
        "assets_dir": root_dir / "assets",
        "default_template": str(cfg.get("default_template") or DEFAULT_BANNER_TEMPLATE),
        "default_size": str(cfg.get("default_size") or DEFAULT_BANNER_SIZE),
    }


def browser_config(*, project_root: Path | None = None) -> dict[str, Any]:
    cfg = _section("browser", project_root=project_root)
    timeout = _int(cfg.get("inactivity_timeout_seconds"), DEFAULT_BROWSER_INACTIVITY_TIMEOUT)
    return {
        "enabled": _bool(cfg.get("enabled"), False),
        "inactivity_timeout_seconds": max(60, timeout),
        "profiles_dir": profiles_dir(project_root=project_root),
    }


def profiles_dir(*, project_root: Path | None = None) -> Path:
    cfg = _section("browser", project_root=project_root)
    return _path(cfg.get("profiles_dir"), akvan_home() / DEFAULT_PROFILES_DIR_NAME)


def x_account_legacy_auth_path(*, project_root: Path | None = None) -> Path:
    """Legacy X auth path for one-shot migration into browser profiles."""

    cfg = _section("x_account", project_root=project_root)
    return _path(cfg.get("auth_state_path"), akvan_home() / "x" / "auth.json")


def runtime_base_url(*, project_root: Path | None = None) -> str:
    cfg = browser_runtime_config(project_root=project_root)
    return f"http://{cfg['host']}:{cfg['port']}"


def is_browser_runtime_configured(*, project_root: Path | None = None) -> bool:
    return bool(browser_runtime_config(project_root=project_root)["enabled"])


def is_docker_browser_runtime(*, project_root: Path | None = None) -> bool:
    cfg = browser_runtime_config(project_root=project_root)
    return bool(cfg["enabled"] and cfg["mode"] == "docker")


def is_banner_generation_configured(*, project_root: Path | None = None) -> bool:
    cfg = banner_generation_config(project_root=project_root)
    return bool(cfg["enabled"] and is_browser_runtime_configured(project_root=project_root))


def is_browser_configured(*, project_root: Path | None = None) -> bool:
    migrate_legacy_x_account_config(project_root=project_root)
    cfg = browser_config(project_root=project_root)
    return bool(cfg["enabled"] and is_browser_runtime_configured(project_root=project_root))


def migrate_legacy_x_account_config(*, project_root: Path | None = None) -> dict[str, Any]:
    """Enable interactive browser tools from legacy x_account and migrate auth profile once.

    Safe to call repeatedly: no-ops when there is nothing to migrate.
    """

    path = config_yaml_path(project_root=project_root)
    data = _read_yaml_file(path)
    x_cfg = data.get("x_account") if isinstance(data.get("x_account"), dict) else {}
    browser_cfg = data.get("browser") if isinstance(data.get("browser"), dict) else {}
    runtime_cfg = data.get("browser_runtime") if isinstance(data.get("browser_runtime"), dict) else {}

    x_enabled = _bool(x_cfg.get("enabled"), False)
    runtime_enabled = _bool(runtime_cfg.get("enabled"), False)
    browser_key_present = "enabled" in browser_cfg
    browser_enabled = _bool(browser_cfg.get("enabled"), False)

    changed = False
    migrated_profile = None

    # Turn on interactive browser when the old X toolset was enabled and browser
    # has not been explicitly configured yet.
    if x_enabled and runtime_enabled and not browser_key_present:
        browser_section = data.setdefault("browser", {})
        if not isinstance(browser_section, dict):
            browser_section = {}
            data["browser"] = browser_section
        browser_section["enabled"] = True
        browser_section.setdefault(
            "inactivity_timeout_seconds", DEFAULT_BROWSER_INACTIVITY_TIMEOUT
        )
        changed = True
        browser_enabled = True

    if x_enabled:
        x_section = data.setdefault("x_account", {})
        if not isinstance(x_section, dict):
            x_section = {}
            data["x_account"] = x_section
        if _bool(x_section.get("enabled"), False):
            x_section["enabled"] = False
            changed = True

    if changed:
        _save_yaml(data, project_root=project_root)
        if is_under_akvan_home(path):
            harden_akvan_home(path.parent)

    if browser_enabled or x_enabled or runtime_enabled:
        try:
            from agent.tools.browser_runtime.profiles import migrate_legacy_x_auth

            migrated_profile = migrate_legacy_x_auth(project_root=project_root)
        except Exception:
            migrated_profile = None

    return {
        "changed": changed,
        "browser_enabled": bool(
            browser_config(project_root=project_root)["enabled"]
            and is_browser_runtime_configured(project_root=project_root)
        ),
        "migrated_profile": migrated_profile,
    }


def save_browser_tools_yaml(
    *,
    browser_runtime: dict[str, Any] | None = None,
    banner_generation: dict[str, Any] | None = None,
    browser: dict[str, Any] | None = None,
    x_account: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> Path:
    data = _read_yaml_file(config_yaml_path(project_root=project_root))
    for key, value in (
        ("browser_runtime", browser_runtime),
        ("banner_generation", banner_generation),
        ("browser", browser),
        ("x_account", x_account),
    ):
        if value is None:
            continue
        section = data.setdefault(key, {})
        if not isinstance(section, dict):
            section = {}
            data[key] = section
        section.update(value)
    path = _save_yaml(data, project_root=project_root)
    if is_under_akvan_home(path):
        harden_akvan_home(path.parent)
    return path
