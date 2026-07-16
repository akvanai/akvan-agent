"""Model-aware context budgets and rough request token accounting."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from agent.context.config import ContextConfig
from agent.messages import Message

CHARS_PER_TOKEN = 4
DEFAULT_CONTEXT_LENGTH = 128_000
IMAGE_TOKEN_ESTIMATE = 1_600


def resolve_context_length(model: str, configured: int | None = None) -> int:
    if configured:
        return configured
    normalized = model.lower()
    if "deepseek" in normalized:
        return 1_000_000
    if normalized.startswith("gpt-5") or "/gpt-5" in normalized:
        return 400_000
    if "gemini" in normalized and ("2.5" in normalized or "3" in normalized):
        return 1_000_000
    if "claude" in normalized:
        return 200_000
    return DEFAULT_CONTEXT_LENGTH


def estimate_value_tokens(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return math.ceil(len(value) / CHARS_PER_TOKEN)
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict) and item.get("type") in {
                "image",
                "image_url",
                "input_image",
            }:
                total += IMAGE_TOKEN_ESTIMATE
            else:
                total += estimate_value_tokens(item)
        return total
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = str(value)
    return math.ceil(len(encoded) / CHARS_PER_TOKEN)


def estimate_message_tokens(message: Message) -> int:
    total = 8 + estimate_value_tokens(message.get("content"))
    total += estimate_value_tokens(message.get("tool_calls"))
    total += estimate_value_tokens(message.get("reasoning_content"))
    return total


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_tool_schema_tokens(schemas: Sequence[Mapping[str, object]]) -> int:
    return estimate_value_tokens(list(schemas))


@dataclass(frozen=True)
class RequestBreakdown:
    messages: int
    tool_schemas: int
    reserved_output: int
    estimated_total: int
    context_length: int
    effective_input: int
    threshold: int

    @property
    def percentage(self) -> float:
        if self.context_length <= 0:
            return 0.0
        return min(100.0, self.estimated_total / self.context_length * 100)

    def as_dict(self) -> dict[str, int | float]:
        return {
            "messages": self.messages,
            "tool_schemas": self.tool_schemas,
            "reserved_output": self.reserved_output,
            "estimated_total": self.estimated_total,
            "context_length": self.context_length,
            "effective_input": self.effective_input,
            "threshold": self.threshold,
            "percentage": round(self.percentage, 1),
        }


@dataclass(frozen=True)
class ContextBudget:
    context_length: int
    reserved_output_tokens: int
    effective_input_tokens: int
    compression_threshold_tokens: int
    max_result_chars: int
    max_turn_chars: int
    preview_chars: int

    @classmethod
    def for_model(cls, model: str, config: ContextConfig) -> "ContextBudget":
        context_length = resolve_context_length(model, config.context_length)
        reserved = min(
            max(0, config.max_output_tokens),
            max(0, context_length // 2),
        )
        effective = max(1, context_length - reserved)
        threshold = max(1, int(effective * config.compression_threshold))
        window_chars = context_length * CHARS_PER_TOKEN
        result_cap = max(
            8_000,
            min(config.max_result_chars, int(window_chars * 0.15)),
        )
        turn_cap = max(
            16_000,
            min(config.max_turn_chars, int(window_chars * 0.30)),
        )
        return cls(
            context_length=context_length,
            reserved_output_tokens=reserved,
            effective_input_tokens=effective,
            compression_threshold_tokens=threshold,
            max_result_chars=result_cap,
            max_turn_chars=turn_cap,
            preview_chars=config.result_preview_chars,
        )

    def estimate(
        self,
        messages: Sequence[Message],
        schemas: Sequence[Mapping[str, object]],
    ) -> RequestBreakdown:
        message_tokens = estimate_messages_tokens(messages)
        schema_tokens = estimate_tool_schema_tokens(schemas)
        return RequestBreakdown(
            messages=message_tokens,
            tool_schemas=schema_tokens,
            reserved_output=self.reserved_output_tokens,
            estimated_total=message_tokens + schema_tokens,
            context_length=self.context_length,
            effective_input=self.effective_input_tokens,
            threshold=self.compression_threshold_tokens,
        )
