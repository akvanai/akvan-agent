"""
Defines the contract implemented by every model provider.
Describes model information, normal completions, and streaming completions.
Lets new providers integrate without changing the core agent loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from agent.messages import Completion, Message


@dataclass(frozen=True)
class ProviderStreamEvent:
    """One safe structured delta from a streaming provider response."""

    content: str | None = None
    tool_calls: tuple[dict[str, object], ...] = ()
    cost_usd: float | None = None
    reasoning_content: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context_length: int | None = None


class ProviderError(RuntimeError):
    """Raised when a model provider cannot complete a request."""


class Provider(ABC):
    """Base interface for model providers."""

    name: str

    @abstractmethod
    def complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        """Return the next assistant completion for a conversation."""

    def list_models(self) -> list[ModelInfo]:
        """Return models available for this provider, when supported."""
        return []

    def close(self) -> None:
        """Release provider-owned resources."""
        return None

    def needs_reasoning_content_pad(self, model: str) -> bool:
        """Return True when assistant tool-call turns must include reasoning_content."""
        return False

    def supports_vision(self, model: str) -> bool:
        """Return True when ``model`` can consume image content parts."""
        from agent.vision.capabilities import model_looks_vision_capable

        return model_looks_vision_capable(model)

    def stream_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """Yield structured text/tool deltas while preserving compatibility."""
        if type(self).stream_complete is not Provider.stream_complete:
            for content in self.stream_complete(messages, model, options):
                yield ProviderStreamEvent(content=content)
            return

        message = self.complete(messages=messages, model=model, options=options).message
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        reasoning_content = message.get("reasoning_content")
        if content is not None and not isinstance(content, str):
            raise ProviderError("Provider returned malformed assistant content.")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ProviderError("Provider returned malformed tool calls.")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            raise ProviderError("Provider returned malformed reasoning content.")
        yield ProviderStreamEvent(
            content=content if isinstance(content, str) else None,
            tool_calls=tuple(tool_calls or ()),
            reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
        )

    def stream_complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[str]:
        """Yield assistant text chunks for compatibility with existing providers."""
        message = self.complete(messages=messages, model=model, options=options).message
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderError("Provider returned an assistant message without text content.")
        yield content
