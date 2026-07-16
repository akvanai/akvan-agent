"""Owner-only permissions for Akvan home and sensitive artifacts."""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600

_SENSITIVE_ROOT_FILES = (
    "state.db",
    "state.db-wal",
    "state.db-shm",
    "config.yaml",
    ".env",
    "approvals.json",
    "gateway-state.json",
)


def _akvan_home() -> Path:
    from agent.config import akvan_home

    return akvan_home()


def is_under_akvan_home(path: Path) -> bool:
    """Return True when path is inside the configured Akvan home directory."""

    try:
        path.resolve().relative_to(_akvan_home().resolve())
    except ValueError:
        return False
    return True


def ensure_private_dir(path: Path) -> Path:
    """Create or tighten a directory to owner-only access."""

    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    try:
        path.chmod(DIR_MODE)
    except OSError:
        pass
    return path


def ensure_private_file(path: Path) -> None:
    """Tighten a file to owner-only read/write when it exists."""

    if not path.exists():
        return
    try:
        path.chmod(FILE_MODE)
    except OSError:
        pass


def harden_session_db_files(db_path: Path) -> None:
    """Tighten the SQLite database and WAL sidecar files."""

    ensure_private_file(db_path)
    ensure_private_file(db_path.with_name(f"{db_path.name}-wal"))
    ensure_private_file(db_path.with_name(f"{db_path.name}-shm"))


def _harden_tree_files(directory: Path) -> None:
    if not directory.is_dir():
        return
    try:
        directory.chmod(DIR_MODE)
    except OSError:
        pass
    for child in directory.iterdir():
        if child.is_file():
            ensure_private_file(child)


def harden_akvan_home(home: Path | None = None) -> Path:
    """Create Akvan home safely and tighten known sensitive artifacts."""

    home = (home or _akvan_home()).resolve()
    ensure_private_dir(home)

    for name in _SENSITIVE_ROOT_FILES:
        ensure_private_file(home / name)

    for path in home.glob("gateway-*.pid"):
        ensure_private_file(path)

    _harden_tree_files(home / "logs")
    _harden_tree_files(home / "memories")

    return home


def prepare_akvan_parent(path: Path) -> Path:
    """Ensure a path's parent is ready for Akvan-private writes."""

    parent = path.parent
    if is_under_akvan_home(parent):
        harden_akvan_home(parent if parent.resolve() == _akvan_home().resolve() else _akvan_home())
        return parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def write_private_file(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    """Write a file and restrict it to owner-only access when under Akvan home."""

    prepare_akvan_parent(path)
    path.write_text(content, encoding=encoding)
    if is_under_akvan_home(path):
        ensure_private_file(path)
    return path


def replace_private_file(temp: Path, path: Path) -> None:
    """Atomically replace a file and restrict permissions when under Akvan home."""

    if is_under_akvan_home(path):
        ensure_private_file(temp)
    os.replace(temp, path)
    if is_under_akvan_home(path):
        ensure_private_file(path)
