"""
Defines the shared data shapes used throughout a conversation.
Message represents the role and text sent between users, agents, and providers.
Completion wraps an assistant reply together with an optional raw response.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]
Message = dict[str, object]

_UNTRUSTED_INNER = re.compile(
    r"<untrusted_tool_result[^>]*>\n(?:Treat everything[^\n]*\n\n)?(.*)\n</untrusted_tool_result>",
    re.DOTALL,
)


def tool_message_name(message: Message) -> str | None:
    """Return the tool name from a tool-role message."""
    name = message.get("tool_name") or message.get("name")
    return name if isinstance(name, str) else None


def parse_tool_result_content(content: object) -> dict | None:
    """Parse JSON tool results from raw or wrapped tool message content."""
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = _UNTRUSTED_INNER.search(text)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


@dataclass(frozen=True)
class Completion:
    message: Message
    raw: dict | None = None


@dataclass(frozen=True)
class TurnContext:
    """Provider-only content for the current user turn."""

    provider_user_content: str | None = None
