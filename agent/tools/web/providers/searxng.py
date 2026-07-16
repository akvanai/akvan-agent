"""SearXNG search provider."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.tools.web.config import get_env_value
from agent.tools.web.provider import WebSearchProvider

logger = logging.getLogger(__name__)


def _searxng_url() -> str:
    return get_env_value("SEARXNG_URL")


class SearXNGProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "searxng"

    @property
    def display_name(self) -> str:
        return "SearXNG"

    def is_available(self) -> bool:
        return bool(_searxng_url())

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        base_url = _searxng_url().rstrip("/")
        if not base_url:
            return {"success": False, "error": "SEARXNG_URL is not set"}
        params = {"q": query, "format": "json", "pageno": 1}
        try:
            resp = httpx.get(
                f"{base_url}/search",
                params=params,
                timeout=15,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("SearXNG HTTP error: %s", exc)
            return {
                "success": False,
                "error": f"SearXNG returned HTTP {exc.response.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("SearXNG request error: %s", exc)
            return {
                "success": False,
                "error": f"Could not reach SearXNG at {base_url}: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("SearXNG response parse error: %s", exc)
            return {"success": False, "error": "Could not parse SearXNG response as JSON"}

        raw_results = data.get("results", [])
        sorted_results = sorted(
            raw_results,
            key=lambda r: float(r.get("score", 0)),
            reverse=True,
        )[:limit]
        web_results = [
            {
                "title": str(r.get("title", "")),
                "url": str(r.get("url", "")),
                "description": str(r.get("content", "")),
                "position": i + 1,
            }
            for i, r in enumerate(sorted_results)
        ]
        logger.info(
            "SearXNG search %r: %d results (limit %d)",
            query,
            len(web_results),
            limit,
        )
        return {"success": True, "data": {"web": web_results}}

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "SearXNG",
            "badge": "free · self-hosted",
            "tag": "Privacy-respecting metasearch. Point SEARXNG_URL at your instance.",
            "env_vars": [
                {
                    "key": "SEARXNG_URL",
                    "prompt": "SearXNG instance URL (e.g. http://localhost:8080)",
                    "url": "https://searx.space/",
                    "default": "http://localhost:8080",
                },
            ],
            "web_backend": "searxng",
        }
