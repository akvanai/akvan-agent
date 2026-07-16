"""Model setup wizard tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.config import Settings
from agent.providers.base import ProviderError
from agent.ui import setup


def test_configured_providers_empty_when_no_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        setup,
        "load_codex_cli_token",
        lambda path: (_ for _ in ()).throw(ProviderError("missing")),
    )
    settings = Settings(provider="openrouter", model="openai/gpt-4o-mini")

    assert setup.configured_providers(settings) == set()
    assert setup.needs_provider_setup(settings) is True


def test_configured_providers_detects_each_backend() -> None:
    assert "openrouter" in setup.configured_providers(
        Settings(provider="openrouter", model="m", openrouter_api_key="key")
    )
    assert "openai-codex" in setup.configured_providers(
        Settings(provider="openai-codex", model="m", openai_api_key="key")
    )


def test_configured_providers_detects_codex_cli_session(monkeypatch) -> None:
    monkeypatch.setattr(setup, "load_codex_cli_token", lambda path: "token")
    assert "openai-codex" in setup.configured_providers(
        Settings(
            provider="openai-codex",
            model="m",
            codex_auth_mode="cli",
        )
    )
    assert "deepseek" in setup.configured_providers(
        Settings(provider="deepseek", model="m", deepseek_api_key="key")
    )
    assert "akvan" in setup.configured_providers(
        Settings(provider="akvan", model="m", akvan_api_key="key")
    )


def test_select_provider_uses_inline_labels_with_check_icon(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_selector(**kwargs):
        captured.update(kwargs)
        return "deepseek"

    monkeypatch.setattr(setup, "run_full_screen_selector", fake_selector)
    result = setup.select_provider(
        MagicMock(),
        current_provider="deepseek",
        configured_providers={"deepseek", "openrouter"},
    )

    assert result == "deepseek"
    items = captured["items"]
    labels = {value: label for value, label in items}
    assert "\n" not in labels["openrouter"]
    assert labels["openrouter"].startswith("✓ ")
    assert labels["deepseek"].startswith("✓ ")
    assert " · current" in labels["deepseek"]
    assert labels["akvan"].startswith("  ")
    assert "needs setup" not in labels["akvan"]
    assert captured["subtitle"] == ""


def test_run_model_setup_restarts_running_gateways_on_success(monkeypatch) -> None:
    monkeypatch.setattr(setup, "_can_run_interactive_setup", lambda: True)
    monkeypatch.setattr(setup, "select_provider", lambda *args, **kwargs: "openrouter")
    monkeypatch.setattr(
        setup,
        "PROVIDER_CONFIGURATORS",
        {"openrouter": lambda console, current: 0},
    )
    notify = MagicMock()
    monkeypatch.setattr(setup, "_restart_running_gateways_after_model_change", notify)

    result = setup.run_model_setup(MagicMock())

    assert result == 0
    notify.assert_called_once()


def test_run_model_setup_skips_gateway_restart_when_configurator_fails(monkeypatch) -> None:
    monkeypatch.setattr(setup, "_can_run_interactive_setup", lambda: True)
    monkeypatch.setattr(setup, "select_provider", lambda *args, **kwargs: "openrouter")
    monkeypatch.setattr(
        setup,
        "PROVIDER_CONFIGURATORS",
        {"openrouter": lambda console, current: 2},
    )
    notify = MagicMock()
    monkeypatch.setattr(setup, "_restart_running_gateways_after_model_change", notify)

    result = setup.run_model_setup(MagicMock())

    assert result == 2
    notify.assert_not_called()


def test_run_model_setup_rejects_non_interactive_terminal(monkeypatch) -> None:
    monkeypatch.setattr(setup, "_can_run_interactive_setup", lambda: False)
    console = MagicMock()

    result = setup.run_model_setup(console)

    assert result == 1
    console.print.assert_called_once()


def test_main_runs_model_setup_when_no_provider_configured(monkeypatch) -> None:
    from agent.ui import app

    setup_called = MagicMock(return_value=0)
    interactive = MagicMock(return_value=0)
    monkeypatch.setattr(app, "needs_provider_setup", lambda settings: True)
    monkeypatch.setattr(app, "run_model_setup", setup_called)
    monkeypatch.setattr(app, "load_setup_settings", lambda: Settings(provider="openrouter", model="m"))
    monkeypatch.setattr(app, "load_settings", lambda **kwargs: Settings(provider="openrouter", model="m", openrouter_api_key="key"))
    monkeypatch.setattr(app, "build_provider", lambda settings: MagicMock(name="provider", close=MagicMock()))
    monkeypatch.setattr(app, "AgentSession", MagicMock())
    monkeypatch.setattr(app.AgentSession, "create", lambda **kwargs: MagicMock(loop=MagicMock(tools=()), prompt=MagicMock(snapshot=MagicMock(skills=MagicMock(skills={}))), tooling=MagicMock(enabled_toolsets=())))
    monkeypatch.setattr(app, "run_interactive_session", interactive)
    monkeypatch.setattr(app.Console, "is_terminal", property(lambda self: True))

    result = app.main([])

    assert result == 0
    setup_called.assert_called_once()
    interactive.assert_called_once()


def test_main_skips_model_setup_when_provider_configured(monkeypatch) -> None:
    from agent.ui import app

    setup_called = MagicMock(return_value=0)
    monkeypatch.setattr(app, "needs_provider_setup", lambda settings: False)
    monkeypatch.setattr(app, "run_model_setup", setup_called)
    monkeypatch.setattr(app, "load_settings", lambda **kwargs: Settings(provider="openrouter", model="m", openrouter_api_key="key"))
    monkeypatch.setattr(app, "build_provider", lambda settings: MagicMock(name="provider", close=MagicMock()))
    monkeypatch.setattr(app, "AgentSession", MagicMock())
    monkeypatch.setattr(app.AgentSession, "create", lambda **kwargs: MagicMock(loop=MagicMock(tools=()), prompt=MagicMock(snapshot=MagicMock(skills=MagicMock(skills={}))), tooling=MagicMock(enabled_toolsets=())))
    monkeypatch.setattr(app, "run_interactive_session", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app.Console, "is_terminal", property(lambda self: True))

    result = app.main([])

    assert result == 0
    setup_called.assert_not_called()


def test_main_returns_setup_exit_code_when_user_cancels(monkeypatch) -> None:
    from agent.ui import app

    setup_called = MagicMock(return_value=1)
    load_settings = MagicMock()
    monkeypatch.setattr(app, "needs_provider_setup", lambda settings: True)
    monkeypatch.setattr(app, "run_model_setup", setup_called)
    monkeypatch.setattr(app, "load_setup_settings", lambda: Settings(provider="openrouter", model="m"))
    monkeypatch.setattr(app, "load_settings", load_settings)
    monkeypatch.setattr(app.Console, "is_terminal", property(lambda self: True))

    result = app.main([])

    assert result == 1
    setup_called.assert_called_once()
    load_settings.assert_not_called()
