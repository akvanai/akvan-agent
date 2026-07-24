"""Gateway soft-stop rolls back the in-flight turn and keeps the session."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.events import AgentState
from agent.gateway.chat_session import ChatSessionService
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayCapabilities
from agent.providers.base import Provider, ProviderStreamEvent
from agent.session import AgentSession


class SlowChunkProvider(Provider):
    name = "slow-chunk"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, model, options=None):  # pragma: no cover
        raise AssertionError("complete should not be used")

    def stream_events(self, messages, model, options=None):
        self.started.set()
        self.release.wait(timeout=2.0)
        yield ProviderStreamEvent(content="late")


@pytest.fixture
def akvan_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_run_turn_rolls_back_messages_on_stop(akvan_home) -> None:
    provider = SlowChunkProvider()
    session = AgentSession.create(
        provider=provider,
        model="test-model",
        max_iterations=3,
        store=None,
        enabled_toolsets=(),
    )
    original_messages = list(session.messages)

    adapter = MagicMock()
    adapter.capabilities = GatewayCapabilities(
        buttons=True,
        callbacks=True,
        message_editing=True,
        typing=True,
        draft_streaming=False,
        max_message_length=4096,
    )
    adapter.send = AsyncMock()
    adapter.send_typing = AsyncMock()

    delivery = MagicMock()
    consumer = MagicMock()
    consumer.run = AsyncMock(return_value=None)
    delivery.create_stream_consumer.return_value = consumer
    delivery.typing_until = AsyncMock(return_value=None)
    delivery.send = AsyncMock()

    settings = MagicMock()
    settings.model = "test-model"
    settings.approval_mode = "ask"
    settings.approval_timeout = 60
    settings.terminal_timeout = 120

    store = MagicMock()
    store.get_gateway_preferences.return_value = {"stream_transport": "edit"}

    service = ChatSessionService(
        settings=settings,
        gateway_id="telegram",
        provider=provider,
        store=store,
        runtime_config=GatewayRuntimeConfig(),
        delivery=delivery,
    )
    approval_flow = MagicMock()
    approval_flow.callback_for.return_value = None
    approval_flow.deny_pending = AsyncMock()

    async def scenario() -> None:
        turn_task = asyncio.create_task(
            service.run_turn(session, "hello", "chat-1", approval_flow)
        )
        assert await asyncio.to_thread(provider.started.wait, 2.0)
        await service.stop_turn("chat-1", approval_flow)
        try:
            await asyncio.wait_for(turn_task, timeout=0.5)
        finally:
            provider.release.set()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert session.messages == original_messages
    delivery.send.assert_any_await("chat-1", "Stopping current response…")
    consumer.on_delta.assert_any_call("\n\n⏹ Stopped")


def test_stop_turn_treats_stopped_state_as_idle() -> None:
    service = ChatSessionService(
        settings=MagicMock(
            model="m",
            approval_mode="ask",
            approval_timeout=60,
            terminal_timeout=120,
        ),
        gateway_id="telegram",
        provider=MagicMock(),
        store=MagicMock(),
        runtime_config=GatewayRuntimeConfig(),
        delivery=MagicMock(send=AsyncMock()),
    )
    from agent.gateway.chat_session import TurnControl

    control = TurnControl(state=AgentState.STOPPED)
    service._active_turns["1"] = control
    asyncio.run(service.stop_turn("1", MagicMock(deny_pending=AsyncMock())))
    service.delivery.send.assert_awaited_once_with(
        "1", "Nothing is currently running."
    )
    assert not control.cancel.is_set()
