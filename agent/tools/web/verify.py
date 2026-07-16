"""Verification helpers for the web tools setup wizard."""

from __future__ import annotations

import time

import httpx


def verify_searxng_url(url: str, *, wait_seconds: float = 0, timeout: float = 15) -> str:
    base = url.strip().rstrip("/")
    if not base:
        raise ValueError("SEARXNG_URL must not be empty.")

    deadline = time.monotonic() + max(0, wait_seconds)
    last_error = "SearXNG did not respond."
    while True:
        try:
            response = httpx.get(
                f"{base}/search",
                params={"q": "test", "format": "json"},
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            if "results" not in payload:
                last_error = "SearXNG response did not include a results array."
            else:
                return base
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = f"SearXNG returned HTTP {status} from {base}/search."
        except httpx.RequestError as exc:
            last_error = f"Could not reach SearXNG at {base}: {exc}"
        except ValueError as exc:
            last_error = f"Could not parse SearXNG response from {base}: {exc}"

        if time.monotonic() >= deadline:
            raise ValueError(last_error)
        time.sleep(2)


def verify_ddgs_available() -> str:
    try:
        import ddgs  # noqa: F401
    except ImportError as exc:
        raise ValueError(
            "ddgs package is not installed — run `pip install akvan-agent[web]`"
        ) from exc
    return "ddgs package available"
