"""Interactive tool-approval flows for gateway chats."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass, field
from typing import Callable

from agent.gateway.delivery import DeliveryService
from agent.gateway.types import CallbackInteraction, InlineButton
from agent.tools.approval import ApprovalChoice, ApprovalRequest


@dataclass
class _ApprovalPending:
    event: threading.Event = field(default_factory=threading.Event)
    result: ApprovalChoice | None = None
    request: ApprovalRequest | None = None
    message_id: str | None = None


def parse_approval_choice(text: str) -> ApprovalChoice | None:
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


class ApprovalFlowService:
    """Manages pending approval prompts and user responses."""

    def __init__(self, *, delivery: DeliveryService) -> None:
        self.delivery = delivery
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, _ApprovalPending] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def is_pending(self, chat_id: str) -> bool:
        return chat_id in self._pending

    async def handle_message(self, chat_id: str, text: str) -> bool:
        """Resolve a text reply while approval is pending. Returns True if handled."""
        choice = parse_approval_choice(text)
        pending = self._pending.get(chat_id)
        if (
            choice is None
            or pending is None
            or pending.request is None
            or choice not in pending.request.choices
        ):
            await self.delivery.send(
                chat_id,
                "Waiting for approval — use the buttons in the approval message.",
            )
            return True
        pending.result = choice
        pending.event.set()
        return True

    async def handle_callback(self, callback: CallbackInteraction) -> bool:
        """Handle an approval:* inline button. Returns True if handled."""
        if not callback.data.startswith("approval:"):
            return False
        chat_id = callback.chat_id
        parts = callback.data.split(":", 2)
        pending = self._pending.get(chat_id)
        if len(parts) != 3 or pending is None or pending.request is None:
            await self.delivery.answer_callback(
                callback.callback_id, "This approval has expired.", alert=True,
            )
            return True
        request_id, raw_choice = parts[1], parts[2]
        try:
            choice = ApprovalChoice(raw_choice)
        except ValueError:
            choice = None
        if (
            request_id != pending.request.request_id
            or choice not in pending.request.choices
            or callback.message_id != pending.message_id
        ):
            await self.delivery.answer_callback(
                callback.callback_id, "This approval has expired.", alert=True,
            )
            return True
        pending.result = choice
        pending.event.set()
        labels = {
            ApprovalChoice.ONCE: "✅ Approved once",
            ApprovalChoice.SESSION: "✅ Approved for this session",
            ApprovalChoice.ALWAYS: "✅ Always approved",
            ApprovalChoice.DENY: "❌ Denied",
        }
        await self.delivery.edit_with_buttons(
            chat_id, callback.message_id, labels[choice],
        )
        await self.delivery.answer_callback(callback.callback_id)
        return True

    async def deny_pending(self, chat_id: str) -> None:
        pending = self._pending.get(chat_id)
        if pending is None:
            return
        pending.result = ApprovalChoice.DENY
        pending.event.set()
        if (
            pending.message_id is not None
            and self.delivery.adapter.capabilities.message_editing
        ):
            await self.delivery.edit_with_buttons(
                chat_id, pending.message_id, "❌ Denied",
            )

    def callback_for(
        self, chat_id: str,
    ) -> Callable[[ApprovalRequest, int], ApprovalChoice | str]:
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
                (
                    InlineButton(
                        labels.get(choice, choice.value),
                        f"approval:{request.request_id}:{choice.value}",
                    ),
                )
                for choice in request.choices
            )

            pending = _ApprovalPending(request=request)
            self._pending[chat_id] = pending
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.delivery.send_with_buttons(chat_id, prompt, buttons),
                    loop,
                )
                result = future.result(timeout=30)
                pending.message_id = result.message_id
                if not result.success:
                    raise TimeoutError("approval prompt failed")
                if not pending.event.wait(timeout):
                    if pending.message_id is not None:
                        expired = asyncio.run_coroutine_threadsafe(
                            self.delivery.edit_with_buttons(
                                chat_id, pending.message_id, "⌛ Timed out — denied",
                            ),
                            loop,
                        )
                        with contextlib.suppress(Exception):
                            expired.result(timeout=10)
                    raise TimeoutError("approval timed out")
                if pending.result is None:
                    raise TimeoutError("approval timed out")
                return pending.result
            finally:
                self._pending.pop(chat_id, None)

        return callback
