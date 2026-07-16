"""CLI bootstrap and non-interactive application flow."""

from __future__ import annotations

import argparse

from rich.console import Console

from agent.agent import AgentLoopError, DEFAULT_MAX_ITERATIONS
from agent.config import load_settings, load_setup_settings
from agent.logging_setup import setup_logging
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
from agent.ui.setup import needs_provider_setup, run_model_setup
from agent.ui.gateway_setup import run_gateway, run_gateway_restart
from agent.ui.logs import run_logs_with_args
from agent.ui.tools_setup import run_tools_setup
from agent.ui.uninstall import run_uninstall
from agent.skills.curator import (
    archive_stale,
    curator_status,
    format_status_report,
    pin_skill,
)
from agent.skills.sync import reset_bundled_skill, sync_bundled_skills
from agent.skills.usage import restore_skill


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
    reset_parser = skills_sub.add_parser(
        "reset",
        help="Reset bundled skill manifest tracking or restore from source.",
    )
    reset_parser.add_argument("name", help="Bundled skill name.")
    reset_parser.add_argument(
        "--restore",
        action="store_true",
        help="Delete local copy and re-copy the bundled version.",
    )
    curator = skills_sub.add_parser("curator", help="Manage agent-created skills.")
    curator_sub = curator.add_subparsers(dest="curator_command", required=True)
    curator_sub.add_parser("status", help="Show agent-created skill usage.")
    archive_parser = curator_sub.add_parser(
        "archive", help="Archive idle agent-created skills."
    )
    archive_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Idle threshold in days (default from config).",
    )
    restore_parser = curator_sub.add_parser(
        "restore", help="Restore an archived agent-created skill."
    )
    restore_parser.add_argument("name", help="Skill name.")
    pin_parser = curator_sub.add_parser("pin", help="Pin an agent-created skill.")
    pin_parser.add_argument("name", help="Skill name.")
    unpin_parser = curator_sub.add_parser("unpin", help="Unpin a skill.")
    unpin_parser.add_argument("name", help="Skill name.")
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
    gateway_sub = gateway.add_subparsers(dest="gateway_command")
    restart_parser = gateway_sub.add_parser(
        "restart",
        help="Restart running gateways to pick up code changes.",
    )
    restart_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output when no gateways are running.",
    )
    commands.add_parser(
        "tools",
        help="Configure web search and extract providers.",
    )
    uninstall = commands.add_parser(
        "uninstall",
        help="Remove Akvan Agent; use --purge to delete all ~/.akvan data.",
    )
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="Remove the application and all Akvan user data.",
    )
    uninstall.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompts.",
    )
    logs = commands.add_parser("logs", help="View Akvan log files.")
    logs.add_argument(
        "log_name",
        nargs="?",
        default="agent",
        help="Log to read: agent, errors, gateway, or list.",
    )
    logs.add_argument(
        "gateway_id",
        nargs="?",
        default=None,
        help="Gateway id when log_name is gateway (e.g. telegram).",
    )
    logs.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of recent lines to show (default: 50).",
    )
    logs.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output (Ctrl+C to stop).",
    )
    logs.add_argument(
        "--level",
        default=None,
        help="Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    logs.add_argument(
        "--session",
        default=None,
        help="Filter by session id substring.",
    )
    logs.add_argument(
        "--since",
        default=None,
        help="Show lines from the last duration (e.g. 1h, 30m, 2d).",
    )
    logs.add_argument(
        "--component",
        default=None,
        help="Filter by component: memory, skills, review, session, gateway, agent, tools.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command not in {"logs", "uninstall"}:
        setup_logging(mode="cli")

    if args.command == "logs":
        return run_logs_with_args(args)

    if args.command == "model":
        return run_model_setup(console)

    if args.command == "gateway":
        if getattr(args, "gateway_command", None) == "restart":
            return run_gateway_restart(
                yolo=args.yolo,
                max_iterations=args.max_iterations,
                quiet=getattr(args, "quiet", False),
            )
        return run_gateway(
            console,
            yolo=args.yolo,
            max_iterations=args.max_iterations,
        )

    if args.command == "tools":
        return run_tools_setup(console)

    if args.command == "uninstall":
        return run_uninstall(purge=args.purge, yes=args.yes)

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

    if args.command == "skills" and args.skills_command == "reset":
        result = reset_bundled_skill(args.name, restore=args.restore)
        print(result.get("message", result))
        return 0 if result.get("ok") else 1

    if args.command == "skills" and args.skills_command == "curator":
        cmd = args.curator_command
        if cmd == "status":
            print(format_status_report(curator_status()))
            return 0
        if cmd == "archive":
            result = archive_stale(days=args.days)
            print(
                f"Archived {len(result['archived'])} skill(s): "
                f"{', '.join(result['archived']) or 'none'}"
            )
            return 0
        if cmd == "restore":
            ok, message = restore_skill(args.name)
            print(message)
            return 0 if ok else 1
        if cmd == "pin":
            ok, message = pin_skill(args.name, pinned=True)
            print(message)
            return 0 if ok else 1
        if cmd == "unpin":
            ok, message = pin_skill(args.name, pinned=False)
            print(message)
            return 0 if ok else 1
        return 1

    if args.command is None and console.is_terminal:
        current = load_setup_settings()
        if needs_provider_setup(current):
            setup_result = run_model_setup(console)
            if setup_result != 0:
                return setup_result

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
            tools=session.loop.tools,
            skills=tuple(session.prompt.snapshot.skills.skills.values()),
            cwd=session.prompt.builder.cwd,
            enabled_toolsets=session.tooling.enabled_toolsets,
        )
    else:
        render_header(
            console,
            provider_name=provider.name,
            model=model,
            max_iterations=args.max_iterations,
            tools=session.loop.tools,
            skills=tuple(session.prompt.snapshot.skills.skills.values()),
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
                        f"Resumed session `{session.persistence.session_id[:8]}` "
                        f"({len(session.messages) - 1} messages loaded).",
                    )
                continue
            if command.kind in {
                SessionCommandKind.SKILLS,
                SessionCommandKind.KNOWLEDGE,
                SessionCommandKind.SESSIONS,
                SessionCommandKind.YOLO,
                SessionCommandKind.ERROR,
            }:
                render_user_message(console, user_input)
                render_markdown_message(console, "AKVAN", command.message or "")
                continue

            transcript.append(("user", user_input))
            render_user_message(console, user_input)

            session.begin_turn()
            turn_start = len(session.messages)
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

            session.scan_turn_for_memory_tool_use(turn_start)
            session.scan_turn_for_skill_tool_use(turn_start)
            session.record_turn_tool_iterations(
                AgentSession.count_turn_tool_iterations(session.messages, turn_start)
            )
            session.persist_new_messages()
            session.maybe_spawn_background_review()
            transcript.append(("assistant", answer))
    finally:
        provider.close()

