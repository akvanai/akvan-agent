"""Gateway extension-boundary tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayCapabilities
from agent.gateway.registry import get_gateway_integration
from agent.gateway.runner import run_gateway
from agent.gateway.service import GatewayService
from agent.gateway.types import ChatSource, InboundMessage


def _service(gateway_id: str = "email") -> GatewayService:
    settings = MagicMock(
        model="test-model",
        approval_mode="ask",
        approval_timeout=60,
        terminal_timeout=120,
    )
    adapter = MagicMock()
    adapter.capabilities = GatewayCapabilities(
        message_editing=False,
        max_message_length=100_000,
    )
    store = MagicMock()
    store.get_gateway_preferences.return_value = {}
    return GatewayService(
        settings=settings,
        gateway_id=gateway_id,
        gateway_name="Email",
        runtime_config=GatewayRuntimeConfig(stream_transport="edit"),
        access_policy=lambda user_id: user_id == "allowed@example.com",
        provider=MagicMock(name="provider"),
        store=store,
        adapter=adapter,
    )


def test_shared_service_uses_injected_gateway_identity() -> None:
    service = _service()
    message = InboundMessage(
        text="hello",
        source=ChatSource(
            platform="email",
            chat_id="thread-42",
            user_id="allowed@example.com",
        ),
    )
    assert service._is_authorized(message)
    assert "Email commands" in service.command.help_text()
    assert "/knowledge" in service.command.help_text()
    service.chat_session.preferences("thread-42")
    service.store.get_gateway_preferences.assert_called_once_with(
        "email", "thread-42"
    )


def test_shared_service_rejects_cross_gateway_message() -> None:
    service = _service()
    message = InboundMessage(
        text="hello",
        source=ChatSource(
            platform="telegram",
            chat_id="thread-42",
            user_id="allowed@example.com",
        ),
    )
    assert not service._is_authorized(message)


def test_telegram_is_registered_as_an_integration() -> None:
    integration = get_gateway_integration("telegram")
    assert integration is not None
    assert integration.definition.name == "Telegram"
    assert integration.runtime_config(integration.load_settings())


def test_unknown_gateway_fails_before_provider_bootstrap(caplog) -> None:
    with caplog.at_level("ERROR"):
        assert asyncio.run(run_gateway(gateway_id="missing")) == 2
    assert "Unknown gateway 'missing'" in caplog.text
