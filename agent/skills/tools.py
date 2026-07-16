"""Progressive-disclosure tools for local skill packages."""

from __future__ import annotations

import hashlib

from agent.skills.registry import SkillRegistry
from agent.skills.usage import bump_use
from agent.tools.base import Tool, ToolResult
from agent.tools.presentation import ToolPresentation, detail_from_arg


def build_skill_tools(registry: SkillRegistry) -> tuple[Tool, Tool]:
    loaded_content: dict[str, str] = {}

    def skills_list() -> ToolResult:
        return ToolResult.trusted(registry.list_metadata())

    def skill_view(name: str, file_path: str | None = None) -> ToolResult:
        result = registry.view(name, file_path)
        digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
        previous = loaded_content.get(digest)
        label = f"{name}/{file_path}" if file_path else name
        if previous is not None:
            return ToolResult.trusted(
                f"Skill content {label!r} is identical to {previous!r}, which "
                "was already loaded in this session. Reuse the earlier content."
            )
        loaded_content[digest] = label
        if file_path is None:
            bump_use(name)
        return ToolResult.trusted(result)

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
