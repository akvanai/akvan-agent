"""End-to-end web tool dispatch tests."""

from __future__ import annotations

import json
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


def test_web_extract_search_only_error():
    from agent.tools.web.tools import web_extract

    provider = MagicMock()
    provider.display_name = "SearXNG"
    provider.supports_extract.return_value = False
    with patch("agent.tools.web.tools.get_extract_backend", return_value="searxng"):
        with patch("agent.tools.web.tools.get_provider", return_value=provider):
            with patch("agent.tools.web.tools.get_active_extract_provider", return_value=None):
                payload = json.loads(web_extract(["https://example.com"]))
    assert payload["success"] is False
    assert "search-only" in payload["error"]


def test_web_extract_ssrf_blocks_localhost():
    from agent.tools.web.tools import web_extract

    with patch("agent.tools.web.tools.async_is_safe_url", new=AsyncMock(return_value=False)):
        payload = json.loads(web_extract(["http://127.0.0.1/secret"]))
    assert payload["results"][0]["error"]
