"""CLI bootstrap and non-interactive application flow."""

from __future__ import annotations

import argparse

from rich.console import Console

from agent.agent import AgentLoopError, DEFAULT_MAX_ITERATIONS
from agent.config import load_settings
from agent.providers import build_provider
from agent.providers.base import ProviderError
from agent.session import AgentSession
from agent.ui.commands import SessionCommandKind, resolve_input
from agent.ui.chat import render_streaming_response, run_interactive_session
from agent.ui.rendering import (
    Spinner,
    ask_user,
    print_error,
    render_compact_header,
    render_header,
    render_markdown_message,
    render_user_message,
)
from agent.ui.setup import run_model_setup
from agent.ui.gateway_setup import run_gateway
from agent.ui.tools_setup import run_tools_setup
from agent.skills.sync import sync_bundled_skills


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Akvan Agent CLI chat loop.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Maximum agent iterations per user turn. Defaults to {DEFAULT_MAX_ITERATIONS}.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Skip ordinary approvals; catastrophic commands remain blocked.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override AKVAN_MODEL for this session.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "model",
        help="Choose or reconfigure the model provider and model.",
    )
    skills = commands.add_parser("skills", help="Manage Akvan skills.")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    sync_parser = skills_sub.add_parser(
        "sync",
        help="Copy bundled skills from the app into ~/.akvan/skills/.",
    )
    sync_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the sync summary unless skills changed.",
    )
    gateway = commands.add_parser(
        "gateway",
        help="Configure, activate, and run messaging gateways in the background.",
    )
    gateway.add_argument(
        "--yolo",
        action="store_true",
        help="Skip ordinary approvals; catastrophic commands remain blocked.",
    )
    gateway.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Maximum agent iterations per user turn. Defaults to {DEFAULT_MAX_ITERATIONS}.",
    )
    commands.add_parser(
        "tools",
        help="Configure web search and extract providers.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "model":
        return run_model_setup(console)

    if args.command == "gateway":
        return run_gateway(
            console,
            yolo=args.yolo,
            max_iterations=args.max_iterations,
        )

    if args.command == "tools":
        return run_tools_setup(console)

    if args.command == "skills" and args.skills_command == "sync":
        summary = sync_bundled_skills(quiet=args.quiet)
        if args.quiet and not any(
            (summary.added, summary.updated, summary.skipped)
        ):
            return 0
        if not args.quiet:
            return 0
        if summary.added or summary.updated:
            print(f"Added: {', '.join(summary.added) or 'none'}")
            print(f"Updated: {', '.join(summary.updated) or 'none'}")
        return 0

    try:
        settings = load_settings()
        model = args.model or settings.model
        provider = build_provider(settings)
        session = AgentSession.create(
            provider=provider,
            model=model,
            max_iterations=args.max_iterations,
            approval_mode=settings.approval_mode,
            approval_timeout=settings.approval_timeout,
            terminal_timeout=settings.terminal_timeout,
            yolo=args.yolo,
        )
    except ValueError as exc:
        print_error(console, f"[bold #ff0000]Configuration error:[/] {exc}")
        return 2
    except ProviderError as exc:
        print_error(console, f"[bold #ff0000]Provider error:[/] {exc}")
        return 2

    if console.is_terminal:
        render_compact_header(
            console,
            provider_name=provider.name,
            model=model,
            max_iterations=args.max_iterations,
            tools=session.tools,
            skills=tuple(session.snapshot.skills.skills.values()),
            cwd=session.prompt_builder.cwd,
            enabled_toolsets=session.enabled_toolsets,
        )
    else:
        render_header(
            console,
            provider_name=provider.name,
            model=model,
            max_iterations=args.max_iterations,
            tools=session.tools,
            skills=tuple(session.snapshot.skills.skills.values()),
        )

    try:
        if console.is_terminal:
            return run_interactive_session(
                console,
                session=session,
            )

        transcript: list[tuple[str, str]] = []
        while True:
            try:
                console.print()
                user_input = ask_user(
                    console,
                    model=model,
                    provider_name=provider.name,
                    max_iterations=args.max_iterations,
                    transcript=transcript,
                    session=session,
                ).strip()
            except KeyboardInterrupt:
                console.print()
                return 0
            except EOFError:
                console.print()
                return 0

            if not user_input:
                continue
            command = resolve_input(session, user_input)
            if command.kind == SessionCommandKind.EXIT:
                session.end()
                return 0
            if command.kind == SessionCommandKind.RELOAD:
                snapshot = session.reload()
                render_user_message(console, user_input)
                render_markdown_message(
                    console,
                    "AKVAN",
                    f"Prompt reloaded (`{snapshot.fingerprint[:12]}`). "
                    f"{len(snapshot.skills.skills)} skills available.",
                )
                continue
            if command.kind == SessionCommandKind.RESUME:
                render_user_message(console, user_input)
                error = session.resume(command.message or "")
                if error:
                    render_markdown_message(console, "AKVAN", error)
                else:
                    render_markdown_message(
                        console,
                        "AKVAN",
                        f"Resumed session `{session.session_id[:8]}` "
                        f"({len(session.messages) - 1} messages loaded).",
                    )
                continue
            if command.kind in {
                SessionCommandKind.SKILLS,
                SessionCommandKind.SESSIONS,
                SessionCommandKind.YOLO,
                SessionCommandKind.ERROR,
            }:
                render_user_message(console, user_input)
                render_markdown_message(console, "AKVAN", command.message or "")
                continue

            transcript.append(("user", user_input))
            render_user_message(console, user_input)

            try:
                console.print(Spinner().render())
                answer = render_streaming_response(
                    console,
                    session.loop,
                    session.messages,
                    command.raw_input,
                    turn_context=command.turn_context,
                )
            except KeyboardInterrupt:
                console.print()
                return 0
            except (AgentLoopError, ProviderError) as exc:
                print_error(console, f"[bold #ff0000]Error:[/] {exc}")
                continue

            session.persist_new_messages()
            transcript.append(("assistant", answer))
    finally:
        provider.close()


