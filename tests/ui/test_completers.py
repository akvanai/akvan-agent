"""Slash-command completer tests."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.document import Document

from agent.messages import Completion
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.storage.store import SessionStore
from agent.ui.completers import SlashCommandAutoSuggest, SlashCommandCompleter


class PromptFakeProvider(Provider):
    name = "fake"

    def complete(self, messages, model, options=None):
        return Completion(message={"role": "assistant", "content": "done"})


def test_completer_suggests_slash_commands(tmp_path: Path) -> None:
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
    completer = SlashCommandCompleter(session)
    completions = list(
        completer.get_completions(Document("/ses"), None)
    )
    labels = {completion.text for completion in completions}
    assert "/sessions" in labels


def test_completer_suggests_resume_numbers_from_cached_page(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = SessionStore(db_path=tmp_path / "state.db")
    store.create_session("listed-chat", source="cli")
    store.append_message("listed-chat", {"role": "user", "content": "listed title"})

    session = AgentSession.create(
        provider=PromptFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=store,
        session_id="current",
    )
    session.fetch_sessions_page(1)

    completer = SlashCommandCompleter(session)
    completions = list(
        completer.get_completions(Document("/resume "), None)
    )
    assert any(completion.text == "1" for completion in completions)


def test_completer_lists_all_commands_on_slash(tmp_path: Path) -> None:
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
    completer = SlashCommandCompleter(session)
    completions = list(completer.get_completions(Document("/"), None))
    labels = {completion.text for completion in completions}
    assert "/sessions" in labels
    assert "/skills" in labels


def test_auto_suggest_shows_command_suffix(tmp_path: Path) -> None:
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
    auto_suggest = SlashCommandAutoSuggest(session)
    suggestion = auto_suggest.get_suggestion(None, Document("/ses"))
    assert suggestion is not None
    assert suggestion.text == "sions"


def test_auto_suggest_skips_bare_slash(tmp_path: Path) -> None:
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
    auto_suggest = SlashCommandAutoSuggest(session)
    assert auto_suggest.get_suggestion(None, Document("/")) is None
