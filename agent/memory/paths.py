"""On-disk paths for Akvan persistent memory files."""

from __future__ import annotations

from pathlib import Path

from agent.config import akvan_home


def memory_dir() -> Path:
    return akvan_home() / "memories"


def memory_file() -> Path:
    return memory_dir() / "MEMORY.md"


def user_file() -> Path:
    return memory_dir() / "USER.md"
