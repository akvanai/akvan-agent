"""Prompt-toolkit completers for the Akvan chat input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.ui.commands import format_session_title
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from agent.session import AgentSession

STATIC_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/exit", "Quit"),
    ("/quit", "Quit"),
    ("/sessions", "List saved chats (paginated)"),
    ("/resume", "Resume a saved chat by list number"),
    ("/usage", "Show estimated context usage"),
    ("/compress", "Compact conversation history"),
    ("/learn", "Learn a reusable skill from sources"),
    ("/reload", "Reload prompt and skills"),
    ("/skills", "List available skills"),
    ("/knowledge", "View and review global knowledge"),
    ("/yolo", "Toggle approval mode"),
)


class SlashCommandCompleter(Completer):
    """Suggest slash commands, skills, and resume numbers while typing."""

    def __init__(self, session: AgentSession) -> None:
        self._session = session

    def matching_commands(self, document) -> list[str]:
        """Return sorted slash-command matches for the text before the cursor."""
        return sorted(
            {
                completion.text
                for completion in self.get_completions(document, None)
            }
        )

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        if text.startswith("/resume "):
            yield from self._resume_completions(text)
            return

        if " " in text:
            return

        prefix = text
        for command, description in STATIC_COMMANDS:
            if command.startswith(prefix):
                yield Completion(
                    command,
                    start_position=-len(prefix),
                    display_meta=description,
                )

        for skill_name in sorted(self._session.prompt.snapshot.skills.skills):
            command = f"/{skill_name}"
            if command.startswith(prefix):
                skill = self._session.prompt.snapshot.skills.skills[skill_name]
                yield Completion(
                    command,
                    start_position=-len(prefix),
                    display_meta=skill.description,
                )

    def _resume_completions(self, text: str):
        arg_prefix = text[len("/resume ") :]
        rows = self._session.persistence._sessions_page_rows
        if not rows and self._session.persistence.store is not None:
            self._session.fetch_sessions_page(self._session.persistence._sessions_page)

        for index, row in enumerate(self._session.persistence._sessions_page_rows, start=1):
            label = str(index)
            if not label.startswith(arg_prefix):
                continue
            title = row.get("title") or row.get("preview") or "(untitled)"
            yield Completion(
                label,
                start_position=-len(arg_prefix),
                display_meta=format_session_title(title),
            )


class SlashCommandAutoSuggest(AutoSuggest):
    """Show gray inline suffixes for slash commands while typing."""

    def __init__(self, session: AgentSession) -> None:
        self._completer = SlashCommandCompleter(session)

    def get_suggestion(self, buffer, document):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return None

        if text.startswith("/resume "):
            arg_prefix = text[len("/resume ") :]
            matches = [
                label
                for label in self._completer.matching_commands(document)
                if label.startswith(arg_prefix) and label != arg_prefix
            ]
            if not matches:
                return None
            best = matches[0]
            return Suggestion(best[len(arg_prefix) :])

        if " " in text or len(text) < 2:
            return None

        matches = [
            command
            for command in self._completer.matching_commands(document)
            if command.startswith(text) and command != text
        ]
        if not matches:
            return None
        return Suggestion(matches[0][len(text) :])
