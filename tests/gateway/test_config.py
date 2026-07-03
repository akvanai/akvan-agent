"""Gateway configuration tests."""

from __future__ import annotations

from agent.gateway.integrations.telegram.config import (
    TelegramSettings,
    load_telegram_settings,
    validate_telegram_settings,
)


def test_load_telegram_settings_from_env(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=token-123\n"
        "TELEGRAM_ALLOWED_USERS=111, 222\n"
        "AKVAN_GATEWAY_STREAM_EDIT_INTERVAL=1.2\n"
        "AKVAN_GATEWAY_STREAM_TRANSPORT=draft\n"
        "AKVAN_GATEWAY_RICH_MESSAGES=false\n"
        "AKVAN_GATEWAY_RICH_DRAFTS=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    settings = load_telegram_settings(project_root=tmp_path)
    assert settings.telegram_bot_token == "token-123"
    assert settings.telegram_allowed_users == frozenset({"111", "222"})
    assert settings.stream_edit_interval == 1.2
    assert settings.stream_transport == "draft"
    assert settings.rich_messages is False
    assert settings.rich_drafts is True


def test_load_telegram_settings_defaults_rich_drafts(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=token-123\n"
        "TELEGRAM_ALLOWED_USERS=111\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    settings = load_telegram_settings(project_root=tmp_path)
    assert settings.rich_drafts is True


def test_validate_telegram_settings_requires_token_and_users() -> None:
    errors = validate_telegram_settings(
        TelegramSettings(
            telegram_bot_token="", telegram_allowed_users=frozenset()
        )
    )
    assert any("TELEGRAM_BOT_TOKEN" in error for error in errors)
    assert any("TELEGRAM_ALLOWED_USERS" in error for error in errors)
