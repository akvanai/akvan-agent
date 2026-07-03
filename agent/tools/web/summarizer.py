"""LLM summarization for long web_extract results."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.config import load_setup_settings
from agent.providers import build_provider
from agent.providers.base import Provider

logger = logging.getLogger(__name__)

DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION = 5000
MAX_CONTENT_SIZE = 2_000_000
CHUNK_THRESHOLD = 500_000
CHUNK_SIZE = 100_000
MAX_OUTPUT_SIZE = 5000


def _resolve_summary_model() -> tuple[Provider | None, str]:
    settings = load_setup_settings()
    model = settings.web_extract_summary_model or settings.model
    try:
        provider = build_provider(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build provider for web extract summarization: %s", exc)
        return None, model
    return provider, model


def _call_summarizer_llm_sync(
    content: str,
    context_str: str,
    model: str,
    *,
    provider: Provider,
    max_tokens: int = 20000,
    is_chunk: bool = False,
    chunk_info: str = "",
) -> str | None:
    if is_chunk:
        system_prompt = """You are an expert content analyst processing a SECTION of a larger document. Extract key information from THIS SECTION ONLY.

Do NOT write introductions or conclusions. Preserve quotes, code, and specific details. Use bullet points."""
        user_prompt = (
            f"Extract key information from this SECTION:\n\n{context_str}{chunk_info}\n\n"
            f"SECTION CONTENT:\n{content}"
        )
    else:
        system_prompt = """You are an expert content analyst. Create a comprehensive yet concise markdown summary that preserves all important information while reducing bulk."""
        user_prompt = (
            f"Process this web content into a markdown summary:\n\n{context_str}"
            f"CONTENT TO PROCESS:\n{content}"
        )

    try:
        completion = provider.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            options={"max_tokens": max_tokens, "temperature": 0.1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Summarizer LLM call failed: %s", exc)
        return None
    finally:
        provider.close()

    message = completion.message
    result = message.get("content")
    return result if isinstance(result, str) and result.strip() else None


async def _call_summarizer_llm(
    content: str,
    context_str: str,
    model: str | None,
    *,
    is_chunk: bool = False,
    chunk_info: str = "",
) -> str | None:
    provider, effective_model = _resolve_summary_model()
    if provider is None:
        return None
    resolved_model = model or effective_model
    return await asyncio.to_thread(
        _call_summarizer_llm_sync,
        content,
        context_str,
        resolved_model,
        provider=provider,
        is_chunk=is_chunk,
        chunk_info=chunk_info,
    )


async def _process_large_content_chunked(
    content: str,
    context_str: str,
    model: str | None,
    chunk_size: int,
    max_output_size: int,
) -> str | None:
    chunks = [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]
    logger.info("Split into %d chunks of ~%d chars each", len(chunks), chunk_size)

    async def summarize_chunk(chunk_idx: int, chunk_content: str) -> tuple[int, str | None]:
        chunk_info = f"[Processing chunk {chunk_idx + 1} of {len(chunks)}]"
        summary = await _call_summarizer_llm(
            chunk_content,
            context_str,
            model,
            is_chunk=True,
            chunk_info=chunk_info,
        )
        return chunk_idx, summary

    tasks = [summarize_chunk(i, chunk) for i, chunk in enumerate(chunks)]
    processed = await asyncio.gather(*tasks, return_exceptions=True)
    summaries: list[str] = []
    for item in processed:
        if isinstance(item, BaseException):
            continue
        _, summary = item
        if summary:
            summaries.append(summary)
    if not summaries:
        return None
    combined = "\n\n---\n\n".join(summaries)
    synthesis = await _call_summarizer_llm(
        combined,
        context_str + "[Synthesizing chunk summaries into a final summary]\n\n",
        model,
    )
    if synthesis and len(synthesis) > max_output_size:
        synthesis = synthesis[:max_output_size] + "\n\n[... summary truncated ...]"
    return synthesis


async def process_content_with_llm(
    content: str,
    *,
    url: str = "",
    title: str = "",
    model: str | None = None,
    min_length: int = DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION,
) -> str | None:
    content_len = len(content)
    if content_len > MAX_CONTENT_SIZE:
        size_mb = content_len / 1_000_000
        return (
            f"[Content too large to process: {size_mb:.1f}MB. "
            "Try a more focused source URL.]"
        )
    if content_len < min_length:
        return None

    context_parts: list[str] = []
    if title:
        context_parts.append(f"Title: {title}")
    if url:
        context_parts.append(f"Source: {url}")
    context_str = "\n".join(context_parts) + "\n\n" if context_parts else ""

    try:
        if content_len > CHUNK_THRESHOLD:
            processed = await _process_large_content_chunked(
                content,
                context_str,
                model,
                CHUNK_SIZE,
                MAX_OUTPUT_SIZE,
            )
        else:
            processed = await _call_summarizer_llm(content, context_str, model)

        if processed and len(processed) > MAX_OUTPUT_SIZE:
            processed = (
                processed[:MAX_OUTPUT_SIZE]
                + "\n\n[... summary truncated for context management ...]"
            )
        return processed
    except Exception as exc:  # noqa: BLE001
        logger.warning("web_extract LLM summarization failed: %s", exc)
        truncated = content[:MAX_OUTPUT_SIZE]
        if len(content) > MAX_OUTPUT_SIZE:
            truncated += (
                f"\n\n[Content truncated — showing first {MAX_OUTPUT_SIZE:,} of "
                f"{len(content):,} chars. LLM summarization failed.]"
            )
        return truncated
