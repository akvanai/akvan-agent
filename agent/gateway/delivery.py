"""Outbound message delivery for gateway chats."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent.events import AgentState
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayAdapter
from agent.gateway.stream_consumer import StreamConsumer
from agent.gateway.types import InlineKeyboard, SendResult, StreamConsumerConfig


class DeliveryService:
    """Thin async wrapper over a platform adapter."""

    def __init__(
        self,
        adapter: GatewayAdapter,
        runtime_config: GatewayRuntimeConfig,
    ) -> None:
        self.adapter = adapter
        self.runtime_config = runtime_config

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        if reply_to is None:
            return await self.adapter.send(chat_id, text)
        return await self.adapter.send(chat_id, text, reply_to=reply_to)

    async def send_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: InlineKeyboard,
    ) -> SendResult:
        return await self.adapter.send_with_buttons(chat_id, text, buttons)

    async def edit_with_buttons(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        buttons: InlineKeyboard | None = None,
    ) -> SendResult:
        return await self.adapter.edit_with_buttons(
            chat_id, message_id, text, buttons,
        )

    async def answer_callback(
        self,
        callback_id: str,
        text: str | None = None,
        *,
        alert: bool = False,
    ) -> None:
        await self.adapter.answer_callback(callback_id, text, alert=alert)

    async def send_typing(self, chat_id: str) -> None:
        await self.adapter.send_typing(chat_id)

    def create_stream_consumer(
        self,
        chat_id: str,
        transport: str,
    ) -> StreamConsumer:
        use_draft = (
            transport != "edit" and self.adapter.capabilities.draft_streaming
        )
        return StreamConsumer(
            self.adapter,
            chat_id,
            config=StreamConsumerConfig(
                edit_interval=self.runtime_config.stream_edit_interval,
                max_message_length=(
                    self.adapter.capabilities.max_message_length or 4096
                ),
                transport=transport,
                cursor="" if use_draft else " ▍",
            ),
        )

    async def typing_until(
        self,
        chat_id: str,
        stop: asyncio.Event,
        is_active: Callable[[], bool],
    ) -> None:
        while not stop.is_set():
            if is_active() and self.adapter.capabilities.typing:
                await self.send_typing(chat_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                continue


_ACTIVE_TYPING_STATES = {
    AgentState.THINKING,
    AgentState.RUNNING_TOOL,
    AgentState.RESPONDING,
}


def is_typing_state(state: AgentState) -> bool:
    return state in _ACTIVE_TYPING_STATES
