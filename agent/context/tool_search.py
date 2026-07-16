"""Progressively disclose large non-core tool schema surfaces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from agent.context.budget import ContextBudget, estimate_tool_schema_tokens
from agent.context.config import ContextConfig
from agent.tools.base import Tool, ToolResult

CORE_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "patch",
        "terminal",
        "process",
        "skills_list",
        "skill_view",
        "skill_manage",
        "session_search",
        "memory",
        "knowledge",
    }
)
BRIDGE_NAMES = frozenset({"tool_search", "tool_describe", "tool_call"})


@dataclass(frozen=True)
class ToolDisclosure:
    visible: tuple[Tool, ...]
    deferred: Mapping[str, Tool]
    activated: bool
    deferred_tokens: int


def _search_catalog(deferred: Mapping[str, Tool], query: str = "") -> str:
    terms = [part.lower() for part in query.split() if part]
    rows = []
    for tool in deferred.values():
        haystack = f"{tool.name} {tool.description}".lower()
        if terms and not all(term in haystack for term in terms):
            continue
        rows.append({"name": tool.name, "description": tool.description})
    return json.dumps({"tools": rows[:20], "count": len(rows)}, ensure_ascii=False)


def build_disclosure(
    tools: tuple[Tool, ...],
    *,
    config: ContextConfig,
    budget: ContextBudget,
) -> ToolDisclosure:
    schemas = [tool.provider_schema() for tool in tools]
    schema_tokens = estimate_tool_schema_tokens(schemas)
    threshold = int(budget.context_length * config.tool_schema_threshold)
    should_defer = config.tool_search_enabled == "on" or (
        config.tool_search_enabled == "auto" and schema_tokens >= threshold
    )
    if config.tool_search_enabled == "off" or not should_defer:
        return ToolDisclosure(tools, {}, False, 0)

    visible = []
    deferred: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in CORE_TOOL_NAMES or tool.name in BRIDGE_NAMES:
            visible.append(tool)
        else:
            deferred[tool.name] = tool
    if not deferred:
        return ToolDisclosure(tools, {}, False, 0)

    def search(query: str = "") -> ToolResult:
        return ToolResult.trusted(_search_catalog(deferred, query))

    def describe(name: str) -> ToolResult:
        tool = deferred.get(name)
        if tool is None:
            return ToolResult(json.dumps({"error": f"Unknown deferred tool: {name}"}))
        return ToolResult.trusted(
            json.dumps(tool.provider_schema(), ensure_ascii=False)
        )

    def call(name: str, arguments: dict | None = None) -> ToolResult:
        tool = deferred.get(name)
        if tool is None:
            return ToolResult(json.dumps({"error": f"Unknown deferred tool: {name}"}))
        # Approval is enforced by AgentLoop before this bridge run is reached.
        return tool.invoke(arguments or {})

    def approval(arguments: Mapping[str, object]) -> object | None:
        name = arguments.get("name")
        payload = arguments.get("arguments")
        tool = deferred.get(name) if isinstance(name, str) else None
        if tool is None or tool.approval is None:
            return None
        return tool.approval(payload if isinstance(payload, dict) else {})

    visible.extend(
        (
            Tool(
                name="tool_search",
                description="Search deferred specialized tools by name or purpose.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
                run=search,
            ),
            Tool(
                name="tool_describe",
                description="Load the full schema for one deferred tool.",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
                run=describe,
            ),
            Tool(
                name="tool_call",
                description="Call one permitted deferred tool after discovering its schema.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                    "additionalProperties": False,
                },
                run=call,
                approval=approval,
            ),
        )
    )
    deferred_tokens = estimate_tool_schema_tokens(
        [tool.provider_schema() for tool in deferred.values()]
    )
    return ToolDisclosure(tuple(visible), deferred, True, deferred_tokens)
