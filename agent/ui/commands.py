"""Parsing and rendering of local slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from enum import Enum
from typing import TYPE_CHECKING

from agent.messages import TurnContext
from agent.skills.learn_prompt import (
    LearnSource,
    build_learn_prompt,
    classify_learn_source,
)
from agent.storage.store import SESSION_PAGE_SIZE

if TYPE_CHECKING:
    from agent.session import AgentSession


class SessionCommandKind(str, Enum):
    TURN = "turn"
    EXIT = "exit"
    YOLO = "yolo"
    RELOAD = "reload"
    SKILLS = "skills"
    KNOWLEDGE = "knowledge"
    SESSIONS = "sessions"
    RESUME = "resume"
    COMPRESS = "compress"
    USAGE = "usage"
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
    if command_token == "/knowledge":
        _, _, request = stripped.partition(" ")
        return SessionCommand(
            SessionCommandKind.KNOWLEDGE,
            raw_input,
            message=knowledge_markdown(session, request.strip()),
        )
    if command_token == "/usage":
        if stripped != command_token:
            return SessionCommand(
                SessionCommandKind.ERROR,
                raw_input,
                message="/usage does not accept arguments.",
            )
        return SessionCommand(
            SessionCommandKind.USAGE,
            raw_input,
            message=session.context_usage_markdown(),
        )
    if command_token == "/compress":
        _, _, focus = stripped.partition(" ")
        result = session.compact_context(focus.strip() or None)
        if result.changed:
            message = (
                "Context compacted: "
                f"{result.before_tokens:,} → {result.after_tokens:,} estimated "
                f"tokens; summarized {result.summarized_messages} messages and "
                f"pruned {result.pruned_results} tool results."
            )
        else:
            message = (
                "Context is already compact; no safe reduction was available "
                f"({result.after_tokens:,} estimated message tokens)."
            )
        return SessionCommand(
            SessionCommandKind.COMPRESS,
            raw_input,
            message=message,
        )
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
            enabled = session.tooling.approval_manager.toggle_yolo()
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
    if command_token == "/learn":
        _, _, learn_request = stripped.partition(" ")
        learn_request = learn_request.strip()
        tools_available = frozenset(tool.name for tool in session.loop.tools)
        if (
            classify_learn_source(learn_request) == LearnSource.PAST_SESSION
            and "session_search" not in tools_available
        ):
            return SessionCommand(
                SessionCommandKind.ERROR,
                raw_input,
                message=(
                    "/learn cannot search past sessions — session persistence is "
                    "unavailable. Run `/resume` to load a saved session first, or "
                    "point `/learn` at a file path or URL."
                ),
            )
        prior_user_turns = sum(
            1 for message in session.messages if message.get("role") == "user"
        )
        return SessionCommand(
            SessionCommandKind.TURN,
            raw_input,
            turn_context=TurnContext(
                provider_user_content=build_learn_prompt(
                    learn_request,
                    prior_user_turns=prior_user_turns,
                    tools_available=tools_available,
                )
            ),
        )
    if not stripped.startswith("/"):
        from agent.vision.attach import build_user_provider_content
        from agent.vision.user_images import extract_image_paths_from_text

        display, paths = extract_image_paths_from_text(stripped)
        if paths:
            return SessionCommand(
                SessionCommandKind.TURN,
                display,
                turn_context=TurnContext(
                    provider_user_content=build_user_provider_content(
                        display,
                        paths,
                        provider=session.provider,
                        model=session.model,
                    )
                ),
            )
        return SessionCommand(SessionCommandKind.TURN, raw_input)

    command, _, request = stripped.partition(" ")
    skill_name = command[1:]
    skill = session.prompt.snapshot.skills.get(skill_name)
    if skill is not None:
        from agent.skills.usage import bump_use

        bump_use(skill_name)
        user_request = request.strip() or f"Activate the {skill_name} skill for this turn."
        provider_content = (
            session.prompt.snapshot.skills.view(skill_name)
            + "\n\n# User Request\n\n"
            + user_request
        )
        return SessionCommand(
            SessionCommandKind.TURN,
            raw_input,
            turn_context=TurnContext(provider_user_content=provider_content),
        )

    names = list(session.prompt.snapshot.skills.skills)
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
        return session.persistence._sessions_page + 1
    if normalized in {"prev", "previous"}:
        return max(1, session.persistence._sessions_page - 1)
    if normalized.isdigit():
        return int(normalized)
    return None


def skills_markdown(session: "AgentSession") -> str:
    lines = ["## Available skills", ""]
    if session.prompt.snapshot.skills.skills:
        by_category: dict[str, list] = {}
        for skill in session.prompt.snapshot.skills.skills.values():
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
    if session.prompt.snapshot.skills.warnings:
        lines.extend(("", "### Discovery warnings", ""))
        lines.extend(f"- {warning}" for warning in session.prompt.snapshot.skills.warnings)
    return "\n".join(lines)


def knowledge_markdown(session: "AgentSession", request: str = "") -> str:
    store = session.tooling.knowledge_store
    if store is None:
        return "Knowledge is disabled in this session."
    parts = request.split()
    action = parts[0].lower() if parts else "status"
    proposal_id = parts[1] if len(parts) > 1 else ""
    try:
        if action == "status":
            status = store.status()
            subjects = ", ".join(status["subjects"]) or "None yet"
            lines = [
                "## Global knowledge",
                "",
                f"- Concepts: {status['concept_count']}",
                f"- Subjects: {subjects}",
                f"- Pending proposals: {status['pending_count']}",
                f"- Last review: {status['last_review_at'] or 'Not yet'}",
            ]
            if status["recent_updates"]:
                lines.extend(("", "### Recent updates", ""))
                lines.extend(f"- {item}" for item in status["recent_updates"])
            lines.extend(("", "Use `/knowledge pending` to review proposals."))
            return "\n".join(lines)
        if action == "pending":
            proposals = store.list_proposals()
            if not proposals:
                return "## Pending knowledge proposals\n\nNo pending proposals."
            lines = ["## Pending knowledge proposals", ""]
            lines.extend(
                f"- `{item['id']}` — {item['operation']} `{item['concept_id']}` ({item['confidence']})"
                for item in proposals
            )
            lines.extend(("", "Use `/knowledge show <id>`, `/knowledge approve <id>`, or `/knowledge reject <id>`."))
            return "\n".join(lines)
        if action in {"show", "approve", "reject"}:
            if not proposal_id:
                return f"`/knowledge {action}` requires a proposal ID."
            result = store.manage(action, proposal_id)
            if action == "show":
                proposal = result["proposal"]
                body = str(proposal.get("body") or "")[:4000]
                return (
                    f"## Knowledge proposal `{proposal_id}`\n\n"
                    f"- Operation: {proposal.get('operation')}\n"
                    f"- Concept: `{proposal.get('concept_id')}`\n"
                    f"- Confidence: {proposal.get('confidence')}\n\n"
                    f"```markdown\n{body}\n```"
                )
            return f"Knowledge proposal `{proposal_id}` {result['status']}."
        return "Unknown knowledge command. Use `/knowledge`, `pending`, `show`, `approve`, or `reject`."
    except (ValueError, OSError) as exc:
        return f"Knowledge error: {exc}"


SESSION_TITLE_MAX_LEN = 40


def format_session_title(
    value: object | None, *, max_len: int = SESSION_TITLE_MAX_LEN
) -> str:
    """Collapse whitespace and trim session titles/previews for display."""
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        return "(untitled)"
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


def format_session_line(index: int, row: dict[str, object]) -> str:
    title = format_session_title(row.get("title") or row.get("preview"))
    started = datetime.fromtimestamp(float(row["started_at"])).strftime(
        "%Y-%m-%d %H:%M"
    )
    status = "ended" if row.get("ended_at") else "active"
    count = int(row.get("message_count") or 0)
    return f"{index:2}  {title}  ({count} messages, {status}, {started})"


def sessions_markdown(session: "AgentSession", *, page: int = 1) -> str:
    if session.persistence.store is None:
        return "Session database not available."

    rows, current_page, total_pages, total_count = session.fetch_sessions_page(page)
    lines = ["## Saved sessions", ""]
    if not rows:
        lines.append("No saved sessions yet.")
        return "\n".join(lines)

    lines.append("```text")
    lines.extend(format_session_line(index, row) for index, row in enumerate(rows, start=1))
    lines.append("```")
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
