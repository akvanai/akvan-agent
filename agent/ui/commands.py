"""Parsing and rendering of local slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from enum import Enum
from typing import TYPE_CHECKING

from agent.messages import TurnContext
from agent.storage.store import SESSION_PAGE_SIZE

if TYPE_CHECKING:
    from agent.session import AgentSession


class SessionCommandKind(str, Enum):
    TURN = "turn"
    EXIT = "exit"
    YOLO = "yolo"
    RELOAD = "reload"
    SKILLS = "skills"
    SESSIONS = "sessions"
    RESUME = "resume"
    ERROR = "error"


@dataclass(frozen=True)
class SessionCommand:
    kind: SessionCommandKind
    raw_input: str
    turn_context: TurnContext | None = None
    message: str | None = None


def resolve_input(session: "AgentSession", raw_input: str) -> SessionCommand:
    stripped = raw_input.strip()
    command_token = stripped.partition(" ")[0]
    if command_token in {"/exit", "/quit", "/reload", "/skills", "/yolo"}:
        if stripped != command_token:
            return SessionCommand(
                SessionCommandKind.ERROR,
                raw_input,
                message=f"{command_token} does not accept arguments.",
            )
        if command_token in {"/exit", "/quit"}:
            return SessionCommand(SessionCommandKind.EXIT, raw_input)
        if command_token == "/reload":
            return SessionCommand(SessionCommandKind.RELOAD, raw_input)
        if command_token == "/yolo":
            enabled = session.approval_manager.toggle_yolo()
            state = "enabled" if enabled else "disabled"
            return SessionCommand(
                SessionCommandKind.YOLO,
                raw_input,
                message=f"YOLO mode {state} for this session.",
            )
        return SessionCommand(
            SessionCommandKind.SKILLS,
            raw_input,
            message=skills_markdown(session),
        )
    if command_token == "/sessions":
        _, _, page_arg = stripped.partition(" ")
        page_arg = page_arg.strip()
        if page_arg:
            page = parse_sessions_page(session, page_arg)
            if page is None:
                return SessionCommand(
                    SessionCommandKind.ERROR,
                    raw_input,
                    message=(
                        "Invalid /sessions page. Use a number, `next`, or `prev`."
                    ),
                )
        else:
            page = 1
        return SessionCommand(
            SessionCommandKind.SESSIONS,
            raw_input,
            message=sessions_markdown(session, page=page),
        )
    if command_token == "/resume":
        _, _, target = stripped.partition(" ")
        target = target.strip()
        if not target:
            return SessionCommand(
                SessionCommandKind.ERROR,
                raw_input,
                message=(
                    "/resume requires a number from `/sessions` "
                    "(run `/sessions` first)."
                ),
            )
        return SessionCommand(
            SessionCommandKind.RESUME,
            raw_input,
            message=target,
        )
    if not stripped.startswith("/"):
        return SessionCommand(SessionCommandKind.TURN, raw_input)

    command, _, request = stripped.partition(" ")
    skill_name = command[1:]
    skill = session.snapshot.skills.get(skill_name)
    if skill is not None:
        user_request = request.strip() or f"Activate the {skill_name} skill for this turn."
        provider_content = (
            session.snapshot.skills.view(skill_name)
            + "\n\n# User Request\n\n"
            + user_request
        )
        return SessionCommand(
            SessionCommandKind.TURN,
            raw_input,
            turn_context=TurnContext(provider_user_content=provider_content),
        )

    names = list(session.snapshot.skills.skills)
    suggestions = get_close_matches(skill_name, names, n=3, cutoff=0.5)
    suggestion_text = ", ".join("/" + name for name in suggestions)
    suffix = f" Did you mean: {suggestion_text}?" if suggestions else ""
    return SessionCommand(
        SessionCommandKind.ERROR,
        raw_input,
        message=f"Unknown command or skill /{skill_name}.{suffix}",
    )


def parse_sessions_page(session: "AgentSession", page_arg: str) -> int | None:
    """Parse `/sessions` page argument. Returns None when invalid."""
    normalized = page_arg.strip().lower()
    if normalized == "next":
        return session._sessions_page + 1
    if normalized in {"prev", "previous"}:
        return max(1, session._sessions_page - 1)
    if normalized.isdigit():
        return int(normalized)
    return None


def skills_markdown(session: "AgentSession") -> str:
    lines = ["## Available skills", ""]
    if session.snapshot.skills.skills:
        by_category: dict[str, list] = {}
        for skill in session.snapshot.skills.skills.values():
            by_category.setdefault(skill.category, []).append(skill)
        for category in sorted(by_category):
            lines.append(f"### {category}")
            lines.append("")
            for skill in sorted(by_category[category], key=lambda item: item.name):
                lines.append(
                    f"- `/{skill.name}` — {skill.description} ({skill.origin})"
                )
            lines.append("")
        if lines[-1] == "":
            lines.pop()
    else:
        lines.append("No skills are currently available.")
    if session.snapshot.skills.warnings:
        lines.extend(("", "### Discovery warnings", ""))
        lines.extend(f"- {warning}" for warning in session.snapshot.skills.warnings)
    return "\n".join(lines)


def sessions_markdown(session: "AgentSession", *, page: int = 1) -> str:
    if session.store is None:
        return "Session database not available."

    rows, current_page, total_pages, total_count = session.fetch_sessions_page(page)
    lines = ["## Saved sessions", ""]
    if not rows:
        lines.append("No saved sessions yet.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        title = row.get("title") or row.get("preview") or "(untitled)"
        started = datetime.fromtimestamp(float(row["started_at"])).strftime(
            "%Y-%m-%d %H:%M"
        )
        status = "ended" if row.get("ended_at") else "active"
        count = int(row.get("message_count") or 0)
        lines.append(f"{index}. {title} ({count} messages, {status}, {started})")

    lines.append("")
    lines.append(
        f"Page {current_page} of {total_pages} ({total_count} sessions)"
    )
    if current_page < total_pages:
        lines.append("`/sessions next` or `/sessions <page>` for more.")
    if current_page > 1:
        lines.append("`/sessions prev` for the previous page.")
    lines.append("Resume with `/resume <number>` using a number from this list.")
    return "\n".join(lines)
