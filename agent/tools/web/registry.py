"""Built-in web provider registry."""

from __future__ import annotations

from agent.tools.web.provider import WebSearchProvider
from agent.tools.web.providers.content_extractor import ContentExtractorProvider
from agent.tools.web.providers.ddgs import DDGSProvider
from agent.tools.web.providers.searxng import SearXNGProvider

_PROVIDERS: dict[str, WebSearchProvider] = {
    "content_extractor": ContentExtractorProvider(),
    "searxng": SearXNGProvider(),
    "ddgs": DDGSProvider(),
}


def list_providers() -> tuple[WebSearchProvider, ...]:
    return tuple(_PROVIDERS.values())


def get_provider(name: str) -> WebSearchProvider | None:
    return _PROVIDERS.get(name)


def search_providers() -> tuple[WebSearchProvider, ...]:
    return tuple(p for p in _PROVIDERS.values() if p.supports_search())


def extract_providers() -> tuple[WebSearchProvider, ...]:
    return tuple(p for p in _PROVIDERS.values() if p.supports_extract())


def get_active_search_provider(*, project_root=None) -> WebSearchProvider | None:
    from agent.tools.web.config import get_search_backend, is_backend_available

    backend = get_search_backend(project_root=project_root)
    if backend:
        provider = get_provider(backend)
        if provider and provider.supports_search() and is_backend_available(backend):
            return provider
    for provider in search_providers():
        if provider.is_available():
            return provider
    return None


def get_active_extract_provider(*, project_root=None) -> WebSearchProvider | None:
    from agent.tools.web.config import get_extract_backend, is_backend_available

    backend = get_extract_backend(project_root=project_root)
    if backend:
        provider = get_provider(backend)
        if provider and provider.supports_extract() and is_backend_available(backend):
            return provider
    for provider in extract_providers():
        if provider.is_available():
            return provider
    return None
