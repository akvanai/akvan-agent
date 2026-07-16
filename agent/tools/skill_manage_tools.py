"""Agent-facing skill authoring tool."""

from __future__ import annotations

import json
from collections.abc import Callable

from agent.skills.manage import skill_manage
from agent.tools.base import Tool, ToolResult
from agent.tools.presentation import ToolPresentation, detail_from_arg


def build_skill_manage_tool(
    *,
    on_skills_changed: Callable[[], None] | None = None,
) -> Tool:
    def run(
        action: str,
        name: str = "",
        content: str = "",
        category: str = "general",
        old_string: str = "",
        new_string: str = "",
        file_path: str = "",
        file_content: str = "",
    ) -> ToolResult:
        result = skill_manage(
            action=action,
            name=name,
            content=content,
            category=category,
            old_string=old_string,
            new_string=new_string,
            file_path=file_path,
            file_content=file_content,
        )
        if result.get("success") and on_skills_changed is not None:
            try:
                on_skills_changed()
            except Exception:
                pass
        return ToolResult(json.dumps(result, ensure_ascii=False))

    return Tool(
        name="skill_manage",
        description=(
            "Manage skills (create, update, delete). Skills are procedural memory — "
            "reusable approaches for recurring task types. New skills are written to "
            "~/.akvan/skills/. Actions: create (full SKILL.md + category), patch "
            "(old_string/new_string — preferred for fixes), edit (full SKILL.md "
            "rewrite), delete, write_file, remove_file. Create when a complex task "
            "succeeded, errors were overcome, the user corrected your approach, or "
            "the user asks you to remember a procedure. Patch when instructions are "
            "stale or missing steps. Confirm with the user before delete. Bundled "
            "skills cannot be deleted. Pinned skills cannot be deleted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create",
                        "edit",
                        "patch",
                        "delete",
                        "write_file",
                        "remove_file",
                    ],
                },
                "name": {"type": "string", "description": "Skill name (lowercase-hyphenated)."},
                "content": {
                    "type": "string",
                    "description": "Full SKILL.md for create/edit.",
                },
                "category": {
                    "type": "string",
                    "description": "Category for create (default: general).",
                },
                "old_string": {"type": "string", "description": "Text to replace (patch)."},
                "new_string": {"type": "string", "description": "Replacement text (patch)."},
                "file_path": {
                    "type": "string",
                    "description": "Relative path for write_file/remove_file.",
                },
                "file_content": {
                    "type": "string",
                    "description": "File body for write_file.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        run=run,
        presentation=ToolPresentation(
            emoji="📝",
            label="Managing skill",
            style="bold #9ad0ff",
            format_detail=lambda args: detail_from_arg(args, "name"),
        ),
    )
