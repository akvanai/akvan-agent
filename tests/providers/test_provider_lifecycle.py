"""Verify Provider lifecycle cleanup contract."""

from __future__ import annotations

import httpx

from agent.messages import Completion
from agent.providers.base import Provider
from agent.providers.deepseek import DeepSeekProvider
from agent.providers.openrouter import OpenRouterProvider


class MinimalProvider(Provider):
    name = "minimal"

    def complete(self, messages, model, options=None):
        return Completion(message={"role": "assistant", "content": "ok"})


def test_provider_base_close_is_noop() -> None:
    provider = MinimalProvider()
    provider.close()


def test_deepseek_close_closes_owned_client() -> None:
    provider = DeepSeekProvider("test-key")
    client = provider._client

    provider.close()

    assert client.is_closed


def test_deepseek_close_does_not_close_injected_client() -> None:
    client = httpx.Client()
    provider = DeepSeekProvider("test-key", client=client)

    provider.close()

    assert not client.is_closed
    client.close()


def test_openrouter_close_closes_owned_client() -> None:
    provider = OpenRouterProvider("test-key")
    client = provider._client

    provider.close()

    assert client.is_closed


def test_openrouter_close_does_not_close_injected_client() -> None:
    client = httpx.Client()
    provider = OpenRouterProvider("test-key", client=client)

    provider.close()

    assert not client.is_closed
    client.close()
