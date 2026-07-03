"""Contracts shared by gateway services and concrete integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from agent.gateway.types import (
    CallbackInteraction, InboundMessage, InlineKeyboard, SendResult,
)

MessageHandler = Callable[[InboundMessage], Awaitable[None]]
CallbackHandler = Callable[[CallbackInteraction], Awaitable[None]]


@dataclass(frozen=True)
class GatewayCapabilities:
    """Optional delivery features exposed by an integration adapter."""

    buttons: bool = False
    callbacks: bool = False
    message_editing: bool = False
    typing: bool = False
    draft_streaming: bool = False
    max_message_length: int | None = None


class GatewayAdapter(Protocol):
    """Contract implemented by each messaging platform adapter."""

    @property
    def capabilities(self) -> GatewayCapabilities:
        """Return the delivery features supported by this adapter."""
        ...

    async def connect(self) -> bool:
        """Connect to the platform and start receiving messages."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the platform."""
        ...

    async def send_with_buttons(
        self, chat_id: str, text: str, buttons: InlineKeyboard,
    ) -> SendResult:
        """Send a message with contextual inline buttons."""
        ...

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        """Send a new message to a chat."""
        ...

    async def send_final(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        """Send completed content using the integration's best rendering."""
        ...

    async def send_draft(
        self, chat_id: str, draft_id: int, text: str,
    ) -> SendResult:
        """Update an ephemeral draft when draft streaming is supported."""
        ...

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit a previously sent message."""
        ...

    async def edit_with_buttons(
        self, chat_id: str, message_id: str, text: str,
        buttons: InlineKeyboard | None = None,
    ) -> SendResult:
        """Edit text and replace or remove its inline keyboard."""
        ...

    async def answer_callback(
        self, callback_id: str, text: str | None = None, *, alert: bool = False,
    ) -> None:
        """Acknowledge a platform callback interaction."""
        ...

    async def send_typing(self, chat_id: str) -> None:
        """Show a typing indicator in the chat."""
        ...

    def set_message_handler(self, handler: MessageHandler) -> None:
        """Register the callback for inbound messages."""
        ...

    def set_callback_handler(self, handler: CallbackHandler) -> None:
        """Register the callback for inline-button interactions."""
        ...
