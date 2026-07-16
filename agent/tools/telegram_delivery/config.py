"""Telegram delivery configuration loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, set_key

from agent.config import akvan_home
from agent.storage.permissions import ensure_private_file, harden_akvan_home, is_under_akvan_home


def _env_path(project_root: Path | None = None) -> Path:
    """Persist delivery credentials under AKVAN_HOME (or test root)."""
    return (project_root or akvan_home()) / ".env"

DELIVERY_BOT_TOKEN_KEY = "TELEGRAM_DELIVERY_BOT_TOKEN"
DELIVERY_ALLOWED_USERS_KEY = "TELEGRAM_DELIVERY_ALLOWED_USERS"
GATEWAY_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
GATEWAY_ALLOWED_USERS_KEY = "TELEGRAM_ALLOWED_USERS"


@dataclass(frozen=True)
class TelegramDeliverySettings:
    telegram_bot_token: str
    telegram_allowed_users: frozenset[str]
    source: str = "none"


def _load_env(project_root: Path | None = None) -> dict[str, str | None]:
    """Load process env, then ~/.akvan/.env, then optional project overlay.

    Credentials live in AKVAN_HOME (not the chat workspace). Always read the
    home file so toolset enablement works when cwd/workspace is elsewhere.
    """
    values: dict[str, str | None] = {}
    for key, value in os.environ.items():
        values[key] = value
    home_env = akvan_home() / ".env"
    paths: list[Path] = [home_env]
    if project_root is not None:
        project_env = project_root / ".env"
        if project_env.resolve() != home_env.resolve():
            paths.append(project_env)
    for env_path in paths:
        if not env_path.exists():
            continue
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


def _format_allowed_users(users: frozenset[str]) -> str:
    return ", ".join(sorted(users))


def has_explicit_telegram_delivery_settings(*, project_root: Path | None = None) -> bool:
    """True when delivery-specific env keys are set (no gateway fallback)."""
    env = _load_env(project_root)
    token = (env.get(DELIVERY_BOT_TOKEN_KEY) or "").strip()
    allowed = _parse_allowed_users(env.get(DELIVERY_ALLOWED_USERS_KEY))
    return bool(token and allowed)


def has_telegram_gateway_credentials(*, project_root: Path | None = None) -> bool:
    """True when gateway Telegram token + allowlist are present."""
    env = _load_env(project_root)
    token = (env.get(GATEWAY_BOT_TOKEN_KEY) or "").strip()
    allowed = _parse_allowed_users(env.get(GATEWAY_ALLOWED_USERS_KEY))
    return bool(token and allowed)


def load_telegram_delivery_settings(
    *, project_root: Path | None = None,
) -> TelegramDeliverySettings:
    """Load delivery settings; fall back to gateway credentials when unset."""
    env = _load_env(project_root)
    delivery_token = (env.get(DELIVERY_BOT_TOKEN_KEY) or "").strip()
    delivery_allowed = _parse_allowed_users(env.get(DELIVERY_ALLOWED_USERS_KEY))
    if delivery_token and delivery_allowed:
        return TelegramDeliverySettings(
            telegram_bot_token=delivery_token,
            telegram_allowed_users=delivery_allowed,
            source="explicit",
        )

    gateway_token = (env.get(GATEWAY_BOT_TOKEN_KEY) or "").strip()
    gateway_allowed = _parse_allowed_users(env.get(GATEWAY_ALLOWED_USERS_KEY))
    if gateway_token and gateway_allowed:
        return TelegramDeliverySettings(
            telegram_bot_token=gateway_token,
            telegram_allowed_users=gateway_allowed,
            source="gateway",
        )

    return TelegramDeliverySettings(
        telegram_bot_token=delivery_token or gateway_token,
        telegram_allowed_users=delivery_allowed or gateway_allowed,
        source="none",
    )


def save_telegram_delivery_settings(
    *,
    bot_token: str,
    allowed_users: str,
    project_root: Path | None = None,
) -> Path:
    """Persist Telegram delivery settings to ~/.akvan/.env (delivery keys only)."""
    root = project_root or akvan_home()
    env_path = _env_path(root)
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch(mode=0o600)
    set_key(
        str(env_path),
        DELIVERY_BOT_TOKEN_KEY,
        bot_token.strip(),
        quote_mode="never",
    )
    set_key(
        str(env_path),
        DELIVERY_ALLOWED_USERS_KEY,
        allowed_users.strip(),
        quote_mode="never",
    )
    ensure_private_file(env_path)
    return env_path


def is_telegram_delivery_configured(*, project_root: Path | None = None) -> bool:
    settings = load_telegram_delivery_settings(project_root=project_root)
    return bool(settings.telegram_bot_token and settings.telegram_allowed_users)


def telegram_delivery_credentials_csv(
    *, project_root: Path | None = None,
) -> tuple[str, str]:
    """Return (token, allowed_users_csv) from explicit delivery settings only."""
    env = _load_env(project_root)
    token = (env.get(DELIVERY_BOT_TOKEN_KEY) or "").strip()
    allowed = _parse_allowed_users(env.get(DELIVERY_ALLOWED_USERS_KEY))
    return token, _format_allowed_users(allowed)


def gateway_telegram_credentials_csv(
    *, project_root: Path | None = None,
) -> tuple[str, str]:
    """Return (token, allowed_users_csv) from gateway settings only."""
    env = _load_env(project_root)
    token = (env.get(GATEWAY_BOT_TOKEN_KEY) or "").strip()
    allowed = _parse_allowed_users(env.get(GATEWAY_ALLOWED_USERS_KEY))
    return token, _format_allowed_users(allowed)
