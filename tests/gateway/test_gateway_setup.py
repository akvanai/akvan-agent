"""Gateway setup wizard tests."""

from __future__ import annotations

from agent.gateway.integrations.telegram.config import (
    load_telegram_settings,
    save_telegram_settings,
    validate_telegram_settings,
)
from agent.ui.app import build_parser


def test_gateway_command_is_available() -> None:
    args = build_parser().parse_args(["gateway"])
    assert args.command == "gateway"


def test_save_telegram_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    env_path = save_telegram_settings(
        bot_token="123:ABC",
        allowed_users="42, 99",
        stream_edit_interval=1.0,
    )
    assert env_path == tmp_path / ".env"
    settings = load_telegram_settings(project_root=tmp_path)
    assert settings.telegram_bot_token == "123:ABC"
    assert settings.telegram_allowed_users == frozenset({"42", "99"})
    assert settings.stream_edit_interval == 1.0
    assert not validate_telegram_settings(settings)
