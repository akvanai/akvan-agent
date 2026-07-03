"""Safe direct file tools with atomic writes and diff reporting."""

from __future__ import annotations

import difflib
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

from agent.tools.approval import classify_file_write
from agent.tools.base import Tool, ToolResult
from agent.tools.presentation import ToolPresentation, detail_from_arg

MAX_FILE_CHARS = 500_000


def _resolve_path(raw_path: str, project_root: Path) -> Path:
    if not raw_path.strip():
        raise ValueError("path must not be empty")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    candidate = candidate.absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"Refusing path through symlink: {current}")
    return candidate.resolve(strict=False)


def _read(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"File does not exist: {path}")
    if path.stat().st_size > MAX_FILE_CHARS * 4:
        raise ValueError(f"File is too large to read safely: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not UTF-8 text: {path}") from exc
    if len(content) > MAX_FILE_CHARS:
        raise ValueError(f"File exceeds {MAX_FILE_CHARS} characters: {path}")
    return content


def _atomic_write(path: Path, content: str) -> None:
    if len(content) > MAX_FILE_CHARS:
        raise ValueError(f"content exceeds {MAX_FILE_CHARS} characters")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _diff(path: Path, before: str, after: str) -> str:
    result = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )
    return result or "(no textual changes)"


def build_file_tools(project_root: Path) -> tuple[Tool, Tool, Tool]:
    root = project_root.resolve()

    def read_file(path: str) -> ToolResult:
        resolved = _resolve_path(path, root)
        return ToolResult(_read(resolved))

    def write_file(path: str, content: str) -> str:
        resolved = _resolve_path(path, root)
        before = _read(resolved) if resolved.exists() else ""
        _atomic_write(resolved, content)
        return f"Wrote {resolved}\n\n{_diff(resolved, before, content)}"

    def patch_file(
        path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> str:
        resolved = _resolve_path(path, root)
        before = _read(resolved)
        count = before.count(old_text)
        if count == 0:
            raise ValueError("old_text was not found in the target file")
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_text matched {count} times; set replace_all=true or provide more context"
            )
        after = before.replace(old_text, new_text, -1 if replace_all else 1)
        _atomic_write(resolved, after)
        return f"Patched {resolved}\n\n{_diff(resolved, before, after)}"

    def approval(arguments: Mapping[str, object]):
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str):
            return None
        return classify_file_write(
            _resolve_path(raw_path, root), project_root=root
        )

    path_property = {
        "type": "string",
        "description": "Absolute path or path relative to the project root.",
    }
    return (
        Tool(
            "read_file",
            "Read a UTF-8 text file.",
            {
                "type": "object",
                "properties": {"path": path_property},
                "required": ["path"],
                "additionalProperties": False,
            },
            read_file,
            presentation=ToolPresentation(
                emoji="📖",
                label="Reading file",
                format_detail=lambda args: detail_from_arg(args, "path"),
            ),
        ),
        Tool(
            "write_file",
            "Create or atomically replace a UTF-8 text file and return a diff.",
            {
                "type": "object",
                "properties": {
                    "path": path_property,
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            write_file,
            approval,
            presentation=ToolPresentation(
                emoji="✏",
                label="Writing file",
                style="bold #7ed68b",
                format_detail=lambda args: detail_from_arg(args, "path"),
            ),
        ),
        Tool(
            "patch",
            "Replace exact text in a UTF-8 file and return a unified diff.",
            {
                "type": "object",
                "properties": {
                    "path": path_property,
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            patch_file,
            approval,
            presentation=ToolPresentation(
                emoji="🩹",
                label="Patching file",
                style="bold #e8b86d",
                format_detail=lambda args: detail_from_arg(args, "path"),
            ),
        ),
    )
