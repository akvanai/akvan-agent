"""Shared Telegram credential prompt tests."""

from __future__ import annotations

from agent.ui.telegram_setup import prompt_telegram_bot_credentials


def test_prompt_reuses_other_side_when_chosen(monkeypatch) -> None:
    choices = ["reuse"]

    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_selector",
        lambda **kwargs: choices.pop(0),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_task",
        lambda **kwargs: kwargs["callback"](),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.verify_telegram_token",
        lambda token: "@reuse_bot" if token == "other-token" else None,
    )
    monkeypatch.setattr("agent.ui.telegram_setup.run_full_screen_message", lambda **kwargs: None)

    result = prompt_telegram_bot_credentials(
        title="Telegram delivery",
        other_side_name="Telegram gateway",
        other_token="other-token",
        other_allowed_users="42",
        current_token="",
        current_allowed_users="",
    )

    assert result == ("other-token", "42")


def test_prompt_sets_up_separately_when_chosen(monkeypatch) -> None:
    choices = ["separate"]
    inputs = ["new-token", "99"]

    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_selector",
        lambda **kwargs: choices.pop(0),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_input",
        lambda **kwargs: inputs.pop(0),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_task",
        lambda **kwargs: kwargs["callback"](),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.verify_telegram_token",
        lambda token: "@new_bot" if token == "new-token" else None,
    )
    monkeypatch.setattr("agent.ui.telegram_setup.run_full_screen_message", lambda **kwargs: None)

    result = prompt_telegram_bot_credentials(
        title="Telegram delivery",
        other_side_name="Telegram gateway",
        other_token="other-token",
        other_allowed_users="42",
        current_token="kept-token",
        current_allowed_users="11",
    )

    assert result == ("new-token", "99")


def test_prompt_skips_reuse_when_other_side_missing(monkeypatch) -> None:
    inputs = ["solo-token", "7"]
    selectors = []

    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_selector",
        lambda **kwargs: selectors.append(kwargs) or "reuse",
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_input",
        lambda **kwargs: inputs.pop(0),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.run_full_screen_task",
        lambda **kwargs: kwargs["callback"](),
    )
    monkeypatch.setattr(
        "agent.ui.telegram_setup.verify_telegram_token",
        lambda token: "@solo",
    )
    monkeypatch.setattr("agent.ui.telegram_setup.run_full_screen_message", lambda **kwargs: None)

    result = prompt_telegram_bot_credentials(
        title="Telegram gateway",
        other_side_name=None,
        other_token="",
        other_allowed_users="",
    )

    assert result == ("solo-token", "7")
    assert selectors == []
