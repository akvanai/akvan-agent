"""Firecrawl extract provider tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def test_firecrawl_extract_returns_markdown():
    from agent.tools.web.providers.firecrawl import FirecrawlProvider

    mock_client = MagicMock()
    mock_client.scrape.return_value = {
        "data": {
            "markdown": "# Hello",
            "metadata": {"title": "Example", "sourceURL": "https://example.com"},
        }
    }

    async def run():
        with patch(
            "agent.tools.web.providers.firecrawl._get_firecrawl_client",
            return_value=mock_client,
        ):
            with patch(
                "agent.tools.web.providers.firecrawl.check_firecrawl_configured",
                return_value=True,
            ):
                return await FirecrawlProvider().extract(["https://example.com"])

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0]["content"] == "# Hello"
    assert results[0]["title"] == "Example"


def test_firecrawl_extract_timeout():
    from agent.tools.web.providers.firecrawl import FirecrawlProvider

    mock_client = MagicMock()
    mock_client.scrape.return_value = {}

    async def run():
        with patch(
            "agent.tools.web.providers.firecrawl._get_firecrawl_client",
            return_value=mock_client,
        ):
            with patch(
                "agent.tools.web.providers.firecrawl.check_firecrawl_configured",
                return_value=True,
            ):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    return await FirecrawlProvider().extract(["https://example.com"])

    results = asyncio.run(run())
    assert results[0]["error"]
