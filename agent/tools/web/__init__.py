"""Web search and extract tools."""

from __future__ import annotations

from typing import Any

__all__ = ["build_web_tools", "web_extract", "web_search"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from agent.tools.web.tools import build_web_tools, web_extract, web_search

    values = {
        "build_web_tools": build_web_tools,
        "web_extract": web_extract,
        "web_search": web_search,
    }
    return values[name]
