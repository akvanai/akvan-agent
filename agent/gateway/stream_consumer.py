"""Bridge sync agent stream deltas to async platform message delivery."""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import time
from typing import Any

from agent.gateway.types import (
    SendResult,
    StreamConsumerConfig,
    truncate_utf16,
    utf16_len,
)

logger = logging.getLogger(__name__)

_DONE = object()
_NO_EDIT_MESSAGE_ID = "__no_edit__"

_THINK_TAG_RE = re.compile(
    r"</?(?:thinking|thought|reasoning|redacted_thinking)>",
    re.IGNORECASE,
)


def _strip_think_tags(text: str) -> str:
    return _THINK_TAG_RE.sub("", text)


class StreamConsumer:
    """Progressively deliver one platform message as streamed text arrives."""

    _draft_id_counter = 0

    def __init__(
        self,
        adapter: Any,
        chat_id: str,
        *,
        config: StreamConsumerConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.chat_id = chat_id
        self.cfg = config or StreamConsumerConfig()
        self._queue: queue.Queue = queue.Queue()
        self._accumulated = ""
        self._message_id: str | None = None
        self._last_edit_time = 0.0
        self._last_sent_text = ""
        self._edit_supported = True
        self._finished = False
        self._use_draft_streaming = False
        self._draft_id: int | None = None
        self._finalized = False

    def on_delta(self, text: str) -> None:
        if not text or self._finished:
            return
        self._queue.put(text)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._queue.put(_DONE)

    async def run(self) -> str:
        """Consume queued deltas and return the final visible text."""
        self._use_draft_streaming = self._resolve_draft_streaming()
        if self._use_draft_streaming:
            StreamConsumer._draft_id_counter = (
                StreamConsumer._draft_id_counter + 1
            ) % 0x7FFFFFFF or 1
            self._draft_id = StreamConsumer._draft_id_counter

        while True:
            try:
                item = await asyncio.to_thread(self._queue.get, True, 0.1)
            except queue.Empty:
                if self._finished and self._queue.empty():
                    break
                continue
            if item is _DONE:
                break
            cleaned = _strip_think_tags(str(item))
            if cleaned:
                self._accumulated += cleaned
            await self._maybe_update(force=False)

        await self._maybe_update(force=True)
        return self._accumulated

    def _effective_max_length(self) -> int:
        limit = self.adapter.capabilities.max_message_length
        if isinstance(limit, int) and limit > 0:
            return limit
        return self.cfg.max_message_length

    def _update_interval(self) -> float:
        if self._use_draft_streaming and self.cfg.draft_update_interval is not None:
            return self.cfg.draft_update_interval
        return self.cfg.edit_interval

    def _visible_text(self, *, final: bool) -> str:
        text = truncate_utf16(self._accumulated, self._effective_max_length())
        if self._use_draft_streaming or final:
            return text
        if self.cfg.cursor:
            candidate = text + self.cfg.cursor
            if utf16_len(candidate) <= self._effective_max_length():
                return candidate
        return text

    def _resolve_draft_streaming(self) -> bool:
        transport = (self.cfg.transport or "edit").lower()
        if transport == "edit":
            return False
        supported = self.adapter.capabilities.draft_streaming
        if not supported and transport == "draft":
            logger.debug(
                "Draft streaming requested but unsupported for chat %s",
                self.chat_id,
            )
        return supported

    async def _send_draft_frame(self, text: str) -> bool:
        if self._draft_id is None:
            self._use_draft_streaming = False
            return False
        try:
            result = await self.adapter.send_draft(
                self.chat_id,
                self._draft_id,
                text,
            )
        except Exception as exc:
            logger.debug("send_draft raised, disabling draft transport: %s", exc)
            self._use_draft_streaming = False
            return False
        if not result.success:
            logger.debug(
                "send_draft failed, disabling draft transport: %s",
                result.error,
            )
            self._use_draft_streaming = False
            return False
        return True

    def _has_stream_anchor(self) -> bool:
        return self._message_id is not None

    def _can_edit_stream(self) -> bool:
        return (
            self._message_id is not None
            and self._message_id != _NO_EDIT_MESSAGE_ID
            and self._edit_supported
            and self.adapter.capabilities.message_editing
        )

    def _note_send_result(
        self,
        result: SendResult,
        visible: str,
        *,
        finalized: bool = False,
    ) -> bool:
        if not result.success:
            return False
        self._message_id = result.message_id or _NO_EDIT_MESSAGE_ID
        self._last_sent_text = visible
        if finalized:
            self._finalized = True
        self._last_edit_time = time.monotonic()
        return True

    async def _finalize_message(self, text: str) -> None:
        if self._finalized or not text.strip():
            return
        result = await self.adapter.send_final(self.chat_id, text)
        if self._note_send_result(result, text, finalized=True):
            return
        logger.warning("Final stream send failed: %s", result.error)

    async def _maybe_update(self, *, force: bool) -> None:
        if not self._accumulated:
            return
        visible = self._visible_text(final=force)
        if not force:
            now = time.monotonic()
            if visible == self._last_sent_text:
                return
            if now - self._last_edit_time < self._update_interval():
                return

        if force:
            if self._use_draft_streaming and not self._has_stream_anchor():
                await self._finalize_message(visible)
                self._last_edit_time = time.monotonic()
                return
            if not self._has_stream_anchor():
                result = await self.adapter.send(self.chat_id, visible)
                if not self._note_send_result(result, visible, finalized=True):
                    logger.warning("Initial stream send failed: %s", result.error)
                return
            if not self._can_edit_stream():
                if self._message_id == _NO_EDIT_MESSAGE_ID:
                    self._finalized = True
                    return
                if visible != self._last_sent_text and not self._finalized:
                    await self._finalize_message(visible)
                return
            if visible == self._last_sent_text:
                return
            result = await self.adapter.edit_message(
                self.chat_id,
                self._message_id,
                visible,
                finalize=True,
            )
            if result.success:
                self._last_sent_text = visible
                self._finalized = True
                self._last_edit_time = time.monotonic()
                return
            error = (result.error or "").lower()
            if result.retry_after or "flood" in error or "retry" in error:
                self._edit_supported = False
                tail = self._accumulated[len(self._last_sent_text) :]
                if tail.strip():
                    await self.adapter.send(self.chat_id, tail)
                return
            logger.warning("Stream edit failed: %s", result.error)
            self._edit_supported = False
            return

        if (
            self._use_draft_streaming
            and not self._has_stream_anchor()
        ):
            if visible == self._last_sent_text:
                return
            if await self._send_draft_frame(visible):
                self._last_sent_text = visible
                self._last_edit_time = time.monotonic()
            return

        if not self._has_stream_anchor():
            result = await self.adapter.send(self.chat_id, visible)
            if not self._note_send_result(result, visible):
                logger.warning("Initial stream send failed: %s", result.error)
            return

        if not self._can_edit_stream():
            return
        if visible == self._last_sent_text:
            return

        result = await self.adapter.edit_message(
            self.chat_id,
            self._message_id,
            visible,
        )
        if result.success:
            self._last_sent_text = visible
            self._last_edit_time = time.monotonic()
            return

        error = (result.error or "").lower()
        if result.retry_after or "flood" in error or "retry" in error:
            self._edit_supported = False
            tail = self._accumulated[len(self._last_sent_text) :]
            if tail.strip():
                await self.adapter.send(self.chat_id, tail)
            return

        logger.warning("Stream edit failed: %s", result.error)
        self._edit_supported = False
