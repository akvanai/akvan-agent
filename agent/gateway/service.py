"""Platform-neutral gateway conversation service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from agent.agent import AgentLoopError
from agent.config import Settings
from agent.events import AgentState
from agent.gateway.bindings import cache_key, get_or_create_session, reset_session
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayAdapter
from agent.gateway.stream_consumer import StreamConsumer
from agent.gateway.types import (
    CallbackInteraction, InboundMessage, InlineButton, InlineKeyboard,
    StreamConsumerConfig,
)
from agent.providers.base import Provider, ProviderError
from agent.session import AgentSession
from agent.storage.store import SessionStore
from agent.tools.approval import ApprovalChoice, ApprovalRequest

logger = logging.getLogger(__name__)


@dataclass
class _ApprovalPending:
    event: threading.Event = field(default_factory=threading.Event)
    result: ApprovalChoice | None = None
    request: ApprovalRequest | None = None
    message_id: str | None = None


@dataclass
class _TurnControl:
    cancel: threading.Event = field(default_factory=threading.Event)
    state: AgentState = AgentState.THINKING


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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_cache: dict[str, AgentSession] = {}
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._approval_pending: dict[str, _ApprovalPending] = {}
        self._active_turns: dict[str, _TurnControl] = {}
        self._model_cache: dict[str, list[str]] = {}

    def _session_factory(
        self, session_id: str | None = None, *, model: str | None = None,
        approval_mode: str | None = None,
    ) -> AgentSession:
        return AgentSession.create(
            provider=self.provider,
            model=model or self.settings.model,
            max_iterations=self.max_iterations,
            approval_mode=approval_mode or self.settings.approval_mode,
            approval_timeout=self.settings.approval_timeout,
            terminal_timeout=self.settings.terminal_timeout,
            yolo=self.yolo,
            store=self.store,
            session_id=session_id,
            session_source=self.gateway_id,
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

    def _preferences(self, chat_id: str) -> dict[str, str]:
        return self.store.get_gateway_preferences(self.gateway_id, chat_id)

    def _factory_for(self, chat_id: str):
        preferences = self._preferences(chat_id)

        def factory(session_id: str | None = None) -> AgentSession:
            return self._session_factory(
                session_id, model=preferences.get("model"),
                approval_mode=preferences.get("approval_mode"),
            )

        return factory

    def _chat_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

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
            await self._stop_turn(chat_id)
            return
        if command == "status":
            await self._send_status(chat_id)
            return
        if command in {"start", "help"}:
            await self.adapter.send(chat_id, self._help_text(welcome=command == "start"))
            return
        if command == "settings":
            if chat_id in self._active_turns:
                await self.adapter.send(chat_id, "Finish or /stop the current response before changing settings.")
            else:
                await self._send_settings(chat_id)
            return

        if chat_id in self._approval_pending:
            choice = self._parse_approval_choice(message.text)
            pending = self._approval_pending[chat_id]
            if choice is None or pending.request is None or choice not in pending.request.choices:
                await self.adapter.send(chat_id, "Waiting for approval — use the buttons in the approval message.")
                return
            pending.result = choice
            pending.event.set()
            return

        async with self._chat_lock(chat_id):
            if command == "new":
                reset_session(
                    platform=self.gateway_id, chat_id=chat_id, store=self.store,
                    session_cache=self._session_cache, factory=self._factory_for(chat_id),
                )
                await self.adapter.send(chat_id, "Started a new conversation.")
                return
            if command is not None:
                await self.adapter.send(chat_id, self._help_text())
                return

            session = get_or_create_session(
                platform=self.gateway_id, chat_id=chat_id, store=self.store,
                session_cache=self._session_cache, factory=self._factory_for(chat_id),
            )
            try:
                await self._run_turn(session, message.text, chat_id)
            except (AgentLoopError, ProviderError) as exc:
                logger.exception("Gateway turn failed")
                await self.adapter.send(chat_id, f"Error: {exc}")


    @staticmethod
    def _parse_approval_choice(text: str) -> ApprovalChoice | None:
        stripped = text.strip()
        mapping = {
            "1": ApprovalChoice.ONCE,
            "2": ApprovalChoice.SESSION,
            "3": ApprovalChoice.ALWAYS,
            "4": ApprovalChoice.DENY,
        }
        if stripped in mapping:
            return mapping[stripped]
        lowered = stripped.lower()
        if lowered in {"once", "allow", "yes", "y", "approve"}:
            return ApprovalChoice.ONCE
        if lowered in {"session"}:
            return ApprovalChoice.SESSION
        if lowered in {"always", "permanent"}:
            return ApprovalChoice.ALWAYS
        if lowered in {"deny", "no", "n"}:
            return ApprovalChoice.DENY
        return None


    def _help_text(self, *, welcome: bool = False) -> str:
        heading = (
            "Welcome to Akvan Agent.\n\n"
            if welcome else f"Akvan {self.gateway_name} commands:\n\n"
        )
        return heading + (
            "/new — start a fresh conversation\n"
            "/status — show session and activity\n"
            "/settings — model, safety, and streaming\n"
            "/stop — stop the current response\n"
            "/help — show this help\n\n"
            "Sensitive operations ask for approval with inline buttons."
        )

    def _activity_label(self, chat_id: str) -> str:
        control = self._active_turns.get(chat_id)
        if control is None:
            return "Idle"
        return {
            AgentState.THINKING: "Thinking",
            AgentState.RUNNING_TOOL: "Running a tool",
            AgentState.AWAITING_APPROVAL: "Awaiting approval",
            AgentState.RESPONDING: "Streaming response",
            AgentState.COMPLETED: "Completed",
            AgentState.FAILED: "Failed",
        }.get(control.state, "Working")

    async def _send_status(self, chat_id: str) -> None:
        session = self._session_cache.get(cache_key(self.gateway_id, chat_id))
        preferences = self._preferences(chat_id)
        model = session.model if session is not None else preferences.get("model", self.settings.model)
        approval = (session.approval_manager.mode if session is not None
                    else preferences.get("approval_mode", self.settings.approval_mode))
        transport = preferences.get(
            "stream_transport", self.runtime_config.stream_transport
        )
        session_id = session.session_id[:8] if session is not None else "not started"
        cost = session.loop.session_cost_usd if session is not None else None
        cost_line = f"\nCost: ${cost:.6f}" if cost is not None else ""
        await self.adapter.send(
            chat_id,
            f"Status: {self._activity_label(chat_id)}\nSession: {session_id}\n"
            f"Provider: {self.provider.name}\nModel: {model}\n"
            f"Approvals: {approval.title()}\nStreaming: {transport.title()}{cost_line}",
        )

    async def _stop_turn(self, chat_id: str) -> None:
        control = self._active_turns.get(chat_id)
        if control is None or control.state in {AgentState.COMPLETED, AgentState.FAILED}:
            await self.adapter.send(chat_id, "Nothing is currently running.")
            return
        control.cancel.set()
        pending = self._approval_pending.get(chat_id)
        if pending is not None:
            pending.result = ApprovalChoice.DENY
            pending.event.set()
            if (
                pending.message_id is not None
                and self.adapter.capabilities.message_editing
            ):
                await self.adapter.edit_with_buttons(
                    chat_id, pending.message_id, "❌ Denied"
                )
        await self.adapter.send(chat_id, "Stopping current response…")


    @staticmethod
    def _settings_keyboard() -> InlineKeyboard:
        return (
            (InlineButton("Model", "settings:model:0"),),
            (InlineButton("Approval policy", "settings:approval"),),
            (InlineButton("Streaming mode", "settings:stream"),),
            (InlineButton("Close", "settings:close"),),
        )

    async def _send_settings(self, chat_id: str) -> None:
        prefs = self._preferences(chat_id)
        text = (
            f"{self.gateway_name} settings\n\n"
            f"Model: {prefs.get('model', self.settings.model)}\n"
            f"Approvals: {prefs.get('approval_mode', self.settings.approval_mode).title()}\n"
            f"Streaming: {prefs.get('stream_transport', self.runtime_config.stream_transport).title()}"
        )
        await self.adapter.send_with_buttons(chat_id, text, self._settings_keyboard())

    async def handle_callback(self, callback: CallbackInteraction) -> None:
        if not self._callback_is_authorized(callback):
            await self.adapter.answer_callback(callback.callback_id, "Not authorized.", alert=True)
            return
        chat_id = callback.chat_id
        data = callback.data
        if data.startswith("approval:"):
            parts = data.split(":", 2)
            pending = self._approval_pending.get(chat_id)
            if len(parts) != 3 or pending is None or pending.request is None:
                await self.adapter.answer_callback(callback.callback_id, "This approval has expired.", alert=True)
                return
            request_id, raw_choice = parts[1], parts[2]
            try:
                choice = ApprovalChoice(raw_choice)
            except ValueError:
                choice = None
            if (request_id != pending.request.request_id or choice not in pending.request.choices
                    or callback.message_id != pending.message_id):
                await self.adapter.answer_callback(callback.callback_id, "This approval has expired.", alert=True)
                return
            pending.result = choice
            pending.event.set()
            labels = {
                ApprovalChoice.ONCE: "✅ Approved once",
                ApprovalChoice.SESSION: "✅ Approved for this session",
                ApprovalChoice.ALWAYS: "✅ Always approved",
                ApprovalChoice.DENY: "❌ Denied",
            }
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, labels[choice])
            await self.adapter.answer_callback(callback.callback_id)
            return

        if chat_id in self._active_turns:
            await self.adapter.answer_callback(callback.callback_id, "Finish or stop the current response first.", alert=True)
            return
        await self._handle_settings_callback(callback)

    async def _handle_settings_callback(self, callback: CallbackInteraction) -> None:
        chat_id, data = callback.chat_id, callback.data
        if data == "settings:close":
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, "Settings closed.")
        elif data == "settings:root":
            prefs = self._preferences(chat_id)
            text = (f"{self.gateway_name} settings\n\nModel: {prefs.get('model', self.settings.model)}\n"
                    f"Approvals: {prefs.get('approval_mode', self.settings.approval_mode).title()}\n"
                    f"Streaming: {prefs.get('stream_transport', self.runtime_config.stream_transport).title()}")
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, text, self._settings_keyboard())
        elif data == "settings:approval":
            buttons = ((InlineButton("Ask", "settings:setapproval:ask"), InlineButton("Deny", "settings:setapproval:deny")),
                       (InlineButton("Back", "settings:root"),))
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, "Approval policy", buttons)
        elif data.startswith("settings:setapproval:"):
            mode = data.rsplit(":", 1)[1]
            if mode not in {"ask", "deny"}:
                await self.adapter.answer_callback(callback.callback_id, "Invalid approval policy.", alert=True)
                return
            self.store.set_gateway_preferences(self.gateway_id, chat_id, approval_mode=mode)
            session = self._session_cache.get(cache_key(self.gateway_id, chat_id))
            if session is not None:
                session.approval_manager.mode = mode
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, f"Approval policy set to {mode.title()}.", ((InlineButton("Back", "settings:root"),),))
        elif data == "settings:stream":
            buttons = ((InlineButton("Auto", "settings:setstream:auto"), InlineButton("Draft", "settings:setstream:draft"), InlineButton("Edit", "settings:setstream:edit")),
                       (InlineButton("Back", "settings:root"),))
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, "Streaming mode", buttons)
        elif data.startswith("settings:setstream:"):
            mode = data.rsplit(":", 1)[1]
            if mode not in {"auto", "draft", "edit"}:
                await self.adapter.answer_callback(callback.callback_id, "Invalid streaming mode.", alert=True)
                return
            self.store.set_gateway_preferences(self.gateway_id, chat_id, stream_transport=mode)
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, f"Streaming mode set to {mode.title()}.", ((InlineButton("Back", "settings:root"),),))
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
                await self.adapter.answer_callback(callback.callback_id, "This model list has expired.", alert=True)
                return
            self.store.set_gateway_preferences(self.gateway_id, chat_id, model=model)
            session = self._session_cache.get(cache_key(self.gateway_id, chat_id))
            if session is not None:
                session.model = model
                session.loop.model = model
                session.reload()
                self.store.update_session_model(session.session_id, model)
            await self.adapter.edit_with_buttons(chat_id, callback.message_id, f"Model set to {model}.", ((InlineButton("Back", "settings:root"),),))
        else:
            await self.adapter.answer_callback(callback.callback_id, "This menu has expired.", alert=True)
            return
        await self.adapter.answer_callback(callback.callback_id)

    async def _show_models(self, callback: CallbackInteraction, page: int) -> None:
        chat_id = callback.chat_id
        try:
            infos = await asyncio.to_thread(self.provider.list_models)
        except Exception as exc:
            await self.adapter.answer_callback(callback.callback_id, f"Could not load models: {exc}", alert=True)
            return
        models = [info.id for info in infos]
        current = self._preferences(chat_id).get("model", self.settings.model)
        if current not in models:
            models.insert(0, current)
        self._model_cache[chat_id] = models
        total_pages = max(1, (len(models) + 7) // 8)
        page = min(page, total_pages - 1)
        start = page * 8
        rows = [(InlineButton(("✓ " if model == current else "") + model, f"settings:setmodel:{index}"),)
                for index, model in enumerate(models[start:start + 8], start=start)]
        nav = []
        if page > 0:
            nav.append(InlineButton("‹ Previous", f"settings:model:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineButton("Next ›", f"settings:model:{page + 1}"))
        if nav:
            rows.append(tuple(nav))
        rows.append((InlineButton("Back", "settings:root"),))
        await self.adapter.edit_with_buttons(chat_id, callback.message_id, f"Choose model — page {page + 1}/{total_pages}", tuple(rows))
        await self.adapter.answer_callback(callback.callback_id)

    def _approval_callback(self, chat_id: str) -> Callable[[ApprovalRequest, int], ApprovalChoice | str]:
        def callback(request: ApprovalRequest, timeout: int) -> ApprovalChoice | str:
            loop = self._loop
            if loop is None:
                raise TimeoutError("gateway event loop unavailable")

            lines = [
                "Approval required:",
                request.summary,
                f"Reason: {request.reason}",
                "",
            ]
            labels = {
                ApprovalChoice.ONCE: "Allow once",
                ApprovalChoice.SESSION: "Allow for session",
                ApprovalChoice.ALWAYS: "Always allow",
                ApprovalChoice.DENY: "Deny",
            }
            prompt = "\n".join(lines)
            buttons = tuple(
                (InlineButton(labels.get(choice, choice.value),
                              f"approval:{request.request_id}:{choice.value}"),)
                for choice in request.choices
            )

            pending = _ApprovalPending(request=request)
            self._approval_pending[chat_id] = pending
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.adapter.send_with_buttons(chat_id, prompt, buttons),
                    loop,
                )
                result = future.result(timeout=30)
                pending.message_id = result.message_id
                if not result.success:
                    raise TimeoutError("approval prompt failed")
                if not pending.event.wait(timeout):
                    if pending.message_id is not None:
                        expired = asyncio.run_coroutine_threadsafe(
                            self.adapter.edit_with_buttons(
                                chat_id, pending.message_id, "⌛ Timed out — denied"
                            ), loop,
                        )
                        with contextlib.suppress(Exception):
                            expired.result(timeout=10)
                    raise TimeoutError("approval timed out")
                if pending.result is None:
                    raise TimeoutError("approval timed out")
                return pending.result
            finally:
                self._approval_pending.pop(chat_id, None)

        return callback

    async def _run_turn(
        self, session: AgentSession, user_input: str, chat_id: str,
    ) -> None:
        preferences = self._preferences(chat_id)
        transport = preferences.get(
            "stream_transport", self.runtime_config.stream_transport
        )
        use_draft = (
            transport != "edit" and self.adapter.capabilities.draft_streaming
        )
        consumer = StreamConsumer(
            self.adapter, chat_id,
            config=StreamConsumerConfig(
                edit_interval=self.runtime_config.stream_edit_interval,
                max_message_length=(
                    self.adapter.capabilities.max_message_length or 4096
                ),
                transport=transport, cursor="" if use_draft else " ▍",
            ),
        )
        consumer_task = asyncio.create_task(consumer.run())
        session.loop.approval_manager.set_callback(self._approval_callback(chat_id))
        control = _TurnControl()
        self._active_turns[chat_id] = control
        original_message_count = len(session.messages)
        stop_typing = asyncio.Event()

        async def typing_loop(stop: asyncio.Event) -> None:
            active_states = {AgentState.THINKING, AgentState.RUNNING_TOOL, AgentState.RESPONDING}
            while not stop.is_set():
                if (
                    control.state in active_states
                    and self.adapter.capabilities.typing
                ):
                    await self.adapter.send_typing(chat_id)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    continue

        typing_task = asyncio.create_task(typing_loop(stop_typing))

        def run_sync() -> None:
            try:
                events = session.loop.stream_events(session.messages, user_input)
                for event in events:
                    control.state = event.state
                    if control.cancel.is_set():
                        events.close()
                        break
                    if event.state == AgentState.RESPONDING and event.content:
                        consumer.on_delta(event.content)
            finally:
                if control.cancel.is_set():
                    consumer.on_delta("\n\n⏹ Stopped")
                consumer.finish()

        try:
            await asyncio.to_thread(run_sync)
            await consumer_task
            if control.cancel.is_set():
                del session.messages[original_message_count:]
            else:
                session.persist_new_messages()
                self.store.set_gateway_binding(
                    self.gateway_id, chat_id, session.session_id
                )
        finally:
            stop_typing.set()
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
            session.loop.approval_manager.set_callback(None)
            self._active_turns.pop(chat_id, None)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.adapter.set_message_handler(self.handle_message)
        if self.adapter.capabilities.callbacks:
            self.adapter.set_callback_handler(self.handle_callback)
        if not await self.adapter.connect():
            raise RuntimeError(f"Failed to connect {self.gateway_name} adapter.")

    async def stop(self) -> None:
        await self.adapter.disconnect()
        self.provider.close()
        self.store.close()
