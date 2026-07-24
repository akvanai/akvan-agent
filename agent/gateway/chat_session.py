"""Gateway chat-to-session binding and turn lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent.config import Settings
from agent.events import AgentState
from agent.gateway.bindings import cache_key, get_or_create_session, reset_session
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.delivery import DeliveryService, is_typing_state
from agent.session import AgentSession
from agent.storage.store import SessionStore
from agent.providers.base import ModelInfo, Provider

if TYPE_CHECKING:
    from agent.gateway.approval_flow import ApprovalFlowService

logger = logging.getLogger(__name__)


@dataclass
class TurnControl:
    cancel: threading.Event = field(default_factory=threading.Event)
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    state: AgentState = AgentState.THINKING


class ChatSessionService:
    """Owns cached sessions, per-chat locks, and agent turn orchestration."""

    def __init__(
        self,
        *,
        settings: Settings,
        gateway_id: str,
        provider: Provider,
        store: SessionStore,
        runtime_config: GatewayRuntimeConfig,
        delivery: DeliveryService,
        yolo: bool = False,
        max_iterations: int = 30,
    ) -> None:
        self.settings = settings
        self.gateway_id = gateway_id
        self.provider = provider
        self.store = store
        self.runtime_config = runtime_config
        self.delivery = delivery
        self.yolo = yolo
        self.max_iterations = max_iterations
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_cache: dict[str, AgentSession] = {}
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._active_turns: dict[str, TurnControl] = {}
        self._model_cache: list[ModelInfo] | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def preferences(self, chat_id: str) -> dict[str, str]:
        return self.store.get_gateway_preferences(self.gateway_id, chat_id)

    def available_models(self) -> list[ModelInfo]:
        if self._model_cache is not None:
            return self._model_cache
        try:
            models = self.provider.list_models()
        except Exception:
            return []
        self._model_cache = models
        return models

    def usable_model(self, chat_id: str, preferred: str | None = None) -> str:
        """Return a model that belongs to the active provider, when discoverable."""
        candidate = preferred or self.settings.model
        models = self.available_models()
        if not models:
            return candidate
        ids = {model.id for model in models}
        if candidate in ids:
            return candidate
        if self.settings.model in ids:
            fallback = self.settings.model
        else:
            fallback = models[0].id
        if preferred:
            self.store.set_gateway_preferences(
                self.gateway_id, chat_id, model=fallback,
            )
        return fallback

    def session_cache(self) -> dict[str, AgentSession]:
        return self._session_cache

    def has_active_turn(self, chat_id: str) -> bool:
        return chat_id in self._active_turns

    def activity_label(self, chat_id: str) -> str:
        control = self._active_turns.get(chat_id)
        if control is None:
            return "Idle"
        return {
            AgentState.THINKING: "Thinking",
            AgentState.RUNNING_TOOL: "Running a tool",
            AgentState.AWAITING_APPROVAL: "Awaiting approval",
            AgentState.RESPONDING: "Streaming response",
            AgentState.COMPLETED: "Completed",
            AgentState.STOPPED: "Stopped",
            AgentState.FAILED: "Failed",
        }.get(control.state, "Working")

    def lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    def _session_factory(
        self,
        session_id: str | None = None,
        *,
        model: str | None = None,
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

    def factory_for(self, chat_id: str):
        preferences = self.preferences(chat_id)

        def factory(session_id: str | None = None) -> AgentSession:
            return self._session_factory(
                session_id,
                model=self.usable_model(chat_id, preferences.get("model")),
                approval_mode=preferences.get("approval_mode"),
            )

        return factory

    def get_or_create(self, chat_id: str) -> AgentSession:
        return get_or_create_session(
            platform=self.gateway_id,
            chat_id=chat_id,
            store=self.store,
            session_cache=self._session_cache,
            factory=self.factory_for(chat_id),
        )

    def reset(self, chat_id: str) -> AgentSession:
        return reset_session(
            platform=self.gateway_id,
            chat_id=chat_id,
            store=self.store,
            session_cache=self._session_cache,
            factory=self.factory_for(chat_id),
        )

    async def stop_turn(
        self,
        chat_id: str,
        approval_flow: ApprovalFlowService,
    ) -> None:
        control = self._active_turns.get(chat_id)
        if control is None or control.state in {
            AgentState.COMPLETED,
            AgentState.STOPPED,
            AgentState.FAILED,
        }:
            await self.delivery.send(chat_id, "Nothing is currently running.")
            return
        control.cancel.set()
        control.stop_requested.set()
        await approval_flow.deny_pending(chat_id)
        await self.delivery.send(chat_id, "Stopping current response…")

    async def run_turn(
        self,
        session: AgentSession,
        user_input: str,
        chat_id: str,
        approval_flow: ApprovalFlowService,
        *,
        image_paths: tuple[str, ...] = (),
    ) -> None:
        preferences = self.preferences(chat_id)
        transport = preferences.get(
            "stream_transport", self.runtime_config.stream_transport,
        )
        consumer = self.delivery.create_stream_consumer(chat_id, transport)
        consumer_task = asyncio.create_task(consumer.run())
        session.loop.approval_manager.set_callback(
            approval_flow.callback_for(chat_id),
        )
        control = TurnControl()
        self._active_turns[chat_id] = control
        session.begin_turn()
        turn_messages = session.turn_messages()
        stop_typing = asyncio.Event()

        typing_task = asyncio.create_task(
            self.delivery.typing_until(
                chat_id,
                stop_typing,
                lambda: is_typing_state(control.state),
            ),
        )

        turn_context = None
        if image_paths:
            from agent.messages import TurnContext
            from agent.vision.attach import build_user_provider_content

            provider_content = build_user_provider_content(
                user_input,
                image_paths,
                provider=session.provider,
                model=session.model,
            )
            turn_context = TurnContext(provider_user_content=provider_content)

        finish_lock = threading.Lock()
        stream_finished = False

        def finish_stream(stopped: bool) -> None:
            nonlocal stream_finished
            with finish_lock:
                if stream_finished:
                    return
                stream_finished = True
            if stopped:
                consumer.on_delta("\n\n⏹ Stopped")
            consumer.finish()

        def run_sync() -> None:
            try:
                events = session.loop.stream_events(
                    turn_messages,
                    user_input,
                    turn_context=turn_context,
                    cancel=control.cancel,
                    defer_compaction_persistence=True,
                )
                for event in events:
                    control.state = event.state
                    if event.state == AgentState.STOPPED:
                        break
                    if control.cancel.is_set():
                        events.close()
                        break
                    if event.state == AgentState.RESPONDING and event.content:
                        consumer.on_delta(event.content)
            finally:
                stopped = (
                    control.cancel.is_set()
                    or control.state == AgentState.STOPPED
                )
                if stopped:
                    control.cancel.set()
                finish_stream(stopped)

        worker_task = asyncio.create_task(asyncio.to_thread(run_sync))
        stop_task = asyncio.create_task(control.stop_requested.wait())

        def consume_background_result(task: asyncio.Task[None]) -> None:
            try:
                task.result()
            except Exception:
                logger.exception("Stopped gateway worker failed during cleanup")

        try:
            done, _ = await asyncio.wait(
                {worker_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            stopped_early = stop_task in done and not worker_task.done()
            if stopped_early:
                finish_stream(True)
                worker_task.add_done_callback(consume_background_result)
            else:
                await worker_task
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task
            await consumer_task
            if control.cancel.is_set() or control.state == AgentState.STOPPED:
                session.cancel_turn()
                session.maybe_spawn_background_review(interrupted=True)
            else:
                turn_start = session.latest_turn_start(turn_messages)
                session.commit_turn_messages(turn_messages)
                session.complete_turn()
                session.scan_turn_for_memory_tool_use(turn_start)
                session.scan_turn_for_skill_tool_use(turn_start)
                session.record_turn_tool_iterations(
                    AgentSession.count_turn_tool_iterations(
                        session.messages, turn_start,
                    ),
                )
                self.store.set_gateway_binding(
                    self.gateway_id, chat_id, session.persistence.session_id,
                )
                if session.prompt.memory_config.memory_notifications != "off":
                    def _notify(message: str | None) -> None:
                        if message and self._loop is not None:
                            asyncio.run_coroutine_threadsafe(
                                self.delivery.send(
                                    chat_id,
                                    f"Self-improvement review: {message}",
                                ),
                                self._loop,
                            )

                    session.maybe_spawn_background_review(on_complete=_notify)
        finally:
            stop_typing.set()
            session.cancel_turn()
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
            session.loop.approval_manager.set_callback(None)
            self._active_turns.pop(chat_id, None)
