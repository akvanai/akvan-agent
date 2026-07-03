"""DuckDuckGo search via the ddgs package."""

from __future__ import annotations

import concurrent.futures as cf
import logging
from typing import Any

from agent.tools.web.provider import WebSearchProvider

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_SECS = 30


def _run_ddgs_search(query: str, safe_limit: int) -> list[dict[str, Any]]:
    from ddgs import DDGS

    results: list[dict[str, Any]] = []
    with DDGS(timeout=10) as client:
        for i, hit in enumerate(client.text(query, max_results=safe_limit)):
            if i >= safe_limit:
                break
            results.append(
                {
                    "title": str(hit.get("title", "")),
                    "url": str(hit.get("href") or hit.get("url") or ""),
                    "description": str(hit.get("body", "")),
                    "position": i + 1,
                }
            )
    return results


class DDGSProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "ddgs"

    @property
    def display_name(self) -> str:
        return "DuckDuckGo (ddgs)"

    def is_available(self) -> bool:
        try:
            import ddgs  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        try:
            import ddgs  # noqa: F401
        except ImportError:
            return {
                "success": False,
                "error": "ddgs package is not installed — run `pip install akvan-agent[web]`",
            }

        safe_limit = max(1, int(limit))
        pool = cf.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_run_ddgs_search, query, safe_limit)
            try:
                web_results = future.result(timeout=_SEARCH_TIMEOUT_SECS)
            except cf.TimeoutError:
                logger.warning("DDGS search timed out after %ds", _SEARCH_TIMEOUT_SECS)
                return {
                    "success": False,
                    "error": (
                        f"DuckDuckGo search timed out after {_SEARCH_TIMEOUT_SECS}s — "
                        "try again later or switch to SearXNG."
                    ),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("DDGS search error: %s", exc)
            return {"success": False, "error": f"DuckDuckGo search failed: {exc}"}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        logger.info("DDGS search %r: %d results", query, len(web_results))
        return {"success": True, "data": {"web": web_results}}

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "DuckDuckGo (ddgs)",
            "badge": "free · no key · search only",
            "tag": "Search via the ddgs Python package — no API key required",
            "env_vars": [],
            "web_backend": "ddgs",
            "post_setup": "ddgs",
        }
