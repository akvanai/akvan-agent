"""Docker lifecycle for Akvan's bundled browser runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agent.config import akvan_home
from agent.tools.browser_runtime.config import (
    CONTAINER_PROFILES_DIR,
    browser_config,
    profiles_dir,
)

DEFAULT_DOCKER_BASE_IMAGE = "mcr.microsoft.com/playwright/python:v1.52.0-noble"
DEFAULT_DOCKER_IMAGE = "akvan-agent-browser-runtime:playwright-1.52.0"
DEFAULT_CONTAINER_NAME = "akvan-agent-browser-runtime"
# Bump when runtime HTTP API / mounts change so stale containers are recreated.
RUNTIME_API_VERSION = "4"


class DockerRuntimeError(RuntimeError):
    pass


def _package_root() -> Path:
    """Return the host tree mounted at /app inside the Docker runtime."""

    app = akvan_home() / "app"
    marker = Path("agent") / "tools" / "browser_runtime" / "server.py"
    if (app / marker).is_file():
        return app.resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / marker).is_file():
            return parent
    return here.parents[3]


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
    package_root = _package_root()
    profiles_root = profiles_dir(project_root=project_root)
    profiles_root.mkdir(parents=True, exist_ok=True)
    timeout = int(browser_config(project_root=project_root)["inactivity_timeout_seconds"])

    _ensure_runtime_image(image=image, base_image=base_image)

    existing = _inspect(container_name)
    if existing and _matches(existing, port=port, image=image):
        if not _is_running(existing):
            _run(["docker", "start", container_name])
            existing = _inspect(container_name) or existing
        # install.sh replaces ~/.akvan/app via mv; Docker can keep the old inode mounted.
        if _app_mount_healthy(container_name, existing):
            return
        _run(["docker", "rm", "-f", container_name])
    elif existing:
        _run(["docker", "rm", "-f", container_name])

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
        "--label",
        f"akvan.runtime.api={RUNTIME_API_VERSION}",
        "-p",
        f"{host}:{port}:{port}",
        "-v",
        f"{package_root}:/app:ro",
        "-v",
        f"{profiles_root}:{CONTAINER_PROFILES_DIR}",
        "-w",
        "/app",
        "-e",
        "PYTHONPATH=/app",
        "-e",
        f"AKVAN_BROWSER_PROFILES_DIR={CONTAINER_PROFILES_DIR}",
        "-e",
        f"AKVAN_BROWSER_INACTIVITY_TIMEOUT={timeout}",
        "-e",
        "AKVAN_BROWSER_RUNTIME_NAME=akvan-docker",
        image,
        "python3",
        "/app/agent/tools/browser_runtime/server.py",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--profiles-dir",
        CONTAINER_PROFILES_DIR,
        "--inactivity-timeout",
        str(timeout),
        "--runtime",
        "akvan-docker",
    ]
    _run(cmd)


CONTAINER_SERVER_MARKER = "/app/agent/tools/browser_runtime/server.py"


def _app_mount_healthy(container_name: str, container: dict[str, Any]) -> bool:
    """Return True when the container can see the mounted runtime server script.

    Path labels alone are not enough: replacing ~/.akvan/app with ``mv`` keeps the
    same host path string while Docker stays bound to the deleted inode.
    """

    if not _is_running(container):
        return False
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-w",
            "/",
            container_name,
            "test",
            "-f",
            CONTAINER_SERVER_MARKER,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


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
        and labels.get("akvan.runtime.api") == RUNTIME_API_VERSION
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
