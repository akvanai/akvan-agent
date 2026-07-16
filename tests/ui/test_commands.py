"""Slash-command routing tests."""

from __future__ import annotations

from pathlib import Path

from agent.messages import Completion
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.ui.commands import SessionCommandKind, resolve_input


class PromptFakeProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.messages = []

    def complete(self, messages, model, options=None):
        self.messages = list(messages)
        return Completion(message={"role": "assistant", "content": "done"})


def test_session_commands_and_invalid_skill_warning(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    invalid = project / ".akvan" / "skills" / "bad"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )

    assert resolve_input(session, "/skills").kind == SessionCommandKind.SKILLS
    knowledge = resolve_input(session, "/knowledge")
    assert knowledge.kind == SessionCommandKind.KNOWLEDGE
    assert "Global knowledge" in (knowledge.message or "")
    assert resolve_input(session, "/reload").kind == SessionCommandKind.RELOAD
    usage = resolve_input(session, "/usage")
    assert usage.kind == SessionCommandKind.USAGE
    assert "Estimated request" in (usage.message or "")
    compressed = resolve_input(session, "/compress")
    assert compressed.kind == SessionCommandKind.COMPRESS
    assert "Context" in (compressed.message or "")
    focused = resolve_input(session, "/compress keep banner decisions")
    assert focused.kind == SessionCommandKind.COMPRESS
    learn = resolve_input(session, "/learn deploy staging")
    assert learn.kind == SessionCommandKind.TURN
    assert learn.turn_context is not None
    assert "deploy staging" in (learn.turn_context.provider_user_content or "")
    assert "SOURCE MODE: user_workflow" in (
        learn.turn_context.provider_user_content or ""
    )

    past_learn = resolve_input(session, "/learn from my last session")
    assert past_learn.kind == SessionCommandKind.ERROR
    assert "session persistence" in (past_learn.message or "").lower()

    assert resolve_input(session, "/reload now").kind == SessionCommandKind.ERROR
    assert resolve_input(session, "/missing").kind == SessionCommandKind.ERROR
    assert session.prompt.snapshot.skills.warnings
