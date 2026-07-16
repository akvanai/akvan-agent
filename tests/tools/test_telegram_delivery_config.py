"""Telegram delivery config load/save tests."""

from __future__ import annotations

from agent.gateway.integrations.telegram.config import save_telegram_settings
from agent.tools.telegram_delivery.config import (
    has_explicit_telegram_delivery_settings,
    has_telegram_gateway_credentials,
    is_telegram_delivery_configured,
    load_telegram_delivery_settings,
    save_telegram_delivery_settings,
)


def test_delivery_settings_prefer_explicit_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    save_telegram_settings(
        bot_token="gateway-token",
        allowed_users="111",
        project_root=tmp_path,
    )
    save_telegram_delivery_settings(
        bot_token="delivery-token",
        allowed_users="222",
        project_root=tmp_path,
    )

    settings = load_telegram_delivery_settings(project_root=tmp_path)

    assert settings.telegram_bot_token == "delivery-token"
    assert settings.telegram_allowed_users == frozenset({"222"})
    assert settings.source == "explicit"
    assert has_explicit_telegram_delivery_settings(project_root=tmp_path)
    assert has_telegram_gateway_credentials(project_root=tmp_path)


def test_delivery_settings_fall_back_to_gateway(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_DELIVERY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_ALLOWED_USERS", raising=False)
    save_telegram_settings(
        bot_token="gateway-token",
        allowed_users="111",
        project_root=tmp_path,
    )

    settings = load_telegram_delivery_settings(project_root=tmp_path)

    assert settings.telegram_bot_token == "gateway-token"
    assert settings.telegram_allowed_users == frozenset({"111"})
    assert settings.source == "gateway"
    assert not has_explicit_telegram_delivery_settings(project_root=tmp_path)
    assert is_telegram_delivery_configured(project_root=tmp_path)


def test_delivery_settings_none_when_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_ALLOWED_USERS", raising=False)

    settings = load_telegram_delivery_settings(project_root=tmp_path)

    assert settings.source == "none"
    assert not is_telegram_delivery_configured(project_root=tmp_path)


def test_save_delivery_does_not_clobber_gateway(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    save_telegram_settings(
        bot_token="gateway-token",
        allowed_users="111",
        project_root=tmp_path,
    )
    path = save_telegram_delivery_settings(
        bot_token="delivery-token",
        allowed_users="222",
        project_root=tmp_path,
    )

    text = path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=gateway-token" in text
    assert "TELEGRAM_ALLOWED_USERS=111" in text
    assert "TELEGRAM_DELIVERY_BOT_TOKEN=delivery-token" in text
    assert "TELEGRAM_DELIVERY_ALLOWED_USERS=222" in text


def test_delivery_reads_akvan_home_when_workspace_differs(monkeypatch, tmp_path) -> None:
    """Toolset enablement uses workspace as project_root; credentials stay in AKVAN_HOME."""
    home = tmp_path / "akvan-home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_ALLOWED_USERS", raising=False)

    save_telegram_delivery_settings(
        bot_token="delivery-token",
        allowed_users="42",
        project_root=home,
    )

    assert is_telegram_delivery_configured(project_root=workspace)
    settings = load_telegram_delivery_settings(project_root=workspace)
    assert settings.source == "explicit"
    assert settings.telegram_bot_token == "delivery-token"
