"""
Defines the shared data shapes used throughout a conversation.
Message represents the role and text sent between users, agents, and providers.
Completion wraps an assistant reply together with an optional raw response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]
Message = dict[str, object]


@dataclass(frozen=True)
class Completion:
    message: Message
    raw: dict | None = None


@dataclass(frozen=True)
class TurnContext:
    """Provider-only content for the current user turn."""

    provider_user_content: str | None = None
