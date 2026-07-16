"""Background gateway process management."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from agent.config import akvan_home
from agent.gateway.registry import GATEWAY_INTEGRATIONS
from agent.logging_setup import logs_dir
from agent.storage.permissions import (
    ensure_private_dir,
    ensure_private_file,
    harden_akvan_home,
    is_under_akvan_home,
)


def gateway_pid_path(gateway_id: str, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / f"gateway-{gateway_id}.pid"


def gateway_log_path(gateway_id: str, project_root: Path | None = None) -> Path:
    return logs_dir(project_root=project_root or akvan_home()) / f"gateway-{gateway_id}.log"


def read_gateway_pid(gateway_id: str, *, project_root: Path | None = None) -> int | None:
    path = gateway_pid_path(gateway_id, project_root)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def clear_gateway_pid(gateway_id: str, *, project_root: Path | None = None) -> None:
    gateway_pid_path(gateway_id, project_root).unlink(missing_ok=True)


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if sys.platform == "linux":
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
            if state == "Z":
                return False
        except (OSError, IndexError):
            pass
    return True


def is_gateway_running(gateway_id: str, *, project_root: Path | None = None) -> bool:
    pid = read_gateway_pid(gateway_id, project_root=project_root)
    if pid is None:
        return False
    if is_process_alive(pid):
        return True
    clear_gateway_pid(gateway_id, project_root=project_root)
    return False


def write_gateway_pid(
    gateway_id: str,
    pid: int,
    *,
    project_root: Path | None = None,
) -> Path:
    root = project_root or akvan_home()
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    path = gateway_pid_path(gateway_id, root)
    path.write_text(f"{pid}\n", encoding="utf-8")
    ensure_private_file(path)
    return path


def _daemon_command(
    gateway_id: str, *, yolo: bool, max_iterations: int,
) -> list[str]:
    command = [
        sys.executable, "-m", "agent.gateway.runner",
        "--gateway-id", gateway_id,
    ]
    if yolo:
        command.append("--yolo")
    command.extend(["--max-iterations", str(max_iterations)])
    return command


def start_gateway_daemon(
    gateway_id: str,
    *,
    yolo: bool = False,
    max_iterations: int = 30,
    project_root: Path | None = None,
) -> tuple[bool, str]:
    if is_gateway_running(gateway_id, project_root=project_root):
        pid = read_gateway_pid(gateway_id, project_root=project_root)
        return False, f"Gateway is already running (pid {pid})."

    root = project_root or akvan_home()
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    log_path = gateway_log_path(gateway_id, root)
    if is_under_akvan_home(log_path):
        ensure_private_dir(log_path.parent)
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        _daemon_command(
            gateway_id, yolo=yolo, max_iterations=max_iterations
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "AKVAN_GATEWAY_ID": gateway_id},
    )
    write_gateway_pid(gateway_id, process.pid, project_root=root)
    return True, f"Gateway started in the background (pid {process.pid}). Logs: {log_path}"


def stop_gateway_daemon(
    gateway_id: str,
    *,
    project_root: Path | None = None,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    pid = read_gateway_pid(gateway_id, project_root=project_root)
    if pid is None or not is_process_alive(pid):
        clear_gateway_pid(gateway_id, project_root=project_root)
        return False, "Gateway is not running."

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        clear_gateway_pid(gateway_id, project_root=project_root)
        return False, f"Could not stop gateway: {exc}"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            clear_gateway_pid(gateway_id, project_root=project_root)
            return True, "Gateway stopped."
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    clear_gateway_pid(gateway_id, project_root=project_root)
    if is_process_alive(pid):
        return False, f"Gateway did not stop (pid {pid})."
    return True, "Gateway stopped."


def restart_gateway_daemon(
    gateway_id: str,
    *,
    yolo: bool = False,
    max_iterations: int = 30,
    project_root: Path | None = None,
) -> tuple[bool, str]:
    if not is_gateway_running(gateway_id, project_root=project_root):
        return False, "Gateway is not running."

    stopped, stop_message = stop_gateway_daemon(
        gateway_id, project_root=project_root,
    )
    if not stopped:
        return False, stop_message

    started, start_message = start_gateway_daemon(
        gateway_id,
        yolo=yolo,
        max_iterations=max_iterations,
        project_root=project_root,
    )
    if not started:
        return False, f"Gateway stopped but could not restart: {start_message}"
    return True, f"Gateway restarted. {start_message}"


def restart_running_gateways(
    *,
    yolo: bool = False,
    max_iterations: int = 30,
    project_root: Path | None = None,
) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for integration in GATEWAY_INTEGRATIONS:
        gateway_id = integration.definition.id
        if not is_gateway_running(gateway_id, project_root=project_root):
            continue
        ok, message = restart_gateway_daemon(
            gateway_id,
            yolo=yolo,
            max_iterations=max_iterations,
            project_root=project_root,
        )
        results.append((gateway_id, ok, message))
    return results
