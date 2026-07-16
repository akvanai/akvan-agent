"""URL safety tests."""

from __future__ import annotations

import pytest

from agent.tools.web.url_safety import is_safe_url, normalize_url_for_request


def test_normalize_url_encodes_non_ascii_host():
    normalized = normalize_url_for_request("https://wttr.in/Köln")
    assert "K" in normalized


def test_blocks_localhost():
    assert is_safe_url("http://127.0.0.1/admin") is False


def test_blocks_metadata_host():
    assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False


def test_allows_public_https(monkeypatch):
    assert is_safe_url("https://example.com") is True
