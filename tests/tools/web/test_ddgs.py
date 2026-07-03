"""DDGS provider tests."""

from __future__ import annotations

from unittest.mock import patch


def test_not_available_without_package():
    from agent.tools.web.providers.ddgs import DDGSProvider

    with patch.dict("sys.modules", {"ddgs": None}):
        import importlib

        import agent.tools.web.providers.ddgs as ddgs_module

        importlib.reload(ddgs_module)
        # After reload with ddgs=None in sys.modules, import ddgs fails
        provider = ddgs_module.DDGSProvider()
        assert provider.is_available() is False


def test_search_returns_error_when_package_missing():
    from agent.tools.web.providers.ddgs import DDGSProvider

    with patch(
        "agent.tools.web.providers.ddgs.DDGSProvider.is_available",
        return_value=False,
    ):
        result = DDGSProvider().search("test")
    assert result["success"] is False
    assert "ddgs" in result["error"].lower()


def test_search_happy_path():
    from agent.tools.web.providers.ddgs import DDGSProvider

    with patch("agent.tools.web.providers.ddgs._run_ddgs_search", return_value=[
        {
            "title": "Hit",
            "url": "https://example.com",
            "description": "Body",
            "position": 1,
        }
    ]):
        with patch.object(DDGSProvider, "is_available", return_value=True):
            with patch.dict("sys.modules", {"ddgs": object()}):
                result = DDGSProvider().search("hello", limit=3)
    assert result["success"] is True
    assert result["data"]["web"][0]["url"] == "https://example.com"
