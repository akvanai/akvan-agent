"""Telegram adapter rich and draft streaming tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from agent.gateway.integrations.telegram.adapter import TelegramAdapter
from agent.gateway.integrations.telegram.config import TelegramSettings
from agent.gateway.types import InlineButton

TABLE = "| Case | Status |\n| --- | --- |\n| one | ok |"


def _settings(**extra: object) -> TelegramSettings:
    return TelegramSettings(
        telegram_bot_token="token",
        telegram_allowed_users=frozenset({"1"}),
        **extra,
    )


def _connected_adapter(**extra: object) -> TelegramAdapter:
    adapter = TelegramAdapter(token="token", gateway_settings=_settings(**extra))
    adapter._app = MagicMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.do_api_request = AsyncMock(
        return_value=MagicMock(message_id=42)
    )
    adapter._app.bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=7)
    )
    adapter._app.bot.send_message_draft = AsyncMock(return_value=True)
    adapter._app.bot.edit_message_text = AsyncMock(
        return_value=MagicMock(message_id=9)
    )
    return adapter


def test_send_routes_markdown_bold_to_send_rich_message() -> None:
    adapter = _connected_adapter(rich_messages=True)

    async def run() -> None:
        result = await adapter.send("123", "Hello **world**")
        assert result.success
        call = adapter._app.bot.do_api_request.await_args
        assert call.args[0] == "sendRichMessage"
        assert "**world**" in call.kwargs["api_kwargs"]["rich_message"]["markdown"]
        adapter._app.bot.send_message.assert_not_called()

    asyncio.run(run())


def test_send_routes_rich_table_to_send_rich_message() -> None:
    adapter = _connected_adapter(rich_messages=True)

    async def run() -> None:
        result = await adapter.send("123", TABLE)
        assert result.success
        assert result.message_id == "42"
        call = adapter._app.bot.do_api_request.await_args
        assert call.args[0] == "sendRichMessage"
        assert "| Case | Status |" in call.kwargs["api_kwargs"]["rich_message"]["markdown"]
        adapter._app.bot.send_message.assert_not_called()

    asyncio.run(run())


def test_send_rich_message_id_from_dict_response() -> None:
    adapter = _connected_adapter(rich_messages=True)
    adapter._app.bot.do_api_request = AsyncMock(return_value={"message_id": 99})

    async def run() -> None:
        result = await adapter.send("123", "Hello **world**")
        assert result.success
        assert result.message_id == "99"

    asyncio.run(run())


def test_api_message_id_reads_nested_result() -> None:
    assert TelegramAdapter._api_message_id({"result": {"message_id": 55}}) == "55"


def test_send_draft_uses_plain_draft_by_default() -> None:
    adapter = _connected_adapter(rich_messages=True, rich_drafts=False)

    async def run() -> None:
        result = await adapter.send_draft("123", 5, "Hello")
        assert result.success
        adapter._app.bot.send_message_draft.assert_awaited_once()
        adapter._app.bot.do_api_request.assert_not_called()

    asyncio.run(run())


def test_send_draft_uses_rich_draft_when_opted_in() -> None:
    adapter = _connected_adapter(rich_messages=True, rich_drafts=True)

    async def run() -> None:
        result = await adapter.send_draft("123", 5, TABLE)
        assert result.success
        call = adapter._app.bot.do_api_request.await_args
        assert call.args[0] == "sendRichMessageDraft"
        adapter._app.bot.send_message_draft.assert_not_called()

    asyncio.run(run())


def test_edit_message_rich_finalize() -> None:
    adapter = _connected_adapter(rich_messages=True)

    async def run() -> None:
        result = await adapter.edit_message("123", "9", TABLE, finalize=True)
        assert result.success
        call = adapter._app.bot.do_api_request.await_args
        assert call.args[0] == "editMessageText"
        adapter._app.bot.edit_message_text.assert_not_called()

    asyncio.run(run())


def test_rich_capability_failure_falls_back_to_plain_send() -> None:
    adapter = _connected_adapter(rich_messages=True)
    adapter._app.bot.do_api_request = AsyncMock(
        side_effect=AttributeError("Endpoint 'sendRichMessage' not found")
    )

    async def run() -> None:
        result = await adapter.send("123", "Hello **world**")
        assert result.success
        call = adapter._app.bot.send_message.await_args
        assert call.kwargs["parse_mode"] == "HTML"
        assert "<b>world</b>" in call.kwargs["text"]
        assert adapter._rich_send_disabled is True

    asyncio.run(run())


def test_send_with_buttons_builds_inline_keyboard() -> None:
    adapter = _connected_adapter()

    async def run() -> None:
        result = await adapter.send_with_buttons(
            "123", "Choose", ((InlineButton("Allow", "approval:id:once"),),)
        )
        assert result.success
        markup = adapter._app.bot.send_message.await_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "approval:id:once"

    asyncio.run(run())
