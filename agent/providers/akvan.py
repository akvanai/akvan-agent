"""
Implements model discovery and chat requests through the Akvan backend proxy.
Uses API-key auth and Akvan credit billing instead of a direct provider key.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

import httpx

from agent.messages import Completion, Message
from agent.providers.base import (
    ModelInfo,
    Provider,
    ProviderError,
    ProviderStreamEvent,
)
from agent.providers.openrouter import (
    _openrouter_cost,
    _parse_stream_line,
    _response_error_detail,
)


class AkvanProvider(Provider):
    name = "akvan"

    def __init__(
        self,
        api_key: str,
        *,
        backend_url: str,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        if not api_key.strip():
            raise ProviderError("Akvan API key is required.")
        if not backend_url.strip():
            raise ProviderError("Akvan backend URL is required.")

        self._api_key = api_key.strip()
        self._backend_url = backend_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _billing_error(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return _response_error_detail(response)
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str) and message.strip():
                return message
            detail = data.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail
        return _response_error_detail(response)

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get(
                f"{self._backend_url}/api/agent/v1/models/",
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            raise ProviderError(
                f"Akvan model lookup failed: {self._billing_error(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Akvan model lookup failed: {exc}") from exc

        try:
            items = response.json()["data"]
            models = [
                ModelInfo(
                    id=item["slug"],
                    name=item.get("display_name") or item["slug"],
                    context_length=item.get("context_window"),
                )
                for item in items
                if isinstance(item, dict) and isinstance(item.get("slug"), str)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "Akvan returned an unexpected model-list response."
            ) from exc

        if not models:
            raise ProviderError("Akvan returned an empty model list.")
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
                f"{self._backend_url}/api/agent/v1/chat/completions/",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            raise ProviderError(
                f"Akvan request failed: {self._billing_error(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Akvan request failed: {exc}") from exc

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            role = message["role"]
            content = message.get("content")
            tool_calls = message.get("tool_calls")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("Akvan returned an unexpected response shape.") from exc

        if role != "assistant" or not (
            isinstance(content, str) or isinstance(tool_calls, list)
        ):
            raise ProviderError("Akvan returned a malformed assistant message.")

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
                f"{self._backend_url}/api/agent/v1/chat/completions/",
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
            raise ProviderError(
                f"Akvan request failed: {self._billing_error(exc.response)}"
            ) from exc
        except httpx.StreamError as exc:
            if not yielded_any:
                yield from self._complete_to_events(messages, model, options)
                return
            raise ProviderError(
                f"Akvan stream failed after partial output: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Akvan request failed: {exc}") from exc

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
            raise ProviderError("Akvan returned malformed assistant content.")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ProviderError("Akvan returned malformed tool calls.")
        raw = completion.raw or {}
        yield ProviderStreamEvent(
            content=content,
            tool_calls=tuple(tool_calls or ()),
            cost_usd=_openrouter_cost(raw.get("usage")),
        )
