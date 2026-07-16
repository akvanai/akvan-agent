"""Abstract base for web search and extract backends."""

from __future__ import annotations

import abc
from typing import Any


class WebSearchProvider(abc.ABC):
    """Pluggable backend for ``web_search`` and ``web_extract``."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier used in config (e.g. ``searxng``)."""

    @property
    def display_name(self) -> str:
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider can service calls (no network I/O)."""

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} does not support search")

    def extract(self, urls: list[str], **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self.name} does not support extract")

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "",
            "tag": "",
            "env_vars": [],
        }
