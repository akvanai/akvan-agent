"""
Verifies the OpenRouter integration without making real network requests.
Checks request URLs, headers, payloads, model discovery, and streaming events.
Covers malformed responses and useful HTTP error reporting.
"""

from __future__ import annotations

import httpx
import pytest

from agent.providers.base import ProviderError
from agent.providers.openrouter import OpenRouterProvider


def test_openrouter_request_construction() -> None:
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
    provider = OpenRouterProvider("test-key", client=client)

    completion = provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="openai/gpt-4o-mini",
    )

    assert completion.message == {"role": "assistant", "content": "done"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["X-Title"] == "Akvan Agent"
    assert request.read() == (
        b'{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}'
    )


def test_openrouter_malformed_response_raises_provider_error() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []}))
    )
    provider = OpenRouterProvider("test-key", client=client)

    with pytest.raises(ProviderError, match="unexpected response shape"):
        provider.complete(messages=[{"role": "user", "content": "hello"}], model="model")


def test_openrouter_http_error_includes_detail() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                401,
                json={"error": {"message": "bad key"}},
            )
        )
    )
    provider = OpenRouterProvider("test-key", client=client)

    with pytest.raises(ProviderError, match="bad key"):
        provider.complete(messages=[{"role": "user", "content": "hello"}], model="model")


def test_openrouter_stream_http_error_includes_detail() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"message": "tools unsupported"}},
            )
        )
    )
    provider = OpenRouterProvider("test-key", client=client)

    with pytest.raises(ProviderError, match="tools unsupported") as exc_info:
        list(
            provider.stream_complete(
                messages=[{"role": "user", "content": "hello"}],
                model="model",
            )
        )

    assert "StreamClosed" not in str(exc_info.value)
    assert "HTTP 400" in str(exc_info.value)


def test_openrouter_stream_closed_falls_back_to_complete() -> None:
    class ClosedStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            raise httpx.StreamClosed()

    class ClosedStreamClient:
        def stream(self, *args, **kwargs):
            return ClosedStreamResponse()

        def post(self, *args, **kwargs):
            request = httpx.Request("POST", args[0], json=kwargs.get("json"), headers=kwargs.get("headers"))
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "fallback",
                            }
                        }
                    ]
                },
            )

    provider = OpenRouterProvider("test-key", client=ClosedStreamClient())

    chunks = list(
        provider.stream_complete(
            messages=[{"role": "user", "content": "hello"}],
            model="model",
        )
    )

    assert chunks == ["fallback"]


def test_openrouter_stream_request_construction() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                b'data: [DONE]\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider("test-key", client=client)

    chunks = list(
        provider.stream_complete(
            messages=[{"role": "user", "content": "hello"}],
            model="openai/gpt-4o-mini",
        )
    )

    assert chunks == ["hel", "lo"]
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.read() == (
        b'{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'
    )


def test_openrouter_stream_reports_final_cost() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    b'data: {"choices":[],"usage":{"cost":0.001234}}\n\n'
                    b"data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    provider = OpenRouterProvider("test-key", client=client)

    events = list(provider.stream_events([{"role": "user", "content": "hi"}], "model"))

    assert events[0].content == "ok"
    assert events[1].cost_usd == pytest.approx(0.001234)


def test_openrouter_lists_models() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-test",
                        "name": "GPT Test",
                        "context_length": 128000,
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider("test-key", client=client)

    models = provider.list_models()

    assert models[0].id == "openai/gpt-test"
    assert models[0].name == "GPT Test"
    assert models[0].context_length == 128000
    request = requests[0]
    assert request.url.path == "/api/v1/models"
    assert request.url.params["sort"] == "most-popular"
    assert request.url.params["output_modalities"] == "text"
    assert request.headers["Authorization"] == "Bearer test-key"


def test_openrouter_accepts_tool_call_response() -> None:
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "testy", "arguments": "{\"value\":\"hello\"}"},
        }
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": tool_calls,
                            }
                        }
                    ]
                },
            )
        )
    )
    provider = OpenRouterProvider("test-key", client=client)

    completion = provider.complete(
        messages=[{"role": "user", "content": "use testy"}], model="model"
    )

    assert completion.message["tool_calls"] == tool_calls


def test_openrouter_streams_structured_tool_call_deltas() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"tool_calls":['
                    b'{"index":0,"id":"call_1","type":"function",'
                    b'"function":{"name":"testy","arguments":""}}]}}]}\n\n'
                    b'data: {"choices":[{"delta":{"tool_calls":['
                    b'{"index":0,"function":{"arguments":"{}"}}]}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    provider = OpenRouterProvider("test-key", client=client)

    events = list(
        provider.stream_events(
            messages=[{"role": "user", "content": "use tool"}],
            model="model",
            options={"tools": []},
        )
    )

    assert len(events) == 2
    assert events[0].tool_calls[0]["id"] == "call_1"
    assert events[1].tool_calls[0]["function"] == {"arguments": "{}"}
