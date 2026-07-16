"""Verifies the Akvan backend proxy provider without real network requests."""

from __future__ import annotations

import httpx
import pytest

from agent.providers.akvan import AkvanProvider
from agent.providers.base import ProviderError


def test_akvan_request_construction() -> None:
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
    provider = AkvanProvider(
        "akv_test_key",
        backend_url="https://api.akvan.test",
        client=client,
    )

    completion = provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="openai/gpt-4o-mini",
    )

    assert completion.message == {"role": "assistant", "content": "done"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.akvan.test/api/agent/v1/chat/completions/"
    assert request.headers["Authorization"] == "Bearer akv_test_key"


def test_akvan_billing_error_includes_message() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                402,
                json={
                    "detail": "insufficient_credit",
                    "message": "Top up at https://akvan.app/dashboard/credits",
                },
            )
        )
    )
    provider = AkvanProvider(
        "akv_test_key",
        backend_url="https://api.akvan.test",
        client=client,
    )

    with pytest.raises(ProviderError, match="Top up at"):
        provider.complete(messages=[{"role": "user", "content": "hello"}], model="model")


def test_akvan_list_models_maps_slug() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "slug": "openai/gpt-4o-mini",
                            "display_name": "GPT-4o Mini",
                            "context_window": 128000,
                        }
                    ]
                },
            )
        )
    )
    provider = AkvanProvider(
        "akv_test_key",
        backend_url="https://api.akvan.test",
        client=client,
    )

    models = provider.list_models()
    assert len(models) == 1
    assert models[0].id == "openai/gpt-4o-mini"
    assert models[0].name == "GPT-4o Mini"
