"""Decide whether images should be sent natively or described via aux vision."""

from __future__ import annotations

from typing import Literal

from agent.config import Settings

VisionMode = Literal["native", "aux", "off"]

# Fail-closed allowlist / heuristics for common vision-capable model slugs.
_VISION_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "claude-3",
    "claude-4",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "gemini",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "llava",
    "pixtral",
    "mistral-small",
    "mistral-medium",
    "mistral-large",
    "vision",
    "vl-",
    "-vl",
    "gpt-4-vision",
)


def model_looks_vision_capable(model: str) -> bool:
    """Return True when the model slug looks vision-capable."""

    slug = (model or "").strip().lower()
    if not slug:
        return False
    return any(marker in slug for marker in _VISION_MARKERS)


def decide_vision_mode(
    provider: str,
    model: str,
    settings: Settings | None = None,
    *,
    provider_supports_vision: bool | None = None,
) -> VisionMode:
    """Return ``native``, ``aux``, or ``off`` for the active model.

    ``auto`` (default) uses an explicit settings override when set, otherwise
    provider/model capability heuristics. Unknown models fail closed to ``aux``.
    """

    mode_cfg = "auto"
    override: bool | None = None
    if settings is not None:
        mode_cfg = (settings.vision_mode or "auto").strip().lower() or "auto"
        override = settings.model_supports_vision

    if mode_cfg == "off":
        return "off"
    if mode_cfg == "native":
        return "native"
    if mode_cfg == "aux":
        return "aux"

    # auto
    if override is True:
        return "native"
    if override is False:
        return "aux"

    if provider_supports_vision is True:
        return "native"
    if provider_supports_vision is False:
        return "aux"

    # DeepSeek chat endpoints are text-only today.
    if (provider or "").strip().lower() == "deepseek":
        return "aux"

    if model_looks_vision_capable(model):
        return "native"
    return "aux"
