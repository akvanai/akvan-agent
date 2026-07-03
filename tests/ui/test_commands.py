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
    assert resolve_input(session, "/reload").kind == SessionCommandKind.RELOAD
    assert resolve_input(session, "/reload now").kind == SessionCommandKind.ERROR
    assert resolve_input(session, "/missing").kind == SessionCommandKind.ERROR
    assert session.snapshot.skills.warnings

