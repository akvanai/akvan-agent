"""Registration object for the Telegram gateway."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping

import httpx

from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.integrations.telegram.adapter import (
    TelegramAdapter, check_telegram_requirements,
)
from agent.gateway.integrations.telegram.config import (
    TelegramSettings,
    load_telegram_settings,
    save_telegram_settings,
    validate_telegram_settings,
)
from agent.gateway.registry import GatewayDefinition, GatewayEnvField


def verify_telegram_token(token: str) -> str:
    """Call Telegram getMe and return the bot username."""

    async def _fetch() -> str:
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        if not payload.get("ok"):
            raise ValueError(
                payload.get("description", "Telegram rejected the bot token.")
            )
        result = payload.get("result") or {}
        username = result.get("username")
        return f"@{username}" if isinstance(username, str) and username else str(
            result.get("first_name", "bot")
        )

    return asyncio.run(_fetch())


class TelegramIntegration:
    definition = GatewayDefinition(
        id="telegram",
        name="Telegram",
        description="Chat with Akvan from Telegram DMs via a BotFather bot.",
        env_fields=(
            GatewayEnvField(
                key="TELEGRAM_BOT_TOKEN",
                label="Bot token",
                description="Create a bot with @BotFather and paste the API token.",
                secret=True,
            ),
            GatewayEnvField(
                key="TELEGRAM_ALLOWED_USERS",
                label="Allowed user IDs",
                description="Comma-separated Telegram user IDs allowed to use the bot.",
            ),
            GatewayEnvField(
                key="AKVAN_GATEWAY_STREAM_EDIT_INTERVAL",
                label="Stream edit interval (seconds)",
                description="How often to edit streaming replies. Default: 0.8.",
                required=False,
                prompt=False,
            ),
        ),
        run_hint="akvan gateway",
    )

    def dependency_error(self) -> str | None:
        if check_telegram_requirements():
            return None
        return (
            "Telegram support is not installed. Install "
            "`akvan-agent[telegram]` or re-run ./install.sh."
        )

    def load_settings(
        self, *, project_root: Path | None = None,
    ) -> TelegramSettings:
        return load_telegram_settings(project_root=project_root)

    def validate_settings(self, settings: TelegramSettings) -> list[str]:
        return validate_telegram_settings(settings)

    def config_values(self, settings: TelegramSettings) -> Mapping[str, str]:
        return {
            "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
            "TELEGRAM_ALLOWED_USERS": ", ".join(
                sorted(settings.telegram_allowed_users)
            ),
            "AKVAN_GATEWAY_STREAM_EDIT_INTERVAL": str(
                settings.stream_edit_interval
            ),
        }

    def save_settings(
        self, values: Mapping[str, str], *, project_root: Path | None = None,
    ) -> Path:
        current = self.load_settings(project_root=project_root)
        return save_telegram_settings(
            bot_token=values.get("TELEGRAM_BOT_TOKEN", ""),
            allowed_users=values.get("TELEGRAM_ALLOWED_USERS", ""),
            stream_edit_interval=current.stream_edit_interval,
            stream_transport=current.stream_transport,
            rich_messages=current.rich_messages,
            rich_drafts=current.rich_drafts,
            project_root=project_root,
        )

    def verify_settings(self, values: Mapping[str, str]) -> str | None:
        return verify_telegram_token(values.get("TELEGRAM_BOT_TOKEN", ""))

    def summary(
        self, settings: TelegramSettings,
    ) -> tuple[tuple[str, str], ...]:
        token = settings.telegram_bot_token
        masked = (
            f"{token[:8]}…{token[-4:]}"
            if len(token) > 12 else ("set" if token else "missing")
        )
        return (
            ("Token", masked),
            ("Users", ", ".join(sorted(settings.telegram_allowed_users)) or "missing"),
            ("Transport", settings.stream_transport),
            (
                "Rich",
                f"messages={'on' if settings.rich_messages else 'off'}, "
                f"drafts={'on' if settings.rich_drafts else 'off'}",
            ),
        )

    def runtime_config(self, settings: TelegramSettings) -> GatewayRuntimeConfig:
        return GatewayRuntimeConfig(
            stream_edit_interval=settings.stream_edit_interval,
            stream_transport=settings.stream_transport,
        )

    def access_policy(self, settings: TelegramSettings):
        allowed = settings.telegram_allowed_users
        return lambda user_id: user_id in allowed

    def create_adapter(self, settings: TelegramSettings) -> TelegramAdapter:
        return TelegramAdapter(
            token=settings.telegram_bot_token,
            gateway_settings=settings,
        )


TELEGRAM_INTEGRATION = TelegramIntegration()
