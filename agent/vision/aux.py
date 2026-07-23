"""Auxiliary vision LLM that turns images into text descriptions."""

from __future__ import annotations

import logging

from agent.config import Settings, load_setup_settings
from agent.providers import build_provider
from agent.providers.base import Provider
from agent.vision.encode import image_url_part, load_image_as_data_url

logger = logging.getLogger(__name__)

DEFAULT_AUX_VISION_MODEL = "openai/gpt-4o-mini"


def _resolve_aux(settings: Settings | None = None) -> tuple[Provider | None, str]:
    cfg = settings or load_setup_settings()
    model = (cfg.aux_vision_model or "").strip() or DEFAULT_AUX_VISION_MODEL
    try:
        provider = build_provider(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build provider for aux vision: %s", exc)
        return None, model
    return provider, model


def describe_image(
    source: str,
    *,
    question: str = "",
    settings: Settings | None = None,
    provider: Provider | None = None,
    model: str | None = None,
) -> str:
    """Describe a single image using a vision-capable auxiliary model."""

    owned_provider = False
    resolved_provider = provider
    resolved_model = model
    if resolved_provider is None or not resolved_model:
        built, built_model = _resolve_aux(settings)
        if resolved_provider is None:
            resolved_provider = built
            owned_provider = built is not None
        if not resolved_model:
            resolved_model = built_model
    if resolved_provider is None or not resolved_model:
        return f"[Vision unavailable: could not describe image at {source}]"

    try:
        data_url = load_image_as_data_url(source)
    except Exception as exc:  # noqa: BLE001
        return f"[Vision unavailable: failed to load image ({exc})]"

    prompt = (
        "Fully describe what you see in this image. "
        "Include text, UI elements, layout, and any notable details."
    )
    if question.strip():
        prompt += f"\n\nThen answer this question specifically:\n{question.strip()}"

    try:
        completion = resolved_provider.complete(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        image_url_part(data_url),
                    ],
                }
            ],
            model=resolved_model,
            options={"max_tokens": 2000, "temperature": 0.1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Aux vision call failed: %s", exc)
        return f"[Vision unavailable: aux model error ({exc})]"
    finally:
        if owned_provider and resolved_provider is not None:
            resolved_provider.close()

    content = completion.message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return "[Vision analysis returned no content.]"
