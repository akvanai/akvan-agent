"""Public activity events emitted while Akvan Agent handles a turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class AgentState(str, Enum):
    """Safe, user-visible phases that never contain private reasoning."""

    THINKING = "thinking"
    RUNNING_TOOL = "running_tool"
    AWAITING_APPROVAL = "awaiting_approval"
    RESPONDING = "responding"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentEvent:
    """A state transition or public response chunk from the agent loop."""

    state: AgentState
    content: str | None = None
    tool_name: str | None = None
    tool_arguments: Mapping[str, object] | None = None
    request_id: str | None = None
    summary: str | None = None
    reason: str | None = None
    choices: tuple[str, ...] = ()
