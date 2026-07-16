"""Uninstall Akvan Agent and optional managed Docker containers."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from agent.config import akvan_home
from agent.tools.browser_runtime.docker import DockerRuntimeError, remove_docker_runtime
from agent.tools.web.searxng_runtime.docker import SearXNGRuntimeError, remove_searxng_runtime

PROGRAM = "Akvan Agent"


def _validate_home(home: Path) -> None:
    resolved = home.expanduser().resolve()
    if resolved in {Path("/").resolve(), Path.home().resolve()}:
        raise ValueError(f"Refusing unsafe AKVAN_HOME: {home}")


def launcher_path() -> Path:
    configured = os.getenv("AKVAN_BIN_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "akvan"
    if os.geteuid() == 0 and Path("/usr/local/bin").is_dir() and os.access("/usr/local/bin", os.W_OK):
        return Path("/usr/local/bin/akvan")
    return Path.home() / ".local" / "bin" / "akvan"


def remove_launcher(*, home: Path | None = None) -> bool:
    root = home or akvan_home()
    launcher = launcher_path()
    venv_akvan = (root / "venv" / "bin" / "akvan").resolve()
    if launcher.is_symlink() and launcher.resolve() == venv_akvan:
        launcher.unlink()
        return True
    return False


def remove_managed_containers() -> None:
    for remover, error_type in (
        (remove_searxng_runtime, SearXNGRuntimeError),
        (remove_docker_runtime, DockerRuntimeError),
    ):
        try:
            remover()
        except error_type:
            continue


def _confirm(*, purge: bool, home: Path) -> bool:
    if purge:
        prompt = (
            f"Remove {PROGRAM} and delete all data in {home}? "
            "This cannot be undone. [y/N] "
        )
    else:
        prompt = (
            f"Remove {PROGRAM} from {home}? "
            "Skills, config, and other user data will be preserved. [y/N] "
        )
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def run_uninstall(*, purge: bool = False, yes: bool = False) -> int:
    home = akvan_home()
    try:
        _validate_home(home)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not yes:
        if not sys.stdin.isatty():
            print("Error: Refusing to uninstall without --yes in non-interactive mode.", file=sys.stderr)
            return 2
        if not _confirm(purge=purge, home=home):
            print("Uninstall cancelled.")
            return 1

    remove_managed_containers()
    remove_launcher(home=home)

    venv_dir = home / "venv"
    app_dir = home / "app"

    if purge:
        shutil.rmtree(home, ignore_errors=True)
        print(f"{PROGRAM} and all data in {home} were removed.")
        return 0

    for path in (venv_dir, app_dir):
        if path.exists():
            shutil.rmtree(path)
    print(f"{PROGRAM} was uninstalled. User data in {home} was preserved.")
    return 0
