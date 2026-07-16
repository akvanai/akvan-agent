"""Optional tools for delivering local files and text through a Telegram bot."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from agent.tools.base import Tool
from agent.tools.telegram_delivery.config import (
    is_telegram_delivery_configured,
    load_telegram_delivery_settings,
)

if TYPE_CHECKING:
    from agent.tools.telegram_delivery.config import TelegramDeliverySettings

_PHOTO_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_ANIMATION_TYPES = frozenset({"image/gif"})
_SUPPORTED_IMAGE_TYPES = _PHOTO_TYPES | _ANIMATION_TYPES
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_CAPTION_CHARS = 1024
_MAX_TEXT_CHARS = 4096
_UPLOAD_TIMEOUT = 120.0
_TEXT_TIMEOUT = 30.0

__all__ = [
    "build_telegram_delivery_tools",
    "is_telegram_delivery_configured",
    "load_telegram_delivery_settings",
]


def _require_configured() -> "TelegramDeliverySettings":
    settings = load_telegram_delivery_settings()
    if not settings.telegram_bot_token or not settings.telegram_allowed_users:
        raise ValueError(
            "Telegram delivery is not configured. Run `akvan tools` and set up "
            "Telegram delivery under Social Media."
        )
    return settings


def _recipient(settings: "TelegramDeliverySettings", requested: str | None) -> str:
    recipient = str(requested or "").strip()
    if recipient:
        if recipient not in settings.telegram_allowed_users:
            raise ValueError(
                "The requested Telegram user is not in the Telegram delivery allowlist."
            )
        return recipient
    if len(settings.telegram_allowed_users) == 1:
        return next(iter(settings.telegram_allowed_users))
    raise ValueError(
        "More than one Telegram user is authorized; recipient_user_id is required."
    )


def _local_file(value: str, *, project_root: Path) -> tuple[Path, str, int]:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"File is empty: {path}")
    if size > _MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File is too large ({size} bytes). Telegram bots accept up to "
            f"{_MAX_UPLOAD_BYTES} bytes."
        )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path, content_type, size


def _choose_method(content_type: str, size: int) -> tuple[str, str]:
    if content_type in _PHOTO_TYPES:
        if size > _MAX_PHOTO_BYTES:
            return "sendDocument", "document"
        return "sendPhoto", "photo"
    if content_type in _ANIMATION_TYPES:
        return "sendAnimation", "animation"
    if content_type.startswith("audio/"):
        return "sendAudio", "audio"
    if content_type.startswith("video/"):
        return "sendVideo", "video"
    return "sendDocument", "document"


def _caption(value: str | None) -> str | None:
    if value is None:
        return None
    caption = value.strip()
    if not caption:
        return None
    if len(caption) > _MAX_CAPTION_CHARS:
        raise ValueError(
            f"Caption is too long ({len(caption)} characters). "
            f"Telegram captions are limited to {_MAX_CAPTION_CHARS} characters."
        )
    return caption


def _parse_telegram_response(
    response: httpx.Response, *, kind: str,
) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.is_error or not isinstance(payload, dict) or not payload.get("ok"):
        description = payload.get("description") if isinstance(payload, dict) else None
        detail = str(description or f"HTTP {response.status_code}")
        if "chat not found" in detail.lower():
            detail += " Ask the user to open the bot and send /start first."
        raise RuntimeError(f"Telegram rejected the {kind}: {detail}")
    result = payload.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    return {
        "ok": True,
        "delivered": True,
        "platform": "telegram",
        "message_id": message_id,
    }


def _send_file(
    settings: "TelegramDeliverySettings",
    *,
    recipient: str,
    path: Path,
    content_type: str,
    size: int,
    caption: str | None,
) -> dict[str, object]:
    method, field = _choose_method(content_type, size)
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    data: dict[str, str] = {"chat_id": recipient}
    caption_text = _caption(caption)
    if caption_text is not None:
        data["caption"] = caption_text
    try:
        with path.open("rb") as handle:
            response = httpx.post(
                url,
                data=data,
                files={field: (path.name, handle, content_type)},
                timeout=_UPLOAD_TIMEOUT,
            )
    except (OSError, httpx.RequestError) as exc:
        # Request URLs contain bot tokens, so never include the underlying exception.
        raise RuntimeError("Could not send the file to Telegram.") from exc

    result = _parse_telegram_response(response, kind="file")
    result.update(
        {
            "recipient_user_id": recipient,
            "file_name": path.name,
            "content_type": content_type,
            "method": method,
            "size_bytes": size,
        }
    )
    return result


def _send_text(
    settings: "TelegramDeliverySettings",
    *,
    recipient: str,
    text: str,
) -> dict[str, object]:
    body = text.strip()
    if not body:
        raise ValueError("Text must not be empty.")
    if len(body) > _MAX_TEXT_CHARS:
        raise ValueError(
            f"Text is too long ({len(body)} characters). "
            f"Telegram messages are limited to {_MAX_TEXT_CHARS} characters."
        )
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        response = httpx.post(
            url,
            data={"chat_id": recipient, "text": body},
            timeout=_TEXT_TIMEOUT,
        )
    except httpx.RequestError as exc:
        raise RuntimeError("Could not send the text to Telegram.") from exc

    result = _parse_telegram_response(response, kind="message")
    result.update(
        {
            "recipient_user_id": recipient,
            "method": "sendMessage",
            "text_length": len(body),
        }
    )
    return result


def build_telegram_delivery_tools(*, project_root: Path) -> tuple[Tool, ...]:
    root = project_root.resolve()

    def telegram_send_file(
        file_path: str,
        caption: str | None = None,
        recipient_user_id: str | None = None,
        confirmed: bool = False,
    ) -> str:
        if not confirmed:
            raise ValueError("Refusing to send a file without explicit user confirmation.")
        settings = _require_configured()
        recipient = _recipient(settings, recipient_user_id)
        path, content_type, size = _local_file(file_path, project_root=root)
        result = _send_file(
            settings,
            recipient=recipient,
            path=path,
            content_type=content_type,
            size=size,
            caption=caption,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def telegram_send_text(
        text: str,
        recipient_user_id: str | None = None,
        confirmed: bool = False,
    ) -> str:
        if not confirmed:
            raise ValueError("Refusing to send text without explicit user confirmation.")
        settings = _require_configured()
        recipient = _recipient(settings, recipient_user_id)
        result = _send_text(settings, recipient=recipient, text=text)
        return json.dumps(result, ensure_ascii=False, indent=2)

    def telegram_send_image(
        image_path: str,
        caption: str | None = None,
        recipient_user_id: str | None = None,
        confirmed: bool = False,
    ) -> str:
        if not confirmed:
            raise ValueError("Refusing to send an image without explicit user confirmation.")
        settings = _require_configured()
        recipient = _recipient(settings, recipient_user_id)
        path, content_type, size = _local_file(image_path, project_root=root)
        if content_type not in _SUPPORTED_IMAGE_TYPES:
            supported = ", ".join(sorted(_SUPPORTED_IMAGE_TYPES))
            raise ValueError(f"Unsupported image type. Expected one of: {supported}.")
        result = _send_file(
            settings,
            recipient=recipient,
            path=path,
            content_type=content_type,
            size=size,
            caption=caption,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    return (
        Tool(
            name="telegram_send_file",
            description=(
                "Send a local file to an authorized Telegram user. Supports images, "
                "PDFs, audio, video, archives, and other Telegram bot uploads. Use only "
                "after the user explicitly asks or confirms. If multiple Telegram users "
                "are configured, provide the intended recipient_user_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "caption": {"type": "string"},
                    "recipient_user_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["file_path", "confirmed"],
                "additionalProperties": False,
            },
            run=telegram_send_file,
        ),
        Tool(
            name="telegram_send_text",
            description=(
                "Send a plain text message to an authorized Telegram user. Use only "
                "after the user explicitly asks or confirms. If multiple Telegram users "
                "are configured, provide the intended recipient_user_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "recipient_user_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["text", "confirmed"],
                "additionalProperties": False,
            },
            run=telegram_send_text,
        ),
        Tool(
            name="telegram_send_image",
            description=(
                "Send a local PNG, JPEG, WebP, or GIF image to an authorized Telegram "
                "user. Prefer telegram_send_file for general files. Use only after the "
                "user explicitly asks or confirms. If multiple Telegram users are "
                "configured, provide the intended recipient_user_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "caption": {"type": "string"},
                    "recipient_user_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["image_path", "confirmed"],
                "additionalProperties": False,
            },
            run=telegram_send_image,
        ),
    )
