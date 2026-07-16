"""SearXNG provider tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSearXNGProvider:
    def test_is_available_when_url_set(self, monkeypatch):
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
        from agent.tools.web.providers.searxng import SearXNGProvider

        assert SearXNGProvider().is_available() is True

    def test_search_normalizes_results(self, monkeypatch):
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
        from agent.tools.web.providers.searxng import SearXNGProvider

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.example",
                    "content": "Desc A",
                    "score": 0.9,
                },
                {
                    "title": "B",
                    "url": "https://b.example",
                    "content": "Desc B",
                    "score": 0.5,
                },
            ]
        }
        with patch("httpx.get", return_value=mock_resp):
            result = SearXNGProvider().search("test", limit=5)
        assert result["success"] is True
        web = result["data"]["web"]
        assert web[0]["title"] == "A"
        assert web[0]["position"] == 1

    def test_search_http_error(self, monkeypatch):
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
        import httpx
        from agent.tools.web.providers.searxng import SearXNGProvider

        response = MagicMock()
        response.status_code = 503
        request = MagicMock()
        with patch(
            "httpx.get",
            side_effect=httpx.HTTPStatusError("fail", request=request, response=response),
        ):
            result = SearXNGProvider().search("test")
        assert result["success"] is False
        assert "503" in result["error"]
