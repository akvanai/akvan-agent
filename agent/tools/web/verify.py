"""Verification helpers for the web tools setup wizard."""

from __future__ import annotations

import httpx

from agent.tools.web.providers.firecrawl import check_firecrawl_configured, _get_firecrawl_client


def verify_searxng_url(url: str) -> str:
    base = url.strip().rstrip("/")
    if not base:
        raise ValueError("SEARXNG_URL must not be empty.")
    response = httpx.get(
        f"{base}/search",
        params={"q": "test", "format": "json"},
        timeout=15,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if "results" not in payload:
        raise ValueError("SearXNG response did not include a results array.")
    return base


def verify_ddgs_available() -> str:
    try:
        import ddgs  # noqa: F401
    except ImportError as exc:
        raise ValueError(
            "ddgs package is not installed — run `pip install akvan-agent[web]`"
        ) from exc
    return "ddgs package available"


def verify_firecrawl(api_url: str, api_key: str = "") -> str:
    import os

    previous_url = os.environ.get("FIRECRAWL_API_URL", "")
    previous_key = os.environ.get("FIRECRAWL_API_KEY", "")
    try:
        if api_url.strip():
            os.environ["FIRECRAWL_API_URL"] = api_url.strip().rstrip("/")
        if api_key.strip():
            os.environ["FIRECRAWL_API_KEY"] = api_key.strip()
        elif "FIRECRAWL_API_KEY" in os.environ and not api_key.strip():
            os.environ.pop("FIRECRAWL_API_KEY", None)
        if not check_firecrawl_configured():
            raise ValueError("FIRECRAWL_API_URL or FIRECRAWL_API_KEY is required.")
        client = _get_firecrawl_client()
        client.scrape(url="https://example.com", formats=["markdown"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not reach Firecrawl: {exc}") from exc
    finally:
        if previous_url:
            os.environ["FIRECRAWL_API_URL"] = previous_url
        else:
            os.environ.pop("FIRECRAWL_API_URL", None)
        if previous_key:
            os.environ["FIRECRAWL_API_KEY"] = previous_key
        else:
            os.environ.pop("FIRECRAWL_API_KEY", None)
        from agent.tools.web.providers import firecrawl as fc_module

        fc_module._firecrawl_client = None
        fc_module._firecrawl_client_config = None
    return api_url.strip().rstrip("/") or "Firecrawl configured"
