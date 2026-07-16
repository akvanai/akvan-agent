"""Docker lifecycle for Akvan-managed local SearXNG."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

from agent.storage.permissions import ensure_private_dir, ensure_private_file, is_under_akvan_home
from agent.tools.web.config import get_env_value, save_web_env
from agent.tools.web.searxng_runtime.config import (
    BUNDLE_DIR,
    DEFAULT_CONTAINER_NAME,
    DEFAULT_INSTANCE_NAME,
    DEFAULT_SEARXNG_HOST,
    DEFAULT_SEARXNG_IMAGE,
    runtime_config_dir,
)


class SearXNGRuntimeError(RuntimeError):
    pass


def ensure_searxng_runtime(
    *,
    port: int,
    host: str = DEFAULT_SEARXNG_HOST,
    project_root: Path | None = None,
) -> str:
    """Ensure Akvan's managed SearXNG container is running and return its base URL."""

    if port < 1 or port > 65535:
        raise SearXNGRuntimeError("Port must be between 1 and 65535.")
    _require_docker()

    config_dir = _materialize_runtime_config(project_root=project_root)
    secret = _ensure_secret(project_root=project_root)
    base_url = f"http://{host}:{port}"
    container_name = DEFAULT_CONTAINER_NAME
    image = DEFAULT_SEARXNG_IMAGE

    existing = _inspect(container_name)
    if existing and _matches(existing, port=port, host=host, image=image):
        if not _is_running(existing):
            _run(["docker", "start", container_name])
        return base_url
    if existing:
        _run(["docker", "rm", "-f", container_name])

    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--label",
        "app=akvan-agent-searxng",
        "--label",
        f"akvan.searxng.port={port}",
        "--label",
        f"akvan.searxng.host={host}",
        "--label",
        f"akvan.searxng.image={image}",
        "-p",
        f"{host}:{port}:8080",
        "-v",
        f"{config_dir / 'searxng_settings.yml'}:/etc/searxng/settings.yml:ro",
        "-v",
        f"{config_dir / 'searxng_limiter.toml'}:/etc/searxng/limiter.toml:ro",
        "-v",
        f"{config_dir / 'data'}:/var/lib/searxng",
        "-e",
        f"SEARXNG_BASE_URL={base_url}/",
        "-e",
        f"SEARXNG_INSTANCE_NAME={DEFAULT_INSTANCE_NAME}",
        "-e",
        f"SEARXNG_SECRET={secret}",
        image,
    ]
    _run(cmd)
    return base_url


def has_matching_searxng_runtime(
    *,
    port: int,
    host: str = DEFAULT_SEARXNG_HOST,
    container_name: str = DEFAULT_CONTAINER_NAME,
) -> bool:
    """Return whether Akvan already owns the requested SearXNG port."""

    existing = _inspect(container_name)
    return bool(existing and _matches(existing, port=port, host=host, image=DEFAULT_SEARXNG_IMAGE))


def remove_searxng_runtime(*, container_name: str = DEFAULT_CONTAINER_NAME) -> bool:
    """Stop and remove Akvan's managed SearXNG container if present."""

    existing = _inspect(container_name)
    if not existing:
        return False
    labels = existing.get("Config", {}).get("Labels") or {}
    if labels.get("app") != "akvan-agent-searxng":
        return False
    _run(["docker", "rm", "-f", container_name])
    return True


def _materialize_runtime_config(*, project_root: Path | None = None) -> Path:
    config_dir = runtime_config_dir(project_root=project_root)
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir = config_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    for name in ("searxng_settings.yml", "searxng_limiter.toml"):
        target = config_dir / name
        if not target.exists():
            shutil.copy2(BUNDLE_DIR / name, target)

    if is_under_akvan_home(config_dir):
        ensure_private_dir(config_dir)
        ensure_private_dir(data_dir)
        for path in (config_dir / "searxng_settings.yml", config_dir / "searxng_limiter.toml"):
            if path.exists():
                ensure_private_file(path)
    return config_dir


def _ensure_secret(*, project_root: Path | None = None) -> str:
    secret = get_env_value("SEARXNG_SECRET", project_root=project_root)
    if secret:
        return secret
    secret = secrets.token_hex(32)
    save_web_env({"SEARXNG_SECRET": secret})
    return secret


def _require_docker() -> None:
    try:
        _run(["docker", "version", "--format", "{{.Server.Version}}"])
    except SearXNGRuntimeError as exc:
        raise SearXNGRuntimeError(
            "Docker is required for managed SearXNG. Start Docker and try again."
        ) from exc


def _inspect(container_name: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", container_name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout or "[]")
    return data[0] if data else None


def _matches(container: dict, *, port: int, host: str, image: str) -> bool:
    labels = container.get("Config", {}).get("Labels") or {}
    return (
        labels.get("app") == "akvan-agent-searxng"
        and labels.get("akvan.searxng.port") == str(port)
        and labels.get("akvan.searxng.host") == host
        and labels.get("akvan.searxng.image") == image
        and _has_published_port(container, host=host, port=port)
    )


def _has_published_port(container: dict, *, host: str, port: int) -> bool:
    network_ports = container.get("NetworkSettings", {}).get("Ports") or {}
    bindings = network_ports.get("8080/tcp") or []
    return any(
        item.get("HostIp") == host and item.get("HostPort") == str(port)
        for item in bindings
        if isinstance(item, dict)
    )


def _is_running(container: dict) -> bool:
    return bool(container.get("State", {}).get("Running"))


def _run(cmd: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SearXNGRuntimeError(detail or f"Command failed: {' '.join(cmd)}")
    return result.stdout.strip()
