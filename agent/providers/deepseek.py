"""
Implements model discovery and chat requests through the native DeepSeek API.
Builds thinking-mode wire format for V4/reasoner models and prepares message
replay so tool-call turns include required reasoning_content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from agent.messages import Completion, Message
from agent.providers.base import (
    ModelInfo,
    Provider,
    ProviderError,
    ProviderStreamEvent,
)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

DEFAULT_DEEPSEEK_MODELS = (
    ModelInfo(id="deepseek-v4-pro", name="DeepSeek V4 Pro", context_length=1_000_000),
    ModelInfo(id="deepseek-v4-flash", name="DeepSeek V4 Flash", context_length=1_000_000),
    ModelInfo(id="deepseek-chat", name="DeepSeek Chat (V3)", context_length=1_000_000),
    ModelInfo(id="deepseek-reasoner", name="DeepSeek Reasoner (R1)", context_length=1_000_000),
)


def model_supports_thinking(model: str | None) -> bool:
    """Return True for DeepSeek models that use thinking-mode wire format."""
    normalized = (model or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("deepseek-v") and not normalized.startswith("deepseek-v3"):
        return True
    return normalized == "deepseek-reasoner"


def needs_reasoning_content_pad(
    provider_name: str,
    model: str,
    base_url: str = "",
) -> bool:
    """Return True when assistant tool-call turns need reasoning_content."""
    if not model_supports_thinking(model):
        return False
    provider = (provider_name or "").lower()
    model_name = (model or "").lower()
    if provider == "deepseek" or "deepseek" in model_name:
        return True
    host = urlparse(base_url).hostname or ""
    return host == "api.deepseek.com"


def _build_thinking_extras(
    model: str,
    *,
    thinking_enabled: bool,
    reasoning_effort: str | None,
) -> dict[str, object]:
    if not model_supports_thinking(model):
        return {}

    extras: dict[str, object] = {
        "extra_body": {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
    }
    if not thinking_enabled:
        return extras

    effort = (reasoning_effort or "").strip().lower()
    if effort in {"xhigh", "max"}:
        extras["reasoning_effort"] = "max"
    elif effort in {"low", "medium", "high"}:
        extras["reasoning_effort"] = effort
    return extras


def prepare_messages_for_api(
    messages: Sequence[Message],
    model: str,
) -> list[Message]:
    """Ensure thinking-mode tool-call turns include reasoning_content on replay."""
    if not model_supports_thinking(model):
        return list(messages)

    prepared: list[Message] = []
    for message in messages:
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            prepared.append(message)
            continue
        copy = dict(message)
        existing = copy.get("reasoning_content")
        if not isinstance(existing, str) or existing == "":
            copy["reasoning_content"] = " "
        prepared.append(copy)
    return prepared


class DeepSeekProvider(Provider):
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        thinking_enabled: bool = True,
        reasoning_effort: str | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderError("DeepSeek API key is required.")

        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"DeepSeek model lookup failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"DeepSeek model lookup failed: {exc}") from exc

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
                "DeepSeek returned an unexpected model-list response."
            ) from exc

        if not models:
            raise ProviderError("DeepSeek returned an empty model list.")
        return models

    def complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        payload = self._build_payload(messages, model, options)

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
            raise ProviderError(f"DeepSeek request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"DeepSeek request failed: {exc}") from exc

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            role = message["role"]
            content = message.get("content")
            tool_calls = message.get("tool_calls")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("DeepSeek returned an unexpected response shape.") from exc

        if role != "assistant" or not (
            isinstance(content, str) or content is None or isinstance(tool_calls, list)
        ):
            raise ProviderError("DeepSeek returned a malformed assistant message.")

        return Completion(message=dict(message), raw=data)

    def stream_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        payload = self._build_payload(messages, model, options, stream=True)

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
            raise ProviderError(f"DeepSeek request failed: {detail}") from exc
        except httpx.StreamError as exc:
            if not yielded_any:
                yield from self._complete_to_events(messages, model, options)
                return
            raise ProviderError(
                f"DeepSeek stream failed after partial output: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"DeepSeek request failed: {exc}") from exc

    def stream_complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[str]:
        for event in self.stream_events(messages, model, options):
            if event.content is not None:
                yield event.content

    def _build_payload(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None,
        *,
        stream: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model,
            "messages": prepare_messages_for_api(messages, model),
        }
        if stream:
            payload["stream"] = True
        payload.update(
            _build_thinking_extras(
                model,
                thinking_enabled=self._thinking_enabled,
                reasoning_effort=self._reasoning_effort,
            )
        )
        if options:
            payload.update(options)
        return payload

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
        reasoning_content = message.get("reasoning_content")
        if content is not None and not isinstance(content, str):
            raise ProviderError("DeepSeek returned malformed assistant content.")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ProviderError("DeepSeek returned malformed tool calls.")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            raise ProviderError("DeepSeek returned malformed reasoning content.")
        yield ProviderStreamEvent(
            content=content if isinstance(content, str) else None,
            tool_calls=tuple(tool_calls or ()),
            reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


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
            "DeepSeek returned malformed streaming JSON."
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError(
            "DeepSeek returned an unexpected streaming response shape."
        )

    choices = data.get("choices")
    if not isinstance(choices, list):
        raise ProviderError(
            "DeepSeek returned an unexpected streaming response shape."
        )
    if not choices:
        return None

    try:
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        tool_calls = delta.get("tool_calls")
        reasoning_content = delta.get("reasoning_content")
    except (AttributeError, TypeError) as exc:
        raise ProviderError(
            "DeepSeek returned an unexpected streaming response shape."
        ) from exc

    if content is not None and not isinstance(content, str):
        raise ProviderError("DeepSeek returned malformed streaming content.")
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise ProviderError("DeepSeek returned malformed streaming tool calls.")
    if tool_calls and not all(isinstance(call, dict) for call in tool_calls):
        raise ProviderError("DeepSeek returned malformed streaming tool calls.")
    if reasoning_content is not None and not isinstance(reasoning_content, str):
        raise ProviderError("DeepSeek returned malformed streaming reasoning content.")
    if content is None and not tool_calls and reasoning_content is None:
        return None
    return ProviderStreamEvent(
        content=content,
        tool_calls=tuple(tool_calls or ()),
        reasoning_content=reasoning_content,
    )


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
