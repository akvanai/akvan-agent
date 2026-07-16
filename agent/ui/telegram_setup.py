"""Shared Telegram credential prompts for gateway and delivery setup."""

from __future__ import annotations

import httpx

from agent.gateway.integrations.telegram.integration import verify_telegram_token
from agent.ui.setup import (
    run_full_screen_input,
    run_full_screen_message,
    run_full_screen_selector,
    run_full_screen_task,
)


def prompt_telegram_bot_credentials(
    *,
    title: str,
    other_side_name: str | None = None,
    other_token: str = "",
    other_allowed_users: str = "",
    current_token: str = "",
    current_allowed_users: str = "",
) -> tuple[str, str] | None:
    """Prompt for bot token + allowlist, optionally offering to reuse the other side.

    Returns ``(bot_token, allowed_users_csv)`` or ``None`` if the user cancels.
    """
    token = current_token.strip()
    allowed_users = current_allowed_users.strip()
    other_token = other_token.strip()
    other_allowed_users = other_allowed_users.strip()
    reuse_available = bool(other_side_name and other_token and other_allowed_users)

    if reuse_available:
        choice = run_full_screen_selector(
            title=title,
            subtitle=(
                f"{other_side_name} is already configured. "
                "Use that setup here, or configure separately?"
            ),
            items=[
                (
                    "reuse",
                    f"Use {other_side_name} setup  copy bot token and allowed users",
                ),
                (
                    "separate",
                    "Set up separately  configure a different bot or allowlist",
                ),
            ],
            default="reuse",
        )
        if choice is None:
            return None
        if choice == "reuse":
            try:
                verified = run_full_screen_task(
                    title=title,
                    text=f"Verifying {other_side_name} credentials for {title}",
                    callback=lambda: verify_telegram_token(other_token),
                )
            except (httpx.HTTPError, ValueError) as exc:
                run_full_screen_message(
                    title=f"Could not verify {title}",
                    text=str(exc),
                )
                return None
            identity = f"\nIdentity {verified}" if verified else ""
            run_full_screen_message(
                title="Credentials verified",
                text=(
                    f"Using {other_side_name} setup for {title}.{identity}"
                ),
            )
            return other_token, other_allowed_users

    entered_token = run_full_screen_input(
        title=f"{title} · Bot token",
        prompt=(
            "Create a bot with @BotFather and paste the API token."
            + (" Press Enter to keep the configured value." if token else "")
        ),
        default=token,
        password=True,
    )
    if entered_token is None:
        return None
    token = entered_token.strip() or token
    if not token:
        run_full_screen_message(
            title="Bot token required",
            text=f"{title} requires a Telegram bot token.",
        )
        return None

    entered_users = run_full_screen_input(
        title=f"{title} · Allowed user IDs",
        prompt=(
            "Comma-separated Telegram user IDs allowed to receive messages."
            + (" Press Enter to keep the configured value." if allowed_users else "")
        ),
        default=allowed_users,
    )
    if entered_users is None:
        return None
    allowed_users = entered_users.strip() or allowed_users
    if not allowed_users:
        run_full_screen_message(
            title="Allowed users required",
            text=f"{title} requires at least one Telegram user ID.",
        )
        return None

    try:
        verified = run_full_screen_task(
            title=title,
            text=f"Verifying {title} configuration",
            callback=lambda: verify_telegram_token(token),
        )
    except (httpx.HTTPError, ValueError) as exc:
        run_full_screen_message(
            title=f"Could not verify {title}",
            text=str(exc),
        )
        return None

    identity = f"\nIdentity {verified}" if verified else ""
    run_full_screen_message(
        title="Credentials verified",
        text=f"{title} bot token is valid.{identity}",
    )
    return token, allowed_users
