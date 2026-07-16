"""End-to-end web tool dispatch tests."""

from __future__ import annotations

import json

import httpx
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_web_search_unconfigured():
    from agent.tools.web.tools import web_search

    with patch("agent.tools.web.tools.get_search_backend", return_value=""):
        with patch("agent.tools.web.tools.get_provider", return_value=None):
            with patch("agent.tools.web.tools.get_active_search_provider", return_value=None):
                payload = json.loads(web_search("hello"))
    assert payload["success"] is False
    assert "akvan tools" in payload["error"]


def test_web_search_reads_env_from_akvan_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    from agent.tools.web.config import save_web_env
    from agent.tools.web.tools import web_search

    save_web_env(
        {
            "AKVAN_WEB_SEARCH_BACKEND": "searxng",
            "SEARXNG_URL": "http://localhost:8090",
        },
        project_root=tmp_path,
    )
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "Test",
                "url": "https://example.com",
                "content": "desc",
                "score": 1.0,
            }
        ]
    }
    with patch("httpx.get", return_value=mock_resp):
        payload = json.loads(web_search("test query"))
    assert payload["success"] is True
    assert payload["data"]["web"]


def test_web_search_dispatches_provider():
    from agent.tools.web.tools import web_search

    provider = MagicMock()
    provider.name = "searxng"
    provider.supports_search.return_value = True
    provider.search.return_value = {
        "success": True,
        "data": {"web": [{"title": "T", "url": "https://x", "description": "", "position": 1}]},
    }
    with patch("agent.tools.web.tools.get_search_backend", return_value="searxng"):
        with patch("agent.tools.web.tools.get_provider", return_value=provider):
            payload = json.loads(web_search("hello"))
    assert payload["success"] is True
    provider.search.assert_called_once()


def test_verify_searxng_url_wraps_request_errors() -> None:
    from agent.tools.web.verify import verify_searxng_url

    request = httpx.Request("GET", "http://127.0.0.1:8090/search")
    with patch("httpx.get", side_effect=httpx.ReadError("reset", request=request)):
        with pytest.raises(ValueError, match="Could not reach SearXNG"):
            verify_searxng_url("http://127.0.0.1:8090")


def test_verify_searxng_url_can_wait_for_startup() -> None:
    from agent.tools.web.verify import verify_searxng_url

    request = httpx.Request("GET", "http://127.0.0.1:8090/search")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"results": []}

    with patch("httpx.get", side_effect=[httpx.ReadError("reset", request=request), response]):
        with patch("time.sleep") as sleep:
            assert (
                verify_searxng_url("http://127.0.0.1:8090", wait_seconds=5)
                == "http://127.0.0.1:8090"
            )

    sleep.assert_called_once_with(2)


def test_web_extract_uses_default_content_extractor():
    from agent.tools.web.tools import web_extract

    provider = MagicMock()
    provider.name = "content_extractor"
    provider.supports_extract.return_value = True

    async def extract(urls, **kwargs):
        return [
            {
                "url": urls[0],
                "title": "Example",
                "content": "Hello",
                "raw_content": "Hello",
            }
        ]

    provider.extract = extract
    with patch("agent.tools.web.tools.get_extract_backend", return_value="content_extractor"):
        with patch("agent.tools.web.tools.get_provider", return_value=provider):
            with patch("agent.tools.web.tools.async_is_safe_url", new=AsyncMock(return_value=True)):
                payload = json.loads(web_extract(["https://example.com"]))
    assert payload["results"][0]["content"] == "Hello"


def test_web_extract_ssrf_blocks_localhost():
    from agent.tools.web.tools import web_extract

    with patch("agent.tools.web.tools.async_is_safe_url", new=AsyncMock(return_value=False)):
        payload = json.loads(web_extract(["http://127.0.0.1/secret"]))
    assert payload["results"][0]["error"]
