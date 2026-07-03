"""Telegram adapter normalization tests."""

from __future__ import annotations

import pytest

telegram = pytest.importorskip("telegram")

from agent.gateway.integrations.telegram.adapter import inbound_from_update


class _User:
    id = 42
    full_name = "Test User"
    username = "tester"


class _Chat:
    id = 12345
    type = telegram.constants.ChatType.PRIVATE


class _Message:
    message_id = 99
    text = "hello there"
    caption = None


class _Update:
    effective_message = _Message()
    effective_chat = _Chat()
    effective_user = _User()


def test_inbound_from_update_private_dm() -> None:
    inbound = inbound_from_update(_Update())
    assert inbound is not None
    assert inbound.text == "hello there"
    assert inbound.source.platform == "telegram"
    assert inbound.source.chat_id == "12345"
    assert inbound.source.user_id == "42"
    assert inbound.source.message_id == "99"


def test_inbound_from_update_ignores_group() -> None:
    update = _Update()
    update.effective_chat.type = telegram.constants.ChatType.GROUP
    assert inbound_from_update(update) is None
