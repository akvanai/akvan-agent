"""Summarizer tests."""

from __future__ import annotations

import asyncio


def test_short_content_skips_summarization():
    from agent.tools.web.summarizer import process_content_with_llm

    result = asyncio.run(process_content_with_llm("short", min_length=5000))
    assert result is None


def test_oversized_content_refused():
    from agent.tools.web.summarizer import process_content_with_llm

    huge = "x" * 2_000_001
    result = asyncio.run(process_content_with_llm(huge))
    assert result is not None
    assert "too large" in result.lower()
