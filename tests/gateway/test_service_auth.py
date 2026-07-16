"""Platform-neutral gateway service authorization tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.gateway.approval_flow import parse_approval_choice
from agent.gateway.chat_session import TurnControl
from agent.gateway.command import CommandService
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayCapabilities
from agent.gateway.service import GatewayService
from agent.gateway.types import CallbackInteraction, ChatSource, InboundMessage
from agent.providers.base import ModelInfo
from agent.tools.approval import ApprovalChoice


@pytest.fixture
def runner() -> GatewayService:
    settings = MagicMock()
    settings.model = "test-model"
    settings.approval_mode = "ask"
    settings.approval_timeout = 60
    settings.terminal_timeout = 120
    provider = MagicMock()
    store = MagicMock()
    store.get_gateway_preferences.return_value = {}
    adapter = MagicMock()
    adapter.capabilities = GatewayCapabilities(
        buttons=True,
        callbacks=True,
        message_editing=True,
        typing=True,
        draft_streaming=True,
        max_message_length=4096,
    )
    return GatewayService(
        settings=settings,
        gateway_id="telegram",
        gateway_name="Telegram",
        runtime_config=GatewayRuntimeConfig(),
        access_policy=lambda user_id: user_id == "42",
        provider=provider,
        store=store,
        adapter=adapter,
    )


def test_is_authorized_allows_listed_user(runner: GatewayService) -> None:
    message = InboundMessage(
        text="hi",
        source=ChatSource(
            platform="telegram",
            chat_id="1",
            user_id="42",
        ),
    )
    assert runner._is_authorized(message) is True


def test_is_authorized_rejects_unknown_user(runner: GatewayService) -> None:
    message = InboundMessage(
        text="hi",
        source=ChatSource(
            platform="telegram",
            chat_id="1",
            user_id="99",
        ),
    )
    assert runner._is_authorized(message) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", ApprovalChoice.ONCE),
        ("2", ApprovalChoice.SESSION),
        ("3", ApprovalChoice.ALWAYS),
        ("4", ApprovalChoice.DENY),
        ("yes", ApprovalChoice.ONCE),
        ("deny", ApprovalChoice.DENY),
    ],
)
def test_parse_approval_choice(text: str, expected: ApprovalChoice) -> None:
    assert parse_approval_choice(text) == expected


def test_settings_keyboard_has_four_actions() -> None:
    keyboard = CommandService.settings_keyboard()
    assert len(keyboard) == 4
    assert [row[0].text for row in keyboard] == [
        "Model", "Approval policy", "Streaming mode", "Close"
    ]


def test_stop_without_active_turn_reports_idle(runner: GatewayService) -> None:
    runner.adapter.send = AsyncMock()

    import asyncio
    asyncio.run(runner.chat_session.stop_turn("1", runner.approval_flow))

    runner.adapter.send.assert_awaited_once_with(
        "1", "Nothing is currently running."
    )


def test_stale_approval_callback_is_rejected(runner: GatewayService) -> None:
    runner.adapter.answer_callback = AsyncMock()
    callback = CallbackInteraction(
        platform="telegram", chat_id="1", user_id="42", message_id="9",
        callback_id="cb", data="approval:expired:once",
    )

    import asyncio
    asyncio.run(runner.handle_callback(callback))

    runner.adapter.answer_callback.assert_awaited_once_with(
        "cb", "This approval has expired.", alert=True
    )


def test_stop_sets_active_turn_cancellation(runner: GatewayService) -> None:
    runner.adapter.send = AsyncMock()
    control = TurnControl()
    runner.chat_session._active_turns["1"] = control

    import asyncio
    asyncio.run(runner.chat_session.stop_turn("1", runner.approval_flow))

    assert control.cancel.is_set()
    runner.adapter.send.assert_awaited_once_with("1", "Stopping current response…")


def test_gateway_replaces_stale_chat_model_preference(runner: GatewayService) -> None:
    runner.settings.model = "deepseek-chat"
    runner.provider.list_models.return_value = [
        ModelInfo(id="deepseek-chat", name="DeepSeek Chat"),
        ModelInfo(id="deepseek-v4-pro", name="DeepSeek V4 Pro"),
    ]
    runner.store.get_gateway_preferences.return_value = {
        "model": "deepseek/deepseek-v4-pro",
    }

    model = runner.chat_session.usable_model(
        "1", "deepseek/deepseek-v4-pro",
    )

    assert model == "deepseek-chat"
    runner.store.set_gateway_preferences.assert_called_once_with(
        "telegram", "1", model="deepseek-chat",
    )


def test_gateway_keeps_provider_usable_chat_model(runner: GatewayService) -> None:
    runner.settings.model = "deepseek-chat"
    runner.provider.list_models.return_value = [
        ModelInfo(id="deepseek-chat", name="DeepSeek Chat"),
        ModelInfo(id="deepseek-v4-pro", name="DeepSeek V4 Pro"),
    ]

    model = runner.chat_session.usable_model("1", "deepseek-v4-pro")

    assert model == "deepseek-v4-pro"
    runner.store.set_gateway_preferences.assert_not_called()
