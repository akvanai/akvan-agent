"""Session-scoped prompt, skill, tool, approval, and history orchestration."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.agent import AgentLoop
from agent.messages import Message
from agent.prompts import PromptBuilder, PromptSnapshot
from agent.providers.base import Provider
from agent.storage.store import SESSION_PAGE_SIZE, SessionStore, open_session_store
from agent.tools.approval import ApprovalManager
from agent.tools.base import Tool
from agent.tools.process_manager import ProcessManager
from agent.tools.registry import (
    BASE_TOOLS,
    ToolRegistry,
    build_registry,
    default_enabled_toolsets,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """One process-local prompt, tools, approvals, and conversation snapshot."""

    provider: Provider
    model: str
    max_iterations: int
    prompt_builder: PromptBuilder
    base_tools: tuple[Tool, ...]
    enabled_toolsets: tuple[str, ...]
    terminal_timeout: int
    approval_manager: ApprovalManager
    process_manager: ProcessManager
    registry: ToolRegistry
    loop: AgentLoop
    messages: list[Message]
    snapshot: PromptSnapshot
    session_id: str
    session_source: str = "cli"
    store: SessionStore | None = None
    _persisted_message_count: int = field(default=0, repr=False)
    _session_persisted: bool = field(default=False, repr=False)
    _sessions_page: int = field(default=1, repr=False)
    _sessions_page_rows: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        provider: Provider,
        model: str,
        max_iterations: int,
        prompt_builder: PromptBuilder | None = None,
        base_tools: tuple[Tool, ...] = BASE_TOOLS,
        enabled_toolsets: tuple[str, ...] | None = None,
        approval_mode: str = "ask",
        approval_timeout: int = 60,
        terminal_timeout: int = 120,
        yolo: bool = False,
        store: SessionStore | None | object = ...,
        session_id: str | None = None,
        session_source: str = "cli",
    ) -> "AgentSession":
        builder = prompt_builder or PromptBuilder()
        resolved_toolsets = (
            enabled_toolsets
            if enabled_toolsets is not None
            else default_enabled_toolsets(project_root=builder.project_root)
        )
        skills = builder.discover_skills()
        process_manager = ProcessManager()
        approvals = ApprovalManager(
            mode=approval_mode,
            timeout=approval_timeout,
            user_home=builder.user_home,
            yolo=yolo,
        )
        registry = build_registry(
            skills,
            project_root=builder.project_root,
            process_manager=process_manager,
            terminal_timeout=terminal_timeout,
            base_tools=base_tools,
        )
        tools = registry.resolve(resolved_toolsets)
        snapshot = builder.build(
            model=model,
            provider=provider.name,
            skills=skills,
            tools=tools,
        )
        loop = AgentLoop(
            provider=provider,
            model=model,
            max_iterations=max_iterations,
            tools=tools,
            approval_manager=approvals,
        )

        resolved_store: SessionStore | None
        if store is ...:
            resolved_store = open_session_store()
            if resolved_store is None:
                logger.warning(
                    "Session store unavailable — continuing without persistence."
                )
        else:
            resolved_store = store  # type: ignore[assignment]

        new_session_id = session_id or str(uuid.uuid4())

        return cls(
            provider,
            model,
            max_iterations,
            builder,
            base_tools,
            resolved_toolsets,
            terminal_timeout,
            approvals,
            process_manager,
            registry,
            loop,
            [{"role": "system", "content": snapshot.content}],
            snapshot,
            new_session_id,
            session_source,
            resolved_store,
            0,
        )

    @property
    def tools(self) -> tuple[Tool, ...]:
        return self.loop.tools

    def reload(self) -> PromptSnapshot:
        skills = self.prompt_builder.discover_skills()
        self.registry = build_registry(
            skills,
            project_root=self.prompt_builder.project_root,
            process_manager=self.process_manager,
            terminal_timeout=self.terminal_timeout,
            base_tools=self.base_tools,
        )
        tools = self.registry.resolve(self.enabled_toolsets)
        snapshot = self.prompt_builder.build(
            model=self.model,
            provider=self.provider.name,
            skills=skills,
            tools=tools,
        )
        self.snapshot = snapshot
        self.loop.set_tools(tools)
        system_message: Message = {"role": "system", "content": snapshot.content}
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = system_message
        else:
            self.messages.insert(0, system_message)
        return snapshot

    def ensure_persisted(self) -> None:
        """Create the SQLite session row on first successful turn."""
        if self.store is None or self._session_persisted:
            return
        if self.store.session_exists(self.session_id):
            self._session_persisted = True
            return
        self.store.create_session(
            self.session_id,
            source=self.session_source,
            model=self.model,
            provider=self.provider.name,
            cwd=str(self.prompt_builder.cwd),
        )
        self._session_persisted = True

    def persist_new_messages(self) -> None:
        """Write any in-memory messages not yet stored to SQLite."""
        if self.store is None:
            return
        if self._persisted_message_count >= len(self.messages):
            return
        self.ensure_persisted()
        self.store.sync_messages(
            self.session_id,
            self.messages,
            start_index=self._persisted_message_count,
        )
        self._persisted_message_count = len(self.messages)

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

    def resume(self, target: str) -> str | None:
        """Load a stored session into this process-local session.

        Returns an error message on failure, or None on success.
        """
        if self.store is None:
            return "Session database not available."
        session_id = self.resolve_resume_target(target)
        if session_id is None:
            if target.isdigit():
                return (
                    f"Session number {target} is not on the current "
                    f"`/sessions` page. Run `/sessions` first."
                )
            return f"Session not found: {target}"
        stored = self.store.get_messages(session_id)
        self.session_id = session_id
        self.messages = stored
        self.reload()
        self._persisted_message_count = len(self.messages)
        self._session_persisted = True
        return None

    def load_persisted(self, session_id: str) -> str | None:
        """Load an existing session by id from the store.

        Returns an error message on failure, or None on success.
        """
        if self.store is None:
            return "Session database not available."
        stored = self.store.get_messages(session_id)
        if not stored:
            return f"Session not found: {session_id}"
        self.session_id = session_id
        self.messages = stored
        self.reload()
        self._persisted_message_count = len(self.messages)
        self._session_persisted = True
        return None

    def end(self) -> None:
        """Mark the current session ended in persistent storage."""
        if self.store is not None and self._session_persisted:
            self.store.end_session(self.session_id)


__all__ = ["AgentSession"]
