"""Resume and sessions slash-command tests."""

from __future__ import annotations

from pathlib import Path

from agent.messages import Completion
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.storage.store import SESSION_PAGE_SIZE, SessionStore
from agent.ui.commands import SessionCommandKind, resolve_input, sessions_markdown


class PromptFakeProvider(Provider):
    name = "fake"

    def complete(self, messages, model, options=None):
        return Completion(message={"role": "assistant", "content": "done"})


def test_sessions_command_lists_numbered_sessions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    store.create_session("saved-001", source="cli")
    store.append_message("saved-001", {"role": "user", "content": "hello there"})

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="current-002",
    )

    command = resolve_input(session, "/sessions")
    assert command.kind == SessionCommandKind.SESSIONS
    assert " 1  hello there" in (command.message or "")
    assert "/resume <number>" in (command.message or "")


def test_sessions_pagination(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    for index in range(SESSION_PAGE_SIZE + 1):
        session_id = f"sess-{index:03d}"
        store.create_session(session_id, source="cli")
        store.append_message(
            session_id,
            {"role": "user", "content": f"chat {index}"},
        )

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="current",
    )

    page_one = sessions_markdown(session, page=1)
    assert "Page 1 of 2" in page_one
    assert f" 1  chat {SESSION_PAGE_SIZE}" in page_one

    page_two = sessions_markdown(session, page=2)
    assert "Page 2 of 2" in page_two
    assert " 1  chat 0" in page_two


def test_sessions_markdown_sanitizes_multiline_preview(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    store.create_session("multiline-chat", source="cli")
    store.append_message(
        "multiline-chat",
        {"role": "user", "content": "Find the directory\n\ni wanna i... (91 messages"},
    )

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="current",
    )

    rendered = sessions_markdown(session, page=1)
    code_block = rendered.split("```text\n", 1)[1].split("\n```", 1)[0]
    assert code_block.count("\n") == 0
    assert "Find the directory i wanna i" in code_block


def test_resume_without_args_is_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )

    command = resolve_input(session, "/resume")
    assert command.kind == SessionCommandKind.ERROR
    assert "requires" in (command.message or "")


def test_resume_by_number_uses_last_sessions_page(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    store.create_session("older-chat", source="cli")
    store.append_message("older-chat", {"role": "user", "content": "older"})
    store.create_session("newer-chat", source="cli")
    store.append_message("newer-chat", {"role": "user", "content": "newer question"})
    store.append_message(
        "newer-chat",
        {"role": "assistant", "content": "newer answer"},
    )

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="fresh-session",
    )

    resolve_input(session, "/sessions")
    command = resolve_input(session, "/resume 1")
    assert command.kind == SessionCommandKind.RESUME

    error = session.resume(command.message or "")
    assert error is None
    assert session.persistence.session_id == "newer-chat"
    assert session.messages[1]["content"] == "newer question"


def test_resume_by_number_on_page_two(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    for index in range(SESSION_PAGE_SIZE + 1):
        session_id = f"sess-{index:03d}"
        store.create_session(session_id, source="cli")
        store.append_message(
            session_id,
            {"role": "user", "content": f"chat {index}"},
        )

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="fresh-session",
    )

    resolve_input(session, "/sessions 2")
    error = session.resume("1")
    assert error is None
    assert session.persistence.session_id == "sess-000"
