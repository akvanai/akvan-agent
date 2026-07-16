"""SQLite session identity and message persistence coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.event_log import log_session
from agent.logging_setup import set_session_context
from agent.messages import Message
from agent.storage.store import SESSION_PAGE_SIZE, SessionStore


@dataclass
class PersistenceCoordinator:
    """Owns SQLite session identity and incremental message sync."""

    store: SessionStore | None
    session_id: str
    session_source: str = "cli"
    _persisted_message_count: int = field(default=0, repr=False)
    _session_persisted: bool = field(default=False, repr=False)
    _sessions_page: int = field(default=1, repr=False)
    _sessions_page_rows: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def ensure_persisted(self, *, model: str, provider: str, cwd: str) -> None:
        """Create the SQLite session row on first successful turn."""
        if self.store is None or self._session_persisted:
            return
        if self.store.session_exists(self.session_id):
            self._session_persisted = True
            return
        self.store.create_session(
            self.session_id,
            source=self.session_source,
            model=model,
            provider=provider,
            cwd=cwd,
        )
        self._session_persisted = True

    def persist_new_messages(self, messages: list[Message], *, model: str, provider: str, cwd: str) -> None:
        """Write any in-memory messages not yet stored to SQLite."""
        if self.store is None:
            return
        if self._persisted_message_count >= len(messages):
            return
        self.ensure_persisted(model=model, provider=provider, cwd=cwd)
        self.store.sync_messages(
            self.session_id,
            messages,
            start_index=self._persisted_message_count,
        )
        self._persisted_message_count = len(messages)

    def replace_messages(
        self,
        messages: list[Message],
        *,
        model: str,
        provider: str,
        cwd: str,
    ) -> None:
        """Persist a compacted transcript atomically and reset the append cursor."""

        if self.store is None:
            self._persisted_message_count = len(messages)
            return
        self.ensure_persisted(model=model, provider=provider, cwd=cwd)
        self.store.replace_messages(self.session_id, messages)
        self._persisted_message_count = len(messages)

    def fetch_sessions_page(
        self, page: int
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Load a page of saved sessions and cache it for `/resume <number>`."""
        if self.store is None:
            self._sessions_page = 1
            self._sessions_page_rows = []
            return [], 1, 1, 0

        exclude_session_id = self.session_id if self._session_persisted else None
        total_count = self.store.count_sessions(exclude_session_id=exclude_session_id)
        total_pages = max(
            1, (total_count + SESSION_PAGE_SIZE - 1) // SESSION_PAGE_SIZE
        )
        current_page = max(1, min(page, total_pages))
        offset = (current_page - 1) * SESSION_PAGE_SIZE
        rows = self.store.list_sessions(
            limit=SESSION_PAGE_SIZE,
            offset=offset,
            exclude_session_id=exclude_session_id,
        )
        self._sessions_page = current_page
        self._sessions_page_rows = rows
        return rows, current_page, total_pages, total_count

    def resolve_resume_target(self, target: str) -> str | None:
        """Resolve `/resume` target from list number, id prefix, or title."""
        if self.store is None:
            return None
        if target.isdigit():
            if not self._sessions_page_rows:
                self.fetch_sessions_page(self._sessions_page)
            index = int(target)
            if 1 <= index <= len(self._sessions_page_rows):
                return str(self._sessions_page_rows[index - 1]["id"])
            return None
        return self.store.resolve_session_id(target)

    def load_messages(self, session_id: str) -> tuple[str | None, list[Message] | None]:
        """Load stored messages by session id.

        Returns ``(error, None)`` on failure or ``(None, messages)`` on success.
        """
        if self.store is None:
            return "Session database not available.", None
        stored = self.store.get_messages(session_id)
        if not stored:
            return f"Session not found: {session_id}", None
        return None, stored

    def mark_loaded(self, session_id: str, message_count: int) -> None:
        self.session_id = session_id
        self._persisted_message_count = message_count
        self._session_persisted = True
        set_session_context(session_id)

    def end(self) -> None:
        """Mark the current session ended in persistent storage."""
        if self.store is not None and self._session_persisted:
            self.store.end_session(self.session_id)


__all__ = ["PersistenceCoordinator"]
