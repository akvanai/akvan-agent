"""Agent settings wizard and CLI default tests."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.config import Settings, save_agent_settings
from agent.ui import app, settings_setup


@pytest.fixture(autouse=True)
def isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home" / ".akvan"))
    for key in (
        "AKVAN_MAX_ITERATIONS",
        "AKVAN_YOLO",
        "AKVAN_APPROVAL_MODE",
        "AKVAN_TERMINAL_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_launch_options_uses_settings_defaults() -> None:
    args = argparse.Namespace(max_iterations=None, yolo=False)
    settings = Settings(
        provider="openrouter",
        model="m",
        max_iterations=12,
        yolo=True,
    )

    max_iterations, yolo = app.resolve_launch_options(args, settings)

    assert max_iterations == 12
    assert yolo is True


def test_resolve_launch_options_cli_overrides_settings() -> None:
    args = argparse.Namespace(max_iterations=7, yolo=True)
    settings = Settings(
        provider="openrouter",
        model="m",
        max_iterations=12,
        yolo=False,
    )

    max_iterations, yolo = app.resolve_launch_options(args, settings)

    assert max_iterations == 7
    assert yolo is True


def test_build_parser_includes_settings_command() -> None:
    parser = app.build_parser()
    args = parser.parse_args(["settings"])
    assert args.command == "settings"
    assert args.max_iterations is None


def test_main_runs_settings_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_called = MagicMock(return_value=0)
    monkeypatch.setattr(app, "run_settings_setup", setup_called)
    monkeypatch.setattr(app, "setup_logging", lambda **kwargs: None)

    result = app.main(["settings"])

    assert result == 0
    setup_called.assert_called_once()


def test_run_settings_setup_rejects_non_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_setup, "_can_run_interactive_setup", lambda: False)
    console = MagicMock()

    result = settings_setup.run_settings_setup(console)

    assert result == 1
    console.print.assert_called_once()


def test_run_settings_setup_saves_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home" / ".akvan"
    home.mkdir(parents=True)
    save_agent_settings(
        max_iterations=100,
        approval_mode="ask",
        terminal_timeout=120,
        yolo=False,
        project_root=home,
    )

    choices = iter(["max_iterations", "done"])
    monkeypatch.setattr(settings_setup, "_can_run_interactive_setup", lambda: True)
    monkeypatch.setattr(
        settings_setup,
        "run_full_screen_selector",
        lambda **kwargs: next(choices),
    )
    monkeypatch.setattr(
        settings_setup,
        "run_full_screen_input",
        lambda **kwargs: "33",
    )
    restart = MagicMock(return_value=[])
    monkeypatch.setattr(settings_setup, "restart_running_gateways", restart)

    result = settings_setup.run_settings_setup(MagicMock())

    assert result == 0
    content = (home / ".env").read_text(encoding="utf-8")
    assert "AKVAN_MAX_ITERATIONS=33" in content
    restart.assert_called_once_with(yolo=False, max_iterations=33)
