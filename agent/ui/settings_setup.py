"""Interactive agent settings wizard (`akvan settings`)."""

from __future__ import annotations

import sys

from rich.console import Console

from agent.config import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_TERMINAL_TIMEOUT,
    Settings,
    load_setup_settings,
    save_agent_settings,
)
from agent.gateway.daemon import restart_running_gateways
from agent.ui.setup import (
    SELECTOR_SEPARATOR,
    run_full_screen_input,
    run_full_screen_message,
    run_full_screen_selector,
)


def _can_run_interactive_setup() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _menu_with_footer(
    items: list[tuple[str, str]],
    *footer: tuple[str, str],
) -> list[tuple[str, str]]:
    if not footer:
        return items
    return [*items, (SELECTOR_SEPARATOR, ""), *footer]


def _approval_label(mode: str) -> str:
    return mode.title()


def _yolo_label(enabled: bool) -> str:
    return "On" if enabled else "Off"


def _root_items(current: Settings) -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            ("max_iterations", f"Max iterations …… {current.max_iterations}"),
            ("approval", f"Approval …………… {_approval_label(current.approval_mode)}"),
            ("terminal", f"Terminal timeout … {current.terminal_timeout}s"),
            ("yolo", f"YOLO ……………… {_yolo_label(current.yolo)}"),
        ],
        ("done", "Done"),
    )


def _parse_positive_int(raw: str | None, *, minimum: int = 1, maximum: int | None = None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _edit_max_iterations(current: Settings) -> Settings | None:
    entered = run_full_screen_input(
        title="Max iterations",
        prompt=(
            "Maximum agent iterations per user turn.\n"
            f"Current: {current.max_iterations} (default {DEFAULT_MAX_ITERATIONS})."
        ),
        default=str(current.max_iterations),
    )
    if entered is None or not entered.strip():
        return None
    value = _parse_positive_int(entered)
    if value is None:
        run_full_screen_message(
            title="Invalid value",
            text="Max iterations must be an integer of at least 1.",
        )
        return None
    return _save(
        current,
        max_iterations=value,
        approval_mode=current.approval_mode,
        terminal_timeout=current.terminal_timeout,
        yolo=current.yolo,
    )


def _edit_approval(current: Settings) -> Settings | None:
    choice = run_full_screen_selector(
        title="Approval policy",
        subtitle=f"Current: {_approval_label(current.approval_mode)}",
        items=[
            ("ask", "Ask — prompt for sensitive operations"),
            ("deny", "Deny — auto-reject sensitive operations"),
            ("off", "Off — skip ordinary approvals"),
        ],
        default=current.approval_mode if current.approval_mode in {"ask", "deny", "off"} else "ask",
    )
    if choice is None:
        return None
    return _save(
        current,
        max_iterations=current.max_iterations,
        approval_mode=choice,
        terminal_timeout=current.terminal_timeout,
        yolo=current.yolo,
    )


def _edit_terminal_timeout(current: Settings) -> Settings | None:
    entered = run_full_screen_input(
        title="Terminal timeout",
        prompt=(
            "Seconds before a terminal command is killed (1–600).\n"
            f"Current: {current.terminal_timeout}s "
            f"(default {DEFAULT_TERMINAL_TIMEOUT}s)."
        ),
        default=str(current.terminal_timeout),
    )
    if entered is None or not entered.strip():
        return None
    value = _parse_positive_int(entered, minimum=1, maximum=600)
    if value is None:
        run_full_screen_message(
            title="Invalid value",
            text="Terminal timeout must be an integer between 1 and 600.",
        )
        return None
    return _save(
        current,
        max_iterations=current.max_iterations,
        approval_mode=current.approval_mode,
        terminal_timeout=value,
        yolo=current.yolo,
    )


def _edit_yolo(current: Settings) -> Settings | None:
    choice = run_full_screen_selector(
        title="YOLO default",
        subtitle=f"Current: {_yolo_label(current.yolo)}",
        items=[
            ("off", "Off — require approvals (unless policy is Off)"),
            ("on", "On — skip ordinary approvals at launch"),
        ],
        default="on" if current.yolo else "off",
    )
    if choice is None:
        return None
    return _save(
        current,
        max_iterations=current.max_iterations,
        approval_mode=current.approval_mode,
        terminal_timeout=current.terminal_timeout,
        yolo=choice == "on",
    )


def _save(
    current: Settings,
    *,
    max_iterations: int,
    approval_mode: str,
    terminal_timeout: int,
    yolo: bool,
) -> Settings:
    save_agent_settings(
        max_iterations=max_iterations,
        approval_mode=approval_mode,
        terminal_timeout=terminal_timeout,
        yolo=yolo,
    )
    updated = load_setup_settings()
    _restart_running_gateways_after_settings_change(updated)
    return updated


def _restart_running_gateways_after_settings_change(settings: Settings) -> None:
    results = restart_running_gateways(
        yolo=settings.yolo,
        max_iterations=settings.max_iterations,
    )
    if not results:
        return
    lines = "\n".join(f"{gateway_id}: {message}" for gateway_id, _, message in results)
    run_full_screen_message(
        title="Gateways restarted",
        text=(
            "Running gateways were restarted to apply the new agent settings.\n\n"
            f"{lines}"
        ),
    )


def run_settings_setup(console: Console) -> int:
    if not _can_run_interactive_setup():
        console.print(
            "[red]Settings setup needs an interactive terminal.[/red]\n"
            "Run `akvan settings` directly from a terminal."
        )
        return 1

    current = load_setup_settings()
    while True:
        choice = run_full_screen_selector(
            title="Agent settings",
            subtitle="Saved to ~/.akvan/.env",
            items=_root_items(current),
            default="done",
        )
        if choice is None or choice == "done":
            return 0
        updated: Settings | None = None
        if choice == "max_iterations":
            updated = _edit_max_iterations(current)
        elif choice == "approval":
            updated = _edit_approval(current)
        elif choice == "terminal":
            updated = _edit_terminal_timeout(current)
        elif choice == "yolo":
            updated = _edit_yolo(current)
        if updated is not None:
            current = updated
