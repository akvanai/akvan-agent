"""Session-scoped prompt, skill, tool, approval, and history orchestration."""

from __future__ import annotations

import copy
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from pathlib import Path

from agent.agent import AgentLoop
from agent.config import akvan_home
from agent.context import CompactionResult, load_context_config
from agent.event_log import log_session
from agent.learning.background_review import spawn_background_review
from agent.logging_setup import set_session_context
from agent.memory.config import MemoryConfig, load_memory_config
from agent.memory.store import MemoryStore
from agent.knowledge.config import load_knowledge_config
from agent.knowledge.review import persisted_review_batch, spawn_knowledge_review
from agent.knowledge.store import KnowledgeStore
from agent.messages import (
    Message,
    extract_message_text,
    parse_tool_result_content,
    tool_message_name,
)
from agent.prompts import PromptBuilder, PromptSnapshot
from agent.providers.base import Provider
from agent.session.persistence import PersistenceCoordinator
from agent.session.prompt import PromptCoordinator
from agent.session.tooling import ToolCoordinator
from agent.skills.config import SkillsConfig, load_skills_config
from agent.storage.store import SessionStore, open_session_store
from agent.tools.approval import ApprovalManager
from agent.tools.base import Tool
from agent.tools.process_manager import ProcessManager
from agent.tools.registry import BASE_TOOLS, default_enabled_toolsets

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """One process-local prompt, tools, approvals, and conversation snapshot."""

    provider: Provider
    model: str
    max_iterations: int
    loop: AgentLoop
    messages: list[Message]
    prompt: PromptCoordinator
    tooling: ToolCoordinator
    persistence: PersistenceCoordinator
    _turns_since_memory: int = field(default=0, repr=False)
    _user_turn_count: int = field(default=0, repr=False)
    _memory_review_pending: bool = field(default=False, repr=False)
    _iters_since_skill: int = field(default=0, repr=False)
    _skill_review_pending: bool = field(default=False, repr=False)
    _turns_since_knowledge: int = field(default=0, repr=False)
    _turn_state_snapshot: (
        tuple[int, int, bool, int, bool, int] | None
    ) = field(default=None, repr=False)
    _active_turn_messages: list[Message] | None = field(default=None, repr=False)

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
        memory_config = load_memory_config(project_root=builder.project_root)
        knowledge_config = load_knowledge_config()
        skills_config = load_skills_config(project_root=builder.project_root)
        context_config = load_context_config(project_root=builder.project_root)
        memory_store: MemoryStore | None = None
        if memory_config.memory_enabled or memory_config.user_profile_enabled:
            memory_store = MemoryStore(
                memory_char_limit=memory_config.memory_char_limit,
                user_char_limit=memory_config.user_char_limit,
            )
            memory_store.load_from_disk()
        knowledge_home = akvan_home()
        if not os.getenv("AKVAN_HOME") and builder.user_home != Path.home().resolve():
            knowledge_home = builder.user_home / ".akvan"
        knowledge_store = (
            KnowledgeStore(
                knowledge_config,
                root=knowledge_home / "knowledge",
                state_root=knowledge_home / "knowledge-state",
            )
            if knowledge_config.enabled
            else None
        )

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

        pending_session: list[AgentSession] = []

        def _skills_changed() -> None:
            if pending_session:
                pending_session[0].reload()

        def _knowledge_user_messages() -> list[str]:
            if not pending_session:
                return []
            return [
                str(message.get("content") or "")
                for message in pending_session[0].messages_for_tools()
                if message.get("role") == "user"
            ]

        tooling = ToolCoordinator.create(
            base_tools=base_tools,
            enabled_toolsets=resolved_toolsets,
            terminal_timeout=terminal_timeout,
            approval_manager=approvals,
            process_manager=process_manager,
            store=resolved_store,
            session_id=new_session_id,
            skills=skills,
            project_root=builder.project_root,
            memory_store=memory_store,
            knowledge_store=knowledge_store,
            knowledge_user_messages=_knowledge_user_messages,
            on_skills_changed=_skills_changed,
        )
        tools = tooling.resolve_tools()
        snapshot = builder.build(
            model=model,
            provider=provider.name,
            skills=skills,
            tools=tools,
            memory_store=memory_store,
            memory_config=memory_config,
        )
        prompt = PromptCoordinator(
            builder,
            memory_store,
            memory_config,
            skills_config,
            snapshot,
        )
        persistence = PersistenceCoordinator(
            resolved_store,
            new_session_id,
            session_source,
        )
        loop = AgentLoop(
            provider=provider,
            model=model,
            max_iterations=max_iterations,
            tools=tools,
            approval_manager=approvals,
            context_config=context_config,
            result_store_root=knowledge_home / "tmp" / "tool-results",
            session_id=new_session_id,
        )

        session = cls(
            provider,
            model,
            max_iterations,
            loop,
            [{"role": "system", "content": snapshot.content}],
            prompt,
            tooling,
            persistence,
        )
        pending_session.append(session)
        loop.compaction_callback = session._persist_compacted_messages
        tooling.update_session_search_id(session._current_session_id)
        set_session_context(new_session_id)
        log_session(f"session started source={session_source} id={new_session_id[:8]}")
        return session

    def _persist_compacted_messages(self, messages: list[Message]) -> None:
        self.persistence.replace_messages(
            messages,
            model=self.model,
            provider=self.provider.name,
            cwd=str(self.prompt.builder.cwd),
        )

    def messages_for_tools(self) -> list[Message]:
        """Expose the active private transcript to session-aware tools."""

        return (
            self._active_turn_messages
            if self._active_turn_messages is not None
            else self.messages
        )

    def turn_messages(self) -> list[Message]:
        """Return an isolated transcript for an in-flight turn."""

        messages = copy.deepcopy(self.messages)
        self._active_turn_messages = messages
        return messages

    def commit_turn_messages(self, messages: list[Message]) -> None:
        """Atomically publish and persist a successfully completed turn."""

        self.messages[:] = messages
        self._active_turn_messages = None
        self._persist_compacted_messages(self.messages)

    @staticmethod
    def latest_turn_start(messages: list[Message]) -> int:
        """Return the latest user-message index after any auto-compaction."""

        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index
        return len(messages)

    def compact_context(self, focus: str | None = None) -> CompactionResult:
        """Compact the live context and atomically persist the result."""

        return self.loop.compact_context(
            self.messages, force=True, focus=focus
        )

    def context_usage_markdown(self) -> str:
        usage = self.loop.context_usage(self.messages)
        deferred = self.loop.deferred_tool_count
        return (
            "## Context usage\n\n"
            f"- Estimated request: **{usage.estimated_total:,} tokens** "
            f"({usage.percentage:.1f}% of {usage.context_length:,})\n"
            f"- Messages: {usage.messages:,} tokens\n"
            f"- Visible tool schemas: {usage.tool_schemas:,} tokens\n"
            f"- Reserved output: {usage.reserved_output:,} tokens\n"
            f"- Automatic compaction threshold: {usage.threshold:,} tokens\n"
            f"- Deferred specialized tools: {deferred}"
        )

    def _current_session_id(self) -> str:
        return self.persistence.session_id

    def reload(self) -> PromptSnapshot:
        self.prompt.reload_memory_from_disk()
        skills = self.prompt.discover_skills()
        self.tooling.rebuild_registry(
            skills,
            project_root=self.prompt.builder.project_root,
            memory_store=self.prompt.memory_store,
            knowledge_user_messages=lambda: [
                extract_message_text(message.get("content"))
                for message in self.messages_for_tools()
                if message.get("role") == "user"
            ],
            on_skills_changed=self.reload,
        )
        tools = self.tooling.resolve_tools()
        snapshot = self.prompt.build_snapshot(
            model=self.model,
            provider=self.provider.name,
            tools=tools,
            skills=skills,
        )
        self.prompt.apply_system_message(self.messages)
        self.loop.set_tools(tools)
        return snapshot

    def begin_turn(self) -> None:
        """Prepare per-turn memory nudge state."""
        if self._turn_state_snapshot is None:
            self._turn_state_snapshot = (
                self._turns_since_memory,
                self._user_turn_count,
                self._memory_review_pending,
                self._iters_since_skill,
                self._skill_review_pending,
                self._turns_since_knowledge,
            )
        knowledge_store = self.tooling.knowledge_store
        if (
            knowledge_store is not None
            and knowledge_store.config.review_interval > 0
            and self.persistence.store is None
        ):
            self._turns_since_knowledge += 1
        if self.prompt.memory_store is not None:
            self.prompt.memory_store.reset_consolidation_failures()
        if (
            self.prompt.memory_store is None
            or self.prompt.memory_config.nudge_interval <= 0
            or "memory" not in {tool.name for tool in self.loop.tools}
        ):
            return
        if self._user_turn_count == 0 and len(self.messages) > 1:
            prior_user_turns = sum(
                1 for message in self.messages if message.get("role") == "user"
            )
            if prior_user_turns > 0:
                self._turns_since_memory = (
                    prior_user_turns % self.prompt.memory_config.nudge_interval
                )
        self._user_turn_count += 1
        self._turns_since_memory += 1
        if self._turns_since_memory >= self.prompt.memory_config.nudge_interval:
            self._memory_review_pending = True
            self._turns_since_memory = 0

    def complete_turn(self) -> None:
        """Commit session-local counters for the completed turn."""

        self._active_turn_messages = None
        self._turn_state_snapshot = None

    def cancel_turn(self) -> None:
        """Restore session-local counters after an interrupted turn."""

        self._active_turn_messages = None
        if self._turn_state_snapshot is None:
            return
        (
            self._turns_since_memory,
            self._user_turn_count,
            self._memory_review_pending,
            self._iters_since_skill,
            self._skill_review_pending,
            self._turns_since_knowledge,
        ) = self._turn_state_snapshot
        self._turn_state_snapshot = None

    def on_memory_tool_success(self) -> None:
        self._turns_since_memory = 0
        self._memory_review_pending = False

    def scan_turn_for_memory_tool_use(self, start_index: int) -> None:
        for message in self.messages[start_index:]:
            if message.get("role") != "tool" or tool_message_name(message) != "memory":
                continue
            payload = parse_tool_result_content(message.get("content"))
            if payload is not None and payload.get("success") is True:
                self.on_memory_tool_success()
                return

    def on_skill_manage_success(self) -> None:
        self._iters_since_skill = 0
        self._skill_review_pending = False

    def record_turn_tool_iterations(self, count: int) -> None:
        if self.prompt.skills_config.creation_nudge_interval <= 0:
            return
        tool_names = {tool.name for tool in self.loop.tools}
        if "skill_manage" not in tool_names:
            return
        if count <= 0:
            return
        self._iters_since_skill += count
        if self._iters_since_skill >= self.prompt.skills_config.creation_nudge_interval:
            self._skill_review_pending = True
            self._iters_since_skill = 0

    @staticmethod
    def count_turn_tool_iterations(messages: list[Message], start_index: int) -> int:
        count = 0
        for message in messages[start_index:]:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                count += 1
        return count

    def scan_turn_for_skill_tool_use(self, start_index: int) -> None:
        for message in self.messages[start_index:]:
            if message.get("role") != "tool" or tool_message_name(message) != "skill_manage":
                continue
            payload = parse_tool_result_content(message.get("content"))
            if payload is not None and payload.get("success") is True:
                self.on_skill_manage_success()
                return

    def maybe_spawn_background_review(
        self,
        *,
        interrupted: bool = False,
        on_complete: Callable[[str | None], None] | None = None,
    ) -> None:
        if interrupted:
            return
        review_memory = self._memory_review_pending
        review_skills = self._skill_review_pending
        knowledge_store = self.tooling.knowledge_store
        knowledge_batch: tuple[int | None, list[Message]] | None = None
        if knowledge_store is not None and knowledge_store.config.review_interval > 0:
            persisted = persisted_review_batch(
                self.persistence.store,
                knowledge_store,
            )
            if persisted is not None:
                knowledge_batch = persisted
            elif (
                self.persistence.store is None
                and self._turns_since_knowledge >= knowledge_store.config.review_interval
            ):
                knowledge_batch = (
                    None,
                    [
                        dict(message)
                        for message in self.messages
                        if message.get("role") in {"user", "assistant"}
                    ],
                )
                self._turns_since_knowledge = 0
        if review_memory:
            if self.prompt.memory_store is None or not (
                self.prompt.memory_config.memory_enabled
                or self.prompt.memory_config.user_profile_enabled
            ):
                review_memory = False
        if review_memory or review_skills:
            self._memory_review_pending = False
            self._skill_review_pending = False
            spawn_background_review(
                provider=self.provider,
                model=self.model,
                memory_store=self.prompt.memory_store,
                memory_config=self.prompt.memory_config,
                messages_snapshot=list(self.messages),
                review_memory=review_memory,
                review_skills=review_skills,
                on_complete=on_complete,
            )
        if knowledge_batch is not None and knowledge_store is not None:
            high_water, messages = knowledge_batch
            spawn_knowledge_review(
                provider=self.provider,
                model=self.model,
                knowledge_store=knowledge_store,
                messages_snapshot=messages,
                high_water_message_id=high_water,
                on_complete=on_complete,
            )

    def maybe_spawn_memory_review(
        self,
        *,
        interrupted: bool = False,
        on_complete: Callable[[str | None], None] | None = None,
    ) -> None:
        """Backward-compatible alias."""
        self.maybe_spawn_background_review(
            interrupted=interrupted,
            on_complete=on_complete,
        )

    def persist_new_messages(self) -> None:
        """Write any in-memory messages not yet stored to SQLite."""
        self.persistence.persist_new_messages(
            self.messages,
            model=self.model,
            provider=self.provider.name,
            cwd=str(self.prompt.builder.cwd),
        )

    def fetch_sessions_page(
        self, page: int
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Load a page of saved sessions and cache it for `/resume <number>`."""
        return self.persistence.fetch_sessions_page(page)

    def resolve_resume_target(self, target: str) -> str | None:
        """Resolve `/resume` target from list number, id prefix, or title."""
        return self.persistence.resolve_resume_target(target)

    def resume(self, target: str) -> str | None:
        """Load a stored session into this process-local session.

        Returns an error message on failure, or None on success.
        """
        if self.persistence.store is None:
            return "Session database not available."
        session_id = self.persistence.resolve_resume_target(target)
        if session_id is None:
            if target.isdigit():
                return (
                    f"Session number {target} is not on the current "
                    f"`/sessions` page. Run `/sessions` first."
                )
            return f"Session not found: {target}"
        error, stored = self.persistence.load_messages(session_id)
        if error is not None:
            return error
        assert stored is not None
        self.messages = stored
        self.reload()
        self.persistence.mark_loaded(session_id, len(self.messages))
        self.loop.update_session_id(session_id)
        self._user_turn_count = 0
        log_session(f"session resumed id={session_id[:8]} messages={len(self.messages)}")
        return None

    def load_persisted(self, session_id: str) -> str | None:
        """Load an existing session by id from the store.

        Returns an error message on failure, or None on success.
        """
        error, stored = self.persistence.load_messages(session_id)
        if error is not None:
            return error
        assert stored is not None
        self.messages = stored
        self.reload()
        self.persistence.mark_loaded(session_id, len(self.messages))
        self.loop.update_session_id(session_id)
        self._user_turn_count = 0
        log_session(f"session loaded id={session_id[:8]} messages={len(self.messages)}")
        return None

    def end(self) -> None:
        """Mark the current session ended in persistent storage."""
        self.persistence.end()


__all__ = ["AgentSession"]
