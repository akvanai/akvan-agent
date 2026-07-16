"""Project-root and prompt-instruction file discovery."""

from __future__ import annotations

from pathlib import Path

from agent.limits import MAX_SOURCE_CHARS, truncate_text


def find_project_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def discover_project_instructions(cwd: Path, project_root: Path) -> Path | None:
    locations: list[Path] = []
    current = cwd.resolve()
    root = project_root.resolve()
    while True:
        locations.append(current)
        if current == root:
            break
        if root not in current.parents:
            break
        current = current.parent

    for names in ((".akvan.md", "AKVAN.md"), ("AGENTS.md",)):
        for location in locations:
            for name in names:
                candidate = location / name
                if candidate.is_file():
                    return candidate
    return None


def read_bounded_text(path: Path, *, label: str) -> str | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return truncate_text(content, MAX_SOURCE_CHARS, label=label)
