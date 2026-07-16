"""Stream consumer tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent.gateway.contracts import GatewayCapabilities
from agent.gateway.stream_consumer import StreamConsumer
from agent.gateway.types import SendResult, StreamConsumerConfig, utf16_len


@dataclass
class MockAdapter:
    sends: list[str] = field(default_factory=list)
    edits: list[tuple[str, str, bool]] = field(default_factory=list)
    drafts: list[tuple[int, str]] = field(default_factory=list)
    rich_sends: list[str] = field(default_factory=list)
    message_id: str = "100"
    supports_drafts: bool = False

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            message_editing=True,
            draft_streaming=self.supports_drafts,
        )

    async def send_draft(self, chat_id: str, draft_id: int, text: str) -> SendResult:
        self.drafts.append((draft_id, text))
        return SendResult(success=True)

    async def send_rich(
        self, chat_id: str, text: str, *, reply_to: str | None = None
    ) -> SendResult:
        self.rich_sends.append(text)
        return SendResult(success=True, message_id=self.message_id)

    async def send_final(
        self, chat_id: str, text: str, *, reply_to: str | None = None,
    ) -> SendResult:
        return await self.send(chat_id, text, reply_to=reply_to)

    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> SendResult:
        self.sends.append(text)
        return SendResult(success=True, message_id=self.message_id)

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


def test_stream_consumer_sends_initial_and_final_edit() -> None:
    adapter = MockAdapter()
    consumer = StreamConsumer(
        adapter,
        "123",
        config=StreamConsumerConfig(edit_interval=0.0, cursor=""),
    )

    async def run() -> str:
        task = asyncio.create_task(consumer.run())
        consumer.on_delta("Hello")
        consumer.on_delta(" world")
        consumer.finish()
        return await task

    final = asyncio.run(run())
    assert final == "Hello world"
    assert adapter.sends
    assert adapter.edits
    assert adapter.edits[-1][1] == "Hello world"


def test_stream_consumer_truncates_utf16() -> None:
    adapter = MockAdapter()
    emoji = "😀" * 5000
    consumer = StreamConsumer(
        adapter,
        "123",
        config=StreamConsumerConfig(edit_interval=0.0, cursor="", max_message_length=100),
    )

    async def run() -> None:
        task = asyncio.create_task(consumer.run())
        consumer.on_delta(emoji)
        consumer.finish()
        await task

    asyncio.run(run())
    sent = adapter.sends[0]
    assert utf16_len(sent) <= 100


def test_stream_consumer_does_not_resend_when_message_id_missing() -> None:
    adapter = MockAdapter(message_id=None)

    async def run() -> None:
        consumer = StreamConsumer(
            adapter,
            "123",
            config=StreamConsumerConfig(
                transport="edit",
                edit_interval=0.0,
                cursor="",
            ),
        )
        task = asyncio.create_task(consumer.run())
        for chunk in ("Once", " upon", " a", " time"):
            consumer.on_delta(chunk)
        consumer.finish()
        await task

    asyncio.run(run())
    assert len(adapter.sends) == 1
    assert adapter.sends[0] == "Once"
