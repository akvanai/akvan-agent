"""Prompt snapshot coordination for a session."""

from __future__ import annotations

from dataclasses import dataclass

from agent.memory.config import MemoryConfig
from agent.memory.store import MemoryStore
from agent.messages import Message
from agent.prompts import PromptBuilder, PromptSnapshot
from agent.skills.config import SkillsConfig
from agent.skills import SkillRegistry
from agent.tools.base import Tool


@dataclass
class PromptCoordinator:
    """Owns the frozen system prompt snapshot and memory-backed prompt inputs."""

    builder: PromptBuilder
    memory_store: MemoryStore | None
    memory_config: MemoryConfig
    skills_config: SkillsConfig
    snapshot: PromptSnapshot

    def discover_skills(self) -> SkillRegistry:
        return self.builder.discover_skills()

    def reload_memory_from_disk(self) -> None:
        if self.memory_store is not None:
            self.memory_store.load_from_disk()

    def build_snapshot(
        self,
        *,
        model: str,
        provider: str,
        tools: tuple[Tool, ...],
        skills: SkillRegistry,
    ) -> PromptSnapshot:
        snapshot = self.builder.build(
            model=model,
            provider=provider,
            skills=skills,
            tools=tools,
            memory_store=self.memory_store,
            memory_config=self.memory_config,
        )
        self.snapshot = snapshot
        return snapshot

    def apply_system_message(self, messages: list[Message]) -> None:
        system_message: Message = {"role": "system", "content": self.snapshot.content}
        if messages and messages[0].get("role") == "system":
            messages[0] = system_message
        else:
            messages.insert(0, system_message)


__all__ = ["PromptCoordinator"]
