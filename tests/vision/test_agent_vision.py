"""Agent loop multimodal tool-result wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.agent import AgentLoop
from agent.messages import Completion, Message
from agent.providers.base import Provider, ProviderStreamEvent
from agent.tools.base import Tool, ToolImage, ToolResult
from agent.vision.encode import write_png_bytes


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class SequencedProvider(Provider):
    name = "openrouter"

    def __init__(self, events: list[list[ProviderStreamEvent]]) -> None:
        self._events = events
        self.calls = 0
        self.last_messages: list[Message] = []

    def complete(self, messages, model, options=None):
        raise AssertionError("complete should not be used")

    def supports_vision(self, model: str) -> bool:
        return True

    def stream_events(self, messages, model, options=None):
        self.calls += 1
        self.last_messages = list(messages)
        for event in self._events[self.calls - 1]:
            yield event


def test_agent_loop_attaches_images_to_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AKVAN_VISION_MODE", "native")
    path = write_png_bytes(TINY_PNG, prefix="loop")

    def run_tool() -> ToolResult:
        return ToolResult(
            '{"ok": true}',
            images=(ToolImage(path=str(path), question="see?"),),
        )

    tool = Tool(
        name="visiony",
        description="returns an image",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        run=run_tool,
    )
    provider = SequencedProvider(
        [
            [
                ProviderStreamEvent(
                    tool_calls=(
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "visiony", "arguments": "{}"},
                        },
                    )
                )
            ],
            [ProviderStreamEvent(content="saw it")],
        ]
    )
    loop = AgentLoop(provider=provider, model="openai/gpt-4o", tools=(tool,))
    messages: list[Message] = []
    answer = loop.run_turn(messages, "look")
    assert answer == "saw it"
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    content = tool_msg["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    # Second provider call should receive the multimodal tool message.
    assert any(
        isinstance(m.get("content"), list) and m.get("role") == "tool"
        for m in provider.last_messages
    )
