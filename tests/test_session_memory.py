"""Session prompt memory injection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory.paths import memory_file, user_file
from agent.memory.store import ENTRY_DELIMITER, MemoryStore
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.skills import SkillRegistry


class FakeProvider(Provider):
    name = "fake"

    def complete(self, messages, model, options=None):
        from agent.messages import Completion

        return Completion(message={"role": "assistant", "content": "ok"})


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "akvan"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("AKVAN_HOME", str(home))
    memory_file().write_text(f"note one{ENTRY_DELIMITER}note two", encoding="utf-8")
    user_file().write_text("User name is Sam", encoding="utf-8")
    return home


def test_system_prompt_includes_memory_blocks(akvan_home: Path) -> None:
    project = akvan_home.parent / "project"
    project.mkdir()
    session = AgentSession.create(
        provider=FakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=akvan_home),
        store=None,
    )
    content = session.messages[0]["content"]
    assert isinstance(content, str)
    assert "note one" in content
    assert "User name is Sam" in content

    store = session.prompt.memory_store
    assert store is not None
    store.add("memory", "mid-session write")
    assert "mid-session write" not in content

    session.reload()
    reloaded = session.messages[0]["content"]
    assert isinstance(reloaded, str)
    assert "mid-session write" in reloaded
