"""
Implements model discovery and chat requests through OpenRouter.
Builds authenticated completion requests and parses streamed event data.
Turns HTTP failures and malformed responses into provider errors.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence

import httpx

from agent.messages import Completion, Message
from agent.providers.base import (
    ModelInfo,
    Provider,
    ProviderError,
    ProviderStreamEvent,
)

class OpenRouterProvider(Provider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        site_url: str | None = None,
        app_name: str = "Akvan Agent",
    ) -> None:
        if not api_key.strip():
            raise ProviderError("OpenRouter API key is required.")

        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._site_url = site_url
        self._app_name = app_name

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get(
                f"{self.base_url}/models",
                params={"output_modalities": "text", "sort": "most-popular"},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenRouter model lookup failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter model lookup failed: {exc}") from exc

        try:
            items = response.json()["data"]
            models = [
                ModelInfo(
                    id=item["id"],
                    name=item.get("name") or item["id"],
                    context_length=item.get("context_length"),
                )
                for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "OpenRouter returned an unexpected model-list response."
            ) from exc

        if not models:
            raise ProviderError("OpenRouter returned an empty model list.")
        return models

    def complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
        }
        if options:
            payload.update(options)

        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenRouter request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from exc

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            role = message["role"]
            content = message.get("content")
            tool_calls = message.get("tool_calls")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("OpenRouter returned an unexpected response shape.") from exc

        if role != "assistant" or not (
            isinstance(content, str) or isinstance(tool_calls, list)
        ):
            raise ProviderError("OpenRouter returned a malformed assistant message.")

        return Completion(message=dict(message), raw=data)

    def stream_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "stream": True,
        }
        if options:
            payload.update(options)

        yielded_any = False
        try:
            with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = _parse_stream_line(line)
                    if event is not None:
                        yielded_any = True
                        yield event
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenRouter request failed: {detail}") from exc
        except httpx.StreamError as exc:
            if not yielded_any:
                yield from self._complete_to_events(messages, model, options)
                return
            raise ProviderError(
                f"OpenRouter stream failed after partial output: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from exc

    def stream_complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[str]:
        for event in self.stream_events(messages, model, options):
            if event.content is not None:
                yield event.content

    def _complete_to_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        completion = self.complete(messages, model, options)
        message = completion.message
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        if content is not None and not isinstance(content, str):
            raise ProviderError("OpenRouter returned malformed assistant content.")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ProviderError("OpenRouter returned malformed tool calls.")
        raw = completion.raw or {}
        yield ProviderStreamEvent(
            content=content,
            tool_calls=tuple(tool_calls or ()),
            cost_usd=_openrouter_cost(raw.get("usage")),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name
        return headers


def _parse_stream_line(line: str) -> ProviderStreamEvent | None:
    if not line.startswith("data:"):
        return None

    data_text = line.removeprefix("data:").strip()
    if not data_text or data_text == "[DONE]":
        return None

    try:
        data = json.loads(data_text)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "OpenRouter returned malformed streaming JSON."
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError(
            "OpenRouter returned an unexpected streaming response shape."
        )

    cost_usd = _openrouter_cost(data.get("usage"))
    choices = data.get("choices")
    if not isinstance(choices, list):
        raise ProviderError(
            "OpenRouter returned an unexpected streaming response shape."
        )
    if not choices:
        return (
            ProviderStreamEvent(cost_usd=cost_usd)
            if cost_usd is not None
            else None
        )

    try:
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        tool_calls = delta.get("tool_calls")
    except (AttributeError, TypeError) as exc:
        raise ProviderError(
            "OpenRouter returned an unexpected streaming response shape."
        ) from exc

    if content is not None and not isinstance(content, str):
        raise ProviderError("OpenRouter returned malformed streaming content.")
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise ProviderError("OpenRouter returned malformed streaming tool calls.")
    if tool_calls and not all(isinstance(call, dict) for call in tool_calls):
        raise ProviderError("OpenRouter returned malformed streaming tool calls.")
    if content is None and not tool_calls and cost_usd is None:
        return None
    return ProviderStreamEvent(
        content=content,
        tool_calls=tuple(tool_calls or ()),
        cost_usd=cost_usd,
    )


def _openrouter_cost(usage: object) -> float | None:
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        return None
    return float(cost)


def _response_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text}"

    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return f"HTTP {response.status_code}: {message}"
        message = data.get("message")
        if isinstance(message, str):
            return f"HTTP {response.status_code}: {message}"

    return f"HTTP {response.status_code}: {data}"
