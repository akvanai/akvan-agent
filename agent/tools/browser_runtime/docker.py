"""Docker lifecycle for Akvan's bundled browser runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agent.tools.browser_runtime.config import x_account_config

DEFAULT_DOCKER_BASE_IMAGE = "mcr.microsoft.com/playwright/python:v1.52.0-noble"
DEFAULT_DOCKER_IMAGE = "akvan-agent-browser-runtime:playwright-1.52.0"
DEFAULT_CONTAINER_NAME = "akvan-agent-browser-runtime"
CONTAINER_AUTH_DIR = "/akvan-auth"


class DockerRuntimeError(RuntimeError):
    pass


def ensure_docker_runtime(*, config: dict[str, Any], project_root: Path | None = None) -> None:
    """Ensure Akvan's Docker browser runtime is running for the configured port."""

    if config.get("mode") != "docker":
        return
    _require_docker()
    container_name = str(config.get("container_name") or DEFAULT_CONTAINER_NAME)
    port = int(config["port"])
    host = str(config.get("host") or "127.0.0.1")
    image = str(config.get("image") or DEFAULT_DOCKER_IMAGE)
    base_image = str(config.get("base_image") or DEFAULT_DOCKER_BASE_IMAGE)
    package_root = Path(__file__).resolve().parents[3]
    x_cfg = x_account_config(project_root=project_root)
    auth_path = Path(x_cfg["auth_state_path"]).expanduser()
    auth_dir = auth_path.parent
    auth_target = f"{CONTAINER_AUTH_DIR}/{auth_path.name}"

    _ensure_runtime_image(image=image, base_image=base_image)

    existing = _inspect(container_name)
    if existing and _matches(existing, port=port, image=image):
        if not _is_running(existing):
            _run(["docker", "start", container_name])
        return
    if existing:
        _run(["docker", "rm", "-f", container_name])

    auth_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--label",
        "app=akvan-agent-browser-runtime",
        "--label",
        f"akvan.runtime.port={port}",
        "--label",
        f"akvan.runtime.image={image}",
        "-p",
        f"{host}:{port}:{port}",
        "-v",
        f"{package_root}:/app:ro",
        "-v",
        f"{auth_dir}:{CONTAINER_AUTH_DIR}:ro",
        "-w",
        "/app",
        "-e",
        "PYTHONPATH=/app",
        "-e",
        f"AKVAN_X_AUTH_STATE_PATH={auth_target}",
        "-e",
        "AKVAN_BROWSER_RUNTIME_NAME=akvan-docker",
        image,
        "python3",
        "/app/agent/tools/browser_runtime/server.py",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--auth-state-path",
        auth_target,
        "--runtime",
        "akvan-docker",
    ]
    _run(cmd)


def remove_docker_runtime(*, container_name: str = DEFAULT_CONTAINER_NAME) -> bool:
    """Stop and remove Akvan's Docker browser runtime container if present."""

    existing = _inspect(container_name)
    if not existing:
        return False
    labels = existing.get("Config", {}).get("Labels") or {}
    if labels.get("app") != "akvan-agent-browser-runtime":
        return False
    _run(["docker", "rm", "-f", container_name])
    return True


def _ensure_runtime_image(*, image: str, base_image: str) -> None:
    if _image_exists(image):
        return
    dockerfile = f"""
FROM {base_image}
RUN python3 -m pip install --no-cache-dir playwright==1.52.0
""".lstrip()
    try:
        _run(["docker", "build", "-t", image, "-"], input_text=dockerfile)
    except DockerRuntimeError as exc:
        raise DockerRuntimeError(
            "Could not build Akvan browser runtime Docker image. "
            "Docker needs network access once to install the Python Playwright package. "
            f"Details: {exc}"
        ) from exc


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def _require_docker() -> None:
    try:
        _run(["docker", "version", "--format", "{{.Server.Version}}"])
    except DockerRuntimeError as exc:
        raise DockerRuntimeError(
            "Docker is required for browser_runtime.mode=docker. Start Docker and try again."
        ) from exc


def _inspect(container_name: str) -> dict[str, Any] | None:
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


def _matches(container: dict[str, Any], *, port: int, image: str) -> bool:
    labels = container.get("Config", {}).get("Labels") or {}
    return (
        labels.get("app") == "akvan-agent-browser-runtime"
        and labels.get("akvan.runtime.port") == str(port)
        and labels.get("akvan.runtime.image") == image
        and _has_published_port(container, port=port)
    )


def _has_published_port(container: dict[str, Any], *, port: int) -> bool:
    network_ports = container.get("NetworkSettings", {}).get("Ports") or {}
    bindings = network_ports.get(f"{port}/tcp") or []
    return any(item.get("HostPort") == str(port) for item in bindings if isinstance(item, dict))


def _is_running(container: dict[str, Any]) -> bool:
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
        raise DockerRuntimeError(detail or f"Command failed: {' '.join(cmd)}")
    return result.stdout.strip()
