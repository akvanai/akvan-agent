"""Telegram adapter for the Akvan gateway."""

from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable

from agent.gateway.integrations.telegram.config import TelegramSettings
from agent.gateway.integrations.telegram.rich import (
    RICH_MESSAGE_MAX_CHARS,
    has_markdown_formatting,
    markdown_to_telegram_html,
    rich_eligible,
    rich_message_payload,
)
from agent.gateway.contracts import (
    CallbackHandler, GatewayCapabilities, MessageHandler,
)
from agent.gateway.types import (
    CallbackInteraction, ChatSource, InboundMessage, InlineKeyboard, SendResult,
)

logger = logging.getLogger(__name__)

try:
    from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ChatType
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler as TelegramMessageHandler,
        filters,
    )

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = object  # type: ignore[misc,assignment]
    Application = object  # type: ignore[misc,assignment]
    ContextTypes = object  # type: ignore[misc,assignment]
    filters = None  # type: ignore[assignment]
    ChatType = object  # type: ignore[misc,assignment]


def check_telegram_requirements() -> bool:
    return TELEGRAM_AVAILABLE


def callback_from_update(update: Update) -> CallbackInteraction | None:
    """Normalize a Telegram callback query into a safe gateway interaction."""
    query = update.callback_query
    user = update.effective_user
    message = query.message if query is not None else None
    chat = message.chat if message is not None else None
    if query is None or user is None or message is None or chat is None or not query.data:
        return None
    if chat.type != ChatType.PRIVATE:
        return None
    return CallbackInteraction(
        platform="telegram",
        chat_id=str(chat.id),
        user_id=str(user.id),
        message_id=str(message.message_id),
        callback_id=str(query.id),
        data=str(query.data),
    )


def inbound_from_update(
    update: Update,
    *,
    image_paths: tuple[str, ...] = (),
) -> InboundMessage | None:
    """Normalize a Telegram update into an inbound gateway message."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return None
    if chat.type != ChatType.PRIVATE:
        return None
    text = (message.text or message.caption or "").strip()
    if not text and not image_paths:
        return None
    if not text and image_paths:
        text = "Please examine this image."
    source = ChatSource(
        platform="telegram",
        chat_id=str(chat.id),
        user_id=str(user.id),
        user_name=user.full_name or user.username,
        chat_type="dm",
        message_id=str(message.message_id),
    )
    return InboundMessage(
        text=text,
        source=source,
        raw=update,
        image_paths=image_paths,
    )


async def download_telegram_images(update: Update, bot: object) -> tuple[str, ...]:
    """Download photo/image-document attachments into the screenshot cache."""

    message = update.effective_message
    if message is None:
        return ()
    from agent.vision.encode import screenshots_dir
    import uuid

    paths: list[str] = []
    photos = getattr(message, "photo", None) or []
    if photos:
        largest = photos[-1]
        file = await bot.get_file(largest.file_id)
        dest = screenshots_dir() / f"telegram_{uuid.uuid4().hex}.jpg"
        await file.download_to_drive(custom_path=str(dest))
        try:
            dest.chmod(0o600)
        except OSError:
            pass
        paths.append(str(dest))

    document = getattr(message, "document", None)
    if document is not None:
        mime = str(getattr(document, "mime_type", "") or "")
        name = str(getattr(document, "file_name", "") or "").lower()
        if mime.startswith("image/") or name.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        ):
            file = await bot.get_file(document.file_id)
            suffix = ".bin"
            if "." in name:
                suffix = "." + name.rsplit(".", 1)[-1]
            elif "/" in mime:
                suffix = "." + mime.split("/", 1)[1].split(";")[0]
            dest = screenshots_dir() / f"telegram_{uuid.uuid4().hex}{suffix}"
            await file.download_to_drive(custom_path=str(dest))
            try:
                dest.chmod(0o600)
            except OSError:
                pass
            paths.append(str(dest))
    return tuple(paths)


class TelegramAdapter:
    """Receive Telegram DMs and send/edit replies."""

    MAX_MESSAGE_LENGTH = 4096

    def __init__(
        self,
        *,
        token: str,
        gateway_settings: TelegramSettings | None = None,
    ) -> None:
        self._token = token
        self._app: Application | None = None
        self._handler: MessageHandler | None = None
        self._callback_handler: CallbackHandler | None = None
        settings = gateway_settings or TelegramSettings(
            telegram_bot_token=token,
            telegram_allowed_users=frozenset(),
        )
        self._rich_messages_enabled = settings.rich_messages
        self._rich_drafts_enabled = settings.rich_drafts
        self._rich_send_disabled = False
        self._rich_draft_disabled = False

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            buttons=True,
            callbacks=True,
            message_editing=True,
            typing=True,
            draft_streaming=self._supports_draft_streaming(),
            max_message_length=(
                self._streaming_limit() or self.MAX_MESSAGE_LENGTH
            ),
        )

    async def send_final(
        self, chat_id: str, text: str, *, reply_to: str | None = None,
    ) -> SendResult:
        """Deliver completed content using rich Telegram rendering when useful."""
        return await self.send_rich(chat_id, text, reply_to=reply_to)

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    def set_callback_handler(self, handler: CallbackHandler) -> None:
        self._callback_handler = handler

    @property
    def _bot(self):
        if self._app is None:
            return None
        return self._app.bot

    def _bot_supports_rich(self) -> bool:
        bot = self._bot
        if bot is None:
            return False
        return inspect.iscoroutinefunction(getattr(bot, "do_api_request", None))

    def _supports_draft_streaming(self) -> bool:
        """Telegram supports sendMessageDraft for private chats only."""
        bot = self._bot
        if bot is None or not hasattr(bot, "send_message_draft"):
            return False
        return True

    def _streaming_limit(self) -> int | None:
        if (
            self._rich_messages_enabled
            and not self._rich_send_disabled
            and self._bot_supports_rich()
        ):
            return RICH_MESSAGE_MAX_CHARS
        return None

    def _rich_eligible(self, content: str) -> bool:
        return rich_eligible(
            content,
            rich_messages_enabled=self._rich_messages_enabled,
            rich_send_disabled=self._rich_send_disabled,
            bot_supports_rich=self._bot_supports_rich(),
        )

    def _should_attempt_rich_draft(self, content: str) -> bool:
        return bool(
            self._rich_messages_enabled
            and self._rich_drafts_enabled
            and not self._rich_send_disabled
            and not self._rich_draft_disabled
            and content
            and content.strip()
            and len(content) <= RICH_MESSAGE_MAX_CHARS
            and self._bot_supports_rich()
        )

    @staticmethod
    def _is_rich_capability_error(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        if name in {"endpointnotfound", "invalidtoken"}:
            return True
        if isinstance(exc, (AttributeError, TypeError, NotImplementedError)):
            return True
        if getattr(exc, "error_code", None) == 404:
            return True
        message = str(exc).lower()
        if ("method" in message or "endpoint" in message) and (
            "not found" in message or "does not exist" in message
        ):
            return True
        return "no such method" in message

    @staticmethod
    def _is_bad_request_error(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        return "badrequest" in name

    def _is_rich_fallback_error(self, exc: Exception) -> bool:
        if self._is_bad_request_error(exc):
            return True
        if self._is_rich_capability_error(exc):
            return True
        message = str(exc).lower()
        return "unsupported" in message or "not implemented" in message

    @staticmethod
    def _api_message_id(message: object) -> str | None:
        """Extract message_id from a PTB object or raw Bot API dict."""
        if isinstance(message, dict):
            raw_id = message.get("message_id")
            if raw_id is None:
                nested = message.get("result")
                if isinstance(nested, dict):
                    raw_id = nested.get("message_id")
            if raw_id is not None:
                return str(raw_id)
            return None
        raw_id = getattr(message, "message_id", None)
        return str(raw_id) if raw_id is not None else None

    async def connect(self) -> bool:
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "python-telegram-bot is not installed. "
                "python-telegram-bot is not installed. Re-run ./install.sh to update Akvan."
            )
            return False
        if not self._token:
            logger.error("Telegram bot token is missing.")
            return False

        self._app = (
            Application.builder().token(self._token).concurrent_updates(True).build()
        )
        self._app.add_handler(
            TelegramMessageHandler(
                (
                    (filters.TEXT | filters.CAPTION | filters.PHOTO | filters.Document.IMAGE)
                    & ~filters.COMMAND
                ),
                self._on_message,
            )
        )
        for command in (
            "start", "new", "status", "usage", "compress", "knowledge",
            "settings", "stop", "help"
        ):
            self._app.add_handler(CommandHandler(command, self._on_command))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.bot.set_my_commands([
            BotCommand("new", "Start a fresh conversation"),
            BotCommand("status", "Show session and activity"),
            BotCommand("usage", "Show estimated context usage"),
            BotCommand("compress", "Compact conversation history"),
            BotCommand("knowledge", "Review global knowledge"),
            BotCommand("settings", "Configure model, safety, and streaming"),
            BotCommand("stop", "Stop the current response"),
            BotCommand("help", "Show help"),
        ])
        if self._app.updater is not None:
            await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram adapter connected (polling).")
        return True

    async def disconnect(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None
            logger.info("Telegram adapter disconnected.")

    async def _try_send_rich(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult | None:
        bot = self._bot
        if bot is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        payload: dict[str, object] = {
            "chat_id": int(chat_id),
            "rich_message": rich_message_payload(content),
        }
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": int(reply_to)}
        try:
            message = await bot.do_api_request(
                "sendRichMessage",
                api_kwargs=payload,
            )
        except Exception as exc:
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    self._rich_send_disabled = True
                logger.info(
                    "sendRichMessage unavailable, falling back to plain send: %s",
                    exc,
                )
                return None
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(
                success=False,
                error=str(exc),
                retry_after=float(retry_after) if retry_after else None,
            )
        message_id = self._api_message_id(message)
        return SendResult(
            success=True,
            message_id=message_id,
        )

    async def send_rich(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        if not self._rich_eligible(text):
            return await self.send(chat_id, text, reply_to=reply_to)
        result = await self._try_send_rich(chat_id, text, reply_to=reply_to)
        if result is None:
            return await self.send(chat_id, text, reply_to=reply_to)
        return result

    @staticmethod
    def _reply_markup(buttons: InlineKeyboard) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(button.text, callback_data=button.callback_data)
             for button in row]
            for row in buttons
        ])

    async def send_with_buttons(
        self, chat_id: str, text: str, buttons: InlineKeyboard,
    ) -> SendResult:
        if self._app is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        try:
            message = await self._app.bot.send_message(
                chat_id=int(chat_id), text=text, reply_markup=self._reply_markup(buttons),
            )
            return SendResult(success=True, message_id=str(message.message_id))
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(success=False, error=str(exc), retry_after=float(retry_after) if retry_after else None)

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        if self._app is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        if self._rich_eligible(text):
            result = await self._try_send_rich(chat_id, text, reply_to=reply_to)
            if result is not None:
                return result
        try:
            kwargs: dict[str, object] = {}
            if reply_to is not None:
                kwargs["reply_to_message_id"] = int(reply_to)
            html_text = markdown_to_telegram_html(text)
            if html_text is not None:
                kwargs["parse_mode"] = "HTML"
                message = await self._app.bot.send_message(
                    chat_id=int(chat_id),
                    text=html_text,
                    **kwargs,
                )
            else:
                message = await self._app.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    **kwargs,
                )
            return SendResult(success=True, message_id=str(message.message_id))
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(
                success=False,
                error=str(exc),
                retry_after=float(retry_after) if retry_after else None,
            )

    async def _try_send_rich_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
    ) -> bool:
        bot = self._bot
        if bot is None:
            return False
        payload: dict[str, object] = {
            "chat_id": int(chat_id),
            "draft_id": int(draft_id),
            "rich_message": rich_message_payload(content),
        }
        try:
            ok = await bot.do_api_request("sendRichMessageDraft", api_kwargs=payload)
            return bool(ok)
        except Exception as exc:
            if self._is_rich_capability_error(exc):
                self._rich_draft_disabled = True
            logger.debug("sendRichMessageDraft failed: %s", exc)
            return False

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        text: str,
    ) -> SendResult:
        if self._app is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        if self._should_attempt_rich_draft(text):
            if await self._try_send_rich_draft(chat_id, draft_id, text):
                return SendResult(success=True, message_id=None)
        bot = self._bot
        if bot is None or not hasattr(bot, "send_message_draft"):
            return SendResult(success=False, error="api_unavailable")
        visible = text
        if len(visible) > self.MAX_MESSAGE_LENGTH:
            visible = visible[: self.MAX_MESSAGE_LENGTH]
        try:
            ok = await bot.send_message_draft(
                chat_id=int(chat_id),
                draft_id=int(draft_id),
                text=visible,
            )
            if ok:
                return SendResult(success=True, message_id=None)
            return SendResult(success=False, error="draft_rejected")
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(
                success=False,
                error=str(exc),
                retry_after=float(retry_after) if retry_after else None,
            )

    async def _try_edit_rich(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult | None:
        bot = self._bot
        if bot is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        payload: dict[str, object] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "rich_message": rich_message_payload(content),
        }
        try:
            await bot.do_api_request("editMessageText", api_kwargs=payload)
        except Exception as exc:
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    self._rich_send_disabled = True
                if "not modified" in str(exc).lower():
                    return SendResult(success=True, message_id=message_id)
                return None
            if "not modified" in str(exc).lower():
                return SendResult(success=True, message_id=message_id)
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(
                success=False,
                error=str(exc),
                retry_after=float(retry_after) if retry_after else None,
            )
        return SendResult(success=True, message_id=message_id)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        if self._app is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        if finalize and self._rich_eligible(text):
            rich_result = await self._try_edit_rich(chat_id, message_id, text)
            if rich_result is not None:
                return rich_result
        try:
            message = await self._app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
            )
            return SendResult(success=True, message_id=str(message.message_id))
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(
                success=False,
                error=str(exc),
                retry_after=float(retry_after) if retry_after else None,
            )

    async def edit_with_buttons(
        self, chat_id: str, message_id: str, text: str,
        buttons: InlineKeyboard | None = None,
    ) -> SendResult:
        if self._app is None:
            return SendResult(success=False, error="Telegram adapter is not connected.")
        try:
            message = await self._app.bot.edit_message_text(
                chat_id=int(chat_id), message_id=int(message_id), text=text,
                reply_markup=self._reply_markup(buttons) if buttons else None,
            )
            return SendResult(success=True, message_id=str(message.message_id))
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return SendResult(success=True, message_id=message_id)
            retry_after = getattr(exc, "retry_after", None)
            return SendResult(success=False, error=str(exc), retry_after=float(retry_after) if retry_after else None)

    async def answer_callback(
        self, callback_id: str, text: str | None = None, *, alert: bool = False,
    ) -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.answer_callback_query(
                callback_query_id=callback_id, text=text, show_alert=alert,
            )
        except Exception:
            logger.debug("Failed to answer Telegram callback", exc_info=True)

    async def send_typing(self, chat_id: str) -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.send_chat_action(
                chat_id=int(chat_id),
                action="typing",
            )
        except Exception:
            logger.debug("Failed to send Telegram typing action", exc_info=True)

    async def _dispatch(self, update: Update) -> None:
        if self._handler is None:
            return
        image_paths: tuple[str, ...] = ()
        if self._app is not None:
            try:
                image_paths = await download_telegram_images(update, self._app.bot)
            except Exception:
                logger.exception("Failed to download Telegram image attachments")
        inbound = inbound_from_update(update, image_paths=image_paths)
        if inbound is None:
            return
        await self._handler(inbound)

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        _ = context
        await self._dispatch(update)

    async def _on_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        _ = context
        await self._dispatch(update)

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        _ = context
        if self._callback_handler is None:
            return
        callback = callback_from_update(update)
        if callback is not None:
            await self._callback_handler(callback)
