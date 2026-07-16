"""Built-in content extractor provider tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest


def test_extract_html_content_formats_blocks_in_order():
    from agent.tools.web.providers.content_extractor import extract_html_content

    html = """
    <h1> Main   Title </h1>
    <p>Hello    world</p>
    <h3>Details</h3>
    <table>
      <tr><th>Name</th><th>Value</th></tr>
      <tr><td>A</td><td>1</td></tr>
    </table>
    """

    assert extract_html_content(html) == "\n".join(
        [
            "# Main Title",
            "Hello world",
            "### Details",
            "| Name | Value |",
            "| A | 1 |",
        ]
    )


def test_extract_html_content_caps_table_rows():
    from agent.tools.web.providers.content_extractor import extract_html_content

    rows = "".join(f"<tr><td>{index}</td></tr>" for index in range(8))
    content = extract_html_content(f"<table>{rows}</table>")

    assert "| 0 |" in content
    assert "| 5 |" in content
    assert "| 6 |" not in content


def test_extract_html_content_empty_html():
    from agent.tools.web.providers.content_extractor import extract_html_content

    assert extract_html_content("   ") == ""


def test_extract_html_content_truncates_long_content():
    from agent.tools.web.providers.content_extractor import (
        MAX_EXTRACTED_CONTENT_LENGTH,
        TRUNCATION_SUFFIX,
        extract_html_content,
    )

    content = extract_html_content(f"<p>{'word ' * 3000}</p>")

    assert len(content) <= MAX_EXTRACTED_CONTENT_LENGTH + len(TRUNCATION_SUFFIX)
    assert content.endswith(TRUNCATION_SUFFIX)


def test_content_extractor_fetch_success():
    from agent.tools.web.providers.content_extractor import ContentExtractorProvider

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text="<title>Example</title><h1>Hello</h1><p>World</p>",
            request=request,
        )

    async def run():
        transport = httpx.MockTransport(handler)
        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(httpx, "AsyncClient", client_factory)
            return await ContentExtractorProvider().extract(["https://example.com"])

    results = asyncio.run(run())

    assert requests[0].headers["user-agent"] == "akvan-web-search/2.0"
    assert results[0]["title"] == "Example"
    assert results[0]["content"] == "# Hello\nWorld"


def test_content_extractor_fetch_http_error():
    from agent.tools.web.providers.content_extractor import ContentExtractorProvider

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(503, request=request))
        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(httpx, "AsyncClient", client_factory)
            return await ContentExtractorProvider().extract(["https://example.com"])

    results = asyncio.run(run())

    assert results[0]["content"] == ""
    assert "HTTP 503" in results[0]["error"]


def test_content_extractor_fetch_request_error():
    from agent.tools.web.providers.content_extractor import ContentExtractorProvider

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def run():
        transport = httpx.MockTransport(handler)
        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(httpx, "AsyncClient", client_factory)
            return await ContentExtractorProvider().extract(["https://example.com"])

    results = asyncio.run(run())

    assert results[0]["content"] == ""
    assert "boom" in results[0]["error"]
