"""Persistent curated memory tool."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.memory.store import MemoryStore
from agent.tools.base import Tool, ToolResult


def _missing_old_text_error(store: MemoryStore, target: str, action: str) -> str:
    entries = store._entries_for(target)
    current = store._char_count(target)
    limit = store._char_limit(target)
    return json.dumps(
        {
            "success": False,
            "error": (
                f"'{action}' needs old_text — a short unique substring of the entry "
                f"to {action}. Reissue with old_text set to part of one of the "
                f"current_entries below."
            ),
            "current_entries": entries,
            "usage": f"{current:,}/{limit:,}",
        },
        ensure_ascii=False,
    )


def memory_tool(
    *,
    action: str | None = None,
    target: str = "memory",
    content: str | None = None,
    old_text: str | None = None,
    operations: list[dict[str, Any]] | None = None,
    store: MemoryStore | None = None,
) -> str:
    if store is None:
        return json.dumps(
            {"success": False, "error": "Memory is not available in this session."},
            ensure_ascii=False,
        )
    if target not in {"memory", "user"}:
        return json.dumps(
            {"success": False, "error": f"Invalid target '{target}'. Use 'memory' or 'user'."},
            ensure_ascii=False,
        )

    if operations:
        if not isinstance(operations, list):
            return json.dumps(
                {"success": False, "error": "operations must be a list."},
                ensure_ascii=False,
            )
        result = store.apply_batch(target, operations)
        return json.dumps(result, ensure_ascii=False)

    if action == "add" and not content:
        return json.dumps(
            {"success": False, "error": "Content is required for 'add' action."},
            ensure_ascii=False,
        )
    if action == "replace":
        if not old_text:
            return _missing_old_text_error(store, target, "replace")
        if not content:
            return json.dumps(
                {"success": False, "error": "content is required for 'replace' action."},
                ensure_ascii=False,
            )
    if action == "remove" and not old_text:
        return _missing_old_text_error(store, target, "remove")

    if action == "add":
        result = store.add(target, content or "")
    elif action == "replace":
        result = store.replace(target, old_text or "", content or "")
    elif action == "remove":
        result = store.remove(target, old_text or "")
    else:
        return json.dumps(
            {"success": False, "error": f"Unknown action '{action}'. Use: add, replace, remove"},
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


MEMORY_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "replace", "remove"],
            "description": "Single-op action. Omit when using 'operations'.",
        },
        "target": {
            "type": "string",
            "enum": ["memory", "user"],
            "description": "'memory' for agent notes, 'user' for user profile.",
        },
        "content": {
            "type": "string",
            "description": "Entry content for add/replace.",
        },
        "old_text": {
            "type": "string",
            "description": "Substring identifying an entry for replace/remove.",
        },
        "operations": {
            "type": "array",
            "description": "Batch of {action, content?, old_text?} applied atomically.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                    "content": {"type": "string"},
                    "old_text": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    "required": ["target"],
}


def build_memory_tools(memory_store: MemoryStore | None) -> tuple[Tool, ...]:
    if memory_store is None:
        return ()

    def run(
        *,
        action: str | None = None,
        target: str = "memory",
        content: str | None = None,
        old_text: str | None = None,
        operations: list[dict[str, Any]] | None = None,
    ) -> str:
        return memory_tool(
            action=action,
            target=target,
            content=content,
            old_text=old_text,
            operations=operations,
            store=memory_store,
        )

    return (
        Tool(
            name="memory",
            description=(
                "Save durable facts to persistent memory across sessions. Use ONE batch "
                "via 'operations' when making multiple changes. TARGETS: 'user' = who the "
                "user is (preferences, style); 'memory' = your notes (environment, "
                "conventions, lessons). Save proactively when the user states preferences "
                "or you learn stable facts."
            ),
            parameters=MEMORY_PARAMETERS,
            run=run,
        ),
    )
