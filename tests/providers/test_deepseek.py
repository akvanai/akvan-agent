"""
Verifies the native DeepSeek integration without making real network requests.
Checks thinking-mode wire format, reasoning_content replay, and streaming.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.config import Settings
from agent.providers import DeepSeekProvider, build_provider
from agent.providers.base import ProviderError
from agent.providers.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    _build_thinking_extras,
    prepare_messages_for_api,
)


def test_deepseek_needs_reasoning_content_pad_for_thinking_models() -> None:
    provider = DeepSeekProvider("test-key", client=httpx.Client())

    assert provider.needs_reasoning_content_pad("deepseek-v4-pro") is True
    assert provider.needs_reasoning_content_pad("deepseek-chat") is False


def test_deepseek_request_construction() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "done",
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider("test-key", client=client)

    completion = provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="deepseek-chat",
    )

    assert completion.message == {"role": "assistant", "content": "done"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{DEFAULT_DEEPSEEK_BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    payload = json.loads(request.read())
    assert payload == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_deepseek_v4_thinking_wire_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "done",
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        "test-key",
        client=client,
        thinking_enabled=True,
        reasoning_effort="high",
    )

    provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="deepseek-v4-pro",
    )

    payload = json.loads(requests[0].read())
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
    assert payload["reasoning_effort"] == "high"


def test_deepseek_thinking_disabled_sends_disabled_marker() -> None:
    extras = _build_thinking_extras(
        "deepseek-v4-pro",
        thinking_enabled=False,
        reasoning_effort="high",
    )
    assert extras == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_deepseek_chat_leaves_thinking_extras_untouched() -> None:
    extras = _build_thinking_extras(
        "deepseek-chat",
        thinking_enabled=True,
        reasoning_effort="high",
    )
    assert extras == {}


def test_prepare_messages_pads_missing_reasoning_content() -> None:
    prepared = prepare_messages_for_api(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "testy", "arguments": "{}"},
                    }
                ],
            }
        ],
        "deepseek-v4-pro",
    )

    assert prepared[0]["reasoning_content"] == " "


def test_prepare_messages_upgrades_empty_reasoning_content() -> None:
    prepared = prepare_messages_for_api(
        [
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "testy", "arguments": "{}"},
                    }
                ],
            }
        ],
        "deepseek-reasoner",
    )

    assert prepared[0]["reasoning_content"] == " "


def test_prepare_messages_leaves_v3_history_untouched() -> None:
    original = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "testy", "arguments": "{}"},
                }
            ],
        }
    ]
    assert prepare_messages_for_api(original, "deepseek-chat") == original


def test_deepseek_streams_reasoning_content_deltas() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    provider = DeepSeekProvider("test-key", client=client)

    events = list(
        provider.stream_events(
            messages=[{"role": "user", "content": "hello"}],
            model="deepseek-v4-pro",
        )
    )

    assert events[0].reasoning_content == "think"
    assert events[1].content == "ok"


def test_deepseek_http_error_includes_detail() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"message": "reasoning_content must be passed back"}},
            )
        )
    )
    provider = DeepSeekProvider("test-key", client=client)

    with pytest.raises(ProviderError, match="reasoning_content must be passed back"):
        provider.complete(messages=[{"role": "user", "content": "hello"}], model="model")


def test_build_provider_returns_deepseek_provider() -> None:
    settings = Settings(
        provider="deepseek",
        model="deepseek-v4-pro",
        deepseek_api_key="test-key",
        deepseek_thinking="enabled",
        deepseek_reasoning_effort="medium",
    )

    provider = build_provider(settings)

    assert isinstance(provider, DeepSeekProvider)
    assert provider.base_url == DEFAULT_DEEPSEEK_BASE_URL


def test_build_provider_uses_custom_deepseek_base_url() -> None:
    settings = Settings(
        provider="deepseek",
        model="deepseek-chat",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.test/v1",
    )

    provider = build_provider(settings)

    assert provider.base_url == "https://example.test/v1"
