"""Firecrawl search and extract (self-hosted or cloud)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.tools.web.config import get_env_value
from agent.tools.web.provider import WebSearchProvider

logger = logging.getLogger(__name__)

_firecrawl_client: Any = None
_firecrawl_client_config: tuple[Any, ...] | None = None


def _get_direct_firecrawl_config() -> tuple[dict[str, str], tuple[Any, ...]] | None:
    api_key = get_env_value("FIRECRAWL_API_KEY")
    api_url = get_env_value("FIRECRAWL_API_URL").rstrip("/")
    if not api_key and not api_url:
        return None
    kwargs: dict[str, str] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if api_url:
        kwargs["api_url"] = api_url
    return kwargs, ("direct", api_url or None, api_key or None)


def check_firecrawl_configured() -> bool:
    return _get_direct_firecrawl_config() is not None


def _get_firecrawl_client() -> Any:
    global _firecrawl_client, _firecrawl_client_config
    direct = _get_direct_firecrawl_config()
    if direct is None:
        raise ValueError(
            "Firecrawl is not configured. Set FIRECRAWL_API_URL for self-hosted "
            "or FIRECRAWL_API_KEY for cloud Firecrawl."
        )
    kwargs, client_config = direct
    if _firecrawl_client is not None and _firecrawl_client_config == client_config:
        return _firecrawl_client
    try:
        from firecrawl import Firecrawl
    except ImportError as exc:
        raise ImportError(
            "firecrawl-py is not installed — run `pip install akvan-agent[web]`"
        ) from exc
    _firecrawl_client = Firecrawl(**kwargs)
    _firecrawl_client_config = client_config
    return _firecrawl_client


def _to_plain_object(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "__dict__"):
        try:
            return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
        except Exception:  # noqa: BLE001
            pass
    return value


def _normalize_result_list(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in values:
        plain = _to_plain_object(item)
        if isinstance(plain, dict):
            normalized.append(plain)
    return normalized


def _extract_web_search_results(response: Any) -> list[dict[str, Any]]:
    response_plain = _to_plain_object(response)
    if isinstance(response_plain, dict):
        data = response_plain.get("data")
        if isinstance(data, list):
            return _normalize_result_list(data)
        if isinstance(data, dict):
            data_web = _normalize_result_list(data.get("web"))
            if data_web:
                return data_web
            data_results = _normalize_result_list(data.get("results"))
            if data_results:
                return data_results
        top_web = _normalize_result_list(response_plain.get("web"))
        if top_web:
            return top_web
        top_results = _normalize_result_list(response_plain.get("results"))
        if top_results:
            return top_results
    if hasattr(response, "web"):
        return _normalize_result_list(getattr(response, "web", []))
    return []


def _extract_scrape_payload(scrape_result: Any) -> dict[str, Any]:
    result_plain = _to_plain_object(scrape_result)
    if not isinstance(result_plain, dict):
        return {}
    nested = result_plain.get("data")
    if isinstance(nested, dict):
        return nested
    return result_plain


class FirecrawlProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "firecrawl"

    @property
    def display_name(self) -> str:
        return "Firecrawl"

    def is_available(self) -> bool:
        return check_firecrawl_configured()

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        try:
            client = _get_firecrawl_client()
            response = client.search(query=query, limit=limit)
            web_results = _extract_web_search_results(response)
            return {"success": True, "data": {"web": web_results}}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Firecrawl search error: %s", exc)
            return {"success": False, "error": f"Firecrawl search failed: {exc}"}

    async def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        format = kwargs.get("format")
        if format == "markdown":
            formats = ["markdown"]
        elif format == "html":
            formats = ["html"]
        else:
            formats = ["markdown", "html"]

        results: list[dict[str, Any]] = []
        for url in urls:
            try:
                logger.info("Firecrawl scraping: %s", url)
                try:
                    scrape_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            _get_firecrawl_client().scrape,
                            url=url,
                            formats=formats,
                        ),
                        timeout=60,
                    )
                except asyncio.TimeoutError:
                    results.append(
                        {
                            "url": url,
                            "title": "",
                            "content": "",
                            "error": (
                                "Scrape timed out after 60s — page may be too large "
                                "or unresponsive. Try browser_navigate instead."
                            ),
                        }
                    )
                    continue

                scrape_payload = _extract_scrape_payload(scrape_result)
                metadata = scrape_payload.get("metadata", {})
                if not isinstance(metadata, dict):
                    if hasattr(metadata, "model_dump"):
                        metadata = metadata.model_dump()
                    elif hasattr(metadata, "__dict__"):
                        metadata = metadata.__dict__
                    else:
                        metadata = {}

                title = str(metadata.get("title", ""))
                final_url = str(metadata.get("sourceURL", url))
                content_markdown = scrape_payload.get("markdown")
                content_html = scrape_payload.get("html")
                if format == "markdown" or (format is None and content_markdown):
                    chosen_content = content_markdown
                else:
                    chosen_content = content_html or content_markdown or ""

                results.append(
                    {
                        "url": final_url,
                        "title": title,
                        "content": chosen_content or "",
                        "raw_content": chosen_content or "",
                        "metadata": metadata,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Firecrawl scrape failed for %s: %s", url, exc)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": str(exc),
                    }
                )
        return results

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Firecrawl (self-hosted)",
            "badge": "free · self-hosted",
            "tag": "Extract page content via your own Firecrawl instance",
            "env_vars": [
                {
                    "key": "FIRECRAWL_API_URL",
                    "prompt": "Firecrawl instance URL (e.g. http://localhost:3002)",
                    "default": "http://localhost:3002",
                },
                {
                    "key": "FIRECRAWL_API_KEY",
                    "prompt": "Firecrawl API key (optional for self-hosted with auth disabled)",
                    "secret": True,
                },
            ],
            "web_backend": "firecrawl",
            "capability": "extract",
        }
