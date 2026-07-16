"""Managed local SearXNG runtime for Akvan web search."""

from agent.tools.web.searxng_runtime.config import (
    DEFAULT_SEARXNG_HOST,
    DEFAULT_SEARXNG_PORT,
    searxng_runtime_config,
)
from agent.tools.web.searxng_runtime.docker import (
    SearXNGRuntimeError,
    ensure_searxng_runtime,
    has_matching_searxng_runtime,
    remove_searxng_runtime,
)
from agent.tools.web.searxng_runtime.ports import is_port_free, suggest_next_port

__all__ = [
    "DEFAULT_SEARXNG_HOST",
    "DEFAULT_SEARXNG_PORT",
    "SearXNGRuntimeError",
    "ensure_searxng_runtime",
    "has_matching_searxng_runtime",
    "is_port_free",
    "remove_searxng_runtime",
    "searxng_runtime_config",
    "suggest_next_port",
]
