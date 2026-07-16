"""Telegram gateway configuration loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, set_key

from agent.config import akvan_home
from agent.gateway.config import gateway_env_path
from agent.storage.permissions import ensure_private_file, harden_akvan_home, is_under_akvan_home

_VALID_STREAM_TRANSPORTS = frozenset({"auto", "draft", "edit"})


@dataclass(frozen=True)
class TelegramSettings:
    telegram_bot_token: str
    telegram_allowed_users: frozenset[str]
    stream_edit_interval: float = 0.8
    stream_transport: str = "auto"
    rich_messages: bool = True
    rich_drafts: bool = True


def _load_env(project_root: Path | None = None) -> dict[str, str | None]:
    root = project_root or akvan_home()
    values: dict[str, str | None] = {}
    for key, value in os.environ.items():
        values[key] = value
    env_path = root / ".env"
    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[key] = value
    return values


def _parse_allowed_users(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(
        part.strip()
        for part in raw.split(",")
        if part.strip()
    )


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_stream_transport(raw: str | None) -> str:
    value = (raw or "auto").strip().lower()
    if value not in _VALID_STREAM_TRANSPORTS:
        return "auto"
    return value


def save_telegram_settings(
    *,
    bot_token: str,
    allowed_users: str,
    stream_edit_interval: float = 0.8,
    stream_transport: str = "auto",
    rich_messages: bool = True,
    rich_drafts: bool = True,
    project_root: Path | None = None,
) -> Path:
    """Persist Telegram gateway settings to ~/.akvan/.env."""
    root = project_root or akvan_home()
    env_path = gateway_env_path(root)
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch(mode=0o600)
    set_key(str(env_path), "TELEGRAM_BOT_TOKEN", bot_token.strip(), quote_mode="never")
    set_key(
        str(env_path),
        "TELEGRAM_ALLOWED_USERS",
        allowed_users.strip(),
        quote_mode="never",
    )
    set_key(
        str(env_path),
        "AKVAN_GATEWAY_STREAM_EDIT_INTERVAL",
        str(stream_edit_interval),
        quote_mode="never",
    )
    set_key(
        str(env_path),
        "AKVAN_GATEWAY_STREAM_TRANSPORT",
        _parse_stream_transport(stream_transport),
        quote_mode="never",
    )
    set_key(
        str(env_path),
        "AKVAN_GATEWAY_RICH_MESSAGES",
        "true" if rich_messages else "false",
        quote_mode="never",
    )
    set_key(
        str(env_path),
        "AKVAN_GATEWAY_RICH_DRAFTS",
        "true" if rich_drafts else "false",
        quote_mode="never",
    )
    ensure_private_file(env_path)
    return env_path


def load_telegram_settings(*, project_root: Path | None = None) -> TelegramSettings:
    """Load Telegram gateway settings from env and ~/.akvan/.env."""
    env = _load_env(project_root)
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    allowed = _parse_allowed_users(env.get("TELEGRAM_ALLOWED_USERS"))
    interval_raw = (env.get("AKVAN_GATEWAY_STREAM_EDIT_INTERVAL") or "0.8").strip()
    try:
        interval = float(interval_raw)
    except ValueError:
        interval = 0.8
    if interval <= 0:
        interval = 0.8
    return TelegramSettings(
        telegram_bot_token=token,
        telegram_allowed_users=allowed,
        stream_edit_interval=interval,
        stream_transport=_parse_stream_transport(
            env.get("AKVAN_GATEWAY_STREAM_TRANSPORT")
        ),
        rich_messages=_parse_bool(env.get("AKVAN_GATEWAY_RICH_MESSAGES"), default=True),
        rich_drafts=_parse_bool(env.get("AKVAN_GATEWAY_RICH_DRAFTS"), default=True),
    )


def validate_telegram_settings(settings: TelegramSettings) -> list[str]:
    """Return human-readable configuration errors, if any."""
    errors: list[str] = []
    if not settings.telegram_bot_token:
        errors.append("TELEGRAM_BOT_TOKEN is required.")
    if not settings.telegram_allowed_users:
        errors.append(
            "TELEGRAM_ALLOWED_USERS must list at least one Telegram user id."
        )
    return errors
