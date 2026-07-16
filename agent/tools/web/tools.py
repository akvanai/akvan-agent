"""web_search and web_extract tool implementations."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

from agent.tools.base import Tool
from agent.tools.web.registry import (
    get_active_extract_provider,
    get_active_search_provider,
    get_provider,
)
from agent.tools.web.config import get_extract_backend, get_search_backend
from agent.tools.web.summarizer import (
    DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION,
    process_content_with_llm,
)
from agent.tools.web.url_safety import async_is_safe_url, normalize_url_for_request

logger = logging.getLogger(__name__)

WEB_SEARCH_DESCRIPTION = (
    "Search the web for information. Returns up to 5 results by default with "
    "titles, URLs, and descriptions. The query is passed through to the "
    "configured backend, so operators such as site:domain, filetype:pdf, "
    "intitle:word, -term, and \"exact phrase\" may work when the backend "
    "supports them."
)

WEB_EXTRACT_DESCRIPTION = (
    "Extract content from web page URLs. Returns page content in markdown format. "
    "Fetches HTML pages directly and extracts paragraphs, headings, and tables. "
    "Pages under 5000 chars return full markdown; larger pages "
    "are LLM-summarized and capped at ~5000 chars per page. Pages over 2M chars "
    "are refused. If a URL fails or times out, use the browser tool to access it "
    "instead."
)


def _clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 5
    return min(max(value, 1), 100)


def web_search(query: str, limit: int = 5) -> str:
    limit = _clamp_limit(limit)
    backend = get_search_backend()
    provider = get_provider(backend) if backend else None
    if provider is None or not provider.supports_search():
        provider = get_active_search_provider()
    if provider is None:
        return json.dumps(
            {
                "success": False,
                "error": (
                    "No web search provider configured. "
                    "Run `akvan tools` to set one up."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Web search via %s: %r (limit=%d)", provider.name, query, limit)
    response_data = provider.search(query, limit)
    return json.dumps(response_data, ensure_ascii=False, indent=2)


async def _web_extract_async(urls: list[str]) -> str:
    normalized_urls = [normalize_url_for_request(url) for url in urls]
    safe_urls: list[str] = []
    ssrf_blocked: list[dict[str, Any]] = []
    for url in normalized_urls:
        if not await async_is_safe_url(url):
            ssrf_blocked.append(
                {
                    "url": url,
                    "title": "",
                    "content": "",
                    "error": "Blocked: URL targets a private or internal network address",
                }
            )
        else:
            safe_urls.append(url)

    if not safe_urls and ssrf_blocked:
        return json.dumps({"results": ssrf_blocked}, ensure_ascii=False, indent=2)

    backend = get_extract_backend()
    provider = get_provider(backend) if backend else None
    if provider is None or not provider.supports_extract():
        if provider is not None and not provider.supports_extract():
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"{provider.display_name} is a search-only backend and "
                        "cannot extract URL content. Set AKVAN_WEB_EXTRACT_BACKEND "
                        "to content_extractor."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        provider = get_active_extract_provider()
        if provider is None:
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        "No web extract provider configured. "
                        "The built-in content extractor should be available."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )

    logger.info("Web extract via %s: %d URL(s)", provider.name, len(safe_urls))
    if inspect.iscoroutinefunction(provider.extract):
        results = await provider.extract(safe_urls, format="markdown")
    else:
        results = await asyncio.to_thread(provider.extract, safe_urls, format="markdown")

    if ssrf_blocked:
        results = ssrf_blocked + list(results)

    for result in results:
        raw_content = result.get("raw_content", "") or result.get("content", "")
        if not raw_content or result.get("error"):
            continue
        processed = await process_content_with_llm(
            raw_content,
            url=str(result.get("url", "")),
            title=str(result.get("title", "")),
            min_length=DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION,
        )
        if processed:
            result["content"] = processed
            result["raw_content"] = raw_content

    trimmed = [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "error": r.get("error"),
        }
        for r in results
    ]
    if not trimmed:
        return json.dumps(
            {"success": False, "error": "Content was inaccessible or not found"},
            ensure_ascii=False,
            indent=2,
        )
    return json.dumps({"results": trimmed}, ensure_ascii=False, indent=2)


def web_extract(urls: list[str]) -> str:
    if not isinstance(urls, list):
        raise ValueError("urls must be a list")
    capped = [str(url) for url in urls[:5]]
    return asyncio.run(_web_extract_async(capped))


def build_web_tools() -> tuple[Tool, Tool]:
    search_tool = Tool(
        name="web_search",
        description=WEB_SEARCH_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query to look up on the web. You may include "
                        "backend-supported operators such as site:example.com."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 5.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        run=web_search,
    )
    extract_tool = Tool(
        name="web_extract",
        description=WEB_EXTRACT_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to extract content from (max 5 URLs per call)",
                    "maxItems": 5,
                },
            },
            "required": ["urls"],
        },
        run=web_extract,
    )
    return search_tool, extract_tool
