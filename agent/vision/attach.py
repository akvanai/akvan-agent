"""Attach images to user/tool messages as native parts or aux text."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent.config import Settings, load_setup_settings
from agent.vision.aux import describe_image
from agent.vision.capabilities import VisionMode, decide_vision_mode
from agent.vision.encode import image_url_part, load_image_as_data_url

if TYPE_CHECKING:
    from agent.providers.base import Provider
    from agent.tools.base import ToolImage

logger = logging.getLogger(__name__)


def extract_text_from_content(content: object) -> str:
    """Return plain text from string or OpenAI-style multimodal content."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def build_multimodal_content(
    text: str,
    image_sources: Sequence[str],
) -> list[dict[str, object]]:
    """Build OpenAI-style multimodal content parts from text + image paths/URLs."""

    parts: list[dict[str, object]] = [{"type": "text", "text": text}]
    for source in image_sources:
        data_url = load_image_as_data_url(source)
        parts.append(image_url_part(data_url))
    return parts


def describe_images_into_text(
    text: str,
    image_sources: Sequence[str],
    *,
    questions: Sequence[str] | None = None,
    settings: Settings | None = None,
) -> str:
    """Append aux vision descriptions for each image onto ``text``."""

    sections = [text.rstrip()] if text.strip() else []
    for index, source in enumerate(image_sources):
        question = ""
        if questions and index < len(questions):
            question = questions[index]
        analysis = describe_image(source, question=question, settings=settings)
        sections.append(f"[Image analysis for {source}]\n{analysis}")
    return "\n\n".join(sections).strip()


def resolve_mode(
    provider: Provider | str,
    model: str,
    settings: Settings | None = None,
) -> VisionMode:
    cfg = settings or load_setup_settings()
    provider_name = provider if isinstance(provider, str) else provider.name
    supports: bool | None = None
    if not isinstance(provider, str):
        try:
            supports = provider.supports_vision(model)
        except Exception:  # noqa: BLE001
            supports = None
    return decide_vision_mode(
        provider_name,
        model,
        cfg,
        provider_supports_vision=supports,
    )


def build_tool_message_content(
    rendered_text: str,
    images: Sequence[ToolImage],
    *,
    provider: Provider | str,
    model: str,
    settings: Settings | None = None,
) -> str | list[dict[str, object]]:
    """Build tool-message content for native or aux vision routing."""

    if not images:
        return rendered_text

    cfg = settings or load_setup_settings()
    mode = resolve_mode(provider, model, cfg)
    sources = [image.path for image in images]
    questions = [image.question for image in images]

    if mode == "off":
        paths = ", ".join(sources)
        return f"{rendered_text.rstrip()}\n\n[Images attached but vision is off: {paths}]"

    if mode == "aux":
        return describe_images_into_text(
            rendered_text,
            sources,
            questions=questions,
            settings=cfg,
        )

    # native
    try:
        return build_multimodal_content(rendered_text, sources)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Native vision attach failed; falling back to aux: %s", exc)
        return describe_images_into_text(
            rendered_text,
            sources,
            questions=questions,
            settings=cfg,
        )


def build_user_provider_content(
    display_text: str,
    image_paths: Sequence[str],
    *,
    provider: Provider | str,
    model: str,
    settings: Settings | None = None,
) -> str | list[dict[str, object]]:
    """Build provider user content for attached images (display text stays plain)."""

    if not image_paths:
        return display_text

    cfg = settings or load_setup_settings()
    mode = resolve_mode(provider, model, cfg)
    hint_lines = [display_text.rstrip()] if display_text.strip() else []
    for path in image_paths:
        hint_lines.append(f"[Image attached at: {path}]")
    text = "\n".join(hint_lines).strip() or "Please examine the attached image(s)."

    if mode == "off":
        return text
    if mode == "aux":
        return describe_images_into_text(text, image_paths, settings=cfg)

    try:
        return build_multimodal_content(text, image_paths)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Native user-image attach failed; falling back to aux: %s", exc)
        return describe_images_into_text(text, image_paths, settings=cfg)


def prune_image_parts(content: object) -> object:
    """Replace image parts with short text placeholders for compaction."""

    if not isinstance(content, list):
        return content
    pruned: list[dict[str, object]] = []
    changed = False
    for item in content:
        if not isinstance(item, dict):
            pruned.append({"type": "text", "text": str(item)})
            changed = True
            continue
        kind = item.get("type")
        if kind in {"image_url", "image", "input_image"}:
            url = ""
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = str(image_url.get("url") or "")[:80]
            pruned.append(
                {
                    "type": "text",
                    "text": f"[image omitted{': ' + url if url else ''}]",
                }
            )
            changed = True
        else:
            pruned.append(item)
    if not changed:
        return content
    # Collapse to string when only text remains and a single part.
    texts = [
        str(part.get("text") or "")
        for part in pruned
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    if len(pruned) == len(texts) and len(texts) == 1:
        return texts[0]
    return pruned
