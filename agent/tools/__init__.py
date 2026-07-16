"""Public tool interfaces."""

from agent.tools.approval import (
    ApprovalChoice,
    ApprovalManager,
    ApprovalRequest,
)
from agent.tools.base import Tool, ToolResult, ToolResultKind


def __getattr__(name: str):
    """Load registry exports only when requested, avoiding skill/tool cycles."""

    if name in {
        "AVAILABLE_TOOLS",
        "BASE_TOOLS",
        "DEFAULT_TOOLSETS",
        "ToolRegistry",
        "build_available_tools",
    }:
        from agent.tools import registry

        return getattr(registry, name)
    raise AttributeError(name)

__all__ = [
    "ApprovalChoice",
    "ApprovalManager",
    "ApprovalRequest",
    "AVAILABLE_TOOLS",
    "BASE_TOOLS",
    "DEFAULT_TOOLSETS",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolResultKind",
    "build_available_tools",
]
