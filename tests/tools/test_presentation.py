"""Tests for tool presentation metadata."""

from __future__ import annotations

from agent.tools.base import Tool
from agent.tools.presentation import ToolPresentation, format_tool_line


def test_format_tool_line_uses_defaults_without_presentation() -> None:
    tool = Tool(
        name="testy",
        description="Test tool.",
        parameters={"type": "object", "properties": {}},
        run=lambda: "ok",
    )

    line = format_tool_line(tool)

    assert "⚙" in line.plain
    assert "Running tool" in line.plain
    assert "testy" in line.plain


def test_format_tool_line_uses_presentation_and_detail() -> None:
    tool = Tool(
        name="read_file",
        description="Read a file.",
        parameters={"type": "object", "properties": {}},
        run=lambda path: path,
        presentation=ToolPresentation(
            emoji="📖",
            label="Reading file",
            format_detail=lambda args: str(args.get("path", "")),
        ),
    )

    line = format_tool_line(tool, {"path": "config.py"})

    assert "📖" in line.plain
    assert "Reading file" in line.plain
    assert "config.py" in line.plain
