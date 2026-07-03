"""Prompt-toolkit completers for the Akvan chat input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from agent.session import AgentSession

STATIC_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/exit", "Quit"),
    ("/quit", "Quit"),
    ("/sessions", "List saved chats (paginated)"),
    ("/resume", "Resume a saved chat by list number"),
    ("/reload", "Reload prompt and skills"),
    ("/skills", "List available skills"),
    ("/yolo", "Toggle approval mode"),
)


class SlashCommandCompleter(Completer):
    """Suggest slash commands, skills, and resume numbers while typing."""

    def __init__(self, session: AgentSession) -> None:
        self._session = session

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

        for skill_name in sorted(self._session.snapshot.skills.skills):
            command = f"/{skill_name}"
            if command.startswith(prefix):
                skill = self._session.snapshot.skills.skills[skill_name]
                yield Completion(
                    command,
                    start_position=-len(prefix),
                    display_meta=skill.description,
                )

    def _resume_completions(self, text: str):
        arg_prefix = text[len("/resume ") :]
        rows = self._session._sessions_page_rows
        if not rows and self._session.store is not None:
            self._session.fetch_sessions_page(self._session._sessions_page)

        for index, row in enumerate(self._session._sessions_page_rows, start=1):
            label = str(index)
            if not label.startswith(arg_prefix):
                continue
            title = row.get("title") or row.get("preview") or "(untitled)"
            yield Completion(
                label,
                start_position=-len(arg_prefix),
                display_meta=str(title),
            )
