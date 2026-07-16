"""Stream consumer draft transport tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent.gateway.contracts import GatewayCapabilities
from agent.gateway.stream_consumer import StreamConsumer
from agent.gateway.types import SendResult, StreamConsumerConfig


@dataclass
class DraftMockAdapter:
    drafts: list[tuple[int, str]] = field(default_factory=list)
    sends: list[str] = field(default_factory=list)
    rich_sends: list[str] = field(default_factory=list)
    edits: list[tuple[str, str, bool]] = field(default_factory=list)
    message_id: str = "100"
    supports_drafts: bool = True
    rich_eligible: bool = False

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            message_editing=True,
            draft_streaming=self.supports_drafts,
        )

    async def send_draft(self, chat_id: str, draft_id: int, text: str) -> SendResult:
        self.drafts.append((draft_id, text))
        return SendResult(success=True)

    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> SendResult:
        self.sends.append(text)
        return SendResult(success=True, message_id=self.message_id)

    async def send_rich(
        self, chat_id: str, text: str, *, reply_to: str | None = None
    ) -> SendResult:
        self.rich_sends.append(text)
        return SendResult(success=True, message_id=self.message_id)

    async def send_final(
        self, chat_id: str, text: str, *, reply_to: str | None = None,
    ) -> SendResult:
        if self.rich_eligible:
            return await self.send_rich(chat_id, text, reply_to=reply_to)
        return await self.send(chat_id, text, reply_to=reply_to)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        self.edits.append((message_id, text, finalize))
        return SendResult(success=True, message_id=message_id)


def test_draft_transport_streams_then_finalizes_with_plain_send() -> None:
    adapter = DraftMockAdapter()

    async def run() -> str:
        consumer = StreamConsumer(
            adapter,
            "123",
            config=StreamConsumerConfig(
                transport="draft",
                edit_interval=0.0,
                cursor="",
            ),
        )
        task = asyncio.create_task(consumer.run())
        consumer.on_delta("Hello")
        consumer.on_delta(" world")
        consumer.finish()
        return await task

    final = asyncio.run(run())
    assert final == "Hello world"
    assert adapter.drafts
    assert adapter.drafts[0][1] == "Hello"
    assert adapter.drafts[-1][1] == "Hello world"
    assert adapter.sends == ["Hello world"]
    assert not adapter.edits


def test_draft_failure_falls_back_to_edit_path() -> None:
    adapter = DraftMockAdapter()

    async def failing_draft(chat_id: str, draft_id: int, text: str) -> SendResult:
        adapter.drafts.append((draft_id, text))
        return SendResult(success=False, error="draft_rejected")

    adapter.send_draft = failing_draft  # type: ignore[method-assign]

    async def run() -> None:
        consumer = StreamConsumer(
            adapter,
            "123",
            config=StreamConsumerConfig(
                transport="draft",
                edit_interval=0.0,
                cursor="",
            ),
        )
        task = asyncio.create_task(consumer.run())
        consumer.on_delta("Hello")
        consumer.finish()
        await task

    asyncio.run(run())
    assert adapter.sends == ["Hello"]
    assert not adapter.edits


def test_draft_finalize_uses_rich_send_when_eligible() -> None:
    adapter = DraftMockAdapter(rich_eligible=True)

    async def run() -> None:
        consumer = StreamConsumer(
            adapter,
            "123",
            config=StreamConsumerConfig(
                transport="draft",
                edit_interval=0.0,
                cursor="",
            ),
        )
        task = asyncio.create_task(consumer.run())
        consumer.on_delta("| A | B |\n| --- | --- |\n| 1 | 2 |")
        consumer.finish()
        await task

    asyncio.run(run())
    assert adapter.rich_sends
    assert not adapter.sends
