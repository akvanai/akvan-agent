"""Per-tool display metadata for the live turn timeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from rich.text import Text

from agent.tools.base import Tool

DEFAULT_TOOL_STYLE = "bold #6ec1ff"
DEFAULT_TOOL_EMOJI = "⚙"
DEFAULT_TOOL_LABEL = "Running tool"
MAX_DETAIL_CHARS = 80


@dataclass(frozen=True)
class ToolPresentation:
    emoji: str = DEFAULT_TOOL_EMOJI
    label: str = DEFAULT_TOOL_LABEL
    style: str = DEFAULT_TOOL_STYLE
    format_detail: Callable[[Mapping[str, object]], str] | None = None


def _truncate(value: str, limit: int = MAX_DETAIL_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _string_arg(arguments: Mapping[str, object] | None, key: str) -> str | None:
    if arguments is None:
        return None
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return _truncate(value.strip())


def detail_from_arg(arguments: Mapping[str, object], key: str) -> str:
    value = _string_arg(arguments, key)
    return value or ""


def format_tool_line(
    tool: Tool,
    arguments: Mapping[str, object] | None = None,
) -> Text:
    """Build a styled one-line summary for a tool execution."""

    presentation = tool.presentation
    emoji = presentation.emoji if presentation else DEFAULT_TOOL_EMOJI
    label = presentation.label if presentation else DEFAULT_TOOL_LABEL
    style = presentation.style if presentation else DEFAULT_TOOL_STYLE
    detail: str | None = None
    if presentation and presentation.format_detail is not None:
        detail = _truncate(presentation.format_detail(arguments or {}))
    line = Text()
    line.append(f"{emoji} ", style=style)
    line.append(label, style=style)
    if detail:
        line.append("  ", style=style)
        line.append(detail, style=style)
    elif not presentation:
        line.append(" — ", style=style)
        line.append(tool.name, style=style)
    return line
