"""Platform-neutral gateway conversation service."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from agent.agent import AgentLoopError
from agent.config import Settings
from agent.event_log import log_gateway
from agent.gateway.approval_flow import ApprovalFlowService
from agent.gateway.chat_session import ChatSessionService
from agent.gateway.command import CommandService
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayAdapter
from agent.gateway.delivery import DeliveryService
from agent.gateway.types import CallbackInteraction, InboundMessage
from agent.logging_setup import set_session_context
from agent.providers.base import Provider, ProviderError
from agent.storage.store import SessionStore

logger = logging.getLogger(__name__)


class GatewayService:
    """Routes inbound gateway messages to cached Akvan sessions."""

    def __init__(
        self,
        *,
        settings: Settings,
        gateway_id: str,
        gateway_name: str,
        runtime_config: GatewayRuntimeConfig,
        access_policy: Callable[[str], bool],
        provider: Provider,
        store: SessionStore,
        adapter: GatewayAdapter,
        yolo: bool = False,
        max_iterations: int = 30,
    ) -> None:
        self.settings = settings
        self.gateway_id = gateway_id
        self.gateway_name = gateway_name
        self.runtime_config = runtime_config
        self.access_policy = access_policy
        self.provider = provider
        self.store = store
        self.adapter = adapter
        self.yolo = yolo
        self.max_iterations = max_iterations
        self.delivery = DeliveryService(adapter, runtime_config)
        self.chat_session = ChatSessionService(
            settings=settings,
            gateway_id=gateway_id,
            provider=provider,
            store=store,
            runtime_config=runtime_config,
            delivery=self.delivery,
            yolo=yolo,
            max_iterations=max_iterations,
        )
        self.approval_flow = ApprovalFlowService(delivery=self.delivery)
        self.command = CommandService(
            gateway_name=gateway_name,
            gateway_id=gateway_id,
            settings=settings,
            provider=provider,
            store=store,
            runtime_config=runtime_config,
            chat_session=self.chat_session,
            delivery=self.delivery,
        )

    def _is_authorized(self, message: InboundMessage) -> bool:
        return (
            message.source.platform == self.gateway_id
            and self.access_policy(message.source.user_id)
        )

    def _callback_is_authorized(self, callback: CallbackInteraction) -> bool:
        return (
            callback.platform == self.gateway_id
            and self.access_policy(callback.user_id)
        )

    async def handle_message(self, message: InboundMessage) -> None:
        if not self._is_authorized(message):
            logger.debug(
                "Ignoring unauthorized %s user %s",
                self.gateway_id,
                message.source.user_id,
            )
            return

        chat_id = message.source.chat_id
        command = message.get_command() if message.is_command() else None

        if command == "stop":
            await self.chat_session.stop_turn(chat_id, self.approval_flow)
            return

        if await self.command.handle(message, command):
            return

        if self.approval_flow.is_pending(chat_id):
            await self.approval_flow.handle_message(chat_id, message.text)
            return

        async with self.chat_session.lock(chat_id):
            if await self.command.handle_locked(message, command):
                return

            session = self.chat_session.get_or_create(chat_id)
            set_session_context(session.persistence.session_id)
            log_gateway(
                f"turn started chat={chat_id} session={session.persistence.session_id[:8]}"
            )
            try:
                await self.chat_session.run_turn(
                    session,
                    message.text,
                    chat_id,
                    self.approval_flow,
                    image_paths=message.image_paths,
                )
            except (AgentLoopError, ProviderError) as exc:
                logger.exception("Gateway turn failed")
                log_gateway(f"turn failed chat={chat_id}: {exc}", level=logging.ERROR)
                await self.delivery.send(chat_id, f"Error: {exc}")
            else:
                log_gateway(f"turn completed chat={chat_id}")

    async def handle_callback(self, callback: CallbackInteraction) -> None:
        if not self._callback_is_authorized(callback):
            await self.delivery.answer_callback(
                callback.callback_id, "Not authorized.", alert=True,
            )
            return

        if await self.approval_flow.handle_callback(callback):
            return

        chat_id = callback.chat_id
        if self.chat_session.has_active_turn(chat_id):
            await self.delivery.answer_callback(
                callback.callback_id,
                "Finish or stop the current response first.",
                alert=True,
            )
            return

        await self.command.handle_settings_callback(callback)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.chat_session.set_loop(loop)
        self.approval_flow.set_loop(loop)
        self.adapter.set_message_handler(self.handle_message)
        if self.adapter.capabilities.callbacks:
            self.adapter.set_callback_handler(self.handle_callback)
        if not await self.adapter.connect():
            raise RuntimeError(f"Failed to connect {self.gateway_name} adapter.")

    async def stop(self) -> None:
        await self.adapter.disconnect()
        self.provider.close()
        self.store.close()
