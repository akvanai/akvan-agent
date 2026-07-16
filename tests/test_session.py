"""Cached agent session and reload tests."""

from __future__ import annotations

from pathlib import Path

from agent.messages import Completion
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.storage.store import SessionStore
from agent.ui.commands import SessionCommandKind, resolve_input


class PromptFakeProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.messages = []

    def complete(self, messages, model, options=None):
        self.messages = list(messages)
        return Completion(message={"role": "assistant", "content": "done"})


class SimpleFakeProvider(Provider):
    name = "fake"

    def complete(self, messages, model, options=None):
        return Completion(message={"role": "assistant", "content": "done"})


def write_skill(
    root: Path, category: str, name: str, description: str, body: str
) -> Path:
    skill_root = root / ".akvan" / "skills" / category / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_root


def test_session_explicit_skill_context_preserves_raw_history_and_reloads(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write_skill(project, "writing", "writer", "Project writer", "FOLLOW THIS WORKFLOW")
    provider = PromptFakeProvider()
    session = AgentSession.create(
        provider=provider,
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )

    command = resolve_input(session, "/writer draft this")
    assert command.kind == SessionCommandKind.TURN
    answer = session.loop.run_turn(
        session.messages,
        command.raw_input,
        turn_context=command.turn_context,
    )

    assert answer == "done"
    assert session.messages[1]["content"] == "/writer draft this"
    assert "FOLLOW THIS WORKFLOW" in provider.messages[1]["content"]
    old_fingerprint = session.prompt.snapshot.fingerprint
    (project / ".akvan.md").write_text("NEW PROJECT RULE", encoding="utf-8")
    session.reload()
    assert session.prompt.snapshot.fingerprint != old_fingerprint
    assert session.messages[1]["content"] == "/writer draft this"
    assert session.messages[0]["content"] == session.prompt.snapshot.content


def test_create_does_not_write_session_row(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")

    AgentSession.create(
        provider=SimpleFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
    )

    assert store.count_sessions() == 0


def test_first_turn_persist_creates_session_row(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    session = AgentSession.create(
        provider=SimpleFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
    )

    session.loop.run_turn(session.messages, "hello there")
    session.persist_new_messages()

    assert store.count_sessions() == 1
    loaded = store.get_messages(session.persistence.session_id)
    assert loaded[0]["content"] == "hello there"
    assert loaded[1]["content"] == "done"


def test_end_without_turn_leaves_no_session_row(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    session = AgentSession.create(
        provider=SimpleFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
    )

    session.end()

    assert store.count_sessions() == 0


def test_resume_marks_session_persisted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    store.create_session("saved-chat", source="cli")
    store.append_message("saved-chat", {"role": "user", "content": "older message"})

    session = AgentSession.create(
        provider=SimpleFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="ephemeral",
    )
    assert store.count_sessions() == 1

    resolve_input(session, "/sessions")
    error = session.resume("1")
    assert error is None
    assert session.persistence.session_id == "saved-chat"

    session.end()
    rows = store.list_sessions()
    saved = next(row for row in rows if row["id"] == "saved-chat")
    assert saved["ended_at"] is not None

