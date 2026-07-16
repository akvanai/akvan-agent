"""Slash commands and settings menus for gateway chats."""

from __future__ import annotations

import asyncio

from agent.config import Settings
from agent.gateway.bindings import cache_key
from agent.gateway.chat_session import ChatSessionService
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.delivery import DeliveryService
from agent.gateway.types import CallbackInteraction, InboundMessage, InlineButton, InlineKeyboard
from agent.providers.base import Provider
from agent.storage.store import SessionStore
from agent.ui.commands import knowledge_markdown


class CommandService:
    """Parses slash commands and manages settings UI callbacks."""

    def __init__(
        self,
        *,
        gateway_name: str,
        gateway_id: str,
        settings: Settings,
        provider: Provider,
        store: SessionStore,
        runtime_config: GatewayRuntimeConfig,
        chat_session: ChatSessionService,
        delivery: DeliveryService,
    ) -> None:
        self.gateway_name = gateway_name
        self.gateway_id = gateway_id
        self.settings = settings
        self.provider = provider
        self.store = store
        self.runtime_config = runtime_config
        self.chat_session = chat_session
        self.delivery = delivery
        self._model_cache: dict[str, list[str]] = {}

    def help_text(self, *, welcome: bool = False) -> str:
        heading = (
            "Welcome to Akvan Agent.\n\n"
            if welcome
            else f"Akvan {self.gateway_name} commands:\n\n"
        )
        return heading + (
            "/new — start a fresh conversation\n"
            "/status — show session and activity\n"
            "/usage — show estimated context usage\n"
            "/compress — compact the current conversation\n"
            "/knowledge — review global knowledge\n"
            "/settings — model, safety, and streaming\n"
            "/stop — stop the current response\n"
            "/help — show this help\n\n"
            "Sensitive operations ask for approval with inline buttons."
        )

    @staticmethod
    def settings_keyboard() -> InlineKeyboard:
        return (
            (InlineButton("Model", "settings:model:0"),),
            (InlineButton("Approval policy", "settings:approval"),),
            (InlineButton("Streaming mode", "settings:stream"),),
            (InlineButton("Close", "settings:close"),),
        )

    async def handle(
        self,
        message: InboundMessage,
        command: str | None,
    ) -> bool:
        """Handle a slash command outside the chat lock. Returns True if handled."""
        chat_id = message.source.chat_id
        if command == "stop":
            return False
        if command == "status":
            await self.send_status(chat_id)
            return True
        if command in {"start", "help"}:
            await self.delivery.send(
                chat_id, self.help_text(welcome=command == "start"),
            )
            return True
        if command == "settings":
            if self.chat_session.has_active_turn(chat_id):
                await self.delivery.send(
                    chat_id,
                    "Finish or /stop the current response before changing settings.",
                )
            else:
                await self.send_settings(chat_id)
            return True
        return False

    async def handle_locked(
        self,
        message: InboundMessage,
        command: str | None,
    ) -> bool:
        """Handle commands that need the per-chat lock. Returns True if handled."""
        chat_id = message.source.chat_id
        if command == "new":
            self.chat_session.reset(chat_id)
            await self.delivery.send(chat_id, "Started a new conversation.")
            return True
        if command == "knowledge":
            session = self.chat_session.get_or_create(chat_id)
            await self.delivery.send(
                chat_id,
                knowledge_markdown(session, message.get_command_args().strip()),
            )
            return True
        if command == "usage":
            session = self.chat_session.get_or_create(chat_id)
            await self.delivery.send(chat_id, session.context_usage_markdown())
            return True
        if command == "compress":
            session = self.chat_session.get_or_create(chat_id)
            result = session.compact_context(
                message.get_command_args().strip() or None
            )
            if result.changed:
                text = (
                    f"Context compacted: {result.before_tokens:,} → "
                    f"{result.after_tokens:,} estimated tokens."
                )
            else:
                text = "Context is already compact; no safe reduction was available."
            await self.delivery.send(chat_id, text)
            return True
        if command is not None:
            await self.delivery.send(chat_id, self.help_text())
            return True
        return False

    async def send_status(self, chat_id: str) -> None:
        session = self.chat_session.session_cache().get(
            cache_key(self.gateway_id, chat_id),
        )
        preferences = self.chat_session.preferences(chat_id)
        model = (
            session.model
            if session is not None
            else self.chat_session.usable_model(chat_id, preferences.get("model"))
        )
        approval = (
            session.tooling.approval_manager.mode
            if session is not None
            else preferences.get("approval_mode", self.settings.approval_mode)
        )
        transport = preferences.get(
            "stream_transport", self.runtime_config.stream_transport,
        )
        session_id = (
            session.persistence.session_id[:8]
            if session is not None
            else "not started"
        )
        cost = session.loop.session_cost_usd if session is not None else None
        cost_line = f"\nCost: ${cost:.6f}" if cost is not None else ""
        await self.delivery.send(
            chat_id,
            f"Status: {self.chat_session.activity_label(chat_id)}\n"
            f"Session: {session_id}\n"
            f"Provider: {self.provider.name}\nModel: {model}\n"
            f"Approvals: {approval.title()}\n"
            f"Streaming: {transport.title()}{cost_line}",
        )

    async def send_settings(self, chat_id: str) -> None:
        prefs = self.chat_session.preferences(chat_id)
        text = (
            f"{self.gateway_name} settings\n\n"
            f"Model: {self.chat_session.usable_model(chat_id, prefs.get('model'))}\n"
            f"Approvals: {prefs.get('approval_mode', self.settings.approval_mode).title()}\n"
            f"Streaming: {prefs.get('stream_transport', self.runtime_config.stream_transport).title()}"
        )
        await self.delivery.send_with_buttons(
            chat_id, text, self.settings_keyboard(),
        )

    async def handle_settings_callback(self, callback: CallbackInteraction) -> None:
        chat_id, data = callback.chat_id, callback.data
        if data == "settings:close":
            await self.delivery.edit_with_buttons(
                chat_id, callback.message_id, "Settings closed.",
            )
        elif data == "settings:root":
            prefs = self.chat_session.preferences(chat_id)
            text = (
                f"{self.gateway_name} settings\n\n"
                f"Model: {self.chat_session.usable_model(chat_id, prefs.get('model'))}\n"
                f"Approvals: {prefs.get('approval_mode', self.settings.approval_mode).title()}\n"
                f"Streaming: {prefs.get('stream_transport', self.runtime_config.stream_transport).title()}"
            )
            await self.delivery.edit_with_buttons(
                chat_id, callback.message_id, text, self.settings_keyboard(),
            )
        elif data == "settings:approval":
            buttons = (
                (
                    InlineButton("Ask", "settings:setapproval:ask"),
                    InlineButton("Deny", "settings:setapproval:deny"),
                ),
                (InlineButton("Back", "settings:root"),),
            )
            await self.delivery.edit_with_buttons(
                chat_id, callback.message_id, "Approval policy", buttons,
            )
        elif data.startswith("settings:setapproval:"):
            mode = data.rsplit(":", 1)[1]
            if mode not in {"ask", "deny"}:
                await self.delivery.answer_callback(
                    callback.callback_id, "Invalid approval policy.", alert=True,
                )
                return
            self.store.set_gateway_preferences(
                self.gateway_id, chat_id, approval_mode=mode,
            )
            session = self.chat_session.session_cache().get(
                cache_key(self.gateway_id, chat_id),
            )
            if session is not None:
                session.tooling.approval_manager.mode = mode
            await self.delivery.edit_with_buttons(
                chat_id,
                callback.message_id,
                f"Approval policy set to {mode.title()}.",
                ((InlineButton("Back", "settings:root"),),),
            )
        elif data == "settings:stream":
            buttons = (
                (
                    InlineButton("Auto", "settings:setstream:auto"),
                    InlineButton("Draft", "settings:setstream:draft"),
                    InlineButton("Edit", "settings:setstream:edit"),
                ),
                (InlineButton("Back", "settings:root"),),
            )
            await self.delivery.edit_with_buttons(
                chat_id, callback.message_id, "Streaming mode", buttons,
            )
        elif data.startswith("settings:setstream:"):
            mode = data.rsplit(":", 1)[1]
            if mode not in {"auto", "draft", "edit"}:
                await self.delivery.answer_callback(
                    callback.callback_id, "Invalid streaming mode.", alert=True,
                )
                return
            self.store.set_gateway_preferences(
                self.gateway_id, chat_id, stream_transport=mode,
            )
            await self.delivery.edit_with_buttons(
                chat_id,
                callback.message_id,
                f"Streaming mode set to {mode.title()}.",
                ((InlineButton("Back", "settings:root"),),),
            )
        elif data.startswith("settings:model:"):
            try:
                page = max(0, int(data.rsplit(":", 1)[1]))
            except ValueError:
                page = 0
            await self._show_models(callback, page)
            return
        elif data.startswith("settings:setmodel:"):
            try:
                index = int(data.rsplit(":", 1)[1])
                model = self._model_cache[chat_id][index]
            except (ValueError, IndexError, KeyError):
                await self.delivery.answer_callback(
                    callback.callback_id,
                    "This model list has expired.",
                    alert=True,
                )
                return
            self.store.set_gateway_preferences(
                self.gateway_id, chat_id, model=model,
            )
            session = self.chat_session.session_cache().get(
                cache_key(self.gateway_id, chat_id),
            )
            if session is not None:
                session.model = model
                session.loop.model = model
                session.reload()
                self.store.update_session_model(
                    session.persistence.session_id, model,
                )
            await self.delivery.edit_with_buttons(
                chat_id,
                callback.message_id,
                f"Model set to {model}.",
                ((InlineButton("Back", "settings:root"),),),
            )
        else:
            await self.delivery.answer_callback(
                callback.callback_id, "This menu has expired.", alert=True,
            )
            return
        await self.delivery.answer_callback(callback.callback_id)

    async def _show_models(self, callback: CallbackInteraction, page: int) -> None:
        chat_id = callback.chat_id
        try:
            infos = await asyncio.to_thread(self.chat_session.available_models)
        except Exception as exc:
            await self.delivery.answer_callback(
                callback.callback_id, f"Could not load models: {exc}", alert=True,
            )
            return
        models = [info.id for info in infos]
        if not models:
            await self.delivery.answer_callback(
                callback.callback_id, "No models are available.", alert=True,
            )
            return
        current = self.chat_session.usable_model(
            chat_id, self.chat_session.preferences(chat_id).get("model"),
        )
        self._model_cache[chat_id] = models
        total_pages = max(1, (len(models) + 7) // 8)
        page = min(page, total_pages - 1)
        start = page * 8
        rows = [
            (
                InlineButton(
                    ("✓ " if model == current else "") + model,
                    f"settings:setmodel:{index}",
                ),
            )
            for index, model in enumerate(models[start:start + 8], start=start)
        ]
        nav = []
        if page > 0:
            nav.append(InlineButton("‹ Previous", f"settings:model:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineButton("Next ›", f"settings:model:{page + 1}"))
        if nav:
            rows.append(tuple(nav))
        rows.append((InlineButton("Back", "settings:root"),))
        await self.delivery.edit_with_buttons(
            chat_id,
            callback.message_id,
            f"Choose model — page {page + 1}/{total_pages}",
            tuple(rows),
        )
        await self.delivery.answer_callback(callback.callback_id)
