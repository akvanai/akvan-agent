"""Built-in HTML content extraction provider."""

from __future__ import annotations

import importlib.util
import logging
import re
from typing import Any

import httpx

from agent.tools.web.provider import WebSearchProvider

logger = logging.getLogger(__name__)


def _bs4_available() -> bool:
    return importlib.util.find_spec("bs4") is not None


def _beautiful_soup(html: str):
    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "beautifulsoup4 is required for web extraction. "
            "Reinstall Akvan Agent to restore the dependency."
        ) from exc
    return BeautifulSoup(html, "html.parser")

CONTENT_FETCH_TIMEOUT_SECONDS = 7.0
MAX_TABLE_ROWS = 6
MAX_EXTRACTED_CONTENT_LENGTH = 10_000
TRUNCATION_SUFFIX = "... [Content truncated]"


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _format_table_rows(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    return "\n".join(f"| {' | '.join(row)} |" for row in rows[:MAX_TABLE_ROWS])


def _truncate_content(content: str) -> str:
    if len(content) <= MAX_EXTRACTED_CONTENT_LENGTH:
        return content
    truncated = content[:MAX_EXTRACTED_CONTENT_LENGTH]
    last_space_index = truncated.rfind(" ")
    if last_space_index > MAX_EXTRACTED_CONTENT_LENGTH * 0.9:
        truncated = truncated[:last_space_index]
    return f"{truncated}{TRUNCATION_SUFFIX}"


def extract_html_content(html: str) -> str:
    """Extract paragraph, heading, and table text using akvan-web rules."""

    trimmed_html = html.strip()
    if not trimmed_html:
        return ""

    soup = _beautiful_soup(trimmed_html)
    blocks: list[str] = []
    for element in soup.select("p,h1,h2,h3,h4,table"):
        tag_name = (element.name or "").lower()
        if tag_name == "table":
            rows: list[list[str]] = []
            for row in element.select("tr"):
                cells = [
                    _normalize_whitespace(cell.get_text(" "))
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                cells = [cell for cell in cells if cell]
                if cells:
                    rows.append(cells)
            table = _format_table_rows(rows)
            if table:
                blocks.append(table)
            continue

        content = _normalize_whitespace(element.get_text(" "))
        if not content:
            continue
        if tag_name == "p":
            blocks.append(content)
            continue
        if tag_name in {"h1", "h2", "h3", "h4"}:
            level = int(tag_name[1])
            blocks.append(f"{'#' * level} {content}")

    return _truncate_content("\n".join(blocks))


def _extract_title(html: str) -> str:
    soup = _beautiful_soup(html)
    if soup.title and soup.title.string:
        return _normalize_whitespace(soup.title.string)
    heading = soup.find(["h1", "h2", "h3", "h4"])
    if heading:
        return _normalize_whitespace(heading.get_text(" "))
    return ""


class ContentExtractorProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "content_extractor"

    @property
    def display_name(self) -> str:
        return "Akvan content extractor"

    def is_available(self) -> bool:
        return _bs4_available()

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        _ = kwargs
        results: list[dict[str, Any]] = []
        headers = {
            "User-Agent": "akvan-web-search/2.0",
            "Accept": "text/html,application/xhtml+xml",
        }
        timeout = httpx.Timeout(CONTENT_FETCH_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for url in urls:
                try:
                    response = await client.get(url, headers=headers)
                    if response.status_code < 200 or response.status_code >= 300:
                        results.append(
                            {
                                "url": url,
                                "title": "",
                                "content": "",
                                "raw_content": "",
                                "error": (
                                    f"HTTP {response.status_code} while fetching URL"
                                ),
                            }
                        )
                        continue
                    html = response.text
                    content = extract_html_content(html)
                    results.append(
                        {
                            "url": str(response.url),
                            "title": _extract_title(html),
                            "content": content,
                            "raw_content": content,
                            "metadata": {},
                        }
                    )
                except httpx.TimeoutException:
                    logger.debug("Content extraction timed out for %s", url)
                    results.append(
                        {
                            "url": url,
                            "title": "",
                            "content": "",
                            "raw_content": "",
                            "error": "Fetch timed out after 7s",
                        }
                    )
                except httpx.RequestError as exc:
                    logger.debug("Content extraction request failed for %s: %s", url, exc)
                    results.append(
                        {
                            "url": url,
                            "title": "",
                            "content": "",
                            "raw_content": "",
                            "error": str(exc),
                        }
                    )
        return results

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "built in",
            "tag": "Extract HTML page content directly with Akvan.",
            "env_vars": [],
            "web_backend": "content_extractor",
            "capability": "extract",
        }
