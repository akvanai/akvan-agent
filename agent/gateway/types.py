"""Shared gateway data shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InlineButton:
    text: str
    callback_data: str


InlineKeyboard = tuple[tuple[InlineButton, ...], ...]


@dataclass(frozen=True)
class CallbackInteraction:
    platform: str
    chat_id: str
    user_id: str
    message_id: str
    callback_id: str
    data: str


@dataclass(frozen=True)
class ChatSource:
    """Where an inbound gateway message originated."""

    platform: str
    chat_id: str
    user_id: str
    user_name: str | None = None
    chat_type: str = "dm"
    message_id: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound message from a platform adapter."""

    text: str
    source: ChatSource
    raw: Any = None
    image_paths: tuple[str, ...] = ()

    def is_command(self) -> bool:
        return self.text.startswith("/")

    def get_command(self) -> str | None:
        if not self.is_command():
            return None
        parts = self.text.split(maxsplit=1)
        raw = parts[0][1:].lower() if parts else None
        if raw and "@" in raw:
            raw = raw.split("@", 1)[0]
        return raw

    def get_command_args(self) -> str:
        if not self.is_command():
            return self.text
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


@dataclass
class SendResult:
    """Result of sending or editing a platform message."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    retry_after: float | None = None


@dataclass
class StreamConsumerConfig:
    """Runtime tuning for progressive message delivery."""

    edit_interval: float = 0.8
    max_message_length: int = 4096
    cursor: str = " ▍"
    transport: str = "auto"
    draft_update_interval: float | None = None


def utf16_len(value: str) -> int:
    """Count UTF-16 code units (Telegram message limit uses this)."""
    return len(value.encode("utf-16-le")) // 2


def truncate_utf16(value: str, limit: int) -> str:
    """Return the longest prefix whose UTF-16 length is at most *limit*."""
    if utf16_len(value) <= limit:
        return value
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(value[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo]
