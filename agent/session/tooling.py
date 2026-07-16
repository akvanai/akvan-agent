"""Tool registry and approval coordination for a session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agent.memory.store import MemoryStore
from agent.knowledge.store import KnowledgeStore
from agent.skills import SkillRegistry
from agent.storage.store import SessionStore
from agent.tools.approval import ApprovalManager
from agent.tools.base import Tool
from agent.tools.process_manager import ProcessManager
from agent.tools.registry import ToolRegistry, build_registry
from agent.tools.session_search_tools import SessionSearchContext


@dataclass
class ToolCoordinator:
    """Owns tool registration, approval policy, and resolved tool lists."""

    base_tools: tuple[Tool, ...]
    enabled_toolsets: tuple[str, ...]
    terminal_timeout: int
    approval_manager: ApprovalManager
    process_manager: ProcessManager
    registry: ToolRegistry
    knowledge_store: KnowledgeStore | None
    _session_search_ctx: SessionSearchContext | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        base_tools: tuple[Tool, ...],
        enabled_toolsets: tuple[str, ...],
        terminal_timeout: int,
        approval_manager: ApprovalManager,
        process_manager: ProcessManager,
        store: SessionStore | None,
        session_id: str,
        skills: SkillRegistry,
        project_root,
        memory_store: MemoryStore | None,
        knowledge_store: KnowledgeStore | None,
        knowledge_user_messages: Callable[[], list[str]],
        on_skills_changed: Callable[[], None],
    ) -> "ToolCoordinator":
        session_search_ctx = (
            SessionSearchContext(
                store=store,
                current_session_id=lambda: session_id,
            )
            if store is not None
            else None
        )
        registry = build_registry(
            skills,
            project_root=project_root,
            process_manager=process_manager,
            terminal_timeout=terminal_timeout,
            base_tools=base_tools,
            memory_store=memory_store,
            knowledge_store=knowledge_store,
            knowledge_user_messages=knowledge_user_messages,
            session_search_ctx=session_search_ctx,
            on_skills_changed=on_skills_changed,
        )
        return cls(
            base_tools,
            enabled_toolsets,
            terminal_timeout,
            approval_manager,
            process_manager,
            registry,
            knowledge_store,
            session_search_ctx,
        )

    def rebuild_registry(
        self,
        skills: SkillRegistry,
        *,
        project_root,
        memory_store: MemoryStore | None,
        knowledge_user_messages: Callable[[], list[str]],
        on_skills_changed: Callable[[], None],
    ) -> None:
        self.registry = build_registry(
            skills,
            project_root=project_root,
            process_manager=self.process_manager,
            terminal_timeout=self.terminal_timeout,
            base_tools=self.base_tools,
            memory_store=memory_store,
            knowledge_store=self.knowledge_store,
            knowledge_user_messages=knowledge_user_messages,
            session_search_ctx=self._session_search_ctx,
            on_skills_changed=on_skills_changed,
        )

    def resolve_tools(self) -> tuple[Tool, ...]:
        return self.registry.resolve(self.enabled_toolsets)

    def update_session_search_id(self, getter: Callable[[], str]) -> None:
        if self._session_search_ctx is not None:
            self._session_search_ctx.current_session_id = getter


__all__ = ["ToolCoordinator"]
