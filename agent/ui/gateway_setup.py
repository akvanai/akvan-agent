"""Registry-driven gateway setup and background-process manager."""

from __future__ import annotations

import httpx
from rich.console import Console

from agent.gateway.config import gateway_env_path
from agent.gateway.daemon import (
    gateway_log_path, is_gateway_running, read_gateway_pid,
    start_gateway_daemon, stop_gateway_daemon,
)
from agent.gateway.registry import (
    GATEWAY_INTEGRATIONS, GatewayIntegration, get_gateway_integration,
)
from agent.gateway.state import is_gateway_enabled, set_gateway_enabled
from agent.ui.setup import (
    run_full_screen_input, run_full_screen_message,
    run_full_screen_selector, run_full_screen_task,
)


def _is_configured(integration: GatewayIntegration) -> bool:
    return not integration.validate_settings(integration.load_settings())


def _gateway_status_label(gateway_id: str, *, configured: bool) -> str:
    if not configured:
        return "needs setup"
    if is_gateway_running(gateway_id):
        return "active · running"
    return "active · stopped" if is_gateway_enabled(gateway_id) else "inactive"


def _gateway_menu_items() -> list[tuple[str, str]]:
    items = []
    for integration in GATEWAY_INTEGRATIONS:
        definition = integration.definition
        status = _gateway_status_label(
            definition.id, configured=_is_configured(integration)
        )
        items.append((
            definition.id,
            f"{definition.name:<14} {status:<22} {definition.description}",
        ))
    items.append(("summary", "View status           Show saved gateway configuration"))
    return items


def _gateway_action_items(
    gateway_id: str, *, configured: bool,
) -> list[tuple[str, str]]:
    running = is_gateway_running(gateway_id)
    enabled = is_gateway_enabled(gateway_id)
    items = []
    if configured:
        items.append(("configure", "Configure             Update credentials and settings"))
        if running:
            items.append(("deactivate", "Deactivate            Stop the background gateway"))
        elif enabled:
            items.extend([
                ("start", "Start                 Run the gateway in the background"),
                ("deactivate", "Deactivate            Keep configured but inactive"),
            ])
        else:
            items.append(("activate", "Activate              Enable and start in the background"))
    else:
        items.append(("configure", "Configure             Set up this gateway"))
    items.append(("back", "Back                  Return to gateway list"))
    return items


def run_gateway(
    console: Console, *, yolo: bool = False, max_iterations: int = 30,
) -> int:
    default = GATEWAY_INTEGRATIONS[0].definition.id if GATEWAY_INTEGRATIONS else None
    while True:
        choice = run_full_screen_selector(
            title="AKVAN · GATEWAYS",
            subtitle="Configure gateways and manage background processes",
            items=_gateway_menu_items(),
            default=default,
        )
        if choice is None:
            return 1
        if choice == "summary":
            show_gateway_summary()
            continue
        integration = get_gateway_integration(choice)
        if integration is None:
            run_full_screen_message(
                title="Gateway unavailable", text=f"Gateway {choice!r} is not registered."
            )
            return 2
        result = _manage_gateway(
            console, integration, yolo=yolo, max_iterations=max_iterations,
        )
        if result:
            return result


def _manage_gateway(
    console: Console, integration: GatewayIntegration, *,
    yolo: bool, max_iterations: int,
) -> int:
    gateway_id = integration.definition.id
    configured = _is_configured(integration)
    while True:
        action = run_full_screen_selector(
            title=f"AKVAN · {integration.definition.name.upper()}",
            subtitle="Configure or control this gateway",
            items=_gateway_action_items(gateway_id, configured=configured),
            default="configure" if not configured else "back",
        )
        if action is None or action == "back":
            return 0
        if action == "configure":
            result = _configure_gateway(console, integration)
            if result:
                return result
            configured = _is_configured(integration)
        elif action in {"activate", "start"}:
            return _start_gateway(
                integration, activate=action == "activate",
                yolo=yolo, max_iterations=max_iterations,
            )
        elif action == "deactivate":
            return _deactivate_gateway(gateway_id)


def _start_gateway(
    integration: GatewayIntegration, *, activate: bool,
    yolo: bool, max_iterations: int,
) -> int:
    if not _is_configured(integration):
        run_full_screen_message(
            title="Setup required",
            text=f"Configure the {integration.definition.name} gateway first.",
        )
        return 2
    error = integration.dependency_error()
    if error:
        run_full_screen_message(title="Dependency required", text=error)
        return 2
    gateway_id = integration.definition.id
    if activate or not is_gateway_enabled(gateway_id):
        set_gateway_enabled(gateway_id, True)
    started, message = start_gateway_daemon(
        gateway_id, yolo=yolo, max_iterations=max_iterations,
    )
    run_full_screen_message(
        title="GATEWAY STARTED" if started else "ALREADY RUNNING", text=message,
    )
    return 0

def _deactivate_gateway(gateway_id: str) -> int:
    set_gateway_enabled(gateway_id, False)
    stopped, message = stop_gateway_daemon(gateway_id)
    title = (
        "GATEWAY DEACTIVATED"
        if stopped or "not running" in message.lower() else "DEACTIVATE FAILED"
    )
    run_full_screen_message(title=title, text=message)
    return 0


def show_gateway_summary() -> int:
    sections = [f"File      {gateway_env_path()}"]
    for integration in GATEWAY_INTEGRATIONS:
        gateway_id = integration.definition.id
        configured = _is_configured(integration)
        if is_gateway_running(gateway_id):
            status = f"active · running (pid {read_gateway_pid(gateway_id)})"
        elif is_gateway_enabled(gateway_id) and configured:
            status = "active · stopped"
        else:
            status = "inactive" if configured else "incomplete"
        rows = "\n".join(
            f"  {label:<9} {value}"
            for label, value in integration.summary(integration.load_settings())
        )
        sections.append(
            f"Logs      {gateway_log_path(gateway_id)}\n\n"
            f"{integration.definition.name}  {status}\n{rows}"
        )
    run_full_screen_message(
        title="GATEWAY STATUS",
        text="\n\n".join(sections) + "\n\nManage with: akvan gateway",
    )
    return 0


def _configure_gateway(
    console: Console, integration: GatewayIntegration,
) -> int:
    _ = console
    values = dict(integration.config_values(integration.load_settings()))
    for field in integration.definition.env_fields:
        if not field.prompt:
            continue
        current = values.get(field.key, "")
        entered = run_full_screen_input(
            title=f"AKVAN · {integration.definition.name.upper()} {field.label.upper()}",
            prompt=field.description + (
                " Press Enter to keep the configured value." if current else ""
            ),
            default=current, password=field.secret,
        )
        if entered is None:
            return 1
        values[field.key] = entered.strip() or current
        if field.required and not values[field.key]:
            run_full_screen_message(
                title=f"{field.label} required",
                text=f"{integration.definition.name} requires {field.key}.",
            )
            return 2
    try:
        verified = run_full_screen_task(
            title=f"AKVAN · {integration.definition.name.upper()}",
            text=f"Verifying {integration.definition.name} configuration",
            callback=lambda: integration.verify_settings(values),
        )
    except (httpx.HTTPError, ValueError) as exc:
        run_full_screen_message(
            title=f"Could not verify {integration.definition.name}", text=str(exc),
        )
        return 2
    path = integration.save_settings(values)
    identity = f"\nIdentity {verified}" if verified else ""
    run_full_screen_message(
        title="CONFIGURATION SAVED",
        text=(
            f"Gateway  {integration.definition.id}{identity}\n"
            f"File     {path}\n\nActivate from: akvan gateway"
        ),
    )
    return 0
