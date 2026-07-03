"""Progressive-disclosure tools for local skill packages."""

from __future__ import annotations

from agent.skills.registry import SkillRegistry
from agent.tools.base import Tool, ToolResult
from agent.tools.presentation import ToolPresentation, detail_from_arg


def build_skill_tools(registry: SkillRegistry) -> tuple[Tool, Tool]:
    def skills_list() -> ToolResult:
        return ToolResult.trusted(registry.list_metadata())

    def skill_view(name: str, file_path: str | None = None) -> ToolResult:
        return ToolResult.trusted(registry.view(name, file_path))

    return (
        Tool(
            name="skills_list",
            description="List available skills with compact names, descriptions, and origins.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            run=skills_list,
            presentation=ToolPresentation(
                emoji="📚",
                label="Listing skills",
                style="bold #9ad0ff",
            ),
        ),
        Tool(
            name="skill_view",
            description=(
                "Load a skill's full instructions by exact name, or read one "
                "supporting text file by providing file_path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact skill name."},
                    "file_path": {
                        "type": "string",
                        "description": "Optional relative path inside the skill directory.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            run=skill_view,
            presentation=ToolPresentation(
                emoji="📄",
                label="Loading skill",
                style="bold #9ad0ff",
                format_detail=lambda args: detail_from_arg(args, "name"),
            ),
        ),
    )
